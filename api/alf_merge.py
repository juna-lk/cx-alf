from __future__ import annotations
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json

from _alf_common import call_anthropic, supabase_get, supabase_post, make_handler_base

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

MERGE_WRITE_SYSTEM = """당신은 채널톡 ALF용 지식 아티클을 작성하는 전문가입니다.
여러 문서를 통합할 때 원칙:
1. 1문서 = 고객의 1가지 완결된 시나리오
2. 각 원본 문서의 핵심 내용을 ## 섹션으로 구분
3. 중복 내용은 한 번만 작성
4. 전체 2,000자 이내 유지 (초과 시 핵심만 추려서)
5. Markdown 형식 유지"""


_Base = make_handler_base()


class handler(_Base):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}
        draft_ids = body.get("draft_ids", [])

        if len(draft_ids) < 2:
            self._respond(400, {"ok": False, "error": "최소 2개 문서 필요"})
            return

        # 선택된 초안 내용 조회
        ids_param = ",".join(f'"{did}"' for did in draft_ids)
        url = f"{SUPABASE_URL}/rest/v1/alf_drafts?select=id,title,content,source_chat_count&id=in.({ids_param})"
        drafts = supabase_get(url, SUPABASE_SERVICE_KEY)

        if len(drafts) < 2:
            self._respond(404, {"ok": False, "error": "문서를 찾을 수 없음"})
            return

        docs_text = "\n\n---\n\n".join(
            f"## 문서: {d['title']}\n{d['content']}" for d in drafts
        )
        titles = " + ".join(d["title"] for d in drafts)

        prompt = f"""아래 {len(drafts)}개 ALF 지식 문서를 하나의 완결된 아티클로 통합해주세요.

{docs_text}

통합 문서 요구사항:
- 제목: 통합 내용을 가장 잘 표현하는 고객 언어
- 각 원본의 핵심 내용을 섹션으로 구분
- 중복 제거, 2,000자 이내
- Markdown 형식"""

        merged_content = call_anthropic(
            prompt, system=MERGE_WRITE_SYSTEM,
            max_tokens=2048, api_key=GROQ_API_KEY,
        )

        # 제목 추출
        merged_title = titles
        for line in merged_content.split("\n"):
            if line.startswith("# "):
                merged_title = line[2:].strip()
                break

        # 새 통합 초안 저장 (병합은 적용 가이드 대상이므로 status=applied)
        new_draft = {
            "title": merged_title,
            "cluster_label": f"통합: {titles[:50]}",
            "content": merged_content,
            "source_chat_count": sum(d.get("source_chat_count", 0) for d in drafts),
            "status": "applied",
        }
        saved = supabase_post(f"{SUPABASE_URL}/rest/v1/alf_drafts", new_draft, SUPABASE_SERVICE_KEY)
        draft_id = saved[0]["id"] if isinstance(saved, list) and saved else None

        self._respond(200, {
            "ok": True,
            "draft_id": draft_id,
            "title": merged_title,
            "content": merged_content,
        })
