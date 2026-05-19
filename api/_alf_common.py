from __future__ import annotations
import json
import os
import re
import urllib.request

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

# 한자/일본어 자주 혼입되는 단어 → 한글 치환 사전
CJK_REPLACE = {
    "内容": "내용", "內容": "내용",
    "編集": "편집", "編輯": "편집",
    "顧客": "고객",
    "答弁": "답변", "答辯": "답변", "返答": "답변",
    "質問": "질문",
    "回答": "회답",
    "管理": "관리",
    "設定": "설정",
    "確認": "확인",
    "問題": "문제",
    "解決": "해결",
    "対応": "대응", "対處": "대처",
    "情報": "정보",
    "状態": "상태",
    "変更": "변경", "變更": "변경",
    "新規": "신규",
    "기능을 안내": "기능을 안내",
    # 일본어 가타카나
    "プラン": "플랜",
    "サイト": "사이트",
    "ホスティング": "호스팅",
    "アップグレード": "업그레이드",
    "ダウングレード": "다운그레이드",
    "サービス": "서비스",
    "ユーザー": "사용자",
    "コンテンツ": "콘텐츠",
    "メッセージ": "메시지",
    "アンケート": "설문",
}


# 아티클/FAQ에 들어가면 안 되는 보일러플레이트 패턴 (line 단위 매칭)
_BOILERPLATE_PATTERNS = [
    # ──── 시작 인사 ────
    re.compile(r'^안녕하세요[,\.\s].*'),
    re.compile(r'^반갑습니다[,\.\s].*'),
    re.compile(r'^라이브클래스를?\s*이용.*'),
    re.compile(r'^.{0,40}안내를?\s*드리겠습니다\.?\s*$'),
    re.compile(r'^.{0,40}안내해?\s*드리겠습니다\.?\s*$'),
    re.compile(r'^.{0,40}안내드립니다\.?\s*$'),
    re.compile(r'^.{0,40}소개해?\s*드리겠습니다\.?\s*$'),
    re.compile(r'^.{0,40}말씀드리겠습니다\.?\s*$'),
    # ──── 마무리 인사 ────
    re.compile(r'^.*감사합니다[\.\!\s🙏]*$'),
    re.compile(r'^.*고맙습니다[\.\!\s🙏]*$'),
    re.compile(r'^.*노력하겠습니다.*$'),
    re.compile(r'^.*마무리할게요.*$'),
    re.compile(r'^.*마무리하겠습니다.*$'),
    re.compile(r'^.*도움이?\s*되었?기?를?\s*바랍니다.*$'),
    re.compile(r'^.*궁금하신\s*점.*편하게\s*메시지\s*주세요.*$'),
    re.compile(r'^.*언제든.*편하게\s*문의.*$'),
    # ──── 자기소개 ────
    re.compile(r'^.{0,60}코치\s+[가-힣]{2,4}(\(.+\))?\s*입니다.*'),
    re.compile(r'^.{0,60}매니저\s+[가-힣]{2,4}(\(.+\))?\s*입니다.*'),
    re.compile(r'^.{0,60}담당자\s+[가-힣]{2,4}(\(.+\))?\s*입니다.*'),
    re.compile(r'^.{0,60}고객\s*성공.*입니다.*'),
    # ──── 1:1 응답 표현 ────
    re.compile(r'^.*혹시\s+.*도움이?\s*필요.*\?.*$'),
    re.compile(r'^.*혹시\s+.*궁금.*\?.*$'),
]


def strip_article_boilerplate(text: str) -> str:
    """아티클/FAQ 본문에서 인사·자기소개·마무리 인사·1:1 응답 패턴을 제거."""
    if not text:
        return text
    out_lines = []
    for line in text.split('\n'):
        stripped = line.strip()
        if stripped and any(p.match(stripped) for p in _BOILERPLATE_PATTERNS):
            continue
        out_lines.append(line)
    # 연속 빈 줄 2개로 압축
    result = '\n'.join(out_lines)
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()


def verify_draft(content: str, format_type: str = "article", cluster_label: str = "") -> dict:
    """채널톡 ALF 가이드라인 기준으로 초안 자동 검증.

    반환: {
      'warnings': [{'rule': '...', 'level': 'warning'|'error', 'message': '...'}],
      'fixed': str  # 자동 수정된 콘텐츠 (이모지 제거 등)
    }
    """
    warnings: list[dict] = []
    fixed = content

    # 1) 이모지 자동 제거 (마케팅톤 방지)
    emoji_pattern = re.compile(
        r"[\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF"
        r"\U0001F700-\U0001F77F\U0001F780-\U0001F7FF\U0001F800-\U0001F8FF"
        r"\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF"
        r"☀-⛿✀-➿]+"
    )
    if emoji_pattern.search(fixed):
        warnings.append({"rule": "이모지", "level": "warning",
                         "message": "이모지를 자동 제거했어요 (Help Doc 톤 유지)"})
        fixed = emoji_pattern.sub("", fixed)

    # 2) 헤딩 구조 (article)
    if format_type == "article":
        if not re.search(r"^#\s", fixed, re.MULTILINE):
            warnings.append({"rule": "제목", "level": "error",
                             "message": "# 제목이 없어요. ALF는 제목을 우선 검색해요"})
        sub_count = len(re.findall(r"^##\s", fixed, re.MULTILINE))
        if sub_count == 0:
            warnings.append({"rule": "소제목", "level": "warning",
                             "message": "## 소제목이 없어요. 구조화하면 ALF 매칭 정확도가 올라가요"})

    # 3) 잔존 보일러플레이트
    boilerplate_keywords = [
        ("안녕하세요", "시작 인사"),
        ("감사합니다", "마무리 인사"),
        ("드리겠습니다.", "안내 도입"),
        ("마무리할게요", "상담 종료 멘트"),
    ]
    for kw, label in boilerplate_keywords:
        if kw in fixed:
            warnings.append({"rule": label, "level": "warning",
                             "message": f'금지 표현 "{kw}" 감지 — Help Doc 톤 위반'})

    # 4) 친근한 종결형 비율
    sentences = [s for s in re.split(r"[.\n!?]", fixed) if len(s.strip()) > 8]
    if sentences:
        friendly = sum(1 for s in sentences
                       if re.search(r"(어요|습니다|예요|에요|드려요|돼요)\s*$", s.strip()))
        ratio = friendly / len(sentences)
        if ratio < 0.25:
            warnings.append({"rule": "종결형", "level": "warning",
                             "message": f"친근한 종결형(-어요/-습니다) 비율이 낮음 ({int(ratio*100)}%) — 권장 30% 이상"})

    # 5) 글자수
    length = len(fixed)
    if format_type == "article" and length > 2000:
        warnings.append({"rule": "글자수", "level": "warning",
                         "message": f"{length}자 — 2,000자 초과. 별도 아티클 분리 권장"})
    elif format_type == "faq" and length > 500:
        warnings.append({"rule": "글자수", "level": "error",
                         "message": f"{length}자 — 채널톡 FAQ 답변 한도 500자 초과"})

    # 6) 불릿·번호 목록 존재
    has_list = bool(re.search(r"^\s*[-*]\s|^\s*\d+\.\s", fixed, re.MULTILINE))
    if format_type == "article" and not has_list and length > 200:
        warnings.append({"rule": "목록", "level": "info",
                         "message": "불릿/번호 목록이 없어요. 단계·조건은 목록으로 정리하면 ALF가 선후 관계 파악 쉬워요"})

    # 7) 제목에 클러스터 키워드 포함
    if format_type == "article" and cluster_label:
        m = re.search(r"^#\s+(.+)$", fixed, re.MULTILINE)
        if m:
            title = m.group(1)
            # 클러스터 라벨의 주요 단어 (2자 이상) 중 하나라도 제목에 있는지
            kw_tokens = [t for t in re.split(r"[\s,/·]+", cluster_label) if len(t) >= 2]
            if kw_tokens and not any(t in title for t in kw_tokens):
                warnings.append({"rule": "제목 키워드", "level": "warning",
                                 "message": f'제목에 핵심 키워드({", ".join(kw_tokens[:3])}) 미포함'})

    return {"warnings": warnings, "fixed": fixed}


def sanitize_korean(text: str) -> str:
    """LLM 출력에서 한자/일본어 단어를 한글로 치환.
    사전에 없는 한자/가타카나가 남아있으면 제거(공백으로) 처리.
    """
    if not text:
        return text
    # 1) 사전 치환 (긴 단어부터)
    for foreign, korean in sorted(CJK_REPLACE.items(), key=lambda x: -len(x[0])):
        text = text.replace(foreign, korean)
    # 2) 남은 일본어 가타카나·히라가나 제거 (안전망)
    text = re.sub(r"[぀-ゟ゠-ヿ]+", "", text)
    # 3) 남은 한자(CJK Unified Ideographs) 단독 → 제거. 한글 텍스트 사이에 끼면 어색하지만 안전망.
    text = re.sub(r"[㐀-䶿一-鿿]+", "", text)
    # 4) 연속 공백 정리
    text = re.sub(r"  +", " ", text)
    return text


def call_anthropic(prompt: str, system: str = "", max_tokens: int = 4096, api_key: str = "") -> str:
    """Gemini API 호출 → 텍스트 응답 반환"""
    if not api_key:
        raise ValueError("api_key is required")

    payload_dict: dict = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if system:
        payload_dict["systemInstruction"] = {"parts": [{"text": system}]}

    payload = json.dumps(payload_dict).encode()
    url = f"{GEMINI_API_URL}?key={api_key}"

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=55) as resp:
        data = json.loads(resp.read())
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return sanitize_korean(text)


def get_supabase_headers(service_key: str) -> dict:
    """Supabase REST API 헤더 반환"""
    return {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def supabase_get(url: str, service_key: str) -> list:
    """Supabase REST GET 요청"""
    req = urllib.request.Request(url, headers=get_supabase_headers(service_key))
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def supabase_delete(url: str, service_key: str) -> None:
    """Supabase REST DELETE 요청"""
    req = urllib.request.Request(
        url, headers=get_supabase_headers(service_key), method="DELETE",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def extract_json(text: str) -> dict | list:
    """LLM 출력에서 JSON 객체/배열을 안전하게 추출.

    ```json ... ``` 코드블록이든, 일반 텍스트 안의 객체든 모두 처리.
    """
    if not text:
        return {}
    text = text.strip()
    # 코드블록 안의 JSON 추출 시도
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        candidate = m.group(1).strip()
    else:
        # 첫 { 또는 [ 부터 매칭되는 닫는 짝까지 추출
        m = re.search(r"[\{\[][\s\S]*[\}\]]", text)
        candidate = m.group(0) if m else text
    return json.loads(candidate)


def supabase_post(url: str, data: dict | list, service_key: str, method: str = "POST") -> dict:
    """Supabase REST POST/PATCH 요청"""
    payload = json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers=get_supabase_headers(service_key),
        method=method,
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read()
        return json.loads(body) if body else {}


def _verify_supabase_token(access_token: str) -> dict | None:
    """Supabase 액세스 토큰으로 사용자 정보 조회. 실패시 None."""
    supabase_url = os.environ.get("SUPABASE_URL", "")
    anon_key = os.environ.get("SUPABASE_ANON_KEY", "")
    if not supabase_url or not anon_key or not access_token:
        return None
    try:
        req = urllib.request.Request(
            f"{supabase_url}/auth/v1/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "apikey": anon_key,
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def make_handler_base():
    """Vercel 서버리스 handler 공통 CORS + Supabase OAuth 인증"""
    from http.server import BaseHTTPRequestHandler

    class _Base(BaseHTTPRequestHandler):
        def _respond(self, code: int, data: dict):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _check_auth(self) -> bool:
            """Supabase 액세스 토큰 검증 + 이메일 도메인 화이트리스트.
            SUPABASE_URL / SUPABASE_ANON_KEY 미설정 시 비활성화 (개발용).
            """
            anon_key = os.environ.get("SUPABASE_ANON_KEY", "")
            if not anon_key:
                return True
            allowed_domain = os.environ.get("ALLOWED_EMAIL_DOMAIN", "liveklass.com").lower()
            header = self.headers.get("Authorization", "")
            token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else header.strip()
            user = _verify_supabase_token(token) if token else None
            if not user:
                self._respond(401, {"ok": False, "error": "인증 필요 — Google 로그인해주세요"})
                return False
            email = (user.get("email") or "").lower()
            if not email.endswith("@" + allowed_domain):
                self._respond(403, {"ok": False, "error": f"@{allowed_domain} 계정만 접근 가능"})
                return False
            return True

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.end_headers()

        def log_message(self, *args):
            pass  # Vercel 로그 노이즈 억제

    return _Base
