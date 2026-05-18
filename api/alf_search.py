from __future__ import annotations
import os
import json
import urllib.request
import urllib.parse

from _alf_common import call_anthropic, supabase_get, make_handler_base

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")


def filter_by_keyword(chats: list, keyword: str) -> list:
    """키워드가 메시지 텍스트에 포함된 채팅만 반환"""
    if not keyword:
        return chats
    kw = keyword.lower()
    return [
        c for c in chats
        if any(kw in m.get("text", "").lower() for m in c.get("messages", []))
    ]


def build_cluster_prompt(chats: list, tag: str) -> str:
    """클러스터링용 Claude 프롬프트 생성"""
    samples = []
    for i, c in enumerate(chats[:40]):  # 최대 40건만 샘플로 사용
        msgs = c.get("messages", [])[:6]  # 건당 최대 6개 메시지
        text = "\n".join(f"[{m.get('role','?')}] {m.get('text','')}" for m in msgs)
        samples.append(f"=== 상담 {i+1} ===\n{text}")

    return f"""아래는 '{tag}' 관련 실제 고객 상담 {len(chats)}건의 샘플입니다.

{chr(10).join(samples)}

위 상담들을 고객이 처한 **구체적인 상황**을 기준으로 2~5개 그룹으로 분류해주세요.
각 그룹은 ALF 지식 아티클 1개로 만들 수 있는 단위여야 합니다.

반드시 아래 JSON 형식으로만 답변하세요:
{{
  "clusters": [
    {{"label": "그룹명 (구체적으로)", "count": 숫자, "description": "이 그룹에 해당하는 고객 상황 한 줄 설명"}},
    ...
  ]
}}"""


_Base = make_handler_base()


class handler(_Base):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}
        tag = body.get("tag", "").strip()
        keyword = body.get("keyword", "").strip()

        if not tag:
            self._respond(400, {"ok": False, "error": "tag 필요"})
            return

        # Supabase에서 태그 매칭 채팅 조회
        encoded_tag = urllib.parse.quote(f'{{"{tag}"}}')
        url = (f"{SUPABASE_URL}/rest/v1/cx_full_messages"
               f"?select=chat_id,messages,tags,date,assignee_name,csat_score"
               f"&tags=cs.{encoded_tag}&limit=200")
        chats = supabase_get(url, SUPABASE_SERVICE_KEY)

        # 키워드 필터링
        filtered = filter_by_keyword(chats, keyword)
        if not filtered:
            self._respond(200, {"ok": True, "total": 0, "clusters": []})
            return

        # Claude로 클러스터링
        prompt = build_cluster_prompt(filtered, tag)
        raw = call_anthropic(prompt, max_tokens=1024, api_key=GROQ_API_KEY)

        try:
            text = raw.strip()
            if "```" in text:
                text = text.split("```")[1].lstrip("json").strip()
            cluster_data = json.loads(text)
            clusters = cluster_data.get("clusters", [])
        except Exception:
            clusters = [{"label": "전체", "count": len(filtered), "description": "분류 실패 — 전체 포함"}]

        # 클러스터에 chat_ids 배분
        n = len(filtered)
        for cl in clusters:
            cl["chat_ids"] = [c["chat_id"] for c in filtered[:min(cl.get("count", 10), n)]]

        self._respond(200, {"ok": True, "total": len(filtered), "clusters": clusters})
