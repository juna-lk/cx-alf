from __future__ import annotations
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json
import urllib.parse

from _alf_common import call_anthropic, supabase_get, supabase_post, make_handler_base, extract_json, strip_article_boilerplate, verify_draft

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

FAQ_SYSTEM_PROMPT = """당신은 채널톡 ALF(AI 에이전트)용 FAQ 콘텐츠 작성 전문가입니다.

【채널톡 공식 FAQ 규격】
- 질문 100자 이내 / 추가 질문 100자 이내 (최대 10개) / 답변 500자 이내
- ALF는 등록된 질문과 관련도 높은 문의에 답변을 참조 → 변형 질문이 다양할수록 매칭 ↑

【ALF가 잘 찾기 위한 필수 조건】 (채널톡 CS팀 직접 답변)
- 질문에 **핵심 키워드(상품명·기능명) 반드시 포함** (ALF는 제목·질문 우선 검색)
- **완성된 문장**(`-어요/-습니다`)으로 작성 → 끊기지 않은 형태일 때 ALF가 의미 정확히 해석
- 답변에서도 **불릿(`-`)/번호(`1.`) 목록** 활용 → 정보 선후 관계를 ALF가 정확히 파악
- 이미지가 필요하면 **이미지 위아래에 설명 텍스트 추가** (이미지만으로는 ALF가 못 봄)
- 모호한/중복된 표현 지양 → ALF가 엉뚱한 문서 참조 가능
- **중복 내용 정리**: 비슷한 FAQ가 여러 개면 일관성·정확도 저하 → 한 FAQ로 통합
- 퍼블리시 상태로 발행해야 참조 가능 (수정 중 상태는 미참조)
- 제목(질문)과 동일한 키워드를 폴더 참조 설명에도 일치시키기

【톤·문체 — 채널톡 Help Doc 표준 그대로】
- 친근한 존댓말 + `-어요`/`-습니다` 종결형
  예) "마이페이지에서 가능해요", "처리해드려요"
- 권유 표현: "~해주시면 좋아요"
- 안심 표현: "안심하세요", "걱정하지 않으셔도 돼요"

【반드시 피해야 할 표현】
- ❌ 작성자/상담원 자기소개
- ❌ 직접 질문 (예: "혹시 도움이 필요하실까요?")
- ❌ 마무리 인사 (예: "감사합니다", "오늘 상담은 마무리할게요")
- ❌ 1회성 처리 응답 (예: "원인 확인 후 ~ 조치하였습니다") → 일반 안내로 추상화
- ❌ 이모지/감탄사
- ❌ 모호 표현 ("보통/일반적으로/경우에 따라")
- ❌ "담당자 문의" 단독 사용

【작성 규칙】
1. 대표 질문(question): 정확한 키워드를 포함한 자연스러운 질문문 (100자 이내)
   - 핵심 키워드는 반드시 질문에 포함 (ALF는 제목·질문 우선 검색)
2. 변형 질문(variations): 실제 고객이 쓴 표현 5~10개
   - 줄임말/구어체/오타 포함 (예: "환불 어케해요?")
   - 동의어 변주 ("취소", "해지", "환불")
3. 답변(answer): 500자 이내, 친근한 Help Doc 톤. **순수 텍스트(plain text)로만 작성**
   - 결론부터, 조건 분기 명확히
   - 관련 페이지 링크가 있다면 답변에 포함
   - 1:1 상담 어투를 **일반 안내 어투로 변환** ("~조치하였습니다" → "~할 수 있어요")
   - **Markdown 금지**: 채널톡 FAQ가 코드 블록으로 인식하므로 다음 금지
     · 코드 펜스 (``` 또는 ~~~)
     · 백틱 (`) — 메뉴명·버튼명도 따옴표 없이 그냥 텍스트로
     · 줄 시작 들여쓰기 4칸 이상 (코드 블록 인식)
     · ## 같은 헤딩 마커 (단, 1., 2. 번호 목록은 OK)
   - 메뉴 경로 표기: 사이트 관리 > 사이트 디자인 (한 줄, 백틱 없이)
4. ALF는 고객/상담 태그·설명은 참조하지 않으므로, 답변에 필요한 모든 정보 포함

【상담 데이터 활용】
- **상담원(사람) 답변만 참조**, ALF/봇 답변은 무시
- 여러 상담의 공통 패턴/문구만 추출
- 특정 사례의 처리 응답은 일반화

【언어 규칙】
모든 출력은 반드시 한국어로만. 일본어(히라가나/가타카나/한자), 중국어 한자, 영어 단어 금지.
예) 編集→편집, プラン→플랜, サイト→사이트."""


def build_faq_prompt(cluster_label: str, chats: list) -> str:
    samples = []
    for i, c in enumerate(chats[:50]):
        msgs = c.get("messages", [])
        # 고객 메시지 (질문 패턴용)
        customer_msgs = [m.get("text", "")[:200] for m in msgs if m.get("role") == "customer"][:2]
        # 상담원(매니저) 메시지만 - ALF/봇 제외, 답변 패턴 추출용
        agent_msgs = [m.get("text", "") for m in msgs if m.get("role") == "agent"]
        if not customer_msgs or not agent_msgs:
            continue
        # 상담원 답변은 더 많이, 더 길게 포함 (답변 핵심 자료)
        agent_full = "\n".join(f"  → {a[:300]}" for a in agent_msgs[:3])
        samples.append(
            f"[상담 {i+1}]\n"
            f"고객: {' / '.join(customer_msgs)}\n"
            f"상담원이 답한 내용:\n{agent_full}"
        )

    if not samples:
        return f"'{cluster_label}' 관련 상담원 답변 데이터가 부족해서 FAQ 작성이 어렵습니다."

    return f"""아래는 '{cluster_label}' 상황의 실제 채널톡 상담 {len(samples)}건입니다.

{chr(10).join(samples)}

위 데이터에서 **공통된 정보·절차·정책**만 뽑아 FAQ 1개를 작성해주세요.

【필수 변환 작업】
1. 1:1 상담 응답 어투 → 일반 안내 어투
   - "원인 확인 후 ~ 조치하였습니다" → "~할 수 있어요"
   - "혹시 ~ 필요하실까요?" → 삭제
   - "○○ 코치 ○○입니다", "감사합니다" → 삭제
2. 특정 사례의 처리 응답 → 누구에게나 적용되는 일반 안내
3. 친근한 `-어요/-습니다` Help Doc 톤 유지

【변형 질문】
- 위 고객들이 실제로 쓴 표현을 그대로 인용 (줄임말·구어체·오타 포함)
- 5~10개, 키워드 변주

반드시 아래 JSON 형식으로만 답변하세요:
{{
  "question": "대표 질문 (100자 이내, 핵심 키워드 포함)",
  "variations": ["변형 질문 1", "변형 질문 2", "..."],
  "answer": "답변 본문 (500자 이내, Help Doc 톤, plain text만 — 코드펜스/백틱/들여쓰기 금지)"
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

        prompt = build_faq_prompt(cluster_label, chats)
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
