"""견고한 백필 — 모든 페이지 끝까지 페이지네이션하면서 chat 수집.

사용:
    python3 run_collect_full.py --all  # 전체 데이터 (날짜 제한 없음)
    python3 run_collect_full.py 138    # 138일치
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
_env_lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
for line in _env_lines:
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).parent / "api"))
from _alf_common import supabase_get, supabase_post  # noqa: E402
from alf_collect import fetch_messages_for_chat, parse_messages, build_row, fetch_all_managers  # noqa: E402

CT_HEADERS = {
    "x-access-key": os.environ["CHANNELTALK_ACCESS_KEY"],
    "x-access-secret": os.environ["CHANNELTALK_ACCESS_SECRET"],
    "Content-Type": "application/json",
}
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]


def get_existing_chat_ids() -> set:
    """이미 수집된 chat_id (Supabase 기본 max-rows=1000이라 1000건 단위로 페이지네이션)"""
    existing = set()
    offset = 0
    PAGE = 1000
    while True:
        url = f"{SUPABASE_URL}/rest/v1/cx_full_messages?select=chat_id&limit={PAGE}&offset={offset}"
        rows = supabase_get(url, SUPABASE_SERVICE_KEY)
        if not rows:
            break
        existing.update(r["chat_id"] for r in rows)
        print(f"      ... offset={offset}, 조회 {len(rows)}건 (누적 {len(existing)})")
        if len(rows) < PAGE:
            break
        offset += PAGE
    return existing


def fetch_chats_full(state: str, since_ts: float = 0) -> list:
    """state별 모든 페이지를 끝까지 가져온 후 date 필터링.

    since_ts=0 이면 날짜 제한 없이 전체 수집.
    안전장치: 200페이지(=100,000건)를 초과하면 중단.
    """
    all_chats = []
    cursor = None
    page = 0
    while page < 200:
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
        if since_ts > 0:
            in_range = [c for c in batch if c.get("createdAt", 0) / 1000 >= since_ts]
        else:
            in_range = batch
        all_chats.extend(in_range)
        oldest = min((c.get("createdAt", 0) for c in batch), default=0) / 1000 if batch else 0
        print(f"      [{state}] page{page}: batch={len(batch)}, collected={len(in_range)}, oldest={datetime.fromtimestamp(oldest).strftime('%Y-%m-%d') if oldest else 'N/A'}")
        next_cursor = data.get("next")
        if not next_cursor or not batch:
            break
        # 날짜 필터 있을 때만 cutoff 체크
        if since_ts > 0 and oldest > 0 and oldest < since_ts - (7 * 86400):
            print(f"      [{state}] cutoff 도달 — 페이지네이션 종료")
            break
        cursor = next_cursor
        time.sleep(0.15)
    return all_chats


if __name__ == "__main__":
    args = sys.argv[1:]
    all_mode = "--all" in args
    force_update = "--force-update" in args
    days_arg = next((a for a in args if a.isdigit()), None)

    if all_mode or force_update:
        since_ts_val = 0.0
        print(f"[start] 전체 데이터 수집 (날짜 제한 없음){' · 강제 업데이트 모드' if force_update else ''}")
    else:
        days = int(days_arg) if days_arg else 138
        since = datetime.now(timezone.utc) - timedelta(days=days)
        since_ts_val = since.timestamp()
        print(f"[start] {days}일치 수집 (since {since.date()})")
    print()

    print("[0/5] 채널톡 매니저 목록 조회...")
    manager_map = fetch_all_managers()
    print(f"      매니저 {len(manager_map)}명 캐시 완료")
    print()

    print("[1/5] 기존 chat_id 조회...")
    existing = get_existing_chat_ids()
    print(f"      기존: {len(existing)}건{' (force-update 모드 — 모두 재처리)' if force_update else ''}")
    print()

    print("[2/5] 채널톡 채팅 목록 수집 (state별 끝까지 페이지네이션)...")
    all_chats = []
    for state in ("closed", "opened", "snoozed", "initial", "missed"):
        chats = fetch_chats_full(state, since_ts_val)
        all_chats.extend(chats)
        print(f"   {state}: {len(chats)}건 누적")
    print(f"      총 {len(all_chats)}건 (중복 포함)")
    print()

    # 중복 제거
    unique = {c["id"]: c for c in all_chats}
    print(f"[3/5] 중복 제거: {len(unique)}건")
    if force_update:
        new_chats = unique  # 모두 다시 처리
    else:
        new_chats = {cid: c for cid, c in unique.items() if cid not in existing}
    print(f"      처리 대상: {len(new_chats)}건")
    print()

    def upsert_chunk(chunk: list, chunk_idx: int) -> bool:
        """chunk를 Supabase에 upsert. timeout/network 에러 3회 재시도. 성공 여부 반환."""
        payload = json.dumps(chunk).encode()
        for attempt in range(3):
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
                with urllib.request.urlopen(req, timeout=60) as resp:
                    resp.read()
                return True
            except urllib.error.HTTPError as e:
                body = e.read().decode()[:200] if hasattr(e, "read") else ""
                print(f"      [warn] chunk {chunk_idx} HTTP {e.code} (attempt {attempt+1}/3): {body}")
                if attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                return False
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                print(f"      [warn] chunk {chunk_idx} network (attempt {attempt+1}/3): {e}")
                if attempt < 2:
                    time.sleep(10 * (attempt + 1))
                    continue
                return False
        return False

    print("[4/5] 메시지 수집 + 즉시 저장 (chunk 단위)...")
    buffer = []
    errors = 0
    stored = 0
    chunk_idx = 0
    total = len(new_chats)
    CHUNK_SIZE = 100

    for i, (cid, chat) in enumerate(new_chats.items()):
        try:
            raw = fetch_messages_for_chat(cid)
            messages = parse_messages(raw, manager_map)
            buffer.append(build_row(chat, messages))
            time.sleep(0.12)
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"      [warn] {cid}: {e}")

        # 100건 모이면 즉시 저장
        if len(buffer) >= CHUNK_SIZE:
            if upsert_chunk(buffer, chunk_idx):
                stored += len(buffer)
            chunk_idx += 1
            buffer = []
            print(f"      ... {i + 1}/{total} 수집, {stored}건 저장됨 (실패 {errors})")
        elif (i + 1) % 50 == 0:
            print(f"      ... {i + 1}/{total} 수집 중 (실패 {errors})")

    # 남은 buffer 저장
    if buffer:
        if upsert_chunk(buffer, chunk_idx):
            stored += len(buffer)
        print(f"      ... 최종 {total}/{total} 수집, {stored}건 저장됨")

    print(f"\n[완료] 신규 {stored}건 저장, 메시지 수집 실패 {errors}건")
