"""collect_and_store 디버깅용."""
from __future__ import annotations
import os
import sys
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

ENV_PATH = Path(__file__).parent / ".env"
for line in ENV_PATH.read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

CT_ACCESS_KEY = os.environ.get("CHANNELTALK_ACCESS_KEY", "")
CT_ACCESS_SECRET = os.environ.get("CHANNELTALK_ACCESS_SECRET", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

CT_HEADERS = {
    "x-access-key": CT_ACCESS_KEY,
    "x-access-secret": CT_ACCESS_SECRET,
    "Content-Type": "application/json",
}
DAYS = 96
since = datetime.now(timezone.utc) - timedelta(days=DAYS)
print(f"since (UTC): {since.isoformat()}")
print(f"since timestamp: {since.timestamp()}")
print()

for state in ("closed", "opened", "snoozed", "initial", "missed"):
    url = f"https://api.channel.io/open/v5/user-chats?limit=500&sortOrder=desc&state={state}"
    try:
        req = urllib.request.Request(url, headers=CT_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        batch = data.get("userChats", [])
        in_range = [c for c in batch if c.get("createdAt", 0) / 1000 >= since.timestamp()]
        print(f"[{state}] batch={len(batch)}, in_range={len(in_range)}, next={data.get('next')}")
        if batch and not in_range:
            # 첫 chat이 since 이전이면 모든 chat이 더 오래된 거
            oldest_ms = batch[-1].get("createdAt", 0)
            newest_ms = batch[0].get("createdAt", 0)
            print(f"  → 첫 chat 시각: {datetime.fromtimestamp(newest_ms/1000)}")
            print(f"  → 마지막 chat 시각: {datetime.fromtimestamp(oldest_ms/1000)}")
    except urllib.error.HTTPError as e:
        print(f"[{state}] HTTP 에러: {e.code} {e.reason}")
        print(f"  → {e.read().decode()[:200]}")
    except Exception as e:
        print(f"[{state}] 에러: {type(e).__name__}: {e}")
