"""Lean(`html` 없는 옛 schema) messages row 일괄 복구.

전략:
  - 날짜 1주 단위로 끊어 `messages->0->>html=is.null` 필터로 chat_id 수집
    (전체 스캔하면 PostgREST statement timeout)
  - 각 chat_id에 대해 채널톡 API 재fetch → parse_messages (12필드) → PATCH

사용:
    python3 repair_lean_messages.py                # 2024-01-01 ~ today
    python3 repair_lean_messages.py 2026-05-01     # 시작일 지정
"""
from __future__ import annotations
import os
import sys
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, date
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


def collect_lean_chat_ids(start_date: date, end_date: date) -> list[str]:
    """1주씩 끊어 lean (`html` 없는) chat_id 수집."""
    all_ids: list[str] = []
    cur = start_date
    while cur <= end_date:
        nxt = min(cur + timedelta(days=7), end_date + timedelta(days=1))
        url = (
            f"{SUPABASE_URL}/rest/v1/cx_full_messages"
            f"?select=chat_id"
            f"&messages->0->>html=is.null"
            f"&date=gte.{cur.isoformat()}&date=lt.{nxt.isoformat()}"
            f"&limit=1000"
        )
        try:
            rows = supabase_get(url, SUPABASE_SERVICE_KEY)
        except Exception as e:
            print(f"  [{cur} ~ {nxt}] fetch 실패: {e}")
            cur = nxt
            continue
        ids = [r["chat_id"] for r in rows]
        all_ids.extend(ids)
        print(f"  [{cur} ~ {nxt}] {len(ids)}건 (누적 {len(all_ids)})")
        cur = nxt
    return all_ids


def patch_chat_messages(chat_id: str, messages: list) -> bool:
    """messages·message_count PATCH. 3회 retry."""
    payload = json.dumps({
        "messages": messages,
        "message_count": len(messages),
    }).encode()
    for attempt in range(3):
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/cx_full_messages?chat_id=eq.{chat_id}",
            data=payload, method="PATCH",
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
                time.sleep(8 * (attempt + 1))
                continue
            return False
    return False


if __name__ == "__main__":
    args = sys.argv[1:]
    start = date(2024, 1, 1)
    if args and args[0] != "--all":
        try:
            start = date.fromisoformat(args[0])
        except ValueError:
            print(f"날짜 형식이 잘못됐어요: {args[0]} (예: 2026-05-01)")
            sys.exit(1)
    end = date.today()
    print(f"[start] 복구 범위: {start} ~ {end}\n")

    print("[0/3] 채널톡 매니저 캐시...")
    manager_map = fetch_all_managers()
    print(f"      매니저 {len(manager_map)}명\n")

    print("[1/3] lean chat_id 수집 (주 단위 페이지네이션)...")
    chat_ids = collect_lean_chat_ids(start, end)
    chat_ids = list(dict.fromkeys(chat_ids))  # dedupe
    print(f"\n      복구 대상 {len(chat_ids)}건\n")

    if not chat_ids:
        print("복구 대상 없음. 종료.")
        sys.exit(0)

    print("[2/3] 채널톡 재fetch + PATCH...")
    success = fail = 0
    for i, cid in enumerate(chat_ids):
        try:
            raw = fetch_messages_for_chat(cid)
            new_msgs = parse_messages(raw, manager_map)
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
            print(f"      ... {i+1}/{len(chat_ids)} (성공 {success}, 실패 {fail})")

    print(f"\n[3/3] 완료 — 성공 {success}건, 실패 {fail}건")
