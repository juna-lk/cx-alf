from __future__ import annotations
import json
import os
import re
import urllib.request

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

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
    """Groq API 호출 → 텍스트 응답 반환"""
    if not api_key:
        raise ValueError("api_key is required")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = json.dumps({
        "model": GROQ_MODEL,
        "max_tokens": max_tokens,
        "messages": messages,
    }).encode()

    req = urllib.request.Request(
        GROQ_API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "cx-alf/1.0 (+https://github.com/juna-lk/cx-alf)",
        },
    )
    with urllib.request.urlopen(req, timeout=55) as resp:
        data = json.loads(resp.read())
    return sanitize_korean(data["choices"][0]["message"]["content"])


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
