from __future__ import annotations
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json

from _alf_common import call_anthropic, supabase_get, supabase_post, make_handler_base

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

ALF_SYSTEM_PROMPT = """당신은 채널톡 ALF(AI 에이전트)용 지식 아티클을 작성하는 전문가입니다.
ALF는 RAG 기술로 등록된 지식을 검색해 답변하므로, 구조화·구체화될수록 답변 품질이 올라갑니다.

**가장 중요한 원칙:**
실제 상담원(사람)이 작성한 답변을 그대로 정리해서 작성하세요.
ALF/봇 답변은 무시하고, 매니저의 실제 답변에서 공통 패턴/단계/문구를 추출하세요.
없는 내용을 새로 만들어내지 마세요.

채널톡 CS팀 공식 가이드 (실전 베스트 프랙티스):
- 짧은 문답·답변이 고정적인 내용은 FAQ(500자), 복잡한 프로세스·정책은 아티클로
- 제목에 핵심 키워드 포함 + 본문에서 소제목으로 정보 구조화 → ALF의 맥락 파악에 큰 도움
- 정확한 용어 사용 + 관련 링크 본문 포함 → ALF가 고객에게 링크까지 함께 안내 가능
- 중복 내용은 ALF가 혼란을 일으킬 수 있으므로 한 문서에서 명확히 정리

아티클 작성 규칙:
1. 1문서 = 고객의 1가지 완결된 시나리오 (여러 상황 섞지 말 것)
2. 제목은 고객이 실제로 검색할 표현 + 핵심 키워드 포함 (예: "구독 중 플랜 환불하는 방법")
3. 첫 문단 = 결론. ALF는 앞부분을 우선 참조함
4. **소제목(##) 적극 활용** — ALF가 맥락 파악하기 쉬워짐
5. 조건 분기는 케이스별로 명확히 명시 ("경우에 따라", "보통", "일반적으로" 금지)
6. 2,000자 이내, 초과 시 별도 아티클로 분리
7. "담당자 문의" 단독 사용 금지 — 구체적 조건/방법 명시
8. 관련 페이지·외부 자료는 링크로 본문에 포함
9. Markdown 사용: # 제목, ## 섹션 헤딩, - 목록, > 콜아웃, | 표
10. **상담원 실제 답변의 어투/문구를 그대로 따를 것** (창작 금지)
11. ALF는 고객/상담의 태그·설명은 참조하지 않으므로, 필요 정보는 본문에 모두 포함할 것

**언어 규칙: 모든 출력은 반드시 한국어로만 작성하세요. 일본어(히라가나/가타카나/한자), 중국어 한자, 영어 단어는 절대 사용하지 마세요. 예) 編集→편집, プラン→플랜, サイト→사이트."""


def build_generate_prompt(cluster_label: str, chats: list) -> str:
    samples = []
    for i, c in enumerate(chats[:15]):
        msgs = c.get("messages", [])
        customer_msgs = [m.get("text", "")[:200] for m in msgs if m.get("role") == "customer"][:2]
        # 상담원 답변만 (ALF/봇 제외, 답변 패턴 추출용)
        agent_msgs = [m.get("text", "") for m in msgs if m.get("role") == "agent"]
        if not customer_msgs or not agent_msgs:
            continue
        agent_full = "\n".join(f"  → {a[:500]}" for a in agent_msgs[:4])
        samples.append(
            f"[상담 {i+1}]\n"
            f"고객: {' / '.join(customer_msgs)}\n"
            f"상담원이 답한 내용:\n{agent_full}"
        )

    if not samples:
        return f"'{cluster_label}' 관련 상담원 답변 데이터가 부족합니다."

    return f"""아래는 '{cluster_label}' 상황의 실제 채널톡 상담 {len(samples)}건입니다.
상담원(사람)이 실제로 작성한 답변에 주목해주세요.

{chr(10).join(samples)}

위 데이터를 바탕으로 채널톡 아티클 형식의 지식 문서를 작성해주세요.
- 제목: '{cluster_label}' 상황의 고객이 검색할 표현
- **본문: 위 상담원들이 실제로 답한 내용에서 공통 패턴/단계/문구를 뽑아 정리**
  (창작하지 말고 실제 답변 어투/표현 그대로 사용)
- Markdown 형식"""


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
