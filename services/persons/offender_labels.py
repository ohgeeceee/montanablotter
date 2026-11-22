"""Label the Montana violent/sexual offender registry correctly.

The state registry covers BOTH violent and sexual offenders, and every public
surface must use the registrant's own `offender_type`. A blanket "sex offender"
label published registered violent offenders as sex offenders, which is
defamatory and dangerous for the people named.

One source of truth: `registry_style()` returns the label, heading and CSS
classes for a given offender_type, and is exposed to templates as the Jinja
global `offender_registry_style`. Templates must not hardcode these strings.
"""

from __future__ import annotations

_STYLES = {
    "both": {
        "label": "Registered Violent & Sex Offender",
        "card_title": "Violent & sexual offender registry",
        "pill_cls": "border-purple-300 bg-purple-50 text-purple-700",
        "card_cls": "border-purple-200 bg-purple-50",
        "title_cls": "text-purple-700",
        "name_cls": "text-purple-900",
    },
    "sexual": {
        "label": "Registered Sex Offender",
        "card_title": "Sex offender registry",
        "pill_cls": "border-red-300 bg-red-50 text-red-700",
        "card_cls": "border-red-200 bg-red-50",
        "title_cls": "text-red-700",
        "name_cls": "text-red-900",
    },
    "violent": {
        "label": "Registered Violent Offender",
        "card_title": "Violent offender registry",
        "pill_cls": "border-orange-300 bg-orange-50 text-orange-700",
        "card_cls": "border-orange-200 bg-orange-50",
        "title_cls": "text-orange-700",
        "name_cls": "text-orange-900",
    },
    "unknown": {
        "label": "Registered Offender",
        "card_title": "Offender registry",
        "pill_cls": "border-slate-300 bg-slate-50 text-slate-700",
        "card_cls": "border-slate-200 bg-slate-50",
        "title_cls": "text-slate-700",
        "name_cls": "text-slate-900",
    },
}


def registry_style(offender_type: str | None) -> dict:
    """Return label/heading/CSS classes appropriate to ``offender_type``."""
    text = (offender_type or "").lower()
    is_sexual = "sexual" in text
    is_violent = "violent" in text
    if is_sexual and is_violent:
        key = "both"
    elif is_sexual:
        key = "sexual"
    elif is_violent:
        key = "violent"
    else:
        key = "unknown"
    return dict(_STYLES[key])


def registry_counts(rows) -> dict:
    """Split registry rows into sexual / violent counts (case-insensitive)."""
    sexual = violent = 0
    for row in rows:
        text = ""
        try:
            text = (row["offender_type"] or "").lower()
        except (TypeError, KeyError, IndexError):
            text = str(getattr(row, "offender_type", "") or "").lower()
        if "sexual" in text:
            sexual += 1
        else:
            violent += 1
    return {"sexual": sexual, "violent": violent, "total": sexual + violent}
