from __future__ import annotations
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json
import urllib.parse

from _alf_common import call_anthropic, supabase_get, supabase_post, make_handler_base, extract_json, strip_article_boilerplate, verify_draft, PRIMARY_MANAGERS

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

FAQ_SYSTEM_PROMPT = """당신은 채널톡 ALF용 FAQ 콘텐츠 작성 전문가입니다.

【핵심 원칙 — 매니저 톤 그대로 살리기】
이 FAQ는 일반론을 만들어내는 게 아닙니다. 실제 매니저들이 고객 상담에서 사용한 표현·문구·말투·맥락을 **그대로 살리는 것**이 목표입니다.
- 매니저들이 자주 쓰는 표현·구절은 **그대로 인용**하세요
- 매니저들이 안내하지 않은 정책·절차는 **절대 생성하지 마세요**
- 한 매니저만 쓴 표현보다 여러 매니저가 공통으로 쓴 표현을 우선
- 답변 어순·연결어·마무리 표현도 매니저들의 실제 패턴을 따라가세요

【채널톡 공식 FAQ 규격】
- 질문 100자 이내 / 추가 질문 100자 이내 (최대 10개) / 답변 500자 이내
- ALF는 등록된 질문과 관련도 높은 문의에 답변을 참조 → 변형 질문이 다양할수록 매칭 ↑

【ALF가 잘 찾기 위한 조건】 (채널톡 CS팀 직접 답변)
- 질문에 **핵심 키워드(상품명·기능명) 반드시 포함** (ALF는 제목·질문 우선 검색)
- 답변은 완성된 문장으로 (끊기지 않은 형태일 때 ALF가 의미 정확히 해석)
- 답변에서도 불릿(`-`)/번호(`1.`) 목록 활용 가능
- 모호한 표현 지양 → ALF가 엉뚱한 문서 참조 가능
- 중복 내용 정리

【제거해야 하는 부분만 (그 외는 매니저 표현 보존)】
- ❌ 매니저 자기소개 ("○○ 코치 ○○입니다") → 제거만, 어투는 유지
- ❌ 직접 질문 ("혹시 도움이 필요하실까요?") → 삭제
- ❌ 마무리 인사 ("감사합니다", "마무리할게요") → 삭제
- ❌ 1:1 호명 ("○○님은") → 일반 표현으로 (단어만 교체, 문장 구조는 유지)
- ❌ 이모지·감탄사
- ❌ "담당자 문의" 단독 사용 — 조건·방법 같이 명시

→ 위 항목 외에는 매니저들이 실제 쓴 어휘·말투·문장 구조·연결어를 **그대로 살려서 답변을 구성**하세요.
일반화·추상화는 위 항목에 해당하는 부분만 하고, 다른 부분은 인용에 가깝게 작성하세요.

【작성 규칙】
1. 대표 질문(question): 정확한 키워드 포함, 100자 이내, ALF 검색용 핵심 키워드 필수
2. 변형 질문(variations): 실제 고객이 쓴 표현 5~10개 (줄임말·구어체·오타 그대로)
3. 답변(answer): 500자 이내. **매니저 답변에서 표현·구절을 가져와 구성**. 결론부터, 조건 분기 명확히.
   - **Markdown 금지**: 코드 펜스 / 백틱(`) / 줄 시작 들여쓰기 4칸 / ## 헤딩 — 1./2. 번호 목록만 OK
   - 메뉴 경로: 사이트 관리 > 사이트 디자인 (한 줄, 백틱 없이)
4. 매니저가 답변에서 안내하지 않은 정보는 답변에 포함하지 마세요 (없으면 비워두는 게 추측보다 낫습니다)

【언어 규칙】
모든 출력은 한국어로만. 일본어(히라가나/가타카나/한자)·중국어 한자·영어 단어 금지.
예) 編集→편집, プラン→플랜, サイト→사이트."""


def build_faq_prompt(cluster_label: str, chats: list, single_chat: bool = False) -> str | None:
    samples = []
    for i, c in enumerate(chats[:50]):
        msgs = c.get("messages", [])
        # 고객 메시지 (질문 패턴용)
        customer_msgs = [m.get("text", "")[:200] for m in msgs if m.get("role") == "customer"][:2]
        # 화이트리스트 매니저 답변만 사용 (전준영·조승현·김푸름)
        # 부분 매칭: 채널톡 매니저 이름이 "조승현(Logan)" 식으로 영문 닉네임 포함 가능
        agent_msgs = [
            m.get("text", "") for m in msgs
            if m.get("role") == "agent"
            and any(p in (m.get("manager") or "") for p in PRIMARY_MANAGERS)
        ]
        # 단일 chat 모드는 화이트리스트가 비면 그 chat의 모든 매니저 답변으로 fallback
        if single_chat and not agent_msgs:
            agent_msgs = [m.get("text", "") for m in msgs if m.get("role") == "agent"]
        if not customer_msgs or not agent_msgs:
            continue
        # 상담원 답변은 충분히 길게 포함 (FAQ 답변 어휘·말투의 원천)
        agent_full = "\n".join(f"  → {a[:600]}" for a in agent_msgs[:6])
        samples.append(
            f"[상담 {i+1}]\n"
            f"고객: {' / '.join(customer_msgs)}\n"
            f"매니저가 답한 내용:\n{agent_full}"
        )

    if not samples:
        return None

    return f"""아래는 '{cluster_label}' 상황의 실제 채널톡 상담 {len(samples)}건입니다.

{chr(10).join(samples)}

위 데이터에서 FAQ 1개를 작성해주세요. **목표는 매니저들이 실제 쓴 어휘·말투·문장 구조를 그대로 살린 답변**입니다.

【작성 절차】
1. 매니저들의 답변에서 **자주 등장하는 표현·구절·연결어·마무리 패턴**을 먼저 식별
2. 그 표현·구절을 **그대로 가져와** 500자 이내 답변으로 구성
3. 다음만 정리:
   · 매니저 자기소개("○○ 코치 ○○입니다") → 삭제
   · 1:1 호명("○○님은") → 일반 표현으로 단어만 교체 (문장 구조는 유지)
   · 마무리 인사("감사합니다") → 삭제
   · 직접 질문("필요하실까요?") → 삭제
4. **매니저가 답변에 포함하지 않은 정책·절차·정보는 추가하지 마세요** (없으면 비워두기)

【변형 질문】
- 위 고객들이 실제로 쓴 표현을 **그대로 인용** (줄임말·구어체·오타 포함)
- 5~10개, 키워드 변주

반드시 아래 JSON 형식으로만 답변하세요:
{{
  "question": "대표 질문 (100자 이내, 핵심 키워드 포함)",
  "variations": ["변형 질문 1", "변형 질문 2", "..."],
  "answer": "답변 본문 (500자 이내, 매니저들 실제 표현 살린 톤, plain text만 — 코드펜스/백틱/들여쓰기 금지)"
}}"""


_Base = make_handler_base()


class handler(_Base):
    def do_POST(self):
        if not self._check_auth():
            return
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}
        cluster_label = (body.get("cluster_label") or "").strip()
        chat_ids = body.get("chat_ids", [])
        single_chat = bool(body.get("single_chat", False))
        similar_search = bool(body.get("similar_search", False))

        if not chat_ids:
            self._respond(400, {"ok": False, "error": "chat_ids 필요"})
            return
        if not cluster_label and not single_chat:
            self._respond(400, {"ok": False, "error": "cluster_label 필요"})
            return

        ids_param = ",".join(f'"{cid}"' for cid in chat_ids[:20])
        url = (f"{SUPABASE_URL}/rest/v1/cx_full_messages"
               f"?select=chat_id,messages,tags&chat_id=in.({ids_param})")
        chats = supabase_get(url, SUPABASE_SERVICE_KEY)

        # DB에 없는 chat은 채널톡 API 실시간 fetch
        existing_ids = {c.get("chat_id") for c in chats}
        missing_ids = [cid for cid in chat_ids if cid not in existing_ids]
        if missing_ids:
            try:
                from alf_collect import fetch_messages_for_chat, parse_messages, fetch_all_managers
                mgr_map = fetch_all_managers()
                for cid in missing_ids[:20]:
                    try:
                        raw_msgs = fetch_messages_for_chat(cid)
                        chats.append({"chat_id": cid, "messages": parse_messages(raw_msgs, mgr_map)})
                    except Exception as e:
                        print(f"[warn] 채널톡 fetch 실패 {cid}: {e}")
            except Exception as e:
                print(f"[warn] alf_collect import 실패: {e}")

        # 단일 상담 모드: cluster_label 자동 추출
        if single_chat and not cluster_label and chats:
            msgs = chats[0].get("messages", [])
            customer_msgs = [m.get("text", "")[:200] for m in msgs if m.get("role") == "customer"][:3]
            if customer_msgs:
                label_prompt = (
                    "다음 고객 문의의 핵심 시나리오를 한 줄로 요약 (예: '환불 처리 기간'):\n\n"
                    + " / ".join(customer_msgs)
                    + "\n\n한 줄 시나리오만 출력:"
                )
                try:
                    cluster_label = call_anthropic(
                        label_prompt, max_tokens=80, api_key=OPENAI_API_KEY,
                    ).strip().strip('"').strip("'")
                except Exception:
                    cluster_label = "단일 상담 기반 FAQ"
            else:
                cluster_label = "단일 상담 기반 FAQ"

        # 유사 케이스 종합 분석
        similar_count = 0
        if single_chat and similar_search and chats:
            origin_chat = chats[0]
            origin_tags = origin_chat.get("tags") or []
            origin_id = origin_chat.get("chat_id", "")
            if origin_tags:
                safe_tag = origin_tags[0].replace('\\', '\\\\').replace('"', '\\"')
                encoded_tag = urllib.parse.quote(f'{{"{safe_tag}"}}', safe='')
                sim_url = (f"{SUPABASE_URL}/rest/v1/cx_full_messages"
                           f"?select=chat_id,messages,tags"
                           f"&tags=cs.{encoded_tag}"
                           f"&chat_id=neq.{origin_id}"
                           f"&order=date.desc&limit=200")
                try:
                    candidates = supabase_get(sim_url, SUPABASE_SERVICE_KEY)
                except Exception as e:
                    print(f"[warn] FAQ 유사 케이스 fetch 실패: {e}")
                    candidates = []
                if candidates:
                    try:
                        from alf_search import filter_by_semantic
                        similar_chats = filter_by_semantic(candidates, cluster_label)[:30]
                        chats.extend(similar_chats)
                        similar_count = len(similar_chats)
                    except Exception as e:
                        print(f"[warn] FAQ 유사 케이스 의미 검색 실패: {e}")

        prompt = build_faq_prompt(cluster_label, chats, single_chat=single_chat)
        if prompt is None:
            self._respond(400, {"ok": False,
                                "error": "이 상담에 매니저 답변이 없거나 화이트리스트(전준영·조승현·김푸름) 매니저가 답변하지 않았어요. 다른 상담을 선택해주세요."})
            return
        raw = call_anthropic(
            prompt, system=FAQ_SYSTEM_PROMPT,
            max_tokens=1500, api_key=OPENAI_API_KEY,
        )

        try:
            import re as _re
            faq = extract_json(raw)
            question = faq.get("question", cluster_label)
            variations = faq.get("variations", [])
            answer = strip_article_boilerplate(faq.get("answer", ""))
            # FAQ는 plain text — 코드 펜스/백틱/들여쓰기 자동 제거 (채널톡 코드블록 인식 방지)
            answer = _re.sub(r"```\w*\s*\n?", "", answer)
            answer = answer.replace("```", "").replace("`", "")
            answer = _re.sub(r"^[ \t]{4,}", "", answer, flags=_re.MULTILINE)
            answer = _re.sub(r"^#{1,6}\s+", "", answer, flags=_re.MULTILINE)
            verification = verify_draft(answer, "faq", cluster_label)
            answer = verification["fixed"]
        except Exception as e:
            print(f"[alf_faq_generate] 파싱 실패: {e}; raw[:200]={raw[:200]!r}")
            self._respond(500, {"ok": False, "error": "FAQ 생성에 실패했어요. 잠시 후 다시 시도해주세요."})
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
            "warnings": verification["warnings"],
            "analyzed_chat_count": len(chats),
            "similar_count": similar_count,
        })
