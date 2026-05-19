from __future__ import annotations
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json

from _alf_common import call_anthropic, supabase_get, supabase_post, supabase_delete, make_handler_base, strip_article_boilerplate, extract_json

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

MERGE_WRITE_SYSTEM = """당신은 채널톡 ALF용 지식 아티클을 작성하는 전문가입니다.
여러 문서를 통합할 때 원칙:
1. 1문서 = 고객의 1가지 완결된 시나리오
2. 각 원본 문서의 핵심 내용을 ## 섹션으로 구분
3. 중복 내용은 한 번만 작성
4. 전체 2,000자 이내 유지 (초과 시 핵심만 추려서)
5. Markdown 형식 유지 (본문 안에 H1 `#` 금지 — 제목은 별도 필드)
6. **통합 후 새 제목·소제목 재생성**: 원본 제목을 그대로 합치지 말고, 통합된 내용을 가장 잘 표현하는 새로운 제목과 한 줄 소제목을 만들어야 함
7. **콜아웃(`>`) 사용 규칙**: Tip·주의사항을 콜아웃으로 표시할 수 있으나, 원본 문서에 실제로 있던 내용일 때만 사용. 추측한 일반론은 콜아웃으로 넣지 말 것.

**언어 규칙: 모든 출력은 반드시 한국어로만 작성하세요. 일본어(히라가나/가타카나/한자), 중국어 한자, 영어 단어는 절대 사용하지 마세요. 예) 編集→편집, プラン→플랜, サイト→사이트."""


_Base = make_handler_base()


class handler(_Base):
    def do_POST(self):
        if not self._check_auth():
            return
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}
        draft_ids = body.get("draft_ids", [])

        if len(draft_ids) < 2:
            self._respond(400, {"ok": False, "error": "최소 2개 문서 필요"})
            return

        # 선택된 초안 내용 조회
        ids_param = ",".join(f'"{did}"' for did in draft_ids)
        url = f"{SUPABASE_URL}/rest/v1/alf_drafts?select=id,title,subtitle,content,source_chat_count&id=in.({ids_param})"
        drafts = supabase_get(url, SUPABASE_SERVICE_KEY)

        if len(drafts) < 2:
            self._respond(404, {"ok": False, "error": "문서를 찾을 수 없음"})
            return

        docs_text = "\n\n---\n\n".join(
            f"## 원본 문서: {d['title']}\n소제목: {d.get('subtitle') or '(없음)'}\n\n{d['content']}"
            for d in drafts
        )
        titles = " + ".join(d["title"] for d in drafts)

        prompt = f"""아래 {len(drafts)}개 ALF 지식 문서를 하나의 완결된 아티클로 통합해주세요.

{docs_text}

【출력 형식 — 반드시 JSON으로만 출력】
```json
{{
  "title": "통합 내용을 가장 잘 표현하는 새 제목 (고객이 검색할 표현)",
  "subtitle": "통합 본문 핵심을 한 줄로 요약 (50자 이내)",
  "content": "## 섹션부터 시작하는 통합 본문 마크다운 (본문 안에 H1 # 금지, 중복 제거, 2000자 이내)"
}}
```

원본 제목을 단순 나열하지 말고, 통합 내용에 어울리는 새로운 제목·소제목을 만들 것."""

        raw = call_anthropic(
            prompt, system=MERGE_WRITE_SYSTEM,
            max_tokens=2048, api_key=OPENAI_API_KEY,
        )

        # JSON 응답 파싱 + fallback
        merged_title = titles
        merged_subtitle = ""
        merged_content = ""
        try:
            parsed = extract_json(raw)
            if isinstance(parsed, dict):
                merged_title = (parsed.get("title") or titles).strip()
                merged_subtitle = (parsed.get("subtitle") or "").strip()
                merged_content = (parsed.get("content") or "").strip()
        except Exception:
            pass

        if not merged_content:
            merged_content = raw
            for line in raw.split("\n"):
                if line.startswith("# "):
                    merged_title = line[2:].strip()
                    break

        merged_content = strip_article_boilerplate(merged_content)

        # 새 통합 초안 저장 (병합은 적용 가이드 대상이므로 status=applied)
        new_draft = {
            "title": merged_title,
            "subtitle": merged_subtitle,
            "cluster_label": f"통합: {titles[:50]}",
            "content": merged_content,
            "source_chat_count": sum(d.get("source_chat_count", 0) for d in drafts),
            "status": "applied",
        }
        saved = supabase_post(f"{SUPABASE_URL}/rest/v1/alf_drafts", new_draft, SUPABASE_SERVICE_KEY)
        draft_id = saved[0]["id"] if isinstance(saved, list) and saved else None

        # 병합 성공 시에만 원본 삭제 (병합본이 만들어진 후)
        deleted = 0
        if draft_id:
            for did in draft_ids:
                try:
                    supabase_delete(
                        f"{SUPABASE_URL}/rest/v1/alf_drafts?id=eq.{did}",
                        SUPABASE_SERVICE_KEY,
                    )
                    deleted += 1
                except Exception as e:
                    print(f"[warn] 원본 {did} 삭제 실패: {e}")

        self._respond(200, {
            "ok": True,
            "draft_id": draft_id,
            "title": merged_title,
            "subtitle": merged_subtitle,
            "content": merged_content,
            "deleted_sources": deleted,
        })
