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


def filter_by_keyword(chats: list, keyword: str) -> list:
    """공백 구분 키워드들이 모두 어딘가에 포함된 채팅만 반환 (AND 매칭)"""
    if not keyword:
        return chats
    tokens = [t.strip().lower() for t in keyword.split() if t.strip()]
    if not tokens:
        return chats
    result = []
    for c in chats:
        full_text = " ".join(
            m.get("text", "").lower() for m in c.get("messages", [])
        )
        if all(tok in full_text for tok in tokens):
            result.append(c)
    return result


CLUSTER_SAMPLE_LIMIT = 50


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

        if not tag:
            self._respond(400, {"ok": False, "error": "tag 필요"})
            return

        # Supabase에서 태그 매칭 채팅 조회
        # PostgreSQL 배열 리터럴 안에서 "는 \\"로, \는 \\\\로 이스케이프
        safe_tag = tag.replace('\\', '\\\\').replace('"', '\\"')
        encoded_tag = urllib.parse.quote(f'{{"{safe_tag}"}}', safe='')
        url = (f"{SUPABASE_URL}/rest/v1/cx_full_messages"
               f"?select=chat_id,messages,tags,date,assignee_name,csat_score"
               f"&tags=cs.{encoded_tag}&order=date.desc&limit=200")
        chats = supabase_get(url, SUPABASE_SERVICE_KEY)

        # 키워드 필터링
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
