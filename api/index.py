"""Railway-deployed webhook receiver for Resend events."""
import json
import os
import sys
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# Minimal triage without full outbound module
DATA_DIR = Path("/tmp/gtm_data")
DATA_DIR.mkdir(exist_ok=True)
CRM_DB = DATA_DIR / "gtm_state.db"

def init_cdb():
    conn = sqlite3.connect(str(CRM_DB))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS suppression_list (
            email TEXT PRIMARY KEY, reason TEXT NOT NULL,
            suppressed_at TEXT NOT NULL, source TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sent_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL,
            slug TEXT, status TEXT, message_id TEXT, sent_at TEXT
        );
    """)
    conn.commit()
    conn.close()

def handler(request):
    try:
        payload = json.loads(request.get("body", "{}"))
    except Exception:
        return {"statusCode": 400, "body": json.dumps({"error": "invalid"})}

    init_cdb()
    rtype = payload.get("type", "")
    if rtype in ("email.bounced", "email.complained"):
        return {"statusCode": 200, "body": json.dumps({"status": "negative"})}
    if rtype in ("email.replied", "inbound.received", "received"):
        data = payload.get("data", {}) or {}
        sender = (data.get("from") or "")
        if isinstance(sender, dict): sender = sender.get("email", "")
        body = data.get("text") or data.get("body") or ""
        text = body.lower()
        if any(kw in text for kw in ["stop", "unsubscribe", "not interested", "remove me"]):
            conn = sqlite3.connect(str(CRM_DB))
            conn.execute("INSERT OR IGNORE INTO suppression_list VALUES (?,?,?,?)",
                         (sender.lower(), "auto_stop", datetime.now(timezone.utc).isoformat(), "webhook"))
            conn.commit()
            conn.close()
            return {"statusCode": 200, "body": json.dumps({"status": "auto_stop"})}
        return {"statusCode": 200, "body": json.dumps({"status": "recorded", "type": "reply", "triage": "calendly"})}
    if rtype == "email.delivered":
        return {"statusCode": 200, "body": json.dumps({"status": "delivered"})}
    return {"statusCode": 202, "body": json.dumps({"status": "ignored", "type": rtype})}
