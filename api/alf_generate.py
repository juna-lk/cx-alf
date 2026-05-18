from __future__ import annotations
import os
import json

from _alf_common import call_anthropic, supabase_get, supabase_post, make_handler_base

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

ALF_SYSTEM_PROMPT = """당신은 채널톡 ALF용 지식 아티클을 작성하는 전문가입니다.
아래 규칙을 반드시 따르세요:

1. 1문서 = 고객의 1가지 완결된 시나리오 (여러 상황을 섞지 말 것)
2. 제목은 고객이 실제로 검색할 표현으로 (예: "구독 중 플랜 환불하는 방법")
3. 핵심 답변을 첫 문단에 배치 (ALF가 앞부분을 더 중요하게 참조)
4. 조건 분기는 "경우에 따라 다릅니다" 대신 케이스별로 명확히 작성
5. 2,000자 이내 (초과 시 별도 아티클로 분리)
6. "담당자에게 문의" 단독 사용 금지 — 구체적인 연락 방법 또는 조건 명시
7. Markdown 형식: # 제목, ## 섹션, - 목록, > 콜아웃, | 표
8. 모호한 표현 금지: "보통", "일반적으로", "경우에 따라" 사용 금지"""


def build_generate_prompt(cluster_label: str, chats: list) -> str:
    samples = []
    for i, c in enumerate(chats[:20]):
        msgs = c.get("messages", [])
        customer_msgs = [m.get("text", "") for m in msgs if m.get("role") == "customer"]
        agent_msgs = [m.get("text", "") for m in msgs if m.get("role") == "agent"]
        if not customer_msgs:
            continue
        samples.append(
            f"[상담 {i+1}] 고객: {' / '.join(customer_msgs[:2])}"
            + (f" | 상담원: {' / '.join(agent_msgs[:2])}" if agent_msgs else "")
        )

    return f"""아래는 '{cluster_label}' 유형의 실제 상담 {len(chats)}건 요약입니다.

{chr(10).join(samples)}

이 상담 데이터를 바탕으로 ALF가 고객 문의에 정확히 답변할 수 있도록
채널톡 아티클 형식의 지식 문서 초안을 작성해주세요.

요구사항:
- 제목: '{cluster_label}' 상황에 처한 고객이 검색할 표현
- 실제 상담에서 나온 답변 패턴을 기반으로 구체적인 내용 작성
- Markdown 형식으로 작성"""


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

        # chat_ids로 상담 내용 로드
        ids_param = ",".join(f'"{cid}"' for cid in chat_ids[:20])
        url = (f"{SUPABASE_URL}/rest/v1/cx_full_messages"
               f"?select=messages&chat_id=in.({ids_param})")
        chats = supabase_get(url, SUPABASE_SERVICE_KEY)

        prompt = build_generate_prompt(cluster_label, chats)
        draft_content = call_anthropic(
            prompt, system=ALF_SYSTEM_PROMPT,
            max_tokens=2048, api_key=GROQ_API_KEY,
        )

        # 제목 추출 (첫 번째 # 줄)
        title = cluster_label
        for line in draft_content.split("\n"):
            if line.startswith("# "):
                title = line[2:].strip()
                break

        # alf_drafts에 저장
        draft = {
            "title": title,
            "cluster_label": cluster_label,
            "content": draft_content,
            "source_chat_count": len(chats),
        }
        saved = supabase_post(f"{SUPABASE_URL}/rest/v1/alf_drafts", draft, SUPABASE_SERVICE_KEY)
        draft_id = saved[0]["id"] if isinstance(saved, list) and saved else None

        self._respond(200, {"ok": True, "draft_id": draft_id, "title": title, "content": draft_content})
