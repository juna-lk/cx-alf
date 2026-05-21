from __future__ import annotations
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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


def fetch_all_managers() -> dict:
    """채널톡 매니저 목록 → {id: name} 매핑. 한 번 호출 후 캐시 권장."""
    url = f"{CT_BASE}/open/v5/managers?limit=500"
    req = urllib.request.Request(url, headers=CT_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return {m.get("id", ""): m.get("name", "") for m in data.get("managers", [])}
    except Exception as e:
        print(f"[warn] 매니저 목록 fetch 실패: {e}")
        return {}


def parse_messages(raw_messages: list, manager_map: dict | None = None) -> list:
    """Channel Talk 메시지 배열을 정규화된 형식으로 변환.

    저장 필드:
      - role: customer / agent / alf / system
      - text: 본문 (plainText)
      - time: KST 'YYYY-MM-DD HH:MM:SS' (초 단위)
      - created_ms: epoch ms (정렬 키)
      - manager: 매니저 이름 (manager_map에서 매핑)
      - person_id: 채널톡 personId 원본 (manager_map에 없는 신규 매니저 대응용)
      - private: 내부 메모 여부 (options에 'private')
      - type: text / image / file / workflow / form / meet / log (추론)
      - language: 채널톡 감지 메시지 언어 (있을 때만)
      - attachments: files 메타 배열 (있을 때만, name/size/content_type/bucket/key)
    """
    role_map = {
        "user": "customer",
        "manager": "agent",
        "bot": "alf",
        "system": "system",
    }
    manager_map = manager_map or {}
    result = []
    for m in raw_messages:
        text = (m.get("plainText") or m.get("text") or "").strip()
        files_raw = m.get("files") or []

        # 본문도 첨부도 없으면 스킵 (단, 워크플로/폼/미팅 같은 인터랙티브는 보존)
        if not text and not files_raw and not (m.get("workflow") or m.get("form") or m.get("meet") or m.get("buttons")):
            continue

        person_type = m.get("personType", "system")
        role = role_map.get(person_type, "system")
        person_id = m.get("personId") or ""

        # 매니저 이름
        manager_name = ""
        if person_type == "manager":
            manager_name = manager_map.get(person_id, "")

        # 내부 메모 여부 — options는 배열로 반환 (예: ["private", "silentToUser", ...])
        options = m.get("options")
        if isinstance(options, list):
            is_private = "private" in options
        elif isinstance(options, dict):
            is_private = bool(options.get("private"))
        else:
            is_private = False

        # KST timestamp (초 단위까지 보존)
        created_ms = m.get("createdAt", 0) or 0
        if created_ms:
            time_str = datetime.fromtimestamp(created_ms / 1000, tz=KST).strftime("%Y-%m-%d %H:%M:%S")
        else:
            time_str = ""

        # 첨부 메타 추출
        attachments = []
        for f in files_raw:
            if isinstance(f, dict):
                attachments.append({
                    "type": f.get("type") or "file",
                    "name": f.get("name") or "",
                    "size": f.get("size") or 0,
                    "content_type": f.get("contentType") or "",
                    "bucket": f.get("bucket") or "",
                    "key": f.get("key") or "",
                })

        # 메시지 종류 추론
        has_image = any(
            (a.get("type") == "image" or a.get("content_type", "").startswith("image/"))
            for a in attachments
        )
        if m.get("workflow"):
            msg_type = "workflow"
        elif m.get("form"):
            msg_type = "form"
        elif m.get("meet"):
            msg_type = "meet"
        elif m.get("buttons"):
            msg_type = "buttons"
        elif has_image:
            msg_type = "image"
        elif attachments:
            msg_type = "file"
        else:
            msg_type = "text"

        msg = {
            "role": role,
            "text": text,
            "time": time_str,
            "created_ms": int(created_ms),
            "manager": manager_name,
            "person_id": person_id,
            "private": is_private,
            "type": msg_type,
        }
        # 옵션 필드는 값이 있을 때만 (jsonb 사이즈 절약)
        if m.get("language"):
            msg["language"] = m["language"]
        if attachments:
            msg["attachments"] = attachments
        result.append(msg)
    # 시간순 정렬 — created_ms(ms epoch) 기준 안정 정렬
    result.sort(key=lambda m: m.get("created_ms") or 0)
    return result


def build_row(chat: dict, messages: list) -> dict:
    """채팅 데이터 + 메시지 → cx_full_messages row"""
    created_ms = chat.get("createdAt") or 0
    dt = datetime.fromtimestamp(created_ms / 1000, tz=KST)
    chat_id = chat["id"]
    return {
        "chat_id": chat_id,
        "date": dt.strftime("%Y-%m-%d"),
        "tags": chat.get("tags") or [],
        "messages": messages,
        "handling_type": chat.get("handlingType") or "none",
        "csat_score": chat.get("csatScore"),
        "message_count": len(messages),
        "alf_tried": any(m["role"] == "alf" for m in messages),
        "assignee_id": str(chat.get("assigneeId") or ""),
        "assignee_name": chat.get("assigneeName") or "",
        "url": f"https://desk.channel.io/liveklass/user-chats/{chat_id}",
    }


def fetch_messages_for_chat(chat_id: str) -> list:
    """단일 상담방 전체 메시지 수집"""
    url = f"{CT_BASE}/open/v5/user-chats/{chat_id}/messages?limit=200"
    req = urllib.request.Request(url, headers=CT_HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return data.get("messages", [])


def fetch_chat_detail(chat_id: str) -> dict:
    """단일 상담의 상세 정보(tags 포함) — Supabase miss 시 fallback용"""
    url = f"{CT_BASE}/open/v5/user-chats/{chat_id}"
    req = urllib.request.Request(url, headers=CT_HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return data.get("userChat") or {}


def get_existing_chat_ids() -> set:
    """이미 수집된 chat_id 목록 조회"""
    url = f"{SUPABASE_URL}/rest/v1/cx_full_messages?select=chat_id&limit=50000"
    rows = supabase_get(url, SUPABASE_SERVICE_KEY)
    return {r["chat_id"] for r in rows}


def collect_and_store(days: int = DEFAULT_COLLECT_DAYS) -> dict:
    """최근 N일 상담 전체 메시지 수집 + Supabase 저장"""
    existing_ids = get_existing_chat_ids()
    manager_map = fetch_all_managers()
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
            oldest_seen = float("inf")
            for c in batch:
                created = c.get("createdAt", 0) / 1000
                if created < oldest_seen:
                    oldest_seen = created
                if created >= since.timestamp():
                    in_range.append(c)
            all_chats.extend(in_range)
            next_cursor = data.get("next")
            # 종료 조건: 다음 페이지 없거나, batch 비어있거나, 가장 옛날 데이터가
            # since보다 7일 이상 옛날일 때만 stop (경계 페이지 누락 방지 buffer)
            buffer_cutoff = since.timestamp() - 7 * 86400
            if not next_cursor or not batch or oldest_seen < buffer_cutoff:
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
            messages = parse_messages(raw_msgs, manager_map)
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
        if not self._check_auth():
            return
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}
        days = int(body.get("days", DEFAULT_COLLECT_DAYS))

        if not CT_ACCESS_KEY or not SUPABASE_URL:
            self._respond(500, {"ok": False, "error": "환경 변수 없음"})
            return

        result = collect_and_store(days)
        self._respond(200, {"ok": True, **result})
