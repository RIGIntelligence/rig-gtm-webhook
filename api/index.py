"""Vercel Python serverless function — uses standard library only."""
import json
import sqlite3
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from io import BytesIO


# ── DB setup ────────────────────────────────────────────────────────────
DATA_DIR = "/tmp/gtm_data"
os.makedirs(DATA_DIR, exist_ok=True)
CRM_DB = os.path.join(DATA_DIR, "gtm_state.db")


def init_cdb():
    conn = sqlite3.connect(CRM_DB)
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


def process(payload):
    rtype = payload.get("type", "")
    if rtype in ("email.bounced", "email.complained"):
        return {"status": "negative", "type": rtype}
    if rtype in ("email.replied", "inbound.received", "received"):
        data = payload.get("data", {}) or {}
        sender = data.get("from", "")
        if isinstance(sender, dict): sender = sender.get("email", "")
        sender = sender.lower().strip()
        body = data.get("text") or data.get("body") or ""
        text = body.lower()
        init_cdb()
        if any(kw in text for kw in ["stop", "unsubscribe", "not interested", "remove me"]):
            conn = sqlite3.connect(CRM_DB)
            conn.execute("INSERT OR IGNORE INTO suppression_list VALUES (?,?,?,?)",
                         (sender, "auto_stop", datetime.now(timezone.utc).isoformat(), "webhook"))
            conn.commit(); conn.close()
            return {"status": "auto_stop", "email": sender}
        triage = "calendly" if any(kw in text for kw in ["yes", "interested", "book", "schedule", "let's talk", "call"]) else "human"
        return {"status": "recorded", "type": "reply", "triage": triage, "email": sender}
    if rtype == "email.delivered":
        return {"status": "delivered"}
    return {"status": "ignored", "type": rtype}


class handler(BaseHTTPRequestHandler):
    """Vercel Python runtime handler."""

    def do_GET(self):
        if self.path == "/api/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/webhook":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                payload = json.loads(body)
                result = process(payload)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress default logging
