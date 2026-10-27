"""Rule-grounded content checkers for BrandLens.

Every finding cites the rule and guideline section it came from, carries a
confidence score, and (where possible) a concrete replacement that keeps the
creator's intent. Nothing here calls an external service.
"""
import base64
import datetime
import io
import math
import os
import re

CATEGORY_PRIORITY = {"confidentiality": 6, "claim": 5, "fact": 5, "accessibility": 4,
                     "visual": 3, "terminology": 3, "messaging": 2, "tone": 1}
SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1}


def today():
    v = os.environ.get("BRANDLENS_TODAY")
    return datetime.date.fromisoformat(v) if v else datetime.date.today()


def make(category, severity, rule_id, message, start=None, end=None, text=None,
         suggestion=None, replacement=None, confidence=0.9, source="",
         needs_reviewer=False, append=None):
    return {
        "category": category, "severity": severity, "rule_id": rule_id,
        "message": message, "start": start, "end": end, "text": text,
        "suggestion": suggestion, "replacement": replacement,
        "confidence": round(confidence, 2), "source": source,
        "needs_reviewer": bool(needs_reviewer), "append": append,
    }


def match_case(src, rep):
    if len(src) > 1 and src.isupper():
        return rep.upper()
    if src[:1].isupper():
        return rep[:1].upper() + rep[1:]
    return rep


def mask_tags(raw):
    """Replace HTML tags with spaces so offsets stay valid for visible-text checks."""
    return re.sub(r"<[^>]*>", lambda m: " " * len(m.group(0)), raw)


# ---------------------------------------------------------------- tone
def check_tone(text, kit):
    out = []
    tone = kit["tone"]
    for h in tone["hype"]:
        for m in re.finditer(r"(?<![\w-])" + re.escape(h["term"]) + r"(?![\w-])", text, re.I):
            rep = match_case(m.group(0), h["replace"])
            kind = "jargon" if h["rule"] == "TONE-JARGON" else "hype language"
            out.append(make(
                "tone", "low", h["rule"],
                f'"{m.group(0)}" reads as {kind}. Our voice is confident and plain: show the benefit instead of the adjective.',
                m.start(), m.end(), m.group(0),
                suggestion=f'Use "{rep}", or describe the specific benefit.', replacement=rep,
                confidence=0.85, source=h["section"]))
    bangs = [m for m in re.finditer(r"!", text)]
    mx = tone["max_exclamations"]
    for m in bangs[mx:]:
        out.append(make(
            "tone", "low", "TONE-EXCLAIM",
            f"More than {mx} exclamation mark in one piece makes the voice feel shouty.",
            m.start(), m.end(), "!", suggestion="End the sentence with a full stop.",
            replacement=".", confidence=0.8, source="Voice §1.3 Calm punctuation"))
    allow = {a.upper() for a in tone["acronyms"]}
    for m in re.finditer(r"\b[A-Z]{4,}\b", text):
        if m.group(0) in allow:
            continue
        out.append(make(
            "tone", "low", "TONE-CAPS",
            "All-caps words read as shouting and are harder to read.",
            m.start(), m.end(), m.group(0), suggestion="Use normal capitalisation.",
            replacement=m.group(0).lower(), confidence=0.8, source="Voice §1.3 Calm punctuation"))
    return out


# --------------------------------------------------------- terminology
def check_terminology(text, kit):
    out = []
    for g in kit["glossary"]:
        flags = 0 if g.get("case_sensitive", True) else re.I
        for w in g["wrong"]:
            for m in re.finditer(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", text, flags):
                if m.group(0) == g["preferred"]:
                    continue
                rep = g["preferred"] if g.get("case_sensitive", True) else match_case(m.group(0), g["preferred"])
                out.append(make(
                    "terminology", g.get("severity", "medium"), g["id"],
                    f'"{m.group(0)}" is not the approved term. {g["note"]}',
                    m.start(), m.end(), m.group(0), suggestion=f'Write "{rep}".',
                    replacement=rep, confidence=0.95, source=g["section"]))
    return out


# ---------------------------------------------------- facts and claims
def _num(s):
    s = s.strip().replace(",", "").replace(" ", "")
    mult = 1
    if s[-1:] in "kK":
        mult, s = 1000, s[:-1]
    return float(s) * mult


def check_facts(text, kit):
    out = []
    t = today()
    for f in kit["facts"]:
        expired = datetime.date.fromisoformat(f["valid_until"]) < t
        for m in re.finditer(f["pattern"], text, re.I):
            try:
                stated = _num(m.group("v"))
            except (ValueError, IndexError):
                continue
            window = text[max(0, m.start() - 14):m.start("v")].lower()
            lower_bound = bool(re.search(r"(over|more than|at least|\bnearly\b|\bjoin)\s*$", window)) or text[m.end("v"):m.end("v") + 1] == "+" or text[m.end():m.end() + 1] == "+"
            s, e = m.start("v"), m.end("v")
            if expired:
                out.append(make(
                    "fact", "high", f["id"],
                    f'The approved source for {f["label"].lower()} ({f["source"]}) expired on {f["valid_until"]}. The figure "{m.group(0)}" cannot be confirmed.',
                    m.start(), m.end(), m.group(0),
                    suggestion="Ask the owner of the source to re-confirm the number before publishing.",
                    confidence=0.9, source=f["section"], needs_reviewer=True))
                continue
            if lower_bound and stated <= f["value"]:
                continue
            if abs(stated - f["value"]) > f["tolerance"]:
                out.append(make(
                    "fact", "high", f["id"],
                    f'{f["label"]} is stated as {m.group("v").strip()}, but the approved figure is {f["display"]} ({f["source"]}).',
                    s, e, m.group("v"),
                    suggestion=f'Use the approved figure {f["display"]}.', replacement=f["display"],
                    confidence=0.95, source=f["section"], needs_reviewer=True))
    return out


def check_claims(text, kit):
    out = []
    t = today()
    for c in kit["claims"]:
        for m in re.finditer(c["trigger"], text, re.I if c["id"] != "CLAIM-COMPETITOR" else 0):
            hit = m.group(0)
            reps = c.get("replacements", {})
            rep = reps.get(hit.lower())
            if rep:
                rep = match_case(hit, rep)
            if c["status"] == "expired":
                if datetime.date.fromisoformat(c["valid_until"]) >= t:
                    continue
                out.append(make(
                    "claim", "high", c["id"],
                    f'"{hit}" is an outdated claim. {c["note"]}',
                    m.start(), m.end(), hit,
                    suggestion=f'Use the approved wording: "{c["approved_wording"]}".',
                    replacement=c["approved_wording"], confidence=0.9, source=c["section"],
                    needs_reviewer=True))
            elif c["status"] == "prohibited":
                out.append(make(
                    "claim", "high", c["id"],
                    f'"{hit}" is an absolute promise. {c["note"]}',
                    m.start(), m.end(), hit,
                    suggestion=(f'Say "{rep}" instead.' if rep else "Rewrite without an absolute promise."),
                    replacement=rep, confidence=0.9 if rep else 0.7, source=c["section"],
                    needs_reviewer=True))
            else:
                out.append(make(
                    "claim", "medium", c["id"],
                    f'"{hit}" is a promotional claim with no source on file. {c["note"]}',
                    m.start(), m.end(), hit,
                    suggestion=(f'Say "{rep}", or attach evidence and send for review.' if rep
                                else "Attach a dated source, or reword without the comparison."),
                    replacement=rep, confidence=0.75 if rep else 0.65, source=c["section"],
                    needs_reviewer=True))
    return out


# ------------------------------------------------------------ messaging
def check_messaging(text, channel, kit):
    out = []
    ch = kit["channels"].get(channel, {})
    mx = ch.get("max_chars")
    if mx and len(text) > mx:
        over = len(text) - mx
        out.append(make(
            "messaging", "medium", "MSG-LENGTH",
            f"This is {len(text)} characters; the {channel} limit is {mx}.",
            mx, len(text), text[mx:],
            suggestion=f"Trim about {over} characters, or move detail to a linked page.",
            confidence=0.99, source="Messaging §5.1 Channel limits"))
    for r in kit["requirements"]:
        if r["channels"] and channel not in r["channels"]:
            continue
        if r["when"]:
            m = re.search(r["when"], text, re.I)
            if not m:
                continue
            span = (m.start(), m.end(), m.group(0))
        else:
            span = (None, None, None)
        if not re.search(r["require"], text, re.I):
            out.append(make(
                "messaging", "medium", r["id"], r["message"], span[0], span[1], span[2],
                suggestion=f'Add: "{r["add"]}"', append=r["add"],
                confidence=0.9, source=r["section"]))
    return out


# --------------------------------------------------------- confidentiality
def _luhn(num):
    digits = [int(d) for d in re.sub(r"\D", "", num)]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def check_confidentiality(text, kit):
    out = []
    conf = kit["confidential"]
    for term in conf["terms"]:
        for m in re.finditer(re.escape(term), text, re.I):
            out.append(make(
                "confidentiality", "high", "CONF-CODENAME",
                f'"{m.group(0)}" is an internal codename or unreleased topic and must not appear in public content.',
                m.start(), m.end(), m.group(0),
                suggestion="Remove it, or describe the work without naming it.",
                replacement=None, confidence=0.95, source=conf["section"], needs_reviewer=True))
    for m in re.finditer(conf["markers"], text, re.I):
        out.append(make(
            "confidentiality", "high", "CONF-MARKER",
            f'The text "{m.group(0)}" suggests this material is not meant for external release.',
            m.start(), m.end(), m.group(0),
            suggestion="Confirm the content is cleared for release, then remove the marker.",
            confidence=0.8, source=conf["section"], needs_reviewer=True))
    secrets = [
        (r"\b(?:sk|pk)-[A-Za-z0-9]{16,}\b", "an API key"),
        (r"\bAKIA[0-9A-Z]{16}\b", "a cloud access key"),
        (r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", "a code-hosting access token"),
    ]
    for pat, label in secrets:
        for m in re.finditer(pat, text):
            out.append(make(
                "confidentiality", "high", "CONF-SECRET",
                f"This looks like {label}. Publishing it would expose a credential.",
                m.start(), m.end(), m.group(0), suggestion="Remove it and rotate the credential.",
                replacement="[removed]", confidence=0.9, source=conf["section"], needs_reviewer=True))
    for m in re.finditer(r"\b(?:\d[ -]?){13,19}\b", text):
        if _luhn(m.group(0)):
            out.append(make(
                "confidentiality", "high", "CONF-CARD",
                "This looks like a payment card number.", m.start(), m.end(), m.group(0),
                suggestion="Remove it.", replacement="[removed]", confidence=0.85,
                source=conf["section"], needs_reviewer=True))
    for m in re.finditer(r"https?://[\w.-]*\.(?:internal|corp|local|intranet)\b[^\s<>\"']*", text, re.I):
        out.append(make(
            "confidentiality", "high", "CONF-INTERNAL-URL",
            "This link points to an internal system that outsiders cannot open.",
            m.start(), m.end(), m.group(0), suggestion="Link to the public page instead.",
            confidence=0.9, source=conf["section"], needs_reviewer=True))
    for m in re.finditer(r"[\w.+-]+@[\w-]+\.[\w.-]+", text):
        out.append(make(
            "confidentiality", "medium", "CONF-EMAIL",
            "A personal or internal email address is in the text. Public content should use a shared address.",
            m.start(), m.end(), m.group(0),
            suggestion="Use the shared contact address (for example, hello@ your domain).",
            confidence=0.7, source=conf["section"]))
    for m in re.finditer(r"(?<![\w.])(?:\+?\d{1,3}[ -]?)?(?:\(?\d{3,5}\)?[ -]?)\d{3}[ -]?\d{4}(?!\d)", text):
        digits = re.sub(r"\D", "", m.group(0))
        if 10 <= len(digits) <= 13 and not _luhn(m.group(0)) and not re.fullmatch(r"(19|20)\d{2}", digits):
            out.append(make(
                "confidentiality", "medium", "CONF-PHONE",
                "A direct phone number is in the text. Check it is meant to be public.",
                m.start(), m.end(), m.group(0), suggestion="Use the main support number or remove it.",
                confidence=0.6, source=conf["section"]))
    return out


# --------------------------------------------------------- accessibility
def _hex_to_rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lum(rgb):
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a, b):
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def fix_color(fg, bg, target=4.5):
    """Move fg toward black (or white on dark backgrounds) until it reaches the target ratio."""
    goal = (0, 0, 0) if _lum(bg) > 0.4 else (255, 255, 255)
    for i in range(0, 101, 2):
        t = i / 100
        cand = tuple(round(fg[k] + (goal[k] - fg[k]) * t) for k in range(3))
        if contrast_ratio(cand, bg) >= target:
            return "#%02x%02x%02x" % cand
    return "#%02x%02x%02x" % goal


def _syllables(word):
    word = word.lower()
    word = re.sub(r"[^a-z]", "", word)
    if not word:
        return 0
    groups = re.findall(r"[aeiouy]+", word)
    n = len(groups)
    if word.endswith("e") and not word.endswith("le") and n > 1:
        n -= 1
    return max(1, n)


def reading_grade(text):
    sentences = [s for s in re.split(r"[.!?]+\s+|\n+", text.strip()) if s.strip()]
    words = re.findall(r"[A-Za-z']+", text)
    if not sentences or not words:
        return 0.0
    syl = sum(_syllables(w) for w in words)
    return 0.39 * (len(words) / len(sentences)) + 11.8 * (syl / len(words)) - 15.59


def check_accessibility(raw, fmt, kit):
    out = []
    tone = kit["tone"]
    visible = mask_tags(raw) if fmt == "html" else raw
    if fmt == "html":
        for m in re.finditer(r"<img\b[^>]*>", raw, re.I):
            tag = m.group(0)
            alt = re.search(r"\balt\s*=\s*(\"([^\"]*)\"|'([^']*)')", tag, re.I)
            if not alt:
                closing = "/>" if tag.rstrip().endswith("/>") else ">"
                fixed = tag.rstrip()[:-len(closing)].rstrip() + ' alt="[Describe this image]"' + closing
                out.append(make(
                    "accessibility", "high", "A11Y-ALT-MISSING",
                    "This image has no alt text, so screen reader users get no description. (WCAG 1.1.1)",
                    m.start(), m.end(), tag,
                    suggestion="Add a short description of what the image shows. The placeholder must be replaced by a person.",
                    replacement=fixed, confidence=0.5, source="Accessibility §7.1 Images", needs_reviewer=False))
            else:
                val = (alt.group(2) if alt.group(2) is not None else alt.group(3)).strip()
                if val and (re.fullmatch(r"(img|image|photo|picture|graphic|banner)[\w-]*(\.\w{3,4})?", val, re.I) or re.search(r"\.(png|jpe?g|gif|webp|svg)$", val, re.I)):
                    out.append(make(
                        "accessibility", "medium", "A11Y-ALT-WEAK",
                        f'The alt text "{val}" does not describe the image.',
                        m.start(), m.end(), tag,
                        suggestion="Describe what the image shows and why it matters on the page.",
                        confidence=0.8, source="Accessibility §7.1 Images"))
        last = 0
        for m in re.finditer(r"<h([1-6])\b", raw, re.I):
            lvl = int(m.group(1))
            if last and lvl > last + 1:
                out.append(make(
                    "accessibility", "medium", "A11Y-HEADING-ORDER",
                    f"Heading jumps from level {last} to level {lvl}. Skipped levels confuse screen reader navigation. (WCAG 1.3.1)",
                    m.start(), m.end(), m.group(0),
                    suggestion=f"Use a level {last + 1} heading here.", confidence=0.85,
                    source="Accessibility §7.3 Structure"))
            last = lvl
        for m in re.finditer(r"<a\b[^>]*>(.*?)</a>", raw, re.I | re.S):
            label = re.sub(r"<[^>]*>", "", m.group(1)).strip()
            if re.fullmatch(r"(click here|here|read more|learn more|more|link|this)", label, re.I):
                out.append(make(
                    "accessibility", "medium", "A11Y-LINK-TEXT",
                    f'The link text "{label}" means nothing out of context. (WCAG 2.4.4)',
                    m.start(), m.end(), m.group(0),
                    suggestion="Name the destination, for example \"Read the Aurora Sync setup guide\".",
                    confidence=0.85, source="Accessibility §7.4 Links"))
        for m in re.finditer(r"<[^>]+\bstyle\s*=\s*\"([^\"]*)\"[^>]*>", raw, re.I):
            style = m.group(1)
            base = m.start(1)
            fg = re.search(r"(?<![-\w])color\s*:\s*(#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?)\b", style)
            if not fg:
                continue
            bgm = re.search(r"background(?:-color)?\s*:\s*(#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?)\b", style)
            bg_rgb = _hex_to_rgb(bgm.group(1)) if bgm else (255, 255, 255)
            fg_rgb = _hex_to_rgb(fg.group(1))
            ratio = contrast_ratio(fg_rgb, bg_rgb)
            if ratio < 4.5:
                s, e = base + fg.start(1), base + fg.end(1)
                new = fix_color(fg_rgb, bg_rgb)
                note = "" if bgm else " (assuming a white background, since none is set here)"
                out.append(make(
                    "accessibility", "high" if ratio < 3 else "medium", "A11Y-CONTRAST",
                    f"Text colour {fg.group(1)} on {bgm.group(1) if bgm else '#ffffff'} has a contrast ratio of {ratio:.1f}:1{note}. Body text needs at least 4.5:1. (WCAG 1.4.3)",
                    s, e, fg.group(1), suggestion=f"Use {new}, the nearest colour that passes.",
                    replacement=new, confidence=0.9 if bgm else 0.75, source="Accessibility §7.2 Colour contrast"))
    # reading level and sentence length run on visible text
    if fmt == "text":
        pos = 0
        for sent in re.finditer(r"[^.!?\n]+[.!?]*", raw):
            words = re.findall(r"[A-Za-z0-9'’-]+", sent.group(0))
            if len(words) > tone["max_sentence_words"]:
                out.append(make(
                    "accessibility", "low", "A11Y-LONG-SENTENCE",
                    f"This sentence has {len(words)} words. Sentences over {tone['max_sentence_words']} words are hard to follow.",
                    sent.start(), sent.end(), sent.group(0).strip(),
                    suggestion="Split it into two sentences at the natural pause.",
                    confidence=0.85, source="Accessibility §7.5 Plain language"))
        for m in re.finditer(r"\bclick here\b", raw, re.I):
            out.append(make(
                "accessibility", "medium", "A11Y-LINK-TEXT",
                '"Click here" means nothing out of context for screen reader users. (WCAG 2.4.4)',
                m.start(), m.end(), m.group(0), suggestion="Name the destination in the link text.",
                confidence=0.85, source="Accessibility §7.4 Links"))
    plain = re.sub(r"\s+", " ", visible).strip()
    if len(re.findall(r"[A-Za-z']+", plain)) >= 25:
        g = reading_grade(plain)
        if g > tone["target_grade"] + 2:
            out.append(make(
                "accessibility", "low", "A11Y-READING-LEVEL",
                f"Reading level is about grade {g:.0f}; our target is grade {tone['target_grade']} or lower. Shorter sentences and everyday words will help.",
                suggestion="Shorten the longest sentences and replace abstract nouns with verbs.",
                confidence=0.7, source="Accessibility §7.5 Plain language"))
    return out


# ---------------------------------------------------------------- visual
def _srgb_to_lab(rgb):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = (0.2126 * r + 0.7152 * g + 0.0722 * b)
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def _de(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def check_image(img_bytes, channel, kit):
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        return [make("visual", "low", "VIS-UNAVAILABLE", "Image checks need the Pillow library.", confidence=1.0, source="n/a")]
    out = []
    try:
        im = Image.open(io.BytesIO(img_bytes))
        im.load()
    except Exception:
        return [make("visual", "high", "VIS-UNREADABLE", "This file could not be read as an image. Upload a PNG or JPEG.", confidence=1.0, source="Visual §8.0")]
    w, h = im.size
    want = kit["channels"].get(channel, {}).get("image")
    if want:
        if abs(w / h - want["w"] / want["h"]) > 0.02:
            out.append(make(
                "visual", "medium", "VIS-ASPECT",
                f"The image is {w}×{h}, but {channel} images use a {want['w']}:{want['h']} shape.",
                suggestion=f"Crop or export at {want['w']}×{want['h']}.", confidence=0.95,
                source="Visual §8.2 Sizes"))
        elif w < want["w"] // 2:
            out.append(make(
                "visual", "low", "VIS-RESOLUTION",
                f"The image is {w}px wide, which will look soft on high-density screens (target {want['w']}px).",
                suggestion="Export a larger version.", confidence=0.9, source="Visual §8.2 Sizes"))
    small = im.convert("RGB").resize((96, 96))
    raw_px = small.tobytes()
    px = [tuple(raw_px[i:i + 3]) for i in range(0, len(raw_px), 3)]
    pal = [(p["name"], p["hex"], _srgb_to_lab(_hex_to_rgb(p["hex"]))) for p in kit["palette"]]
    off, buckets = 0, {}
    for p in px:
        lab = _srgb_to_lab(p)
        chroma = math.hypot(lab[1], lab[2])
        if chroma < 14:           # greys, white and black are always allowed
            continue
        d = min(_de(lab, pl[2]) for pl in pal)
        if d > 28:
            off += 1
            key = tuple((c // 32) * 32 + 16 for c in p)
            buckets[key] = buckets.get(key, 0) + 1
    share = off / len(px)
    if share > 0.12:
        top = sorted(buckets.items(), key=lambda kv: -kv[1])[:3]
        names = ", ".join("#%02x%02x%02x" % c for c, _ in top)
        nearest = []
        for c, _ in top[:2]:
            lab = _srgb_to_lab(c)
            n = min(pal, key=lambda pl: _de(lab, pl[2]))
            nearest.append(f"{n[0]} {n[1]}")
        out.append(make(
            "visual", "high" if share > 0.35 else "medium", "VIS-PALETTE",
            f"About {share:.0%} of the image uses colours outside the approved palette (mainly {names}).",
            suggestion="Recolour those areas with the nearest approved colours: " + ", ".join(nearest) + ".",
            confidence=0.8, source="Visual §8.1 Colour palette"))
    return out


# ------------------------------------------------------------ orchestrate
def _resolve_overlaps(findings):
    spans = [f for f in findings if f["start"] is not None]
    rest = [f for f in findings if f["start"] is None]
    spans.sort(key=lambda f: (-SEVERITY_ORDER[f["severity"]], -CATEGORY_PRIORITY.get(f["category"], 0), f["start"]))
    kept = []
    for f in spans:
        if any(f["start"] < k["end"] and k["start"] < f["end"] for k in kept):
            continue
        kept.append(f)
    kept.sort(key=lambda f: f["start"])
    return kept + rest


def analyze(content, fmt="text", channel="web", kit=None, image_bytes=None):
    """Run every relevant checker and return findings with stable ids f1, f2, ..."""
    findings = []
    visible = mask_tags(content) if fmt == "html" else content
    if fmt in ("text", "html", "image"):
        findings += check_tone(visible, kit)
        findings += check_terminology(visible, kit)
        findings += check_facts(visible, kit)
        findings += check_claims(visible, kit)
        findings += check_confidentiality(visible, kit)
        findings += check_messaging(visible, channel, kit)
        if fmt in ("text", "html"):
            findings += check_accessibility(content, fmt, kit)
    if fmt == "image":
        if image_bytes:
            findings += check_image(image_bytes, channel, kit)
        if not content.strip():
            findings.append(make(
                "accessibility", "high", "A11Y-ALT-MISSING",
                "No alt text or caption was provided for this image. (WCAG 1.1.1)",
                suggestion="Describe what the image shows in one sentence.", confidence=0.95,
                source="Accessibility §7.1 Images"))
    findings = _resolve_overlaps(findings)
    for i, f in enumerate(findings, 1):
        f["fid"] = f"f{i}"
    return findings


def apply_fixes(content, items):
    """items: list of dicts with start, end, new (replacement text) or append."""
    text = content
    appends = []
    for it in sorted([i for i in items if i.get("start") is not None and i.get("new") is not None],
                     key=lambda i: -i["start"]):
        text = text[:it["start"]] + it["new"] + text[it["end"]:]
    for it in items:
        if it.get("append") and it["append"] not in appends:
            appends.append(it["append"])
    for a in appends:
        if a.lower() not in text.lower():
            text = text.rstrip() + (" " if not text.endswith("\n") else "") + a
    return text
