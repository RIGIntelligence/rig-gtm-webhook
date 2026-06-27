"""Vercel serverless endpoint for Resend webhook events."""
import json
import os
import sys
import sqlite3
import hashlib
from datetime import datetime, timezone
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# DB paths (use /tmp for Vercel serverless — ephemeral but works for single-event processing)
DB_DIR = Path("/tmp/gtm_data")
DB_DIR.mkdir(exist_ok=True)
FUNNEL_DB = DB_DIR / "outbound_funnel.db"
CRM_DB = DB_DIR / "gtm_state.db"
SEND_LOG_PATH = Path(__file__).resolve().parent.parent.parent / "clients" / "_send-log.json"


def init_dbs():
    """Initialize SQLite databases."""
    from outbound.crm_tracker import init_db as init_crm
    from outbound.funnel_store import init_db as init_funnel
    init_crm(db_path=CRM_DB)
    init_funnel(db_path=FUNNEL_DB)


def triage_reply(email: str, body: str) -> dict:
    """Route a reply to the right bucket."""
    text = (body or "").lower().strip()
    STOP_KEYWORDS = {"stop", "unsubscribe", "remove me", "don't contact", "not interested", "no thanks", "not now", "opt out"}
    CALENDLY_KEYWORDS = {"yes", "sure", "interested", "tell me more", "book", "schedule", "let's talk", "call me", "available"}
    for kw in STOP_KEYWORDS:
        if kw in text:
            return {"bucket": "auto_stop", "reason": f"keyword: '{kw}'"}
    for kw in CALENDLY_KEYWORDS:
        if kw in text:
            return {"bucket": "calendly", "reason": f"keyword: '{kw}'"}
    return {"bucket": "human", "reason": "no keyword match"}


def handler(request):
    """Vercel serverless function handler."""
    try:
        payload = json.loads(request.get("body", "{}"))
    except Exception:
        return {"statusCode": 400, "body": json.dumps({"error": "invalid JSON"})}

    # Lazy init DBs
    init_dbs()

    rtype = payload.get("type", "")

    # Negative outcomes: report, don't score
    if rtype in ("email.bounced", "email.complained"):
        return {"statusCode": 200, "body": json.dumps({"status": "negative", "type": rtype})}

    # Inbound reply
    if rtype in ("email.replied", "inbound.received", "received"):
        data = payload.get("data", {}) or {}
        sender = data.get("from", "")
        if isinstance(sender, dict):
            sender = sender.get("email", "")
        sender = sender.lower().strip()
        body = data.get("text") or data.get("body") or ""

        # Triage
        result = triage_reply(sender, body)

        if result["bucket"] == "auto_stop":
            from outbound.crm_tracker import suppress_email
            suppress_email(sender, reason="auto_stop", slug="triage", db_path=CRM_DB)
        elif result["bucket"] == "calendly":
            pass  # Mike gets notified via human_review_queue for now

        # Record in funnel
        from outbound.funnel_store import record_event
        from outbound.contracts import FunnelEvent, deterministic_event_id
        event_id = deterministic_event_id("unknown", "email", "reply", payload.get("created_at", ""), str(payload.get("id", "")))
        event = FunnelEvent(
            event_id=event_id, slug="unknown", channel="email", type="reply",
            ts=payload.get("created_at", datetime.now(timezone.utc).isoformat()),
            meta={"from": sender, "inbound": True, "triage": result},
        )
        record_event(event, db_path=FUNNEL_DB)

        return {"statusCode": 200, "body": json.dumps({"status": "recorded", "type": "reply", "triage": result})}

    # Delivery
    if rtype == "email.delivered":
        return {"statusCode": 200, "body": json.dumps({"status": "delivered"})}

    return {"statusCode": 202, "body": json.dumps({"status": "ignored", "type": rtype})}
