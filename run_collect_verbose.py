"""verbose 버전 — 단계별 로그를 찍는다."""
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
from alf_collect import (  # noqa: E402
    fetch_messages_for_chat,
    parse_messages,
    build_row,
)

CT_ACCESS_KEY = os.environ["CHANNELTALK_ACCESS_KEY"]
CT_ACCESS_SECRET = os.environ["CHANNELTALK_ACCESS_SECRET"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

CT_HEADERS = {
    "x-access-key": CT_ACCESS_KEY,
    "x-access-secret": CT_ACCESS_SECRET,
    "Content-Type": "application/json",
}

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 96
since = datetime.now(timezone.utc) - timedelta(days=DAYS)
print(f"[start] {DAYS}일치 수집 시작 (since {since.isoformat()})")

# 1. 기존 chat_id 조회
print("[1/3] 기존 chat_id 조회 중...")
url = f"{SUPABASE_URL}/rest/v1/cx_full_messages?select=chat_id&limit=50000"
rows = supabase_get(url, SUPABASE_SERVICE_KEY)
existing_ids = {r["chat_id"] for r in rows}
print(f"      → 기존 수집: {len(existing_ids)}건")

# 2. 채널톡에서 chat 목록 가져오기 (페이지네이션 포함)
print("[2/3] 채널톡 채팅 목록 수집 중...")
all_chats = []
for state in ("closed", "opened", "snoozed", "initial", "missed"):
    cursor = None
    page = 0
    state_total = 0
    while True:
        page += 1
        params = f"limit=500&sortOrder=desc&state={state}"
        if cursor:
            params += f"&since={urllib.parse.quote(cursor)}"
        req = urllib.request.Request(
            f"https://api.channel.io/open/v5/user-chats?{params}",
            headers=CT_HEADERS,
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        batch = data.get("userChats", [])
        in_range = [c for c in batch if c.get("createdAt", 0) / 1000 >= since.timestamp()]
        all_chats.extend(in_range)
        state_total += len(in_range)
        next_cursor = data.get("next")
        print(f"      [{state}] page{page}: batch={len(batch)}, in_range={len(in_range)}, total={state_total}")
        if not next_cursor or not batch or len(in_range) < len(batch):
            break
        cursor = next_cursor
        time.sleep(0.1)

print(f"      → 총 {len(all_chats)}건 수집됨")

# 3. 각 chat 메시지 가져와서 Supabase 저장
print("[3/3] 메시지 수집 + 저장 중...")
new_rows = []
skipped_existing = 0
fetch_errors = 0
for i, chat in enumerate(all_chats):
    cid = chat.get("id", "")
    if cid in existing_ids:
        skipped_existing += 1
        continue
    try:
        raw_msgs = fetch_messages_for_chat(cid)
        messages = parse_messages(raw_msgs)
        new_rows.append(build_row(chat, messages))
        time.sleep(0.1)  # 10 RPS 한도 준수
    except Exception as e:
        fetch_errors += 1
        if fetch_errors <= 5:
            print(f"      [warn] chat {cid} 실패: {e}")
    if (i + 1) % 50 == 0:
        print(f"      ... {i + 1}/{len(all_chats)} 처리됨")

print(f"      → 신규 {len(new_rows)}건, 기존 스킵 {skipped_existing}건, 실패 {fetch_errors}건")

# 4. Supabase에 저장
print("[저장] Supabase 업로드 중...")
stored = 0
for i in range(0, len(new_rows), 100):
    chunk = new_rows[i:i + 100]
    supabase_post(
        f"{SUPABASE_URL}/rest/v1/cx_full_messages",
        chunk, SUPABASE_SERVICE_KEY,
    )
    stored += len(chunk)
    print(f"      ... {stored}/{len(new_rows)} 저장됨")

print(f"\n[done] 신규 저장: {stored}건, 기존 스킵: {skipped_existing}건, 실패: {fetch_errors}건")
