"""채널톡 Documents Open API — 아티클 등록·게시"""
from __future__ import annotations
import os, sys, json, re, urllib.request, urllib.error, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _alf_common import make_handler_base, docs_req

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
DOCS_ACCESS_KEY = os.environ.get("CHANNELTALK_DOCS_ACCESS_KEY", "")
DOCS_ACCESS_SECRET = os.environ.get("CHANNELTALK_DOCS_ACCESS_SECRET", "")


def _escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def _inline(s: str) -> str:
    s = _escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\*(.+?)\*", r"<em>\1</em>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def markdown_to_html(md: str) -> str:
    lines = md.split("\n")
    parts: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # heading
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            level = len(m.group(1))
            parts.append(f"<h{level}>{_inline(m.group(2).strip())}</h{level}>")
            i += 1
            continue
        # unordered list
        if re.match(r"^[-*]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^[-*]\s+", lines[i]):
                text = re.sub(r"^[-*]\s+", "", lines[i])
                items.append(f"<li>{_inline(text)}</li>")
                i += 1
            parts.append("<ul>" + "".join(items) + "</ul>")
            continue
        # ordered list
        if re.match(r"^\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i]):
                text = re.sub(r"^\d+\.\s+", "", lines[i])
                items.append(f"<li>{_inline(text)}</li>")
                i += 1
            parts.append("<ol>" + "".join(items) + "</ol>")
            continue
        # empty line
        if not line.strip():
            i += 1
            continue
        # paragraph
        parts.append(f"<p>{_inline(line)}</p>")
        i += 1
    return "\n".join(parts)


_Base = make_handler_base()


class handler(_Base):
    def do_GET(self):
        if not self._check_auth():
            return
        if not DOCS_ACCESS_KEY or not DOCS_ACCESS_SECRET:
            self._respond(500, {"ok": False, "error": "채널톡 Documents API 키가 설정되지 않았어요."})
            return
        try:
            data = docs_req("/open/v1/spaces/$me/articles?language=ko&limit=100", method="GET")
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
                "bodyHtml": a.get("bodyHtml") or "",
                "createdAt": a.get("createdAt"),
                "updatedAt": a.get("updatedAt"),
            }
            for a in articles
        ]
        result.sort(key=lambda x: (0 if x["state"] == "published" else 1, -(x["updatedAt"] or 0)))
        self._respond(200, {"ok": True, "articles": result, "total": len(result)})

    def do_POST(self):
        if not self._check_auth():
            return
        if not DOCS_ACCESS_KEY or not DOCS_ACCESS_SECRET:
            self._respond(500, {"ok": False, "error": "채널톡 Documents API 키가 설정되지 않았어요."})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}

        name = (body.get("name") or "").strip()
        content = (body.get("content") or "").strip()
        subtitle = (body.get("subtitle") or "").strip()
        draft_id = body.get("draft_id")
        do_publish = bool(body.get("publish", False))

        if not name:
            self._respond(400, {"ok": False, "error": "제목이 필요해요."})
            return
        if not content:
            self._respond(400, {"ok": False, "error": "본문이 비어있어요."})
            return

        article_body: dict = {"name": name, "language": "ko", "bodyHtml": markdown_to_html(content)}
        if subtitle:
            article_body["summary"] = subtitle

        try:
            result = docs_req("/open/v1/spaces/$me/articles", method="POST", body=article_body)
        except urllib.error.HTTPError as e:
            self._respond(500, {"ok": False, "error": f"아티클 생성 실패: {e.read().decode()}"})
            return
        except Exception as e:
            self._respond(500, {"ok": False, "error": f"아티클 생성 실패: {e}"})
            return

        article = result.get("article", {})
        revision = result.get("revision", {})
        article_id = article.get("id", "")
        revision_id = revision.get("id", "")
        slug = article.get("slug", "")

        published = False
        if do_publish and article_id and revision_id:
            try:
                docs_req(
                    f"/open/v1/spaces/$me/articles/{article_id}/revisions/{revision_id}/publish",
                    method="PUT",
                )
                published = True
            except Exception as e:
                print(f"[alf_publish] 게시 실패: {e}")

        # alf_drafts status 업데이트
        if draft_id:
            try:
                patch_url = f"{SUPABASE_URL}/rest/v1/alf_drafts?id=eq.{urllib.parse.quote(str(draft_id))}"
                patch_req = urllib.request.Request(
                    patch_url,
                    data=json.dumps({"status": "applied"}).encode(),
                    headers={
                        "apikey": SUPABASE_SERVICE_KEY,
                        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                        "Content-Type": "application/json",
                        "Prefer": "return=minimal",
                    },
                    method="PATCH",
                )
                urllib.request.urlopen(patch_req, timeout=10)
            except Exception as e:
                print(f"[alf_publish] alf_drafts 업데이트 실패: {e}")

        self._respond(200, {
            "ok": True,
            "article_id": article_id,
            "revision_id": revision_id,
            "slug": slug,
            "published": published,
        })
