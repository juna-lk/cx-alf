from __future__ import annotations
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json

from _alf_common import call_anthropic, supabase_get, make_handler_base

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

MERGE_SYSTEM = """당신은 ALF 지식 문서 구조 최적화 전문가입니다.
원칙: "1문서 = 고객의 1가지 완결된 시나리오"
병합 유형:
- 중복 주제: 같은 주제를 다른 각도로 다룸 → 하나로 통합
- 계층 구조: 하위 주제들을 상위 주제로 묶을 수 있음
- 보완 관계: 함께 읽어야 완전한 답변이 됨
- 중복 내용: 내용이 실질적으로 겹침

**언어 규칙: 모든 출력은 반드시 한국어로만 작성하세요. 일본어(히라가나/가타카나/한자), 중국어 한자, 영어 단어는 절대 사용하지 마세요. 예) 編集→편집, プラン→플랜, サイト→사이트."""


_Base = make_handler_base()


class handler(_Base):
    def do_GET(self):
        # ALF 적용된 가이드만 대상으로 병합 분석
        url = (f"{SUPABASE_URL}/rest/v1/alf_drafts"
               f"?select=id,title,cluster_label,content"
               f"&status=eq.applied&order=created_at.desc&limit=30")
        drafts = supabase_get(url, SUPABASE_SERVICE_KEY)

        if len(drafts) < 2:
            self._respond(200, {"ok": True, "suggestions": []})
            return

        doc_summaries = "\n".join(
            f"[{d['id'][:8]}] {d['title']}\n  내용 요약: {d['content'][:200]}..."
            for d in drafts
        )

        prompt = f"""아래 ALF 지식 문서 {len(drafts)}개를 분석해 병합이 권장되는 그룹을 찾아주세요.

{doc_summaries}

반드시 아래 JSON 형식으로만 답변하세요 (병합 권장 없으면 빈 배열):
{{
  "suggestions": [
    {{
      "type": "중복 주제" | "계층 구조" | "보완 관계" | "중복 내용",
      "draft_ids": ["id1", "id2"],
      "draft_titles": ["제목1", "제목2"],
      "reason": "병합을 권장하는 이유 (고객/ALF 관점 2-3문장)"
    }}
  ]
}}"""

        raw = call_anthropic(prompt, system=MERGE_SYSTEM, max_tokens=1024, api_key=GROQ_API_KEY)

        try:
            text = raw.strip()
            if "```" in text:
                text = text.split("```")[1].lstrip("json").strip()
            result = json.loads(text)
            # draft_ids를 전체 ID로 복원 (앞 8자로 매핑)
            id_map = {d["id"][:8]: d["id"] for d in drafts}
            for sg in result.get("suggestions", []):
                sg["draft_ids"] = [id_map.get(did, did) for did in sg["draft_ids"]]
        except Exception:
            result = {"suggestions": []}

        self._respond(200, {"ok": True, **result})
