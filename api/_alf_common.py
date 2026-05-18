from __future__ import annotations
import json
import urllib.request

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"


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
        },
    )
    with urllib.request.urlopen(req, timeout=55) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


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


def make_handler_base():
    """Vercel 서버리스 handler 공통 CORS 응답 헬퍼 반환"""
    from http.server import BaseHTTPRequestHandler

    class _Base(BaseHTTPRequestHandler):
        def _respond(self, code: int, data: dict):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def log_message(self, *args):
            pass  # Vercel 로그 노이즈 억제

    return _Base
