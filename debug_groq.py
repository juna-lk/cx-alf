"""Groq API 키 유효성 테스트."""
from __future__ import annotations
import os
import json
import urllib.request
import urllib.error
from pathlib import Path

ENV_PATH = Path(__file__).parent / ".env"
for line in ENV_PATH.read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

key = os.environ.get("GROQ_API_KEY", "")
print(f"키 앞 10자: {key[:10]}...")
print(f"키 뒤 6자: ...{key[-6:]}")
print(f"키 길이: {len(key)}")
has_quote = ('"' in key) or ("'" in key)
has_space = ' ' in key
has_newline = ('\n' in key) or ('\r' in key)
print(f"키에 따옴표 포함? {has_quote}")
print(f"키에 공백 포함? {has_space}")
print(f"키에 줄바꿈 포함? {has_newline}")
print()

payload = json.dumps({
    "model": "llama-3.3-70b-versatile",
    "max_tokens": 50,
    "messages": [{"role": "user", "content": "안녕"}],
}).encode()

req = urllib.request.Request(
    "https://api.groq.com/openai/v1/chat/completions",
    data=payload,
    headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "cx-alf/1.0",
    },
)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    print("✅ 성공")
    print(f"응답: {data['choices'][0]['message']['content'][:100]}")
except urllib.error.HTTPError as e:
    print(f"❌ HTTP 에러: {e.code} {e.reason}")
    print(f"본문: {e.read().decode()[:300]}")
except Exception as e:
    print(f"❌ 에러: {type(e).__name__}: {e}")
