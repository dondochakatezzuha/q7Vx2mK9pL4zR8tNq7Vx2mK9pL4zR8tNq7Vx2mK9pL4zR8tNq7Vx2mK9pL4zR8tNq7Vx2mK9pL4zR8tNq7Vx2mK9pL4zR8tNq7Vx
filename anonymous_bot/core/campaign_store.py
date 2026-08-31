"""Persistent campaign database for Anonymous Bot v7.

This database is intentionally outside the Python package. Bot updates can
replace anonymous_bot/ without touching campaign history.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..config import DATA_DIR

ROOT = Path(DATA_DIR)
DB_PATH = ROOT / "database" / "campaign.db"
BACKUP_DIR = ROOT / "backups"
MIGRATION_MARKER = ROOT / ".v7_migration_complete"
_LOCK = threading.RLock()


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def initialize():
    ROOT.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    with _LOCK, _connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            message_id TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL,
            channel_id TEXT,
            author_id TEXT,
            author_name TEXT,
            content TEXT NOT NULL DEFAULT '',
            jump_url TEXT,
            created_at TEXT,
            is_priority INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_messages_guild_created ON messages(guild_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_messages_guild_author ON messages(guild_id, author_id);
        CREATE INDEX IF NOT EXISTS idx_messages_guild_channel_created ON messages(guild_id, channel_id, created_at);
        CREATE TABLE IF NOT EXISTS server_sync (
            guild_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            channel_name TEXT,
            last_sync_at TEXT,
            oldest_message_at TEXT,
            messages_synced INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, channel_id)
        );
        CREATE TABLE IF NOT EXISTS lore_facts (
            fact_id TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL,
            text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'unconfirmed',
            classification TEXT NOT NULL DEFAULT 'unclassified',
            confidence REAL NOT NULL DEFAULT 0,
            source_message_id TEXT,
            source_url TEXT,
            created_at TEXT,
            author_id TEXT,
            author_name TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_lore_guild ON lore_facts(guild_id, created_at);
        CREATE TABLE IF NOT EXISTS canon_records (
            record_id TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL,
            name TEXT,
            type TEXT,
            visibility TEXT,
            description TEXT,
            gm_notes TEXT,
            created_at TEXT,
            source TEXT,
            session_number INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_records_guild ON canon_records(guild_id, type, name);
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL,
            session_number INTEGER,
            title TEXT,
            started_at TEXT,
            ended_at TEXT,
            recap TEXT,
            status TEXT NOT NULL DEFAULT 'completed'
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_guild ON sessions(guild_id, session_number);
        CREATE TABLE IF NOT EXISTS audit_log (
            audit_id TEXT PRIMARY KEY,
            guild_id TEXT,
            actor_id TEXT,
            actor_name TEXT,
            action TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'system',
            target_type TEXT,
            target_id TEXT,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_audit_guild_created ON audit_log(guild_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_audit_category ON audit_log(guild_id, category, created_at);
        """)
    migrate_legacy_json()


def _legacy_candidates():
    candidates = [
        ROOT / "anonymous_item_data.json",
        ROOT.parent / "anonymous_bot" / "anonymous_item_data.json",
        ROOT.parent / "anonymous_bot_data" / "anonymous_item_data.json",
    ]
    return [p for p in candidates if p.exists()]


def migrate_legacy_json():
    if MIGRATION_MARKER.exists():
        return
    source = next(iter(_legacy_candidates()), None)
    if source is None:
        MIGRATION_MARKER.write_text("no legacy JSON found\n", encoding="utf-8")
        return
    try:
        with source.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        print(f"[campaign-store] Legacy migration deferred: {type(exc).__name__}: {exc}")
        return

    imported = {"messages": 0, "facts": 0, "records": 0, "sessions": 0}
    with _LOCK, _connect() as db:
        for guild_id, state in (data.get("guilds") or {}).items():
            mem = (state or {}).get("campaign_memory") or {}
            combined = {}
            for row in (mem.get("archive") or []) + (mem.get("priority_archive") or []):
                if row.get("message_id"):
                    combined[str(row["message_id"])] = row
            for row in combined.values():
                db.execute("""INSERT OR IGNORE INTO messages
                    (message_id,guild_id,channel_id,author_id,author_name,content,jump_url,created_at,is_priority)
                    VALUES (?,?,?,?,?,?,?,?,?)""", (
                    str(row.get("message_id")), str(guild_id), str(row.get("channel_id") or ""),
                    str(row.get("author_id") or ""), row.get("author_name") or "Unknown",
                    row.get("content") or "", row.get("jump_url") or "", row.get("created_at") or "",
                    0,
                ))
                imported["messages"] += 1
            for fact in mem.get("lore_facts") or []:
                fid = str(fact.get("id") or "")
                if not fid:
                    continue
                db.execute("""INSERT OR IGNORE INTO lore_facts
                    (fact_id,guild_id,text,status,classification,confidence,source_message_id,source_url,created_at,author_id,author_name)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
                    fid, str(guild_id), fact.get("text") or "", fact.get("status") or "unconfirmed",
                    fact.get("classification") or "unclassified", float(fact.get("confidence") or 0),
                    str(fact.get("source_message_id") or ""), fact.get("source_url") or "",
                    fact.get("created_at") or "", str(fact.get("author_id") or ""), fact.get("author_name") or "Unknown"))
                imported["facts"] += 1
            for rec in mem.get("records") or []:
                rid = str(rec.get("id") or "")
                if not rid:
                    continue
                db.execute("""INSERT OR IGNORE INTO canon_records
                    (record_id,guild_id,name,type,visibility,description,gm_notes,created_at,source,session_number)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""", (
                    rid, str(guild_id), rec.get("name") or "Untitled", rec.get("type") or "other",
                    rec.get("visibility") or "canon", rec.get("description") or "", rec.get("gm_notes") or "",
                    rec.get("created_at") or "", rec.get("source") or "", rec.get("session_number")))
                imported["records"] += 1
            gm = state.get("gm_tools") or {}
            for session in gm.get("session_history") or []:
                sid = f"{guild_id}:{session.get('session_number')}:{session.get('started_at')}"
                db.execute("""INSERT OR IGNORE INTO sessions
                    (session_id,guild_id,session_number,title,started_at,ended_at,recap,status)
                    VALUES (?,?,?,?,?,?,?,?)""", (
                    sid, str(guild_id), session.get("session_number"), session.get("title") or "",
                    session.get("started_at") or "", session.get("ended_at") or "",
                    json.dumps(session.get("ai_recap") or {}, ensure_ascii=False), "completed"))
                imported["sessions"] += 1
    MIGRATION_MARKER.write_text(json.dumps(imported), encoding="utf-8")
    print(f"[campaign-store] v7 migration complete: {imported}")


def archive_message(message, priority=False):
    if not message or not getattr(message, "guild", None):
        return
    with _LOCK, _connect() as db:
        db.execute("""INSERT OR REPLACE INTO messages
            (message_id,guild_id,channel_id,author_id,author_name,content,jump_url,created_at,is_priority)
            VALUES (?,?,?,?,?,?,?,?,?)""", (
            str(message.id), str(message.guild.id), str(message.channel.id), str(message.author.id),
            getattr(message.author, "display_name", str(message.author)), (message.content or ""),
            getattr(message, "jump_url", ""), message.created_at.isoformat(), 1 if priority else 0))


def upsert_fact(guild_id, fact):
    with _LOCK, _connect() as db:
        db.execute("""INSERT OR REPLACE INTO lore_facts
            (fact_id,guild_id,text,status,classification,confidence,source_message_id,source_url,created_at,author_id,author_name)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
            str(fact.get("id")), str(guild_id), fact.get("text") or "", fact.get("status") or "unconfirmed",
            fact.get("classification") or "unclassified", float(fact.get("confidence") or 0),
            str(fact.get("source_message_id") or ""), fact.get("source_url") or "", fact.get("created_at") or "",
            str(fact.get("author_id") or ""), fact.get("author_name") or "Unknown"))


def upsert_record(guild_id, record):
    with _LOCK, _connect() as db:
        db.execute("""INSERT OR REPLACE INTO canon_records
            (record_id,guild_id,name,type,visibility,description,gm_notes,created_at,source,session_number)
            VALUES (?,?,?,?,?,?,?,?,?,?)""", (
            str(record.get("id")), str(guild_id), record.get("name") or "Untitled", record.get("type") or "other",
            record.get("visibility") or "canon", record.get("description") or "", record.get("gm_notes") or "",
            record.get("created_at") or "", record.get("source") or "", record.get("session_number")))


def search_messages(guild_id, query, limit=30):
    terms = [x for x in query.casefold().split() if len(x) > 2]
    if not terms:
        return []
    where = " OR ".join("content LIKE ?" for _ in terms)
    params = [f"%{t}%" for t in terms]
    params += [str(guild_id), int(limit)]
    with _LOCK, _connect() as db:
        rows = db.execute(f"""SELECT * FROM messages WHERE ({where}) AND guild_id=?
            ORDER BY created_at DESC LIMIT ?""", params).fetchall()
    return [dict(r) for r in rows]



def get_server_sync(guild_id, channel_id):
    with _LOCK, _connect() as db:
        row = db.execute("SELECT * FROM server_sync WHERE guild_id=? AND channel_id=?", (str(guild_id), str(channel_id))).fetchone()
    return dict(row) if row else None


def upsert_server_sync(guild_id, channel_id, channel_name, last_sync_at=None, oldest_message_at=None, messages_synced=0):
    with _LOCK, _connect() as db:
        db.execute("""INSERT INTO server_sync
            (guild_id,channel_id,channel_name,last_sync_at,oldest_message_at,messages_synced)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(guild_id,channel_id) DO UPDATE SET
              channel_name=excluded.channel_name,
              last_sync_at=excluded.last_sync_at,
              oldest_message_at=COALESCE(excluded.oldest_message_at, server_sync.oldest_message_at),
              messages_synced=server_sync.messages_synced + excluded.messages_synced
        """, (str(guild_id), str(channel_id), channel_name or "", last_sync_at, oldest_message_at, int(messages_synced or 0)))


def search_server_messages(guild_id, query, limit=60):
    """Search the historical server archive, including channel/author metadata."""
    terms = [x for x in query.casefold().split() if len(x) > 2]
    if not terms:
        return []
    where = " OR ".join("(content LIKE ? OR author_name LIKE ?)" for _ in terms)
    params = []
    for t in terms:
        params.extend([f"%{t}%", f"%{t}%"])
    params += [str(guild_id), int(limit)]
    with _LOCK, _connect() as db:
        rows = db.execute(f"""SELECT * FROM messages WHERE ({where}) AND guild_id=?
            ORDER BY is_priority DESC, created_at DESC LIMIT ?""", params).fetchall()
    return [dict(r) for r in rows]

def message_count(guild_id):
    with _LOCK, _connect() as db:
        return int(db.execute("SELECT COUNT(*) FROM messages WHERE guild_id=?", (str(guild_id),)).fetchone()[0])


def messages_between(guild_id, start=None, end=None, limit=5000):
    clauses = ["guild_id=?"]
    params = [str(guild_id)]
    if start:
        clauses.append("created_at>=?")
        params.append(start)
    if end:
        clauses.append("created_at<=?")
        params.append(end)
    params.append(int(limit))
    with _LOCK, _connect() as db:
        rows = db.execute(f"SELECT * FROM messages WHERE {' AND '.join(clauses)} ORDER BY created_at ASC LIMIT ?", params).fetchall()
    return [dict(r) for r in rows]


def session_count(guild_id):
    with _LOCK, _connect() as db:
        return int(db.execute("SELECT COUNT(*) FROM sessions WHERE guild_id=?", (str(guild_id),)).fetchone()[0])


def record_session(guild_id, session):
    sid = f"{guild_id}:{session.get('session_number')}:{session.get('started_at')}"
    with _LOCK, _connect() as db:
        db.execute("""INSERT OR REPLACE INTO sessions
            (session_id,guild_id,session_number,title,started_at,ended_at,recap,status)
            VALUES (?,?,?,?,?,?,?,?)""", (
            sid, str(guild_id), session.get("session_number"), session.get("title") or "",
            session.get("started_at") or "", session.get("ended_at") or "",
            json.dumps(session.get("ai_recap") or {}, ensure_ascii=False), "completed"))



def audit_event(guild_id, action, category="system", actor_id=None, actor_name=None, target_type=None, target_id=None, details=None):
    """Persist a lightweight admin/audit trail for important bot actions."""
    import uuid
    with _LOCK, _connect() as db:
        db.execute("""INSERT INTO audit_log
            (audit_id,guild_id,actor_id,actor_name,action,category,target_type,target_id,details_json,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)""", (
            str(uuid.uuid4()), str(guild_id) if guild_id is not None else None,
            str(actor_id) if actor_id is not None else None, actor_name, str(action), str(category),
            str(target_type) if target_type is not None else None, str(target_id) if target_id is not None else None,
            json.dumps(details or {}, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))


def audit_events(guild_id=None, category=None, limit=300):
    clauses=[]; params=[]
    if guild_id is not None:
        clauses.append("guild_id=?"); params.append(str(guild_id))
    if category:
        clauses.append("category=?"); params.append(str(category))
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(int(limit))
    with _LOCK, _connect() as db:
        rows=db.execute(f"SELECT * FROM audit_log{where} ORDER BY created_at DESC LIMIT ?", params).fetchall()
    out=[]
    for r in rows:
        item=dict(r)
        try: item["details"]=json.loads(item.pop("details_json") or "{}")
        except Exception: item["details"]={}
        out.append(item)
    return out

def backup(label="manual"):
    ROOT.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    destination = BACKUP_DIR / f"campaign_{label}_{stamp}.db"
    if DB_PATH.exists():
        with _LOCK:
            shutil.copy2(DB_PATH, destination)
    # Keep a bounded backup set.
    backups = sorted(BACKUP_DIR.glob("campaign_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[25:]:
        try:
            old.unlink()
        except OSError:
            pass
    return destination
