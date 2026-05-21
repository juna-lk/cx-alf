"""채널톡 Documents API — 등록된 아티클 목록 조회"""
from __future__ import annotations
import os, sys, json, base64, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _alf_common import make_handler_base

DOCS_ACCESS_KEY = os.environ.get("CHANNELTALK_DOCS_ACCESS_KEY", "")
DOCS_ACCESS_SECRET = os.environ.get("CHANNELTALK_DOCS_ACCESS_SECRET", "")
DOCS_BASE = "https://document-api.channel.io"

_Base = make_handler_base()


def docs_get(path: str) -> dict:
    token = base64.b64encode(f"{DOCS_ACCESS_KEY}:{DOCS_ACCESS_SECRET}".encode()).decode()
    req = urllib.request.Request(
        f"{DOCS_BASE}{path}",
        headers={"Authorization": f"Basic {token}"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


class handler(_Base):
    def do_GET(self):
        if not self._check_auth():
            return
        if not DOCS_ACCESS_KEY or not DOCS_ACCESS_SECRET:
            self._respond(500, {"ok": False, "error": "채널톡 Documents API 키가 설정되지 않았어요."})
            return

        try:
            data = docs_get("/open/v1/spaces/$me/articles?language=ko&limit=100")
        except urllib.error.HTTPError as e:
            self._respond(500, {"ok": False, "error": f"채널톡 API 오류: {e.read().decode()}"})
            return
        except Exception as e:
            self._respond(500, {"ok": False, "error": str(e)})
            return

        articles = data.get("articles") or []
        result = [
            {
                "id": a.get("id"),
                "title": a.get("title") or a.get("name") or "",
                "subtitle": a.get("subtitle") or a.get("summary") or "",
                "slug": a.get("slug", ""),
                "state": a.get("state", ""),
                "createdAt": a.get("createdAt"),
                "updatedAt": a.get("updatedAt"),
            }
            for a in articles
        ]
        # published 먼저, 그 안에서 최신순
        result.sort(key=lambda x: (0 if x["state"] == "published" else 1, -(x["updatedAt"] or 0)))

        self._respond(200, {"ok": True, "articles": result, "total": len(result)})
