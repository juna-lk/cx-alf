from __future__ import annotations
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json

from _alf_common import call_anthropic, supabase_get, extract_json, make_handler_base
from alf_search import (
    filter_by_keyword,
    filter_by_semantic,
    build_cluster_prompt,
    CLUSTER_SAMPLE_LIMIT,
)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# 태그 없이 전체 cx_full_messages에서 검색하는 엔드포인트.
# 최근 1000건 가져온 뒤 keyword 또는 semantic 필터링으로 좁힘.
FETCH_LIMIT = 1000

_Base = make_handler_base()


class handler(_Base):
    def do_POST(self):
        if not self._check_auth():
            return
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}
        keyword = body.get("keyword", "").strip()
        ai_search = bool(body.get("ai_search", False))
        do_cluster = bool(body.get("cluster", False))

        if not keyword:
            self._respond(400, {"ok": False, "error": "keyword 필요"})
            return

        # 최근 N건 fetch (태그 무관)
        url = (f"{SUPABASE_URL}/rest/v1/cx_full_messages"
               f"?select=chat_id,messages,tags,date,assignee_name,csat_score"
               f"&order=date.desc&limit={FETCH_LIMIT}")
        chats = supabase_get(url, SUPABASE_SERVICE_KEY)

        if ai_search:
            # 시맨틱 전 keyword pre-filter로 LLM 부하 절감
            pre = filter_by_keyword(chats, keyword)
            filtered = filter_by_semantic(pre if pre else chats, keyword)
        else:
            filtered = filter_by_keyword(chats, keyword)

        if not filtered:
            self._respond(200, {"ok": True, "total": 0, "chats": [], "clusters": []})
            return

        # 클러스터링 (선택)
        clusters = []
        if do_cluster and len(filtered) >= 3:
            prompt, _ = build_cluster_prompt(filtered, keyword)
            try:
                raw = call_anthropic(prompt, max_tokens=1024, api_key=OPENAI_API_KEY)
                cluster_data = extract_json(raw)
                clusters = cluster_data.get("clusters", []) if isinstance(cluster_data, dict) else []
            except Exception:
                clusters = []

            n = len(filtered)
            assigned = set()
            for cl in clusters:
                indices = cl.get("chat_indices", []) if isinstance(cl.get("chat_indices"), list) else []
                chat_ids = []
                for idx in indices:
                    try:
                        i = int(idx)
                    except (TypeError, ValueError):
                        continue
                    if 0 <= i < n and i not in assigned:
                        chat_ids.append(filtered[i]["chat_id"])
                        assigned.add(i)
                cl["chat_ids"] = chat_ids
                cl["count"] = len(chat_ids)
                cl.pop("chat_indices", None)

        # 응답 사이즈 절감 — 메시지 본문은 최대 30개까지
        compact = []
        for c in filtered[:CLUSTER_SAMPLE_LIMIT * 2]:
            msgs = c.get("messages") or []
            compact.append({
                "chat_id": c.get("chat_id"),
                "date": c.get("date"),
                "tags": c.get("tags") or [],
                "messages": msgs[:30],
                "assignee_name": c.get("assignee_name") or "",
                "csat_score": c.get("csat_score"),
            })

        self._respond(200, {
            "ok": True,
            "total": len(filtered),
            "chats": compact,
            "clusters": clusters,
        })
