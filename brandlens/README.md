# BrandLens

Pre-publication brand governance for the Inkloom AI problem statement. A creator submits a draft; BrandLens checks it against an approved brand kit, explains every finding, proposes a fix that keeps the creator's intent, and routes the piece through a manager and (when needed) a legal reviewer before it is marked approved.

The bundled brand kit is **fictional sample data** ("Aurora Cloud"). Replace `data/brand_kit.json` with a real organisation's approved material.

## Run it

```bash
pip install -r requirements.txt        # only Pillow, used for image checks
python3 -m scripts.seed_demo           # optional: loads 9 SAMPLE pieces so Insights has data
python3 -m app.server                  # http://127.0.0.1:8000
python3 -m unittest tests.test_brandlens   # 21 tests
```

Demo accounts (password `demo123`, demo only): `maya`, `ravi` (creators), `meera` (manager), `lena` (legal reviewer).
Set `BRANDLENS_TODAY=2026-10-25` to pin the date used for "expired" claims and facts.

## What it does (Round 2 scope)

| Area | What is built |
| --- | --- |
| Approved knowledge base | `data/brand_kit.json`: voice rules, glossary, approved facts with source and expiry, claim register, required disclosures, confidential terms, palette, channel specs. Shown read-only in the **Brand kit** page. |
| Checkers | Tone and jargon, terminology, fact accuracy (with "over/more than" handled), expired or prohibited or unsupported claims, channel length and required disclosures, confidentiality leaks (codenames, markers, keys, card numbers via Luhn, internal URLs, emails, phones), accessibility (alt text, heading order, link text, WCAG contrast with a computed passing colour, sentence length, reading grade), and **visual** checks on images (palette adherence in Lab colour space, channel aspect ratio, resolution). |
| Explainable results | Every finding has severity, a confidence score, the rule id, and the exact guideline section it comes from. |
| Fix, don't reject | Findings carry a replacement or an addition. The creator can **accept**, **edit** or **dismiss with a reason**, then **apply** fixes to create a new tracked version that is re-checked. Dismissals carry over to the next version. |
| Workflow | Roles: creator, manager, reviewer. Creator sends for review (blocked while high-severity findings are undecided) → manager → legal reviewer when claims, facts or sensitive items remain → approved. Request changes, escalate and reject are supported; nobody reviews their own work; locked once in review. Comments and version history on every piece. |
| Insights | Findings by type, rules by raised/accepted/edited/dismissed, **guidelines to clarify** (rules dismissed or hand-rewritten often), **repeated mistakes** per creator, first-draft clean rate, versions to approval. Creators see only their own. |
| Confidentiality | Per-workspace data scoping, role-based visibility, PBKDF2 password hashing, session expiry, audit log of every decision, no outbound network calls, strict security headers, all user content escaped in the UI. |

## Architecture

`app/checkers.py` (rules, no I/O) → `app/store.py` (SQLite, access control, workflow, insights) → `app/server.py` (JSON API and static files) → `static/` (single-page UI). Standard library plus Pillow only.

## Honest limitations

- **No LLM yet.** Findings come from deterministic rules and colour maths, which keeps results reproducible and explainable, but it cannot judge tone or meaning beyond the rules in the kit. A retrieval-grounded model is the planned next layer (see Round 3).
- Visual checks cover palette, shape and size. There is **no logo detection or font checking** yet.
- Text, HTML and image-plus-caption only. **DOCX, PDF and slide ingestion are not built.**
- Auth is demo-grade (seeded accounts, no rate limiting or password reset) and the SQLite file is not encrypted at rest.
- The UI was tested by running its rendering code against the live API in a simulated DOM, **not in a real browser**. Open it in a browser and check layout and keyboard use before demoing.
- The stack differs from the original plan: FastAPI, React and Postgres were replaced by the standard library, vanilla JS and SQLite because package installs were unavailable while building. Porting is straightforward since the logic is separated from the transport.

## Suggested "three major improvements" for Round 3

Only commit to these if you will deliver them; Round 3 is judged against whatever you name.

1. **Retrieval-grounded LLM review** for tone and meaning, citing kit passages, with a confidence gate that routes weak results to a human.
2. **More formats**: DOCX, PDF and slide-deck ingestion with per-slide findings.
3. **Evaluation report**: a labelled test set with measured precision and recall per rule, published in the repo.

## AI and tool disclosure (for the submission form)

This project was written with Claude (Anthropic) acting as a coding assistant, directed by the participant. Libraries used: Pillow. Everything else is the Python standard library and browser JavaScript. No earlier code or templates were reused.
