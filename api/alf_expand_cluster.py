"""클러스터 상세 탐색 — 선택한 세부 유형에 대해 같은 태그 풀에서 의미적으로 가까운 채팅을 추가로 25건까지 끌어와 패턴 분석 풀을 확장한다.

흐름:
  1. current_chat_ids[0]의 tags 조회
  2. 같은 태그 + current_chat_ids 제외로 후보 최대 200건 fetch
  3. alf_search.filter_by_semantic으로 cluster_label 의미 유사 limit건 추출
  4. added_chat_ids 반환 (프론트에서 cluster.chat_ids에 머지)
"""
from __future__ import annotations
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json
import urllib.parse

from _alf_common import supabase_get, make_handler_base, is_safe_postgrest_tag

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

_Base = make_handler_base()


class handler(_Base):
    def do_POST(self):
        if not self._check_auth():
            return
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}
        cluster_label = (body.get("cluster_label") or "").strip()
        current_chat_ids = body.get("current_chat_ids") or []
        limit = min(int(body.get("limit") or 25), 30)

        if not cluster_label:
            self._respond(400, {"ok": False, "error": "cluster_label 필요"})
            return
        if not current_chat_ids:
            self._respond(400, {"ok": False, "error": "current_chat_ids 필요"})
            return

        # 1) 현재 클러스터 첫 번째 chat의 tags 조회
        first_id = current_chat_ids[0]
        url1 = (f"{SUPABASE_URL}/rest/v1/cx_full_messages"
                f"?select=chat_id,tags&chat_id=eq.{first_id}&limit=1")
        try:
            first_rows = supabase_get(url1, SUPABASE_SERVICE_KEY)
        except Exception as e:
            self._respond(500, {"ok": False, "error": f"원본 chat 조회 실패: {e}"})
            return
        if not first_rows:
            self._respond(404, {"ok": False, "error": "원본 chat 미존재"})
            return
        origin_tags = first_rows[0].get("tags") or []
        if not origin_tags:
            self._respond(400, {"ok": False,
                                "error": "원본 chat에 tags 없음 — 탐색 불가"})
            return
        if not is_safe_postgrest_tag(origin_tags[0]):
            self._respond(400, {"ok": False,
                                "error": "원본 chat의 태그가 PostgREST 검색에 안전하지 않아요"})
            return

        # 2) 같은 태그의 200건 후보 fetch (현재 클러스터 외)
        safe_tag = origin_tags[0].replace("\\", "\\\\").replace('"', '\\"')
        encoded_tag = urllib.parse.quote(f'{{"{safe_tag}"}}', safe="")
        excl_ids = ",".join(f'"{c}"' for c in current_chat_ids[:80])
        sim_url = (f"{SUPABASE_URL}/rest/v1/cx_full_messages"
                   f"?select=chat_id,messages,tags"
                   f"&tags=cs.{encoded_tag}"
                   f"&chat_id=not.in.({excl_ids})"
                   f"&order=date.desc&limit=200")
        try:
            candidates = supabase_get(sim_url, SUPABASE_SERVICE_KEY)
        except Exception as e:
            self._respond(500, {"ok": False, "error": f"후보 fetch 실패: {e}"})
            return

        # 3) LLM 의미 검색 → limit건 추출
        added: list = []
        if candidates:
            try:
                from alf_search import filter_by_semantic
                added = filter_by_semantic(candidates, cluster_label)[:limit]
            except Exception as e:
                self._respond(500, {"ok": False,
                                    "error": f"의미 검색 실패: {e}"})
                return

        added_chat_ids = [c.get("chat_id") for c in added if c.get("chat_id")]

        self._respond(200, {
            "ok": True,
            "added_chat_ids": added_chat_ids,
            "added_count": len(added_chat_ids),
            "candidates_count": len(candidates),
            "total_count": len(current_chat_ids) + len(added_chat_ids),
            "origin_tag": origin_tags[0],
        })
