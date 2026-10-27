"""Fill a fresh database with SAMPLE pieces so the dashboard has something to show.

All names and content are made up. Run: python3 -m scripts.seed_demo
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.server import seed_users  # noqa: E402
from app.store import Store, ROOT  # noqa: E402

DB = os.environ.get("BRANDLENS_DB", os.path.join(ROOT, "data", "brandlens.db"))

PIECES = [
    ("maya", "Spring launch post", "social", "Meet AuroraSync, the fastest way to share files! Try it free for 30 days."),
    ("maya", "Customer story", "web", "Over 10,000 customers trust Aurora cloud. We guarantee zero downtime."),
    ("maya", "Security page update", "web", "We are SOC 2 Type II certified and 100% secure."),
    ("maya", "Newsletter intro", "email", "Welcome back! Our best release yet is here, and it is game-changing."),
    ("ravi", "Help centre article", "web", "To log in, open the app and enter your e-mail address. Aurora Sync will do the rest."),
    ("ravi", "Reliability blog", "web", "Aurora Sync delivers 99.99% uptime across 14 regions, founded in 2014."),
    ("ravi", "Partner announcement", "press", "Leveraging our unparalleled platform, Project Falcon will reshape the industry."),
    ("ravi", "Clean release note", "web", "Aurora Sync now keeps your files up to date across devices, even on slow networks."),
    ("ravi", "Pricing banner", "web", '<h1>Pricing</h1><h3>Plans</h3><img src="plans.png"><p style="color:#9a9a9a">Start a free trial</p><a href="/p">click here</a>'),
]


def main():
    store = Store(DB)
    seed_users(store)
    if store.db.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]:
        print("Database already has pieces; leaving it alone.")
        return
    users = {r["username"]: dict(r) for r in store.db.execute("SELECT * FROM users")}
    for who, title, channel, content in PIECES:
        u = users[who]
        fmt = "html" if content.startswith("<h1>") else "text"
        s = store.create_submission(u, title, channel, fmt, content)
        sid = s["id"]
        # creators dismiss claim-type findings (they say they have a source) and accept most other fixes
        for f in s["findings"]:
            if f["category"] in ("claim", "confidentiality") and f["severity"] != "low":
                store.decide(u, sid, f["fid"], "dismiss", "We have this on file with the campaign brief.")
            elif f["replacement"] is not None or f.get("append"):
                store.decide(u, sid, f["fid"], "accept")
            elif f["severity"] == "high":
                store.decide(u, sid, f["fid"], "dismiss", "Reviewed with the product team.")
        cur = store.get_submission(u, sid)
        if any(x["decision"] in ("accept", "edit") for x in cur["findings"]):
            cur = store.apply_decisions(u, sid)
            for f in cur["findings"]:
                if f["severity"] == "high" and not f["decision"]:
                    store.decide(u, sid, f["fid"], "dismiss", "Reviewed with the product team.")
        try:
            store.send_for_review(u, sid)
        except Exception as e:  # leave it as a draft if blockers remain
            print("left as draft:", title, "-", e)
    mgr, rev = users["meera"], users["lena"]
    for sid, action, who, comment in [(1, "approve", mgr, ""), (1, "approve", rev, "Cleared for launch."), (8, "approve", mgr, ""),
                                      (5, "request_changes", mgr, "Use sign in, not log in, throughout."), (3, "reject", mgr, "Certification wording cannot go out yet.")]:
        try:
            store.review(who, sid, action, comment)
        except Exception as e:
            print("skip review", sid, action, "-", e)
    print("Seeded", store.db.execute("SELECT COUNT(*) FROM submissions").fetchone()[0], "sample pieces into", DB)


if __name__ == "__main__":
    main()
