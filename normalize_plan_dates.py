"""기존 cx_users 의 plan_start_date / plan_end_date 를 'YYYY-MM-DD' 로 정규화.

채널톡 원본이 epoch ms 와 ISO datetime 으로 섞여 들어와 있어 통일한다.
무료 플랜의 이용기간 날짜도 의미있는 값이므로 그대로 보존(변환만).

사용:
  python3 normalize_plan_dates.py          # dry-run
  python3 normalize_plan_dates.py --apply
"""
from __future__ import annotations
import os
import sys
import time
import argparse
import urllib.request
import urllib.error
from pathlib import Path

ENV_PATH = Path(__file__).parent / ".env"
for line in ENV_PATH.read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).parent / "api"))
from _alf_common import supabase_get, supabase_post, _parse_date  # noqa: E402

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
COLS = ("plan_start_date", "plan_end_date")


def fetch_all() -> list[dict]:
    rows, off = [], 0
    while True:
        url = (f"{SUPABASE_URL}/rest/v1/cx_users"
               f"?select=user_id,plan_start_date,plan_end_date&limit=1000&offset={off}")
        b = supabase_get(url, SUPABASE_SERVICE_KEY)
        if not b:
            break
        rows += b
        if len(b) < 1000:
            break
        off += 1000
    return rows


def _patch_retry(url: str, body: dict, retries: int = 5) -> None:
    for attempt in range(retries):
        try:
            supabase_post(url, body, SUPABASE_SERVICE_KEY, method="PATCH")
            return
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    dry = not args.apply
    print(f"=== plan 날짜 정규화 ({'DRY-RUN' if dry else 'APPLY'}) ===\n")

    rows = fetch_all()
    print(f"cx_users {len(rows)}건 조회\n")

    changes = []
    for r in rows:
        new = {c: _parse_date(r.get(c)) for c in COLS}
        if any(new[c] != r.get(c) for c in COLS):
            changes.append((r["user_id"], r, new))

    print(f"변환 대상: {len(changes)}건 / 변경 없음: {len(rows) - len(changes)}건")
    print("\n샘플 before → after 5건:")
    for uid, old, new in changes[:5]:
        print(f"  start: {str(old.get('plan_start_date'))[:24]:<26} → {new['plan_start_date']}")
        print(f"  end  : {str(old.get('plan_end_date'))[:24]:<26} → {new['plan_end_date']}")
        print()

    if dry:
        print("[dry-run] 쓰기 없음. --apply 로 실제 실행.")
        return

    done = 0
    for uid, old, new in changes:
        url = f"{SUPABASE_URL}/rest/v1/cx_users?user_id=eq.{uid}"
        _patch_retry(url, new)
        done += 1
        time.sleep(0.02)
        if done % 1000 < 1:
            print(f"  {done}/{len(changes)} PATCH...")
    print(f"\n[완료] {done}건 정규화")


if __name__ == "__main__":
    main()
