"""Montana Blotter public chat assistant.

Provides a streaming /api/chat endpoint backed by the Hermes/9Router gateway.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime

from flask import Blueprint, Response, request, jsonify
import openai

chat_agent_bp = Blueprint("chat_agent", __name__, url_prefix="/api/chat")

SYSTEM_PROMPT = """You are the Montana Blotter assistant. Montana Blotter is a real-time public record reporting site for Montana that aggregates incident records, jail rosters, court records, and other public data from official sources.

Your job:
- Help visitors find records by county, city, date range, or record type.
- Explain what Montana Blotter publishes (incident reports, jail rosters, court cases, warrants where available, blog digests).
- Explain that many record pages require a free account to view, and that warrant access is a separate paid add-on where applicable.
- Guide users to the right page: / (homepage), /search, county/city blotter pages, /jail, /courts, /wanted, /about, /contact, or /subscribe.
- Explain data sources (law enforcement, courts, detention centers, official agencies) and that records are updated as agencies publish them.
- Explain redaction policies: PII such as SSNs, dates of birth, home addresses, and victim identities are redacted.
- For corrections or removal requests, direct users to the contact or takedown process.

Hard rules:
- Never give legal advice. If a user asks for legal advice, say you can't and suggest consulting an attorney.
- Never state that someone is guilty or innocent based on a record; only describe what the record shows.
- Do not invent specific incidents, names, addresses, case numbers, or dates.
- Do not disclose private information beyond what is already public on the site.
- Be concise, neutral, and helpful. Two to three sentences unless the user asks for detail.
- Today's date is {}.""".format(datetime.now().strftime("%B %d, %Y"))

GATEWAY_URL = os.environ.get("CHAT_GATEWAY_URL", "http://127.0.0.1:20128/v1")
GATEWAY_KEY = os.environ.get("CHAT_GATEWAY_KEY", "")
MODEL = os.environ.get("CHAT_MODEL", "Main")

# Simple in-memory rate limit: 20 requests per IP per 5 minutes.
_rate_limit: dict[str, tuple[int, float]] = {}
RATE_LIMIT = 20
WINDOW_SECONDS = 5 * 60


def _check_rate_limit(ip: str) -> tuple[bool, int]:
    now = time.time()
    count, reset_at = _rate_limit.get(ip, (0, 0))
    if now > reset_at:
        _rate_limit[ip] = (1, now + WINDOW_SECONDS)
        return True, 0
    if count >= RATE_LIMIT:
        return False, int(reset_at - now)
    _rate_limit[ip] = (count + 1, reset_at)
    return True, 0


def _client():
    return openai.OpenAI(base_url=GATEWAY_URL, api_key=GATEWAY_KEY)


@chat_agent_bp.route("/", methods=["POST"])
def chat():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    ok, retry_after = _check_rate_limit(ip)
    if not ok:
        return jsonify({"error": "Too many requests. Try again later."}), 429

    if not GATEWAY_KEY:
        return jsonify({"error": "Chat not configured"}), 503

    data = request.get_json(silent=True) or {}
    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        return jsonify({"error": "Missing messages"}), 400

    if messages[-1].get("role") != "user":
        return jsonify({"error": "Last message must be from user"}), 400

    upstream_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    upstream_messages.extend(messages[-10:])

    def generate():
        try:
            client = _client()
            stream = client.chat.completions.create(
                model=MODEL,
                messages=upstream_messages,
                stream=True,
                max_tokens=800,
                temperature=0.5,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    yield f"data: {json.dumps({'text': delta})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            yield "data: [DONE]\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
        },
    )


@chat_agent_bp.route("/", methods=["GET"])
def status():
    return jsonify({
        "ok": True,
        "online": bool(GATEWAY_KEY and GATEWAY_URL),
        "model": MODEL,
    })
