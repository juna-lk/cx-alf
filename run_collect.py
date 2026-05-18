"""로컬에서 96일치 채널톡 데이터를 Supabase에 수집한다.

사용법:
    cd /Users/juna/cx-alf
    python3 run_collect.py            # 기본 96일
    python3 run_collect.py 30         # 30일치
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

# .env 로드 (python-dotenv 없이 직접 파싱)
ENV_PATH = Path(__file__).parent / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())

# api 폴더를 import path에 추가
sys.path.insert(0, str(Path(__file__).parent / "api"))

from alf_collect import collect_and_store, DEFAULT_COLLECT_DAYS  # noqa: E402

if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_COLLECT_DAYS
    print(f"[start] 채널톡 {days}일치 수집 시작...")
    result = collect_and_store(days=days)
    print(f"[done] {result}")
