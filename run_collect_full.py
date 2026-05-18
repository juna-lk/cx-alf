"""견고한 백필 — 모든 페이지 끝까지 페이지네이션하면서 날짜 범위 내 chat만 수집.

기존 alf_collect는 첫 페이지에 out-of-range가 일부 있으면 break해서
오래된 데이터가 누락됨. 이 스크립트는 끝까지 페이지네이션.

사용:
    python3 run_collect_full.py 138    # 138일치 (2026-01-01부터)
    python3 run_collect_full.py        # 기본 138일
"""
from __future__ import annotations
import os
import sys
import json
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

ENV_PATH = Path(__file__).parent / ".env"
for line in ENV_PATH.read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).parent / "api"))
from _alf_common import supabase_get, supabase_post  # noqa: E402
from alf_collect import fetch_messages_for_chat, parse_messages, build_row  # noqa: E402

CT_HEADERS = {
    "x-access-key": os.environ["CHANNELTALK_ACCESS_KEY"],
    "x-access-secret": os.environ["CHANNELTALK_ACCESS_SECRET"],
    "Content-Type": "application/json",
}
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]


def get_existing_chat_ids() -> set:
    """이미 수집된 chat_id"""
    existing = set()
    offset = 0
    while True:
        url = f"{SUPABASE_URL}/rest/v1/cx_full_messages?select=chat_id&limit=10000&offset={offset}"
        rows = supabase_get(url, SUPABASE_SERVICE_KEY)
        if not rows:
            break
        existing.update(r["chat_id"] for r in rows)
        if len(rows) < 10000:
            break
        offset += 10000
    return existing


def fetch_chats_full(state: str, since_ts: float) -> list:
    """state별 모든 페이지를 끝까지 가져온 후 date 필터링.

    안전장치: 100페이지(=50,000건)를 초과하면 중단.
    """
    all_chats = []
    cursor = None
    page = 0
    while page < 100:
        page += 1
        params = f"limit=500&sortOrder=desc&state={state}"
        if cursor:
            params += f"&since={urllib.parse.quote(cursor)}"
        req = urllib.request.Request(
            f"https://api.channel.io/open/v5/user-chats?{params}",
            headers=CT_HEADERS,
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            print(f"      [{state}] page{page} 실패: {e}")
            break
        batch = data.get("userChats", [])
        in_range = [c for c in batch if c.get("createdAt", 0) / 1000 >= since_ts]
        all_chats.extend(in_range)
        oldest = min((c.get("createdAt", 0) for c in batch), default=0) / 1000 if batch else 0
        print(f"      [{state}] page{page}: batch={len(batch)}, in_range={len(in_range)}, oldest={datetime.fromtimestamp(oldest) if oldest else 'N/A'}")
        next_cursor = data.get("next")
        if not next_cursor or not batch:
            break
        # 가장 오래된 chat이 cutoff보다 1주 이상 오래되었으면 그 다음 페이지도 다 out-of-range
        if oldest > 0 and oldest < since_ts - (7 * 86400):
            print(f"      [{state}] cutoff 도달 — 페이지네이션 종료")
            break
        cursor = next_cursor
        time.sleep(0.15)
    return all_chats


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 138
    since = datetime.now(timezone.utc) - timedelta(days=days)
    print(f"[start] {days}일치 수집 (since {since.date()})")
    print()

    print("[1/4] 기존 chat_id 조회...")
    existing = get_existing_chat_ids()
    print(f"      기존: {len(existing)}건")
    print()

    print("[2/4] 채널톡 채팅 목록 수집 (state별 끝까지 페이지네이션)...")
    all_chats = []
    for state in ("closed", "opened", "snoozed", "initial", "missed"):
        chats = fetch_chats_full(state, since.timestamp())
        all_chats.extend(chats)
        print(f"   {state}: {len(chats)}건 누적")
    print(f"      총 {len(all_chats)}건 (중복 포함)")
    print()

    # 중복 제거
    unique = {c["id"]: c for c in all_chats}
    print(f"[3/4] 중복 제거: {len(unique)}건")
    new_chats = {cid: c for cid, c in unique.items() if cid not in existing}
    print(f"      신규: {len(new_chats)}건")
    print()

    print("[4/4] 각 신규 chat 메시지 수집...")
    new_rows = []
    errors = 0
    for i, (cid, chat) in enumerate(new_chats.items()):
        try:
            raw = fetch_messages_for_chat(cid)
            messages = parse_messages(raw)
            new_rows.append(build_row(chat, messages))
            time.sleep(0.12)
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"      [warn] {cid}: {e}")
        if (i + 1) % 50 == 0:
            print(f"      ... {i + 1}/{len(new_chats)} (실패 {errors})")
    print()

    print("[저장] Supabase 업로드 (upsert)...")
    stored = 0
    for i in range(0, len(new_rows), 100):
        chunk = new_rows[i:i + 100]
        # 중복 처리: on_conflict로 upsert
        payload = json.dumps(chunk).encode()
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/cx_full_messages?on_conflict=chat_id",
            data=payload,
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
            stored += len(chunk)
            print(f"      {stored}/{len(new_rows)} 저장됨")
        except urllib.error.HTTPError as e:
            print(f"      [warn] chunk {i//100} 실패: {e.code} {e.read().decode()[:200]}")

    print(f"\n[완료] 신규 {stored}건, 실패 {errors}건")
