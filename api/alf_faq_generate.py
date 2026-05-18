from __future__ import annotations
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json

from _alf_common import call_anthropic, supabase_get, supabase_post, make_handler_base

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

FAQ_SYSTEM_PROMPT = """당신은 채널톡 ALF용 FAQ 콘텐츠 작성 전문가입니다.
실제 고객이 사용한 자연어 표현을 기반으로 FAQ를 작성합니다.

작성 규칙:
1. 대표 질문(question): 고객이 가장 자주 묻는 표현 1개 (100자 이내)
2. 변형 질문(variations): 같은 의도지만 표현이 다른 질문 5~10개 (각 100자 이내)
   - 반드시 실제 상담에서 나온 고객 표현을 기반으로 작성
   - 줄임말/오타/구어체 포함 (예: "환불 어케해요?", "무료로 바꿀 수 있나요?")
3. 답변(answer): 핵심만 간결하게 (500자 이내, Markdown 가능)
   - 결론부터 먼저
   - 조건이 있다면 명확하게 분기
   - "담당자 문의" 단독 사용 금지"""


def build_faq_prompt(cluster_label: str, chats: list) -> str:
    samples = []
    for i, c in enumerate(chats[:20]):
        msgs = c.get("messages", [])
        customer_msgs = [m.get("text", "") for m in msgs if m.get("role") == "customer"][:3]
        agent_msgs = [m.get("text", "") for m in msgs if m.get("role") == "agent"][:2]
        if customer_msgs:
            samples.append(
                f"[상담 {i+1}]\n고객: {' / '.join(customer_msgs)}"
                + (f"\n상담원: {' / '.join(agent_msgs)}" if agent_msgs else "")
            )

    return f"""아래는 '{cluster_label}' 상황의 실제 고객 상담 {len(chats)}건입니다.

{chr(10).join(samples)}

위 상담 데이터를 분석해서 FAQ 1개를 작성해주세요.
**변형 질문은 위 상담에서 고객이 실제로 사용한 표현을 기반으로 5~10개 뽑아주세요.**

반드시 아래 JSON 형식으로만 답변하세요:
{{
  "question": "대표 질문 (100자 이내)",
  "variations": ["변형 질문 1", "변형 질문 2", "..."],
  "answer": "답변 본문 (500자 이내, Markdown 가능)"
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

        ids_param = ",".join(f'"{cid}"' for cid in chat_ids[:20])
        url = (f"{SUPABASE_URL}/rest/v1/cx_full_messages"
               f"?select=messages&chat_id=in.({ids_param})")
        chats = supabase_get(url, SUPABASE_SERVICE_KEY)

        prompt = build_faq_prompt(cluster_label, chats)
        raw = call_anthropic(
            prompt, system=FAQ_SYSTEM_PROMPT,
            max_tokens=1500, api_key=GROQ_API_KEY,
        )

        try:
            text = raw.strip()
            if "```" in text:
                text = text.split("```")[1].lstrip("json").strip()
            faq = json.loads(text)
            question = faq.get("question", cluster_label)
            variations = faq.get("variations", [])
            answer = faq.get("answer", "")
        except Exception as e:
            self._respond(500, {"ok": False, "error": f"FAQ 파싱 실패: {e}", "raw": raw[:500]})
            return

        # alf_drafts에 저장 (format='faq')
        draft = {
            "title": question,
            "cluster_label": cluster_label,
            "content": answer,
            "format": "faq",
            "variations": variations,
            "source_chat_count": len(chats),
        }
        saved = supabase_post(f"{SUPABASE_URL}/rest/v1/alf_drafts", draft, SUPABASE_SERVICE_KEY)
        draft_id = saved[0]["id"] if isinstance(saved, list) and saved else None

        self._respond(200, {
            "ok": True,
            "draft_id": draft_id,
            "question": question,
            "variations": variations,
            "answer": answer,
        })
