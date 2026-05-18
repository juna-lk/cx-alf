"""cx_chats에는 있지만 cx_full_messages에 없는 chat_id들을 백필한다.

채널톡 user-chats 페이지네이션이 updatedAt 기준이라
date 필터로 break하면 일부 누락됨. cx_chats의 chat_id 목록을 기준으로 보강.
"""
from __future__ import annotations
import os
import sys
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

ENV_PATH = Path(__file__).parent / ".env"
for line in ENV_PATH.read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).parent / "api"))
from _alf_common import supabase_get, supabase_post  # noqa: E402
from alf_collect import fetch_messages_for_chat, parse_messages  # noqa: E402

CT_HEADERS = {
    "x-access-key": os.environ["CHANNELTALK_ACCESS_KEY"],
    "x-access-secret": os.environ["CHANNELTALK_ACCESS_SECRET"],
    "Content-Type": "application/json",
}
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
KST = timezone(timedelta(hours=9))


def fetch_chat_metadata(chat_id: str) -> dict | None:
    """단일 chat의 메타데이터 조회"""
    url = f"https://api.channel.io/open/v5/user-chats/{chat_id}"
    req = urllib.request.Request(url, headers=CT_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return data.get("userChat") or data
    except Exception as e:
        print(f"  [warn] {chat_id} 메타 실패: {e}")
        return None


def get_missing_chat_ids(days: int = 96) -> list[str]:
    """cx_chats엔 있는데 cx_full_messages엔 없는 chat_id 목록"""
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)

    # cx_chats에서 96일 내 chat_id (페이지네이션)
    cx_chat_ids = set()
    offset = 0
    while True:
        url = (f"{SUPABASE_URL}/rest/v1/cx_chats?select=chat_id"
               f"&created_at=gte.{cutoff_ms}&limit=1000&offset={offset}")
        rows = supabase_get(url, SUPABASE_SERVICE_KEY)
        if not rows:
            break
        cx_chat_ids.update(r["chat_id"] for r in rows)
        if len(rows) < 1000:
            break
        offset += 1000

    # cx_full_messages에서 chat_id (페이지네이션)
    full_chat_ids = set()
    offset = 0
    while True:
        url = (f"{SUPABASE_URL}/rest/v1/cx_full_messages?select=chat_id"
               f"&limit=10000&offset={offset}")
        rows = supabase_get(url, SUPABASE_SERVICE_KEY)
        if not rows:
            break
        full_chat_ids.update(r["chat_id"] for r in rows)
        if len(rows) < 10000:
            break
        offset += 10000

    missing = cx_chat_ids - full_chat_ids
    print(f"cx_chats 96일: {len(cx_chat_ids)}건")
    print(f"cx_full_messages: {len(full_chat_ids)}건")
    print(f"누락: {len(missing)}건")
    return list(missing)


def build_row_from_meta(chat: dict, messages: list) -> dict:
    """채널톡 메타 + 메시지 → cx_full_messages row"""
    created_ms = chat.get("createdAt") or 0
    dt = datetime.fromtimestamp(created_ms / 1000, tz=KST)
    return {
        "chat_id": chat["id"],
        "date": dt.strftime("%Y-%m-%d"),
        "tags": chat.get("tags") or [],
        "messages": messages,
        "handling_type": chat.get("handlingType") or "none",
        "csat_score": chat.get("csatScore"),
        "message_count": len(messages),
        "alf_tried": any(m["role"] == "alf" for m in messages),
        "assignee_id": str(chat.get("assigneeId") or ""),
        "assignee_name": chat.get("assigneeName") or "",
    }


if __name__ == "__main__":
    missing = get_missing_chat_ids(days=96)
    if not missing:
        print("누락 없음. 종료.")
        sys.exit(0)

    print(f"\n[수집 시작] {len(missing)}건")
    new_rows = []
    errors = 0
    for i, cid in enumerate(missing):
        chat = fetch_chat_metadata(cid)
        if not chat:
            errors += 1
            continue
        try:
            raw_msgs = fetch_messages_for_chat(cid)
            messages = parse_messages(raw_msgs)
            new_rows.append(build_row_from_meta(chat, messages))
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  [warn] {cid} 메시지 실패: {e}")
        time.sleep(0.15)  # rate limit 여유
        if (i + 1) % 50 == 0:
            print(f"  ... {i + 1}/{len(missing)} 처리됨 (실패 {errors}건)")

    print(f"\n[저장] {len(new_rows)}건")
    stored = 0
    for i in range(0, len(new_rows), 100):
        chunk = new_rows[i:i + 100]
        supabase_post(
            f"{SUPABASE_URL}/rest/v1/cx_full_messages",
            chunk, SUPABASE_SERVICE_KEY,
        )
        stored += len(chunk)
        print(f"  ... {stored}/{len(new_rows)} 저장됨")

    print(f"\n[완료] 신규 저장 {stored}건, 실패 {errors}건")
