from __future__ import annotations
import os
import re
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json

import urllib.parse
import urllib.request
from _alf_common import call_anthropic, supabase_get, supabase_post, make_handler_base, strip_article_boilerplate, verify_draft, extract_json


def fetch_reference_url(url: str, max_chars: int = 5000) -> str:
    """첨부 URL을 HTML로 가져와 텍스트만 추출. 실패 시 빈 문자열."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (cx-alf reference fetch)"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<nav[^>]*>.*?</nav>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<footer[^>]*>.*?</footer>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as e:
        print(f"[warn] reference URL fetch 실패 ({url}): {e}")
        return ""

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

ALF_SYSTEM_PROMPT = """당신은 채널톡 ALF(AI 에이전트)용 지식 아티클을 작성하는 전문가입니다.
ALF는 RAG로 등록된 지식을 참조해 고객 응대하므로, 잘 정리된 문서일수록 응답 품질이 올라갑니다.

【아티클의 성격】
- 채널톡 도큐먼트(Help Doc) 형식의 **재사용 가능한 지식 문서**
- 1:1 상담 응답을 옮긴 것이 아니라, 누가 봐도 같은 정보가 적용되는 일반 가이드
- 마케팅 블로그가 아닌 친절한 기술문서

【ALF가 잘 찾기 위한 필수 조건】 (채널톡 CS팀 직접 답변 반영)
- ALF는 **제목·소제목**을 주요 기준으로 구조 파악 → `##`, `###`로 섹션 명확히 구분
- **제목에 핵심 키워드(상품명·기능명 등) 반드시 포함**. 본문에만 키워드 있으면 매칭 실패 가능
- 이미지만으로 구성된 아티클은 ALF가 참조 못함 → **이미지 위아래에 설명 텍스트 추가**
- **불릿 포인트(`-`) / 번호 목록(`1.`)** 적극 활용 → 정보의 선후 관계를 ALF가 정확히 파악
- **완성된 문장**(`-어요/-습니다`)으로 작성 → 끊기지 않은 형태일 때 ALF가 의미를 가장 정확히 해석
- 모호한·중복된 표현 지양 → 엉뚱한 문서 참조 위험
- 퍼블리시 상태만 참조됨 (수정 중 상태는 미참조)
- 폴더 참조 설명과 일치하는 키워드 사용
- **중복 내용 정리**: 비슷한 정보는 한 문서에 모아 명확히 작성 → 여러 문서에 중복되면 ALF가 일관성·정확도 떨어진 답변 가능

【톤·문체 가이드 — 채널톡 Help Doc 표준 그대로】
- 친근한 존댓말 + `-어요`/`-습니다` 종결형
  예) "다운로드 받을 수 있어요", "처리해드려요", "이전됩니다"
- 권유 표현: "~해주시면 좋아요", "~를 권장해요"
- 안심 표현: "안심하세요", "걱정하지 않으셔도 돼요"
- 정의-설명 구조 활용 가능: `{기능명}: {설명}`

【반드시 피해야 할 표현】
- ❌ 작성자/상담원 자기소개 (예: "라이브클래스 코치 조승현입니다")
- ❌ 직접 질문 (예: "혹시 어떤 부분에서 도움이 필요하실까요?")
- ❌ 마무리 인사 (예: "감사합니다 🙏", "상담은 여기서 마무리할게요")
- ❌ 1회성 처리 응답 (예: "원인 확인 후 ~ 조치하였습니다") → 일반 안내로 추상화
- ❌ 이모지/감탄사/마케팅 표현 (🎉, "주목해 주세요!", "요즘 핫한")
- ❌ 모호한 표현 ("보통", "일반적으로", "경우에 따라")
- ❌ "담당자 문의" 단독 사용 — 구체적 조건/방법 함께 명시

【작성 원칙】(채널톡 ALF 팀 공식 가이드 직접 반영)
1. **1문서 = 1가지 큰 주제** (관련 케이스는 묶기, 무관한 케이스는 분리)
   - 묶기 권장: 같은 주제·공통 정책 → "회원 계정 관리 가이드" 안에 [가입/탈퇴/비밀번호 변경]
   - 분리 권장: 시나리오·정책이 완전 다름 → "결제 환불"과 "도메인 연결"은 별도 아티클
   - 이유: 관련 주제를 묶으면 ALF가 맥락 따라 필요한 부분만 참조 + 공통 정책 변경 시 한 곳만 수정
2. **제목**: ALF는 제목을 가장 먼저 검색함. **상품명·핵심 서비스명·기능명 반드시 포함**
   - O 단일 케이스: "구독 플랜 환불받는 방법"
   - O 묶음 케이스: "회원 계정 관리 가이드" / "결제·환불 종합 안내"
   - X: "환불 안내" / "변경 관련 문의"
3. 첫 문단 = 결론 또는 문서 개요. ALF는 앞부분을 우선 참조함
4. **대제목·중제목·소제목 적극 활용** — 영역 명확히 구분
   - `##` = 대제목 (예: ## 1. 회원가입 방법)
   - `###` = 중제목 (예: ### 가입 절차)
   - ALF는 본문 내 소제목을 두 번째로 우선 참조함
5. **표준 구조 권장 (채널톡 추천)**
   **단일 케이스 아티클**:
   ```
   ## 문제 상황
   (어떤 케이스인지 명확히)

   ## 해결 방법
   1. 첫 번째 단계
   2. 두 번째 단계
   ```
   **묶음 케이스 아티클** (관련 주제 여러 개):
   ```
   ## 1. {첫 번째 케이스명}
   설명 및 절차

   ## 2. {두 번째 케이스명}
   설명 및 절차

   ## 3. {세 번째 케이스명}
   설명 및 절차
   ```
6. 조건 분기는 케이스별로 명확히 명시 (## 소제목으로 분리)
7. 2,000자 이내 (초과 시 별도 아티클로 분리). 묶음이라도 2,000자 안에서.
8. 관련 페이지·URL은 본문에 링크로 포함
9. Markdown: ## 섹션, - 목록, > 콜아웃, | 표 (본문 안에 # H1 제목은 넣지 말 것 — 제목은 별도 필드). **## 및 - 다음에는 반드시 공백 1칸 필수** (예: `## 제목` O, `##제목` X)
10. **메뉴 경로 표기**: 백틱(`)·따옴표 없이, 화살표(>)로 한 줄로 표시. 줄바꿈 금지.
    - O: 사이트 관리 > 사이트 디자인 > 고급 설정 > 로그인 종류
    - O: 사이트 관리 > 매출 및 정산 관리 > 결제 조회
    - X: `사이트 관리` > `사이트 디자인` > `고급 설정` > `로그인 종류`
    - X: 사이트 관리 →
         사이트 디자인 →
         고급 설정
9. **콜아웃(`>`) 사용 규칙**: Tip·주의사항을 콜아웃으로 표시할 수 있으나, **상담 데이터에서 상담원이 실제로 언급한 내용일 때만** 사용. LLM 추측으로 만들어진 일반론은 콜아웃으로 넣지 말 것.

【상담 데이터 활용법】
- **상담원(사람) 답변만 참조**, ALF/봇 답변은 무시
- 여러 상담의 공통 패턴/단계/문구를 추출
- 특정 고객의 특정 사례는 **일반 안내로 추상화**
  - ❌ "원인 확인 후 해당 클래스에서 수정 조치하였습니다"
  - ✅ "수강생 목록 다운로드는 클래스 설정 페이지에서 받을 수 있어요"
- **매니저 답변에 명시되지 않은 정책·절차·메뉴 경로·조건은 절대 생성하지 마세요**
  - 매니저가 어떻게 처리하는지 안내한 단계만 옮기고, 추측되는 정보는 본문에서 제외
  - 정책이 매니저들 답변 사이에서 상충하면 다수가 안내한 쪽을 따르고, 명확하지 않으면 그 정책은 생략
  - 차라리 짧고 정확한 아티클이 길고 추측 섞인 아티클보다 ALF 품질에 좋음

【언어 규칙】
모든 출력은 반드시 한국어로만 작성. 일본어(히라가나/가타카나/한자), 중국어 한자, 영어 단어 금지.
예) 編集→편집, プラン→플랜, サイト→사이트, ホスティング→호스팅."""


def build_generate_prompt(cluster_label: str, chats: list, reference_doc: str = "") -> str:
    samples = []
    for i, c in enumerate(chats[:50]):
        msgs = c.get("messages", [])
        customer_msgs = [m.get("text", "")[:200] for m in msgs if m.get("role") == "customer"][:2]
        # 상담원 답변만 (ALF/봇 제외, 답변 패턴 추출용) — 정책·절차의 핵심이 잘리지 않도록 충분히 길게
        agent_msgs = [m.get("text", "") for m in msgs if m.get("role") == "agent"]
        if not customer_msgs or not agent_msgs:
            continue
        agent_full = "\n".join(f"  → {a[:600]}" for a in agent_msgs[:6])
        samples.append(
            f"[상담 {i+1}]\n"
            f"고객: {' / '.join(customer_msgs)}\n"
            f"상담원이 답한 내용:\n{agent_full}"
        )

    if not samples:
        return f"'{cluster_label}' 관련 상담원 답변 데이터가 부족합니다."

    ref_section = ""
    if reference_doc:
        ref_section = f"""

【참고 가이드 문서】 ← 우선 기준
작성자가 첨부한 운영 가이드 원본입니다. **정책·절차·메뉴 경로·용어는 이 가이드를 정답으로 사용**하고, 상담 데이터는 고객이 실제로 묻는 표현·자주 헷갈리는 케이스·매니저 답변 톤을 추출하는 보강 자료로만 활용해주세요. 가이드에 없는 내용을 상담 데이터에서 가져오는 건 가능하지만, 가이드와 충돌하면 가이드를 따르세요.

\"\"\"
{reference_doc}
\"\"\"
"""

    return f"""아래는 '{cluster_label}' 상황의 실제 채널톡 상담 {len(samples)}건입니다.

{chr(10).join(samples)}
{ref_section}
위 데이터에서 **공통된 정보·절차·정책**만 뽑아 채널톡 Help Doc 형식의 지식 아티클로 정리해주세요.

【필수 변환 작업】
1. 1:1 상담 응답 어투 → 일반 안내 어투로 변환
   - "원인 확인 후 ~ 조치하였습니다" → "~할 수 있어요"
   - "혹시 ~ 도움이 필요하실까요?" → 삭제
   - "라이브클래스 코치 ○○입니다" → 삭제
   - "감사합니다", "마무리할게요" → 삭제
2. 특정 사례의 처리 응답 → 누구에게나 적용되는 일반 안내
3. 상담원이 사용한 단계/방법/정책은 유지하되 친근한 `-어요/-습니다` Help Doc 톤으로

【출력 형식 — 반드시 JSON으로만 출력】
```json
{{
  "title": "고객이 검색할 표현 (예: 구독 플랜 환불 받는 방법)",
  "subtitle": "본문 핵심을 한 줄로 요약 (50자 이내)",
  "content": "## 섹션부터 시작하는 본문 마크다운 (H1 # 금지, ## 소제목으로 케이스/단계 분리)"
}}
```

- **title**: 채널톡 아티클 "제목" 필드용. 핵심 키워드(상품명·기능명)와 동사 포함.
- **subtitle**: 채널톡 아티클 "소제목" 필드용. 본문을 한 줄로 압축한 요약. 검색에 도움이 되는 추가 키워드 포함 권장.
- **content**: 본문. ## 소제목으로 단계/케이스별 분리, 본문 전체 2,000자 이내. 본문 안에 H1(`#`)은 절대 넣지 말 것.
- JSON 외 다른 텍스트(설명·인사·코드블록 시작 표시 외)는 출력하지 말 것."""


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
        reference_doc = (body.get("reference_doc") or "").strip()
        reference_url = (body.get("reference_url") or "").strip()
        if reference_url and not reference_doc:
            reference_doc = fetch_reference_url(reference_url)

        if not chat_ids:
            self._respond(400, {"ok": False, "error": "chat_ids 필요"})
            return
        if not cluster_label and not single_chat:
            self._respond(400, {"ok": False, "error": "cluster_label 필요"})
            return

        # chat_ids로 상담 내용 로드 (tags도 함께) — 최신순 우선
        ids_param = ",".join(f'"{cid}"' for cid in chat_ids[:20])
        url = (f"{SUPABASE_URL}/rest/v1/cx_full_messages"
               f"?select=chat_id,messages,tags,date&chat_id=in.({ids_param})"
               f"&order=date.desc")
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

        # 단일 상담 모드: cluster_label이 비어있으면 LLM이 자동 추출
        if single_chat and not cluster_label and chats:
            first_chat = chats[0]
            msgs = first_chat.get("messages", [])
            customer_msgs = [m.get("text", "")[:200] for m in msgs if m.get("role") == "customer"][:3]
            if customer_msgs:
                label_prompt = (
                    "다음 고객 문의의 핵심 시나리오를 한 줄로 요약해주세요 (예: '구독 플랜 환불 받는 방법').\n\n"
                    + " / ".join(customer_msgs)
                    + "\n\n한 줄 시나리오만 출력 (따옴표·설명 없이):"
                )
                try:
                    cluster_label = call_anthropic(
                        label_prompt, max_tokens=80, api_key=OPENAI_API_KEY,
                    ).strip().strip('"').strip("'")
                except Exception:
                    cluster_label = "단일 상담 기반 가이드"
            else:
                cluster_label = "단일 상담 기반 가이드"

        # 유사 케이스 종합 분석: 원본 chat의 tags로 200건 fetch → LLM 의미 검색
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
                    print(f"[warn] 유사 케이스 fetch 실패: {e}")
                    candidates = []
                # LLM 의미 검색으로 유사한 30건 추출
                if candidates:
                    try:
                        from alf_search import filter_by_semantic
                        similar_chats = filter_by_semantic(candidates, cluster_label)[:30]
                        chats.extend(similar_chats)
                        similar_count = len(similar_chats)
                    except Exception as e:
                        print(f"[warn] 유사 케이스 의미 검색 실패: {e}")
            print(f"[info] 유사 케이스 {similar_count}건 추가됨 (원본 1건 + 유사 {similar_count}건 = 총 {len(chats)}건 분석)")

        prompt = build_generate_prompt(cluster_label, chats, reference_doc=reference_doc)
        raw = call_anthropic(
            prompt, system=ALF_SYSTEM_PROMPT,
            max_tokens=2048, api_key=OPENAI_API_KEY,
        )

        # JSON 응답 파싱 (title/subtitle/content) + fallback
        title = cluster_label
        subtitle = ""
        draft_content = ""
        try:
            parsed = extract_json(raw)
            if isinstance(parsed, dict):
                title = (parsed.get("title") or cluster_label).strip()
                subtitle = (parsed.get("subtitle") or "").strip()
                draft_content = (parsed.get("content") or "").strip()
        except Exception:
            pass

        # fallback: JSON 파싱 실패 시 raw에서 H1 추출 후 그대로 사용
        if not draft_content:
            draft_content = raw
            for line in raw.split("\n"):
                if line.startswith("# "):
                    title = line[2:].strip()
                    break

        # 인사·자기소개·마무리 인사 제거 + 자동 검증
        draft_content = strip_article_boilerplate(draft_content)
        verification = verify_draft(draft_content, "article", cluster_label, title=title)
        draft_content = verification["fixed"]

        # alf_drafts에 저장
        draft = {
            "title": title,
            "subtitle": subtitle,
            "cluster_label": cluster_label,
            "content": draft_content,
            "source_chat_count": len(chats),
        }
        saved = supabase_post(f"{SUPABASE_URL}/rest/v1/alf_drafts", draft, SUPABASE_SERVICE_KEY)
        draft_id = saved[0]["id"] if isinstance(saved, list) and saved else None

        self._respond(200, {
            "ok": True, "draft_id": draft_id, "title": title, "subtitle": subtitle,
            "content": draft_content, "warnings": verification["warnings"],
            "analyzed_chat_count": len(chats),
            "similar_count": similar_count,
        })
