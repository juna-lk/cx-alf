from __future__ import annotations
import ipaddress
import os
import re
import socket
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json

import urllib.error
import urllib.parse
import urllib.request
from _alf_common import call_anthropic, supabase_get, supabase_post, make_handler_base, strip_article_boilerplate, verify_draft, extract_json, is_safe_postgrest_tag, select_specific_tag, docs_req, list_docs_spaces


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """SSRF 방어 — redirect를 통한 private host 우회 차단."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def fetch_reference_url(url: str, max_chars: int = 5000) -> str:
    """첨부 URL을 HTML로 가져와 텍스트만 추출. 실패 시 빈 문자열.

    SSRF 방어:
    - scheme http/https만 허용
    - host resolve 후 private/loopback/link-local/multicast/reserved IP 차단
    - HTTP redirect 차단 (private host 우회 방지)
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            print(f"[warn] reference URL 차단 — scheme={parsed.scheme!r}")
            return ""
        host = parsed.hostname or ""
        if not host:
            return ""
        try:
            resolved = {info[4][0] for info in socket.getaddrinfo(host, None)}
        except socket.gaierror:
            print(f"[warn] reference URL host resolve 실패 ({host})")
            return ""
        for ip_str in resolved:
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
                print(f"[warn] reference URL 차단 — internal IP {ip} ({host})")
                return ""
        opener = urllib.request.build_opener(_NoRedirectHandler())
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (cx-alf reference fetch)"})
        with opener.open(req, timeout=15) as resp:
            html = resp.read(2 * 1024 * 1024).decode("utf-8", errors="ignore")
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<nav[^>]*>.*?</nav>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<footer[^>]*>.*?</footer>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            print(f"[warn] reference URL 리다이렉트 차단 ({url})")
        else:
            print(f"[warn] reference URL HTTP {e.code} ({url})")
        return ""
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
- ❌ **특정 날짜·시점·처리 결과 표현** — 다음 고객에게 그대로 적용 불가
  · "2/10" / "2월 25일(금)" → "결제일" / "정산 예정일" / "환불 가능 기간 내"
  · "오늘" / "어제" / "내일" / "이번 주" → "신청일" / "처리일" / "해당 기간"
  · "확인됩니다" / "처리되었습니다" / "진행된 것으로 확인됩니다" → "확인할 수 있어요" / "처리해드려요"

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
5. **표준 구조 권장 (채널톡 추천)** — 케이스에 맞게 세 패턴 중 선택
   **A. 단일 케이스 아티클** (절차/방법 가이드):
   ```
   ## 문제 상황 (또는 ○○이란?)
   한 문장 정의 + 보조 설명·책임 한계.

   ## 해결 방법 (또는 ○○ 어떻게 하나요?)
   1. 첫 번째 단계
   2. 두 번째 단계
   ```
   **B. 묶음 케이스 — 명사형** (절차/기능 묶음):
   ```
   ## 1. {첫 번째 기능}
   설명 및 절차

   ## 2. {두 번째 기능}
   설명 및 절차
   ```
   **C. 묶음 케이스 — 질문형 ★ 권장** (자주 묻는 질문 묶음 — 고객 검색 의도와 직접 매칭):
   ```
   ## ○○이란?
   한 문장 정의. 이어서 보조 설명·책임 한계·가공 기준. 마지막은 관련 가이드 안내.

   ## ○○는 언제 ~되나요?
   - 1분기: ...
   - 2분기: ...

   ## ○○는 어디서 확인할 수 있나요?
   사이트 관리 > 매출 및 정산 관리 > ○○

   ## ○○ 전에 다른 방법으로 확인하기
   대체 시나리오·경로 안내
   ```
5-1. **첫 ## 섹션 = 정의/개요** — 모든 패턴에서 첫 H2 직후 한 문장 정의/개요를 배치하고, 같은 단락에 책임 한계·예외·가공 기준을 자연스럽게 녹임. 첫 단락 마지막에 관련 가이드 링크 안내(예: "자세한 내용은 ○○ 가이드를 통해 확인하실 수 있어요").
5-2. **고객 검색 표현을 H2로 그대로 받기** — "○○이란?", "○○는 언제 ~?", "○○는 어디서 ~?", "○○ 어떻게 ~?" 같은 질문형 헤딩이 ALF 매칭 정확도를 높임. 사용자가 실제로 묻는 표현을 그대로 H2로 사용.
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

【매니저 답변 우선 원칙】 user prompt에 제공되는 '매니저들이 실제로 답변한 내용' 블록을 본문 작성의 가장 강한 근거로 삼으세요. 매니저들이 자주 쓴 표현·종결형·메뉴 경로 표기는 가능한 한 그대로 차용합니다. LLM이 만든 일반론보다 매니저가 실제로 쓴 표현이 우선.

【등록 문서 스타일 참고】 user prompt의 '우리 채널톡에 이미 등록된 문서들의 스타일' 블록을 참고해서 헤딩 구조·문단 길이·종결형 톤을 우리 도큐먼트 스타일과 일관되게 작성하세요. 단, 가이드라인 17개 룰(이모지·헤딩공백·H1·소제목·보일러플레이트·모호표현·날짜·시점·1회성처리·담당자문의·종결형·글자수·목록·단계번호·메뉴경로·외국어·제목)은 모두 준수해야 합니다.

【권장 아티클 예시 — 이 톤·구조를 모방해서 작성하세요】
다음은 실제 운영 중인 라이브클래스 도움말 아티클입니다. **헤딩 구성·단락 길이·정보 밀도·종결형 혼용·메뉴 경로 표기**를 그대로 모방하세요.

---
제목: 부가세 신고 자료
소제목: 매출 신고 시 참고할 수 있는 분기별 부가세 자료 안내

## 부가세 신고 자료란?
부가세 신고 자료는 매출 신고 시 참고하실 수 있는 자료예요. 신고 시에 좀 더 용이하게 참고하실 수 있도록 가공된 자료입니다. 실제 매출은 개별 사이트에서 직접 신고하셔야 하며, 라이브클래스는 미신고분에 대한 책임을 지지 않습니다. 부가세 신고 자료는 과세사업자/과세상품을 기준으로 제공되고 있어요. 면세가액이 포함된 매출이 있는 경우, 자체적인 회계자료를 기반으로 확인 및 신고해 주셔야 합니다. 자세한 내용은 매출 및 정산 가이드와 매출 신고 가이드를 통해 확인하실 수 있어요.

## 부가세 신고 참고 자료는 언제 업데이트되나요?
부가세 신고 참고 자료는 분기별로 업데이트되고 있어요.
- 1분기(1~3월): 분기 종료 후 약 15일 경
- 2분기(4~6월): 분기 종료 후 약 15일 경
- 3분기(7~9월): 분기 종료 후 약 15일 경
- 4분기(10~12월): 분기 종료 후 약 15일 경

## 부가세 신고 참고 자료는 어디서 확인할 수 있나요?
부가세 신고 자료는 아래 경로를 통해 확인 및 엑셀 파일로 다운로드할 수 있어요.

사이트 관리 > 매출 및 정산 관리 > 부가세 신고 자료

## 부가세 신고 자료 업데이트 전에 매출 내역 확인하기
부가세 신고 자료는 참고용으로 제공되는 자료입니다. 만약, 부가세 신고 자료 업데이트 전에 매출 자료가 필요하시다면 아래 경로를 통해 직접 기간별 매출 등을 확인하여 부가세 신고를 진행하시면 됩니다.

- 사이트 관리 > 매출 및 정산 관리 > 결제 조회
- 사이트 관리 > 매출 및 정산 관리 > 정산 내역
---

**이 예시의 좋은 점 — 반드시 체득**:
- 첫 ## = 한 문장 정의 + 보조 설명 + 책임 한계("~책임을 지지 않습니다") + 가공 기준("과세사업자/과세상품 기준") + 예외 처리("면세가액이 포함된 경우") + 관련 가이드 링크 안내 — 모두 자연스럽게 한 단락 흐름.
- 둘째~넷째 ## = 고객이 실제 묻는 질문 표현을 그대로 헤딩화("언제?", "어디서?", "전에 다른 방법으로?").
- 일정·경로는 평문 나열 없이 즉시 불릿 또는 단일 라인 메뉴 경로.
- "~예요/~합니다" 자연스럽게 혼용 (정중함과 친근함 균형). 책임·주의사항은 "~해 주셔야 합니다"로 약간 단호한 톤.
- 마무리 인사·자기소개·이모지 없이 정보만으로 단단하게 구성.
- 마지막 ## 은 보조 시나리오 ("정자료 나오기 전에 어떻게 미리 확인할까?") 안내 — 실사용 흐름에서 자주 묻는 후속 질문을 선제 처리.

【언어 규칙】
모든 출력은 반드시 한국어로만 작성. 일본어(히라가나/가타카나/한자), 중국어 한자, 영어 단어 금지.
예) 編集→편집, プラン→플랜, サイト→사이트, ホスティング→호스팅."""


def fetch_registered_articles(max_articles: int = 5, min_chars: int = 200) -> list[dict]:
    """채널톡 ALF_MD 스페이스에 등록된 아티클을 가져옵니다.

    실패 시 빈 list 반환 (절대 throw 안 함).
    """
    try:
        data = docs_req(
            "/open/v1/spaces/$me/articles?language=ko&limit=100",
            method="GET",
        )
        articles = data.get("articles") or []
        result = []
        for a in articles:
            title = (a.get("title") or a.get("name") or "").strip()
            subtitle = (a.get("subtitle") or a.get("summary") or "").strip()
            body_html = a.get("bodyHtml") or ""
            # HTML 태그 제거
            body_text = re.sub(r"<[^>]+>", " ", body_html)
            body_text = re.sub(r"\s+", " ", body_text).strip()
            combined = title + subtitle + body_text
            if len(combined) < min_chars:
                continue
            result.append({
                "title": title,
                "subtitle": subtitle,
                "body_preview": body_text[:600],
            })
        # 최신 순 — API가 이미 정렬된 상태로 반환하므로 앞에서 자르기
        return result[:max_articles]
    except Exception as e:
        print(f"[warn] fetch_registered_articles 실패: {e}")
        return []


def build_generate_prompt(cluster_label: str, chats: list, reference_doc: str = "") -> str | None:
    samples = []
    manager_blocks: list[str] = []  # 매니저 답변만 별도 수집

    for i, c in enumerate(chats[:50]):
        msgs = c.get("messages", [])
        customer_msgs = [m.get("text", "")[:200] for m in msgs if m.get("role") == "customer"][:2]
        # 모든 매니저 답변 사용 (ALF/봇 제외)
        agent_msgs = [m.get("text", "") for m in msgs if m.get("role") == "agent"]
        if not customer_msgs or not agent_msgs:
            continue
        agent_full = "\n".join(f"  → {a[:600]}" for a in agent_msgs[:6])
        samples.append(
            f"[상담 {i+1}]\n"
            f"고객: {' / '.join(customer_msgs)}\n"
            f"상담원이 답한 내용:\n{agent_full}"
        )

        # 매니저 답변 별도 수집 (30자 미만 인사/봇 표현 제외, 최대 50건)
        if len(manager_blocks) < 50:
            chat_id = c.get("chat_id", f"chat_{i+1}")
            valid_answers = [
                a[:200] for a in agent_msgs
                if len(a.strip()) >= 30
            ]
            if valid_answers:
                block_lines = "\n".join(f"  {a}" for a in valid_answers[:4])
                manager_blocks.append(f"[{chat_id}]\n{block_lines}")

    if not samples:
        return None

    # 매니저 답변 별도 블록
    manager_section = ""
    if manager_blocks:
        manager_section = (
            "\n## 매니저들이 실제로 답변한 내용 (이 표현·구조·정책을 우선 차용해주세요)\n\n"
            + "\n\n".join(manager_blocks[:50])
            + "\n"
        )

    # 채널톡 등록 문서 reference 블록
    article_section = ""
    registered = fetch_registered_articles(max_articles=5, min_chars=200)
    if registered:
        parts = []
        for a in registered:
            part = f"제목: {a['title']}"
            if a["subtitle"]:
                part += f"\n소제목: {a['subtitle']}"
            part += f"\n본문(발췌): {a['body_preview']}"
            parts.append(part)
        article_section = (
            "\n## 우리 채널톡에 이미 등록된 문서들의 스타일 "
            "(참고 — 가이드라인 17개 룰은 그대로 준수)\n\n"
            + "\n\n---\n\n".join(parts)
            + "\n"
        )

    ref_section = ""
    if reference_doc:
        ref_section = f"""

【참고 가이드 문서】 ← 우선 기준
작성자가 첨부한 운영 가이드 원본입니다. **정책·절차·메뉴 경로·용어는 이 가이드를 정답으로 사용**하고, 상담 데이터는 고객이 실제로 묻는 표현·자주 헷갈리는 케이스·매니저 답변 톤을 추출하는 보강 자료로만 활용해주세요. 가이드에 없는 내용을 상담 데이터에서 가져오는 건 가능하지만, 가이드와 충돌하면 가이드를 따르세요.

\"\"\"
{reference_doc}
\"\"\"
"""

    return f"""{manager_section}{article_section}아래는 '{cluster_label}' 상황의 실제 채널톡 상담 {len(samples)}건입니다.

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
- **content 구조 강제** (시스템 프롬프트 마지막 권장 예시 형태를 모방):
  · 첫 줄은 반드시 `## ○○이란?` 또는 `## 문제 상황` 같은 **정의/개요 헤딩**으로 시작.
  · 첫 H2 직후 첫 단락에 한 문장 정의 + 보조 설명·책임 한계·예외·관련 가이드 안내를 자연스럽게 통합.
  · 묶음 케이스라면 둘째 H2부터 "○○는 언제 ~?", "○○는 어디서 ~?", "○○ 전에 ~하는 방법" 같은 **고객 검색 표현을 그대로 받는 질문형 헤딩**을 우선 사용.
  · 일정·조건·항목은 평문 나열 금지 → 반드시 불릿(`- `) 또는 번호(`1. `).
  · 메뉴 경로는 단일 라인 + `>` 화살표 (예: `사이트 관리 > 매출 및 정산 관리 > 부가세 신고 자료`). 백틱·따옴표·줄바꿈 금지.
- JSON 외 다른 텍스트(설명·인사·코드블록 시작 표시 외)는 출력하지 말 것."""


_Base = make_handler_base()


class handler(_Base):
    def do_POST(self):
        if not self._check_auth():
            return
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}
        mode = (body.get("mode") or "").strip()

        # ─── 채널톡 ALF 등록 지식 키워드 검색 ────────────────────────────
        # ALF Studio 검증 탭에서 "기존 채널톡 아티클 불러오기"용.
        # 채널톡 도큐먼트 API의 list endpoint를 호출하고 query로 필터링.
        if mode == "kb_search":
            query = (body.get("query") or "").strip()
            space = body.get("space") or None
            # 빈 query → 전체 list 반환 (마이그레이션 탭에서 도큐먼트 list 받을 때 사용)
            try:
                data = docs_req(
                    "/open/v1/spaces/$me/articles?language=ko&limit=100",
                    method="GET",
                    space=space,
                )
            except urllib.error.HTTPError as e:
                err_body = ""
                try:
                    err_body = e.read().decode()
                except Exception:
                    pass
                print(f"[kb_search] HTTP {e.code}: {err_body[:200]}")
                self._respond(500, {"ok": False, "error": f"채널톡 API 오류 ({e.code})"})
                return
            except Exception as e:
                print(f"[kb_search] 실패: {e}")
                self._respond(500, {"ok": False, "error": f"채널톡 검색 실패: {e}"})
                return
            articles = data.get("articles") or []
            q_lower = query.lower()
            results = []
            for a in articles:
                title = (a.get("title") or a.get("name") or "")
                subtitle = (a.get("subtitle") or a.get("summary") or "")
                body_html = a.get("bodyHtml") or ""
                # HTML 태그 제거해서 plain text body 만들기
                body_text = re.sub(r"<[^>]+>", " ", body_html)
                body_text = re.sub(r"\s+", " ", body_text).strip()
                score = 0
                title_l = title.lower()
                subtitle_l = subtitle.lower()
                body_l = body_text.lower()
                if q_lower:
                    if q_lower in title_l:
                        score += 100
                    if q_lower in subtitle_l:
                        score += 50
                    if q_lower in body_l:
                        score += 10
                    # 토큰 단위 부분 매칭(공백 분리)
                    for token in q_lower.split():
                        if len(token) < 2:
                            continue
                        if token in title_l:
                            score += 20
                        if token in subtitle_l:
                            score += 10
                        if token in body_l:
                            score += 2
                    if score <= 0:
                        continue
                else:
                    # 빈 query: 모든 article 통과. 정렬은 published 우선 + 최신순
                    score = 1
                results.append({
                    "id": a.get("id"),
                    "title": title,
                    "subtitle": subtitle,
                    "state": a.get("state", ""),
                    "slug": a.get("slug", ""),
                    "bodyHtml": body_html,
                    "preview": body_text[:200],
                    "score": score,
                    "updatedAt": a.get("updatedAt"),
                })
            if q_lower:
                results.sort(key=lambda x: (-x["score"], -(x.get("updatedAt") or 0)))
                results = results[:20]
            else:
                # 빈 query: published 먼저, 그 안에서 최신순. limit 없음 (전체 반환)
                results.sort(key=lambda x: (0 if x["state"] == "published" else 1, -(x.get("updatedAt") or 0)))
            self._respond(200, {
                "ok": True,
                "results": results,
                "total_scanned": len(articles),
                "matched": len(results),
                "spaces": list_docs_spaces(),
            })
            return

        # ─── LK1.0 → LK2.0 도큐먼트 마이그레이션 ──────────────────────────
        # article_id로 채널톡 도큐먼트 한 건을 받아 lk2_mapping.json + new_features를
        # reference로 LLM에 보내 2.0 버전으로 변환한 마크다운을 반환.
        # 별도 mode "migrate_apply"가 alf_publish.py에서 채널톡 update PUT 호출.
        if mode == "migrate":
            article_id = (body.get("article_id") or "").strip()
            space = body.get("space") or None
            if not article_id:
                self._respond(400, {"ok": False, "error": "article_id 필요"})
                return
            try:
                data = docs_req(
                    "/open/v1/spaces/$me/articles?language=ko&limit=100",
                    method="GET",
                    space=space,
                )
            except Exception as e:
                print(f"[migrate] 도큐먼트 fetch 실패: {e}")
                self._respond(500, {"ok": False, "error": f"채널톡 도큐먼트 fetch 실패: {e}"})
                return
            article = next(
                (a for a in (data.get("articles") or []) if a.get("id") == article_id),
                None,
            )
            if not article:
                self._respond(404, {"ok": False, "error": "해당 article_id를 채널톡에서 찾을 수 없어요"})
                return

            original_title = article.get("title") or article.get("name") or ""
            original_subtitle = article.get("subtitle") or article.get("summary") or ""
            original_html = article.get("bodyHtml") or ""
            original_text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", original_html, flags=re.DOTALL | re.IGNORECASE)
            original_text = re.sub(r"<br\s*/?>", "\n", original_text)
            original_text = re.sub(r"</p>", "\n\n", original_text)
            original_text = re.sub(r"</li>", "\n", original_text)
            original_text = re.sub(r"<[^>]+>", " ", original_text)
            original_text = re.sub(r"&nbsp;", " ", original_text).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            original_text = re.sub(r"[ \t]+", " ", original_text)
            original_text = re.sub(r"\n{3,}", "\n\n", original_text).strip()

            # 매핑 로드 (repo 안 migration/lk2_mapping.json)
            mapping_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "migration", "lk2_mapping.json"
            )
            try:
                with open(mapping_path, "r", encoding="utf-8") as f:
                    mapping_data = json.loads(f.read())
            except Exception as e:
                print(f"[migrate] lk2_mapping.json 로드 실패: {e}")
                self._respond(500, {"ok": False, "error": "마이그레이션 매핑 파일을 로드하지 못했어요. 백엔드 배포를 확인해주세요."})
                return

            # 운영 가이드 2.0 reference (있으면 prompt에 첨부 — 매니저 톤·구조 모방용)
            op_guide_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "migration", "_operation_guide_v2.md"
            )
            op_guide_text = ""
            try:
                with open(op_guide_path, "r", encoding="utf-8") as f:
                    op_guide_text = f.read()
            except Exception as e:
                print(f"[migrate] _operation_guide_v2.md 로드 실패 (있어도 없어도 동작): {e}")

            approved_mappings = []
            for domain_slug, info in (mapping_data.get("domains") or {}).items():
                for m in info.get("mappings", []):
                    if m.get("status") == "approved":
                        approved_mappings.append({
                            "old": m.get("old", ""),
                            "new": m.get("new", ""),
                            "category": m.get("category", ""),
                            "note": m.get("note", ""),
                            "domain": domain_slug,
                        })

            # LLM 변환 prompt — 매핑만 reference. spec/glossary 통째 주입은 토큰 부담 큼.
            mapping_block = "\n".join(
                f"- [{m['category']} · {m['domain']}] '{m['old']}' → '{m['new']}'"
                + (f"\n  · note: {m['note']}" if m.get("note") else "")
                for m in approved_mappings
            )

            op_guide_section = ""
            if op_guide_text:
                op_guide_section = f"""

## LK2.0 운영 가이드 발췌 (매니저 톤·구조·메뉴 경로 표준 reference)
원본 노션 운영 가이드 2.0의 핵심 발췌. 변환된 본문이 이 톤·구조·표현과 일관되도록 모방하세요.
\"\"\"
{op_guide_text[:18000]}
\"\"\"
"""

            migrate_prompt = f"""당신은 라이브클래스 1.0 → 2.0 도큐먼트 마이그레이션 전문가입니다.

## 작업
아래 LK1.0 ALF 도큐먼트 본문을 LK2.0 버전으로 정확하게 변환하세요. 매핑표(아래)와 운영 가이드를 reference로 사용. 매핑표에 없는 추측 변환 금지.

## LK1.0 → LK2.0 매핑표 (사용자 검토 완료, status=approved {len(approved_mappings)}개)
{mapping_block}{op_guide_section}

## 변환 규칙
1. **매핑표에 명시된 LK1.0 표현이 본문에 등장하면 LK2.0 표현으로 치환**. 단순 문자 치환 X — 컨텍스트 보고 자연스럽게.
2. **매핑 note 준수**. 예: "클래스 → 상품" 매핑의 note에 "단 '강의' 상품 타입 안의 콘텐츠 안내에서는 '강의' 그대로 유지"가 있으면 그 분기 따름.
3. **매핑표에 없는 LK1.0 추정 표현은 건드리지 않음**. 원본 유지.
4. **마크다운 형식 유지**. 헤딩(##), 불릿(-), 번호 목록(1.), 메뉴 경로 화살표(>) 등 본문 구조 보존.
5. **친근한 종결형(~어요/~합니다) 유지**. 본문 톤 변경 X.
6. 본문에 LK2.0 spec과 모순되는 정책이 있으면 변환하지 말고 warnings에 명시.

## 원본 도큐먼트
- 제목: {original_title}
- 소제목: {original_subtitle}
- 본문:
\"\"\"
{original_text[:8000]}
\"\"\"

## 출력 형식 — 반드시 아래 JSON으로만
```json
{{
  "migrated_title": "변환된 제목 (변환 없으면 원본 그대로)",
  "migrated_subtitle": "변환된 소제목 (변환 없으면 원본 그대로)",
  "migrated_markdown": "변환된 본문 마크다운 (원본의 마크다운 구조 보존, # H1 본문 안 금지)",
  "applied_mappings": [
    {{"old": "발견된 LK1.0 표현", "new": "변환한 LK2.0 표현", "category": "용어|메뉴 경로|기능명|정책", "note": "변환 사유 한 줄"}}
  ],
  "warnings": ["변환 망설인 부분이 있다면 한 줄씩"]
}}
```

JSON 외 다른 텍스트 출력 금지."""
            try:
                raw = call_anthropic(
                    migrate_prompt, max_tokens=4096, api_key=OPENAI_API_KEY, json_mode=True,
                )
                parsed = extract_json(raw)
                if not isinstance(parsed, dict):
                    self._respond(500, {"ok": False, "error": "마이그레이션 결과 파싱 실패"})
                    return
                migrated_md = (parsed.get("migrated_markdown") or "").strip()
                migrated_md = strip_article_boilerplate(migrated_md)
                self._respond(200, {
                    "ok": True,
                    "article_id": article_id,
                    "original_title": original_title,
                    "original_subtitle": original_subtitle,
                    "original_markdown": original_text,
                    "original_html": original_html,
                    "migrated_title": (parsed.get("migrated_title") or original_title).strip(),
                    "migrated_subtitle": (parsed.get("migrated_subtitle") or original_subtitle).strip(),
                    "migrated_markdown": migrated_md,
                    "applied_mappings": parsed.get("applied_mappings") or [],
                    "warnings": parsed.get("warnings") or [],
                    "approved_mapping_count": len(approved_mappings),
                })
                return
            except Exception as e:
                print(f"[migrate] LLM 변환 실패: {e}")
                self._respond(500, {"ok": False, "error": f"마이그레이션 변환에 실패했어요: {e}"})
                return

        # ─── LK2.0 매핑·신설 기능 데이터 fetch ─────────────────────────────
        # 프론트엔드의 "LK 2.0 변경 가이드" 탭이 매핑표·신설 기능 list를 한 번에 받음.
        if mode == "migration_data":
            mapping_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "migration", "lk2_mapping.json"
            )
            features_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "migration", "lk2_new_features.json"
            )
            mapping_data: dict = {}
            features_data: dict = {}
            try:
                with open(mapping_path, "r", encoding="utf-8") as f:
                    mapping_data = json.loads(f.read())
            except Exception as e:
                print(f"[migration_data] mapping 로드 실패: {e}")
            try:
                with open(features_path, "r", encoding="utf-8") as f:
                    features_data = json.loads(f.read())
            except Exception:
                features_data = {"new_features": [], "stats": {}}
            self._respond(200, {
                "ok": True,
                "mapping": mapping_data,
                "new_features": features_data,
                "spaces": list_docs_spaces(),
            })
            return

        # ─── 이미지 추천 위치 분석 ────────────────────────────────────────
        # 본문을 LLM에 보내서 "스크린샷·이미지가 필요한 위치"를 분석 요청.
        # 결과는 [{position, reason, section}] 형식. chat_ids 불필요.
        if mode == "image_suggest":
            user_content = (body.get("content") or "").strip()
            if not user_content:
                self._respond(400, {"ok": False, "error": "content 필요"})
                return
            if len(user_content) > 6000:
                user_content = user_content[:6000]
            image_prompt = f"""다음은 채널톡 ALF 지식 아티클의 본문이에요. 이 본문에 **스크린샷·이미지가 함께 있으면 고객 이해가 훨씬 쉬워질 위치**를 찾아주세요.

【본문】
\"\"\"
{user_content}
\"\"\"

【이미지가 필요한 전형적인 위치】
- 메뉴 경로 안내 (예: "사이트 관리 > 매출 및 정산 관리" — 이 메뉴가 어디 있는지 시각적 표시)
- 버튼·UI 위치 안내 ("○○ 버튼을 눌러주세요" — 어디에 있는지 화면 캡처)
- 단계별 절차 (1./2./3.로 분리된 작업 — 각 단계의 결과 화면)
- 옵션·설정 화면 ("○○를 체크하세요" — 실제 체크박스 위치)
- 결과 확인 ("이런 화면이 나오면 성공이에요" — 정상 결과 예시)

【이미지가 불필요한 곳】
- 정의/개념 설명 (텍스트만으로 충분)
- 정책·약관·면책 문구
- FAQ 짧은 답변

본문에서 위 기준에 해당하는 위치를 최대 5개 찾아서 반환하세요. 본문이 짧거나 이미지가 필요 없으면 빈 배열 OK.

반드시 아래 JSON 형식으로만 답변하세요 (다른 텍스트 금지):
{{
  "suggestions": [
    {{
      "section": "본문의 어느 H2 헤딩 아래 위치인지 (예: '## 부가세 신고 자료는 어디서 확인할 수 있나요?')",
      "position": "그 섹션 안에서 정확히 어떤 문장·항목 뒤에 이미지가 들어가면 좋은지 (예: '사이트 관리 > 매출 및 정산 관리 > 부가세 신고 자료 메뉴 경로 안내 뒤')",
      "reason": "왜 이 위치에 이미지가 필요한지 한 줄 (예: '메뉴 깊이가 3단계라 글로만 찾기 어려움')",
      "type": "screenshot | diagram | example_result 중 하나"
    }}
  ]
}}"""
            try:
                raw = call_anthropic(
                    image_prompt, max_tokens=900, api_key=OPENAI_API_KEY,
                )
                parsed = extract_json(raw)
                suggestions = []
                if isinstance(parsed, dict):
                    suggestions = parsed.get("suggestions") or []
                # suggestions 정리: dict만 + 필수 키만 추출
                cleaned = []
                for s in suggestions[:5]:
                    if not isinstance(s, dict):
                        continue
                    cleaned.append({
                        "section": (s.get("section") or "").strip(),
                        "position": (s.get("position") or "").strip(),
                        "reason": (s.get("reason") or "").strip(),
                        "type": (s.get("type") or "screenshot").strip(),
                    })
                self._respond(200, {"ok": True, "suggestions": cleaned})
                return
            except Exception as e:
                print(f"[alf_generate image_suggest] 실패: {e}")
                self._respond(500, {"ok": False, "error": "이미지 추천 분석에 실패했어요. 잠시 후 다시 시도해주세요."})
                return

        # ─── 제목·소제목 추천 모드 ─────────────────────────────────────────
        # 사용자가 직접 작성한 본문을 받아 LLM이 제목·소제목 추천만 반환.
        # chat_ids 불필요. ALF Studio 검증 탭에서 사용.
        if mode == "title_suggest":
            user_content = (body.get("content") or "").strip()
            if not user_content:
                self._respond(400, {"ok": False, "error": "content 필요"})
                return
            if len(user_content) > 5000:
                user_content = user_content[:5000]
            suggest_prompt = f"""다음은 채널톡 ALF 지식 아티클의 본문이에요. 본문 내용에 가장 어울리는 제목과 소제목을 추천해주세요.

【제목 규칙】
- 핵심 키워드(상품명·기능명·서비스명) 반드시 포함
- 동사 형태 권장 (예: "구독 플랜 환불받는 방법", "비메오 영상 업로드하는 방법")
- 마케팅·과장 표현 금지 ("요즘 핫한", "강력 추천" 등)
- "○○ 안내", "○○ 관련" 류는 피하고 구체적 동사로 표현
- 50자 이내

【소제목 규칙】
- 본문 핵심을 한 줄로 요약
- 제목에 없는 추가 키워드 포함 권장 (검색 매칭 ↑)
- 50자 이내

【본문】
\"\"\"
{user_content}
\"\"\"

반드시 아래 JSON 형식으로만 답변하세요 (다른 텍스트·설명 금지):
{{
  "title": "추천 제목",
  "subtitle": "추천 소제목",
  "alternatives": ["대체 제목 1", "대체 제목 2"]
}}"""
            if body.get("prompt_only"):
                self._respond(200, {
                    "ok": True, "prompt_only": True, "prompt": suggest_prompt,
                    "expected_schema": {"title": "str", "subtitle": "str", "alternatives": "list[str]"},
                })
                return
            try:
                raw = call_anthropic(
                    suggest_prompt, max_tokens=400, api_key=OPENAI_API_KEY,
                )
                parsed = extract_json(raw)
                if isinstance(parsed, dict):
                    self._respond(200, {
                        "ok": True,
                        "title": (parsed.get("title") or "").strip(),
                        "subtitle": (parsed.get("subtitle") or "").strip(),
                        "alternatives": parsed.get("alternatives") or [],
                    })
                    return
                self._respond(500, {"ok": False, "error": "추천 결과 파싱 실패"})
                return
            except Exception as e:
                print(f"[alf_generate title_suggest] 실패: {e}")
                self._respond(500, {"ok": False, "error": "제목 추천에 실패했어요. 잠시 후 다시 시도해주세요."})
                return

        # ─── 본문 재작성 모드 ────────────────────────────────────────────
        # 사용자가 작성한 본문을 채널톡 ALF 가이드에 맞게 LLM이 재작성.
        # warnings(검증기 결과)도 함께 전달하면 LLM이 우선 해결.
        if mode == "rewrite":
            user_content = (body.get("content") or "").strip()
            user_warnings = body.get("warnings") or []
            user_title = (body.get("title") or "").strip()
            if not user_content:
                self._respond(400, {"ok": False, "error": "content 필요"})
                return
            if len(user_content) > 6000:
                user_content = user_content[:6000]

            warnings_section = ""
            if isinstance(user_warnings, list) and user_warnings:
                lines = []
                for w in user_warnings[:30]:
                    if isinstance(w, dict):
                        rule = (w.get("rule") or "").strip()
                        msg = (w.get("message") or "").strip()
                        if rule or msg:
                            lines.append(f"- [{rule}] {msg}")
                if lines:
                    warnings_section = (
                        "\n\n【검증기가 감지한 문제점 — 우선 해결】\n" + "\n".join(lines)
                    )
            title_hint = f"\n\n【현재 제목】 {user_title}" if user_title else ""

            rewrite_prompt = f"""다음은 사용자가 직접 작성한 채널톡 ALF 지식 아티클입니다. 위 시스템 프롬프트의 **모든 채널톡 ALF 가이드라인**에 맞게 재작성해주세요.

【원본 본문】
\"\"\"
{user_content}
\"\"\"{title_hint}{warnings_section}

【재작성 규칙 — 반드시 모두 적용】

1. **마크다운 구조 강제 적용 (가장 중요)**
   - 본문은 **반드시** `## 헤딩`으로 시작하고 케이스/주제별로 `## 헤딩`을 사용해 분리. 평문으로만 작성 금지.
   - `## ` 뒤 공백 1칸 필수. `##헤딩` ❌ → `## 헤딩` ✅
   - 본문 안에 H1(`#`)은 절대 사용 금지 (제목은 별도 필드)
   - 원본이 평문 텍스트라도 적극적으로 ## 헤딩 추가. 질문 형태("○○이란?", "○○ 어떻게?", "○○ 언제?")는 그 자체로 ## 헤딩으로 승격
   - 단계·일정·조건·항목은 **반드시** 불릿(`- `) 또는 번호 목록(`1. `)으로 변환. 평문 나열 금지.
   - 강조는 `**굵게**` 사용

2. **사실 정보 보존**
   - 원본의 **사실 정보·메뉴 경로·정책·일정·수치·URL**은 그대로 유지
   - 새로운 정책·절차·일정·수치는 절대 생성·추측 금지

3. **톤·표현 수정**
   - 친근한 `-어요/-습니다` 종결형으로 변환 (단호한 명사형 종결 ❌)
   - 이모지·마케팅 표현·인사·자기소개·1:1 응답·모호 표현("보통", "일반적으로") 모두 제거
   - 특정 날짜("4월 15일") → 일반 조건("분기 종료 후 약 15일 경")로 변환

4. **검증기 문제점이 있다면 우선 해결**

【예시】 평문 입력을 마크다운으로 변환할 때:
- 원본: "부가세 신고 자료란? 부가세 신고 자료는 매출 신고 시 참고하실 수 있는 자료입니다."
- 변환: "## 부가세 신고 자료란?\\n\\n부가세 신고 자료는 매출 신고 시 참고할 수 있는 자료예요."

- 원본: "1분기(1~3월) : 4월 15일 경 2분기(4~6월) : 7월 15일 경"
- 변환: "- 1분기(1~3월): 분기 종료 후 약 15일 경\\n- 2분기(4~6월): 분기 종료 후 약 15일 경"

반드시 아래 JSON 형식으로만 답변하세요 (다른 텍스트·설명 금지):
{{
  "content": "재작성된 본문 마크다운 (반드시 ## 헤딩부터 시작, H1 금지, 케이스별 ## 분리)",
  "title": "추천 제목 (50자 이내, 핵심 키워드 + 동사)",
  "subtitle": "추천 소제목 (50자 이내, 본문 한 줄 요약)",
  "changes": ["주요 변경 사항 1", "주요 변경 사항 2", "..."]
}}"""
            if body.get("prompt_only"):
                # 수동 모드: 시스템 프롬프트를 사용자 프롬프트 위에 합쳐서 한 번에 복붙 가능하게
                combined_prompt = ALF_SYSTEM_PROMPT + "\n\n" + rewrite_prompt
                self._respond(200, {
                    "ok": True, "prompt_only": True, "prompt": combined_prompt,
                    "expected_schema": {"content": "str", "title": "str", "subtitle": "str", "changes": "list[str]"},
                })
                return
            try:
                raw = call_anthropic(
                    rewrite_prompt, system=ALF_SYSTEM_PROMPT,
                    max_tokens=2500, api_key=OPENAI_API_KEY,
                )
                parsed = extract_json(raw)
                if not isinstance(parsed, dict):
                    self._respond(500, {"ok": False, "error": "재작성 결과 파싱 실패"})
                    return
                revised = (parsed.get("content") or "").strip()
                revised = strip_article_boilerplate(revised)
                verification = verify_draft(revised, "article",
                                            cluster_label="",
                                            title=(parsed.get("title") or user_title))
                self._respond(200, {
                    "ok": True,
                    "content": verification["fixed"],
                    "title": (parsed.get("title") or "").strip(),
                    "subtitle": (parsed.get("subtitle") or "").strip(),
                    "changes": parsed.get("changes") or [],
                    "warnings": verification["warnings"],
                })
                return
            except Exception as e:
                print(f"[alf_generate rewrite] 실패: {e}")
                self._respond(500, {"ok": False, "error": "재작성에 실패했어요. 잠시 후 다시 시도해주세요."})
                return

        # ─── 고객 인입 패턴 리서치 모드 ────────────────────────────────────────
        # 키워드/태그/아티클 본문 중 하나 이상으로 관련 채팅을 조회한 뒤
        # 워크플로 버튼·봇 멘트를 제외한 첫 고객 자연어 멘트만 모음.
        # LLM에게 패턴 요약 + 추천 헤딩 생성 요청.
        if mode == "customer_patterns":
            import re as _re_cp
            keywords = body.get("keywords") or []
            tags = body.get("tags") or []
            article_content = (body.get("content") or "").strip()
            try:
                limit = min(int(body.get("limit") or 200), 500)
            except (TypeError, ValueError):
                limit = 200

            if not keywords and not tags and not article_content:
                self._respond(400, {"ok": False, "error": "keywords, tags, content 중 최소 하나는 필요해요."})
                return

            # article_content만 있으면 LLM이 키워드 추출
            if article_content and not keywords and not tags:
                kw_prompt = (
                    "다음은 채널톡 ALF 지식 아티클의 본문이에요. 이 아티클이 다루는 주제와 관련된 "
                    "채널톡 상담 태그 후보 또는 한국어 키워드를 3~5개 뽑아주세요. 슬래시 계층 태그가 있다면 우선.\n\n"
                    "【본문】\n" + article_content[:2000] + "\n\n"
                    "반드시 아래 JSON 형식으로만 답변:\n"
                    '{"keywords": ["키워드1", "키워드2"]}'
                )
                try:
                    raw = call_anthropic(kw_prompt, max_tokens=200, api_key=OPENAI_API_KEY)
                    parsed_kw = extract_json(raw)
                    if isinstance(parsed_kw, dict):
                        keywords = parsed_kw.get("keywords") or []
                except Exception as e:
                    print(f"[customer_patterns] keyword extract 실패: {e}")

            # 태그: PostgREST cs OR 조합으로 후보 채팅 fetch
            # 키워드: messages 본문에 대한 ilike 필터 (PostgREST가 jsonb에 ilike 미지원이라 백엔드 post-filter)
            # chat_ids: 특정 클러스터의 채팅만 한정해서 분석 (워크플로우 STEP 2 세부 유형 선택 결과)
            import urllib.parse as _up_cp
            safe_tags = [t for t in tags if is_safe_postgrest_tag(t)]
            chat_ids_filter = body.get("chat_ids") or []

            chats = []
            if chat_ids_filter:
                # 클러스터의 chat_ids로만 조회 (태그·키워드 무시)
                ids_quoted = ",".join('"' + str(c).replace('"', '') + '"' for c in chat_ids_filter[:300])
                url = (
                    f"{SUPABASE_URL}/rest/v1/cx_full_messages"
                    f"?chat_id=in.({ids_quoted})&limit={limit}"
                    f"&select=chat_id,tags,messages,date&order=date.desc"
                )
                try:
                    chats = supabase_get(url, SUPABASE_SERVICE_KEY)
                except Exception as e:
                    print(f"[customer_patterns] chat_ids fetch 실패: {e}")
            elif safe_tags:
                quoted_terms = [_up_cp.quote('tags.cs.{"' + t + '"}', safe='') for t in safe_tags[:8]]
                or_clauses = ",".join(quoted_terms)
                url = (
                    f"{SUPABASE_URL}/rest/v1/cx_full_messages"
                    f"?or=({or_clauses})&limit={limit}"
                    f"&select=chat_id,tags,messages,date&order=date.desc"
                )
                try:
                    chats = supabase_get(url, SUPABASE_SERVICE_KEY)
                except Exception as e:
                    print(f"[customer_patterns] tag query 실패: {e}")
            elif keywords:
                # 태그 없이 키워드만 → 최근 채팅 N건을 가져와서 client-side 필터
                url = (
                    f"{SUPABASE_URL}/rest/v1/cx_full_messages"
                    f"?limit={min(limit * 3, 1000)}"
                    f"&select=chat_id,tags,messages,date&order=date.desc"
                )
                try:
                    chats = supabase_get(url, SUPABASE_SERVICE_KEY)
                except Exception as e:
                    print(f"[customer_patterns] recent fetch 실패: {e}")

            # 키워드 post-filter: 메시지 텍스트(고객·매니저 포함)에 키워드 중 하나라도 들어가면 매칭
            if keywords and chats:
                kw_lower = [k.lower() for k in keywords if k]
                def _chat_has_keyword(chat):
                    msgs = chat.get("messages") or []
                    for m in msgs:
                        t = (m.get("text") or "").lower()
                        if any(k in t for k in kw_lower):
                            return True
                    return False
                chats = [c for c in chats if _chat_has_keyword(c)]
                chats = chats[:limit]

            if not chats:
                self._respond(200, {
                    "ok": True, "samples": [], "patterns": [], "suggested_headings": [],
                    "keywords_used": keywords, "tags_used": tags, "total_chats": 0,
                    "warning": "관련 채팅을 찾지 못했어요. 다른 키워드/태그로 시도해보세요.",
                })
                return

            # 첫 비-워크플로 고객 메시지 추출
            samples = []
            for chat in chats:
                msgs = chat.get("messages") or []
                cust_msgs = [m for m in msgs if m.get("role") == "customer"]
                for m in cust_msgs:
                    t = (m.get("text") or "").strip()
                    if not t:
                        continue
                    if len(t) < 50:
                        # 한글/영문/숫자가 아닌 문자(이모지 등)로 시작 → 워크플로 버튼으로 간주
                        if _re_cp.match(r"^[^\w가-힣]", t):
                            continue
                        if t in ("플랜 변경", "플랜 상담", "FAQ", "문의", "도움말", "상담사 연결"):
                            continue
                    samples.append({
                        "chat_id": chat.get("chat_id"),
                        "tags": chat.get("tags") or [],
                        "text": t[:400],
                    })
                    break

            sample_texts = [s["text"] for s in samples[:20]]
            if not sample_texts:
                self._respond(200, {
                    "ok": True, "samples": samples, "patterns": [], "suggested_headings": [],
                    "keywords_used": keywords, "tags_used": tags, "total_chats": len(chats),
                    "warning": "워크플로 버튼만 있고 실제 자연어 멘트가 없어요.",
                })
                return

            sample_joined = "\n".join("- " + t for t in sample_texts)
            _cp_context = {
                "keywords_used": keywords,
                "tags_used": tags,
                "total_chats": len(chats),
                "samples": samples[:50],
            }
            context_label_parts = []
            if tags:
                context_label_parts.append("태그: " + ", ".join(tags))
            if keywords:
                context_label_parts.append("키워드: " + ", ".join(keywords))
            context_label = " / ".join(context_label_parts) if context_label_parts else "(없음)"
            pattern_prompt = (
                "다음은 라이브클래스 고객들이 채널톡에 직접 작성한 실제 첫 인입 멘트들이에요. "
                "이 데이터는 다음 검색 조건으로 모았어요: " + context_label + "\n\n"
                "이 검색 조건의 주제에 직접 관련된 표현·키워드 패턴만 정리하고, 채널톡 ALF가 검색·매칭하기 좋은 "
                "아티클 헤딩(질문형, ## 마크다운)을 추천해주세요. 검색 조건과 무관한 일반적 패턴(예: 상담사 연결, 문의 인사)은 빼주세요.\n\n"
                "【고객 실제 멘트】\n" + sample_joined + "\n\n"
                "【작성 가이드】\n"
                "- 헤딩은 고객이 실제로 쓰는 자연어 표현을 살리되, 채널톡 가이드 친근체(-어요, -하나요?)로 마무리\n"
                "- '업그레이드/다운그레이드' 같은 내부 용어는 노출 금지. '더 큰 플랜/무료 플랜으로 변경' 같이 풀어 쓰기\n"
                "- 검색 조건 주제에 직접 맞닿은 헤딩만 5~8개 추천 (관련성 낮으면 적게)\n"
                "- 너무 짧은 헤딩(5자 이하) 또는 너무 광범위한 헤딩 금지\n\n"
                "반드시 아래 JSON 형식으로만 답변 (다른 텍스트 금지):\n"
                '{\n'
                '  "patterns": [\n'
                '    {"phrase": "공통 표현", "count_hint": "약 N건", "note": "어떤 의도에서 자주 나오는지"}\n'
                '  ],\n'
                '  "suggested_headings": ["## 헤딩 예시 1?", "## 헤딩 예시 2?"]\n'
                '}'
            )
            if body.get("prompt_only"):
                self._respond(200, {
                    "ok": True, "prompt_only": True, "prompt": pattern_prompt,
                    "context": _cp_context,
                    "expected_schema": {"patterns": "list", "suggested_headings": "list[str]"},
                })
                return
            try:
                raw = call_anthropic(pattern_prompt, max_tokens=1500, api_key=OPENAI_API_KEY)
                parsed = extract_json(raw)
                if not isinstance(parsed, dict):
                    parsed = {}
                self._respond(200, {
                    "ok": True,
                    "keywords_used": keywords,
                    "tags_used": tags,
                    "total_chats": len(chats),
                    "samples": samples[:50],
                    "patterns": parsed.get("patterns") or [],
                    "suggested_headings": parsed.get("suggested_headings") or [],
                })
                return
            except Exception as e:
                print(f"[customer_patterns] LLM 실패: {e}")
                self._respond(500, {"ok": False, "error": "패턴 분석에 실패했어요. 잠시 후 다시 시도해주세요."})
                return

        cluster_label = (body.get("cluster_label") or "").strip()
        chat_ids = body.get("chat_ids", [])
        single_chat = bool(body.get("single_chat", False))
        similar_search = bool(body.get("similar_search", False))
        reference_doc = (body.get("reference_doc") or "").strip()
        reference_url = (body.get("reference_url") or "").strip()
        reference_fetch_failed = False
        if reference_url and not reference_doc:
            reference_doc = fetch_reference_url(reference_url)
            if not reference_doc:
                reference_fetch_failed = True

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
                from alf_collect import fetch_messages_for_chat, fetch_chat_detail, parse_messages, fetch_all_managers
                mgr_map = fetch_all_managers()
                for cid in missing_ids[:20]:
                    try:
                        raw_msgs = fetch_messages_for_chat(cid)
                        # chat detail에서 tags 함께 가져오기 (similar_search·태그 화이트리스트용)
                        chat_tags: list = []
                        try:
                            chat_tags = fetch_chat_detail(cid).get("tags") or []
                        except Exception as te:
                            print(f"[warn] 채널톡 chat detail fetch 실패 {cid}: {te}")
                        chats.append({
                            "chat_id": cid,
                            "messages": parse_messages(raw_msgs, mgr_map),
                            "tags": chat_tags,
                        })
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
            chosen_tag = select_specific_tag(origin_tags)
            if chosen_tag:
                safe_tag = chosen_tag.replace('\\', '\\\\').replace('"', '\\"')
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
        if prompt is None:
            self._respond(400, {"ok": False,
                                "error": "매니저 답변이 없거나 처리할 수 없어요. 다른 상담을 선택해주세요."})
            return
        if body.get("prompt_only"):
            combined_prompt = ALF_SYSTEM_PROMPT + "\n\n" + prompt
            self._respond(200, {
                "ok": True, "prompt_only": True, "prompt": combined_prompt,
                "expected_schema": {"title": "str", "subtitle": "str", "content": "str"},
                "context": {"cluster_label": cluster_label, "analyzed_chat_count": len(chats)},
            })
            return
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

        # fallback: JSON 파싱 실패 시 raw에서 H1 추출 + 코드펜스 strip
        if not draft_content:
            cleaned = raw
            # ```json ... ``` 또는 ``` ... ``` 코드펜스 제거
            fence_match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", cleaned)
            if fence_match:
                cleaned = fence_match.group(1).strip()
            draft_content = cleaned
            for line in cleaned.split("\n"):
                if line.startswith("# "):
                    title = line[2:].strip()
                    break

        # 인사·자기소개·마무리 인사 제거 + 자동 검증
        draft_content = strip_article_boilerplate(draft_content)
        verification = verify_draft(draft_content, "article", cluster_label, title=title)
        draft_content = verification["fixed"]

        # 참고 가이드 fetch 실패 알림 — LLM이 가이드 없이 생성됐다는 사실을 사용자에게 명시
        if reference_fetch_failed:
            verification["warnings"].insert(0, {
                "rule": "참고 가이드",
                "level": "warning",
                "message": "첨부한 URL을 가져오지 못했어요 — 가이드 없이 생성됐어요. URL이 공개 페이지인지 확인하거나, 본문을 직접 붙여넣어 다시 시도해주세요.",
            })

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
