"""SQLite storage, roles, versions, review workflow, audit log and insights."""
import base64
import datetime
import hashlib
import json
import os
import secrets
import sqlite3
import threading

from . import checkers

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT_PATH = os.path.join(ROOT, "data", "brand_kit.json")
SESSION_HOURS = 12
MAX_IMAGE_BYTES = 6 * 1024 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, workspace TEXT NOT NULL, username TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL, role TEXT NOT NULL, salt TEXT NOT NULL, pw TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS submissions(
  id INTEGER PRIMARY KEY, workspace TEXT NOT NULL, title TEXT NOT NULL, channel TEXT NOT NULL,
  fmt TEXT NOT NULL, creator_id INTEGER NOT NULL, status TEXT NOT NULL, current_version INTEGER NOT NULL,
  created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS versions(
  id INTEGER PRIMARY KEY, submission_id INTEGER NOT NULL, n INTEGER NOT NULL, content TEXT NOT NULL,
  image BLOB, note TEXT, author_id INTEGER, created TEXT NOT NULL, UNIQUE(submission_id, n));
CREATE TABLE IF NOT EXISTS findings(
  id INTEGER PRIMARY KEY, submission_id INTEGER NOT NULL, version_n INTEGER NOT NULL, fid TEXT NOT NULL,
  data TEXT NOT NULL, carried INTEGER NOT NULL DEFAULT 0,
  decision TEXT, reason TEXT, edited TEXT, decided_by INTEGER, decided_at TEXT,
  UNIQUE(submission_id, version_n, fid));
CREATE TABLE IF NOT EXISTS comments(
  id INTEGER PRIMARY KEY, submission_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  body TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reviews(
  id INTEGER PRIMARY KEY, submission_id INTEGER NOT NULL, version_n INTEGER NOT NULL, user_id INTEGER NOT NULL,
  stage TEXT NOT NULL, action TEXT NOT NULL, comment TEXT, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, workspace TEXT NOT NULL, user_id INTEGER,
  action TEXT NOT NULL, submission_id INTEGER, detail TEXT);
"""


class Forbidden(Exception):
    pass


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 120_000).hex()


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.reload_kit()

    def reload_kit(self):
        with open(KIT_PATH, encoding="utf-8") as fh:
            self.kit = json.load(fh)

    # ------------------------------------------------------------ users
    def add_user(self, username, name, role, password, workspace="aurora"):
        if role not in ("creator", "manager", "reviewer"):
            raise Invalid("Unknown role.")
        salt = secrets.token_hex(16)
        with self.lock:
            self.db.execute("INSERT OR IGNORE INTO users(workspace,username,name,role,salt,pw) VALUES(?,?,?,?,?,?)",
                            (workspace, username, name, role, salt, hash_pw(password, salt)))
            self.db.commit()

    def login(self, username, password):
        with self.lock:
            u = self.db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not u or not secrets.compare_digest(hash_pw(password, u["salt"]), u["pw"]):
                self.audit(None, "login_failed", None, username=username[:40], ws="aurora")
                raise Forbidden("Wrong username or password.")
            token = secrets.token_urlsafe(32)
            exp = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=SESSION_HOURS)).isoformat()
            self.db.execute("INSERT INTO sessions VALUES(?,?,?)", (token, u["id"], exp))
            self.db.commit()
            self.audit(u, "login", None)
            return token, self.public_user(u)

    def user_for_token(self, token):
        if not token:
            return None
        with self.lock:
            row = self.db.execute("SELECT u.*, s.expires FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?", (token,)).fetchone()
            if not row:
                return None
            if datetime.datetime.fromisoformat(row["expires"]) < datetime.datetime.now(datetime.timezone.utc):
                self.db.execute("DELETE FROM sessions WHERE token=?", (token,))
                self.db.commit()
                return None
            return row

    @staticmethod
    def public_user(u):
        return {"id": u["id"], "username": u["username"], "name": u["name"], "role": u["role"]}

    def audit(self, user, action, sub_id, ws=None, **detail):
        wsname = ws or (user["workspace"] if user else "aurora")
        self.db.execute("INSERT INTO audit(ts,workspace,user_id,action,submission_id,detail) VALUES(?,?,?,?,?,?)",
                        (now(), wsname, user["id"] if user else None, action, sub_id, json.dumps(detail)))
        self.db.commit()

    # ------------------------------------------------------ access control
    def _sub(self, user, sub_id, write=False):
        s = self.db.execute("SELECT * FROM submissions WHERE id=? AND workspace=?", (sub_id, user["workspace"])).fetchone()
        if not s:
            raise NotFound("Submission not found.")
        if user["role"] == "creator" and s["creator_id"] != user["id"]:
            raise NotFound("Submission not found.")
        return s

    # ---------------------------------------------------------- checking
    def quick_check(self, user, content, fmt, channel):
        self._validate_input(content, fmt, channel)
        return checkers.analyze(content, fmt, channel, self.kit)

    def _validate_input(self, content, fmt, channel):
        if fmt not in ("text", "html", "image"):
            raise Invalid("Format must be text, html or image.")
        if channel not in self.kit["channels"]:
            raise Invalid("Unknown channel.")
        if not isinstance(content, str) or len(content) > 200_000:
            raise Invalid("Content is missing or too long (limit 200,000 characters).")
        if fmt != "image" and not content.strip():
            raise Invalid("Add some content to check.")

    def _decode_image(self, b64):
        if not b64:
            return None
        if "," in b64[:100]:
            b64 = b64.split(",", 1)[1]
        try:
            data = base64.b64decode(b64, validate=False)
        except Exception:
            raise Invalid("The image could not be decoded.")
        if len(data) > MAX_IMAGE_BYTES:
            raise Invalid("Image is larger than 6 MB.")
        return data

    def create_submission(self, user, title, channel, fmt, content, image_b64=None):
        if user["role"] != "creator" and user["role"] not in ("manager", "reviewer"):
            raise Forbidden("Not allowed.")
        title = (title or "").strip()
        if not title or len(title) > 120:
            raise Invalid("Give the piece a title of up to 120 characters.")
        self._validate_input(content, fmt, channel)
        img = self._decode_image(image_b64)
        if fmt == "image" and not img:
            raise Invalid("Upload an image to check.")
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO submissions(workspace,title,channel,fmt,creator_id,status,current_version,created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                (user["workspace"], title, channel, fmt, user["id"], "checked", 1, now(), now()))
            sid = cur.lastrowid
            self._add_version(sid, 1, content, img, "First check", user["id"], carry_from=None)
            self.audit(user, "submission_created", sid, title=title, channel=channel, fmt=fmt)
            return self.get_submission(user, sid)

    def _add_version(self, sid, n, content, img, note, author_id, carry_from):
        s = self.db.execute("SELECT * FROM submissions WHERE id=?", (sid,)).fetchone()
        self.db.execute("INSERT INTO versions(submission_id,n,content,image,note,author_id,created) VALUES(?,?,?,?,?,?,?)",
                        (sid, n, content, img, note, author_id, now()))
        found = checkers.analyze(content, s["fmt"], s["channel"], self.kit, img)
        carried = {}
        if carry_from:
            for r in self.db.execute("SELECT * FROM findings WHERE submission_id=? AND version_n=? AND decision='dismiss'", (sid, carry_from)):
                d = json.loads(r["data"])
                carried[(d["rule_id"], d["text"])] = r
        for f in found:
            prev = carried.get((f["rule_id"], f["text"]))
            self.db.execute(
                "INSERT INTO findings(submission_id,version_n,fid,data,carried,decision,reason,decided_by,decided_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (sid, n, f["fid"], json.dumps(f), 1 if prev else 0,
                 "dismiss" if prev else None, prev["reason"] if prev else None,
                 prev["decided_by"] if prev else None, prev["decided_at"] if prev else None))
        self.db.execute("UPDATE submissions SET current_version=?, updated=? WHERE id=?", (n, now(), sid))
        self.db.commit()

    # ------------------------------------------------------------ reading
    def _finding_rows(self, sid, n):
        rows = self.db.execute("SELECT * FROM findings WHERE submission_id=? AND version_n=? ORDER BY id", (sid, n)).fetchall()
        out = []
        for r in rows:
            d = json.loads(r["data"])
            d.update({"decision": r["decision"], "reason": r["reason"], "edited": r["edited"],
                      "carried": bool(r["carried"]), "decided_at": r["decided_at"]})
            out.append(d)
        return out

    def _requires_reviewer(self, findings):
        for f in findings:
            if f["decision"] in ("accept", "edit"):
                continue
            if f["needs_reviewer"] or f["confidence"] < 0.6:
                return True
            if f["decision"] == "dismiss" and f["severity"] == "high":
                return True
        return False

    def blockers(self, findings):
        msgs = []
        undecided_high = [f for f in findings if f["severity"] == "high" and not f["decision"]]
        if undecided_high:
            msgs.append(f"{len(undecided_high)} high-severity finding(s) still need a decision.")
        pending = [f for f in findings if f["decision"] in ("accept", "edit")]
        if pending:
            msgs.append(f"{len(pending)} accepted fix(es) are not applied yet. Apply them to create a new version.")
        return msgs

    def get_submission(self, user, sid):
        s = self._sub(user, sid)
        creator = self.db.execute("SELECT name, username FROM users WHERE id=?", (s["creator_id"],)).fetchone()
        n = s["current_version"]
        findings = self._finding_rows(sid, n)
        vers = [{"n": r["n"], "note": r["note"], "created": r["created"],
                 "author": (self.db.execute("SELECT name FROM users WHERE id=?", (r["author_id"],)).fetchone() or {"name": "?"})["name"],
                 "has_image": r["image"] is not None}
                for r in self.db.execute("SELECT n,note,created,author_id,image FROM versions WHERE submission_id=? ORDER BY n", (sid,))]
        cur = self.db.execute("SELECT content FROM versions WHERE submission_id=? AND n=?", (sid, n)).fetchone()
        comments = [{"user": r["name"], "role": r["role"], "body": r["body"], "created": r["created"]} for r in self.db.execute(
            "SELECT c.body,c.created,u.name,u.role FROM comments c JOIN users u ON u.id=c.user_id WHERE c.submission_id=? ORDER BY c.id", (sid,))]
        reviews = [{"user": r["name"], "role": r["role"], "stage": r["stage"], "action": r["action"], "comment": r["comment"],
                    "version": r["version_n"], "created": r["created"]} for r in self.db.execute(
            "SELECT r.*,u.name,u.role FROM reviews r JOIN users u ON u.id=r.user_id WHERE r.submission_id=? ORDER BY r.id", (sid,))]
        return {
            "id": s["id"], "title": s["title"], "channel": s["channel"], "fmt": s["fmt"], "status": s["status"],
            "creator": creator["name"], "creator_id": s["creator_id"], "version": n, "versions": vers,
            "content": cur["content"], "findings": findings, "comments": comments, "reviews": reviews,
            "requires_reviewer": self._requires_reviewer(findings), "blockers": self.blockers(findings),
            "created": s["created"], "updated": s["updated"],
        }

    def get_image(self, user, sid, n=None):
        s = self._sub(user, sid)
        row = self.db.execute("SELECT image FROM versions WHERE submission_id=? AND n=?", (sid, n or s["current_version"])).fetchone()
        if not row or not row["image"]:
            raise NotFound("No image on this version.")
        return row["image"]

    def get_version(self, user, sid, n):
        self._sub(user, sid)
        v = self.db.execute("SELECT content FROM versions WHERE submission_id=? AND n=?", (sid, n)).fetchone()
        if not v:
            raise NotFound("Version not found.")
        return {"n": n, "content": v["content"], "findings": self._finding_rows(sid, n)}

    def list_submissions(self, user, status=None):
        q = "SELECT s.*, u.name AS creator FROM submissions s JOIN users u ON u.id=s.creator_id WHERE s.workspace=?"
        args = [user["workspace"]]
        if user["role"] == "creator":
            q += " AND s.creator_id=?"
            args.append(user["id"])
        if status:
            q += " AND s.status=?"
            args.append(status)
        q += " ORDER BY s.updated DESC"
        out = []
        for r in self.db.execute(q, args):
            fs = self._finding_rows(r["id"], r["current_version"])
            open_ = [f for f in fs if not f["decision"]]
            out.append({"id": r["id"], "title": r["title"], "channel": r["channel"], "fmt": r["fmt"], "status": r["status"],
                        "creator": r["creator"], "version": r["current_version"], "updated": r["updated"],
                        "open_findings": len(open_), "high_open": len([f for f in open_ if f["severity"] == "high"]),
                        "requires_reviewer": self._requires_reviewer(fs)})
        return out

    # ----------------------------------------------------------- decisions
    def decide(self, user, sid, fid, action, reason=None, edited=None):
        with self.lock:
            s = self._sub(user, sid)
            if user["id"] != s["creator_id"]:
                raise Forbidden("Only the creator decides on findings. Reviewers can comment or request changes.")
            if s["status"] not in ("checked", "changes_requested"):
                raise Forbidden("This piece is locked while it is in review or after a final decision.")
            if action not in ("accept", "edit", "dismiss", "undo"):
                raise Invalid("Unknown action.")
            row = self.db.execute("SELECT * FROM findings WHERE submission_id=? AND version_n=? AND fid=?", (sid, s["current_version"], fid)).fetchone()
            if not row:
                raise NotFound("Finding not found.")
            f = json.loads(row["data"])
            reason = (reason or "").strip()
            if action == "accept" and f["replacement"] is None and not f.get("append"):
                raise Invalid("This finding has no automatic fix. Use Edit to write your own, or Dismiss with a reason.")
            if action == "edit":
                if edited is None or f["start"] is None:
                    raise Invalid("Write the replacement text for this span.")
                if len(edited) > 5000:
                    raise Invalid("Replacement is too long.")
            if action == "dismiss" and len(reason) < 5:
                raise Invalid("Say briefly why you are dismissing this (at least 5 characters). Reviewers will see it.")
            if action == "undo":
                self.db.execute("UPDATE findings SET decision=NULL,reason=NULL,edited=NULL,decided_by=NULL,decided_at=NULL WHERE id=?", (row["id"],))
            else:
                self.db.execute("UPDATE findings SET decision=?,reason=?,edited=?,decided_by=?,decided_at=? WHERE id=?",
                                (action, reason or None, edited if action == "edit" else None, user["id"], now(), row["id"]))
            self.db.commit()
            self.audit(user, f"finding_{action}", sid, fid=fid, rule=f["rule_id"], version=s["current_version"])
            return self.get_submission(user, sid)

    def apply_decisions(self, user, sid):
        with self.lock:
            s = self._sub(user, sid)
            if user["id"] != s["creator_id"]:
                raise Forbidden("Only the creator can apply fixes.")
            if s["status"] not in ("checked", "changes_requested"):
                raise Forbidden("This piece is locked while it is in review or after a final decision.")
            n = s["current_version"]
            content = self.db.execute("SELECT content, image FROM versions WHERE submission_id=? AND n=?", (sid, n)).fetchone()
            items = []
            for r in self.db.execute("SELECT * FROM findings WHERE submission_id=? AND version_n=? AND decision IN ('accept','edit')", (sid, n)):
                f = json.loads(r["data"])
                new = r["edited"] if r["decision"] == "edit" else f["replacement"]
                items.append({"start": f["start"], "end": f["end"], "new": new, "append": f.get("append") if r["decision"] == "accept" else None})
            if not items:
                raise Invalid("Nothing to apply. Accept or edit at least one fix first.")
            new_content = checkers.apply_fixes(content["content"], items)
            self._add_version(sid, n + 1, new_content, content["image"], f"Applied {len(items)} fix(es)", user["id"], carry_from=n)
            self.db.execute("UPDATE submissions SET status='checked' WHERE id=?", (sid,))
            self.db.commit()
            self.audit(user, "fixes_applied", sid, from_version=n, count=len(items))
            return self.get_submission(user, sid)

    def new_version(self, user, sid, content, image_b64=None):
        with self.lock:
            s = self._sub(user, sid)
            if user["id"] != s["creator_id"]:
                raise Forbidden("Only the creator can upload a new version.")
            if s["status"] not in ("checked", "changes_requested"):
                raise Forbidden("This piece is locked while it is in review or after a final decision.")
            self._validate_input(content, s["fmt"], s["channel"])
            n = s["current_version"]
            old = self.db.execute("SELECT content, image FROM versions WHERE submission_id=? AND n=?", (sid, n)).fetchone()
            img = self._decode_image(image_b64) or old["image"]
            if content == old["content"] and img == old["image"]:
                raise Invalid("Nothing has changed since the last version.")
            self._add_version(sid, n + 1, content, img, "Edited by creator", user["id"], carry_from=n)
            self.db.execute("UPDATE submissions SET status='checked' WHERE id=?", (sid,))
            self.db.commit()
            self.audit(user, "version_uploaded", sid, version=n + 1)
            return self.get_submission(user, sid)

    # ------------------------------------------------------------ workflow
    def send_for_review(self, user, sid):
        with self.lock:
            s = self._sub(user, sid)
            if user["id"] != s["creator_id"]:
                raise Forbidden("Only the creator can send a piece for review.")
            if s["status"] not in ("checked", "changes_requested"):
                raise Forbidden("This piece is already in review or finished.")
            sub = self.get_submission(user, sid)
            if sub["blockers"]:
                raise Invalid(" ".join(sub["blockers"]))
            self.db.execute("UPDATE submissions SET status='pending_manager', updated=? WHERE id=?", (now(), sid))
            self.db.commit()
            self.audit(user, "sent_for_review", sid, version=s["current_version"], requires_reviewer=sub["requires_reviewer"])
            return self.get_submission(user, sid)

    def review(self, user, sid, action, comment=None):
        with self.lock:
            s = self._sub(user, sid)
            if action not in ("approve", "request_changes", "reject", "escalate"):
                raise Invalid("Unknown action.")
            if user["id"] == s["creator_id"]:
                raise Forbidden("You cannot review your own work.")
            stage = {"pending_manager": "manager", "pending_reviewer": "reviewer"}.get(s["status"])
            if not stage:
                raise Forbidden("This piece is not waiting for a review.")
            if user["role"] != stage:
                raise Forbidden(f"This piece is waiting for a {stage}.")
            comment = (comment or "").strip()
            if action in ("request_changes", "reject", "escalate") and len(comment) < 5:
                raise Invalid("Add a short comment so the creator knows why.")
            if action == "escalate" and stage != "manager":
                raise Invalid("Only a manager can escalate to a reviewer.")
            sub = self.get_submission(user, sid)
            if action == "approve":
                if stage == "manager" and sub["requires_reviewer"]:
                    new = "pending_reviewer"
                else:
                    new = "approved"
            elif action == "escalate":
                new = "pending_reviewer"
            elif action == "request_changes":
                new = "changes_requested"
            else:
                new = "rejected"
            self.db.execute("INSERT INTO reviews(submission_id,version_n,user_id,stage,action,comment,created) VALUES(?,?,?,?,?,?,?)",
                            (sid, s["current_version"], user["id"], stage, action, comment or None, now()))
            self.db.execute("UPDATE submissions SET status=?, updated=? WHERE id=?", (new, now(), sid))
            self.db.commit()
            self.audit(user, f"review_{action}", sid, stage=stage, result=new)
            return self.get_submission(user, sid)

    def add_comment(self, user, sid, body):
        with self.lock:
            self._sub(user, sid)
            body = (body or "").strip()
            if not body or len(body) > 2000:
                raise Invalid("Write a comment of up to 2,000 characters.")
            self.db.execute("INSERT INTO comments(submission_id,user_id,body,created) VALUES(?,?,?,?)", (sid, user["id"], body, now()))
            self.db.commit()
            self.audit(user, "comment", sid)
            return self.get_submission(user, sid)

    def audit_log(self, user, limit=200):
        if user["role"] == "creator":
            raise Forbidden("Audit log is for managers and reviewers.")
        rows = self.db.execute(
            "SELECT a.ts,a.action,a.submission_id,a.detail,u.name FROM audit a LEFT JOIN users u ON u.id=a.user_id WHERE a.workspace=? ORDER BY a.id DESC LIMIT ?",
            (user["workspace"], limit)).fetchall()
        return [{"ts": r["ts"], "action": r["action"], "submission_id": r["submission_id"], "user": r["name"] or "unknown", "detail": json.loads(r["detail"] or "{}")} for r in rows]

    # ------------------------------------------------------------ insights
    def insights(self, user):
        ws = user["workspace"]
        mine_only = user["role"] == "creator"
        subs = self.db.execute("SELECT s.*, u.name AS creator FROM submissions s JOIN users u ON u.id=s.creator_id WHERE s.workspace=?" + (" AND s.creator_id=?" if mine_only else ""),
                               (ws, user["id"]) if mine_only else (ws,)).fetchall()
        ids = [s["id"] for s in subs]
        by_status, first_pass_clean, versions_to_approve = {}, 0, []
        for s in subs:
            by_status[s["status"]] = by_status.get(s["status"], 0) + 1
            v1 = self._finding_rows(s["id"], 1)
            if not [f for f in v1 if f["severity"] == "high"]:
                first_pass_clean += 1
            if s["status"] == "approved":
                versions_to_approve.append(s["current_version"])
        rules, cats, creators, trend = {}, {}, {}, {}
        for s in subs:
            for r in self.db.execute("SELECT * FROM findings WHERE submission_id=? AND carried=0", (s["id"],)):
                d = json.loads(r["data"])
                key = d["rule_id"]
                e = rules.setdefault(key, {"rule_id": key, "category": d["category"], "source": d["source"], "raised": 0,
                                           "accepted": 0, "edited": 0, "dismissed": 0, "undecided": 0})
                e["raised"] += 1
                dec = r["decision"]
                if dec == "accept":
                    e["accepted"] += 1
                elif dec == "edit":
                    e["edited"] += 1
                elif dec == "dismiss":
                    e["dismissed"] += 1
                else:
                    e["undecided"] += 1
                cats[d["category"]] = cats.get(d["category"], 0) + 1
                c = creators.setdefault((s["creator"], key), {"creator": s["creator"], "rule_id": key, "category": d["category"], "count": 0, "pieces": set()})
                c["count"] += 1
                c["pieces"].add(s["id"])
        for s in subs:
            day = s["created"][:10]
            trend[day] = trend.get(day, 0) + 1
        rule_list = sorted(rules.values(), key=lambda e: -e["raised"])
        unclear = []
        for e in rule_list:
            decided = e["accepted"] + e["edited"] + e["dismissed"]
            if decided >= 3 and e["dismissed"] / decided >= 0.5:
                unclear.append({**e, "decided": decided, "rate": round(e["dismissed"] / decided, 2), "kind": "dismissed",
                                "advice": f"People dismiss this rule {e['dismissed']} of {decided} times. Check whether {e['source']} is clear, has examples, and is not too strict."})
            elif decided >= 3 and e["edited"] / decided >= 0.4:
                unclear.append({**e, "decided": decided, "rate": round(e["edited"] / decided, 2), "kind": "edited",
                                "advice": f"The suggested fix is often rewritten by hand ({e['edited']} of {decided}). Improve the approved wording in {e['source']}."})
        repeats = sorted([{**c, "pieces": len(c["pieces"])} for c in creators.values() if c["count"] >= 2 and len(c["pieces"]) >= 2],
                         key=lambda c: -c["count"])[:10]
        if mine_only:
            repeats = [r for r in repeats if r["creator"] == user["name"]]
        return {
            "scope": "your pieces" if mine_only else "everyone in your workspace",
            "totals": {"pieces": len(subs), "by_status": by_status,
                       "first_pass_clean_rate": round(first_pass_clean / len(subs), 2) if subs else None,
                       "avg_versions_to_approval": round(sum(versions_to_approve) / len(versions_to_approve), 1) if versions_to_approve else None,
                       "findings": sum(cats.values())},
            "by_category": cats, "rules": rule_list[:12], "unclear_guidelines": unclear, "repeated_mistakes": repeats,
            "per_day": dict(sorted(trend.items())),
        }

    def kit_summary(self):
        k = self.kit
        return {"brand": k["brand"], "version": k["version"], "palette": k["palette"], "channels": list(k["channels"].keys()),
                "glossary": [{"preferred": g["preferred"], "avoid": g["wrong"], "section": g["section"]} for g in k["glossary"]],
                "facts": [{"label": f["label"], "value": f["display"], "source": f["source"], "valid_until": f["valid_until"]} for f in k["facts"]],
                "claims": [{"id": c["id"], "status": c["status"], "section": c["section"], "note": c["note"]} for c in k["claims"]]}
