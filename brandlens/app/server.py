"""Zero-dependency HTTP server: JSON API under /api plus the static UI."""
import json
import mimetypes
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .store import Forbidden, Invalid, NotFound, Store, ROOT

STATIC = os.path.join(ROOT, "static")
MAX_BODY = 12 * 1024 * 1024
DEMO_PASSWORD = "demo123"

CSP = ("default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")


def seed_users(store):
    for username, name, role in [("maya", "Maya Iyer", "creator"), ("ravi", "Ravi Nair", "creator"),
                                 ("meera", "Meera Kapoor", "manager"), ("lena", "Lena Fischer", "reviewer")]:
        store.add_user(username, name, role, DEMO_PASSWORD)


class Handler(BaseHTTPRequestHandler):
    store = None
    server_version = "BrandLens/2.0"

    def log_message(self, fmt, *args):
        if os.environ.get("BRANDLENS_QUIET") != "1":
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # ---- helpers
    def _send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _err(self, code, msg):
        self._send(code, {"error": msg})

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise Invalid("Request is too large.")
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except ValueError:
            raise Invalid("Request body must be JSON.")

    def _user(self):
        h = self.headers.get("Authorization", "")
        token = h[7:] if h.startswith("Bearer ") else None
        return self.store.user_for_token(token)

    # ---- routing
    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        url = urlparse(self.path)
        path = url.path
        try:
            if not path.startswith("/api/"):
                if method != "GET":
                    return self._err(405, "Method not allowed.")
                return self._static(path)
            if path == "/api/login" and method == "POST":
                b = self._body()
                token, user = self.store.login(str(b.get("username", "")), str(b.get("password", "")))
                return self._send(200, {"token": token, "user": user})
            user = self._user()
            if not user:
                return self._err(401, "Sign in to continue.")
            q = parse_qs(url.query)
            with self.store.lock:
                return self._api(method, path, q, user)
        except Invalid as e:
            self._err(400, str(e))
        except Forbidden as e:
            self._err(403, str(e))
        except NotFound as e:
            self._err(404, str(e))
        except Exception as e:  # keep details server-side
            sys.stderr.write(f"error: {e!r}\n")
            self._err(500, "Something went wrong on the server.")

    def _api(self, method, path, q, user):
        S = self.store
        if path == "/api/me" and method == "GET":
            return self._send(200, S.public_user(user))
        if path == "/api/kit" and method == "GET":
            return self._send(200, S.kit_summary())
        if path == "/api/check" and method == "POST":
            b = self._body()
            f = S.quick_check(user, b.get("content", ""), b.get("fmt", "text"), b.get("channel", "web"))
            return self._send(200, {"findings": f})
        if path == "/api/submissions" and method == "GET":
            return self._send(200, S.list_submissions(user, (q.get("status") or [None])[0]))
        if path == "/api/submissions" and method == "POST":
            b = self._body()
            return self._send(201, S.create_submission(user, b.get("title"), b.get("channel", "web"), b.get("fmt", "text"),
                                                       b.get("content", ""), b.get("image")))
        if path == "/api/insights" and method == "GET":
            return self._send(200, S.insights(user))
        if path == "/api/audit" and method == "GET":
            return self._send(200, S.audit_log(user))
        m = re.fullmatch(r"/api/submissions/(\d+)", path)
        if m and method == "GET":
            return self._send(200, S.get_submission(user, int(m.group(1))))
        m = re.fullmatch(r"/api/submissions/(\d+)/image", path)
        if m and method == "GET":
            n = int((q.get("v") or [0])[0]) or None
            img = S.get_image(user, int(m.group(1)), n)
            ctype = "image/png" if img[:4] == b"\x89PNG" else "image/jpeg" if img[:2] == b"\xff\xd8" else "application/octet-stream"
            return self._send(200, img, ctype)
        m = re.fullmatch(r"/api/submissions/(\d+)/versions/(\d+)", path)
        if m and method == "GET":
            return self._send(200, S.get_version(user, int(m.group(1)), int(m.group(2))))
        m = re.fullmatch(r"/api/submissions/(\d+)/(versions|apply|send|review|comments)", path)
        if m and method == "POST":
            sid, what = int(m.group(1)), m.group(2)
            b = self._body()
            if what == "versions":
                return self._send(201, S.new_version(user, sid, b.get("content", ""), b.get("image")))
            if what == "apply":
                return self._send(201, S.apply_decisions(user, sid))
            if what == "send":
                return self._send(200, S.send_for_review(user, sid))
            if what == "review":
                return self._send(200, S.review(user, sid, b.get("action"), b.get("comment")))
            return self._send(201, S.add_comment(user, sid, b.get("body")))
        m = re.fullmatch(r"/api/submissions/(\d+)/findings/(f\d+)", path)
        if m and method == "POST":
            b = self._body()
            return self._send(200, S.decide(user, int(m.group(1)), m.group(2), b.get("action"), b.get("reason"), b.get("edited")))
        return self._err(404, "Unknown endpoint.")

    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        full = os.path.normpath(os.path.join(STATIC, path.lstrip("/")))
        if not full.startswith(STATIC + os.sep) or not os.path.isfile(full):
            return self._err(404, "Not found.")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as fh:
            self._send(200, fh.read(), ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))


def make_server(db_path, host="127.0.0.1", port=8000):
    store = Store(db_path)
    seed_users(store)
    Handler.store = store
    return ThreadingHTTPServer((host, port), Handler), store


def main():
    db = os.environ.get("BRANDLENS_DB", os.path.join(ROOT, "data", "brandlens.db"))
    port = int(os.environ.get("PORT", "8000"))
    srv, _ = make_server(db, port=port)
    print(f"BrandLens running at http://127.0.0.1:{port}  (demo password for all accounts: {DEMO_PASSWORD})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
