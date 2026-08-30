"""Humor scoring and PII redaction for the public /funniest blotter feed.

Philosophy: surface the absurd, low-stakes side of small-town Montana policing
(loose livestock, lawn gnomes, inflatable mishaps) without ever mocking victims.
Records tied to sensitive incident types are excluded entirely, and every field
is run through the existing PII auditor before it can be shown.
"""
from __future__ import annotations

import re

from services.blotter.auditor import get_pii_spans

# Incident types that must never appear in a "funniest" feed.
DENY_INCIDENT_TYPES = {
    "domestic_violence",
    "sexual_assault",
    "assault",
    "homicide",
    "suicide",
    "missing_person",
    "child_related",
    "sex_offense",
}

# Keyword -> point contributions. Low-stakes, absurd, non-victim signals.
FUNNY_KEYWORDS = {
    "goat": 3, "cow": 2, "sheep": 2, "chicken": 2, "duck": 2, "pig": 2,
    "goose": 3, "turkey": 2, "seagull": 2, "crow": 1, "woodpecker": 2,
    "beaver": 2, "porcupine": 2, "possum": 2, "raccoon": 2, "moose": 2,
    "bear": 1, "snake": 2, "elk": 1, "deer": 1, "stuck": 3, "wedged": 3,
    "trapped": 3, "couch": 2, "lawn gnome": 4, "gnome": 3, "inflatable": 3,
    "snowman": 3, "mailbox": 2, "toilet": 3, "lawnmower": 2, "go-cart": 2,
    "golf cart": 2, "bicycle": 1, "unicycle": 4, "scooter": 1, "trombone": 4,
    "accordion": 4, "kazoo": 4, "bagpipe": 4, "clown": 4, "pirate": 3,
    "dinosaur": 4, "mannequin": 3, "dummy": 2, "rubber": 2, "borrowed": 2,
    "stolen lawn": 4, "watermelon": 2, "pumpkin": 2, "naked": 3, "birthday": 2,
    "mall cop": 3, "hoa": 3, "neighbor": 1, "kayak": 2, "canoe": 2,
    "hoverboard": 3, "suspicious": 1, "banned": 1, "fake": 1,
}

# Higher-value phrase patterns for absurdist combos.
FUNNY_PATTERNS = [
    (re.compile(r"\b(stuck|wedged|trapped)\b", re.I), 3),
    (re.compile(r"\b(chasing|chased)\b[^\.]*\b(goat|goose|cow|pig|chicken|duck|turkey)\b", re.I), 4),
    (re.compile(r"\b(loud|noisy)\b[^\.]*\b(trombone|accordion|bagpipe|kazoo|singing)\b", re.I), 4),
    (re.compile(r"\b(naked|partially clothed)\b", re.I), 3),
    (re.compile(r"\b(lawn gnome|lawn gnomes|garden gnome)\b", re.I), 4),
    (re.compile(r"\b(inflatable|pool float|kiddie pool)\b", re.I), 3),
    (re.compile(r"\b(mailbox|mailboxes)\b", re.I), 2),
    (re.compile(r"\b(suspicious (watermelon|pumpkin|snowman|gnome|goose|banana))\b", re.I), 5),
]

MIN_TEXT_LEN = 8          # ignore empty / junk rows
MAX_SCORE = 25.0          # cap so one row can't dominate the feed
SHORT_BONUS_THRESHOLD = 80


def is_eligible(incident_type: str | None, incident: str = "", details: str = "") -> bool:
    """True if a record is allowed in the humor feed."""
    it = (incident_type or "").strip().lower()
    if it in DENY_INCIDENT_TYPES:
        return False
    text = f"{incident} {details}".strip()
    return len(text) >= MIN_TEXT_LEN


def score_humor(incident: str = "", details: str = "", incident_type: str | None = None) -> float:
    """Return a non-negative humor score for a record (0 when ineligible)."""
    if not is_eligible(incident_type, incident, details):
        return 0.0
    text = f"{incident} {details}".lower()
    score = 0.0
    for kw, pts in FUNNY_KEYWORDS.items():
        if kw in text:
            score += pts
    for pat, pts in FUNNY_PATTERNS:
        if pat.search(text):
            score += pts
    # Mild bonus for short, punchy entries (the good kind of blotter line).
    if MIN_TEXT_LEN <= len(text) <= SHORT_BONUS_THRESHOLD:
        score += 1.0
    return round(min(score, MAX_SCORE), 2)


def redact_text(text: str) -> str:
    """Mask PII in ``text`` using the existing auditor spans.

    HIGH-severity matches collapse to ``[redacted]``; lower severity keeps the
    last two characters masked. Returns the original string when nothing matches.
    """
    if not text:
        return text
    spans = get_pii_spans(text)
    if not spans:
        return text
    out: list[str] = []
    last = 0
    for span in spans:
        start, end = span["start"], span["end"]
        out.append(text[last:start])
        matched = text[start:end]
        if span.get("severity") == "high":
            out.append("[redacted]")
        else:
            keep = 2
            out.append("*" * max(len(matched) - keep, 3) + matched[-keep:])
        last = end
    out.append(text[last:])
    return "".join(out)
