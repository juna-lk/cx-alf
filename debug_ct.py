"""채널톡 API 응답 디버깅용 스크립트."""
from __future__ import annotations
import os
import json
import urllib.request
from pathlib import Path

# .env 로드
ENV_PATH = Path(__file__).parent / ".env"
for line in ENV_PATH.read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

CT_ACCESS_KEY = os.environ.get("CHANNELTALK_ACCESS_KEY", "")
CT_ACCESS_SECRET = os.environ.get("CHANNELTALK_ACCESS_SECRET", "")

print(f"KEY 앞 6자: {CT_ACCESS_KEY[:6]}***")
print(f"SECRET 앞 6자: {CT_ACCESS_SECRET[:6]}***")
print()

url = "https://api.channel.io/open/v5/user-chats?limit=5&sortOrder=desc&state=closed"
req = urllib.request.Request(url, headers={
    "x-access-key": CT_ACCESS_KEY,
    "x-access-secret": CT_ACCESS_SECRET,
    "Content-Type": "application/json",
})

try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    chats = data.get("userChats", [])
    print(f"받은 chat 개수: {len(chats)}")
    if chats:
        c = chats[0]
        print(f"첫 chat id: {c.get('id')}")
        print(f"첫 chat createdAt: {c.get('createdAt')}")
        print(f"전체 키: {list(c.keys())[:15]}")
    else:
        print(f"응답 키: {list(data.keys())}")
        print(f"응답 전체(앞 500자): {json.dumps(data)[:500]}")
except urllib.error.HTTPError as e:
    print(f"HTTP 에러: {e.code} {e.reason}")
    print(f"응답: {e.read().decode()[:500]}")
except Exception as e:
    print(f"에러: {type(e).__name__}: {e}")
