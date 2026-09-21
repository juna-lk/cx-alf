#!/usr/bin/env python3
"""채널톡에서 {user_id: 이름} 사전을 만든다 (읽기 전용).

맨몸으로 나오는 이름(`제가 김철수인데요`)은 정규식으로 못 잡는다. 한국어는
띄어쓰기로 낱말이 갈리지 않아 `안녕하세요` 가 `안녕하세` 로 잘려 후보에
걸리기 때문이다(실측: 상담의 99%가 후보로 잡혔다).

대신 '이 상담의 고객이 누구인지' 를 이용한다. 상담마다 user_id 가 있고
채널톡이 그 사람의 이름을 주므로, 그 이름 글자만 정확히 찾아 가리면 된다.
낱말 뭉개짐이 없고 LLM 도 필요 없다.

    python3 build_name_dict.py --out name_dict.json

user-chats 목록 응답에 users[] 가 동봉되므로 상담 1건마다 부르지 않고
페이지 단위로 쓸어 담는다.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ENV_PATH = Path(__file__).parent / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

CT_BASE = "https://api.channel.io"
HEADERS = {
    "x-access-key": os.environ["CHANNELTALK_ACCESS_KEY"],
    "x-access-secret": os.environ["CHANNELTALK_ACCESS_SECRET"],
    "Content-Type": "application/json",
}
STATES = ("closed", "opened", "snoozed", "initial", "missed")


def fetch_page(state: str, cursor: str | None):
    params = f"limit=500&sortOrder=desc&state={state}"
    if cursor:
        params += f"&since={urllib.parse.quote(cursor)}"
    req = urllib.request.Request(f"{CT_BASE}/open/v5/user-chats?{params}",
                                 headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="name_dict.json")
    ap.add_argument("--max-pages", type=int, default=400,
                    help="상태별 페이지 상한 (안전장치)")
    args = ap.parse_args()

    users: dict[str, dict] = {}
    chat_user: dict[str, str] = {}
    t0 = time.time()

    for state in STATES:
        cursor, pages = None, 0
        while pages < args.max_pages:
            try:
                data = fetch_page(state, cursor)
            except Exception as e:
                print(f"  [!] {state} p{pages}: {e}")
                break
            for u in data.get("users", []):
                if u.get("id"):
                    users[u["id"]] = {
                        "name": u.get("name") or "",
                        "email": u.get("email") or "",
                        "mobile": u.get("mobileNumber") or "",
                    }
            for c in data.get("userChats", []):
                if c.get("id") and c.get("userId"):
                    chat_user[c["id"]] = c["userId"]
            pages += 1
            cursor = data.get("next")
            if not cursor or not data.get("userChats"):
                break
            time.sleep(0.05)
        print(f"  {state:<9} {pages}페이지 — 누적 고객 {len(users):,}명, "
              f"상담 {len(chat_user):,}건")

    named = sum(1 for v in users.values() if v["name"])
    Path(args.out).write_text(
        json.dumps({"users": users, "chat_user": chat_user},
                   ensure_ascii=False), encoding="utf-8")
    print(f"\n고객 {len(users):,}명 (이름 있음 {named:,}), "
          f"상담 {len(chat_user):,}건, {time.time()-t0:.0f}초")
    print(f"저장: {args.out}")


if __name__ == "__main__":
    sys.exit(main())
