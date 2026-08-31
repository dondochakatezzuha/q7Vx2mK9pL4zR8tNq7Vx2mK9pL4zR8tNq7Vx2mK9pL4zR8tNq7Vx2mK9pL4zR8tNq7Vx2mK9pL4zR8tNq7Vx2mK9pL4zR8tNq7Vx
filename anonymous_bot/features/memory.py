import asyncio
import re
import uuid
import json
from datetime import datetime, timezone

import discord
from discord import app_commands

from ..state import is_staff
from ..config import GAME_CHANNEL_ID, GM_USER_IDS
from .items import item_state, save_item_data
from .groups import ADMIN_MEMORY_GROUP
from ..core import campaign_store, lore_index
from .ai_providers import complete

MEMORY_GROUP = app_commands.Group(name="memory", description="Internal campaign memory tools.")
LORE_GROUP = app_commands.Group(name="lore", description="Campaign knowledge, NPCs, locations, mysteries, timeline, and lore search.")

# These accounts are intentionally high-priority lore sources for this campaign.
# Their messages are archived per-server, not globally across servers.
PRIORITY_LORE_USERS = {"1463100226318368874", "1538115083513761813", "1187513925739237408", "1311460994660306996"}
LORE_ARCHIVE_GUILD_ID = "1535189086258855946"

TYPES = [
    app_commands.Choice(name="Item", value="item"), app_commands.Choice(name="Weapon", value="weapon"),
    app_commands.Choice(name="Armor", value="armor"), app_commands.Choice(name="Character", value="character"),
    app_commands.Choice(name="NPC", value="npc"), app_commands.Choice(name="Vehicle", value="vehicle"),
    app_commands.Choice(name="Location", value="location"), app_commands.Choice(name="Faction", value="faction"),
    app_commands.Choice(name="Creature", value="creature"), app_commands.Choice(name="Lore", value="lore"),
    app_commands.Choice(name="Event", value="event"), app_commands.Choice(name="Other", value="other"),
]


def _store(guild_id):
    st = item_state(guild_id)
    st.setdefault("campaign_memory", {})
    mem = st["campaign_memory"]
    mem.setdefault("records", [])
    mem.setdefault("archive", [])
    mem.setdefault("priority_archive", [])
    mem.setdefault("lore_facts", [])
    mem.setdefault("entities", {})
    mem.setdefault("entity_profiles", {})
    mem.setdefault("contradictions", [])
    mem.setdefault("settings", {"channel_id": None, "auto_archive": True, "priority_backfill_version": 0, "lore_server_archive": False, "ai_queue": [], "ai_last_call": 0, "ai_error": None})
    mem["settings"].setdefault("channel_id", None)
    mem["settings"].setdefault("auto_archive", True)
    mem["settings"].setdefault("priority_backfill_version", 0)
    mem["settings"].setdefault("lore_server_archive", False)
    mem["settings"].setdefault("ai_queue", [])
    mem["settings"].setdefault("ai_last_call", 0)
    mem["settings"].setdefault("ai_error", None)
    return mem


def _now():
    return datetime.now(timezone.utc).isoformat()


def _queue_ai_message(guild_id, row, private_user_id=None):
    """Queue a message for optional background lore classification.

    Archiving is always local-first. This function only records work for the
    optional AI classifier; it never makes a network request itself. Duplicate
    message IDs are ignored and the queue is bounded so a long backfill or
    provider outage cannot grow memory without limit.
    """
    if not guild_id or not isinstance(row, dict):
        return False

    mem = _store(guild_id)
    settings = mem["settings"]
    queue = settings.setdefault("ai_queue", [])
    message_id = str(row.get("message_id") or "")
    if not message_id:
        return False

    # Do not queue the same message repeatedly when Discord events/backfills
    # overlap. Private DM ownership is retained for the classifier.
    for queued in queue:
        if str(queued.get("message_id") or "") == message_id:
            if private_user_id is not None:
                queued["private_user_id"] = str(private_user_id)
            return False

    item = dict(row)
    if private_user_id is not None:
        item["private_user_id"] = str(private_user_id)
        item["private"] = True
    item["content"] = str(item.get("content") or "")[:2500]

    queue.append(item)
    # Keep enough backlog for a temporary AI outage, but never let it grow
    # indefinitely during a large archive/backfill.
    settings["ai_queue"] = queue[-5000:]
    return True


def _is_hell_channel(channel):
    # Hell is part of campaign lore. It is no longer excluded from /lore or memory ingestion.
    return False



def _entity_profile(mem, key, name=None, entity_type="other"):
    """Return/create a durable structured lore profile for one entity.

    Profiles are intentionally separate from raw message evidence.  The AI can
    retrieve a compact profile first, then consult source messages only when
    needed.  This keeps long campaigns searchable and prevents old chatter from
    becoming the entity's current identity.
    """
    key = str(key or "").casefold().strip()
    if not key:
        return None
    profiles = mem.setdefault("entity_profiles", {})
    profile = profiles.setdefault(key, {
        "id": f"entity-{uuid.uuid4().hex[:12]}",
        "name": name or key,
        "type": entity_type or "other",
        "status": "unconfirmed",
        "authority": "unknown",
        "aliases": [],
        "summary": "",
        "backstory": "",
        "origin": "",
        "origin_details": "",
        "role": "",
        "titles": [],
        "species": "",
        "age": "",
        "personality": "",
        "motivations": [],
        "goals": [],
        "fears": [],
        "family": [],
        "faction": "",
        "current_status": "unknown",
        "current_location": "",
        "facts": [],
        "abilities": [],
        "weaknesses": [],
        "relationships": [],
        "locations": [],
        "items": [],
        "possessions": [],
        "sessions": [],
        "events": [],
        "first_appearance": None,
        "last_appearance": None,
        "history": [],
        "corrections": [],
        "death": None,
        "player_owner_id": None,
        "character_version": None,
        "previous_character_id": None,
        "next_character_id": None,
        "source_message_ids": [],
        "source_urls": [],
        "source_records": [],
        "first_seen": None,
        "last_updated": None,
        "last_updated_session": None,
        "created_at": _now(),
    })
    # Schema expansion is additive so old campaign data remains intact.
    defaults = {
        "origin_details":"", "titles":[], "species":"", "age":"", "personality":"",
        "motivations":[], "goals":[], "fears":[], "family":[], "faction":"",
        "current_location":"", "weaknesses":[], "possessions":[], "events":[]
    }
    for _field, _default in defaults.items():
        profile.setdefault(_field, _default.copy() if isinstance(_default, list) else _default)
    if name and not profile.get("name"):
        profile["name"] = name
    if entity_type and (profile.get("type") in {None, "other", ""}):
        profile["type"] = entity_type
    return profile


def _add_profile_fact(profile, text, source_row=None, status="unconfirmed", authority="unknown", session_number=None):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return
    facts = profile.setdefault("facts", [])
    key = text.casefold()
    if not any(str(f.get("text", "")).casefold() == key for f in facts if isinstance(f, dict)):
        facts.append({"text": text[:700], "status": status, "authority": authority,
                      "source_message_id": str((source_row or {}).get("message_id") or ""),
                      "source_url": (source_row or {}).get("jump_url"),
                      "created_at": (source_row or {}).get("created_at") or _now(),
                      "session_number": session_number})
    profile["facts"] = facts[-200:]
    if source_row:
        mid = str(source_row.get("message_id") or "")
        if mid and mid not in profile.setdefault("source_message_ids", []):
            profile["source_message_ids"].append(mid)
            profile["source_message_ids"] = profile["source_message_ids"][-200:]
        url = source_row.get("jump_url")
        if url and url not in profile.setdefault("source_urls", []):
            profile["source_urls"].append(url)
            profile["source_urls"] = profile["source_urls"][-100:]
    if not profile.get("first_seen"):
        profile["first_seen"] = (source_row or {}).get("created_at") or _now()
    profile["last_updated"] = _now()
    if session_number is not None:
        profile["last_updated_session"] = session_number
    # Current authority/status is monotonic: a GM correction/canon outranks
    # weaker evidence, while older evidence remains in facts/history.
    rank = {"unknown": 0, "unconfirmed": 1, "player": 2, "possible_canon": 3, "canon": 4, "gm_correction": 5}
    if rank.get(status, 0) >= rank.get(profile.get("status", "unknown"), 0):
        profile["status"] = status
    if rank.get(authority, 0) >= rank.get(profile.get("authority", "unknown"), 0):
        profile["authority"] = authority


def _ensure_player_profile(guild_id, message):
    mem = _store(guild_id)
    uid = str(getattr(getattr(message, "author", None), "id", ""))
    if not uid:
        return
    name = getattr(getattr(message, "author", None), "display_name", None) or getattr(getattr(message, "author", None), "name", uid)
    is_gm = uid in {str(x) for x in GM_USER_IDS}
    entity_type = "gm" if is_gm else "player"
    p = _entity_profile(mem, f"player:{uid}", name, entity_type)
    p["discord_user_id"] = uid
    p["account_role"] = "gm" if is_gm else "player"
    p["display_name"] = name
    p.setdefault("character_ids", [])
    p.setdefault("active_character_id", None)
    p.setdefault("character_history", [])
    p.setdefault("player_status", "active")
    p["status"] = "canon" if is_gm else p.get("status", "unconfirmed")
    p["authority"] = "gm" if is_gm else "player"
    mid = str(getattr(message, "id", ""))
    if mid and mid not in p.setdefault("source_message_ids", []):
        p["source_message_ids"].append(mid)
        p["source_message_ids"] = p["source_message_ids"][-300:]
    p["last_updated"] = _now()
    try:
        lore_index.upsert_profile(guild_id, f"player:{uid}", p)
    except Exception as exc:
        print(f"Player lore index warning: {type(exc).__name__}: {exc}")


def _new_character_profile(mem, name, player_id=None, source_row=None, session_number=None):
    """Create a distinct in-world character record for a player's current life.

    A Discord player may own many characters over a campaign. Character identity
    is therefore never keyed only by the player's Discord ID or by the name.
    Death permanently closes that character record; a replacement gets a new ID.
    """
    name = re.sub(r"\s+", " ", str(name or "")).strip()[:120]
    cid = f"character-{uuid.uuid4().hex[:12]}"
    key = f"character_instance:{cid}"
    profile = _entity_profile(mem, key, name, "character")
    profile["character_version"] = len([x for x in mem.get("entity_profiles", {}).values() if isinstance(x, dict) and x.get("player_owner_id") == str(player_id)]) + 1 if player_id else 1
    profile["player_owner_id"] = str(player_id) if player_id is not None else None
    profile["current_status"] = "alive"
    profile["status"] = "canon" if source_row and str(source_row.get("author_id")) in {str(x) for x in GM_USER_IDS} else "unconfirmed"
    profile["authority"] = "gm" if source_row and str(source_row.get("author_id")) in {str(x) for x in GM_USER_IDS} else "player"
    profile["first_appearance"] = {
        "session_number": session_number,
        "created_at": (source_row or {}).get("created_at") or _now(),
        "source_message_id": str((source_row or {}).get("message_id") or ""),
        "source_url": (source_row or {}).get("jump_url"),
    }
    profile["first_seen"] = profile["first_appearance"]["created_at"]
    if player_id is not None:
        pp = _entity_profile(mem, f"player:{player_id}", None, "player")
        pp.setdefault("character_ids", []).append(profile["id"])
        pp["active_character_id"] = profile["id"]
        pp.setdefault("character_history", []).append({
            "character_id": profile["id"], "name": name, "started_at": profile["first_seen"],
            "session_number": session_number, "status": "alive"
        })
    return profile


def _mark_character_dead(mem, character_id, source_row=None, session_number=None, cause=""):
    """Close a character's life without deleting its history."""
    profile = mem.get("entity_profiles", {}).get(str(character_id))
    if not profile or profile.get("type") != "character":
        return False
    profile["current_status"] = "dead"
    profile["death"] = {
        "declared_at": (source_row or {}).get("created_at") or _now(),
        "session_number": session_number,
        "cause": str(cause or "Unknown")[:700],
        "source_message_id": str((source_row or {}).get("message_id") or ""),
        "source_url": (source_row or {}).get("jump_url"),
    }
    profile.setdefault("history", []).append({"type":"death", **profile["death"]})
    owner = profile.get("player_owner_id")
    if owner:
        pp = mem.get("entity_profiles", {}).get(f"player:{owner}")
        if pp:
            if pp.get("active_character_id") == profile.get("id"):
                pp["active_character_id"] = None
            for item in pp.get("character_history", []):
                if item.get("character_id") == profile.get("id"):
                    item["status"] = "dead"
                    item["ended_at"] = profile["death"]["declared_at"]
                    item["death_cause"] = profile["death"]["cause"]
    return True


def _record_active_session_activity(guild_id, message, session_number):
    """Automatically marks a player as participating when they meaningfully chat during a live session.

    Attendance buttons are optional evidence/overrides. Actual game-channel activity is the
    primary participation signal, so late arrivals are still counted.
    """
    if session_number is None or not message or getattr(message, "guild", None) is None:
        return
    try:
        gm = item_state(guild_id).get("gm_tools") or {}
        if not gm.get("game_started"):
            # Before the formal session system existed, ordinary campaign chat is the
            # evidence for the inferred first session. Once formal sessions exist,
            # only the active game channel counts automatically.
            if int((gm.get("session_number") or 0)) > 0 or not _store(guild_id).get("settings", {}).get("campaign_origin_at"):
                return
            session_number = 1
        current = gm.get("current_session") or {}
        active_channel = current.get("channel_id")
        if gm.get("game_started") and active_channel and int(active_channel) != int(getattr(getattr(message, "channel", None), "id", 0)):
            return
        content = re.sub(r"\s+", " ", str(getattr(message, "content", "") or "")).strip()
        if len(content) < 2 and not getattr(message, "attachments", None):
            return
        player_id = str(getattr(getattr(message, "author", None), "id", ""))
        if not player_id:
            return
        pp = mem = _store(guild_id)
        profile = (mem.get("entity_profiles") or {}).get(f"player:{player_id}")
        character_id = profile.get("active_character_id") if isinstance(profile, dict) else None
        lore_index.record_participation(guild_id, session_number, player_id, character_id, message.created_at.isoformat())
        gm.setdefault("attendance", {})
        row = gm["attendance"].setdefault(player_id, {})
        row.update({"present": True, "auto_joined": True, "first_activity_at": row.get("first_activity_at") or message.created_at.timestamp(), "last_activity_at": message.created_at.timestamp()})
        if character_id:
            row["character_id"] = character_id
        # Keep the player profile's session roster explicit.
        if isinstance(profile, dict):
            profile.setdefault("sessions", [])
            if session_number not in profile["sessions"]:
                profile["sessions"].append(session_number)
                profile["sessions"] = profile["sessions"][-100:]
        save_item_data()
    except Exception as exc:
        print(f"Session participation index warning: {type(exc).__name__}: {exc}")


def _record_profile_history(profile, event_type, text, source_row=None, session_number=None):
    entry = {"type": event_type, "text": str(text or "")[:1200], "created_at": (source_row or {}).get("created_at") or _now(), "session_number": session_number, "source_message_id": str((source_row or {}).get("message_id") or ""), "source_url": (source_row or {}).get("jump_url")}
    profile.setdefault("history", []).append(entry)
    profile["history"] = profile["history"][-300:]
    if profile.get("first_appearance") is None:
        profile["first_appearance"] = {"session_number": session_number, "created_at": entry["created_at"], "source_message_id": entry["source_message_id"], "source_url": entry["source_url"]}


def _extract_entities(text):
    """Return likely named entities from campaign text without external AI calls.

    This intentionally stays conservative: capitalized multi-word names and
    Discord mentions are useful for lore indexing while ordinary sentence
    starts are ignored as much as practical.
    """
    text = text or ""
    found = []
    seen = set()

    # Discord user/channel/role mentions.
    for match in re.findall(r"<[@#!&]?(\d+)>|<@!?([0-9]+)>", text):
        value = next((x for x in match if x), None)
        if value and value not in seen:
            seen.add(value)
            found.append(value)

    # Capitalized names, including simple multi-word names.
    pattern = r"\b(?:[A-Z][A-Za-z'’-]{2,})(?:\s+(?:of|the|[A-Z][A-Za-z'’-]{2,})){0,3}\b"
    stop = {"The", "This", "That", "These", "Those", "When", "Where", "What", "Who", "How", "And", "But", "For", "With", "From", "After", "Before", "Then", "There", "Their", "They", "Your", "You", "Campaign", "Server", "Discord"}
    for candidate in re.findall(pattern, text):
        candidate = candidate.strip(" .,!?;:\"'()[]{}")
        if not candidate or candidate in stop or len(candidate) < 3:
            continue
        key = candidate.casefold()
        if key not in seen:
            seen.add(key)
            found.append(candidate)
    return found[:40]


def _ingest_lore(guild_id, message, priority=False):
    """Index a Discord message as lore evidence and update lightweight entities.

    This is synchronous because it only mutates the in-memory campaign state;
    callers save the state asynchronously afterward when appropriate.
    """
    if not message or getattr(message, "guild", None) is None:
        return
    mem = _store(guild_id)
    content = (getattr(message, "content", "") or "").strip()
    attachments = [
        {
            "filename": a.filename, "url": a.url, "content_type": a.content_type,
            "size": a.size,
        }
        for a in getattr(message, "attachments", [])
    ]
    # Capture temporal campaign metadata at ingestion time. The timestamp is
    # evidence age; the session number is the campaign chronology boundary.
    # This lets the historian distinguish an old statement from a later canon
    # correction instead of treating every archived message as equally current.
    try:
        gm_state = item_state(guild_id).get("gm_tools") or {}
        current_session = gm_state.get("current_session") or {}
        session_number = gm_state.get("session_number") if gm_state.get("game_started") else None
        session_title = current_session.get("title") or ""
        # The campaign may have started organically in Discord before /game start
        # existed. Those earliest campaign messages are therefore Session 1 by
        # inferred chronology, not discarded or forced into a later formal session.
        if session_number is None:
            settings = mem.setdefault("settings", {})
            origin = settings.get("campaign_origin_at")
            if not origin:
                settings["campaign_origin_at"] = message.created_at.isoformat()
                origin = settings["campaign_origin_at"]
            session_number = 1
            session_title = "Session #1 — Inferred Campaign Start"
    except Exception:
        session_number = None
        session_title = ""
    row = {
        "message_id": message.id,
        "channel_id": message.channel.id,
        "guild_id": guild_id,
        "author_id": message.author.id,
        "author_name": message.author.display_name,
        "content": content,
        "attachments": attachments,
        "jump_url": message.jump_url,
        "created_at": message.created_at.isoformat(),
        "observed_at": _now(),
        "session_number": session_number,
        "session_title": session_title,
        "source_kind": "priority_gm_writer" if priority else "server_message",
    }

    _record_active_session_activity(guild_id, message, session_number)
    campaign_store.archive_message(message, priority=priority)

    target = mem["priority_archive"] if priority else mem["archive"]
    existing = {str(x.get("message_id")) for x in target}
    if str(message.id) not in existing:
        target.append(row)
        if not priority:
            mem["archive"] = target[-5000:]

    # Store simple searchable facts for non-empty lore text.
    if content:
        fact_id = f"fact-{message.id}"
        facts = mem["lore_facts"]
        if not any(str(f.get("id")) == fact_id for f in facts):
            facts.append({
                "id": fact_id, "text": content[:2000], "status": "unconfirmed",
                "classification": "unclassified", "confidence": 0.0,
                "source_message_id": message.id, "source_url": message.jump_url,
                "created_at": message.created_at.isoformat(),
                "author_id": message.author.id, "author_name": message.author.display_name,
            })
            mem["lore_facts"] = facts[-2000:]
            campaign_store.upsert_fact(guild_id, facts[-1])

    # Every Discord player gets a durable profile. This is separate from their
    # in-world character profile, so OOC/player identity and campaign characters
    # cannot accidentally become the same entity.
    _ensure_player_profile(guild_id, message)

    # Track lightweight entity appearances for /lore entity.
    for name in _extract_entities(content):
        key = name.casefold().strip()
        if not key:
            continue
        entity = mem["entities"].setdefault(key, {"name": name, "mentions": 0, "sources": []})
        entity["name"] = entity.get("name") or name
        entity["mentions"] = int(entity.get("mentions", 0)) + 1
        profile = _entity_profile(mem, key, name, "other")
        if message.jump_url not in profile.setdefault("source_urls", []):
            profile["source_urls"].append(message.jump_url)
            profile["source_urls"] = profile["source_urls"][-200:]
        if str(message.id) not in profile.setdefault("source_message_ids", []):
            profile["source_message_ids"].append(str(message.id))
            profile["source_message_ids"] = profile["source_message_ids"][-300:]
        profile.setdefault("source_records", []).append({"message_id": str(message.id), "url": message.jump_url, "author_id": str(message.author.id), "author_name": message.author.display_name, "created_at": message.created_at.isoformat(), "session_number": session_number})
        profile["source_records"] = profile["source_records"][-200:]
        profile["last_updated"] = _now()
        if session_number is not None and session_number not in profile.setdefault("sessions", []):
            profile["sessions"].append(session_number)
            profile["sessions"] = profile["sessions"][-100:]
        if not profile.get("first_appearance"):
            profile["first_appearance"] = {"session_number": session_number, "created_at": message.created_at.isoformat(), "source_message_id": str(message.id), "source_url": message.jump_url}
        profile["last_appearance"] = {"session_number": session_number, "created_at": message.created_at.isoformat(), "source_message_id": str(message.id), "source_url": message.jump_url}
        sources = entity.setdefault("sources", [])
        if message.jump_url not in sources:
            sources.append(message.jump_url)
            entity["sources"] = sources[-100:]
        try:
            lore_index.upsert_profile(guild_id, key, profile)
        except Exception as exc:
            print(f"Lore index profile warning: {type(exc).__name__}: {exc}")

    return row

def _record_embed(record, include_gm=False):
    e = discord.Embed(title=record.get("name") or "Untitled memory", colour=discord.Colour.dark_grey())
    e.add_field(name="Type", value=record.get("type", "other").replace("_", " ").title(), inline=True)
    e.add_field(name="Status", value=record.get("visibility", "canon").replace("_", " ").title(), inline=True)
    if record.get("description"):
        e.add_field(name="Description", value=record["description"][:1024], inline=False)
    if record.get("source_message_url"):
        e.add_field(name="Source", value=f"[View original message]({record['source_message_url']})", inline=False)
    if include_gm and record.get("gm_notes"):
        e.add_field(name="GM Notes", value=record["gm_notes"][:1024], inline=False)
    e.set_footer(text=f"Memory ID: {record.get('id', '?')}")
    return e


def _save_record(guild_id, record):
    _store(guild_id)["records"].append(record)
    campaign_store.upsert_record(guild_id, record)
    save_item_data()


def _tokenize_query(q):
    return [x for x in re.findall(r"[\w'-]+", q.casefold()) if len(x) > 2]


def _score(query, text):
    q_tokens = _tokenize_query(query)
    if not q_tokens:
        return 0
    t = (text or "").casefold()
    score = 0
    for token in q_tokens:
        if token in t:
            score += 3
    exact = query.casefold().strip()
    if exact and exact in t:
        score += 12
    # Reward matches near the beginning because names/subjects are commonly
    # introduced there, while still allowing ordinary prose to match.
    if q_tokens and all(t.find(x) >= 0 for x in q_tokens):
        score += 2
    return score


def _best_lore(guild_id, query, include_gm=False, viewer_id=None):
    mem = _store(guild_id)
    rows = []

    def add(kind, row, text, bonus=0):
        score = _score(query, text) + bonus
        if score:
            rows.append((score, kind, row))

    # Dedicated profiles are the first retrieval layer: they are compact and
    # far less noisy than raw Discord messages.
    for key, profile in mem.get("entity_profiles", {}).items():
        text = " ".join([
            str(profile.get("name", "")), str(profile.get("type", "")),
            str(profile.get("summary", "")),
            " ".join(str(f.get("text", "")) for f in profile.get("facts", [])[-40:] if isinstance(f, dict)),
            " ".join(str(profile.get("aliases", []))),
        ])
        add("profile", profile, text, 18 if profile.get("status") in {"canon", "gm_correction"} else 10)

    for record in mem["records"]:
        visibility = record.get("visibility", "canon")
        if visibility == "gm_only" and not include_gm:
            continue
        if visibility == "private_user":
            allowed = {str(x) for x in record.get("allowed_user_ids", [])}
            if not include_gm and str(viewer_id or "") not in allowed:
                continue
        text = f"{record.get('name','')} {record.get('description','')} {record.get('type','')}"
        if include_gm:
            text += f" {record.get('gm_notes','')}"
        if visibility in {"joke", "chatter"}: continue
        bonus_map = {"canon": 12, "possible_canon": 8, "rumor": 5, "contradiction": 4, "private_user": 7, "gm_only": 6}
        add("record", record, text, bonus_map.get(visibility, 3))

    # Raw facts and message archives are GM evidence only. Never expose
    # conversational text, including DM-derived text, to ordinary players.
    if include_gm:
        for fact in mem["lore_facts"]:
            add("fact", fact, fact.get("text", ""), 2 if fact.get("status") == "confirmed" else 0)
        for msg in mem["priority_archive"]:
            add("priority", msg, msg.get("content", ""), 1)
        for msg in mem["archive"]:
            add("archive", msg, msg.get("content", ""))

    if include_gm:
        try:
            for msg in campaign_store.search_server_messages(guild_id, query, limit=80):
                text = msg.get("content", "")
            if msg.get("author_name"):
                text = f"{msg.get('author_name')}: {text}"
            if msg.get("channel_id"):
                text = f"[server channel {msg.get('channel_id')}] {text}"
            bonus = 4 if str(msg.get("author_id")) in {str(x) for x in GM_USER_IDS} else 1
            add("archive", msg, text, bonus)
        except Exception as exc:
            print(f"Campaign database search warning: {type(exc).__name__}: {exc}")

    rows.sort(key=lambda x: (x[0], x[2].get("created_at", "")), reverse=True)
    return rows[:24]


def _session_context(guild_id, session_number, include_gm=False, player_id=None, character_id=None):
    """Return one exact completed session, including recorded chronology.

    This is deliberately separate from semantic lore search. Questions such as
    "what happened in the first session" are chronological requests, so the
    historian must retrieve Session #1 rather than whichever lore records happen
    to contain the words "first" or "session".
    """
    mem = _store(guild_id)
    try:
        state = item_state(guild_id)
        gm = (state.get("gm_tools") or {})
        history = list(gm.get("session_history") or [])
    except Exception:
        return None
    target = None
    for row in history:
        try:
            if int(row.get("session_number", -1)) == int(session_number):
                target = row
                break
        except Exception:
            continue
    if not target and int(session_number) == 1:
        # Session 1 may predate the formal /game start system. In that case use
        # the earliest archived campaign messages as the real first session.
        archived = [x for x in (mem.get("archive") or []) if int(x.get("session_number", -1) or -1) == 1]
        if archived:
            archived.sort(key=lambda x: x.get("created_at", ""))
            target = {
                "session_number": 1,
                "title": "Session #1 — Inferred Campaign Start",
                "started_at": archived[0].get("created_at"),
                "ended_at": archived[-1].get("created_at"),
                "events": [], "attendance": {}, "today": "",
                "ai_recap": {}, "inferred": True,
            }
    if not target:
        return None
    recap = target.get("ai_recap") or {}
    if not isinstance(recap, dict):
        recap = {}
    events = list(target.get("events") or [])
    # Fetch the actual session transcript as supporting chronology. This is
    # stronger than guessing from global lore search results.
    try:
        rows = campaign_store.messages_between(guild_id, target.get("started_at", ""), target.get("ended_at", ""), limit=3500)
    except Exception:
        rows = []
    transcript = []
    for row in rows[-300:]:
        text = re.sub(r"\s+", " ", str(row.get("content") or "")).strip()
        if text:
            transcript.append({
                "created_at": row.get("created_at"),
                "author_name": row.get("author_name") or "Unknown",
                "author_id": row.get("author_id"),
                "content": text[:900],
            })
    return {
        "session_number": target.get("session_number"),
        "title": target.get("title") or f"Session #{target.get('session_number')}",
        "started_at": target.get("started_at"),
        "ended_at": target.get("ended_at"),
        "today": target.get("today") or "",
        "events": events[-100:],
        "attendance": target.get("attendance") or {},
        "recap": recap,
        "transcript": transcript,
    }


def _latest_session_context(guild_id, include_gm=False):
    """Return the newest completed session recap/history as high-priority context.

    Session recaps are the campaign's freshness boundary: newer completed
    sessions take precedence over older lore when the campaign state changes.
    """
    try:
        state = item_state(guild_id)
        gm = (state.get("gm_tools") or {})
        history = list(gm.get("session_history") or [])
    except Exception:
        return None
    completed = [x for x in history if isinstance(x, dict) and x.get("ended_at")]
    if not completed:
        return None
    session = completed[-1]
    recap = session.get("ai_recap") or {}
    if not isinstance(recap, dict):
        recap = {}
    return {
        "session_number": session.get("session_number"),
        "title": session.get("title") or f"Session #{session.get('session_number')}",
        "ended_at": session.get("ended_at"),
        "major_events": list(recap.get("major_events") or []),
        "characters_involved": list(recap.get("characters_involved") or []),
        "major_discoveries": list(recap.get("major_discoveries") or []),
        "unresolved_events": list(recap.get("unresolved_events") or []),
        "new_threats": list(recap.get("new_threats") or []),
        "lore_events": list(recap.get("lore_events") or []),
    }


def _lore_source_text(kind, row):
    if kind == "record":
        return row.get("description") or "No description recorded."
    if kind == "fact":
        return row.get("text") or ""
    if kind == "profile":
        facts = [str(f.get("text", "")) for f in row.get("facts", [])[-12:] if isinstance(f, dict)]
        return f"{row.get('name','')} ({row.get('type','other')}): {row.get('summary','')} {' '.join(facts)}".strip()
    return row.get("content") or "[attachment only]"


def _format_date(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%b %d, %Y")
    except Exception:
        return "Unknown date"


def _build_lore_answer(guild_id, question, include_gm=False):
    rows = _best_lore(guild_id, question, include_gm)
    latest = _latest_session_context(guild_id, include_gm=include_gm)
    if not rows and not latest:
        return None, []
    # Fallback answers should still read like an actual lore explanation even
    # when an AI provider is unavailable. Do not dump raw source messages.
    canon = [x for x in rows if x[1] in {"record", "profile"}][:10]
    facts = [x for x in rows if x[1] == "fact"][:10]
    evidence = [x for x in rows if x[1] in {"priority", "archive"}][:8] if include_gm else []
    lines = []
    if latest:
        lines.append(f"**Most recent session — Session #{latest.get('session_number')}:**")
        for label, key in (
            ("Major events", "major_events"),
            ("Discoveries", "major_discoveries"),
            ("Unresolved", "unresolved_events"),
        ):
            values = latest.get(key) or []
            if values:
                lines.append(f"• {label}: " + "; ".join(str(x)[:500] for x in values[:5]))
        lines.append("")
    if canon:
        lines.append("**Profile / established lore:**")
        for _, _, r in canon:
            lines.append(f"• **{r.get('name','Untitled')}** — {_lore_source_text('record', r)[:500]}")
    if facts:
        lines.append("\n**Known facts & events:**")
        seen = set()
        for _, _, r in facts:
            text = _lore_source_text('fact', r)[:500].strip()
            key = text.casefold()
            if not text or key in seen:
                continue
            seen.add(key)
            status = r.get("status", "unconfirmed").replace("_", " ").title()
            lines.append(f"• [{status}] {text}")
    if evidence and not canon and not facts:
        # If only archived GM evidence is available, present it as supporting
        # evidence rather than using the old source-dump heading.
        lines.append("\n**Known evidence:**")
        for _, kind, r in evidence[:8]:
            text = _lore_source_text(kind, r)[:500].strip()
            if text:
                lines.append(f"• {text}")
    lines.append("\n**Certainty:** Confirmed canon is treated as established. Archived evidence and recorded facts may be unconfirmed. Unknown details are not invented.")
    return "\n".join(lines)[:3900], rows


async def generate_post_game_review(guild_id, session):
    """Generate an independent, GM-only post-game review.

    The review evaluates fairness, player agency, consistency, participation,
    pacing, and potential bias. It deliberately excludes dice/RNG analysis.
    It never changes campaign canon.
    """
    start = session.get("started_at")
    end = session.get("ended_at")
    try:
        rows = campaign_store.messages_between(guild_id, start, end, limit=12000)
    except Exception as exc:
        print(f"Post-game review database warning: {type(exc).__name__}: {exc}")
        rows = []

    transcript_parts = []
    for row in rows:
        content = (row.get("content") or "").strip()
        if not content:
            continue
        author = row.get("author_name") or "Unknown"
        created = row.get("created_at") or ""
        transcript_parts.append(f"[{created}] {author}: {content[:1800]}")
    transcript = "\n".join(transcript_parts)

    if not transcript:
        return None

    # Keep the prompt bounded while preserving the beginning and end of a long
    # session. The database remains the authoritative source for later review.
    max_chars = 42000
    if len(transcript) > max_chars:
        transcript = transcript[:21000] + "\n[Middle of transcript omitted for this review]\n" + transcript[-21000:]

    player_ids = session.get("player_ids") or []
    prompt = f"""
You are an independent tabletop RPG campaign analyst reviewing a completed game.
You are NOT the GM's servant and you are NOT the final authority. The GM has
final narrative authority, but your job is to honestly identify strengths,
weaknesses, possible unfairness, and possible bias using evidence.

Do NOT analyze dice, RNG, rolls, probability, or random outcomes. Do not mention
dice analysis in the review.

Do not invent facts. Distinguish observed evidence from interpretation. If there
is not enough evidence for a conclusion, say that evidence is insufficient.
Do not automatically side with either the GM or a player.

Evaluate:
- overall session quality
- player agency and meaningful choices
- fairness and proportionality of consequences
- consistency with previously established campaign rules when evidence exists
- possible GM bias
- possible player-side manipulation or unreasonable behavior
- participation balance and whether anyone was unintentionally sidelined
- pacing
- story and roleplay quality
- whether players had enough information to make reasonable decisions
- whether consequences were reasonably telegraphed
- continuity/lore concerns
- practical suggestions for the next session

If a player appears justified in a complaint, say so clearly and explain why.
If the GM appears justified, say so clearly and explain why.
Do not frame disagreement as an attack on the GM.

Return ONLY valid JSON with this exact structure:
{{
  "overall_score": 0,
  "story_score": 0,
  "player_agency_score": 0,
  "fairness_score": 0,
  "pacing_score": 0,
  "participation_score": 0,
  "consistency_score": 0,
  "summary": "string",
  "strengths": ["string"],
  "concerns": ["string"],
  "player_defense": ["string"],
  "gm_defense": ["string"],
  "possible_bias": ["string"],
  "agency_issues": ["string"],
  "continuity_issues": ["string"],
  "next_session_suggestions": ["string"],
  "evidence_confidence": "high|medium|low"
}}

Scores are 1-10. Use 0 only if there is genuinely insufficient evidence.

Session number: {session.get("session_number")}
Session title: {session.get("title") or ""}
Player IDs recorded at start: {player_ids}

SESSION TRANSCRIPT:
{transcript}
""".strip()

    try:
        raw = await asyncio.to_thread(complete, prompt, True, 1800)
        review = json.loads(raw)
        if not isinstance(review, dict):
            raise ValueError("AI review was not an object")
        return review
    except Exception as exc:
        print(f"Post-game AI review warning for {guild_id}: {type(exc).__name__}: {exc}")
        return None


class MemoryModal(discord.ui.Modal, title="Save Campaign Memory"):
    name = discord.ui.TextInput(label="Name", max_length=100, required=True)
    description = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, max_length=2000, required=False)
    gm_notes = discord.ui.TextInput(label="GM notes", style=discord.TextStyle.paragraph, max_length=1500, required=False)

    def __init__(self, source_message, record_type="other", visibility="canon"):
        super().__init__(); self.source_message = source_message; self.record_type = record_type; self.visibility = visibility

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None or not is_staff(interaction):
            return await interaction.response.send_message("GM/admin only.", ephemeral=True)
        record = {
            "id": f"memory-{uuid.uuid4().hex[:10]}", "name": self.name.value.strip(), "type": self.record_type,
            "visibility": self.visibility, "description": self.description.value.strip(), "gm_notes": self.gm_notes.value.strip(),
            "guild_id": interaction.guild.id, "created_by": interaction.user.id, "created_at": _now(),
            "source_channel_id": self.source_message.channel.id, "source_message_id": self.source_message.id,
            "source_message_url": self.source_message.jump_url, "attachments": [], "related_ids": [],
        }
        _save_record(interaction.guild.id, record)
        await interaction.response.send_message(embed=_record_embed(record, include_gm=True), ephemeral=True)


class MemorySaveView(discord.ui.View):
    def __init__(self, message):
        super().__init__(timeout=300); self.message = message

    @discord.ui.button(label="Save as Canon", style=discord.ButtonStyle.secondary)
    async def canon(self, interaction, button):
        if not is_staff(interaction): return await interaction.response.send_message("GM/admin only.", ephemeral=True)
        await interaction.response.send_modal(MemoryModal(self.message, "other", "canon"))

    @discord.ui.button(label="Save as GM Only", style=discord.ButtonStyle.danger)
    async def gm_only(self, interaction, button):
        if not is_staff(interaction): return await interaction.response.send_message("GM/admin only.", ephemeral=True)
        await interaction.response.send_modal(MemoryModal(self.message, "other", "gm_only"))

    @discord.ui.button(label="Ignore", style=discord.ButtonStyle.secondary)
    async def ignore(self, interaction, button):
        await interaction.response.edit_message(content="Campaign memory suggestion dismissed.", embed=None, view=None)


async def _remember_context_callback(interaction: discord.Interaction, message: discord.Message):
    if interaction.guild is None or not is_staff(interaction):
        return await interaction.response.send_message("GM/admin only.", ephemeral=True)
    await interaction.response.send_modal(MemoryModal(message))


remember_context = app_commands.ContextMenu(name="Remember for Campaign", callback=_remember_context_callback)


@LORE_GROUP.command(name="search", description="Search campaign lore and return the strongest relevant evidence.")
async def memory_search(interaction: discord.Interaction, query: str):
    if interaction.guild is None: return await interaction.response.send_message("Server only.", ephemeral=True)
    rows = _best_lore(interaction.guild.id, query, is_staff(interaction), interaction.user.id)
    if not rows: return await interaction.response.send_message("No campaign memory matched that search.", ephemeral=True)
    e = discord.Embed(title=f"Campaign Memory: {query}", colour=discord.Colour.dark_grey())
    for score, kind, row in rows[:10]:
        if kind == "record":
            value = f"{row.get('type','other').title()} — {row.get('visibility','canon').replace('_',' ').title()}\n{row.get('description','No description.')[:300]}"
            name = row.get("name", "Untitled")
        else:
            value = row.get("content") or row.get("text") or "[no text]"
            name = f"{kind.title()} source"
        e.add_field(name=name[:256], value=value[:1024], inline=False)
    await interaction.response.send_message(embed=e)


@LORE_GROUP.command(name="info", description="View one saved campaign lore record.")
async def memory_info(interaction: discord.Interaction, memory_id: str):
    if interaction.guild is None: return await interaction.response.send_message("Server only.", ephemeral=True)
    include_gm = is_staff(interaction); mem = _store(interaction.guild.id); q = memory_id.casefold().strip()
    record = next((r for r in mem["records"] if r.get("id", "").casefold() == q or r.get("name", "").casefold() == q), None)
    if not record or (record.get("visibility") == "gm_only" and not include_gm): return await interaction.response.send_message("Memory record not found.", ephemeral=True)
    await interaction.response.send_message(embed=_record_embed(record, include_gm=include_gm))


@LORE_GROUP.command(name="ask", description="Ask the campaign archive a useful lore question and get its known history.")
async def lore_ask(interaction: discord.Interaction, question: str):
    if interaction.guild is None:
        return await interaction.response.send_message("Server only.", ephemeral=True)

    # AI calls can exceed Discord's 3-second initial-response window. Defer
    # immediately, then finish with a follow-up response.
    await interaction.response.defer(ephemeral=True, thinking=True)

    include_gm = is_staff(interaction)
    answer = None
    try:
        answer = await _gemini_lore_answer(
            interaction.guild.id,
            question,
            interaction.user.id,
            include_gm=include_gm,
        )
    except Exception as exc:
        print(f"Lore ask warning: {type(exc).__name__}: {exc}")

    if not answer:
        # Never fall back to a raw-message/fact dump. If AI is unavailable, say so
        # rather than defeating the purpose of /lore ask by exposing the archive.
        if not _ai_enabled():
            answer = "The lore AI is not configured right now, so I cannot synthesize this answer."
        else:
            answer = "The lore AI could not answer that right now. No raw archive messages were exposed."

    await interaction.followup.send(
        embed=discord.Embed(
            title=f"Lore — {question[:180]}",
            description=answer[:4000],
            colour=discord.Colour.dark_grey(),
        ),
        ephemeral=True,
    )



@LORE_GROUP.command(name="new-character", description="Create a fresh character instance for a player; prior characters remain in history.")
@app_commands.describe(player="Discord player who owns the character.", name="New in-world character name.")
async def lore_new_character(interaction: discord.Interaction, player: discord.Member, name: str):
    if interaction.guild is None or not is_staff(interaction):
        return await interaction.response.send_message("GM/admin only.", ephemeral=True)
    name = name.strip()[:120]
    if not name:
        return await interaction.response.send_message("Character name is required.", ephemeral=True)
    mem = _store(interaction.guild.id)
    profile = _new_character_profile(mem, name, player.id, {"author_id": interaction.user.id, "message_id": "", "jump_url": None, "created_at": _now()}, None)
    save_item_data()
    await interaction.response.send_message(f"Created **{name}** as a new character instance for {player.mention}. Character ID: `{profile['id']}`. Any previous character remains in that player's history.", ephemeral=True)


@LORE_GROUP.command(name="character-death", description="GM: permanently mark a specific character instance dead without deleting its history.")
@app_commands.describe(character_id="Character instance ID.", cause="How the character died, if known.")
async def lore_character_death(interaction: discord.Interaction, character_id: str, cause: str = ""):
    if interaction.guild is None or not is_staff(interaction):
        return await interaction.response.send_message("GM/admin only.", ephemeral=True)
    mem = _store(interaction.guild.id)
    ok = _mark_character_dead(mem, character_id.strip(), {"author_id": interaction.user.id, "message_id": str(interaction.id), "jump_url": None, "created_at": _now()}, None, cause)
    if not ok:
        return await interaction.response.send_message("Character instance not found.", ephemeral=True)
    save_item_data()
    await interaction.response.send_message("Character permanently marked **DEAD**. The profile and history remain preserved; the player can create a new character instance separately.", ephemeral=True)


@LORE_GROUP.command(name="npc", description="Build an AI campaign profile for an NPC using established lore.")
@app_commands.describe(name="NPC or character name to investigate.")
async def lore_npc(interaction: discord.Interaction, name: str):
    if interaction.guild is None:
        return await interaction.response.send_message("Server only.", ephemeral=True)
    name = name.strip()[:120]
    if not name:
        return await interaction.response.send_message("Provide an NPC name.", ephemeral=True)

    await interaction.response.defer(ephemeral=True, thinking=True)
    include_gm = is_staff(interaction)
    rows = _best_lore(interaction.guild.id, name, include_gm=include_gm, viewer_id=interaction.user.id)
    if not rows:
        return await interaction.followup.send(
            f"I couldn't find enough campaign information about **{discord.utils.escape_markdown(name)}**.",
            ephemeral=True,
        )

    evidence = []
    for _, kind, row in rows[:14]:
        if kind == "record":
            label = "CANON"
            text = row.get("description") or ""
            if include_gm and row.get("gm_notes"):
                text += f" GM notes: {row.get('gm_notes')}"
        elif kind == "fact":
            label = "RECORDED EVIDENCE"
            text = row.get("text") or ""
        else:
            label = "GM EVIDENCE"
            text = row.get("content") or ""
        text = re.sub(r"\s+", " ", str(text)).strip()[:700]
        if text:
            evidence.append(f"[{label}] {text}")

    if not evidence:
        return await interaction.followup.send("There is not enough usable campaign evidence for that NPC.", ephemeral=True)

    visibility_rule = (
        "You are answering the GM. GM-only evidence may inform the profile, but never expose private source text or internal archive details."
        if include_gm else
        "You are answering a player. Use only public/canon-safe evidence and never reveal GM-only or private information."
    )
    prompt = f"""You are the campaign historian for a GM-authored D&D campaign.
Build a useful NPC profile from the supplied evidence.

IMPORTANT:
- The GM controls the story. You are an assistant, not the GM.
- Never invent a fact and present it as established.
- Distinguish:
  CANON = explicitly established.
  INFERENCE = reasonable interpretation supported by canon.
  SPECULATION = possible idea, clearly marked NOT CANON.
- If something is unknown, say UNKNOWN instead of filling the gap.
- {visibility_rule}
- Do not mention the archive, database, AI, source messages, IDs, or internal systems.

Return a concise profile with exactly these sections:
## Known
## Relationships
## Unknown
## Recent Developments
## AI Analysis
In AI Analysis, keep inferences clearly labeled and do not create new canon.

NPC:
{name}

EVIDENCE:
{chr(10).join(evidence)}
"""
    try:
        answer = (await asyncio.to_thread(_ai_sync_text, prompt)).strip()[:3800]
    except Exception as exc:
        print(f"NPC lore warning: {type(exc).__name__}: {exc}")
        answer = None
    if not answer:
        return await interaction.followup.send(
            "The lore AI could not build the NPC profile right now. No raw archive messages were exposed.",
            ephemeral=True,
        )
    await interaction.followup.send(
        embed=discord.Embed(
            title=f"NPC — {name}",
            description=answer,
            colour=discord.Colour.dark_grey(),
        ),
        ephemeral=True,
    )

@LORE_GROUP.command(name="entity", description="Show the campaign history, known facts, and appearances of an entity.")
async def lore_entity(interaction: discord.Interaction, name: str):
    if interaction.guild is None:
        return await interaction.response.send_message("Server only.", ephemeral=True)
    mem = _store(interaction.guild.id)
    rows = _best_lore(interaction.guild.id, name, is_staff(interaction), interaction.user.id)
    entity = mem["entities"].get(name.casefold().strip())
    if not entity and not rows:
        return await interaction.response.send_message("The archive has no recorded history for that entity.", ephemeral=True)
    e = discord.Embed(title=f"Lore — {name}", colour=discord.Colour.dark_grey())
    if entity:
        e.add_field(
            name="Presence in the archive",
            value=f"Mentioned **{entity.get('mentions', 0)}** times across recorded messages.\n"
                  f"Tracked sources: **{len(entity.get('sources', []))}**",
            inline=False,
        )
    for _, kind, row in rows[:7]:
        text = _lore_source_text(kind, row)
        label = "Canon" if kind == "record" else ("Recorded fact" if kind == "fact" else "Source evidence")
        e.add_field(name=label, value=text[:900], inline=False)
    await interaction.response.send_message(embed=e)



async def _lore_report(interaction: discord.Interaction, title: str, question: str, query: str, gm_only: bool = False):
    if interaction.guild is None: return await interaction.response.send_message("Server only.", ephemeral=True)
    if gm_only and not is_staff(interaction): return await interaction.response.send_message("GM/admin only.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    include_gm=is_staff(interaction)
    rows=_best_lore(interaction.guild.id, query, include_gm=include_gm, viewer_id=interaction.user.id)
    if not rows: return await interaction.followup.send("I couldn't find enough campaign knowledge for that yet.", ephemeral=True)
    evidence=[]
    for _,kind,row in rows[:28]:
        if kind=="record":
            status=str(row.get("visibility","canon")).replace("_"," ").upper()
            evidence.append(f"[{status} | confidence {float(row.get('confidence',1) or 1):.0%}] {row.get('name','Untitled')}: {(row.get('description') or '')[:700]}")
        elif include_gm:
            evidence.append(f"[SOURCE EVIDENCE] {_lore_source_text(kind,row)[:700]}")
    if not evidence: return await interaction.followup.send("No usable campaign evidence was found.", ephemeral=True)
    visibility_rule="You are answering the GM; GM-only evidence may inform the answer but private source text must never be exposed." if include_gm else "You are answering a player; never reveal GM-only or private information."
    prompt = (
        "You are the campaign historian for a D&D campaign.\n"
        "Create a useful campaign-aware report for the requested topic.\n\n"
        "Rules:\n"
        "- Never treat a joke as canon.\n"
        "- CANON is established. POSSIBLE CANON, RUMOR, and CONTRADICTION must be labeled clearly.\n"
        "- INFERENCE is reasoning from supplied evidence, not a new fact.\n"
        "- SPECULATION is optional and must be labeled NOT CANON.\n"
        "- If chronology is unknown, say so. Never invent dates.\n"
        "- " + visibility_rule + "\n"
        "- Do not mention databases, archives, source IDs, or internal systems.\n\n"
        "REPORT: " + title + "\n"
        "REQUEST: " + question + "\n\n"
        "EVIDENCE:\n" + "\n".join(evidence)
    )
    try: answer=(await asyncio.to_thread(_ai_sync_text,prompt)).strip()[:3900]
    except Exception as exc: print(f"Lore report warning: {type(exc).__name__}: {exc}"); answer=None
    if not answer: return await interaction.followup.send("The lore AI could not build this report right now.", ephemeral=True)
    await interaction.followup.send(embed=discord.Embed(title=title,description=answer,colour=discord.Colour.dark_grey()),ephemeral=True)

@LORE_GROUP.command(name="location", description="Build an AI profile of a campaign location.")
@app_commands.describe(name="Location to investigate.")
async def lore_location(interaction: discord.Interaction, name: str):
    await _lore_report(interaction,f"Location — {name[:120]}",f"What do we know about {name}? Include what players know, events, NPCs, items, entrances, unresolved mysteries, and previous appearances.",name)

@LORE_GROUP.command(name="timeline", description="Reconstruct the campaign timeline from established evidence.")
async def lore_timeline(interaction: discord.Interaction):
    await _lore_report(interaction,"Campaign Timeline","Reconstruct the campaign chronologically. Group events by known dates, seasons, sessions, or relative order. Explicitly flag uncertain chronology and never invent dates.","campaign events history sessions timeline")

@LORE_GROUP.command(name="mysteries", description="Find unresolved mysteries and dangling story threads.")
async def lore_mysteries(interaction: discord.Interaction):
    await _lore_report(interaction,"Active Campaign Mysteries","Identify unresolved mysteries, unanswered questions, missing people/items, unexplained events, and dangling plot threads. Do not invent mysteries unsupported by evidence.","mystery unresolved unknown missing unexplained unanswered")

@LORE_GROUP.command(name="relationships", description="Explain known relationships around an NPC, faction, or character.")
@app_commands.describe(name="NPC, character, faction, or other entity.")
async def lore_relationships(interaction: discord.Interaction, name: str):
    await _lore_report(interaction,f"Relationships — {name[:120]}",f"Map the known relationships of {name}. For each relationship, explain the entity involved and whether it is canon, possible, rumored, inferred, or unknown. Do not invent relationships.",name)

@LORE_GROUP.command(name="contradictions", description="GM: show unresolved lore contradictions detected by the archive.")
async def lore_contradictions(interaction: discord.Interaction):
    if interaction.guild is None or not is_staff(interaction): return await interaction.response.send_message("GM/admin only.", ephemeral=True)
    mem = _store(interaction.guild.id)
    # Lightweight contradiction detector: same key subject + strong opposing verbs in stored facts.
    facts = mem["lore_facts"][-2000:]
    groups = {}
    for fact in facts:
        text = fact.get("text", "")
        names = _extract_entities(text)
        for name in names: groups.setdefault(name.casefold(), []).append(fact)
    contradictions = []
    opposing = [("died", "alive"), ("destroyed", "exists"), ("left", "returned"), ("killed", "survived")]
    for name, rows in groups.items():
        joined = " ".join(r.get("text", "").casefold() for r in rows)
        for a, b in opposing:
            if a in joined and b in joined:
                contradictions.append((name, rows[-2:]))
                break
    if not contradictions: return await interaction.response.send_message("No obvious contradictions were detected. This is only a flagging system; GMs decide canon.", ephemeral=True)
    e = discord.Embed(title="⚠️ Unresolved Lore Contradictions", colour=discord.Colour.dark_grey())
    for name, rows in contradictions[:10]:
        e.add_field(name=name[:256], value="\n".join(r.get("text", "")[:400] for r in rows), inline=False)
    await interaction.response.send_message(embed=e)


@ADMIN_MEMORY_GROUP.command(name="channel", description="GM: set a channel for automatic raw campaign archiving.")
async def memory_channel(interaction: discord.Interaction, channel: discord.TextChannel, enabled: bool = True):
    if interaction.guild is None or not is_staff(interaction): return await interaction.response.send_message("GM/admin only.", ephemeral=True)
    mem = _store(interaction.guild.id); mem["settings"]["channel_id"] = channel.id; mem["settings"]["auto_archive"] = enabled; save_item_data()
    await interaction.response.send_message(f"Campaign memory archiving {'enabled' if enabled else 'disabled'} for {channel.mention}.", ephemeral=True)


@ADMIN_MEMORY_GROUP.command(name="priority", description="GM: view the four priority lore-source accounts and archive counts.")
async def memory_priority(interaction: discord.Interaction):
    if interaction.guild is None or not is_staff(interaction): return await interaction.response.send_message("GM/admin only.", ephemeral=True)
    mem = _store(interaction.guild.id)
    await interaction.response.send_message("Priority lore accounts:\n" + "\n".join(f"• <@{uid}> — archived messages: {sum(1 for x in mem['priority_archive'] if str(x.get('author_id')) == uid)}" for uid in sorted(PRIORITY_LORE_USERS)), ephemeral=True)


@ADMIN_MEMORY_GROUP.command(name="status", description="GM: show persistent campaign memory/database status.")
async def memory_status(interaction: discord.Interaction):
    if interaction.guild is None or not is_staff(interaction):
        return await interaction.response.send_message("GM/admin only.", ephemeral=True)
    mem = _store(interaction.guild.id)
    settings = mem["settings"]
    count = campaign_store.message_count(interaction.guild.id)
    sessions = campaign_store.session_count(interaction.guild.id)
    await interaction.response.send_message(
        f"🧠 **Campaign Memory Status**\n\n"
        f"Database messages: **{count:,}**\n"
        f"Stored sessions: **{sessions:,}**\n"
        f"JSON memory cache: **{len(mem.get('archive', [])):,}** recent messages\n"
        f"AI queue: **{len(settings.get('ai_queue', [])):,}**\n"
        f"Backfill running: **{bool(settings.get('backfill_running'))}**",
        ephemeral=True)


@ADMIN_MEMORY_GROUP.command(name="backfill", description="GM: backfill historical campaign messages into memory.")
async def memory_backfill(interaction: discord.Interaction):
    if interaction.guild is None or not is_staff(interaction):
        return await interaction.response.send_message("GM/admin only.", ephemeral=True)

    mem = _store(interaction.guild.id)
    settings = mem["settings"]
    if settings.get("backfill_running"):
        return await interaction.response.send_message("Memory backfill is already running. Use **/gm-memory priority** to check the current priority archive count.", ephemeral=True)

    settings["backfill_running"] = True
    settings["backfill_started_at"] = _now()
    settings["backfill_scanned"] = 0
    settings["backfill_imported"] = 0
    settings["backfill_failed_channels"] = []
    save_item_data()

    await interaction.response.send_message(
        "🧠 **Memory backfill started.**\n\n"
        "I'm loading historical messages in the background now. This version backfills **all accessible campaign text channels**, not just the four priority accounts.\n\n"
        "You can keep using the bot while it runs.",
        ephemeral=True,
    )

    asyncio.create_task(_run_memory_backfill(interaction.guild))


async def _run_memory_backfill(guild: discord.Guild):
    """Load historical messages without making AI calls during the scan.

    Backfill is deliberately local-first: Discord history is imported into the
    campaign memory archive/lore index first. AI classification can then process
    the queued messages in normal batches, so exhausted providers never prevent
    the historical archive from loading.
    """
    mem = _store(guild.id)
    settings = mem["settings"]
    existing_archive = {str(x.get("message_id")) for x in mem.get("archive", [])}
    existing_priority = {str(x.get("message_id")) for x in mem.get("priority_archive", [])}
    existing_facts = {str(x.get("id")) for x in mem.get("lore_facts", [])}
    failed = []
    scanned = 0
    imported = 0
    queued = 0

    try:
        channels = sorted(guild.text_channels, key=lambda c: c.position)
        for channel in channels:
            perms = channel.permissions_for(guild.me)
            if not perms.view_channel or not perms.read_message_history:
                continue
            try:
                async for message in channel.history(limit=None, oldest_first=True):
                    scanned += 1
                    if message.author.bot:
                        continue

                    mid = str(message.id)
                    is_priority = str(message.author.id) in PRIORITY_LORE_USERS

                    # Priority messages are retained separately as well as in the
                    # normal campaign archive so they can be queried as high-value
                    # lore sources later.
                    if is_priority and mid not in existing_priority:
                        _ingest_lore(guild.id, message, priority=True)
                        existing_priority.add(mid)
                        imported += 1

                    if mid not in existing_archive:
                        _ingest_lore(guild.id, message, priority=False)
                        existing_archive.add(mid)
                        imported += 1

                    # Queue only messages that do not already have a fact entry.
                    # Do not invoke AI here; the normal classifier will consume the
                    # queue later and automatically use the provider health manager.
                    fact_id = f"fact-{message.id}"
                    if (message.content or "").strip() and fact_id not in existing_facts:
                        _queue_ai_message(guild.id, {
                            "message_id": message.id,
                            "channel_id": channel.id,
                            "guild_id": guild.id,
                            "author_id": message.author.id,
                            "author_name": message.author.display_name,
                            "content": (message.content or "")[:2500],
                            "attachments": [],
                            "jump_url": message.jump_url,
                            "created_at": message.created_at.isoformat(),
                        })
                        queued += 1
                        existing_facts.add(fact_id)

                    if scanned % 500 == 0:
                        settings["backfill_scanned"] = scanned
                        settings["backfill_imported"] = imported
                        settings["backfill_queued"] = queued
                        await asyncio.to_thread(save_item_data)
                        await asyncio.sleep(0)
            except Exception as exc:
                failed.append(f"{channel.name}: {type(exc).__name__}")

        settings["backfill_scanned"] = scanned
        settings["backfill_imported"] = imported
        settings["backfill_queued"] = queued
        settings["backfill_failed_channels"] = failed[:25]
        settings["backfill_running"] = False
        settings["priority_backfill_version"] = 2
        settings["backfill_finished_at"] = _now()
        await asyncio.to_thread(save_item_data)
        print(f"Memory backfill complete for guild {guild.id}: scanned={scanned}, imported={imported}, queued={queued}, failed={len(failed)}")
    except Exception as exc:
        settings["backfill_running"] = False
        settings["backfill_error"] = f"{type(exc).__name__}: {exc}"
        await asyncio.to_thread(save_item_data)
        print(f"Memory backfill failed for guild {guild.id}: {type(exc).__name__}: {exc}")


@ADMIN_MEMORY_GROUP.command(name="suggestions", description="GM: review archived messages that may be worth saving as canon.")
async def memory_suggestions(interaction: discord.Interaction):
    if interaction.guild is None or not is_staff(interaction): return await interaction.response.send_message("GM/admin only.", ephemeral=True)
    mem = _store(interaction.guild.id)
    suggestions = [x for x in (mem.get("priority_archive", []) + mem.get("archive", [])) if x.get("content") or x.get("attachments")][-10:]
    if not suggestions: return await interaction.response.send_message("There are no current memory suggestions.", ephemeral=True)
    for entry in suggestions:
        e = discord.Embed(title=f"{entry.get('author_name','Unknown')} — possible lore", description=(entry.get("content") or "[attachment only]")[:1500], colour=discord.Colour.dark_grey())
        e.add_field(name="Source", value=f"[Open message]({entry.get('jump_url')})", inline=False)
        await interaction.channel.send(embed=e)
    await interaction.response.send_message("Memory suggestions posted in this channel.", ephemeral=True)


@ADMIN_MEMORY_GROUP.command(name="delete", description="GM: delete a saved campaign memory record.")
async def memory_delete(interaction: discord.Interaction, memory_id: str):
    if interaction.guild is None or not is_staff(interaction): return await interaction.response.send_message("GM/admin only.", ephemeral=True)
    mem = _store(interaction.guild.id); before = len(mem["records"]); mem["records"] = [r for r in mem["records"] if r.get("id") != memory_id]
    if len(mem["records"]) == before: return await interaction.response.send_message("Memory record not found.", ephemeral=True)
    save_item_data(); await interaction.response.send_message("Memory record deleted.", ephemeral=True)


# ============================================================
# GEMINI LORE INTELLIGENCE
# ============================================================

AI_BATCH_SIZE = 20
AI_COOLDOWN_SECONDS = 25

from .ai_providers import complete as _provider_complete, provider_status as _provider_status

def _ai_enabled():
    # Ollama is always considered a configured local fallback. If it is not
    # running, the provider manager simply quarantines it and continues.
    return True

def _provider_is_daily_quota(exc):
    text=str(exc).upper()
    return any(marker in text for marker in ("GENERATE_REQUESTS_PER_DAY", "REQUESTS_PER_DAY", "RPD", "PER_DAY", "TPD", "DAILY QUOTA", "RESOURCE_EXHAUSTED"))

def _provider_is_retryable(exc):
    text=str(exc).upper()
    return any(marker in text for marker in ("429", "RATE LIMIT", "RESOURCE_EXHAUSTED", "TOO MANY REQUESTS", "500", "502", "503", "504", "UNAVAILABLE", "TIMEOUT", "TIMED OUT"))

def _ai_sync_analyze(prompt):
    return _provider_complete(prompt, structured=True, max_tokens=1000)

def _ai_sync_text(prompt):
    return _provider_complete(prompt, structured=False, max_tokens=700)

def _parse_ai_json(text):
    import json
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return {"items": []}
        try:
            return json.loads(match.group(0))
        except Exception:
            return {"items": []}

async def _analyze_lore_batch(guild_id, force=False):
    if not _ai_enabled():
        return
    mem = _store(guild_id); settings = mem["settings"]; queue = settings.get("ai_queue", [])
    # AI is optional. Once a provider hits a daily quota, pause background
    # classification for a while instead of repeatedly calling exhausted
    # providers for every batch of Discord messages. Core game/lore features
    # continue to work from the local archive without AI.
    import time
    disabled_until = float(settings.get("ai_disabled_until", 0) or 0)
    if time.time() < disabled_until:
        return
    if not queue:
        return
    if len(queue) < AI_BATCH_SIZE and not force:
        return
    import time
    now = time.time()
    if now - float(settings.get("ai_last_call", 0) or 0) < AI_COOLDOWN_SECONDS:
        return
    batch = queue[:AI_BATCH_SIZE]; settings["ai_queue"] = queue[AI_BATCH_SIZE:]; settings["ai_last_call"] = now
    lines=[]
    for i,row in enumerate(batch,1):
        privacy = "PRIVATE DM — only the listed user may know this unless the text itself clearly indicates public knowledge." if row.get("private_user_id") else "PUBLIC SERVER MESSAGE"
        lines.append(f"[{i}] {privacy} | channel={row.get('channel_id')} | author={row.get('author_name')} | {row.get('created_at')}\n{row.get('content','')}")
    prompt=f'''You are the campaign memory classifier for a D&D Discord server.
Your job is to separate actual campaign information from ordinary Discord conversation.

CRITICAL CLASSIFICATION RULES:
- CANON: strong evidence that an event/fact/character/location/etc. is actually established in the campaign.
- POSSIBLE_CANON: may be serious and campaign-relevant, but is not sufficiently confirmed.
- RUMOR: an in-world rumor or player claim that exists in the conversation but is not established as true.
- JOKE: memes, sarcasm, shitposting, obvious jokes, absurd exaggerations, or playful statements.
- CHATTER: normal conversation, OOC discussion, reactions, arguments, logistics, or unrelated talk.
- CONTRADICTION: a serious campaign claim that appears to conflict with established information.

A message saying something ridiculous is NOT canon merely because it names a character. Use surrounding messages in the batch as context.
Never turn community jokes into campaign facts. Keep jokes classified as JOKE so later lore answers can explicitly know they were non-canon.
PRIVATE DM information must never become public lore.
Never invent facts.

Return ONLY valid JSON:
{{
  "items":[{{"source_indexes":[1],"title":"...","type":"event|character|location|faction|creature|world|planet|item|lore|player|other","classification":"CANON|POSSIBLE_CANON|RUMOR|JOKE|CHATTER|CONTRADICTION","summary":"short factual summary","confidence":0.0,"importance":0.0,"music_mood":"main_ost|action|calm|dark|funny|sad|scary|none","visibility":"public|private","allowed_user_ids":["123"],"entities":[{{"name":"Vespa","type":"character|player|faction|location|planet|world|item|creature|lore|event|other","summary":"only what this batch establishes","backstory":"only if established, else UNKNOWN","origin":"only if established, else UNKNOWN","role":"only if established, else UNKNOWN","status":"alive|dead|active|inactive|unknown","aliases":["..."],"relationships":[{{"entity":"...","relationship":"..."}}],"abilities":["..."],"locations":["..."],"items":["..."],"is_new_character_instance":false,"player_owner_id":"Discord ID only when clearly established"}}]}}]
}}
ENTITY RULES:
- Whenever a campaign-relevant person, player, faction, location, planet/world, item, creature, event, or named lore concept is newly introduced, include it in entities even if details are sparse.
- A new entity must create/update a dedicated profile. Never make the AI wait for a /lore command to create it.
- Profiles must preserve ALL useful information explicitly established in the source: backstory, origin, role/title, current status, facts, abilities, relationships, locations, possessions/items, aliases, first appearance, sessions, history, corrections, and exact source references. Unknown fields must remain UNKNOWN rather than being invented.
- If a player character is clearly identified, keep that character separate from the Discord player and from every previous character that player has had. A player may have multiple characters across the campaign.
- If a GM clearly establishes that a specific character died, classify the death as canon and include a lifecycle/death entity update. Do not revive or reuse that dead character as the player's new character. The player's next character must receive a new character instance/profile and a new first appearance.
- Do NOT create profiles for ordinary words, sentence-start capitalization, jokes, or unrelated names.
- If the message is a joke/chat, do not create canon entity facts from it.
- music_mood is a scene cue, never a fact: action only for a real battle or attack; scary for fear/horror; sad for loss/grief; funny for clear comedy; dark for an ominous serious scene; calm for a restful scene; main_ost for a normal scene; none for ordinary chatter or uncertainty.
- A player's Discord profile is separate from an in-world character with a similar name.
Only return items with importance >= 0.30. Keep summaries under 500 characters.

BATCH:
{chr(10).join(lines)}'''
    try:
        raw=await asyncio.to_thread(_ai_sync_analyze,prompt); data=_parse_ai_json(raw)
        batch_by_index={i+1:row for i,row in enumerate(batch)}
        for item in (data.get("items",[]) if isinstance(data,dict) else [])[:30]:
            if not isinstance(item,dict): continue
            classification=str(item.get("classification","CHATTER")).upper()
            if classification not in {"CANON","POSSIBLE_CANON","RUMOR","JOKE","CHATTER","CONTRADICTION"}: classification="CHATTER"
            indexes=[int(x) for x in (item.get("source_indexes") or []) if str(x).isdigit() and int(x) in batch_by_index]
            if not indexes: continue
            source_rows=[batch_by_index[i] for i in indexes]
            music_mood=str(item.get("music_mood") or "none").casefold().replace(" ", "_")
            if music_mood in {"main_ost", "action", "calm", "dark", "funny", "sad", "scary"} and classification in {"CANON", "POSSIBLE_CANON", "RUMOR"}:
                try:
                    from .. import web_app
                    web_app._apply_ai_music_mood(guild_id, music_mood)
                except Exception as exc:
                    print(f"AI music cue warning: {type(exc).__name__}: {exc}")
            confidence=max(0.0,min(1.0,float(item.get("confidence",0) or 0)))
            importance=max(0.0,min(1.0,float(item.get("importance",0) or 0)))
            source_ids={str(x.get("message_id")) for x in source_rows}
            for fact in mem["lore_facts"]:
                if str(fact.get("source_message_id")) in source_ids:
                    fact["classification"]=classification.lower()
                    fact["confidence"]=confidence
                    fact["status"]=("confirmed" if classification=="CANON" else classification.lower())
                    fact["classification_reason"]=str(item.get("summary") or "")[:500]
            if classification not in {"CANON","POSSIBLE_CANON","RUMOR","CONTRADICTION"}: continue
            visibility=str(item.get("visibility","public")).lower()
            if visibility not in {"public","private"}: visibility="public"
            if visibility=="private":
                private_ids={str(x.get("private_user_id")) for x in source_rows if x.get("private_user_id")}
                allowed=[str(x) for x in item.get("allowed_user_ids",[]) if str(x) in private_ids]
                if not allowed: continue
            else: allowed=[]
            record_visibility={"CANON":"canon","POSSIBLE_CANON":"possible_canon","RUMOR":"rumor","CONTRADICTION":"contradiction"}[classification]
            title=str(item.get("title") or "Untitled lore")[:100]
            summary=str(item.get("summary") or "")[:2000]
            if not title or not summary: continue
            # Build/update dedicated entity profiles from the classifier's entity
            # extraction. Profiles are durable, source-linked, and intentionally
            # much richer than a one-line summary.
            for ent in (item.get("entities") or []):
                if not isinstance(ent, dict):
                    continue
                ename = str(ent.get("name") or "").strip()[:120]
                etype = str(ent.get("type") or "other").strip().lower()[:40]
                allowed_types = {"character","player","faction","location","planet","world","item","creature","lore","event","other"}
                if not ename or etype not in allowed_types:
                    continue
                player_owner_id = ent.get("player_owner_id")
                if etype == "character" and ent.get("is_new_character_instance") and player_owner_id:
                    profile = _new_character_profile(mem, ename, player_owner_id, source_rows[0], source_rows[0].get("session_number"))
                else:
                    ekey = ename.casefold() if etype != "character" else f"character:{ename.casefold()}"
                    profile = _entity_profile(mem, ekey, ename, etype)
                if etype != "other":
                    profile["type"] = etype
                if player_owner_id and etype == "character":
                    profile["player_owner_id"] = str(player_owner_id)
                summary_text = str(ent.get("summary") or "").strip()
                if summary_text and classification == "CANON":
                    authority = "gm_correction" if str(source_rows[0].get("author_id")) in {str(x) for x in GM_USER_IDS} else "player"
                    _add_profile_fact(profile, summary_text, source_rows[0], "canon", authority, source_rows[0].get("session_number"))
                    profile["summary"] = summary_text[:1500]
                    _record_profile_history(profile, "canon_update", summary_text, source_rows[0], source_rows[0].get("session_number"))
                for field in ("backstory","origin","role","origin_details","species","age","personality","faction","current_location"):
                    value = str(ent.get(field) or "").strip()
                    if value and value.upper() != "UNKNOWN" and classification == "CANON":
                        profile[field] = value[:1500]
                for field in ("titles","motivations","goals","fears","family","weaknesses","possessions"):
                    vals = [str(x).strip()[:400] for x in (ent.get(field) or []) if str(x).strip() and str(x).strip().upper() != "UNKNOWN"]
                    if vals and classification == "CANON":
                        profile[field] = list(dict.fromkeys(profile.get(field, []) + vals))[-200:]
                correction = ent.get("correction") or {}
                if classification in {"CANON", "CONTRADICTION"} and isinstance(correction, dict) and correction.get("new_claim"):
                    profile.setdefault("corrections", []).append({
                        "old_claim": str(correction.get("old_claim") or "")[:700],
                        "new_claim": str(correction.get("new_claim") or "")[:700],
                        "authority": "gm" if str(source_rows[0].get("author_id")) in {str(x) for x in GM_USER_IDS} else "unknown",
                        "session_number": source_rows[0].get("session_number"),
                        "source_message_id": str(source_rows[0].get("message_id") or ""),
                        "source_url": source_rows[0].get("jump_url"),
                        "created_at": source_rows[0].get("created_at") or _now(),
                    })
                    profile["corrections"] = profile["corrections"][-100:]
                aliases = [str(x).strip()[:120] for x in (ent.get("aliases") or []) if str(x).strip()]
                if aliases:
                    profile["aliases"] = list(dict.fromkeys(profile.get("aliases", []) + aliases))[-100:]
                for field in ("abilities","locations","items"):
                    vals = [str(x).strip()[:300] for x in (ent.get(field) or []) if str(x).strip()]
                    if vals and classification == "CANON":
                        profile[field] = list(dict.fromkeys(profile.get(field, []) + vals))[-200:]
                rels = ent.get("relationships") or []
                if classification == "CANON":
                    for rel in rels:
                        if isinstance(rel, dict) and rel.get("entity") and rel.get("relationship"):
                            entry = {"entity": str(rel["entity"])[:120], "relationship": str(rel["relationship"])[:500], "source_message_id": str(source_rows[0].get("message_id") or ""), "session_number": source_rows[0].get("session_number")}
                            if not any(str(x.get("entity")) == entry["entity"] and str(x.get("relationship")) == entry["relationship"] for x in profile.get("relationships", []) if isinstance(x, dict)):
                                profile.setdefault("relationships", []).append(entry)
                    profile["relationships"] = profile["relationships"][-300:]
                status = str(ent.get("status") or "unknown").lower()
                if status in {"alive","dead","active","inactive"} and classification == "CANON":
                    profile["current_status"] = status
                    if status == "dead" and etype == "character":
                        _mark_character_dead(mem, profile.get("id"), source_rows[0], source_rows[0].get("session_number"), summary_text)
                for sr in source_rows:
                    mid = str(sr.get("message_id") or "")
                    if mid and mid not in profile.setdefault("source_message_ids", []):
                        profile["source_message_ids"].append(mid)
                    if sr.get("jump_url") and sr["jump_url"] not in profile.setdefault("source_urls", []):
                        profile["source_urls"].append(sr["jump_url"])
                    profile.setdefault("source_records", []).append({"message_id": mid, "url": sr.get("jump_url"), "author_id": str(sr.get("author_id") or ""), "author_name": sr.get("author_name"), "created_at": sr.get("created_at"), "session_number": sr.get("session_number")})
                profile["source_message_ids"] = profile["source_message_ids"][-400:]
                profile["source_urls"] = profile["source_urls"][-250:]
                profile["source_records"] = profile["source_records"][-300:]
                if source_rows[0].get("session_number") is not None and source_rows[0]["session_number"] not in profile.setdefault("sessions", []):
                    profile["sessions"].append(source_rows[0]["session_number"])
                profile["sessions"] = profile["sessions"][-100:]
                if not profile.get("first_appearance"):
                    profile["first_appearance"] = {"session_number": source_rows[0].get("session_number"), "created_at": source_rows[0].get("created_at"), "source_message_id": str(source_rows[0].get("message_id") or ""), "source_url": source_rows[0].get("jump_url")}
                profile["last_appearance"] = {"session_number": source_rows[-1].get("session_number"), "created_at": source_rows[-1].get("created_at"), "source_message_id": str(source_rows[-1].get("message_id") or ""), "source_url": source_rows[-1].get("jump_url")}
                profile["last_updated"] = _now()
                try:
                    lore_index.upsert_profile(guild_id, (f"character:{ename.casefold()}" if etype == "character" and not player_owner_id else (f"character_instance:{profile.get('id')}" if etype == "character" else ename.casefold())), profile)
                except Exception as exc:
                    print(f"Lore index sync warning: {type(exc).__name__}: {exc}")

            record={"id":f"ai-lore-{uuid.uuid4().hex[:10]}","name":title,"type":str(item.get("type") or "other")[:40],"visibility":"private_user" if visibility=="private" else record_visibility,"description":summary,"gm_notes":f"AI classification: {classification}; confidence {confidence:.0%}.","guild_id":guild_id,"created_by":0,"created_at":_now(),"ai_generated":True,"ai_status":classification.lower(),"allowed_user_ids":allowed,"source_message_ids":[str(x.get("message_id")) for x in source_rows[:10]],"source_message_urls":[x.get("jump_url") for x in source_rows[:10] if x.get("jump_url")],"source_private":visibility=="private","importance":importance,"confidence":confidence}
            duplicate=any(record["name"].casefold()==str(r.get("name","")).casefold() and _score(record["description"],r.get("description",""))>=6 for r in mem["records"][-150:])
            if not duplicate: mem["records"].append(record)
        mem["records"]=mem["records"][-3000:]; settings["ai_error"]=None
        await asyncio.to_thread(save_item_data)
    except Exception as exc:
        settings["ai_queue"]= (batch+settings.get("ai_queue",[]))[-120:]; settings["ai_error"]=f"{type(exc).__name__}: {exc}"[:500]
        # Daily quota exhaustion is not a game failure. Pause the optional
        # analyzer for one hour so the bot does not spam the console while
        # continuing to ingest/store campaign messages locally.
        if _provider_is_daily_quota(exc):
            settings["ai_disabled_until"] = time.time() + 3600
            print(f"Lore AI quota exhausted for guild {guild_id}; AI analysis paused for 1 hour. Local lore storage remains active.")
        else:
            print(f"Gemini lore analyzer warning for guild {guild_id}: {type(exc).__name__}: {exc}")
        await asyncio.to_thread(save_item_data)

def _local_lore_answer(guild_id, question, viewer_id, include_gm=False):
    """Deterministic lore answer used when every AI provider is unavailable."""
    rows = _best_lore(guild_id, question, include_gm=include_gm, viewer_id=viewer_id)
    latest = _latest_session_context(guild_id, include_gm=include_gm)
    if not rows and not latest:
        return "I couldn't find any recorded campaign information matching that question."
    parts = []
    if latest:
        parts.append(f"**Most recent session:** {latest.get('title') or 'Untitled Session'} (Session #{latest.get('session_number', '?')})")
        for label, key in (("Major events", "major_events"), ("Discoveries", "major_discoveries"), ("Unresolved", "unresolved_events"), ("Threats", "new_threats")):
            vals = latest.get(key) or []
            if vals:
                parts.append(f"**{label}:** " + " | ".join(str(x)[:400] for x in vals[:5]))
    if rows:
        evidence=[]
        for _, kind, row in rows[:8]:
            text = row.get("description") or row.get("content") or row.get("text") or ""
            text = re.sub(r"\s+", " ", str(text)).strip()
            if text:
                name = row.get("name") or kind
                evidence.append(f"• **{name}:** {text[:500]}")
        if evidence:
            parts.append("**Recorded lore:**\n" + "\n".join(evidence))
    parts.append("*AI is currently unavailable, so this answer uses the bot's locally stored campaign records. No AI is required for the game to continue.*")
    return "\n\n".join(parts)[:3800]

async def _gemini_lore_answer(guild_id, question, viewer_id, include_gm=False):
    """Have the multi-provider AI synthesize lore; never use the archive formatter as the answer."""
    if not _ai_enabled():
        return None
    rows = _best_lore(guild_id, question, include_gm=include_gm, viewer_id=viewer_id)
    q = question.casefold()
    wants_recent = any(x in q for x in ("last time", "most recent", "latest", "recent session", "current session", "what happened today"))
    wants_session = bool(re.search(r"\b(?:session|game)\s*(?:#|number)?\s*(?:\d+|one|two|three|four|five|first|second|third)\b", q)) or "first ever session" in q or "first session" in q
    latest = _latest_session_context(guild_id, include_gm=include_gm) if wants_recent and not wants_session else None

    # Player-history query: use actual participation chronology, not mentions.
    player_session = None
    if "last time" in q and "played" in q:
        try:
            mem = _store(guild_id)
            candidates = []
            for key, p in (mem.get("entity_profiles") or {}).items():
                if not isinstance(p, dict) or p.get("type") != "player":
                    continue
                name = str(p.get("name") or "").casefold()
                if name and name in q:
                    candidates.append(p)
            if candidates:
                player_session = lore_index.latest_player_session(guild_id, str(candidates[0].get("discord_user_id") or ""))
                if player_session:
                    exact = _session_context(guild_id, int(player_session["session_number"]), include_gm=include_gm)
                    if exact:
                        latest = exact
                        rows = [r for r in rows if r[1] in {"profile", "record"}]
        except Exception as exc:
            print(f"Player session retrieval warning: {type(exc).__name__}: {exc}")

    if not rows and not latest:
        return None

    context = []
    if latest:
        recent_lines = [
            f"Session #{latest['session_number']} — {latest['title']} — completed {latest.get('ended_at','')}",
            "This is the MOST RECENT COMPLETED SESSION and has priority when later events changed older campaign state.",
        ]
        sections = [
            ("Major events", latest["major_events"]),
            ("Characters involved", latest["characters_involved"]),
            ("Major discoveries", latest["major_discoveries"]),
            ("Unresolved events", latest["unresolved_events"]),
            ("New threats", latest["new_threats"]),
        ]
        for label, values in sections:
            if values:
                recent_lines.append(label + ": " + " | ".join(str(x)[:500] for x in values[:10]))
        if latest["lore_events"]:
            recent_lines.append("Confirmed lore from this session: " + " | ".join(
                f"{x.get('title')}: {x.get('summary')}" for x in latest["lore_events"][:12]
            ))
        context.append("[MOST RECENT SESSION]\n" + "\n".join(recent_lines)[:5000])

    if player_session and latest:
        try:
            player_id = str(candidates[0].get("discord_user_id") or "") if candidates else ""
            character_id = str(player_session.get("character_id") or "")
            rows_for_session = campaign_store.messages_between(guild_id, latest.get("started_at"), latest.get("ended_at"), limit=5000)
            player_profile = (mem.get("entity_profiles") or {}).get(f"player:{player_id}") if player_id else None
            character_name = ""
            if character_id and player_profile:
                cp = next((p for p in (mem.get("entity_profiles") or {}).values() if isinstance(p, dict) and str(p.get("id")) == character_id), None)
                character_name = str((cp or {}).get("name") or "")
            focused = []
            for rr in rows_for_session:
                txt = re.sub(r"\s+", " ", str(rr.get("content") or "")).strip()
                if not txt:
                    continue
                authored = str(rr.get("author_id") or "") == player_id
                mentions_character = bool(character_name and character_name.casefold() in txt.casefold())
                if authored or mentions_character:
                    focused.append(f"[{rr.get('created_at','')}] {rr.get('author_name','Unknown')}: {txt[:900]}")
            if focused:
                context.append("[PLAYER'S ACTUAL LAST PLAY SESSION EVIDENCE]\n" + "\n".join(focused[-120:])[:9000])
            context.append(f"[PLAYER SESSION FACT] This player's latest recorded participation is Session #{player_session.get('session_number')}. Participation was established by actual activity; check-in is only supplementary evidence.")
        except Exception as exc:
            print(f"Player session context warning: {type(exc).__name__}: {exc}")

    # Keep evidence small. Raw message/fact evidence is input to the AI only;
    # it is never rendered directly to the user.
    for _, kind, r in rows[:10]:
        if kind == "record":
            status = str(r.get("visibility", "canon")).replace("_", " ").upper()
            label = status + " LORE"
            text = r.get("description") or ""
            if include_gm and r.get("gm_notes"):
                text += f" GM notes: {r.get('gm_notes')}"
        elif kind == "fact":
            label = "RECORDED EVIDENCE"
            text = r.get("text") or ""
        else:
            label = "GM ARCHIVED EVIDENCE"
            text = r.get("content") or ""
        text = re.sub(r"\s+", " ", str(text)).strip()[:500]
        if text:
            context.append(f"[{label}] {text}")

    if not context:
        return None
    visibility_rule = (
        "This is a GM/admin request, so you may use GM-only evidence, but do not expose private source text or internal archive details."
        if include_gm else
        "This is a player-visible request. Use only canon/publicly safe evidence. Never reveal GM-only or private source text."
    )
    prompt = f"""You are the campaign historian for a dark-fantasy Discord RPG.
Answer the question as a natural explanation, not as a database search result.

RULES:
- Use ONLY the supplied evidence.
- A session-specific question MUST use that exact session. Do not import events from other sessions unless explicitly asked.
- A "last time [player] played" question MUST use the latest session in the participation index for that player, not the latest message mentioning them.
- The MOST RECENT SESSION section is only relevant when the question asks for recent/current/latest activity. Do not inject it into unrelated character/lore questions.
- Newer confirmed canon can replace an older current-state claim, but the older claim remains historical and must not be treated as current.
- Synthesize related facts into a coherent campaign-aware explanation; do NOT dump or quote source messages.
- Do not mention the archive, database, AI, source messages, message IDs, or internal systems.
- Separate conclusions into three levels when useful:
  CANON — explicitly established by the supplied campaign evidence.
  Report confidence when useful; confidence is evidence strength, not truth by itself.
  INFERENCE — a reasonable conclusion supported by multiple established facts, but not explicitly confirmed.
  SPECULATION — a creative possibility not established by the evidence; clearly label it as NOT CANON.
- Never present an inference or speculation as canon.
- If the evidence does not answer something, explicitly say what is unknown.
- Do not invent motives, relationships, events, dialogue, powers, or chronology and call them facts.
- Prefer a concise character/profile explanation when the question is about a person.
- Prefer a concise chronological explanation when the question asks what happened.
- {visibility_rule}

QUESTION:
{question[:700]}

SUPPLIED EVIDENCE:
{chr(10).join(context)}
"""
    try:
        return (await asyncio.to_thread(_ai_sync_text, prompt)).strip()[:3800]
    except Exception as exc:
        # AI is an enhancement, never a dependency. Fall back to deterministic
        # local lore retrieval when all providers are exhausted/unavailable.
        if _provider_is_daily_quota(exc):
            _store(guild_id)["settings"]["ai_disabled_until"] = __import__("time").time() + 3600
        print(f"Lore AI unavailable; using local lore fallback: {type(exc).__name__}: {exc}")
        return _local_lore_answer(guild_id, question, viewer_id, include_gm=include_gm)


async def archive_dm_message(message: discord.Message, bot):
    # Discord bots cannot read ordinary user-to-user DMs. This only handles DMs sent to this bot.
    if message.guild is not None or message.author.bot: return
    for guild in getattr(bot,"guilds",[]):
        if guild.get_member(message.author.id) is None: continue
        mem=_store(guild.id); private=mem.setdefault("private_archive",{}); rows=private.setdefault(str(message.author.id),[])
        if any(str(x.get("message_id"))==str(message.id) for x in rows): continue
        row={"message_id":message.id,"channel_id":getattr(message.channel,"id",0),"guild_id":guild.id,"author_id":message.author.id,"author_name":getattr(message.author,"display_name",message.author.name),"content":(message.content or "")[:2500],"attachments":[],"jump_url":None,"created_at":message.created_at.isoformat(),"private":True}
        rows.append(row); private[str(message.author.id)]=rows[-1000:]; _queue_ai_message(guild.id,row,private_user_id=message.author.id); await asyncio.to_thread(save_item_data); await _analyze_lore_batch(guild.id)

async def archive_message(message: discord.Message):
    if message.guild is None or message.author.bot: return
    if _is_hell_channel(message.channel): return
    row=_ingest_lore(message.guild.id,message,priority=False)
    _queue_ai_message(message.guild.id,row)
    mem=_store(message.guild.id); mem["settings"]["auto_archive"]=True
    await asyncio.to_thread(save_item_data)
    # GM/priority lore is indexed immediately after saving, while ordinary
    # chatter remains batched for efficiency. This is what makes a new GM-named
    # character/faction/location become searchable almost immediately.
    force = str(message.author.id) in {str(x) for x in GM_USER_IDS}
    await _analyze_lore_batch(message.guild.id, force=force)


async def generate_session_recap(guild_id, session):
    """Generate a GM-only recap and index high-confidence story facts."""
    if not _ai_enabled():
        return None
    mem = _store(guild_id)
    start = session.get("started_at", "")
    end = session.get("ended_at", "")
    events = list(session.get("events", []))
    archive = []
    try:
        session_rows = campaign_store.messages_between(guild_id, start=start, end=end, limit=5000)
    except Exception:
        session_rows = []
    if not session_rows:
        session_rows = list(mem.get("archive", []))
    for row in session_rows:
        created = row.get("created_at", "")
        content = (row.get("content") or "").strip()
        if content:
            archive.append({"author": row.get("author_name", "Unknown"), "content": content[:1800], "created_at": created})
    source = {"session_number": session.get("session_number"), "title": session.get("title") or "Untitled Session", "briefing": session.get("today") or "", "gm_events": events[-100:], "session_messages": archive[-220:], "attendance": session.get("attendance", {})}
    prompt = f"""You are a campaign historian for a GM-authored Discord D&D campaign.
The GM writes the story. Your job is ONLY to summarize what is supported by the supplied session record.
Never invent events, motives, discoveries, threats, characters, lore, or outcomes.
Do not turn player speculation or jokes into canon. Treat an event as canon only when the supplied record clearly supports it.
The recap is GM-only and may mention unresolved/hidden information present in the supplied GM record.

Return ONLY valid JSON in this shape:
{{
  "major_events": ["..."],
  "characters_involved": ["..."],
  "major_discoveries": ["..."],
  "unresolved_events": ["..."],
  "new_threats": ["..."],
  "lore_events": [{{"title":"...","type":"event|character|location|faction|creature|lore|other","summary":"...","importance":0.0}}]
}}
Keep each list concise. Only include lore_events with importance >= 0.75.

SESSION RECORD:
{json.dumps(source, ensure_ascii=False)[:50000]}"""
    try:
        raw = await asyncio.to_thread(_ai_sync_analyze, prompt)
        data = _parse_ai_json(raw)
        if not isinstance(data, dict):
            return None
        def clean_list(key, limit=12):
            return [str(x).strip()[:600] for x in (data.get(key) or []) if str(x).strip()][:limit]
        recap = {"generated_at": _now(), "major_events": clean_list("major_events"), "characters_involved": clean_list("characters_involved"), "major_discoveries": clean_list("major_discoveries"), "unresolved_events": clean_list("unresolved_events"), "new_threats": clean_list("new_threats"), "lore_events": []}
        existing_names = {str(r.get("name", "")).casefold() for r in mem.get("records", [])[-500:]}
        for item in (data.get("lore_events") or [])[:25]:
            if not isinstance(item, dict):
                continue
            try: importance = float(item.get("importance", 0) or 0)
            except Exception: importance = 0
            title = str(item.get("title") or "Untitled lore")[:100].strip()
            summary = str(item.get("summary") or "")[:1800].strip()
            if importance < 0.75 or not title or not summary or title.casefold() in existing_names:
                continue
            recap["lore_events"].append({"title": title, "type": str(item.get("type") or "event")[:30], "summary": summary, "importance": importance})
            mem["records"].append({"id": f"session-lore-{uuid.uuid4().hex[:10]}", "name": title, "type": str(item.get("type") or "event")[:30], "visibility": "canon", "description": summary, "gm_notes": f"Indexed automatically from Session #{session.get('session_number')} recap.", "guild_id": guild_id, "created_by": session.get("started_by") or 0, "created_at": _now(), "ai_generated": True, "ai_status": "canon", "source": "session_recap", "session_number": session.get("session_number"), "importance": importance})
            existing_names.add(title.casefold())
        mem["records"] = mem["records"][-3000:]
        await asyncio.to_thread(save_item_data)
        return recap
    except Exception as exc:
        mem["settings"]["ai_error"] = f"session recap: {type(exc).__name__}: {exc}"[:500]
        print(f"Gemini session recap warning for guild {guild_id}: {type(exc).__name__}: {exc}")
        return None


def register(bot_instance):
    bot_instance.tree.add_command(remember_context)
    bot_instance.tree.add_command(LORE_GROUP)
