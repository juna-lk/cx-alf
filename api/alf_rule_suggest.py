"""적용된 아티클/FAQ 가이드를 분석해서 추천 규칙(rules)을 생성한다.

채널톡 규칙: 채널당 최대 30개, 규칙당 2,000자 이하
규칙 유형:
- disambiguation (구분): 비슷한 지식 혼동 방지
- tone (톤): 일관된 어투/표현
- terminology (용어사전): 헷갈리기 쉬운 용어 정의
- priority (우선순위): 어떤 지식을 우선 참조할지
"""
from __future__ import annotations
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json

from _alf_common import call_anthropic, supabase_get, supabase_post, make_handler_base

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

RULE_SYSTEM = """당신은 채널톡 ALF 규칙(Rule) 작성 전문가입니다.
채널톡 공식 가이드(ALF 문제 해결, 규칙 작성법)를 기반으로 규칙을 추천합니다.

채널톡 규칙 기본 정보:
- ALF가 비즈니스 상황에 맞게 답변하도록 가이드하는 지시문
- 채널당 최대 30개, 규칙당 2,000자 이하
- 작동중/종료됨 상태로 관리, 필터링 조건 설정 가능

【채널톡 공식: 규칙으로 해결하는 5가지 상황】
1. ALF가 원하는 대로 동작하지 않을 때 → 행동 제어 규칙
2. ALF가 거짓 정보를 생성할 때 → 제한 규칙 ("할 수 없는 일" 명시)
3. ALF가 유사한 지식을 혼동할 때 → 우선순위/구분 규칙
4. 과도한 해결 의지로 오답변 → 약속/확답 표현 제한 규칙
5. 상담 연결이 제대로 작동하지 않을 때 → 멘트 제거 단순화 규칙

【추천할 4가지 규칙 유형】

1) disambiguation (구분) — 비슷한 지식 혼동 방지
   ★ 채널톡 공식 예시: "A 기능 문의는 B, C, D 기능에 대해 안내하지 않고 A에 대해서만 안내해주세요"
   ★ 채널톡 공식 예시: "특정 키워드를 언급하면 F 기능에 대해서만 안내해주세요"

2) tone (톤) — 일관된 어투/표현
   ★ "고객님 호칭 사용, '~드립니다/안내드려요' 어투, 친근하지만 거리감 있는 존대를 유지하세요"
   ★ "업무 처리를 약속하는 표현 대신, 고객이 스스로 진행할 절차와 정보를 안내해주세요"
   ★ "필요 이상의 추가 제안 없이, 핵심 정보만 명확하게 안내해주세요"

3) terminology (용어사전) — 헷갈리기 쉬운 용어 정의
   ★ 채널톡 공식 예시: "프리오더: 출시 전 상품을 예약 주문하는 방식"
   ★ "'플랜'과 '구독'은 동일한 의미로 사용됩니다"

4) priority (우선순위) — 어떤 지식을 우선 참조할지
   ★ "신규 가입 고객(가입 7일 이내) 문의는 '입문 가이드' 폴더의 아티클을 먼저 참조해주세요"

【제한 규칙(거짓 정보 방지) — 5번째 유형으로 disambiguation에 포함】
   ★ "확실하지 않은 환불 처리 기간, 결제 금액, 배송일정은 임의로 답변하지 마세요"
   ★ "전화 연결을 권유하지 말고, 현재 대화 내에서 해결 가능한 방식으로 안내해주세요"

【피해야 할 표현】 (채널톡 공식 — 역효과 규칙):
   ❌ "항상 해결하려는 태도로 답변해" — 거짓 정보 생성 위험
   ❌ "확실하지 않아도 최대한 답변해줘" — 부정확한 답변 유도
   ✅ "불확실하면 상담 열기가 필요한지 먼저 물어봐"

【작성 원칙】
- 한 규칙 = 한 주제 (여러 주제 섞지 말 것)
- 명확한 지시문 형식 ("~하세요", "~하지 마세요")
- 적용된 아티클/FAQ의 정확한 이름을 구체적으로 언급
- 2,000자 이하
- 추천 시 채널톡 공식 예시 패턴을 참고하여 작성

**언어 규칙: 모든 출력은 반드시 한국어로만 작성하세요. 일본어(히라가나/가타카나/한자), 중국어 한자, 영어 단어는 절대 사용하지 마세요. 예) 編集→편집, プラン→플랜, サイト→사이트."""


def build_suggest_prompt(drafts: list) -> str:
    docs_text = "\n".join(
        f"[{d['format'].upper()}] {d['title']}\n  요약: {d['content'][:150]}..."
        for d in drafts
    )
    return f"""아래는 ALF에 적용된 지식 문서 {len(drafts)}개입니다.

{docs_text}

이 문서들을 ALF가 더 정확하게 활용하도록 도울 규칙(rules)을 3~6개 추천해주세요.
각 규칙은 한 가지 주제만 다뤄야 하며, 위 문서들 중 어떤 것에 관련된 규칙인지 명시해주세요.

반드시 아래 JSON 형식으로만 답변하세요:
{{
  "rules": [
    {{
      "title": "규칙 제목 (50자 이내)",
      "rule_type": "disambiguation" | "tone" | "terminology" | "priority",
      "content": "규칙 본문 (지시문 형식, 2000자 이내)",
      "related_titles": ["관련 문서 제목1", "관련 문서 제목2"],
      "reason": "이 규칙이 필요한 이유 (1~2문장)"
    }}
  ]
}}"""


_Base = make_handler_base()


class handler(_Base):
    def do_GET(self):
        # ALF 적용된 가이드만 조회
        url = (f"{SUPABASE_URL}/rest/v1/alf_drafts"
               f"?select=id,title,format,content"
               f"&status=eq.applied&order=created_at.desc&limit=20")
        drafts = supabase_get(url, SUPABASE_SERVICE_KEY)

        if len(drafts) < 2:
            self._respond(200, {"ok": True, "rules": [],
                                "message": "규칙 추천을 위해 ALF 적용 가이드가 최소 2개 필요해요."})
            return

        prompt = build_suggest_prompt(drafts)
        raw = call_anthropic(prompt, system=RULE_SYSTEM, max_tokens=2048, api_key=GROQ_API_KEY)

        try:
            text = raw.strip()
            if "```" in text:
                text = text.split("```")[1].lstrip("json").strip()
            result = json.loads(text)
            rules = result.get("rules", [])
            # 관련 문서 제목 → ID 매핑
            title_to_id = {d["title"]: d["id"] for d in drafts}
            for r in rules:
                r["related_draft_ids"] = [
                    title_to_id[t] for t in r.get("related_titles", [])
                    if t in title_to_id
                ]
        except Exception as e:
            self._respond(500, {"ok": False, "error": f"규칙 추천 파싱 실패: {e}", "raw": raw[:500]})
            return

        self._respond(200, {"ok": True, "rules": rules})

    def do_POST(self):
        """추천된 규칙을 alf_rules 테이블에 저장"""
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}
        rules = body.get("rules", [])
        if not rules:
            self._respond(400, {"ok": False, "error": "rules 필요"})
            return

        rows = [{
            "title": r.get("title", "")[:200],
            "content": r.get("content", ""),
            "rule_type": r.get("rule_type", "tone"),
            "related_draft_ids": r.get("related_draft_ids", []),
        } for r in rules]
        saved = supabase_post(
            f"{SUPABASE_URL}/rest/v1/alf_rules", rows, SUPABASE_SERVICE_KEY,
        )
        self._respond(200, {"ok": True, "saved": len(saved) if isinstance(saved, list) else 0})
