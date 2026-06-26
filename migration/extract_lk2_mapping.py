#!/usr/bin/env python3
"""LK1.0 → LK2.0 ALF 도큐먼트 변경 매핑 자동 추출.

흐름:
  1. 채널톡 Documents API에서 등록된 아티클 전체 fetch (language=ko)
  2. GitHub repo의 LK2.0 spec.md 3개 (product-structure, product-sales-settings,
     payment-settlement) + glossary/terms.md를 reference로 fetch
  3. Claude에 (a) 도큐먼트별 도메인 분류 → (b) 도메인별 매핑 추출 요청
  4. 결과를 migration/lk2_mapping.json에 저장

환경변수:
  - CHANNELTALK_DOCS_ACCESS_KEY / SECRET (cx-alf/.env)
  - ANTHROPIC_API_KEY (셸)
  - GITHUB_TOKEN (선택 — private repo면 필요. gh CLI 인증되어 있으면 자동 사용)

실행:
  cd /Users/juna/cx-alf
  set -a && source .env && set +a
  python3 migration/extract_lk2_mapping.py
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

# ── 환경 ──────────────────────────────────────────────────────────────────────
DOCS_ACCESS_KEY = os.environ.get("CHANNELTALK_DOCS_ACCESS_KEY", "")
DOCS_ACCESS_SECRET = os.environ.get("CHANNELTALK_DOCS_ACCESS_SECRET", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
if not (DOCS_ACCESS_KEY and DOCS_ACCESS_SECRET and ANTHROPIC_API_KEY):
    print("[오류] 환경변수 누락: CHANNELTALK_DOCS_ACCESS_KEY/SECRET, ANTHROPIC_API_KEY 필요")
    sys.exit(1)

try:
    from anthropic import Anthropic
except ImportError:
    print("[오류] anthropic SDK 미설치. `pip3 install anthropic` 후 재시도")
    sys.exit(1)

CLAUDE_MODEL = "claude-sonnet-4-6"
DOCS_BASE = "https://document-api.channel.io"
REPO = "futureschole-ai-all/liveklass-ai-workspace"
TARGET_DOMAINS = [
    ("product-structure", "상품구조"),
    ("product-sales-settings", "판매설정"),
    ("payment-settlement", "결제/주문/정산"),
]
OUTPUT_PATH = Path(__file__).parent / "lk2_mapping.json"
client = Anthropic(api_key=ANTHROPIC_API_KEY)


# ── 채널톡 도큐먼트 fetch ─────────────────────────────────────────────────────
def fetch_channeltalk_articles() -> list[dict]:
    """채널톡에 등록된 한국어 아티클 전체 list (state, title, subtitle, bodyHtml 포함)."""
    token = base64.b64encode(f"{DOCS_ACCESS_KEY}:{DOCS_ACCESS_SECRET}".encode()).decode()
    headers = {"Authorization": f"Basic {token}"}
    url = f"{DOCS_BASE}/open/v1/spaces/$me/articles?language=ko&limit=100"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    articles = data.get("articles") or []
    print(f"[1/3] 채널톡 도큐먼트 {len(articles)}건 로드")
    return articles


def html_to_plain(html: str) -> str:
    """간단 HTML → 텍스트. 매핑 추출용으로 충분."""
    if not html:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</p>", "\n\n", text)
    text = re.sub(r"</li>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r" *\n *", "\n", text).strip()
    return text


# ── GitHub spec.md fetch (gh CLI 사용) ────────────────────────────────────────
def fetch_github_file(path: str) -> str:
    """gh API로 repo의 파일 본문 가져오기 (base64 decode)."""
    cmd = ["gh", "api", f"repos/{REPO}/contents/{path}", "--jq", ".content"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return base64.b64decode(out.stdout).decode("utf-8")


def fetch_reference_specs() -> dict[str, str]:
    """3개 도메인 spec.md + glossary/terms.md fetch."""
    refs: dict[str, str] = {}
    for slug, _ in TARGET_DOMAINS:
        path = f"service/domains/{slug}/spec.md"
        try:
            refs[slug] = fetch_github_file(path)
            print(f"      {slug}/spec.md: {len(refs[slug])} chars")
        except Exception as e:
            print(f"[경고] {path} fetch 실패: {e}")
            refs[slug] = ""
    refs["glossary"] = fetch_github_file("service/domains/glossary/terms.md")
    print(f"      glossary/terms.md: {len(refs['glossary'])} chars")
    return refs


# ── Claude 호출 ───────────────────────────────────────────────────────────────
def call_claude(prompt: str, max_tokens: int = 4096) -> str:
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def parse_json_response(text: str):
    """LLM 응답에서 첫 JSON 블록 추출 (```json ... ``` 또는 raw)."""
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    candidate = m.group(1).strip() if m else text.strip()
    return json.loads(candidate)


# ── Step A: 도큐먼트별 도메인 분류 ─────────────────────────────────────────────
def classify_articles(articles: list[dict]) -> dict[str, str]:
    """각 article을 (product-structure / product-sales-settings /
    payment-settlement / other) 4분류 → {article_id: domain}.

    title + subtitle만 보내서 토큰 절약. 본문은 매핑 추출 단계에서만.
    """
    summaries = []
    for a in articles:
        aid = a.get("id", "")
        title = (a.get("title") or a.get("name") or "").strip()
        sub = (a.get("subtitle") or a.get("summary") or "").strip()
        summaries.append({"id": aid, "title": title[:120], "subtitle": sub[:160]})

    prompt = f"""다음은 라이브클래스(LiveKlass) 채널톡에 등록된 ALF 지식 아티클의 제목·소제목 목록입니다.

각 아티클을 LK2.0 도메인 중 어디에 속하는지 분류해주세요. 가능 도메인:
- product-structure: 상품/패키지/아이템 구조, 상품 타입, 강의·라이브·VOD 상품 자체에 대한 안내
- product-sales-settings: 판매 조건(가격·할인·수강기간·판매기간), 옵션, 오퍼 등 판매 설정
- payment-settlement: 결제, 주문, 정산, 환불, 세금계산서, 부가세 등
- other: 위 3개에 해당하지 않는 경우 (예: 사이트 디자인, 회원관리, 도메인, 마케팅, 코칭, 멤버십 등)

아티클 목록:
{json.dumps(summaries, ensure_ascii=False, indent=2)}

반드시 아래 JSON 형식으로만 답변하세요 (다른 텍스트 금지):
{{
  "classifications": [
    {{"id": "아티클 id", "title": "제목", "domain": "product-structure | product-sales-settings | payment-settlement | other"}},
    ...
  ]
}}"""
    print(f"\n[2/3] 도메인 분류 중 ({len(articles)}건)...")
    raw = call_claude(prompt, max_tokens=8192)
    parsed = parse_json_response(raw)
    id_to_domain: dict[str, str] = {}
    for c in parsed.get("classifications", []):
        if isinstance(c, dict) and c.get("id"):
            id_to_domain[c["id"]] = c.get("domain", "other")

    # 카운트 출력
    counts: dict[str, int] = {}
    for d in id_to_domain.values():
        counts[d] = counts.get(d, 0) + 1
    for d, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"      {d}: {n}건")
    return id_to_domain


# ── Step B: 도메인별 매핑 추출 ─────────────────────────────────────────────────
def extract_domain_mapping(
    domain_slug: str,
    domain_label: str,
    articles: list[dict],
    spec_md: str,
    glossary_md: str,
) -> list[dict]:
    """도메인별 본문을 모두 합쳐서 LLM에 보내고 (old, new, category, evidence) 매핑 추출."""
    if not articles:
        return []

    # 본문 합치기 (HTML → 텍스트, 길이 cap)
    chunks = []
    for a in articles:
        body = html_to_plain(a.get("bodyHtml") or "")
        if not body:
            continue
        chunks.append({
            "id": a.get("id"),
            "title": a.get("title") or a.get("name") or "",
            "body": body[:2500],
        })

    if not chunks:
        return []

    prompt = f"""당신은 라이브클래스 1.0 → 2.0 마이그레이션 전문가입니다.

아래는 **현재 채널톡에 등록된 LK1.0 기준 ALF 지식 아티클**의 본문 일부와, **LK2.0의 신규 사양 문서**입니다.
LK1.0 아티클 본문에서 발견되는 **용어·기능명·메뉴 경로**가 LK2.0에서 어떻게 바뀌어야 하는지 매핑을 추출해주세요.

## LK2.0 도메인: {domain_label} ({domain_slug})

## LK2.0 신규 사양 (spec.md, 단일 진실 원천)
\"\"\"
{spec_md[:25000]}
\"\"\"

## LK2.0 신규 용어집 (glossary/terms.md, 표준 용어)
\"\"\"
{glossary_md}
\"\"\"

## LK1.0 ALF 아티클 본문 (이 도메인에 분류된 {len(chunks)}건)
{json.dumps(chunks, ensure_ascii=False, indent=2)[:60000]}

## 작업
LK1.0 본문에서 LK2.0과 **다르게 표현된 용어·기능·경로**를 찾아 매핑을 만들어주세요.

규칙:
1. **spec.md / terms.md에 명시된 용어**를 정답으로 사용. 추측 금지.
2. **확실한 매핑만**(spec/terms 명시) → confidence: "high". 추정·미확정 → confidence: "low".
3. 본문에서 발견된 **정확한 1.0 표현**을 old에, **spec/terms 기준 2.0 표현**을 new에.
4. **메뉴 경로**도 매핑 ("사이트 관리 > A > B" → "관리 > C > D" 같이).
5. **변화 없는 용어는 제외**. 1.0과 2.0이 같으면 안 적음.
6. 본문 인용은 evidence에 짧게 (50자 이내).

반드시 아래 JSON 형식으로만 답변하세요:
{{
  "mappings": [
    {{
      "old": "LK1.0 표현",
      "new": "LK2.0 표현",
      "category": "용어 | 기능명 | 메뉴 경로 | 정책",
      "evidence": "발견된 본문 짧은 인용",
      "confidence": "high | low",
      "note": "추가 설명 (옵션)"
    }}
  ]
}}"""
    print(f"\n      [{domain_slug}] 매핑 추출 중 (아티클 {len(chunks)}건)...")
    raw = call_claude(prompt, max_tokens=8192)
    parsed = parse_json_response(raw)
    mappings = parsed.get("mappings", [])
    print(f"      [{domain_slug}] {len(mappings)}개 매핑 추출")
    return mappings


# ── 메인 ──────────────────────────────────────────────────────────────────────
def main():
    articles = fetch_channeltalk_articles()

    print("\n[2/3] LK2.0 reference (spec + glossary) 로드 중...")
    refs = fetch_reference_specs()

    id_to_domain = classify_articles(articles)

    print("\n[3/3] 도메인별 매핑 추출 중...")
    domain_to_articles: dict[str, list] = {}
    for a in articles:
        d = id_to_domain.get(a.get("id", ""), "other")
        domain_to_articles.setdefault(d, []).append(a)

    result: dict = {
        "generated_at": __import__("datetime").datetime.now().isoformat(),
        "source_repo": REPO,
        "channeltalk_article_count": len(articles),
        "domains": {},
    }

    for slug, label in TARGET_DOMAINS:
        arts = domain_to_articles.get(slug, [])
        spec_md = refs.get(slug, "")
        mappings = extract_domain_mapping(slug, label, arts, spec_md, refs["glossary"])
        result["domains"][slug] = {
            "label": label,
            "article_count": len(arts),
            "article_ids": [a.get("id") for a in arts],
            "mappings": mappings,
        }

    OUTPUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[완료] {OUTPUT_PATH} 저장")
    print(f"       전체 매핑 수: {sum(len(d['mappings']) for d in result['domains'].values())}")
    for slug, info in result["domains"].items():
        high = sum(1 for m in info["mappings"] if m.get("confidence") == "high")
        low = sum(1 for m in info["mappings"] if m.get("confidence") == "low")
        print(f"       {slug}: 아티클 {info['article_count']}건 / 매핑 {len(info['mappings'])}개 (high {high} / low {low})")


if __name__ == "__main__":
    main()
