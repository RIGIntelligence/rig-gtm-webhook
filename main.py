"""Webhook receiver for Resend events — runs on Railway with uvicorn."""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

DATA_DIR = Path("/tmp/gtm_data")
DATA_DIR.mkdir(exist_ok=True)
CRM_DB = DATA_DIR / "gtm_state.db"

app = FastAPI(title="RIG GTM Webhook")


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


def process(payload: dict) -> dict:
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
            conn = sqlite3.connect(str(CRM_DB))
            conn.execute("INSERT OR IGNORE INTO suppression_list VALUES (?,?,?,?)",
                         (sender, "auto_stop", datetime.now(timezone.utc).isoformat(), "webhook"))
            conn.commit(); conn.close()
            return {"status": "auto_stop", "email": sender}
        triage = "calendly" if any(kw in text for kw in ["yes", "interested", "book", "schedule", "let's talk", "call"]) else "human"
        return {"status": "recorded", "type": "reply", "triage": triage, "email": sender}
    if rtype == "email.delivered":
        return {"status": "delivered"}
    return {"status": "ignored", "type": rtype}


@app.post("/webhooks/resend")
async def webhook(request: Request):
    payload = await request.json()
    return JSONResponse(process(payload))


@app.get("/health")
async def health():
    return {"status": "ok"}
