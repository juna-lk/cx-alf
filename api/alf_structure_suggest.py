from __future__ import annotations
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json

from _alf_common import call_anthropic, supabase_get, make_handler_base, extract_json

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")


def build_suggest_prompt(cluster_label: str, tag: str, existing_drafts: list) -> str:
    if not existing_drafts:
        existing_section = "기존 문서 없음"
    else:
        lines = [f"- [{d['id'][:8]}] {d['title']} ({d.get('cluster_label', '')})"
                 for d in existing_drafts]
        existing_section = "\n".join(lines)

    return f"""새로 작성하려는 ALF 지식 문서:
- 주제 태그: {tag}
- 유형: {cluster_label}

현재 저장된 ALF 문서 목록:
{existing_section}

원칙: "1문서 = 고객의 1가지 완결된 시나리오"
- 같은 시나리오의 연속 내용이면 기존 문서에 추가
- 고객이 처한 상황 자체가 다르면 새 문서 생성

위 새 유형을 기존 문서 중 어디에 추가하는 게 좋을지, 아니면 새 문서로 만들어야 할지 판단하세요.

반드시 아래 JSON 형식으로만 답변하세요:
{{
  "recommendation": "add" 또는 "new",
  "target_id": "기존 문서 ID (recommendation=add일 때만, 없으면 null)",
  "target_title": "기존 문서 제목 (recommendation=add일 때만, 없으면 null)",
  "reason": "추천 이유 2-3문장 (고객 관점에서 설명)"
}}"""


_Base = make_handler_base()


class handler(_Base):
    def do_POST(self):
        if not self._check_auth():
            return
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}
        tag = body.get("tag", "")
        cluster_label = body.get("cluster_label", "")

        # 기존 alf_drafts 목록 조회
        url = f"{SUPABASE_URL}/rest/v1/alf_drafts?select=id,title,cluster_label&order=created_at.desc&limit=20"
        existing = supabase_get(url, SUPABASE_SERVICE_KEY)

        prompt = build_suggest_prompt(cluster_label, tag, existing)
        raw = call_anthropic(prompt, max_tokens=512, api_key=GROQ_API_KEY)

        try:
            result = extract_json(raw)
        except Exception:
            result = {"recommendation": "new", "target_id": None, "target_title": None,
                      "reason": "분석 실패 — 새 문서 생성을 권장합니다."}

        self._respond(200, {"ok": True, **result})
