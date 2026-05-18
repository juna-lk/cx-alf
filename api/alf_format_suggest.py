from __future__ import annotations
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json

from _alf_common import call_anthropic, supabase_get, make_handler_base, extract_json

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

FORMAT_SYSTEM = """당신은 채널톡 ALF 지식 콘텐츠 전략가입니다.
상담 데이터를 보고 FAQ가 효과적인지, 아티클이 효과적인지 판단합니다.

판단 기준:
- FAQ: 단순/반복 질문, 답변 500자 이내, 같은 질문이 표현만 다르게 반복됨
- 아티클: 다단계 절차, 조건 분기 많음, 답변에 표/링크/이미지 필요, 500자 초과

**언어 규칙: 모든 출력은 반드시 한국어로만 작성하세요. 일본어(히라가나/가타카나/한자), 중국어 한자, 영어 단어는 절대 사용하지 마세요. 예) 編集→편집, プラン→플랜, サイト→사이트."""


def build_format_prompt(cluster_label: str, chats: list) -> str:
    samples = []
    for i, c in enumerate(chats[:15]):
        msgs = c.get("messages", [])
        customer_msgs = [m.get("text", "") for m in msgs if m.get("role") == "customer"][:2]
        agent_msgs = [m.get("text", "") for m in msgs if m.get("role") == "agent"][:2]
        if customer_msgs:
            samples.append(
                f"[상담 {i+1}] 고객: {' / '.join(customer_msgs)}"
                + (f" | 상담원: {' / '.join(agent_msgs)}" if agent_msgs else "")
            )

    return f"""'{cluster_label}' 유형의 상담 {len(chats)}건을 보고 FAQ와 아티클 중 어떤 포맷이 더 효과적일지 판단해주세요.

{chr(10).join(samples)}

반드시 아래 JSON 형식으로만 답변하세요:
{{
  "recommendation": "faq" 또는 "article",
  "reason": "추천 이유 (1~2문장, 한국어)"
}}"""


_Base = make_handler_base()


class handler(_Base):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}
        cluster_label = body.get("cluster_label", "")
        chat_ids = body.get("chat_ids", [])

        if not cluster_label or not chat_ids:
            self._respond(400, {"ok": False, "error": "cluster_label, chat_ids 필요"})
            return

        ids_param = ",".join(f'"{cid}"' for cid in chat_ids[:15])
        url = (f"{SUPABASE_URL}/rest/v1/cx_full_messages"
               f"?select=messages&chat_id=in.({ids_param})")
        chats = supabase_get(url, SUPABASE_SERVICE_KEY)

        prompt = build_format_prompt(cluster_label, chats)
        raw = call_anthropic(prompt, system=FORMAT_SYSTEM, max_tokens=512, api_key=GROQ_API_KEY)

        try:
            result = extract_json(raw)
            recommendation = result.get("recommendation", "article")
            reason = result.get("reason", "")
        except Exception:
            recommendation = "article"
            reason = "분석 실패 - 기본값(article)"

        self._respond(200, {
            "ok": True,
            "recommendation": recommendation,
            "reason": reason,
        })
