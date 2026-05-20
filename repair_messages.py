"""옛 schema chat의 messages 컬럼을 새 schema로 강제 업데이트.

PostgREST upsert가 일부 chat의 messages를 덮어쓰지 않는 이슈 우회용.
채널톡 API에서 다시 fetch → parse_messages 적용 → PATCH로 messages 컬럼만 직접 업데이트.

사용:
    python3 repair_messages.py
"""
from __future__ import annotations
import os
import sys
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

ENV_PATH = Path(__file__).parent / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).parent / "api"))
from alf_collect import fetch_messages_for_chat, parse_messages, fetch_all_managers  # noqa: E402
from _alf_common import supabase_get  # noqa: E402

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]


def patch_chat_messages(chat_id: str, messages: list) -> bool:
    """messages·message_count 컬럼만 PATCH로 강제 업데이트. 3회 retry."""
    payload = json.dumps({
        "messages": messages,
        "message_count": len(messages),
    }).encode()
    for attempt in range(3):
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/cx_full_messages?chat_id=eq.{chat_id}",
            data=payload,
            method="PATCH",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
            return True
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:200] if hasattr(e, "read") else ""
            print(f"      [warn] {chat_id} HTTP {e.code} (attempt {attempt+1}/3): {body}")
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            return False
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"      [warn] {chat_id} network (attempt {attempt+1}/3): {e}")
            if attempt < 2:
                time.sleep(10 * (attempt + 1))
                continue
            return False
    return False


if __name__ == "__main__":
    # 기본 = 전체 chat 재처리. --old-only 옵션 줄 때만 옛 schema chat만.
    old_only = "--old-only" in sys.argv
    mode_label = "옛 schema chat_id" if old_only else "전체 chat_id"

    print("[0/3] 채널톡 매니저 목록 fetch...")
    manager_map = fetch_all_managers()
    print(f"      매니저 {len(manager_map)}명 캐시")
    print()

    print(f"[1/3] {mode_label} 조회 (페이지네이션)...")
    old_chat_ids = []
    offset = 0
    PAGE = 1000
    while True:
        url = f"{SUPABASE_URL}/rest/v1/cx_full_messages?select=chat_id,messages&limit={PAGE}&offset={offset}"
        rows = supabase_get(url, SUPABASE_SERVICE_KEY)
        if not rows:
            break
        for r in rows:
            msgs = r.get("messages") or []
            if old_only:
                if msgs and isinstance(msgs[0], dict) and "time" not in msgs[0]:
                    old_chat_ids.append(r["chat_id"])
            else:
                old_chat_ids.append(r["chat_id"])
        print(f"      ... offset={offset}, 누적 {len(old_chat_ids)}건")
        if len(rows) < PAGE:
            break
        offset += PAGE
    print(f"      총 처리 대상: {len(old_chat_ids)}건")
    print()

    if not old_chat_ids:
        print("처리할 옛 schema chat이 없습니다. 완료!")
        sys.exit(0)

    print("[2/3] 채널톡 재fetch + 새 schema PATCH...")
    success = 0
    fail = 0
    for i, cid in enumerate(old_chat_ids):
        try:
            raw_msgs = fetch_messages_for_chat(cid)
            new_msgs = parse_messages(raw_msgs, manager_map)
            if patch_chat_messages(cid, new_msgs):
                success += 1
            else:
                fail += 1
        except Exception as e:
            fail += 1
            if fail <= 5:
                print(f"      [warn] {cid}: {e}")
        time.sleep(0.12)
        if (i + 1) % 50 == 0:
            print(f"      ... {i + 1}/{len(old_chat_ids)} (성공 {success}, 실패 {fail})")
    print()

    print(f"[3/3] 완료 — 성공 {success}건, 실패 {fail}건")
