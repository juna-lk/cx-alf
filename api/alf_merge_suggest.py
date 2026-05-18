from __future__ import annotations
import os
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
- 중복 내용: 내용이 실질적으로 겹침"""


_Base = make_handler_base()


class handler(_Base):
    def do_GET(self):
        # 전체 초안 목록 + 내용 조회
        url = f"{SUPABASE_URL}/rest/v1/alf_drafts?select=id,title,cluster_label,content&order=created_at.desc&limit=30"
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
