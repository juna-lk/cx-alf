from __future__ import annotations
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json

from _alf_common import call_anthropic, supabase_get, supabase_post, make_handler_base

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

FAQ_SYSTEM_PROMPT = """당신은 채널톡 ALF(AI 에이전트)용 FAQ 콘텐츠 작성 전문가입니다.
채널톡 공식 FAQ 규격:
- 질문 100자 이내, 추가 질문 100자 이내 (최대 10개), 답변 500자 이내
ALF는 등록된 FAQ 질문과 관련도 높은 문의가 들어올 때 답변을 참조하므로, 변형 질문이 다양할수록 매칭 성능이 좋아집니다.

**가장 중요한 원칙:**
실제 상담원(사람)이 답한 내용을 그대로 정리해서 작성하세요.
ALF/봇 답변은 무시하고, 사람 상담원 답변만 참고하세요.
답변을 새로 창작하지 말고, 실제 답변에서 공통 패턴/문구를 추출하세요.

작성 규칙:
1. 대표 질문(question): 정확한 키워드를 포함한 자연스러운 질문 (100자 이내)
2. 변형 질문(variations): 실제 상담에서 고객이 사용한 표현 5~10개
   - 줄임말/구어체/오타 그대로 포함 (예: "환불 어케해요?")
   - 키워드 변주 ("취소", "해지", "환불" 등 동의어 활용)
3. 답변(answer): 500자 이내, Markdown 가능
   - **상담원이 실제로 답한 표현, 어투, 단계를 그대로 따를 것**
   - 여러 상담 답변의 공통 핵심을 묶어서 정리
   - 결론부터 먼저, 조건 분기 명확히
   - "담당자 문의" 단독 답변 금지 — 구체적 조건/방법 명시
   - "보통/일반적으로/경우에 따라" 같은 모호한 표현 금지
4. ALF는 고객/상담의 태그·설명은 참조하지 않으므로, 답변에 필요한 모든 정보 포함"""


def build_faq_prompt(cluster_label: str, chats: list) -> str:
    samples = []
    for i, c in enumerate(chats[:15]):
        msgs = c.get("messages", [])
        # 고객 메시지 (질문 패턴용)
        customer_msgs = [m.get("text", "")[:200] for m in msgs if m.get("role") == "customer"][:2]
        # 상담원(매니저) 메시지만 - ALF/봇 제외, 답변 패턴 추출용
        agent_msgs = [m.get("text", "") for m in msgs if m.get("role") == "agent"]
        if not customer_msgs or not agent_msgs:
            continue
        # 상담원 답변은 더 많이, 더 길게 포함 (답변 핵심 자료)
        agent_full = "\n".join(f"  → {a[:400]}" for a in agent_msgs[:4])
        samples.append(
            f"[상담 {i+1}]\n"
            f"고객: {' / '.join(customer_msgs)}\n"
            f"상담원이 답한 내용:\n{agent_full}"
        )

    if not samples:
        return f"'{cluster_label}' 관련 상담원 답변 데이터가 부족해서 FAQ 작성이 어렵습니다."

    return f"""아래는 '{cluster_label}' 상황에 대한 실제 채널톡 상담 {len(samples)}건입니다.
상담원(사람)이 실제로 작성한 답변에 주목해주세요.

{chr(10).join(samples)}

위 데이터를 바탕으로 FAQ 1개를 작성해주세요.
- **변형 질문**: 위 고객들이 실제로 쓴 표현 그대로
- **답변**: 위 상담원들이 실제로 답한 내용에서 공통 패턴을 뽑아 정리
  (새 답변을 창작하지 말고, 실제 답변 어투/문구를 따를 것)

반드시 아래 JSON 형식으로만 답변하세요:
{{
  "question": "대표 질문 (100자 이내)",
  "variations": ["변형 질문 1", "변형 질문 2", "..."],
  "answer": "답변 본문 (500자 이내, 상담원 실제 답변 기반, Markdown 가능)"
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
