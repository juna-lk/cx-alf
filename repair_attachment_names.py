#!/usr/bin/env python3
"""첨부 파일명 복구.

개인정보 백필 때 mask_deep 이 attachments 의 `name`(파일명)을 사람 이름으로
오인해 image.png → i**** 로 뭉갰다. 채널톡에는 원본이 남아 있으니 다시 받아와
덮어쓴다. 받아온 원문에는 개인정보가 그대로라 저장 전에 고쳐진 규칙으로
가린다(_pii.py — 이제 attachments 밑의 name 은 건드리지 않는다).

    python3 repair_attachment_names.py --list <파일>              # dry-run
    python3 repair_attachment_names.py --list <파일> --apply      # 실제 반영
    python3 repair_attachment_names.py --scan                     # 대상 다시 뽑기

--list 를 안 주면 --scan 으로 대상을 직접 찾는다.
스레드로 동시에 받는다. 채널톡 rate limit 을 생각해 기본 4개만.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ENV_PATH = Path(__file__).parent / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).parent / "api"))
from alf_collect import fetch_messages_for_chat, parse_messages, fetch_all_managers  # noqa: E402
from _pii import mask_messages  # noqa: E402

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
HEADERS = {"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}"}


def _get(path: str):
    req = urllib.request.Request(f"{SUPABASE_URL}/rest/v1/{path}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def scan_damaged() -> list[str]:
    """attachments[].name 에 별표가 든 상담을 찾는다."""
    out, offset = [], 0
    while True:
        rows = _get(f"cx_full_messages?select=chat_id,messages&limit=500&offset={offset}")
        if not rows:
            break
        for row in rows:
            for m in (row.get("messages") or []):
                if any("*" in (a.get("name") or "")
                       for a in (m.get("attachments") or [])):
                    out.append(row["chat_id"])
                    break
        offset += len(rows)
        if offset % 5000 == 0:
            print(f"      …{offset}행 훑음, 대상 {len(out)}건")
    return out


def patch(chat_id: str, messages: list) -> None:
    payload = json.dumps({"messages": messages, "message_count": len(messages)},
                         ensure_ascii=False).encode()
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/cx_full_messages?chat_id=eq.{chat_id}",
        data=payload, method="PATCH",
        headers={**HEADERS, "Content-Type": "application/json",
                 "Prefer": "return=minimal"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        resp.read()


def repair_one(chat_id: str, manager_map: dict, apply: bool):
    """(chat_id, 복구된 파일명 수, 오류) 반환."""
    try:
        raw = fetch_messages_for_chat(chat_id)
        msgs = mask_messages(parse_messages(raw, manager_map))
        names = [a.get("name") for m in msgs for a in (m.get("attachments") or [])]
        restored = sum(1 for n in names if n and "*" not in n)
        if apply:
            patch(chat_id, msgs)
        return (chat_id, restored, None)
    except Exception as e:
        return (chat_id, 0, e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", help="복구할 chat_id 목록 파일 (한 줄에 하나)")
    ap.add_argument("--scan", action="store_true", help="대상을 직접 찾는다")
    ap.add_argument("--apply", action="store_true", help="실제 반영 (없으면 dry-run)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, help="앞 N건만 (시험용)")
    args = ap.parse_args()

    if args.list:
        ids = [l.strip() for l in Path(args.list).read_text().splitlines() if l.strip()]
    elif args.scan:
        print("[0/2] 훼손된 상담 찾는 중...")
        ids = scan_damaged()
    else:
        print("--list 또는 --scan 중 하나가 필요합니다.")
        return 1
    if args.limit:
        ids = ids[:args.limit]

    print(f"대상 {len(ids)}건 — {'적용' if args.apply else 'DRY-RUN (아무것도 안 바꿈)'}")
    print("[1/2] 채널톡 매니저 목록...")
    manager_map = fetch_all_managers()
    print(f"      매니저 {len(manager_map)}명")

    print(f"[2/2] 재수집 + 복구 (동시 {args.workers}개)...")
    ok = failed = restored_total = 0
    errs = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, (cid, restored, err) in enumerate(
                pool.map(lambda c: repair_one(c, manager_map, args.apply), ids)):
            if err:
                failed += 1
                if len(errs) < 5:
                    errs.append((cid, err))
            else:
                ok += 1
                restored_total += restored
            if (i + 1) % 500 == 0:
                el = time.time() - t0
                print(f"      …{i+1}/{len(ids)} (성공 {ok}, 실패 {failed}, "
                      f"{(i+1)/el:.1f}건/초)")

    print(f"\n완료 — 성공 {ok}, 실패 {failed}, 파일명 {restored_total}건 복구, "
          f"{time.time()-t0:.0f}초")
    for cid, e in errs:
        print(f"  [!] {cid}: {e}")
    if not args.apply:
        print("\n실제 반영하려면 --apply 를 붙이세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
