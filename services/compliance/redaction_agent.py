#!/usr/bin/env python3
"""
services/compliance/redaction_agent.py
=======================================
Public records compliance agent for montanablotter.com.

Applies redactions per Montana Code Annotated Title 44, Chapter 5:
  1. Juvenile names → "Juvenile Suspect / Victim / Witness"
  2. Victim / witness / reporting-party names → descriptive titles
  3. Exact addresses → block-level (e.g. "1200 Block of Central Ave")
  4. PFMA / sexual-offense suspect names (if co-habitating victim exposure)
  5. Narrative text synced to all substitutions
  6. Sets redaction_applied: true if any field was altered

Architecture: Hybrid ReAct + typed tool execution.
  - Claude reasons over the full record to identify what needs redacting.
  - Each redaction is applied via atomic apply_redaction tool calls.
  - finalize_record closes the loop and stamps the metadata field.

CLI:
  python -m services.compliance.redaction_agent record.json
  echo '{...}' | python -m services.compliance.redaction_agent
  python -m services.compliance.redaction_agent --test
"""

import copy
import json
import logging
import sys
from typing import Any

import anthropic
import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt — kept minimal and invariant per context-budgeting guidance.
# Detailed protocol rules are inlined here because they are always needed;
# they do not grow with conversation history.
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a public records compliance officer for montanablotter.com.
Examine the incoming JSON blotter record and apply all necessary redactions under
Montana Code Annotated Title 44, Chapter 5.

Redaction Protocols — apply ALL that are triggered:

1. JUVENILES: Any person identified as a minor or under 18 → replace their name with
   "Juvenile Suspect", "Juvenile Victim", or "Juvenile Witness" (pick the correct role).

2. VICTIM CONFIDENTIALITY: Victims, witnesses, and reporting parties → replace with
   "Victim 1", "Victim 2", "Witness 1", "Reporting Party", etc. Number sequentially if multiple.

3. GEOGRAPHIC BLURRING: Never expose an exact house number or apartment number.
   Blur to block level: "1245 Central Ave" → "1200 Block of Central Ave".
   Round down to the nearest hundred. Remove apartment/unit numbers entirely.

4. CRIME SENSITIVITY: For PFMA (Partner/Family Member Assault) or sexual offense charges,
   redact the adult suspect name if it would readily reveal the co-habitating victim's identity.

5. NARRATIVE SYNC: After applying protocols 1–4, scan the narrative/story/description field
   and replace every occurrence of the original values with the redacted replacements.
   One apply_redaction call per substitution per field.

6. METADATA: When all redactions are done, call finalize_record with redaction_applied=true
   if you changed anything, false if the record was already clean.

Rules:
- Call apply_redaction once per (field, original_value) pair — do not batch multiple substitutions.
- Always call finalize_record at the end, even for a clean record.
- Do not output any text — only tool calls.
"""

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

_TOOLS: list[dict] = [
    {
        "name": "apply_redaction",
        "description": (
            "Apply one redaction: replace original_value with redacted_value inside the "
            "specified field. Call once per field per original string."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "field_path": {
                    "type": "string",
                    "description": (
                        "Dot-notation path to the target field "
                        "(e.g. 'suspect_name', 'address', 'narrative', 'victims.0.name')."
                    ),
                },
                "original_value": {
                    "type": "string",
                    "description": "Exact substring to replace.",
                },
                "redacted_value": {
                    "type": "string",
                    "description": "Replacement string.",
                },
                "protocol": {
                    "type": "string",
                    "enum": [
                        "juvenile",
                        "victim_confidentiality",
                        "geographic_blur",
                        "crime_sensitivity",
                        "narrative_sync",
                    ],
                    "description": "Which protocol triggered this redaction.",
                },
            },
            "required": ["field_path", "original_value", "redacted_value", "protocol"],
        },
    },
    {
        "name": "finalize_record",
        "description": "Close the redaction pass. Must be called exactly once, at the end.",
        "input_schema": {
            "type": "object",
            "properties": {
                "redaction_applied": {
                    "type": "boolean",
                    "description": "True if any field was altered; false if the record was clean.",
                },
            },
            "required": ["redaction_applied"],
        },
    },
]

# ---------------------------------------------------------------------------
# Field mutation helpers
# ---------------------------------------------------------------------------

def _get_nested(obj: Any, parts: list[str]) -> Any:
    for part in parts:
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list):
            try:
                obj = obj[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return obj


def _set_nested(obj: Any, parts: list[str], old_val: str, new_val: str) -> bool:
    """Replace old_val with new_val at the leaf field. Returns True if changed."""
    if not parts:
        return False
    target = _get_nested(obj, parts[:-1])
    key = parts[-1]
    if isinstance(target, dict) and key in target:
        val = target[key]
        if isinstance(val, str) and old_val in val:
            target[key] = val.replace(old_val, new_val)
            return True
    elif isinstance(target, list):
        try:
            idx = int(key)
            val = target[idx]
            if isinstance(val, str) and old_val in val:
                target[idx] = val.replace(old_val, new_val)
                return True
        except (ValueError, IndexError):
            pass
    return False


# ---------------------------------------------------------------------------
# Core agent loop
# ---------------------------------------------------------------------------

def redact_record(record: dict) -> dict:
    """
    Run the compliance agent on a single blotter record.
    Returns a new dict with all required redactions applied.
    """
    if not getattr(config, "USE_PAID_LLM", False):
        logger.warning("redact_record skipped: paid LLM disabled via MB_USE_PAID_LLM")
        return {**record, "redaction_applied": record.get("redaction_applied", False)}
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    working = copy.deepcopy(record)
    any_changed = False

    messages: list[dict] = [
        {
            "role": "user",
            "content": (
                "Apply all compliance redactions to this record:\n\n"
                + json.dumps(record, indent=2)
            ),
        }
    ]

    while True:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            tools=_TOOLS,
            messages=messages,
        )

        tool_calls = [b for b in response.content if b.type == "tool_use"]

        if not tool_calls:
            # Agent produced only text — treat as finalized with no changes.
            break

        tool_results = []
        done = False

        for tc in tool_calls:
            name: str = tc.name
            args: dict = tc.input

            if name == "apply_redaction":
                parts = args["field_path"].split(".")
                changed = _set_nested(working, parts, args["original_value"], args["redacted_value"])
                if changed:
                    any_changed = True

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": json.dumps({
                        "status": "success" if changed else "warning",
                        "summary": (
                            f"Replaced '{args['original_value']}' → '{args['redacted_value']}' "
                            f"in {args['field_path']}"
                            if changed
                            else f"No match for '{args['original_value']}' in {args['field_path']}"
                        ),
                        "next_actions": (
                            ["continue applying remaining redactions"]
                            if not done
                            else []
                        ),
                        "artifacts": [args["field_path"]],
                    }),
                })

            elif name == "finalize_record":
                working["redaction_applied"] = args["redaction_applied"] or any_changed
                done = True
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": json.dumps({
                        "status": "success",
                        "summary": "Record finalized.",
                        "next_actions": [],
                        "artifacts": [],
                    }),
                })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

        if done:
            break

        if response.stop_reason == "end_turn":
            break

    # Guarantee the metadata field is always present.
    if "redaction_applied" not in working:
        working["redaction_applied"] = any_changed

    return working


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

_SAMPLE_RECORD = {
    "incident_id": "CF-2026-04471",
    "date": "2026-05-29",
    "incident_type": "Partner/Family Member Assault",
    "address": "1245 Central Avenue, Great Falls, MT 59401",
    "suspect_name": "John Ray Doe",
    "suspect_dob": "1988-03-14",
    "victim_name": "Jane Doe",
    "reporting_party": "Sarah Wilson",
    "narrative": (
        "Officers responded to 1245 Central Avenue regarding a PFMA complaint. "
        "Sarah Wilson called 911 after hearing a disturbance. Upon arrival officers "
        "contacted John Ray Doe and Jane Doe. Juvenile Tyler Doe, age 14, was present. "
        "John Ray Doe was placed under arrest."
    ),
    "charges": ["45-5-206 MCA - Partner/Family Member Assault"],
    "redaction_applied": False,
}


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    if "--test" in sys.argv:
        record = _SAMPLE_RECORD
    elif len(sys.argv) > 1 and sys.argv[1] != "--test":
        with open(sys.argv[1]) as f:
            record = json.load(f)
    else:
        record = json.load(sys.stdin)

    if isinstance(record, list):
        results = [redact_record(r) for r in record]
    else:
        results = redact_record(record)

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
