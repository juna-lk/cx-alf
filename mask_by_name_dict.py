#!/usr/bin/env python3
"""고객 이름 사전으로 상담 본문의 '맨몸 이름' 을 가린다.

정규식은 `홍길동님`·`예금주: 홍길동` 처럼 표시가 붙은 이름만 잡는다. 표시 없이
나오는 이름(`제가 김철수인데요`)은 한국어 특성상 패턴으로 못 잡는다.

여기서는 '이 상담의 고객이 누구인지' 를 이용한다. build_name_dict.py 가 만든
{chat_id -> 고객 이름} 을 보고, 그 상담 본문에서 그 이름 글자만 찾아 가린다.
다른 낱말은 건드리지 않으므로 `결제`·`환불` 이 뭉개질 일이 없다.

    python3 mask_by_name_dict.py --dict name_dict.json            # dry-run
    python3 mask_by_name_dict.py --dict name_dict.json --apply

가리는 대상은 한글 2~4자 이름만. 닉네임·사이트명(`가방끈`, `아보카도`)은
개인정보가 아니라는 결정에 따라 제외한다.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ENV_PATH = Path(__file__).parent / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
HEADERS = {"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}"}
PAGE = 300

PERSON_NAME_RE = re.compile(r'^[가-힣]{2,4}$')
# 이름이면서 흔한 낱말이기도 한 것. 가리면 본문이 망가지므로 건너뛴다.
UNSAFE_NAMES = {
    "결제", "환불", "취소", "문의", "안내", "확인", "주문", "정산", "수강", "회원",
    "고객", "관리", "강사", "선생", "본부", "대표", "담당", "운영", "판매", "구매",
    "신청", "승인", "거절", "완료", "대기", "진행", "정지", "해지", "변경", "삭제",
    "등록", "조회", "검색", "설정", "저장", "전송", "발송", "수신", "이름", "성함",
    "번호", "금액", "가격", "할인", "쿠폰", "포인트", "적립", "사용", "기간", "날짜",
}


def _req(method, path, body=None, extra=None):
    headers = dict(HEADERS)
    headers.update(extra or {})
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(f"{SUPABASE_URL}/rest/v1/{path}",
                                 data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=90) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def mask_name(name: str) -> str:
    return name[0] + "*" * (len(name) - 1)


def mask_in_messages(messages, name: str):
    """messages 안의 문자열에서 name 을 가린다. (새 messages, 바꾼 횟수)"""
    masked = mask_name(name)
    n = 0

    def walk(v):
        nonlocal n
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, str) and name in v:
            n += v.count(name)
            return v.replace(name, masked)
        return v

    return walk(messages), n


def fetch_page(offset):
    return _req("GET", f"cx_full_messages?select=chat_id,messages"
                       f"&order=chat_id.asc&limit={PAGE}&offset={offset}")


def patch(chat_id, messages):
    cid = urllib.parse.quote(str(chat_id), safe="")
    _req("PATCH", f"cx_full_messages?chat_id=eq.{cid}",
         {"messages": messages}, {"Prefer": "return=minimal"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dict", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    d = json.loads(Path(args.dict).read_text(encoding="utf-8"))
    users, chat_user = d["users"], d["chat_user"]

    # chat_id -> 가릴 이름
    target: dict[str, str] = {}
    skipped_unsafe = set()
    for chat_id, uid in chat_user.items():
        nm = (users.get(uid) or {}).get("name") or ""
        if not PERSON_NAME_RE.match(nm):
            continue
        if nm in UNSAFE_NAMES:
            skipped_unsafe.add(nm)
            continue
        target[chat_id] = nm

    print(f"사전: 고객 {len(users):,}명 / 상담 {len(chat_user):,}건")
    print(f"  가릴 이름이 정해진 상담: {len(target):,}건")
    print(f"  낱말과 겹쳐 건너뛴 이름: {len(skipped_unsafe)}종 "
          f"{sorted(skipped_unsafe)[:8]}")
    print(f"모드: {'적용' if args.apply else 'DRY-RUN (아무것도 안 바꿈)'}\n")

    offsets = list(range(0, args.limit or 24500, PAGE))
    rows_hit = hits = patched = failed = 0
    samples = []
    t0 = time.time()

    def work(off):
        nonlocal rows_hit, hits, patched, failed
        try:
            rows = fetch_page(off)
        except Exception:
            return
        for row in rows or []:
            nm = target.get(row["chat_id"])
            if not nm:
                continue
            new_msgs, n = mask_in_messages(row.get("messages") or [], nm)
            if not n:
                continue
            rows_hit += 1
            hits += n
            if len(samples) < 5:
                samples.append((nm, n))
            if args.apply:
                try:
                    patch(row["chat_id"], new_msgs)
                    patched += 1
                except Exception:
                    failed += 1

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, offsets))

    print(f"이름이 본문에 실제로 나온 상담: {rows_hit:,}건")
    print(f"  가린 횟수: {hits:,}회")
    if args.apply:
        print(f"  반영 {patched:,}건, 실패 {failed:,}건")
    print(f"  {time.time()-t0:.0f}초")
    if samples:
        print("\n  표본 (이름 앞 1글자만):")
        for nm, n in samples:
            print(f"    {mask_name(nm)} — {n}회")
    if not args.apply:
        print("\n실제 반영하려면 --apply 를 붙이세요.")


if __name__ == "__main__":
    sys.exit(main())
