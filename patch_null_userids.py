"""cx_full_messages / cx_chats 에서 user_id 가 비어있는 행만 골라 PATCH.

전체 재PATCH는 느려서(20k건), null 인 것만 타겟팅한다.
채널톡 전체 스윕으로 chat_to_user 만든 뒤 null chat_id 만 채운다.
"""
from __future__ import annotations
import os, sys, json, time, urllib.request
from pathlib import Path

ENV_PATH = Path(__file__).parent / ".env"
for line in ENV_PATH.read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).parent / "api"))
from _alf_common import supabase_get  # noqa: E402
from backfill_user_ids import sweep_channeltalk, patch_user_ids  # noqa: E402

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]


def fetch_null_chat_ids(table: str) -> list[str]:
    """user_id 가 null 인 chat_id 전체 (페이지네이션)."""
    ids, off, PAGE = [], 0, 1000
    while True:
        url = f"{SUPABASE_URL}/rest/v1/{table}?select=chat_id&user_id=is.null&limit={PAGE}&offset={off}"
        rows = supabase_get(url, SUPABASE_SERVICE_KEY)
        if not rows:
            break
        ids.extend(r["chat_id"] for r in rows if r.get("chat_id"))
        if len(rows) < PAGE:
            break
        off += PAGE
    return ids


def main():
    print("[1/3] 채널톡 전체 스윕 → chat_to_user...", flush=True)
    chat_to_user, _ = sweep_channeltalk()
    print(f"  매핑 {len(chat_to_user)}건", flush=True)

    for table in ("cx_full_messages", "cx_chats"):
        print(f"\n[2/3] {table} user_id null 조회...", flush=True)
        null_ids = fetch_null_chat_ids(table)
        matched = [c for c in null_ids if c in chat_to_user]
        print(f"  null {len(null_ids)}건 · 채널톡 매칭 {len(matched)}건", flush=True)
        if not matched:
            print(f"  → PATCH 대상 없음", flush=True)
            continue
        print(f"[3/3] {table} PATCH {len(matched)}건...", flush=True)
        n = patch_user_ids(table, chat_to_user, matched)
        print(f"  {table} PATCH 완료 {n}건", flush=True)

    print("\n[완료]", flush=True)


if __name__ == "__main__":
    main()
