import base64, io, json, os, tempfile, threading, unittest, urllib.request, urllib.error

os.environ["BRANDLENS_TODAY"] = "2026-10-25"
os.environ["BRANDLENS_QUIET"] = "1"
from app import checkers
from app.server import make_server

KIT = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data", "brand_kit.json")))


def rules(findings):
    return {f["rule_id"] for f in findings}


class CheckerTests(unittest.TestCase):
    def run_text(self, t, ch="web", fmt="text"):
        return checkers.analyze(t, fmt, ch, KIT)

    def test_terminology_and_replacement(self):
        f = self.run_text("Try AuroraSync today.")
        hit = [x for x in f if x["rule_id"] == "TERM-PRODUCT"][0]
        self.assertEqual(hit["replacement"], "Aurora Sync")
        self.assertEqual(checkers.apply_fixes("Try AuroraSync today.", [{"start": hit["start"], "end": hit["end"], "new": hit["replacement"]}]), "Try Aurora Sync today.")

    def test_correct_term_not_flagged(self):
        self.assertNotIn("TERM-PRODUCT", rules(self.run_text("Try Aurora Sync today.")))

    def test_hype_keeps_case(self):
        f = [x for x in self.run_text("Revolutionary tools for you.") if x["rule_id"] == "TONE-HYPE"][0]
        self.assertEqual(f["replacement"], "New")

    def test_fact_mismatch_and_lower_bound(self):
        self.assertIn("FACT-CUSTOMERS", rules(self.run_text("Trusted by 10,000 customers.")))
        self.assertNotIn("FACT-CUSTOMERS", rules(self.run_text("Trusted by over 10,000 customers.")))
        self.assertNotIn("FACT-CUSTOMERS", rules(self.run_text("Trusted by 12,000 customers.")))
        f = [x for x in self.run_text("We offer 99.99% uptime.") if x["rule_id"] == "FACT-UPTIME"][0]
        self.assertEqual(f["replacement"], "99.9")

    def test_expired_claim(self):
        f = [x for x in self.run_text("We are SOC 2 Type II certified.") if x["rule_id"] == "CLAIM-SOC2"]
        self.assertTrue(f and f[0]["severity"] == "high" and f[0]["needs_reviewer"])

    def test_claim_valid_before_expiry(self):
        os.environ["BRANDLENS_TODAY"] = "2026-01-01"
        try:
            self.assertNotIn("CLAIM-SOC2", rules(self.run_text("We are SOC 2 certified.")))
        finally:
            os.environ["BRANDLENS_TODAY"] = "2026-10-25"

    def test_superlative_and_guarantee(self):
        r = rules(self.run_text("The fastest, guaranteed, risk-free choice, better than Acme."))
        self.assertTrue({"CLAIM-SUPERLATIVE", "CLAIM-GUARANTEE", "CLAIM-COMPETITOR"} <= r)

    def test_requirements(self):
        self.assertIn("REQ-TERMS", rules(self.run_text("Start your free trial.")))
        self.assertNotIn("REQ-TERMS", rules(self.run_text("Start your free trial. Terms apply.")))
        self.assertIn("REQ-UNSUB", rules(self.run_text("Our newsletter.", ch="email")))
        f = [x for x in self.run_text("Free trial now", ch="web") if x["rule_id"] == "REQ-TERMS"][0]
        self.assertEqual(checkers.apply_fixes("Free trial now", [{"start": None, "end": None, "new": None, "append": f["append"]}]), "Free trial now Terms apply.")

    def test_channel_length(self):
        self.assertIn("MSG-LENGTH", rules(self.run_text("word " * 80, ch="social")))

    def test_confidentiality(self):
        t = "Project Falcon ships soon. Key sk-abcdefghijklmnop1234 and card 4111 1111 1111 1111 see http://wiki.corp/x"
        r = rules(self.run_text(t))
        self.assertTrue({"CONF-CODENAME", "CONF-SECRET", "CONF-CARD", "CONF-INTERNAL-URL"} <= r)

    def test_html_accessibility(self):
        h = '<h1>A</h1><h3>B</h3><img src="x.png"><p style="color:#bbbbbb">hi</p><a href="/">click here</a>'
        r = rules(self.run_text(h, fmt="html"))
        self.assertTrue({"A11Y-HEADING-ORDER", "A11Y-ALT-MISSING", "A11Y-CONTRAST", "A11Y-LINK-TEXT"} <= r)
        good = '<h1>A</h1><h2>B</h2><img src="x.png" alt="Dashboard showing sync status"><p style="color:#111111">hi</p>'
        self.assertFalse([x for x in self.run_text(good, fmt="html") if x["category"] == "accessibility"])

    def test_contrast_fix_passes(self):
        fg, bg = checkers._hex_to_rgb("#aaaaaa"), (255, 255, 255)
        new = checkers.fix_color(fg, bg)
        self.assertGreaterEqual(checkers.contrast_ratio(checkers._hex_to_rgb(new), bg), 4.5)

    def test_html_tags_do_not_trigger_text_rules(self):
        self.assertNotIn("TERM-PRODUCT", rules(self.run_text('<a title="AuroraSync" href="/">Aurora Sync guide</a>', fmt="html")))

    def test_image_palette_and_size(self):
        from PIL import Image
        def png(color, size):
            b = io.BytesIO(); Image.new("RGB", size, color).save(b, "PNG"); return b.getvalue()
        off = checkers.check_image(png((200, 40, 200), (1200, 630)), "social", KIT)
        self.assertIn("VIS-PALETTE", rules(off))
        ok = checkers.check_image(png((31, 79, 216), (1200, 630)), "social", KIT)
        self.assertEqual(rules(ok), set())
        self.assertIn("VIS-ASPECT", rules(checkers.check_image(png((31, 79, 216), (800, 800)), "social", KIT)))
        self.assertIn("VIS-UNREADABLE", rules(checkers.check_image(b"nope", "social", KIT)))


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.srv, cls.store = make_server(os.path.join(cls.tmp, "t.db"), port=0)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.tok = {}
        for u in ("maya", "ravi", "meera", "lena"):
            cls.tok[u] = cls.call("POST", "/api/login", {"username": u, "password": "demo123"})[1]["token"]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    @classmethod
    def call(cls, method, path, body=None, user=None):
        req = urllib.request.Request(f"http://127.0.0.1:{cls.port}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
        if user:
            req.add_header("Authorization", "Bearer " + cls.tok[user])
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_auth_required_and_bad_login(self):
        self.assertEqual(self.call("GET", "/api/submissions")[0], 401)
        self.assertEqual(self.call("POST", "/api/login", {"username": "maya", "password": "x"})[0], 403)

    def test_full_workflow_with_reviewer_escalation(self):
        code, s = self.call("POST", "/api/submissions", {"title": "Launch post", "channel": "web", "fmt": "text",
                            "content": "AuroraSync is the fastest, trusted by 10,000 customers. Contact Project Falcon team."}, "maya")
        self.assertEqual(code, 201)
        sid = s["id"]
        self.assertTrue(s["blockers"])  # undecided high findings
        self.assertEqual(self.call("POST", f"/api/submissions/{sid}/send", {}, "maya")[0], 400)
        for f in s["findings"]:
            if f["replacement"] is not None:
                self.assertEqual(self.call("POST", f"/api/submissions/{sid}/findings/{f['fid']}", {"action": "accept"}, "maya")[0], 200)
        # no automatic fix for the codename: dismissing needs a reason
        cn = [f for f in s["findings"] if f["rule_id"] == "CONF-CODENAME"][0]
        self.assertEqual(self.call("POST", f"/api/submissions/{sid}/findings/{cn['fid']}", {"action": "accept"}, "maya")[0], 400)
        self.assertEqual(self.call("POST", f"/api/submissions/{sid}/findings/{cn['fid']}", {"action": "dismiss", "reason": "x"}, "maya")[0], 400)
        self.assertEqual(self.call("POST", f"/api/submissions/{sid}/findings/{cn['fid']}", {"action": "dismiss", "reason": "Press release already public"}, "maya")[0], 200)
        # other users cannot touch it
        self.assertEqual(self.call("GET", f"/api/submissions/{sid}", None, "ravi")[0], 404)
        self.assertEqual(self.call("POST", f"/api/submissions/{sid}/send", {}, "maya")[0], 400)  # accepted fixes not applied
        code, s2 = self.call("POST", f"/api/submissions/{sid}/apply", {}, "maya")
        self.assertEqual(code, 201)
        self.assertEqual(s2["version"], 2)
        self.assertIn("Aurora Sync", s2["content"])
        self.assertNotIn("10,000", s2["content"])
        carried = [f for f in s2["findings"] if f["rule_id"] == "CONF-CODENAME"][0]
        self.assertEqual(carried["decision"], "dismiss")  # decision carried to new version
        # fix any remaining undecided high findings by dismissing with reason
        for f in s2["findings"]:
            if f["severity"] == "high" and not f["decision"]:
                self.call("POST", f"/api/submissions/{sid}/findings/{f['fid']}", {"action": "dismiss", "reason": "Reviewed with legal"}, "maya")
        code, s3 = self.call("POST", f"/api/submissions/{sid}/send", {}, "maya")
        self.assertEqual(code, 200, s3)
        self.assertEqual(s3["status"], "pending_manager")
        self.assertTrue(s3["requires_reviewer"])
        # locked for the creator, wrong roles blocked
        self.assertEqual(self.call("POST", f"/api/submissions/{sid}/versions", {"content": "new"}, "maya")[0], 403)
        self.assertEqual(self.call("POST", f"/api/submissions/{sid}/review", {"action": "approve"}, "lena")[0], 403)
        self.assertEqual(self.call("POST", f"/api/submissions/{sid}/review", {"action": "approve"}, "maya")[0], 403)
        code, s4 = self.call("POST", f"/api/submissions/{sid}/review", {"action": "approve", "comment": "ok"}, "meera")
        self.assertEqual(s4["status"], "pending_reviewer")
        self.assertEqual(self.call("POST", f"/api/submissions/{sid}/review", {"action": "approve"}, "meera")[0], 403)
        code, s5 = self.call("POST", f"/api/submissions/{sid}/review", {"action": "request_changes"}, "lena")
        self.assertEqual(code, 400)  # comment required
        code, s5 = self.call("POST", f"/api/submissions/{sid}/review", {"action": "approve", "comment": "Cleared"}, "lena")
        self.assertEqual(s5["status"], "approved")
        self.assertEqual(self.call("POST", f"/api/submissions/{sid}/comments", {"body": "Thanks"}, "maya")[0], 201)
        audit = self.call("GET", "/api/audit", None, "meera")[1]
        self.assertTrue({"submission_created", "fixes_applied", "review_approve"} <= {a["action"] for a in audit})
        self.assertEqual(self.call("GET", "/api/audit", None, "maya")[0], 403)

    def test_clean_content_skips_reviewer(self):
        code, s = self.call("POST", "/api/submissions", {"title": "Clean", "channel": "web", "fmt": "text",
                            "content": "Aurora Sync keeps your files up to date across devices."}, "ravi")
        self.assertEqual(s["findings"], [])
        self.call("POST", f"/api/submissions/{s['id']}/send", {}, "ravi")
        _, done = self.call("POST", f"/api/submissions/{s['id']}/review", {"action": "approve"}, "meera")
        self.assertEqual(done["status"], "approved")

    def test_request_changes_and_new_version(self):
        _, s = self.call("POST", "/api/submissions", {"title": "Edit loop", "channel": "web", "fmt": "text", "content": "Keep your e-mail in sync."}, "ravi")
        self.call("POST", f"/api/submissions/{s['id']}/send", {}, "ravi")
        _, r = self.call("POST", f"/api/submissions/{s['id']}/review", {"action": "request_changes", "comment": "Tighten the headline"}, "meera")
        self.assertEqual(r["status"], "changes_requested")
        code, v = self.call("POST", f"/api/submissions/{s['id']}/versions", {"content": "Keep your email in sync."}, "ravi")
        self.assertEqual((code, v["version"], v["status"]), (201, 2, "checked"))
        self.assertEqual(self.call("POST", f"/api/submissions/{s['id']}/versions", {"content": "Keep your email in sync."}, "ravi")[0], 400)

    def test_image_submission(self):
        from PIL import Image
        b = io.BytesIO(); Image.new("RGB", (1200, 630), (200, 40, 200)).save(b, "PNG")
        img = base64.b64encode(b.getvalue()).decode()
        code, s = self.call("POST", "/api/submissions", {"title": "Banner", "channel": "social", "fmt": "image", "content": "", "image": img}, "maya")
        self.assertEqual(code, 201)
        self.assertTrue({"VIS-PALETTE", "A11Y-ALT-MISSING"} <= rules(s["findings"]))
        with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{self.port}/api/submissions/{s['id']}/image",
                                                           headers={"Authorization": "Bearer " + self.tok["maya"]})) as r:
            self.assertEqual(r.headers["Content-Type"], "image/png")
        self.assertEqual(self.call("POST", "/api/submissions", {"title": "x", "channel": "social", "fmt": "image", "content": "a"}, "maya")[0], 400)

    def test_input_validation_and_xss_safe_json(self):
        self.assertEqual(self.call("POST", "/api/submissions", {"title": "", "content": "x"}, "maya")[0], 400)
        self.assertEqual(self.call("POST", "/api/submissions", {"title": "t", "channel": "nope", "content": "x"}, "maya")[0], 400)
        code, s = self.call("POST", "/api/submissions", {"title": "<script>alert(1)</script>", "content": "hello"}, "maya")
        self.assertEqual(s["title"], "<script>alert(1)</script>")  # stored verbatim; the UI escapes on render
        self.assertEqual(self.call("GET", "/../app/store.py")[0], 404)

    def test_insights(self):
        for i in range(4):
            _, s = self.call("POST", "/api/submissions", {"title": f"Ins {i}", "channel": "web", "fmt": "text", "content": "We use the best tools."}, "maya")
            f = s["findings"][0]
            self.call("POST", f"/api/submissions/{s['id']}/findings/{f['fid']}", {"action": "dismiss", "reason": "We have a source for this"}, "maya")
        ins = self.call("GET", "/api/insights", None, "meera")[1]
        self.assertTrue(any(u["rule_id"] == "CLAIM-SUPERLATIVE" for u in ins["unclear_guidelines"]))
        self.assertTrue(any(r["creator"] == "Maya Iyer" for r in ins["repeated_mistakes"]))
        mine = self.call("GET", "/api/insights", None, "ravi")[1]
        self.assertEqual(mine["scope"], "your pieces")


if __name__ == "__main__":
    unittest.main()
