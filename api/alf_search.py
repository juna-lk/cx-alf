from __future__ import annotations
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json
import urllib.request
import urllib.parse

from _alf_common import call_anthropic, supabase_get, extract_json, make_handler_base

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")


import re

# 한국어 조사·어미 패턴
_JOSA_END = re.compile(r"(은는|이가|을를|에서|로|으로|에|의|와|과|도|만|보다|까지|부터|에게|한테|께|이|가|을|를|로|은|는)$")
_EOMI_END = re.compile(r"(습니다|했어요|었어요|아요|어요|이에요|예요|이요|네요|시오|시요|돼요|되요|이다|하다|했다|되다|입니다)$")
_STOPWORDS = {
    "그리고", "그래서", "근데", "그런데", "또한", "이때", "그때", "지금",
    "혹시", "정말", "진짜", "너무", "그냥", "막", "다시", "또", "이거",
    "저거", "그거", "이게", "저게", "그게", "이런", "저런", "그런",
    "있다", "없다", "되다", "하다", "이다", "같다", "안", "못", "안돼",
    "전혀", "정도", "조금", "약간", "매우", "참고", "관련",
}


def extract_keywords(text: str) -> list[str]:
    """자연어 입력에서 핵심 검색어 추출 (한국어 조사·어미 단순 제거 + 불용어 필터).

    예: "수강생 수강 시도 시 결제 페이지로 이동"
       → ["수강생", "수강", "시도", "결제", "페이지", "이동"]
    """
    # 공백·쉼표·점 등으로 분리
    raw_tokens = re.split(r"[\s,.\?\!·\-/]+", text)
    keywords = []
    seen = set()
    for tok in raw_tokens:
        tok = tok.strip().lower()
        if not tok:
            continue
        # 어미 제거 (2~5글자)
        clean = _EOMI_END.sub("", tok)
        # 조사 제거 (1글자만, 단어 길이 ≥3일 때)
        if len(clean) >= 3:
            clean = _JOSA_END.sub("", clean)
        # 길이 ≥ 2, 불용어 아님, 중복 아님
        if len(clean) >= 2 and clean not in _STOPWORDS and clean not in seen:
            keywords.append(clean)
            seen.add(clean)
    return keywords


def filter_by_keyword(chats: list, keyword: str) -> list:
    """자연어 입력에서 핵심 키워드 추출 후 매칭 (30% 이상 토큰 매칭 시 통과).

    - 단일 단어 입력 시: 그 단어 포함된 상담만
    - 짧은 문장: 모든 토큰 매칭 필요
    - 긴 문장: 토큰 30% 이상 매칭 시 통과 (자연어 검색 허용)
    """
    if not keyword:
        return chats
    keywords = extract_keywords(keyword)
    if not keywords:
        return chats
    # 토큰 수에 따른 임계값
    if len(keywords) <= 2:
        threshold = len(keywords)  # 다 매칭돼야
    else:
        threshold = max(2, int(len(keywords) * 0.4))  # 40% 이상
    result = []
    for c in chats:
        full_text = " ".join(
            m.get("text", "").lower() for m in c.get("messages", [])
        )
        match_count = sum(1 for k in keywords if k in full_text)
        if match_count >= threshold:
            result.append(c)
    return result


CLUSTER_SAMPLE_LIMIT = 50
SEMANTIC_SEARCH_LIMIT = 80  # LLM 의미 검색 후 반환할 최대 건수


def filter_by_semantic(chats: list, query: str) -> list:
    """LLM 의미 검색: 자연어 query와 의미적으로 가까운 상담을 골라 반환.

    실패 시 빈 리스트 대신 keyword 필터로 fallback (handler에서 처리).
    """
    if not query or not chats:
        return chats
    # 200건 첫 customer 메시지만 추출 (token 절약)
    samples = []
    for i, c in enumerate(chats):
        first_customer = next(
            (m.get("text", "")[:120] for m in c.get("messages", []) if m.get("role") == "customer"),
            "",
        )
        if first_customer:
            samples.append(f"[{i}] {first_customer}")
    if not samples:
        return chats

    prompt = f"""사용자가 다음 상황의 CX 상담을 찾고 있습니다:
"{query}"

아래 {len(samples)}건 상담 중 위 상황과 **의미적으로 가장 비슷한** 상담의 인덱스를 골라주세요.
정확히 같은 단어가 아니어도, 같은 시나리오·맥락이면 포함하세요.
관련 없는 상담은 제외하세요. 최대 {SEMANTIC_SEARCH_LIMIT}건까지 가능합니다.

{chr(10).join(samples)}

반드시 아래 JSON 형식으로만 답변:
{{"matched_indices": [0, 5, 12, ...]}}"""

    try:
        raw = call_anthropic(prompt, max_tokens=1024, api_key=OPENAI_API_KEY)
        parsed = extract_json(raw)
        indices = parsed.get("matched_indices", []) if isinstance(parsed, dict) else []
        # 인덱스 정수화·중복 제거·범위 검증 → 원래 인덱스(=date.desc) 순으로 재정렬
        # LLM은 의미 유사도 순으로 반환하므로 그대로 쓰면 date 정렬이 깨짐 → 인덱스 오름차순으로 강제
        seen: set[int] = set()
        valid_indices: list[int] = []
        for idx in indices[:SEMANTIC_SEARCH_LIMIT]:
            try:
                i = int(idx)
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(chats) and i not in seen:
                seen.add(i)
                valid_indices.append(i)
        valid_indices.sort()  # 원래 인덱스 오름차순 = date.desc 유지
        result = [chats[i] for i in valid_indices]
        return result if result else chats  # 매칭 0건이면 전체 반환 (사용자가 결과 보고 판단)
    except Exception:
        return chats  # LLM 실패 시 전체 반환 (handler에서 keyword fallback 가능)


def build_cluster_prompt(chats: list, tag: str) -> tuple[str, list[int]]:
    """클러스터링용 Claude 프롬프트 생성.

    반환: (prompt, sample_indices)
    sample_indices: 프롬프트에 포함된 chats 배열 인덱스 (LLM이 반환하는 chat_indices와 매핑)
    """
    samples = []
    sample_indices = []
    for i, c in enumerate(chats[:CLUSTER_SAMPLE_LIMIT]):
        customer_msgs = [
            m.get("text", "")[:200]
            for m in c.get("messages", [])
            if m.get("role") == "customer"
        ][:3]
        if not customer_msgs:
            continue
        samples.append(f"[상담 {i}] {' / '.join(customer_msgs)}")
        sample_indices.append(i)

    prompt = f"""아래는 '{tag}' 관련 실제 고객 상담 샘플입니다 (총 {len(chats)}건 중 {len(samples)}건 발췌).

{chr(10).join(samples)}

위 상담들을 고객이 처한 **구체적인 상황**을 기준으로 **2~8개 그룹**으로 분류해주세요.
각 그룹은 ALF 지식 아티클 1개로 만들 수 있는 단위여야 합니다.
큰 카테고리로 뭉뚱그리지 말고, 가능하면 **세분화**해서 작은 패턴도 별도 클러스터로 잡아주세요.

**중요**: 각 클러스터에 어떤 상담들이 속하는지 위 "[상담 N]" 번호(N)로 알려주세요.
하나의 상담은 하나의 클러스터에만 속해야 합니다.
주요 패턴에서 벗어난 1~2건의 outlier 상담은 굳이 작은 클러스터로 만들지 말고 분류에서 제외해도 됩니다.

**언어 규칙: 모든 텍스트는 반드시 한국어로만 작성하세요. 일본어/한자/영어 금지.**

반드시 아래 JSON 형식으로만 답변하세요:
{{
  "clusters": [
    {{
      "label": "그룹명 (한국어, 구체적으로)",
      "description": "이 그룹의 고객 상황 한 줄 설명 (한국어)",
      "chat_indices": [0, 3, 7]
    }}
  ]
}}"""
    return prompt, sample_indices


_Base = make_handler_base()


class handler(_Base):
    def do_POST(self):
        if not self._check_auth():
            return
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}
        tag = body.get("tag", "").strip()
        keyword = body.get("keyword", "").strip()
        ai_search = bool(body.get("ai_search", False))

        if not tag:
            self._respond(400, {"ok": False, "error": "tag 필요"})
            return
        from _alf_common import is_safe_postgrest_tag
        if not is_safe_postgrest_tag(tag):
            self._respond(400, {"ok": False, "error": "tag에 허용되지 않는 문자가 있어요 (쉼표·중괄호·제어 문자 불가)"})
            return

        # Supabase에서 태그 매칭 채팅 조회
        # PostgreSQL 배열 리터럴 안에서 "는 \\"로, \는 \\\\로 이스케이프
        safe_tag = tag.replace('\\', '\\\\').replace('"', '\\"')
        encoded_tag = urllib.parse.quote(f'{{"{safe_tag}"}}', safe='')
        url = (f"{SUPABASE_URL}/rest/v1/cx_full_messages"
               f"?select=chat_id,messages,tags,date,assignee_name,csat_score"
               f"&tags=cs.{encoded_tag}&order=date.desc&limit=200")
        chats = supabase_get(url, SUPABASE_SERVICE_KEY)

        # 검색 모드 분기
        if ai_search and keyword:
            filtered = filter_by_semantic(chats, keyword)
        else:
            filtered = filter_by_keyword(chats, keyword)

        if not filtered:
            self._respond(200, {"ok": True, "total": 0, "clusters": []})
            return

        # Claude로 클러스터링
        prompt, _sample_indices = build_cluster_prompt(filtered, tag)
        raw = call_anthropic(prompt, max_tokens=1024, api_key=OPENAI_API_KEY)

        try:
            cluster_data = extract_json(raw)
            clusters = cluster_data.get("clusters", []) if isinstance(cluster_data, dict) else []
        except Exception:
            clusters = []

        # 빈 결과 fallback: 전체 한 그룹으로
        if not clusters:
            clusters = [{
                "label": "전체",
                "description": "분류 실패 — 전체 포함",
                "chat_indices": list(range(min(len(filtered), CLUSTER_SAMPLE_LIMIT))),
            }]

        # chat_indices → chat_ids 매핑 (LLM이 반환한 인덱스를 실제 chat_id로 변환)
        n = len(filtered)
        assigned = set()
        for cl in clusters:
            indices = cl.get("chat_indices", [])
            if not isinstance(indices, list):
                indices = []
            chat_ids = []
            for idx in indices:
                try:
                    i = int(idx)
                except (TypeError, ValueError):
                    continue
                if 0 <= i < n and i not in assigned:
                    chat_ids.append(filtered[i]["chat_id"])
                    assigned.add(i)
            cl["chat_ids"] = chat_ids
            cl["count"] = len(chat_ids)
            cl.pop("chat_indices", None)

        # 어느 클러스터에도 안 들어간 chat은 별도 unclustered로 노출 (사용자 직접 확인용)
        unassigned = [i for i in range(min(n, CLUSTER_SAMPLE_LIMIT)) if i not in assigned]
        unclustered_ids = [filtered[i]["chat_id"] for i in unassigned]

        # 50건 초과 상담도 분석 대상이 아니므로 unclustered에 포함 (사용자가 직접 검토 가능)
        if n > CLUSTER_SAMPLE_LIMIT:
            unclustered_ids.extend(c["chat_id"] for c in filtered[CLUSTER_SAMPLE_LIMIT:])

        self._respond(200, {
            "ok": True,
            "total": len(filtered),
            "clusters": clusters,
            "unclustered_ids": unclustered_ids,
            "unclustered_count": len(unclustered_ids),
        })
