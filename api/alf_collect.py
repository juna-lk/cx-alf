from __future__ import annotations
import os
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from _alf_common import supabase_get, supabase_post, make_handler_base

CT_ACCESS_KEY = os.environ.get("CHANNELTALK_ACCESS_KEY", "")
CT_ACCESS_SECRET = os.environ.get("CHANNELTALK_ACCESS_SECRET", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
CT_BASE = "https://api.channel.io"
KST = timezone(timedelta(hours=9))
DEFAULT_COLLECT_DAYS = 96  # 약 3개월치 초기 수집

CT_HEADERS = {
    "x-access-key": CT_ACCESS_KEY,
    "x-access-secret": CT_ACCESS_SECRET,
    "Content-Type": "application/json",
}


def parse_messages(raw_messages: list) -> list:
    """Channel Talk 메시지 배열을 {role, text} 형식으로 변환"""
    role_map = {
        "user": "customer",
        "manager": "agent",
        "bot": "alf",
        "system": "system",
    }
    result = []
    for m in raw_messages:
        text = (m.get("plainText") or m.get("text") or "").strip()
        if not text:
            continue
        person_type = m.get("personType", "system")
        result.append({"role": role_map.get(person_type, "system"), "text": text})
    return result


def build_row(chat: dict, messages: list) -> dict:
    """채팅 데이터 + 메시지 → cx_full_messages row"""
    created_ms = chat.get("createdAt") or 0
    dt = datetime.fromtimestamp(created_ms / 1000, tz=KST)
    return {
        "chat_id": chat["id"],
        "date": dt.strftime("%Y-%m-%d"),
        "tags": chat.get("tags") or [],
        "messages": messages,
        "handling_type": chat.get("handlingType") or "none",
        "csat_score": chat.get("csatScore"),
        "message_count": len(messages),
        "alf_tried": any(m["role"] == "alf" for m in messages),
        "assignee_id": str(chat.get("assigneeId") or ""),
        "assignee_name": chat.get("assigneeName") or "",
    }


def fetch_messages_for_chat(chat_id: str) -> list:
    """단일 상담방 전체 메시지 수집"""
    url = f"{CT_BASE}/open/v5/user-chats/{chat_id}/messages?limit=200"
    req = urllib.request.Request(url, headers=CT_HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return data.get("messages", [])


def get_existing_chat_ids() -> set:
    """이미 수집된 chat_id 목록 조회"""
    url = f"{SUPABASE_URL}/rest/v1/cx_full_messages?select=chat_id&limit=50000"
    rows = supabase_get(url, SUPABASE_SERVICE_KEY)
    return {r["chat_id"] for r in rows}


def collect_and_store(days: int = DEFAULT_COLLECT_DAYS) -> dict:
    """최근 N일 상담 전체 메시지 수집 + Supabase 저장"""
    existing_ids = get_existing_chat_ids()
    since = datetime.now(timezone.utc) - timedelta(days=days)

    all_chats = []
    for state in ("closed", "opened", "snoozed", "initial", "missed"):
        cursor = None
        while True:
            params = f"limit=500&sortOrder=desc&state={state}"
            if cursor:
                params += f"&since={urllib.parse.quote(cursor)}"
            req = urllib.request.Request(f"{CT_BASE}/open/v5/user-chats?{params}", headers=CT_HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            batch = data.get("userChats", [])
            in_range = []
            for c in batch:
                created = c.get("createdAt", 0)
                if created / 1000 >= since.timestamp():
                    in_range.append(c)
            all_chats.extend(in_range)
            next_cursor = data.get("next")
            if not next_cursor or not batch or len(in_range) < len(batch):
                break
            cursor = next_cursor
            time.sleep(0.1)

    new_rows = []
    for chat in all_chats:
        cid = chat.get("id", "")
        if cid in existing_ids:
            continue
        try:
            raw_msgs = fetch_messages_for_chat(cid)
            messages = parse_messages(raw_msgs)
            new_rows.append(build_row(chat, messages))
            time.sleep(0.05)
        except Exception as e:
            print(f"[warn] chat {cid} 메시지 수집 실패: {e}")
            continue

    stored = 0
    for i in range(0, len(new_rows), 100):
        chunk = new_rows[i:i + 100]
        supabase_post(
            f"{SUPABASE_URL}/rest/v1/cx_full_messages",
            chunk, SUPABASE_SERVICE_KEY,
        )
        stored += len(chunk)

    return {"collected": len(new_rows), "stored": stored}


_Base = make_handler_base()


class handler(_Base):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}
        days = int(body.get("days", DEFAULT_COLLECT_DAYS))

        if not CT_ACCESS_KEY or not SUPABASE_URL:
            self._respond(500, {"ok": False, "error": "환경 변수 없음"})
            return

        result = collect_and_store(days)
        self._respond(200, {"ok": True, **result})
