"""프론트엔드용 데이터 API — 모든 alf_drafts/alf_rules/tags 읽기·쓰기·삭제 처리.

기존에 anon key로 직접 Supabase에 PATCH/DELETE/SELECT 하던 부분을 모두 이 API로 이관.
인증 필수 (APP_AUTH_TOKEN), service_key로 백엔드에서 처리.
"""
from __future__ import annotations
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json
import urllib.parse

from _alf_common import supabase_get, supabase_post, supabase_delete, make_handler_base

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def _safe_uuid(value: str) -> str | None:
    """UUID 형식만 통과시킨다 (PostgREST URL 주입 방어)."""
    import re as _re
    if not value:
        return None
    if _re.fullmatch(r"[0-9a-fA-F-]{8,40}", value):
        return value
    return None


_Base = make_handler_base()


class handler(_Base):
    def do_GET(self):
        if not self._check_auth():
            return
        # 쿼리스트링 파싱
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)

        type_ = (params.get("type", [""])[0] or "").strip()
        if type_ == "drafts":
            self._list_drafts(params)
        elif type_ == "draft":
            self._get_draft(params)
        elif type_ == "rules":
            self._list_rules()
        elif type_ == "rule":
            self._get_rule(params)
        elif type_ == "tags":
            self._list_tags()
        elif type_ == "stats":
            self._collect_stats()
        elif type_ == "chat":
            self._get_chat(params)
        elif type_ == "chats":
            self._get_chats(params)
        else:
            self._respond(400, {"ok": False, "error": "type 필요 (drafts|draft|rules|rule|tags|stats|chat|chats)"})

    def do_POST(self):
        if not self._check_auth():
            return
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}
        action = (body.get("action") or "").strip()
        if action == "save_draft":
            self._save_draft(body)
        elif action == "update_status":
            self._update_status(body)
        elif action == "delete_draft":
            self._delete_draft(body)
        elif action == "delete_rule":
            self._delete_rule(body)
        else:
            self._respond(400, {"ok": False, "error": "action 필요 (save_draft|update_status|delete_draft|delete_rule)"})

    # -------------------- 읽기 --------------------

    def _list_drafts(self, params: dict):
        status = (params.get("status", [""])[0] or "").strip()
        fmt = (params.get("format", [""])[0] or "").strip()
        url = (f"{SUPABASE_URL}/rest/v1/alf_drafts"
               f"?select=id,title,subtitle,content,cluster_label,format,variations,source_chat_count,created_at,status"
               f"&order=created_at.desc")
        if status in ("draft", "applied"):
            url += f"&status=eq.{status}"
        if fmt in ("article", "faq"):
            url += f"&format=eq.{fmt}"
        rows = supabase_get(url, SUPABASE_SERVICE_KEY)
        self._respond(200, {"ok": True, "drafts": rows})

    def _get_draft(self, params: dict):
        did = _safe_uuid(params.get("id", [""])[0])
        if not did:
            self._respond(400, {"ok": False, "error": "유효하지 않은 id"})
            return
        url = (f"{SUPABASE_URL}/rest/v1/alf_drafts"
               f"?id=eq.{did}&select=id,title,subtitle,content,format,variations,status,cluster_label")
        rows = supabase_get(url, SUPABASE_SERVICE_KEY)
        self._respond(200, {"ok": True, "draft": rows[0] if rows else None})

    def _get_chat(self, params: dict):
        """단일 chat 상세 (messages 전체)"""
        import re
        cid = (params.get("id", [""])[0] or "").strip()
        if not re.match(r"^[A-Za-z0-9_-]{1,64}$", cid):
            self._respond(400, {"ok": False, "error": "유효하지 않은 chat_id"})
            return
        url = (f"{SUPABASE_URL}/rest/v1/cx_full_messages"
               f"?chat_id=eq.{cid}&select=chat_id,date,tags,messages,url,assignee_name,csat_score,handling_type")
        rows = supabase_get(url, SUPABASE_SERVICE_KEY)
        self._respond(200, {"ok": True, "chat": rows[0] if rows else None})

    def _get_chats(self, params: dict):
        """여러 chat의 미리보기 (첫 customer/agent 메시지 + 메타)"""
        import re
        ids_raw = (params.get("ids", [""])[0] or "").strip()
        ids = [i for i in ids_raw.split(",") if re.match(r"^[A-Za-z0-9_-]{1,64}$", i)]
        if not ids:
            self._respond(400, {"ok": False, "error": "ids 필요"})
            return
        ids = ids[:50]  # 한 번에 최대 50건
        ids_param = ",".join(f'"{i}"' for i in ids)
        url = (f"{SUPABASE_URL}/rest/v1/cx_full_messages"
               f"?chat_id=in.({ids_param})"
               f"&select=chat_id,date,messages,url,assignee_name,csat_score"
               f"&order=date.desc")
        rows = supabase_get(url, SUPABASE_SERVICE_KEY)
        # 미리보기용으로 메시지 압축 (첫 고객·상담원 메시지 + 메시지 수)
        previews = []
        for r in rows:
            msgs = r.get("messages") or []
            first_customer = next((m.get("text", "") for m in msgs if m.get("role") == "customer"), "")
            first_agent = next((m.get("text", "") for m in msgs if m.get("role") == "agent"), "")
            previews.append({
                "chat_id": r["chat_id"],
                "date": r.get("date"),
                "url": r.get("url") or f"https://desk.channel.io/liveklass/user-chats/{r['chat_id']}",
                "assignee_name": r.get("assignee_name") or "",
                "csat_score": r.get("csat_score"),
                "message_count": len(msgs),
                "first_customer": first_customer[:150],
                "first_agent": first_agent[:150],
            })
        self._respond(200, {"ok": True, "chats": previews})

    def _list_rules(self):
        url = f"{SUPABASE_URL}/rest/v1/alf_rules?select=*&order=created_at.desc"
        rows = supabase_get(url, SUPABASE_SERVICE_KEY)
        self._respond(200, {"ok": True, "rules": rows})

    def _get_rule(self, params: dict):
        rid = _safe_uuid(params.get("id", [""])[0])
        if not rid:
            self._respond(400, {"ok": False, "error": "유효하지 않은 id"})
            return
        url = f"{SUPABASE_URL}/rest/v1/alf_rules?id=eq.{rid}&select=content"
        rows = supabase_get(url, SUPABASE_SERVICE_KEY)
        self._respond(200, {"ok": True, "rule": rows[0] if rows else None})

    def _collect_stats(self):
        """cx_full_messages 수집 현황 통계 (최초/최신 일자, 총 건수, 마지막 업데이트)."""
        # 가장 오래된 + 가장 최신 + 총 건수
        oldest_url = f"{SUPABASE_URL}/rest/v1/cx_full_messages?select=date,collected_at&order=date.asc&limit=1"
        newest_url = f"{SUPABASE_URL}/rest/v1/cx_full_messages?select=date,collected_at&order=date.desc&limit=1"
        last_updated_url = f"{SUPABASE_URL}/rest/v1/cx_full_messages?select=collected_at&order=collected_at.desc&limit=1"
        count_url = f"{SUPABASE_URL}/rest/v1/cx_full_messages?select=chat_id"

        oldest = supabase_get(oldest_url, SUPABASE_SERVICE_KEY)
        newest = supabase_get(newest_url, SUPABASE_SERVICE_KEY)
        last_updated = supabase_get(last_updated_url, SUPABASE_SERVICE_KEY)

        # Count via Prefer header (HEAD-like 카운트는 별도 헤더 필요. 여기선 간단히 limit 으로 추정)
        # PostgREST의 정확한 카운트를 위해 Prefer: count=exact 사용
        import urllib.request as _req
        req = _req.Request(
            f"{SUPABASE_URL}/rest/v1/cx_full_messages?select=chat_id&limit=1",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Prefer": "count=exact",
                "Range-Unit": "items",
                "Range": "0-0",
            },
        )
        try:
            with _req.urlopen(req, timeout=10) as resp:
                content_range = resp.headers.get("Content-Range", "")
                # "0-0/1234" 형식 → 1234 추출
                total = int(content_range.split("/")[-1]) if "/" in content_range else 0
        except Exception:
            total = 0

        self._respond(200, {
            "ok": True,
            "stats": {
                "oldest_date": oldest[0].get("date") if oldest else None,
                "newest_date": newest[0].get("date") if newest else None,
                "last_collected_at": last_updated[0].get("collected_at") if last_updated else None,
                "total": total,
            },
        })

    def _list_tags(self):
        """cx_full_messages에서 태그 목록 + 빈도 추출 (자동완성용)."""
        url = f"{SUPABASE_URL}/rest/v1/cx_full_messages?select=tags&limit=10000"
        rows = supabase_get(url, SUPABASE_SERVICE_KEY)
        counts: dict[str, int] = {}
        for r in rows:
            for t in r.get("tags") or []:
                if t:
                    counts[t] = counts.get(t, 0) + 1
        sorted_tags = sorted(counts.items(), key=lambda x: -x[1])
        self._respond(200, {"ok": True, "tags": [t for t, _ in sorted_tags]})

    # -------------------- 쓰기 --------------------

    def _save_draft(self, body: dict):
        did = _safe_uuid(body.get("id"))
        # 허용 필드만 통과 (보안)
        allowed = {"title", "subtitle", "content", "variations", "format"}
        payload = {k: v for k, v in body.items() if k in allowed}
        if not did:
            # 신규 draft 생성 (검증 탭 등 draft id 없는 경로용)
            if not (payload.get("title") and payload.get("content")):
                self._respond(400, {"ok": False, "error": "title과 content가 필요해요"})
                return
            saved = supabase_post(
                f"{SUPABASE_URL}/rest/v1/alf_drafts",
                payload, SUPABASE_SERVICE_KEY,
            )
            new_id = saved[0]["id"] if isinstance(saved, list) and saved else None
            self._respond(200, {"ok": True, "id": new_id})
            return
        if not payload:
            self._respond(400, {"ok": False, "error": "수정할 필드 없음"})
            return
        supabase_post(
            f"{SUPABASE_URL}/rest/v1/alf_drafts?id=eq.{did}",
            payload, SUPABASE_SERVICE_KEY, method="PATCH",
        )
        self._respond(200, {"ok": True})

    def _update_status(self, body: dict):
        did = _safe_uuid(body.get("id"))
        status = body.get("status", "")
        if not did or status not in ("draft", "applied"):
            self._respond(400, {"ok": False, "error": "id, status (draft|applied) 필요"})
            return
        supabase_post(
            f"{SUPABASE_URL}/rest/v1/alf_drafts?id=eq.{did}",
            {"status": status}, SUPABASE_SERVICE_KEY, method="PATCH",
        )
        self._respond(200, {"ok": True})

    def _delete_draft(self, body: dict):
        did = _safe_uuid(body.get("id"))
        if not did:
            self._respond(400, {"ok": False, "error": "유효하지 않은 id"})
            return
        supabase_delete(
            f"{SUPABASE_URL}/rest/v1/alf_drafts?id=eq.{did}",
            SUPABASE_SERVICE_KEY,
        )
        self._respond(200, {"ok": True})

    def _delete_rule(self, body: dict):
        rid = _safe_uuid(body.get("id"))
        if not rid:
            self._respond(400, {"ok": False, "error": "유효하지 않은 id"})
            return
        supabase_delete(
            f"{SUPABASE_URL}/rest/v1/alf_rules?id=eq.{rid}",
            SUPABASE_SERVICE_KEY,
        )
        self._respond(200, {"ok": True})
