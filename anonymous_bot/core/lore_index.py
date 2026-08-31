"""Structured campaign lore index.

Keeps entity profiles, player participation, session events, relationships,
and canon corrections in SQLite so retrieval can be deterministic instead of
asking the language model to rediscover chronology from raw Discord text.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone

from . import campaign_store


def _now():
    return datetime.now(timezone.utc).isoformat()


def initialize():
    with campaign_store._LOCK, campaign_store._connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS lore_entities (
            entity_id TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            player_owner_id TEXT,
            character_version INTEGER,
            status TEXT,
            current_status TEXT,
            authority TEXT,
            profile_json TEXT NOT NULL,
            first_session INTEGER,
            last_session INTEGER,
            first_seen TEXT,
            last_seen TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(guild_id, entity_key)
        );
        CREATE INDEX IF NOT EXISTS idx_lore_entities_name ON lore_entities(guild_id, name);
        CREATE INDEX IF NOT EXISTS idx_lore_entities_owner ON lore_entities(guild_id, player_owner_id);
        CREATE TABLE IF NOT EXISTS lore_aliases (
            guild_id TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            alias TEXT NOT NULL,
            PRIMARY KEY(guild_id, entity_id, alias)
        );
        CREATE INDEX IF NOT EXISTS idx_lore_aliases ON lore_aliases(guild_id, alias);
        CREATE TABLE IF NOT EXISTS session_participants (
            guild_id TEXT NOT NULL,
            session_number INTEGER NOT NULL,
            player_id TEXT NOT NULL,
            character_id TEXT,
            first_activity_at TEXT,
            last_activity_at TEXT,
            evidence_count INTEGER NOT NULL DEFAULT 0,
            check_in INTEGER NOT NULL DEFAULT 0,
            manual_override TEXT,
            PRIMARY KEY(guild_id, session_number, player_id)
        );
        CREATE INDEX IF NOT EXISTS idx_session_participants_player ON session_participants(guild_id, player_id, session_number);
        CREATE TABLE IF NOT EXISTS session_events_index (
            event_id TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL,
            session_number INTEGER NOT NULL,
            event_order INTEGER,
            title TEXT,
            summary TEXT,
            canon_status TEXT,
            source_message_id TEXT,
            source_url TEXT,
            participant_player_ids TEXT,
            participant_character_ids TEXT,
            created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_session_events ON session_events_index(guild_id, session_number, event_order);
        CREATE TABLE IF NOT EXISTS canon_corrections (
            correction_id TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL,
            subject_entity_id TEXT,
            old_claim TEXT,
            new_claim TEXT,
            status TEXT,
            authority TEXT,
            session_number INTEGER,
            source_message_id TEXT,
            source_url TEXT,
            created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_corrections_subject ON canon_corrections(guild_id, subject_entity_id);
        """)


def upsert_profile(guild_id: str, key: str, profile: dict):
    entity_id = str(profile.get("id") or key)
    aliases = [str(x).strip() for x in profile.get("aliases") or [] if str(x).strip()]
    first = profile.get("first_appearance") or {}
    last = profile.get("last_appearance") or {}
    with campaign_store._LOCK, campaign_store._connect() as db:
        existing = db.execute("SELECT entity_id FROM lore_entities WHERE guild_id=? AND entity_key=?", (str(guild_id), str(key))).fetchone()
        if existing and str(existing[0]) != entity_id:
            entity_id = str(existing[0])
        db.execute("""INSERT INTO lore_entities
            (entity_id,guild_id,entity_key,name,entity_type,player_owner_id,character_version,status,current_status,authority,profile_json,first_session,last_session,first_seen,last_seen,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(entity_id) DO UPDATE SET
              name=excluded.name, entity_type=excluded.entity_type,
              player_owner_id=excluded.player_owner_id, character_version=excluded.character_version,
              status=excluded.status, current_status=excluded.current_status,
              authority=excluded.authority, profile_json=excluded.profile_json,
              first_session=excluded.first_session, last_session=excluded.last_session,
              first_seen=excluded.first_seen, last_seen=excluded.last_seen,
              updated_at=excluded.updated_at""", (
            entity_id, str(guild_id), str(key), str(profile.get("name") or key), str(profile.get("type") or "other"),
            str(profile.get("player_owner_id")) if profile.get("player_owner_id") is not None else None,
            profile.get("character_version"), str(profile.get("status") or "unknown"), str(profile.get("current_status") or "unknown"),
            str(profile.get("authority") or "unknown"), json.dumps(profile, ensure_ascii=False),
            first.get("session_number"), last.get("session_number"), first.get("created_at"), last.get("created_at"), _now()))
        db.execute("DELETE FROM lore_aliases WHERE guild_id=? AND entity_id=?", (str(guild_id), entity_id))
        for alias in aliases:
            db.execute("INSERT OR IGNORE INTO lore_aliases(guild_id,entity_id,alias) VALUES (?,?,?)", (str(guild_id), entity_id, alias))


def record_participation(guild_id, session_number, player_id, character_id=None, activity_at=None, check_in=None, manual_override=None):
    if session_number is None or not player_id:
        return
    ts = activity_at or _now()
    with campaign_store._LOCK, campaign_store._connect() as db:
        row = db.execute("SELECT first_activity_at,evidence_count,check_in,manual_override FROM session_participants WHERE guild_id=? AND session_number=? AND player_id=?", (str(guild_id), int(session_number), str(player_id))).fetchone()
        first = row[0] if row and row[0] else ts
        count = int(row[1]) if row else 0
        old_check = int(row[2]) if row else 0
        old_override = row[3] if row else None
        db.execute("""INSERT OR REPLACE INTO session_participants
            (guild_id,session_number,player_id,character_id,first_activity_at,last_activity_at,evidence_count,check_in,manual_override)
            VALUES (?,?,?,?,?,?,?,?,?)""", (str(guild_id), int(session_number), str(player_id), str(character_id) if character_id else None,
            first, ts, count + 1, int(bool(check_in)) if check_in is not None else old_check,
            manual_override if manual_override is not None else old_override))


def latest_player_session(guild_id, player_id):
    with campaign_store._LOCK, campaign_store._connect() as db:
        row = db.execute("""SELECT session_number,character_id,first_activity_at,last_activity_at,evidence_count,check_in,manual_override
                           FROM session_participants WHERE guild_id=? AND player_id=?
                           ORDER BY session_number DESC LIMIT 1""", (str(guild_id), str(player_id))).fetchone()
    return dict(row) if row else None


def player_sessions(guild_id, player_id, limit=50):
    with campaign_store._LOCK, campaign_store._connect() as db:
        rows = db.execute("""SELECT * FROM session_participants WHERE guild_id=? AND player_id=? ORDER BY session_number DESC LIMIT ?""", (str(guild_id), str(player_id), int(limit))).fetchall()
    return [dict(r) for r in rows]


def session_events(guild_id, session_number, limit=300):
    with campaign_store._LOCK, campaign_store._connect() as db:
        rows = db.execute("""SELECT * FROM session_events_index WHERE guild_id=? AND session_number=? ORDER BY event_order ASC, created_at ASC LIMIT ?""", (str(guild_id), int(session_number), int(limit))).fetchall()
    return [dict(r) for r in rows]


def search_entities(guild_id, query, limit=12):
    q = str(query or "").casefold().strip()
    if not q:
        return []
    tokens = [x for x in re.findall(r"[\w'-]+", q) if len(x) > 2]
    with campaign_store._LOCK, campaign_store._connect() as db:
        rows = db.execute("""SELECT e.* FROM lore_entities e WHERE e.guild_id=?
            AND (lower(e.name) LIKE ? OR lower(e.entity_key) LIKE ? OR e.entity_id IN
                (SELECT entity_id FROM lore_aliases WHERE guild_id=? AND lower(alias) LIKE ?))
            ORDER BY CASE WHEN lower(e.name)=? THEN 0 WHEN lower(e.name) LIKE ? THEN 1 ELSE 2 END,
                     e.updated_at DESC LIMIT ?""", (str(guild_id), f"%{q}%", f"%{q}%", str(guild_id), f"%{q}%", q, f"{q}%", int(limit))).fetchall()
    return [dict(r) for r in rows]



def rebuild_from_database(guild_id, gm_user_ids=None):
    """Rebuild the structured admin index from the durable Discord archive.

    This is intentionally deterministic and does not ask an LLM to invent
    entities. Existing raw messages, GM/canon records, facts, and sessions are
    the evidence; the GUI can then expose the resulting records immediately.
    """
    import re
    from datetime import datetime, timezone

    gid = str(guild_id)
    gm_ids = {str(x) for x in (gm_user_ids or [])}
    now = _now()

    def iso_ts(value):
        if not value:
            return None
        try:
            if isinstance(value, (int, float)):
                return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
            v = str(value)
            if re.fullmatch(r"-?\d+(?:\.\d+)?", v):
                return datetime.fromtimestamp(float(v), tz=timezone.utc).isoformat()
            return v
        except Exception:
            return str(value)

    with campaign_store._LOCK, campaign_store._connect() as db:
        messages = [dict(r) for r in db.execute(
            "SELECT * FROM messages WHERE guild_id=? ORDER BY created_at ASC", (gid,)
        ).fetchall()]
        records = [dict(r) for r in db.execute(
            "SELECT * FROM canon_records WHERE guild_id=? ORDER BY created_at ASC", (gid,)
        ).fetchall()]
        sessions = [dict(r) for r in db.execute(
            "SELECT * FROM sessions WHERE guild_id=? ORDER BY session_number ASC", (gid,)
        ).fetchall()]
        # Preserve already-rich profiles; rebuild only missing/stale derived data.
        existing = {str(r["entity_id"]): dict(r) for r in db.execute(
            "SELECT * FROM lore_entities WHERE guild_id=?", (gid,)
        ).fetchall()}

    def upsert_local(profile, key=None):
        upsert_profile(gid, key or profile.get("entity_key") or profile.get("id"), profile)

    # Discord accounts: GMs are explicitly GM entities, never player entities.
    authors = {}
    for m in messages:
        aid = str(m.get("author_id") or "")
        if not aid:
            continue
        authors[aid] = m.get("author_name") or aid
    for aid, name in authors.items():
        typ = "gm" if aid in gm_ids else "player"
        key = f"player:{aid}"
        profile = {
            "id": key, "name": name, "type": typ, "discord_user_id": aid,
            "display_name": name, "account_role": "gm" if typ == "gm" else "player",
            "status": "canon" if typ == "gm" else "active",
            "current_status": "active", "authority": "gm" if typ == "gm" else "player",
            "summary": "Configured campaign GM/writer account." if typ == "gm" else "Discord player account.",
            "source_message_ids": [], "source_urls": [], "sessions": [],
            "character_ids": [], "character_history": [], "active_character_id": None,
            "first_appearance": None, "last_appearance": None, "updated_at": now,
        }
        old = existing.get(key)
        if old:
            try:
                oldp = json.loads(old.get("profile_json") or "{}")
                profile.update({k:v for k,v in oldp.items() if k not in {"type","account_role","authority","status","current_status"}})
            except Exception:
                pass
        profile["type"] = typ
        profile["account_role"] = "gm" if typ == "gm" else "player"
        profile["authority"] = "gm" if typ == "gm" else "player"
        for m in messages:
            if str(m.get("author_id") or "") != aid:
                continue
            sid = _message_session_number(m, sessions)
            profile["sessions"] = sorted(set(profile.get("sessions", [])) | ({sid} if sid is not None else set()))
            profile["last_appearance"] = {"session_number": sid, "created_at": m.get("created_at"), "source_message_id": str(m.get("message_id") or ""), "source_url": m.get("jump_url")}
            if not profile.get("first_appearance"):
                profile["first_appearance"] = {"session_number": sid, "created_at": m.get("created_at"), "source_message_id": str(m.get("message_id") or ""), "source_url": m.get("jump_url")}
            if len(profile["source_message_ids"]) < 300:
                profile["source_message_ids"].append(str(m.get("message_id") or ""))
        upsert_local(profile, key)

    # Canon records become first-class records, and their useful proper names
    # seed real lore entities without turning entire record titles into fake characters.
    stop = {"The","This","That","These","Those","When","Where","What","Who","How","And","But","For","With","From","After","Before","Then","There","Their","They","Your","You","Campaign","Game","Player","Character","Claim","Speculation","Upcoming","Horn","Desire"}
    known = {}
    for r in records:
        rid = str(r.get("record_id"))
        rtype = str(r.get("type") or "other")
        record_profile = {
            "id": f"record:{rid}", "name": r.get("name") or "Untitled", "type": "lore_record",
            "record_type": rtype, "status": r.get("visibility") or "unconfirmed",
            "current_status": r.get("visibility") or "unknown", "authority": "gm" if str(r.get("source") or "").startswith("gm") else "archive",
            "summary": r.get("description") or "", "description": r.get("description") or "",
            "gm_notes": r.get("gm_notes") or "", "source": r.get("source") or "",
            "source_record_id": rid, "session_number": r.get("session_number"),
            "created_at": r.get("created_at"), "sources": [{"record_id": rid, "created_at": r.get("created_at")}],
            "updated_at": now,
        }
        upsert_local(record_profile, f"record:{rid}")
        title = str(r.get("name") or "")
        for candidate in re.findall(r"\b[A-Z][A-Za-z'’-]{2,}\b", title):
            candidate = candidate.strip("'")
            if candidate in stop or candidate in authors.values() or len(candidate) < 3:
                continue
            key = candidate.casefold()
            known.setdefault(key, {"name":candidate, "type": "character" if rtype in {"character","creature"} else rtype, "records":[]})
            known[key]["records"].append(record_profile)

    # Seed named lore entities from high-signal canon record names and archived messages.
    # Avoid common sentence words and known Discord account names.
    common = stop | {"Discord","Anonymous","Admin","Test","Lore","Session","Message","Server","Realm","RPG","AI","GM","Civic","Draven"}
    for m in messages:
        text = str(m.get("content") or "")
        candidates = re.findall(r"\b[A-Z][A-Za-z'’-]{2,}\b", text)
        for candidate in candidates:
            if candidate in common or candidate in authors.values():
                continue
            # Do not make ordinary sentence-leading words into entities.
            if candidate in {"I","Okay","Ok","Bruh","Wallah","Yeah","Nah","No","Yes","Well","Give","Tell","Why","What","Who","How","The"}:
                continue
            key = candidate.casefold()
            entry = known.setdefault(key, {"name": candidate, "type": "other", "records":[]})
            sid = _message_session_number(m, sessions)
            entry.setdefault("sources", []).append({
                "message_id": str(m.get("message_id") or ""), "url": m.get("jump_url"),
                "created_at": m.get("created_at"), "session_number": sid
            })

    for key, entry in known.items():
        eid = f"entity:{key}"
        old = existing.get(eid)
        oldp = {}
        if old:
            try: oldp = json.loads(old.get("profile_json") or "{}")
            except Exception: oldp = {}
        profile = dict(oldp)
        profile.update({"id": eid, "name": entry["name"], "type": oldp.get("type") if oldp.get("type") not in (None,"other") else entry["type"],
                        "authority": oldp.get("authority") or "archive", "status": oldp.get("status") or "unknown",
                        "current_status": oldp.get("current_status") or "unknown", "updated_at": now})
        recs = entry.get("records") or []
        if recs:
            profile["summary"] = profile.get("summary") or recs[0].get("description") or ""
            profile["sources"] = (profile.get("sources") or []) + recs[:20]
            profile["sources"] = profile["sources"][-100:]
        srcs = entry.get("sources") or []
        if srcs:
            profile["source_records"] = (profile.get("source_records") or []) + srcs[-200:]
            profile["source_records"] = profile["source_records"][-300:]
            sess = [x.get("session_number") for x in profile["source_records"] if x.get("session_number") is not None]
            if sess:
                profile["sessions"] = sorted(set(profile.get("sessions", [])) | set(sess))
                profile["first_appearance"] = profile.get("first_appearance") or profile["source_records"][0]
                profile["last_appearance"] = profile["source_records"][-1]
        upsert_local(profile, eid)

    # Deterministic session participation from archived messages.
    for m in messages:
        aid = str(m.get("author_id") or "")
        if not aid or aid in gm_ids:
            continue
        sid = _message_session_number(m, sessions)
        if sid is None:
            continue
        record_participation(gid, sid, aid, None, m.get("created_at"))

    # Ensure an organic Session 1 exists when the campaign archive predates /game start.
    if messages and not any(int(s.get("session_number") or 0) == 1 for s in sessions):
        first = messages[0]
        with campaign_store._LOCK, campaign_store._connect() as db:
            db.execute("""INSERT OR IGNORE INTO sessions(session_id,guild_id,session_number,title,started_at,ended_at,recap,status)
                          VALUES(?,?,?,?,?,?,?,?)""",
                       (f"{gid}:1:inferred", gid, 1, "Session 1 — Organic Campaign Start",
                        first.get("created_at"), None, "{}", "inferred"))

    return {
        "messages": len(messages), "records": len(records), "authors": len(authors),
        "entities": len(known), "sessions": len(sessions) + (1 if messages and not any(int(s.get("session_number") or 0)==1 for s in sessions) else 0)
    }


def _message_session_number(message, sessions):
    """Map an archived message to the formal session interval; pre-session history becomes Session 1."""
    from datetime import datetime, timezone
    value = str(message.get("created_at") or "")
    try:
        mt = datetime.fromisoformat(value.replace("Z","+00:00"))
    except Exception:
        mt = None
    if mt is not None:
        for s in sessions:
            try:
                st = datetime.fromtimestamp(float(s.get("started_at")), tz=timezone.utc) if str(s.get("started_at","")).replace(".","",1).isdigit() else datetime.fromisoformat(str(s.get("started_at")).replace("Z","+00:00"))
                en = None
                if s.get("ended_at"):
                    en = datetime.fromtimestamp(float(s.get("ended_at")), tz=timezone.utc) if str(s.get("ended_at","")).replace(".","",1).isdigit() else datetime.fromisoformat(str(s.get("ended_at")).replace("Z","+00:00"))
                if mt >= st and (en is None or mt <= en):
                    return int(s.get("session_number"))
            except Exception:
                continue
    if sessions:
        nums = sorted(int(s.get("session_number") or 0) for s in sessions if s.get("session_number") is not None)
        if nums and nums[0] > 1:
            return 1
    return None

def sync_all_profiles(guild_id, profiles):
    for key, profile in (profiles or {}).items():
        if isinstance(profile, dict):
            upsert_profile(guild_id, key, profile)
