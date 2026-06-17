"""기존 누적 데이터에 채널톡 user_id 백필 (1회용).

채널톡 user-chats 전체를 1회 스윕해서
  - chat_to_user = {chat_id: user_id}
  - user_map     = {user_id: user 객체}
를 만든 뒤, cx_users / cx_full_messages / cx_chats 를 한 번에 채운다.

사용:
  python3 backfill_user_ids.py            # dry-run (기본, 쓰기 없음)
  python3 backfill_user_ids.py --apply    # 실제 적재
"""
from __future__ import annotations
import os
import sys
import json
import time
import argparse
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from collections import defaultdict

ENV_PATH = Path(__file__).parent / ".env"
for line in ENV_PATH.read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).parent / "api"))
from _alf_common import (  # noqa: E402
    supabase_get, supabase_post, supabase_upsert, user_to_row,
)

CT_BASE = "https://api.channel.io"
CT_HEADERS = {
    "x-access-key": os.environ["CHANNELTALK_ACCESS_KEY"],
    "x-access-secret": os.environ["CHANNELTALK_ACCESS_SECRET"],
    "Content-Type": "application/json",
}
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]


def mask(v):
    if not isinstance(v, str) or len(v) <= 4:
        return v
    return v[:3] + "***" + v[-2:]


def sweep_channeltalk() -> tuple[dict, dict]:
    """user-chats 전체 스윕 → (chat_to_user, user_map)."""
    chat_to_user = {}
    user_map = {}
    for state in ("closed", "opened", "snoozed", "initial", "missed"):
        cursor = None
        pages = 0
        while True:
            params = f"limit=500&sortOrder=desc&state={state}"
            if cursor:
                params += f"&since={urllib.parse.quote(cursor)}"
            req = urllib.request.Request(f"{CT_BASE}/open/v5/user-chats?{params}", headers=CT_HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
            for u in data.get("users", []):
                if u.get("id"):
                    user_map[u["id"]] = u
            batch = data.get("userChats", [])
            for c in batch:
                if c.get("id") and c.get("userId"):
                    chat_to_user[c["id"]] = c["userId"]
            pages += 1
            cursor = data.get("next")
            if not cursor or not batch:
                break
            time.sleep(0.1)
        print(f"  [{state}] {pages}페이지 스윕")
    return chat_to_user, user_map


def fetch_all_chat_ids(table: str) -> list[str]:
    """테이블의 chat_id 전체 (페이지네이션).

    PostgREST 기본 max-rows=1000 cap 때문에 limit=1000 고정 + offset 증가로 끝까지 읽는다.
    """
    PAGE = 1000
    ids, offset = [], 0
    while True:
        url = f"{SUPABASE_URL}/rest/v1/{table}?select=chat_id&limit={PAGE}&offset={offset}"
        rows = supabase_get(url, SUPABASE_SERVICE_KEY)
        if not rows:
            break
        ids.extend(r["chat_id"] for r in rows if r.get("chat_id"))
        if len(rows) < PAGE:
            break
        offset += PAGE
    return ids


def _patch_with_retry(url: str, body: dict, retries: int = 5) -> None:
    """connection reset 등 일시 오류 재시도 (backoff)."""
    for attempt in range(retries):
        try:
            supabase_post(url, body, SUPABASE_SERVICE_KEY, method="PATCH")
            return
        except Exception as e:
            if attempt == retries - 1:
                raise
            wait = 1.5 * (attempt + 1)
            print(f"    [retry {attempt+1}/{retries-1}] {type(e).__name__}: {e} → {wait:.1f}s 대기")
            time.sleep(wait)


def patch_user_ids(table: str, chat_to_user: dict, target_chat_ids: list[str]) -> int:
    """target chat_id들에 user_id PATCH (user_id별 그룹 + in 필터로 호출 최소화)."""
    by_user = defaultdict(list)
    for cid in target_chat_ids:
        uid = chat_to_user.get(cid)
        if uid:
            by_user[uid].append(cid)
    total = sum(len(v) for v in by_user.values())
    patched = 0
    for uid, cids in by_user.items():
        for i in range(0, len(cids), 50):  # in 필터 URL 길이 안전치
            chunk = cids[i:i + 50]
            in_list = ",".join(chunk)
            url = f"{SUPABASE_URL}/rest/v1/{table}?chat_id=in.({in_list})"
            _patch_with_retry(url, {"user_id": uid})
            patched += len(chunk)
            time.sleep(0.03)  # rate 완화 (connection reset 방지)
            if patched % 2000 < 50:
                print(f"    {table}: {patched}/{total} PATCH...")
    return patched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제 적재 (미지정 시 dry-run)")
    args = ap.parse_args()
    dry = not args.apply

    print(f"=== 채널톡 user_id 백필 ({'DRY-RUN' if dry else 'APPLY'}) ===\n")

    print("[1/4] 채널톡 user-chats 전체 스윕...")
    chat_to_user, user_map = sweep_channeltalk()
    members = sum(1 for u in user_map.values() if u.get("memberId"))
    leads = len(user_map) - members
    print(f"  → user {len(user_map)}명 (member {members} / lead {leads}), "
          f"chat→user 매핑 {len(chat_to_user)}건\n")

    print("[2/4] Supabase 기존 chat_id 조회...")
    fm_ids = fetch_all_chat_ids("cx_full_messages")
    chat_ids = fetch_all_chat_ids("cx_chats")
    fm_match = [c for c in fm_ids if c in chat_to_user]
    chat_match = [c for c in chat_ids if c in chat_to_user]
    print(f"  cx_full_messages: {len(fm_ids)}건 중 매칭 {len(fm_match)} / 미매칭 {len(fm_ids) - len(fm_match)}")
    print(f"  cx_chats(고유 chat_id): {len(set(chat_ids))}건 중 "
          f"매칭 {len(set(chat_match))} / 미매칭 {len(set(chat_ids)) - len(set(chat_match))}\n")

    # 샘플 3건
    print("[3/4] 샘플 (chat_id → user_id / member_id):")
    for cid in fm_match[:3]:
        uid = chat_to_user[cid]
        u = user_map.get(uid, {})
        print(f"  {mask(cid)} → user {mask(uid)} / member {mask(u.get('memberId'))} "
              f"/ plan {u.get('profile', {}).get('plan_name')}")
    print()

    print("[4/4] 적재:")
    if dry:
        n_user_rows = len([u for u in user_map.values() if u.get("id")])
        print(f"  [dry-run] cx_users upsert 예정: {n_user_rows}건")
        print(f"  [dry-run] cx_full_messages PATCH 예정: {len(fm_match)}건")
        print(f"  [dry-run] cx_chats PATCH 예정: {len(set(chat_match))}건 (행 기준은 더 많을 수 있음)")
        print(f"  [dry-run] 쓰기 없음. --apply 로 실제 실행.")
        return

    # cx_users upsert
    user_rows = [user_to_row(u) for u in user_map.values() if u.get("id")]
    us = 0
    for i in range(0, len(user_rows), 100):
        supabase_upsert(f"{SUPABASE_URL}/rest/v1/cx_users",
                        user_rows[i:i + 100], SUPABASE_SERVICE_KEY, on_conflict="user_id")
        us += len(user_rows[i:i + 100])
    print(f"  cx_users upsert {us}건")

    p1 = patch_user_ids("cx_full_messages", chat_to_user, fm_match)
    print(f"  cx_full_messages PATCH {p1}건")
    p2 = patch_user_ids("cx_chats", chat_to_user, list(set(chat_ids)))
    print(f"  cx_chats PATCH {p2}건 (chat_id 매칭 행)")
    print("\n[완료]")


if __name__ == "__main__":
    main()
