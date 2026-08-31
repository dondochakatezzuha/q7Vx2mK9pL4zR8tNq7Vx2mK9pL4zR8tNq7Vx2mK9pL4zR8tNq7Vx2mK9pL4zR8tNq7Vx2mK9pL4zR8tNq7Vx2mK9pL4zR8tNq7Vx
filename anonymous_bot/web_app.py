"""Production web client for Anonymous Bot.

Serves the campaign UI and bridges authenticated Discord users to the campaign
server. Discord OAuth2 decides whether the browser is a player or GM; the
browser never gets a client secret or a manual role switch.
"""
from __future__ import annotations

import hashlib
import base64
import io
import hmac
import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
import asyncio
import concurrent.futures
import uuid
import re
import queue
import discord
from pathlib import Path

from .config import (
    DATA_DIR, DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DISCORD_REDIRECT_URI,
    WEB_HOST, WEB_PORT, ANONYMOUS_APP_URL, WEB_SESSION_SECRET,
    COOKIE_HTTPS_ONLY, GAME_GUILD_ID, GAME_CHANNEL_ID, GENERAL_CHANNEL_ID, GM_USER_IDS, WEB_TEST_GM_IDS, CAMPAIGN_NAME,
)
from .core import campaign_store
from .features.memory import _store

ROOT = Path(__file__).resolve().parent.parent / "Anonymous_BotV2"
WORLD_STATE_FILE = Path(DATA_DIR) / "web_world_state.json"
MUSIC_LIBRARY_DIR = Path(DATA_DIR) / "web_audio"
MUSIC_MOOD_TAGS = {"action", "calm", "dark", "funny", "main_ost", "sad", "scary"}
_WORLD_LOCK = threading.RLock()
_GENERAL_HISTORY_LOCK = threading.RLock()
_GENERAL_HISTORY_CACHE = {"at": 0.0, "items": []}


def _default_world_state():
    return {
        "version": 2,
        "campaign_name": CAMPAIGN_NAME,
        "campaign_short_name": "".join(word[0] for word in CAMPAIGN_NAME.split() if word)[:8] or "RPG",
        "world_threat": 0,
        "surprise_danger": {"enabled": True, "level": 0, "target": "all", "until": 0, "next_trigger_at": 0, "last_trigger_at": 0},
        "region_danger": {},
        "danger_enabled": {},
        "danger_player_overrides": {},
        "player_regions": {},
        "regions": [],
        "font_commands": [],
        "session_live": False,
        "story_locked": False,
        "atmosphere": {},
        "character_records": {},
        "npc_records": {},
        "chapter_history": [],
        "current_chapter": {"id": "chapter-1", "name": CAMPAIGN_NAME, "parent_id": None, "created_at": _now()},
        "session_history": [],
        "event_queue": [],
        "lore_connections": [],
        "emergency": {"player_lock": False, "hide_player_actions": False, "pause_session": False},
        "ooc_messages": [],
        "dm_threads": {},
        "groups": [],
        "ideas": [],
        "user_settings": {},
        "gm_messages": [],
        "campaign_notifications": [],
        "companions": [],
        "audio_assets": [],
        "custom_fonts": [],
        "active_audio": None,
        "main_ost_id": None,
        "ability_catalog": {
            "aure-focus": {"id":"aure-focus","name":"Luminous Focus","aspect":"Aure","description":"Concentrates Aro light into a precise controlled burst.","severity":"Moderate"},
            "tyreror-flow": {"id":"tyreror-flow","name":"Flow Control","aspect":"Tyreror","description":"Redirects nearby liquid with increasing precision.","severity":"Moderate"},
            "infernia-ember": {"id":"infernia-ember","name":"Ember Command","aspect":"Infernia","description":"Shapes flames into a controlled offensive technique.","severity":"Moderate"},
            "gravitos-pull": {"id":"gravitos-pull","name":"Gravity Pull","aspect":"Gravitos","description":"Temporarily increases gravitational force on a chosen point.","severity":"High"},
            "astria-fold": {"id":"astria-fold","name":"Spatial Fold","aspect":"Astria","description":"Creates a short-lived fold in local space.","severity":"Severe"},
            "mystia-focus": {"id":"mystia-focus","name":"Mental Focus","aspect":"Mystia","description":"Attempts controlled influence over a target's attention.","energy":16},
            "tempest-spark": {"id":"tempest-spark","name":"Spark Lance","aspect":"Tempest","description":"Condenses lightning into a directed strike.","severity":"Moderate"},
        },
        "character_journals": {},
        "character_inbox": [],
        "web_items": [],
        "economy": {"currency_names": {"vg":"Vesperian Gold"}, "balances": {}, "prices": []},
        "advanced": {
            "ambience": {"enabled": True, "ai_music": True, "auto_battle": True, "auto_funny": True, "state": "main_ost", "state_audio": {"main_ost": None, "calm": None, "action": None, "dark": None, "funny": None, "sad": None, "scary": None, "tension": None, "combat": None, "critical": None, "silence": None}},
            "character_states": {},
            "timelines": [{"id": "timeline-main", "name": "Main Timeline", "location": "General", "players": [], "locked": False}],
            "active_timelines": {},
            "storybooks": [],
            "personas": {},
            "persona_overrides": {},
            "pov_rules": {},
        },
    }


def _load_world_state():
    with _WORLD_LOCK:
        try:
            if WORLD_STATE_FILE.exists():
                data = json.loads(WORLD_STATE_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    state = _default_world_state()
                    state.update(data)
                    # Older state files did not have a campaign identity.  Do
                    # not let their empty placeholder values blank the home UI.
                    if not str(state.get("campaign_name") or "").strip():
                        state["campaign_name"] = CAMPAIGN_NAME
                    if not str(state.get("campaign_short_name") or "").strip():
                        state["campaign_short_name"] = "".join(word[0] for word in str(state["campaign_name"]).split() if word)[:8] or "RPG"
                    return state
        except Exception as exc:
            print(f"[web] world state load warning: {type(exc).__name__}: {exc}")
        return _default_world_state()


def _save_world_state(state):
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    tmp = WORLD_STATE_FILE.with_suffix(".tmp")
    with _WORLD_LOCK:
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(WORLD_STATE_FILE)


def _update_world_state(mutator):
    state = _load_world_state()
    mutator(state)
    _save_world_state(state)
    return state

WEBHOOK_FILE = Path(DATA_DIR) / "web_campaign_webhook.json"
_SERVER = None
_THREAD = None
_BOT = None
_SESSIONS = {}
_SESSIONS_LOCK = threading.RLock()
_SSE_CLIENTS = set()
_SSE_LOCK = threading.RLock()
_OAUTH_STATES = {}
_OAUTH_LOCK = threading.RLock()


def _music_tags(filename: str) -> list[str]:
    """Give imported server music browsable RPG mood tags from its title.

    The supplied library has flat filenames rather than genre metadata. Tags are
    intentionally a starting point for GM editing, not a claim about the music's
    original genre.
    """
    title = Path(filename).stem.casefold()
    tags = []
    rules = {
        "action": ("battle", "fight", "lucha", "guerra", "duelo", "blade", "sword", "ignition", "demolition", "rage", "storm", "danger"),
        "funny": ("comical", "ditty", "dance", "tango", "boogie", "fiesta", "smart", "charade", "craft", "chad", "magot", "spla"),
        "sad": ("requiem", "lost", "burden", "never meant", "choked", "torn apart", "swan song", "explained"),
        "calm": ("peaceful", "going home", "afternoon", "head in the clouds", "compassion", "errante"),
        "ominous": ("creeping", "shadow", "enemy unseen", "haunted", "insanity", "ominous", "apocalypse", "nube negra", "hollowed", "phenomena"),
        "tension": ("precipice", "destiny", "confrontation", "back to the wall", "unseen", "awaits", "calling"),
    }
    for tag, keywords in rules.items():
        if any(keyword in title for keyword in keywords):
            tags.append(tag)
    return tags or ["ambient"]


def _index_server_music_library():
    """Register bundled server music once, without touching user uploads."""
    if not MUSIC_LIBRARY_DIR.exists():
        return
    state = _load_world_state()
    assets = state.setdefault("audio_assets", [])
    # Old flat-library entries become dead links once the same track has been
    # organized into a category. Remove only bundled-library records whose file
    # no longer exists; uploaded tracks are never touched here.
    original_count = len(assets)
    assets[:] = [item for item in assets if not (
        isinstance(item, dict)
        and item.get("source") == "server-library"
        and not (MUSIC_LIBRARY_DIR / Path(str(item.get("filename") or ""))).is_file()
    )]
    changed = len(assets) != original_count
    known = {str(item.get("filename")) for item in assets if isinstance(item, dict)}
    for file in sorted(MUSIC_LIBRARY_DIR.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if not file.is_file() or file.suffix.lower() not in {".mp3", ".ogg", ".wav", ".m4a"}:
            continue
        relative = file.relative_to(MUSIC_LIBRARY_DIR).as_posix()
        if relative in known:
            continue
        digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:16]
        category = file.parent.name.casefold().replace(" ", "_")
        tags = ([category] if category in MUSIC_MOOD_TAGS else []) + _music_tags(file.name)
        assets.append({
            "id": f"library-{digest}",
            "name": file.stem,
            "filename": relative,
            "url": "/media/audio/" + urllib.parse.quote(relative),
            "tags": list(dict.fromkeys(tags)),
            "source": "server-library",
            "at": file.stat().st_mtime,
        })
        changed = True
    if changed:
        assets.sort(key=lambda item: str(item.get("name") or "").casefold())
    # A categorized Main OST pack is the default soundtrack. Preserve an
    # explicit GM selection, but self-heal old worlds that predate the field.
    main_ost_id = str(state.get("main_ost_id") or "")
    valid_main = next((item for item in assets if str(item.get("id")) == main_ost_id), None)
    if not valid_main:
        main = next((item for item in assets if "main_ost" in {str(tag).casefold().replace(" ", "_") for tag in (item.get("tags") or [])}), None)
        if main:
            state["main_ost_id"] = str(main["id"])
            changed = True
    if changed:
        _save_world_state(state)


def _apply_ai_music_mood(guild_id, mood: str) -> bool:
    """Switch server music only when the lore AI returns a recognized scene mood."""
    if str(guild_id) != str(GAME_GUILD_ID):
        return False
    mood = str(mood or "").casefold().replace(" ", "_")
    if mood not in MUSIC_MOOD_TAGS:
        return False
    state = _load_world_state()
    if not state.get("session_live"):
        return False
    ambience = state.setdefault("advanced", {}).setdefault("ambience", {})
    if not ambience.get("ai_music", True):
        return False
    assets = state.get("audio_assets") or []
    candidates = [asset for asset in assets if mood in {str(tag).casefold().replace(" ", "_") for tag in (asset.get("tags") or [])}]
    if not candidates and mood == "main_ost":
        candidates = [asset for asset in assets if str(asset.get("id")) == str(state.get("main_ost_id") or "")]
    if not candidates:
        return False
    item = candidates[int(time.time()) % len(candidates)]
    state["active_audio"] = {"id": str(item["id"]), "name": item.get("name"), "tags": item.get("tags") or [mood], "loop": True, "nonce": uuid.uuid4().hex, "source": "lore-ai"}
    ambience["state"] = mood
    _save_world_state(state)
    _broadcast("audio_changed", {"active_audio": state["active_audio"], "mood": mood})
    return True


def _queue_web_lore_analysis(message_id, channel_id, author_id, author_name, content, created_at):
    """Queue browser roleplay for the same optional AI lore pipeline as Discord."""
    if not _BOT or not _BOT.loop or not _BOT.loop.is_running():
        return
    row = {"message_id": str(message_id), "channel_id": str(channel_id), "guild_id": str(GAME_GUILD_ID),
           "author_id": str(author_id), "author_name": str(author_name), "content": str(content)[:2500],
           "attachments": [], "jump_url": None, "created_at": created_at, "private": False}
    async def runner():
        try:
            from .features import memory
            memory._queue_ai_message(GAME_GUILD_ID, row)
            await asyncio.to_thread(memory.save_item_data)
            await memory._analyze_lore_batch(GAME_GUILD_ID, force=True)
        except Exception as exc:
            print(f"Web lore queue warning: {type(exc).__name__}: {exc}")
    _BOT.loop.call_soon_threadsafe(lambda: asyncio.create_task(runner()))


def _broadcast(event, payload=None):
    """Push a real-time event to every authenticated web client currently connected."""
    message = {"event": str(event), "data": payload or {}, "at": time.time()}
    dead = []
    with _SSE_LOCK:
        for q in list(_SSE_CLIENTS):
            try:
                q.put_nowait(message)
            except Exception:
                dead.append(q)
        for q in dead:
            _SSE_CLIENTS.discard(q)


def _now():
    return int(time.time())


def _sign(value: str) -> str:
    return hmac.new(WEB_SESSION_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()


def _cookie(value: str, secure=None) -> str:
    """Build the session cookie; local HTTP must not receive Secure."""
    flags = ["Path=/", "HttpOnly", "SameSite=Lax"]
    if secure is None:
        secure = COOKIE_HTTPS_ONLY
    if secure:
        flags.append("Secure")
    return f"anon_session={value}.{_sign(value)}; " + "; ".join(flags)


def _request_base(handler):
    """Choose local HTTP or the configured public HTTPS base for this request."""
    host = (handler.headers.get("Host") or "").split(":", 1)[0].lower()
    forwarded = (handler.headers.get("X-Forwarded-Proto") or "").split(",", 1)[0].strip().lower()
    if host == "localhost" or host == "::1" or host.startswith("127."):
        return f"http://127.0.0.1:{WEB_PORT}", False
    configured = (ANONYMOUS_APP_URL or "").rstrip("/")
    secure = forwarded == "https" or (not forwarded and COOKIE_HTTPS_ONLY)
    if configured:
        return configured, secure
    scheme = "https" if secure else "http"
    return f"{scheme}://{handler.headers.get('Host', f'127.0.0.1:{WEB_PORT}')}", secure


def _request_redirect_uri(handler):
    base, _ = _request_base(handler)
    return base + "/oauth/callback"


def _session_from_request(handler):
    raw = handler.headers.get("Cookie", "")
    for part in raw.split(";"):
        part = part.strip()
        if not part.startswith("anon_session="):
            continue
        token = part.split("=", 1)[1]
        try:
            value, sig = token.rsplit(".", 1)
        except ValueError:
            return None
        if not hmac.compare_digest(sig, _sign(value)):
            return None
        with _SESSIONS_LOCK:
            session = _SESSIONS.get(value)
            if session and session["expires"] > _now():
                return session
    return None


def _oauth_state_new():
    token = secrets.token_urlsafe(32)
    with _OAUTH_LOCK:
        _OAUTH_STATES[token] = _now() + 600
        # Remove expired states while we are here.
        now = _now()
        for key, expires in list(_OAUTH_STATES.items()):
            if expires <= now:
                _OAUTH_STATES.pop(key, None)
    return token

def _oauth_state_valid(token):
    if not token:
        return False
    with _OAUTH_LOCK:
        expires = _OAUTH_STATES.pop(token, None)
    return bool(expires and expires > _now())

def _redirect(handler, location, cookie=None):
    handler.send_response(302)
    handler.send_header("Location", location)
    if cookie:
        handler.send_header("Set-Cookie", cookie)
    handler.end_headers()


def _json(handler, payload, status=200):
    raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _body(handler):
    n = int(handler.headers.get("Content-Length", "0") or 0)
    return json.loads(handler.rfile.read(n).decode("utf-8") or "{}") if n else {}


_DISCORD_API_BASE = "https://discord.com/api/v10"
_DISCORD_USER_AGENT = "DiscordBot (AnonymousBot, 1.0)"


def _discord_token(code, redirect_uri=None):
    """Exchange a Discord OAuth2 authorization code for an access token.

    Discord's token endpoint expects HTTP Basic authentication for the OAuth
    client credentials.  Use a Discord-style User-Agent instead of urllib's
    default Python-urllib signature, which can be rejected by Discord's
    reverse-proxy layer. Never log the authorization code, client secret, or
    access token.
    """
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri or DISCORD_REDIRECT_URI,
    }).encode("utf-8")
    credentials = f"{DISCORD_CLIENT_ID}:{DISCORD_CLIENT_SECRET}".encode("utf-8")
    basic = base64.b64encode(credentials).decode("ascii")
    headers = {
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "User-Agent": _DISCORD_USER_AGENT,
    }
    req = urllib.request.Request(
        f"{_DISCORD_API_BASE}/oauth2/token",
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except Exception:
            detail = raw[:1000]
        raise RuntimeError(f"Discord token exchange failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Discord token exchange network error: {exc.reason}") from exc


def _discord_me(access_token):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": _DISCORD_USER_AGENT,
    }
    req = urllib.request.Request(f"{_DISCORD_API_BASE}/users/@me", headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except Exception:
            detail = raw[:1000]
        raise RuntimeError(f"Discord user lookup failed ({exc.code}): {detail}") from exc


# Kaizen is explicitly authorized as the web GM test account.  This is kept
# in addition to the normal configured GM list so web testing cannot disappear
# when a Discord role/cache changes.  Server administrators are also GMs.
def _discord_oauth_guilds(access_token):
    """Return the OAuth user's Discord guild list. Used to reliably detect server administrators without depending on bot member cache."""
    if not access_token:
        return []
    req = urllib.request.Request(
        f"{_DISCORD_API_BASE}/users/@me/guilds",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json", "User-Agent": _DISCORD_USER_AGENT},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data=json.loads(r.read().decode("utf-8"))
            return data if isinstance(data,list) else []
    except Exception as exc:
        print(f"[web] OAuth guild lookup warning: {type(exc).__name__}: {exc}")
        return []


def _oauth_is_server_admin(user_id, access_token):
    target=str(GAME_GUILD_ID)
    for guild in _discord_oauth_guilds(access_token):
        if str(guild.get("id")) != target:
            continue
        # Discord permissions returned by /users/@me/guilds are a bitfield.
        try:
            perms=int(str(guild.get("permissions") or "0"))
        except Exception:
            perms=0
        return bool(perms & 0x8)  # Administrator
    return False


def _gm(uid, access_token=None):
    uid = str(uid or "")
    if uid in {str(x) for x in GM_USER_IDS} or uid in WEB_TEST_GM_IDS:
        return True
    if access_token and _oauth_is_server_admin(uid, access_token):
        return True
    member = _guild_member(uid)
    perms = getattr(member, "guild_permissions", None)
    if not perms:
        return False
    return bool(getattr(perms, "administrator", False) or getattr(perms, "manage_guild", False) or getattr(perms, "manage_channels", False))

def _guild_member(uid):
    try:
        guild = _BOT.get_guild(int(GAME_GUILD_ID)) if _BOT else None
        if not guild:
            return None
        member = guild.get_member(int(uid))
        if member is not None:
            return member
        # Discord member caches can be incomplete immediately after startup.
        # Fetch the member from Discord instead of treating a real member as a
        # non-member and rejecting OAuth.
        if _BOT.loop and _BOT.loop.is_running():
            fut = concurrent.futures.Future()
            async def fetch():
                try:
                    fut.set_result(await guild.fetch_member(int(uid)))
                except Exception:
                    fut.set_result(None)
            _BOT.loop.call_soon_threadsafe(lambda: asyncio.create_task(fetch()))
            try:
                return fut.result(timeout=8)
            except Exception:
                return None
        return None
    except Exception:
        return None


def _discord_avatar_url(user):
    uid = str(user.get("id") or "")
    avatar = user.get("avatar")
    if avatar and uid:
        return f"https://cdn.discordapp.com/avatars/{uid}/{avatar}.png?size=128"
    return f"https://cdn.discordapp.com/embed/avatars/{int(uid) % 5}.png" if uid.isdigit() else ""


def _session_user_payload(user):
    return {
        "id": str(user.get("id") or ""),
        "username": user.get("username"),
        "display_name": user.get("global_name") or user.get("username"),
        "avatar_url": _discord_avatar_url(user),
    }


def _gm_users():
    found = {}
    guild = _BOT.get_guild(int(GAME_GUILD_ID)) if _BOT else None
    if guild:
        for member in guild.members:
            if getattr(member, "bot", False):
                continue
            if _gm(member.id):
                found[str(member.id)] = {"id":str(member.id),"name":member.display_name or member.name,"username":member.name}
    for uid in {str(x) for x in GM_USER_IDS} | WEB_TEST_GM_IDS:
        if uid not in found:
            member = _guild_member(uid)
            if member:
                found[uid] = {"id":uid,"name":member.display_name or member.name,"username":member.name}
    return list(found.values())


def _character_for(uid):
    mem = _store(GAME_GUILD_ID)
    player = mem.get("entity_profiles", {}).get(f"player:{uid}") or {}
    cid = player.get("active_character_id")
    profile = mem.get("entity_profiles", {}).get(str(cid)) if cid else None
    if not isinstance(profile, dict):
        profile = None
    state = _load_world_state()
    records = state.setdefault("character_records", {})
    rec = records.get(str(cid or uid), {})
    if not isinstance(rec, dict):
        rec = {}

    # A Discord account name is not a character. The UI stays on the neutral
    # Character label until the player/GM creates one explicitly.
    name = rec.get("name") or (profile.get("name") if profile else None)
    if name and not rec.get("name"):
        cid = str(cid or uid)
        rec = dict(rec)
        rec.setdefault("id", cid)
        rec["name"] = name
        rec.setdefault("type", "PLAYER")
        records[cid] = rec
        state["character_records"] = records
        _save_world_state(state)

    return {
        "id": str(profile.get("id")) if profile else str(rec.get("id") or cid or uid),
        "name": name,
        "status": rec.get("status") or (profile.get("current_status", "alive") if profile else "alive"),
        "image": rec.get("image"),
        "type": rec.get("type", "PLAYER"),
        "aro": rec.get("aro") or {},
        "journal": (state.get("character_journals") or {}).get(str(rec.get("id") or cid or uid), []),
    }


def _webhook_url(channel_id=GAME_CHANNEL_ID):
    """Return a webhook URL that belongs to the exact requested Discord channel.

    Older builds could leave a webhook from a previous channel in the cache, which
    made Game messages appear in General or fail with Discord HTTP 403. The cache
    now stores the channel id and is validated before every send.
    """
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    target_id = str(channel_id)
    if WEBHOOK_FILE.exists():
        try:
            value = json.loads(WEBHOOK_FILE.read_text(encoding="utf-8"))
            if value.get("url") and str(value.get("channel_id") or "") == target_id:
                return value["url"]
        except Exception:
            pass
    if _BOT is None:
        return None
    channel = _BOT.get_channel(int(channel_id))
    if channel is None and _BOT.loop.is_running():
        future = concurrent.futures.Future()
        async def fetch_channel():
            try: future.set_result(await _BOT.fetch_channel(int(channel_id)))
            except Exception as exc: future.set_exception(exc)
        _BOT.loop.call_soon_threadsafe(lambda: asyncio.create_task(fetch_channel()))
        try: channel = future.result(timeout=10)
        except Exception: channel = None
    if channel is None:
        return None
    future = concurrent.futures.Future()
    async def create():
        try:
            webhooks = await channel.webhooks()
            hook = next((x for x in webhooks if x.name == "Anonymous Web Client" and str(getattr(x, "channel_id", "")) == target_id), None)
            if hook is None:
                hook = await channel.create_webhook(name="Anonymous Web Client", reason="Anonymous RPG website bridge")
            future.set_result(hook)
        except Exception as exc:
            future.set_exception(exc)
    _BOT.loop.call_soon_threadsafe(lambda: asyncio.create_task(create()))
    try:
        webhook = future.result(timeout=15)
        WEBHOOK_FILE.write_text(json.dumps({"url": webhook.url, "channel_id": target_id}), encoding="utf-8")
        return webhook.url
    except Exception as exc:
        print(f"[web] webhook setup warning: {type(exc).__name__}: {exc}")
        return None

def _send_webhook(character, content, avatar_url=None):
    url = _webhook_url(GAME_CHANNEL_ID)
    if url:
        payload = {"content": content[:2000], "username": character[:80]}
        if avatar_url:
            payload["avatar_url"] = avatar_url
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as exc:
            # A stale/revoked webhook must never turn the whole game composer
            # into HTTP 403. Fall through to a normal bot message instead.
            if exc.code not in (401, 403, 404):
                raise
            try:
                WEBHOOK_FILE.unlink(missing_ok=True)
            except Exception:
                pass
    if not _BOT or not _BOT.loop.is_running():
        raise RuntimeError("Discord bot is not running.")
    channel = _BOT.get_channel(int(GAME_CHANNEL_ID))
    if channel is None:
        future = concurrent.futures.Future()
        async def fetch_game_channel():
            try: future.set_result(await _BOT.fetch_channel(int(GAME_CHANNEL_ID)))
            except Exception as exc: future.set_exception(exc)
        _BOT.loop.call_soon_threadsafe(lambda: asyncio.create_task(fetch_game_channel()))
        try: channel = future.result(timeout=10)
        except Exception as exc: raise RuntimeError(f"Game channel {GAME_CHANNEL_ID} is not available to the Discord bot: {exc}") from exc
    future = concurrent.futures.Future()
    async def send():
        try:
            msg = await channel.send(f"**{character[:80]}**\n{content[:1900]}")
            future.set_result({"id": str(msg.id), "channel_id": str(channel.id), "content": content[:1900]})
        except Exception as exc:
            future.set_exception(exc)
    _BOT.loop.call_soon_threadsafe(lambda: asyncio.create_task(send()))
    try:
        return future.result(timeout=15)
    except Exception as exc:
        raise RuntimeError(f"Discord game-channel send failed: {exc}") from exc


def _discord_image_file(image_data):
    """Turn a small browser image data URL into a Discord attachment."""
    if not isinstance(image_data, str) or not image_data.startswith("data:image/") or "," not in image_data:
        return None
    header, encoded = image_data.split(",", 1)
    mime = header[5:].split(";", 1)[0].lower()
    extension = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp"}.get(mime)
    if not extension:
        raise ValueError("Unsupported image format. Use PNG, JPG, GIF, or WebP.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("The image data could not be decoded.") from exc
    if not raw or len(raw) > 7_500_000:
        raise ValueError("Images sent to Discord must be smaller than 7.5 MB.")
    return discord.File(io.BytesIO(raw), filename=f"web-image.{extension}")


def _send_webhook_to_channel(channel_id, username, content, avatar_url=None, image_data=None):
    """Send to an exact Discord channel, using a webhook when possible and the bot account as a safe fallback."""
    if not _BOT:
        raise RuntimeError("Discord bot is not running.")
    channel = _BOT.get_channel(int(channel_id))
    if channel is None:
        future = concurrent.futures.Future()
        async def fetch_target_channel():
            try: future.set_result(await _BOT.fetch_channel(int(channel_id)))
            except Exception as exc: future.set_exception(exc)
        _BOT.loop.call_soon_threadsafe(lambda: asyncio.create_task(fetch_target_channel()))
        try: channel = future.result(timeout=10)
        except Exception as exc: raise RuntimeError(f"Discord channel {channel_id} is not available to the bot: {exc}") from exc
    future = concurrent.futures.Future()
    async def create_and_send():
        try:
            attachment = _discord_image_file(image_data)
            webhooks = await channel.webhooks()
            hook = next((x for x in webhooks if x.name == "Anonymous Web Client" and str(getattr(x, "channel_id", "")) == str(channel_id)), None)
            if hook is None:
                hook = await channel.create_webhook(name="Anonymous Web Client", reason="Anonymous RPG website bridge")
            future.set_result(await hook.send(content[:2000] or None, username=username[:80], avatar_url=avatar_url or None, file=attachment, wait=True))
        except (discord.Forbidden, discord.HTTPException) as exc:
            try:
                # Manage Webhooks is optional. Sending through the bot account keeps
                # the website multiplayer bridge working even without webhook permission.
                msg = await channel.send(f"**{username[:80]}**\n{content[:1900]}".rstrip(), file=_discord_image_file(image_data))
                future.set_result(msg)
            except Exception:
                future.set_exception(exc)
        except Exception as exc:
            future.set_exception(exc)
    _BOT.loop.call_soon_threadsafe(lambda: asyncio.create_task(create_and_send()))
    try:
        return future.result(timeout=20)
    except Exception as exc:
        raise RuntimeError(f"Discord channel send failed: {exc}") from exc


def _queue_discord_send(channel_id, username, content, avatar_url=None):
    """Deliver a web message without holding a chat HTTP request open."""
    def deliver():
        try:
            _send_webhook_to_channel(channel_id, username, content, avatar_url)
        except Exception as exc:
            print(f"[web] queued Discord send warning: {type(exc).__name__}: {exc}")
    threading.Thread(target=deliver, name="web-discord-send", daemon=True).start()


def _media_attachment_urls(message):
    """Return every visual Discord attachment, including GIFs with no MIME type."""
    urls = []
    for attachment in getattr(message, "attachments", []) or []:
        content_type = str(getattr(attachment, "content_type", "") or "").lower()
        filename = str(getattr(attachment, "filename", "") or "").lower()
        if content_type.startswith("image/") or filename.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".apng")):
            url = str(getattr(attachment, "url", "") or "")
            if url:
                urls.append(url)
    return urls


def _refresh_member_identity(rows):
    """Overlay archived messages with the current Discord display name/avatar.

    Archives intentionally retain original message text, but author profile data
    must follow Discord's current member profile rather than an old database row.
    """
    guild = _BOT.get_guild(int(GAME_GUILD_ID)) if _BOT else None
    if not guild:
        return rows
    for row in rows:
        try:
            member = guild.get_member(int(row.get("author_id") or 0))
        except (TypeError, ValueError):
            member = None
        if member:
            name = getattr(member, "display_name", None) or getattr(member, "name", None)
            if name:
                row["author_name"] = name
                row["name"] = name
            avatar = str(getattr(getattr(member, "display_avatar", None), "url", "") or "")
            if avatar:
                row["avatar_url"] = avatar
    return rows


def _send_discord_dm(user_id, content):
    if not _BOT:
        raise RuntimeError("Discord bot is not running.")
    future = concurrent.futures.Future()
    async def send():
        try:
            user = _BOT.get_user(int(user_id))
            if user is None:
                user = await _BOT.fetch_user(int(user_id))
            future.set_result(await user.send(content[:2000]))
        except Exception as exc:
            future.set_exception(exc)
    _BOT.loop.call_soon_threadsafe(lambda: asyncio.create_task(send()))
    try:
        return future.result(timeout=15)
    except Exception as exc:
        raise RuntimeError(f"Discord DM failed: {exc}") from exc


def _discord_channel_history(channel_id, limit=150):
    """Fetch a channel's current Discord history and archive it locally."""
    if not _BOT or not _BOT.loop or not _BOT.loop.is_running():
        return []
    guild=_BOT.get_guild(int(GAME_GUILD_ID))
    channel=guild.get_channel(int(channel_id)) if guild else None
    fut=concurrent.futures.Future()
    async def read_history():
        try:
            target = channel
            if target is None:
                target = await _BOT.fetch_channel(int(channel_id))
            if not hasattr(target, "history"):
                fut.set_result([])
                return
            rows=[]
            async for m in target.history(limit=max(1,min(int(limit),200)), oldest_first=True):
                if getattr(m.author,"bot",False) and not getattr(m,"webhook_id",None):
                    continue
                try:
                    campaign_store.archive_message(m)
                except Exception:
                    pass
                media = _media_attachment_urls(m)
                rows.append({
                    "id":str(m.id),
                    "author_id":str(m.author.id),
                    "author_name":getattr(m.author,"display_name",None) or getattr(m.author,"name",str(m.author.id)),
                    "avatar_url":str(getattr(getattr(m.author,"display_avatar",None),"url","") or ""),
                    "text":m.content or "",
                    "kind":"public",
                    "channel_id":str(channel_id),
                    "channel_name":getattr(target,"name","channel"),
                    "created_at":m.created_at.isoformat(),
                    "image":media[0] if media else None,
                    "images":media,
                    "source":"discord"
                })
            fut.set_result(rows)
        except Exception as exc:
            fut.set_exception(exc)
    _BOT.loop.call_soon_threadsafe(lambda: asyncio.create_task(read_history()))
    try:
        return _refresh_member_identity(fut.result(timeout=15))
    except Exception as exc:
        print(f"[web] channel history warning: {type(exc).__name__}: {exc}")
        return []

def _campaign_channel_history(channel_id, limit=200):
    """Read archived Discord messages locally instead of repeatedly hitting REST."""
    with campaign_store._LOCK, campaign_store._connect() as db:
        rows=[dict(r) for r in db.execute(
            "SELECT message_id,author_id,author_name,content,created_at FROM messages "
            "WHERE guild_id=? AND channel_id=? ORDER BY created_at DESC LIMIT ?",
            (str(GAME_GUILD_ID), str(channel_id), max(1,min(int(limit),500)))).fetchall()]
    rows.reverse()
    out=[]
    for r in rows:
        try: at=__import__("datetime").datetime.fromisoformat(str(r.get("created_at")).replace("Z","+00:00")).timestamp()
        except Exception:
            try: at=float(r.get("created_at") or 0)
            except Exception: at=0
        out.append({"id":"discord-"+str(r.get("message_id")),"author_id":str(r.get("author_id") or ""),"author_name":r.get("author_name") or "Discord User","avatar_url":"","text":r.get("content") or "","image":None,"at":at,"source":"discord-archive"})
    return _refresh_member_identity(out)

def _discord_general_history(limit=100):
    """Read the configured Discord General channel so the web General page is
    a real view of Discord, including messages that existed before the website.
    """
    if not _BOT or not _BOT.loop or not _BOT.loop.is_running():
        return []
    guild = _BOT.get_guild(int(GAME_GUILD_ID))
    channel = guild.get_channel(int(GENERAL_CHANNEL_ID)) if guild else None
    fut = concurrent.futures.Future()
    async def read_history():
        try:
            target = channel
            if target is None:
                target = await _BOT.fetch_channel(int(GENERAL_CHANNEL_ID))
            if not hasattr(target, "history"):
                fut.set_result([])
                return
            rows=[]
            async for m in target.history(limit=max(1,min(int(limit),200)), oldest_first=True):
                if getattr(m.author, "bot", False) and not getattr(m, "webhook_id", None):
                    continue
                media = _media_attachment_urls(m)
                rows.append({
                    "id":"discord-"+str(m.id),
                    "author_id":str(m.author.id),
                    "author_name":getattr(m.author,"display_name",None) or getattr(m.author,"name",str(m.author.id)),
                    "avatar_url":str(getattr(getattr(m.author,"display_avatar",None),"url","") or ""),
                    "text":m.content or "",
                    "image":media[0] if media else None,
                    "images":media,
                    "at":m.created_at.timestamp(),
                    "source":"discord",
                })
            fut.set_result(rows)
        except Exception as exc:
            fut.set_exception(exc)
    _BOT.loop.call_soon_threadsafe(lambda: asyncio.create_task(read_history()))
    try:
        return _refresh_member_identity(fut.result(timeout=12))
    except Exception as exc:
        print(f"[web] General history warning: {type(exc).__name__}: {exc}")
        return []


def _live_general_history(limit=200):
    """Return a short-lived live snapshot of Discord General.

    The website polls its social feed frequently.  Cache the Discord read so
    that polling does not hammer Discord, while still loading messages that
    were posted before the bot's in-memory event bridge started.
    """
    now = time.time()
    with _GENERAL_HISTORY_LOCK:
        cached = list(_GENERAL_HISTORY_CACHE["items"])
        if cached and now - float(_GENERAL_HISTORY_CACHE["at"]) < 2:
            return cached[-limit:]
    rows = _discord_general_history(limit)
    if rows:
        with _GENERAL_HISTORY_LOCK:
            _GENERAL_HISTORY_CACHE["at"] = now
            _GENERAL_HISTORY_CACHE["items"] = rows[-500:]
        return rows[-limit:]
    return cached[-limit:]

def record_ooc_discord_message(message):
    """Called by the Discord bot for the configured General channel."""
    try:
        if getattr(message, "author", None) is None or (getattr(message.author, "bot", False) and not getattr(message, "webhook_id", None)):
            return
        if not message.guild or int(message.guild.id) != int(GAME_GUILD_ID):
            return
        if int(message.channel.id) != int(GENERAL_CHANNEL_ID):
            return
        campaign_store.archive_message(message)
        state = _load_world_state()
        media = _media_attachment_urls(message)
        item = {
            "id": "discord-" + str(message.id),
            "author_id": str(message.author.id),
            "author_name": getattr(message.author, "display_name", None) or getattr(message.author, "name", str(message.author.id)),
            "avatar_url": _discord_avatar_url({"id": str(message.author.id), "avatar": getattr(message.author, "avatar", None) and getattr(message.author.avatar, "key", None)}),
            "text": message.content or "",
            "image": media[0] if media else None,
            "images": media,
            "at": time.time(),
            "source": "discord",
        }
        items = state.setdefault("ooc_messages", [])
        if not any(str(x.get("id")) == item["id"] for x in items):
            items.append(item)
            state["ooc_messages"] = items[-500:]
            _save_world_state(state)
            with _GENERAL_HISTORY_LOCK:
                cached = [x for x in _GENERAL_HISTORY_CACHE["items"] if str(x.get("id")) != item["id"]]
                cached.append(item)
                _GENERAL_HISTORY_CACHE["at"] = time.time()
                _GENERAL_HISTORY_CACHE["items"] = cached[-500:]
            _broadcast("general_message", {"item": item})
    except Exception as exc:
        print(f"[web] general bridge warning: {type(exc).__name__}: {exc}")


def record_game_discord_message(message):
    """Archive and live-sync Discord text-channel messages for the web client.

    The old bridge only handled GAME_CHANNEL_ID, which made every other Discord
    channel appear empty in the web client.  Keep the archive channel-aware so
    the UI can display the actual channel the message came from.
    """
    try:
        if not message.guild or int(message.guild.id) != int(GAME_GUILD_ID):
            return
        channel = getattr(message, "channel", None)
        if channel is None or not hasattr(channel, "id"):
            return
        if getattr(message.author, "bot", False) and not getattr(message, "webhook_id", None):
            return
        # Archive every text/announcement/thread message the bot can see.
        campaign_store.archive_message(message)
        media = _media_attachment_urls(message)
        item={
            "id":str(message.id),
            "name":getattr(message.author,"display_name",None) or getattr(message.author,"name",str(message.author.id)),
            "text":message.content or "",
            "kind":"public",
            "author_id":str(message.author.id),
            "avatar_url":str(getattr(getattr(message.author,"display_avatar",None),"url","") or ""),
            "channel_id":str(channel.id),
            "channel_name":getattr(channel, "name", "channel"),
            "created_at":message.created_at.isoformat(),
            "image":media[0] if media else None,
            "images":media,
            "source":"discord"
        }
        _broadcast("game_message", {"item": item})
    except Exception as exc:
        print(f"[web] game bridge warning: {type(exc).__name__}: {exc}")


def _messages(limit=150, channel_id=GAME_CHANNEL_ID):
    """Return archived messages for one exact campaign channel."""
    with campaign_store._LOCK, campaign_store._connect() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT * FROM messages WHERE guild_id=? AND channel_id=? ORDER BY created_at DESC LIMIT ?",
            (str(GAME_GUILD_ID), str(channel_id), int(limit))).fetchall()]
    rows.reverse()
    for row in rows:
        row["id"] = row.get("message_id")
        row["kind"] = "gm" if str(row.get("author_id")) in {str(x) for x in GM_USER_IDS} else "public"
        row["name"] = row.get("author_name") or "Unknown"
        row["text"] = row.get("content") or ""
    return _refresh_member_identity(rows)


def _find_message(mid):
    with campaign_store._LOCK, campaign_store._connect() as db:
        row = db.execute("SELECT * FROM messages WHERE guild_id=? AND message_id=?", (str(GAME_GUILD_ID), str(mid))).fetchone()
    return dict(row) if row else None


ARO_ASPECTS = {
    "Aure": "The power to control the essence of light itself.",
    "Tyreror": "The power to control any type of liquid, even blood.",
    "Infernia": "The power to control flames. White flames are the hardest to obtain and control.",
    "Gravitos": "The power to control gravity itself.",
    "Astria": "The power to manipulate space.",
    "Mystia": "The power to control one's mind.",
    "Tempest": "The power to control lightning.",
}
ARO_RULES = {
    "name": "Aro Aspects",
    "summary": "An Aro Aspect is the expression of a character's awakened life essence. Every character chooses one Aspect during character creation in the web client.",
    "growth": "Every choice can strengthen or weaken Aro. Two characters with the same Aspect can have different Aro strength.",
    "colours": {
        "red": "Aro is weakening",
        "green": "Aro is growing stronger",
        "blue": "Aro is close to awakening",
        "yellow": "Aro is evolving",
    },
    "connection": "Before awakening, a character can communicate with Aro. Genuine connection strengthens the future Aspect; seeking Aro only for power weakens it.",
    "evolution": "At a Critical Evolution Point, a Yellow Evolution Stone can create a completely new Aspect shaped by personality, experiences, and true nature.",
    "form": "Aro takes the form its user wants. It must remain non-explicit.",
}

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "AnonymousBotWeb/1.1"
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/healthz":
            discord_ready = bool(_BOT and _BOT.is_ready() and _BOT.loop and _BOT.loop.is_running())
            guild_ready = bool(_BOT and _BOT.get_guild(int(GAME_GUILD_ID)))
            return _json(self, {"ok": bool(discord_ready and guild_ready), "service": "anonymous-rpg-web", "port": int(WEB_PORT), "discord_ready": discord_ready, "guild_ready": guild_ready})
        if parsed.path == "/login":
            if not DISCORD_CLIENT_ID:
                return _json(self, {"error": "DISCORD_CLIENT_ID is not configured."}, 503)
            oauth_state = _oauth_state_new()
            redirect_uri = _request_redirect_uri(self)
            params = urllib.parse.urlencode({"client_id": DISCORD_CLIENT_ID, "response_type": "code", "redirect_uri": redirect_uri, "scope": "identify guilds", "state": oauth_state})
            return _redirect(self, "https://discord.com/oauth2/authorize?" + params)
        if parsed.path == "/oauth/callback":
            query = urllib.parse.parse_qs(parsed.query)
            code = query.get("code", [""])[0]
            oauth_state = query.get("state", [""])[0]
            if not code or not _oauth_state_valid(oauth_state):
                return _redirect(self, "/login")
            try:
                redirect_uri = _request_redirect_uri(self)
                token = _discord_token(code, redirect_uri)
                user = _discord_me(token["access_token"])
                token = token["access_token"]
                oauth_guilds = _discord_oauth_guilds(token)
                is_member = _guild_member(user["id"]) is not None or any(str(g.get("id")) == str(GAME_GUILD_ID) for g in oauth_guilds)
                if not is_member:
                    return _json(self, {"error": "Your Discord account is not a member of the campaign server."}, 403)
                sid = secrets.token_urlsafe(32)
                with _SESSIONS_LOCK:
                    _SESSIONS[sid] = {"user": user, "access_token": token, "expires": _now() + 86400, "role": "gm" if _gm(user["id"], token) else "player"}
                _, secure = _request_base(self)
                return _redirect(self, "/", _cookie(sid, secure=secure))
            except Exception as exc:
                return _json(self, {"error": f"Discord OAuth failed: {type(exc).__name__}: {exc}"}, 502)
        if parsed.path == "/logout":
            return _redirect(self, "/login", "anon_session=deleted; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
        if parsed.path == "/api/events":
            session = _session_from_request(self)
            if not session:
                return _json(self, {"error": "unauthorized"}, 401)
            q = queue.Queue(maxsize=200)
            with _SSE_LOCK:
                _SSE_CLIENTS.add(q)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                initial = {"event":"connected","data":{"role":session.get("role","player")},"at":time.time()}
                self.wfile.write(("event: connected\ndata: " + json.dumps(initial["data"]) + "\n\n").encode("utf-8"))
                self.wfile.flush()
                while True:
                    try:
                        item=q.get(timeout=20)
                        block=f"event: {item['event']}\ndata: {json.dumps(item.get('data') or {}, ensure_ascii=False)}\n\n"
                    except queue.Empty:
                        block=": keepalive\n\n"
                    self.wfile.write(block.encode("utf-8")); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with _SSE_LOCK:
                    _SSE_CLIENTS.discard(q)
            return
        if parsed.path == "/api/session":
            session = _session_from_request(self)
            if not session:
                return _json(self, {"authenticated": False}, 401)
            u = session["user"]
            # Never trust the role cached at OAuth time. Re-evaluate every session
            # request so a configured GM becomes a GM immediately without a refresh.
            role = "gm" if _gm(u["id"], session.get("access_token")) else "player"
            session["role"] = role
            char = _character_for(u["id"])
            return _json(self, {"authenticated": True, "user": _session_user_payload(u), "role": role, "character": char, "gms": _gm_users(), "campaign": {"guild_id": str(GAME_GUILD_ID), "name": CAMPAIGN_NAME}})
        if parsed.path == "/api/diagnostics":
            session = _session_from_request(self)
            if not session or session.get("role") != "gm":
                return _json(self, {"error": "forbidden"}, 403)
            bot_online = bool(_BOT and _BOT.loop and _BOT.loop.is_running())
            guild = _BOT.get_guild(int(GAME_GUILD_ID)) if _BOT else None
            channels = []
            for cid, label in ((GAME_CHANNEL_ID, "Game"), (GENERAL_CHANNEL_ID, "General")):
                channel = guild.get_channel(int(cid)) if guild else None
                record = {"label": label, "id": str(cid), "cached": bool(channel), "name": getattr(channel, "name", None)}
                me = getattr(guild, "me", None) if guild else None
                if channel and me:
                    try:
                        perms = channel.permissions_for(me)
                        record["permissions"] = {
                            "view_channel": bool(perms.view_channel),
                            "read_message_history": bool(perms.read_message_history),
                            "send_messages": bool(perms.send_messages),
                            "manage_webhooks": bool(perms.manage_webhooks),
                            "attach_files": bool(perms.attach_files),
                            "embed_links": bool(perms.embed_links),
                        }
                    except Exception:
                        record["permissions"] = None
                channels.append(record)
            return _json(self, {"ok": True, "bot_online": bot_online, "guild_cached": bool(guild), "channels": channels})
        if parsed.path == "/api/gm/lore":
            session = _session_from_request(self)
            if not session or session["role"] != "gm": return _json(self,{"error":"forbidden"},403)
            uid = str(session["user"]["id"])
            params = urllib.parse.parse_qs(parsed.query)
            query = str(params.get("query", [""])[0]).strip()
            if not query: return _json(self,{"error":"Query is required."},400)
            try:
                relevant = _best_lore(GAME_GUILD_ID, query, include_gm=True)
            except Exception:
                relevant = []
            results=[]
            for _, kind, row in relevant[:30]:
                text = row.get("description") or row.get("text") or row.get("content") or ""
                label = row.get("name") or row.get("type") or kind
                results.append({"kind":kind,"label":label,"text":re.sub(r"\s+"," ",str(text)).strip()[:900],"source_id":str(row.get("message_id") or row.get("id") or "")})
            answer = ""
            if params.get("ai", [""])[0].lower() in {"1", "true", "yes", "on"}:
                try:
                    answer = asyncio.run(_gemini_lore_answer(GAME_GUILD_ID, query, uid, include_gm=True)) or ""
                except Exception:
                    answer = ""
            return _json(self,{"ok":True,"query":query,"results":results,"answer":answer})

        if parsed.path == "/api/gm/typing":
            session = _session_from_request(self)
            if not session or session["role"] != "gm": return _json(self,{"error":"forbidden"},403)
            guild = _BOT.get_guild(int(GAME_GUILD_ID)) if _BOT else None
            channel = guild.get_channel(int(GAME_CHANNEL_ID)) if guild else None
            if not channel or not _BOT or not _BOT.loop.is_running(): return _json(self,{"error":"Discord game channel is unavailable."},503)
            fut=concurrent.futures.Future()
            async def runner():
                try: await channel.typing(); fut.set_result(True)
                except Exception as exc: fut.set_exception(exc)
            _BOT.loop.call_soon_threadsafe(lambda: asyncio.create_task(runner()))
            try: fut.result(timeout=5)
            except Exception as exc: return _json(self,{"error":str(exc)},503)
            return _json(self,{"ok":True})

        if parsed.path == "/api/gm/advanced":
            if session["role"] != "gm": return _json(self,{"error":"forbidden"},403)
            state=_load_world_state(); adv=state.setdefault("advanced", {}); action=str(body.get("action") or "state")
            if action == "ambience_config":
                amb=adv.setdefault("ambience", {});
                for k in ("enabled","auto_battle","auto_funny"):
                    if k in body: amb[k]=bool(body[k])
                if isinstance(body.get("state_audio"),dict): amb["state_audio"].update({str(k): (str(v) if v else None) for k,v in body["state_audio"].items()})
            elif action == "ambience_state":
                state_name=str(body.get("state") or "calm").lower()
                if state_name not in {"calm","tension","combat","critical","funny","silence"}: return _json(self,{"error":"Invalid ambience state."},400)
                amb=adv.setdefault("ambience",{}); amb["state"]=state_name
                mapped=(amb.get("state_audio") or {}).get(state_name)
                if mapped: state["active_audio"]={"id":str(mapped),"loop":True,"nonce":uuid.uuid4().hex}
            elif action == "character_state":
                target=str(body.get("player_id") or ""); st=str(body.get("state") or "normal").lower(); enabled=bool(body.get("enabled",True))
                if not target: return _json(self,{"error":"player_id is required."},400)
                allowed_states={"normal","silenced","unconscious","panic","fear","drunk","poisoned","restrained","stunned","custom"}
                if st not in allowed_states: return _json(self,{"error":"Invalid character state."},400)
                adv.setdefault("character_states",{})[target]={"state":st,"enabled":enabled,"limit":max(0,int(body.get("limit") or 0)),"filter":str(body.get("filter") or ""),"updated_at":time.time()}
            elif action == "timeline":
                sub=str(body.get("subaction") or "create"); timelines=adv.setdefault("timelines",[])
                if sub=="delete": timelines[:]=[x for x in timelines if str(x.get("id"))!=str(body.get("id"))]
                elif sub=="lock":
                    for x in timelines:
                        if str(x.get("id"))==str(body.get("id")): x["locked"]=bool(body.get("locked",True))
                else:
                    item={"id":str(body.get("id") or "timeline-"+uuid.uuid4().hex),"name":str(body.get("name") or "New Timeline"),"location":str(body.get("location") or ""),"players":[str(x) for x in (body.get("players") or [])],"locked":False,"created_at":time.time()}
                    old=next((x for x in timelines if str(x.get("id"))==item["id"]),None)
                    if old: old.update(item)
                    else: timelines.append(item)
            elif action == "timeline_assign":
                target=str(body.get("player_id") or ""); tid=str(body.get("timeline_id") or "")
                if target and tid: adv.setdefault("active_timelines",{})[target]=tid
            elif action == "storybook_merge":
                tids=[str(x) for x in (body.get("timeline_ids") or [])]
                title=str(body.get("title") or "Story Book").strip()[:120]
                timeline_names=[x.get("name") for x in adv.get("timelines",[]) if str(x.get("id")) in tids]
                source_messages=_messages(1200)
                story_lines=[]
                for msg in source_messages:
                    name=str(msg.get("author_name") or msg.get("name") or "Unknown"); text=str(msg.get("content") or msg.get("text") or "").strip()
                    if text: story_lines.append(f"{name}: {text}")
                content="\n\n".join(story_lines)[-120000:]
                adv.setdefault("storybooks",[]).append({"id":"storybook-"+uuid.uuid4().hex,"title":title,"timelines":tids,"timeline_names":timeline_names,"created_at":time.time(),"status":"merged","content":content})
            elif action == "persona_override":
                target=str(body.get("player_id") or ""); persona=body.get("persona")
                if target: adv.setdefault("persona_overrides",{})[target]=persona if persona else None
            elif action == "pov_rule":
                mid=str(body.get("message_id") or ""); audiences=[str(x) for x in (body.get("audience_ids") or [])]
                if mid: adv.setdefault("pov_rules",{})[mid]=audiences
            else:
                return _json(self,{"error":"unknown advanced action"},400)
            state["version"]=4; _save_world_state(state); campaign_store.audit_event(GAME_GUILD_ID,"gm_advanced_control","web",actor_id=uid,actor_name=session["user"].get("username"),details={"action":action})
            return _json(self,{"ok":True,"advanced":adv,"state":state})
        if parsed.path == "/api/gm/state":
            session = _session_from_request(self)
            if not session or session["role"] != "gm":
                return _json(self, {"error": "forbidden"}, 403)
            return _json(self, {"state": _load_world_state()})
        if parsed.path == "/api/players":
            guild = _BOT.get_guild(int(GAME_GUILD_ID)) if _BOT else None
            items = []
            if guild:
                for member in guild.members:
                    if getattr(member, "bot", False) or _gm(member.id):
                        continue
                    items.append({"id": str(member.id), "name": member.display_name, "username": member.name, "avatar_url": str(member.display_avatar.url) if getattr(member, "display_avatar", None) else ""})
            return _json(self, {"items": items})
        if parsed.path == "/api/gm/channels":
            session = _session_from_request(self)
            if not session or session["role"] != "gm": return _json(self, {"error":"forbidden"}, 403)
            guild = _BOT.get_guild(int(GAME_GUILD_ID)) if _BOT else None
            items=[]
            if guild:
                for ch in sorted(guild.channels, key=lambda x: (getattr(x, "position", 0), getattr(x, "id", 0))):
                    if not hasattr(ch, "overwrites_for") or getattr(ch, "type", None).__str__().lower() not in {"text", "announcement", "forum"}:
                        continue
                    ow = ch.overwrites_for(guild.default_role)
                    items.append({"id":str(ch.id),"name":ch.name,"type":str(getattr(ch,"type","")),"locked":ow.send_messages is False,"category":getattr(getattr(ch,"category",None),"name",None)})
            return _json(self, {"items":items})
        if parsed.path == "/api/gm/players":
            session = _session_from_request(self)
            if not session or session["role"] != "gm":
                return _json(self, {"error": "forbidden"}, 403)
            guild = _BOT.get_guild(int(GAME_GUILD_ID)) if _BOT else None
            items=[]
            members=list(getattr(guild,"members",[]) or []) if guild else []
            # The member cache can be partial even with the Members intent enabled.
            # For GM controls, prefer an authoritative fetch when the cache does not
            # contain any eligible players so the Danger player selector cannot appear empty.
            eligible_cached=[m for m in members if not getattr(m,"bot",False) and not _gm(m.id)]
            if guild and not eligible_cached and _BOT and _BOT.loop.is_running():
                fut=concurrent.futures.Future()
                async def fetch_all():
                    try: fut.set_result([m async for m in guild.fetch_members(limit=None)])
                    except Exception: fut.set_result([])
                _BOT.loop.call_soon_threadsafe(lambda: asyncio.create_task(fetch_all()))
                try: members=fut.result(timeout=15)
                except Exception: members=[]
            gm_ids={str(x) for x in GM_USER_IDS}
            for member in members:
                mid=str(member.id)
                if getattr(member,"bot",False) or mid in gm_ids:
                    continue
                perms=getattr(member,"guild_permissions",None)
                if perms and (perms.administrator or perms.manage_guild or perms.manage_channels):
                    continue
                char=_character_for(member.id)
                items.append({"id":mid,"name":member.display_name or member.name,"username":member.name,"avatar_url":str(member.display_avatar.url) if getattr(member,"display_avatar",None) else "","character":char})
            return _json(self,{"items":items})
        if parsed.path == "/api/character":
            session = _session_from_request(self)
            if not session:
                return _json(self, {"error": "unauthorized"}, 401)
            uid = str(session["user"]["id"])
            state = _load_world_state()
            char = _character_for(uid)
            return _json(self, {
                "character": char,
                "aro_aspects": ARO_ASPECTS,
                "aro_rules": ARO_RULES,
            })

        if parsed.path == "/api/gm/characters":
            session = _session_from_request(self)
            if not session or session["role"] != "gm":
                return _json(self, {"error": "forbidden"}, 403)
            state = _load_world_state()
            records = dict(state.get("character_records") or {})
            guild = _BOT.get_guild(int(GAME_GUILD_ID)) if _BOT else None
            if guild:
                for member in guild.members:
                    if getattr(member, "bot", False) or _gm(member.id):
                        continue
                    char = _character_for(member.id)
                    cid = str(char.get("id") or member.id)
                    rec = records.get(cid) or {"id": cid, "type": "PLAYER"}
                    rec.setdefault("name", char.get("name") or member.display_name)
                    rec.setdefault("status", char.get("status") or "alive")
                    rec.setdefault("image", char.get("image"))
                    rec.setdefault("aro", char.get("aro") or {})
                    records[cid] = rec
            npcs = state.get("npc_records") or {}
            return _json(self, {
                "players": records,
                "npcs": npcs,
                "aro_aspects": ARO_ASPECTS,
            })

        if parsed.path == "/api/social":
            session = _session_from_request(self)
            if not session:
                return _json(self, {"error": "unauthorized"}, 401)
            state = _load_world_state()
            kind = urllib.parse.parse_qs(parsed.query).get("kind", ["ooc"])[0]
            uid = str(session["user"]["id"])
            if kind == "ooc":
                archived = _campaign_channel_history(GENERAL_CHANNEL_ID, 200)
                live = _live_general_history(200)
                cached = state.get("ooc_messages") or []
                merged = {str(x.get("id")): x for x in cached if x.get("id")}
                # Historical rows may not have an avatar URL. Never discard a
                # known Discord avatar when merging a lower-fidelity archive row.
                for x in [*archived, *live]:
                    key = str(x.get("id"))
                    previous = merged.get(key) or {}
                    if not x.get("avatar_url") and previous.get("avatar_url"):
                        x = {**x, "avatar_url": previous["avatar_url"]}
                    merged[key] = x
                items = sorted(merged.values(), key=lambda x: float(x.get("at") or 0))[-500:]
                # A fast web send is shown immediately, then Discord echoes it
                # back with its actual message ID. Collapse that short hand-off
                # into one message rather than making General look duplicated.
                deduped = []
                for item in items:
                    duplicate = next((old for old in reversed(deduped) if str(old.get("author_id")) == str(item.get("author_id")) and str(old.get("text")) == str(item.get("text")) and abs(float(old.get("at") or 0) - float(item.get("at") or 0)) < 30), None)
                    if duplicate:
                        if str(item.get("id", "")).startswith("discord-"):
                            deduped[deduped.index(duplicate)] = item
                        continue
                    deduped.append(item)
                items = deduped[-500:]
                if items != cached:
                    state["ooc_messages"] = items
                    _save_world_state(state)
                return _json(self, {"items": items})
            if kind == "ideas":
                items = state.get("ideas") or []
                if session["role"] != "gm":
                    items = [x for x in items if x.get("author_id") == uid or x.get("status") == "published"]
                return _json(self, {"items": items})
            if kind == "groups":
                return _json(self, {"items": state.get("groups") or []})
            if kind == "dm":
                peer = urllib.parse.parse_qs(parsed.query).get("peer", [""])[0]
                key = "::".join(sorted([uid, str(peer)])) if peer else uid
                return _json(self, {"items": (state.get("dm_threads") or {}).get(key, [])})
            if kind == "settings":
                return _json(self, {"settings": (state.get("user_settings") or {}).get(uid, {})})
            if kind == "gmchat":
                if session["role"] != "gm": return _json(self,{"error":"forbidden"},403)
                return _json(self,{"items":[x for x in (state.get("gm_messages") or []) if x.get("scope")=="gmchat"]})
            return _json(self, {"error": "unknown social kind"}, 400)
        if parsed.path == "/api/advanced":
            session = _session_from_request(self)
            if not session:
                return _json(self, {"error": "unauthorized"}, 401)
            state = _load_world_state()
            advanced = state.get("advanced") or {}
            uid = str(session["user"]["id"])
            payload = {
                "ambience": advanced.get("ambience") or {},
                "character_state": (advanced.get("character_states") or {}).get(uid) or {"state":"normal","enabled":False},
                "timeline": (advanced.get("timelines") or [{}])[0],
                "active_timeline": (advanced.get("active_timelines") or {}).get(uid) or "timeline-main",
                "personas": (advanced.get("personas") or {}).get(uid) or [],
                "persona_override": (advanced.get("persona_overrides") or {}).get(uid),
            }
            if session["role"] == "gm":
                payload["advanced"] = advanced
            return _json(self, payload)
        if parsed.path == "/api/world":
            session = _session_from_request(self)
            if not session:
                return _json(self, {"error": "unauthorized"}, 401)
            state = _load_world_state()
            uid = str(session["user"]["id"])
            region_id = str(state.get("player_regions", {}).get(uid) or "")
            # Players receive only their own location override; GMs receive the full control state.
            if session["role"] == "gm":
                payload = state
            else:
                override = state.get("danger_player_overrides", {}).get(uid) or {}
                player_region = next((r for r in state.get("regions", []) if isinstance(r, dict) and str(r.get("id")) == region_id), None)
                payload = {
                    "version": state.get("version", 3),
                    "session_live": state.get("session_live", False),
                    "story_locked": state.get("story_locked", False),
                    "regions": [{k:v for k,v in r.items() if k != "assignedPlayers"} for r in state.get("regions", []) if isinstance(r, dict)],
                    "player_region_id": region_id,
                    "current_region": {k:v for k,v in (player_region or {}).items() if k != "assignedPlayers"},
                    "current_chapter": state.get("current_chapter") or {},
                    "font_commands": [],
                    "custom_fonts": state.get("custom_fonts") or [],
                    "danger_effect": {
                        "active": bool((state.get("surprise_danger") or {}).get("level", 0) or (override.get("enabled") and override.get("level", 0)) or state.get("danger_enabled", {}).get(region_id)),
                        "intensity": round(max(0.0, min(1.0, ((float(override.get("level", 0))/100.0) if override.get("enabled") else ((float(state.get("region_danger", {}).get(region_id, 0))/100.0) if state.get("danger_enabled", {}).get(region_id) else float((state.get("surprise_danger") or {}).get("level", 0))/100.0)))), 3),
                    },
                    "emergency": {"player_lock": bool((state.get("emergency") or {}).get("player_lock"))},
                    "active_audio": state.get("active_audio"),
                    "audio_assets": state.get("audio_assets") or [],
                    "ability_catalog": state.get("ability_catalog") or {},
                    "audio_assets": state.get("audio_assets") or [],
                    "inventory": [x for x in state.get("web_items",[]) if str(x.get("owner_id"))==uid],
                    "economy": (state.get("economy") or {}).get("balances",{}).get(uid,{}) if isinstance(state.get("economy"),dict) else {},
                }
            return _json(self, {"state": payload})
        if parsed.path == "/api/notifications":
            session = _session_from_request(self)
            if not session:
                return _json(self, {"error": "unauthorized"}, 401)
            state = _load_world_state()
            return _json(self, {"items": (state.get("campaign_notifications") or [])[-100:]})
        if parsed.path == "/api/channels":
            session = _session_from_request(self)
            if not session:
                return _json(self, {"error": "unauthorized"}, 401)
            guild = _BOT.get_guild(int(GAME_GUILD_ID)) if _BOT else None
            items=[]
            if guild:
                for ch in sorted(getattr(guild, "channels", []) or [], key=lambda x: (getattr(x, "position", 0), getattr(x, "id", 0))):
                    typ=str(getattr(ch, "type", "")).lower()
                    if typ not in {"text", "announcement", "news", "public_thread", "private_thread", "thread"}:
                        continue
                    me=getattr(guild, "me", None)
                    if me:
                        try:
                            perms=ch.permissions_for(me)
                            if not perms.view_channel or not perms.read_message_history:
                                continue
                        except Exception:
                            pass
                    items.append({"id":str(ch.id),"name":getattr(ch,"name","channel"),"category":getattr(getattr(ch,"category",None),"name",None),"type":typ,"is_game":str(ch.id)==str(GAME_CHANNEL_ID),"is_general":str(ch.id)==str(GENERAL_CHANNEL_ID)})
            # The Discord cache can be empty briefly after startup.  Always expose
            # the two configured bridges so the web client can still load their
            # archived/live histories instead of remaining on "Loading channels".
            known={str(x.get("id")) for x in items}
            for cid, name, game, general in ((str(GAME_CHANNEL_ID), "game", True, False), (str(GENERAL_CHANNEL_ID), "general", False, True)):
                if cid not in known:
                    items.append({"id":cid,"name":name,"category":"Campaign","type":"text","is_game":game,"is_general":general})
            return _json(self, {"items":items})
        if parsed.path == "/api/channel-messages":
            session = _session_from_request(self)
            if not session:
                return _json(self, {"error": "unauthorized"}, 401)
            params=urllib.parse.parse_qs(parsed.query)
            channel_id=str(params.get("channel_id", [GAME_CHANNEL_ID])[0])
            limit=max(1,min(int(params.get("limit", [150])[0]),500))
            guild = _BOT.get_guild(int(GAME_GUILD_ID)) if _BOT else None
            channel = guild.get_channel(int(channel_id)) if guild and channel_id.isdigit() else None
            configured = channel_id in {str(GAME_CHANNEL_ID), str(GENERAL_CHANNEL_ID)}
            if not channel and not configured:
                return _json(self,{"error":"channel_not_found"},404)
            me=getattr(guild,"me",None)
            if me and channel:
                try:
                    perms=channel.permissions_for(me)
                    if not perms.view_channel or not perms.read_message_history:
                        return _json(self,{"error":"forbidden"},403)
                except Exception:
                    pass
            live_items=_discord_channel_history(channel_id, limit)
            items=_messages(limit, channel_id)
            # Prefer the live Discord archive for channel content, then merge any
            # locally-created web messages that belong to the selected channel.
            merged={str(x.get("id")):x for x in items if x.get("id")}
            for x in live_items: merged[str(x.get("id"))]=x
            items=list(merged.values())
            items.sort(key=lambda x:str(x.get("created_at") or x.get("at") or ""))
            # GM-only web messages are never exposed through public channel views.
            items=[x for x in items if x.get("scope") != "gmchat" and not x.get("private")]
            state = _load_world_state()
            broadcasts=[x for x in (state.get("gm_messages") or []) if x.get("scope")=="broadcast" and str(x.get("channel_id") or GAME_CHANNEL_ID)==channel_id]
            items += broadcasts[-limit:]
            if session["role"]=="gm":
                items += [x for x in (state.get("gm_messages") or []) if x.get("scope") not in {"gmchat","broadcast"} and str(x.get("channel_id") or channel_id)==channel_id][-limit:]
                items.sort(key=lambda x:str(x.get("created_at") or x.get("at") or ""))
            else:
                uid = str(session["user"]["id"])
                items += [x for x in (state.get("gm_messages") or []) if x.get("scope") == "pov" and str(x.get("channel_id")) == channel_id and uid in {str(a) for a in (x.get("audience_ids") or [])}][-limit:]
                items.sort(key=lambda x:str(x.get("created_at") or x.get("at") or ""))
            return _json(self,{"items":items[-limit:],"channel_id":channel_id})
        if parsed.path == "/api/messages":
            session = _session_from_request(self)
            if not session:
                return _json(self, {"error": "unauthorized"}, 401)
            uid=str(session["user"]["id"])
            limit=int(urllib.parse.parse_qs(parsed.query).get("limit", [150])[0])
            items=_messages(limit, GAME_CHANNEL_ID)
            if session["role"]!="gm":
                items=[x for x in items if not x.get("audience_ids") or uid in {str(a) for a in (x.get("audience_ids") or [])}]
            if session["role"]=="gm":
                state=_load_world_state()
                items += [x for x in (state.get("gm_messages") or []) if x.get("scope") != "gmchat" and not x.get("private") and str(x.get("channel_id") or GAME_CHANNEL_ID)==str(GAME_CHANNEL_ID)][-limit:]
                items.sort(key=lambda x: str(x.get("created_at") or x.get("at") or ""))
            return _json(self, {"items": items[-limit:]})
        if parsed.path == "/api/gm/inbox":
            session = _session_from_request(self)
            if not session or session["role"] != "gm": return _json(self, {"error":"forbidden"}, 403)
            with campaign_store._LOCK, campaign_store._connect() as db:
                rows = [dict(r) for r in db.execute("SELECT * FROM audit_log WHERE guild_id=? ORDER BY created_at DESC LIMIT 100", (str(GAME_GUILD_ID),)).fetchall()]
            return _json(self, {"items": rows})
        if parsed.path == "/api/gm/character-inbox":
            session = _session_from_request(self)
            if not session or session["role"] != "gm": return _json(self, {"error":"forbidden"}, 403)
            state = _load_world_state()
            return _json(self, {"items": (state.get("character_inbox") or [])[-200:]})
        if parsed.path.startswith("/media/font/"):
            name = Path(parsed.path).name
            safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", name)
            target = Path(DATA_DIR) / "web_fonts" / safe
            if not target.exists() or not target.is_file():
                return _json(self, {"error":"not_found"}, 404)
            mime = {".ttf":"font/ttf", ".otf":"font/otf", ".woff":"font/woff", ".woff2":"font/woff2"}.get(target.suffix.lower(), "application/octet-stream")
            raw = target.read_bytes()
            self.send_response(200); self.send_header("Content-Type", mime); self.send_header("Cache-Control", "public, max-age=3600"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        if parsed.path.startswith("/media/audio/"):
            relative = Path(urllib.parse.unquote(parsed.path[len("/media/audio/"):]))
            library_root = MUSIC_LIBRARY_DIR.resolve()
            if relative.is_absolute() or ".." in relative.parts:
                return _json(self, {"error":"not_found"}, 404)
            target = (library_root / relative).resolve()
            if target != library_root and library_root not in target.parents:
                return _json(self, {"error":"not_found"}, 404)
            if not target.exists() or not target.is_file():
                return _json(self, {"error":"not_found"}, 404)
            mime = "audio/mpeg" if target.suffix.lower()==".mp3" else "audio/ogg" if target.suffix.lower()==".ogg" else "audio/wav" if target.suffix.lower()==".wav" else "audio/mp4"
            raw = target.read_bytes()
            self.send_response(200); self.send_header("Content-Type", mime); self.send_header("Cache-Control", "public, max-age=3600"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        if parsed.path == "/":
            session = _session_from_request(self)
            if not session:
                return _redirect(self, "/login")
        target = ROOT / "index.html" if parsed.path in {"/", "/index.html"} else ROOT / parsed.path.lstrip("/")
        if not target.exists() or not target.is_file() or ROOT not in target.resolve().parents:
            return _json(self, {"error":"not_found"}, 404)
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8" if target.suffix == ".html" else "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def do_POST(self):
        session = _session_from_request(self)
        if not session:
            return _json(self, {"error": "unauthorized"}, 401)
        parsed = urllib.parse.urlparse(self.path)
        body = _body(self)
        uid = str(session["user"]["id"])
        session["role"] = "gm" if _gm(uid, session.get("access_token")) else "player"
        if parsed.path == "/api/gm/session":
            if session["role"] != "gm":
                return _json(self, {"error": "forbidden"}, 403)
            enabled = bool(body.get("enabled"))
            # Starting a session is a Discord action first: the game channel
            # must unlock and its players must receive the start announcement.
            # Do not claim success in the site if the bot cannot do those jobs.
            if not _BOT or not _BOT.loop or not _BOT.loop.is_running():
                return _json(self, {"error": "Discord bot is not running. Start Everything and wait for the bot to be online before starting a session."}, 503)
            guild = _BOT.get_guild(int(GAME_GUILD_ID))
            if guild is None:
                return _json(self, {"error": "Discord campaign server is not available to the bot yet. Wait for it to finish connecting, then try again."}, 503)
            result = {}
            future = concurrent.futures.Future()
            async def session_runner():
                try:
                    from .features import gm_tools
                    if enabled:
                        result = await gm_tools._start_game_now(
                            guild,
                            int(GAME_CHANNEL_ID),
                            started_by=int(uid),
                            briefing={"today":"", "title":CAMPAIGN_NAME, "player_ids":[], "check_in_minutes":5},
                        )
                    else:
                        result = await gm_tools._end_game_from_web(guild, int(uid))
                        if gm_tools._guild(guild.id).get("game_started"):
                            raise RuntimeError("Discord did not confirm that the game session closed.")
                    future.set_result(result or {})
                except Exception as exc:
                    future.set_exception(exc)
            _BOT.loop.call_soon_threadsafe(lambda: asyncio.create_task(session_runner()))
            try:
                result = future.result(timeout=75)
            except Exception as exc:
                return _json(self, {"error": f"Discord session action failed: {exc}"}, 503)

            state = _load_world_state()
            state["session_live"] = enabled
            emergency = state.setdefault("emergency", {})
            if enabled:
                # A manually started session must not remain paused by an old
                # emergency state from a prior session.
                emergency["pause_session"] = False
                # The GM can designate one server-wide main OST. It begins with
                # every successfully announced session; players retain their
                # own local mute control.
                main_ost_id = str(state.get("main_ost_id") or "")
                main_ost = next((item for item in state.get("audio_assets", []) if str(item.get("id")) == main_ost_id), None)
                if not main_ost:
                    main_ost = next((item for item in state.get("audio_assets", []) if "main_ost" in {str(tag).casefold().replace(" ", "_") for tag in (item.get("tags") or [])}), None)
                    if main_ost:
                        main_ost_id = str(main_ost["id"])
                        state["main_ost_id"] = main_ost_id
                if main_ost:
                    state["active_audio"] = {"id": main_ost_id, "name": main_ost.get("name"), "tags": main_ost.get("tags") or ["main-ost"], "loop": True, "nonce": uuid.uuid4().hex, "started_by": uid}
            else:
                # A session soundtrack is never left playing after the real
                # Discord game channel has been locked.
                state["active_audio"] = None
            state.setdefault("session_history", []).append({
                "id": "session-" + uuid.uuid4().hex,
                "started": enabled,
                "at": time.time(),
                "gm_id": uid,
                "gm_name": session["user"].get("username"),
                "chapter_id": (state.get("current_chapter") or {}).get("id"),
                "chapter_name": (state.get("current_chapter") or {}).get("name"),
            })
            state["session_history"] = state["session_history"][-200:]
            state["version"] = 3
            _save_world_state(state)
            _broadcast("session_changed", {"live": enabled})
            _broadcast("audio_changed", {"active_audio": state.get("active_audio")})
            try:
                campaign_store.audit_event(GAME_GUILD_ID, "web_session_toggled", "web", actor_id=uid, actor_name=session["user"].get("username"), details={"live": enabled, "discord": result})
            except Exception as exc:
                print(f"[web] session audit warning: {type(exc).__name__}: {exc}")
            return _json(self, {"ok": True, "state": state, "discord": result})
        if parsed.path == "/api/gm/lore":
            if session["role"] != "gm": return _json(self,{"error":"forbidden"},403)
            query = str(body.get("query") or "").strip()
            if not query: return _json(self,{"error":"Query is required."},400)
            try:
                relevant = _best_lore(GAME_GUILD_ID, query, include_gm=True)
            except Exception:
                relevant = []
            results=[]
            for _, kind, row in relevant[:30]:
                text = row.get("description") or row.get("text") or row.get("content") or ""
                label = row.get("name") or row.get("type") or kind
                results.append({"kind":kind,"label":label,"text":re.sub(r"\s+"," ",str(text)).strip()[:900],"source_id":str(row.get("message_id") or row.get("id") or "")})
            answer = ""
            if body.get("ai"):
                try: answer = asyncio.run(_gemini_lore_answer(GAME_GUILD_ID, query, uid, include_gm=True)) or ""
                except Exception: answer = ""
            return _json(self,{"ok":True,"query":query,"results":results,"answer":answer})
        if parsed.path == "/api/gm/typing":
            if session["role"] != "gm": return _json(self,{"error":"forbidden"},403)
            guild = _BOT.get_guild(int(GAME_GUILD_ID)) if _BOT else None
            channel = guild.get_channel(int(GAME_CHANNEL_ID)) if guild else None
            if not channel and _BOT and _BOT.loop.is_running():
                fut=concurrent.futures.Future()
                async def fetch_typing_channel():
                    try: fut.set_result(await _BOT.fetch_channel(int(GAME_CHANNEL_ID)))
                    except Exception as exc: fut.set_exception(exc)
                _BOT.loop.call_soon_threadsafe(lambda: asyncio.create_task(fetch_typing_channel()))
                try: channel=fut.result(timeout=10)
                except Exception: channel=None
            if not channel or not _BOT or not _BOT.loop.is_running(): return _json(self,{"error":"Discord game channel is unavailable."},503)
            fut=concurrent.futures.Future()
            async def runner():
                try: await channel.typing(); fut.set_result(True)
                except Exception as exc: fut.set_exception(exc)
            _BOT.loop.call_soon_threadsafe(lambda: asyncio.create_task(runner()))
            try: fut.result(timeout=5)
            except Exception as exc: return _json(self,{"error":str(exc)},503)
            return _json(self,{"ok":True})
        if parsed.path == "/api/social/ooc":
            text = str(body.get("text") or "").strip()
            if not text or len(text) > 2000:
                return _json(self, {"error": "Message must be 1-2000 characters."}, 400)
            user = session["user"]
            name = user.get("global_name") or user.get("username") or uid
            state = _load_world_state()
            item = {"id":"general-web-"+uuid.uuid4().hex,"author_id":uid,"author_name":name,"avatar_url":_discord_avatar_url(user),"text":text,"image":None,"images":[],"at":time.time(),"source":"web-pending"}
            items = state.setdefault("ooc_messages", [])
            if not any(str(x.get("id")) == item["id"] for x in items):
                items.append(item); state["ooc_messages"] = items[-500:]; _save_world_state(state); _broadcast("general_message", {"item":item})
            _queue_discord_send(GENERAL_CHANNEL_ID, name, text, _discord_avatar_url(user))
            return _json(self, {"ok": True, "item": item})
        if parsed.path == "/api/social/dm":
            peer = str(body.get("peer_id") or "").strip()
            text = str(body.get("text") or "").strip()
            if not peer or not text:
                return _json(self, {"error": "peer_id and text are required."}, 400)
            key = "::".join(sorted([uid, peer]))
            state = _load_world_state()
            item = {"id": "dm-" + uuid.uuid4().hex, "author_id": uid, "author_name": session["user"].get("global_name") or session["user"].get("username") or uid, "text": text, "at": time.time(), "source": "web"}
            state.setdefault("dm_threads", {}).setdefault(key, []).append(item)
            state["dm_threads"][key] = state["dm_threads"][key][-300:]
            _save_world_state(state)
            try:
                _send_discord_dm(peer, text)
            except Exception as exc:
                # Keep the website thread even if Discord DMs are disabled.
                item["discord_delivery"] = str(exc)
            return _json(self, {"ok": True, "item": item})
        if parsed.path == "/api/social/groups":
            state = _load_world_state()
            action = str(body.get("action") or "create")
            groups = state.setdefault("groups", [])
            if action == "create":
                name = str(body.get("name") or "").strip()
                if not name: return _json(self, {"error": "Group name is required."}, 400)
                members = [str(x).strip() for x in (body.get("members") or []) if str(x).strip()]
                if uid not in members: members.insert(0, uid)
                item = {"id": "group-" + uuid.uuid4().hex, "name": name, "owner_id": uid, "owner_name": session["user"].get("global_name") or session["user"].get("username") or uid, "members": members, "messages": [], "at": time.time()}
                groups.append(item)
            elif action == "delete":
                gid = str(body.get("id") or "")
                groups[:] = [g for g in groups if not (str(g.get("id")) == gid and (str(g.get("owner_id")) == uid or session["role"] == "gm"))]
            elif action == "message":
                gid = str(body.get("id") or "")
                text = str(body.get("text") or "").strip()
                group = next((g for g in groups if str(g.get("id")) == gid), None)
                if not group or (uid not in [str(x) for x in group.get("members", [])] and session["role"] != "gm"): return _json(self, {"error": "You are not in this group."}, 403)
                if not text: return _json(self, {"error": "Message is required."}, 400)
                group.setdefault("messages", []).append({"id":"gm-"+uuid.uuid4().hex,"author_id":uid,"author_name":session["user"].get("global_name") or session["user"].get("username") or uid,"text":text,"at":time.time()})
                group["messages"] = group["messages"][-300:]
            else:
                return _json(self, {"error": "unknown group action"}, 400)
            _save_world_state(state)
            _broadcast("social_update", {"kind":"groups"})
            return _json(self, {"ok": True, "items": groups})
        if parsed.path == "/api/social/idea":
            text = str(body.get("text") or "").strip()
            if not text: return _json(self, {"error": "Idea is required."}, 400)
            state = _load_world_state()
            item = {"id":"idea-"+uuid.uuid4().hex,"author_id":uid,"author_name":session["user"].get("global_name") or session["user"].get("username") or uid,"text":text,"status":"new","at":time.time()}
            state.setdefault("ideas", []).append(item)
            _save_world_state(state)
            campaign_store.audit_event(GAME_GUILD_ID, "web_game_idea", "web", actor_id=uid, actor_name=item["author_name"], details={"text":text[:500]})
            return _json(self, {"ok": True, "item": item})
        if parsed.path == "/api/social/settings":
            state = _load_world_state()
            settings = state.setdefault("user_settings", {}).setdefault(uid, {})
            settings.update(body if isinstance(body, dict) else {})
            _save_world_state(state)
            return _json(self, {"ok": True, "settings": settings})
        if parsed.path == "/api/character/save":
            char = _character_for(uid)
            cid = str(char.get("id") or uid)
            state = _load_world_state()
            records = state.setdefault("character_records", {})
            rec = records.setdefault(cid, {"id": cid, "type": "PLAYER", "owner_id": uid})
            for key in ("name", "age", "sex", "height", "weight", "description", "wears"):
                if key in body:
                    rec[key] = str(body.get(key) or "")
            rec["owner_id"] = uid
            if isinstance(body.get("image"), str) and body.get("image"):
                return _json(self, {"error": "Players cannot change the character portrait."}, 403)
            _save_world_state(state)
            return _json(self, {"ok": True, "character": _character_for(uid)})

        if parsed.path == "/api/character/submit":
            char = _character_for(uid)
            aspect = str(body.get("aro_aspect") or (char.get("aro") or {}).get("aspect") or "").strip()
            if aspect not in ARO_ASPECTS:
                return _json(self, {"error":"Choose an Aro Aspect before submitting the character."}, 400)
            state = _load_world_state()
            item = {
                "id":"character-request-"+uuid.uuid4().hex,
                "player_id":uid,
                "player_name":session["user"].get("global_name") or session["user"].get("username") or uid,
                "character_id":str(char.get("id") or uid),
                "character_name":str(body.get("name") or char.get("name") or session["user"].get("username") or "Character"),
                "age":str(body.get("age") or ""),
                "sex":str(body.get("sex") or ""),
                "height":str(body.get("height") or ""),
                "weight":str(body.get("weight") or ""),
                "description":str(body.get("description") or ""),
                "wears":str(body.get("wears") or ""),
                "aro_aspect":aspect,
                "status":"pending",
                "created_at":time.time(),
            }
            state.setdefault("character_inbox", []).append(item)
            state["character_inbox"] = state["character_inbox"][-200:]
            # Persist the submitted player information into the character record so
            # the GM character index is a real information store, not just an inbox.
            records = state.setdefault("character_records", {})
            cid = str(char.get("id") or uid)
            rec = records.setdefault(cid, {"id":cid,"type":"PLAYER"})
            rec.update({"name":item["character_name"],"age":item["age"],"sex":item["sex"],"height":item["height"],"weight":item["weight"],"description":item["description"],"wears":item["wears"],"type":"PLAYER","owner_id":uid})
            aro = rec.get("aro") or {}
            aro["aspect"] = aspect
            rec["aro"] = aro
            _save_world_state(state)
            campaign_store.audit_event(GAME_GUILD_ID,"character_update_request","web",actor_id=uid,actor_name=item["player_name"],target_type="character",target_id=item["character_id"],details={"aro_aspect":aspect})
            return _json(self,{"ok":True,"item":item})

        if parsed.path == "/api/character/aro":
            char = _character_for(uid)
            if not char.get("id"):
                return _json(self, {"error": "No active character is assigned."}, 409)
            aspect = str(body.get("aspect") or "").strip()
            if aspect not in ARO_ASPECTS:
                return _json(self, {"error": "Invalid Aro Aspect."}, 400)
            state = _load_world_state()
            records = state.setdefault("character_records", {})
            cid = str(char["id"])
            rec = records.setdefault(cid, {"id": cid, "name": char.get("name") or session["user"].get("username"), "type": "PLAYER"})
            existing_aro = rec.get("aro") if isinstance(rec.get("aro"), dict) else {}
            rec["aro"] = dict(existing_aro)
            rec["aro"].update({
                "aspect": aspect,
                "growth": int(existing_aro.get("growth", 0)),
                "color": existing_aro.get("color", "blue"),
                "awakened": bool(existing_aro.get("awakened", False)),
                "form": existing_aro.get("form"),
                "unlocked_abilities": list(existing_aro.get("unlocked_abilities", [])),
            })
            _save_world_state(state)
            campaign_store.audit_event(GAME_GUILD_ID, "aro_aspect_selected", "web", actor_id=uid, actor_name=session["user"].get("username"), target_type="character", target_id=cid, details={"aspect": aspect})
            return _json(self, {"ok": True, "character": _character_for(uid)})

        if parsed.path == "/api/gm/chat":
            if session["role"] != "gm": return _json(self,{"error":"forbidden"},403)
            text=str(body.get("text") or "").strip()
            if not text or len(text)>2000: return _json(self,{"error":"Message must be 1-2000 characters."},400)
            state=_load_world_state(); item={"id":"gmchat-"+uuid.uuid4().hex,"author_id":uid,"author_name":session["user"].get("global_name") or session["user"].get("username") or uid,"text":text,"at":time.time(),"kind":"gm","scope":"gmchat"}
            state.setdefault("gm_messages",[]).append(item); state["gm_messages"]=state["gm_messages"][-500:]; _save_world_state(state); return _json(self,{"ok":True,"item":item})
        if parsed.path == "/api/character/archive":
            char = _character_for(uid)
            cid = str(char.get("id") or uid)
            state = _load_world_state()
            journal = state.setdefault("character_journals", {}).setdefault(cid, [])
            journal.append({"id":"journal-"+uuid.uuid4().hex,"source_message_id":str(body.get("message_id") or ""),"text":str(body.get("text") or ""),"image":body.get("image") if isinstance(body.get("image"), str) else None,"at":time.time()})
            state["character_journals"][cid]=journal[-500:]
            _save_world_state(state)
            return _json(self,{"ok":True,"journal":state["character_journals"][cid]})
        if parsed.path == "/api/gm/ability":
            if session["role"] != "gm": return _json(self,{"error":"forbidden"},403)
            state=_load_world_state(); catalog=state.setdefault("ability_catalog",{})
            action=str(body.get("action") or "create")
            aid=str(body.get("id") or ("ability-"+uuid.uuid4().hex))
            if action=="delete": catalog.pop(aid,None)
            else:
                catalog[aid]={"id":aid,"name":str(body.get("name") or "Unnamed Ability"),"aspect":str(body.get("aspect") or "Custom"),"description":str(body.get("description") or ""),"severity":str(body.get("severity") or "Moderate"),"created_by":uid,"created_at":time.time()}
            _save_world_state(state); return _json(self,{"ok":True,"ability_catalog":catalog})
        if parsed.path == "/api/gm/ability-lock":
            if session["role"] != "gm": return _json(self,{"error":"forbidden"},403)
            cid=str(body.get("character_id") or ""); aid=str(body.get("ability_id") or ""); unlocked=bool(body.get("unlocked"))
            if not cid or not aid: return _json(self,{"error":"character_id and ability_id are required"},400)
            state=_load_world_state(); rec=state.setdefault("character_records",{}).setdefault(cid,{"id":cid,"type":"PLAYER"}); aro=rec.setdefault("aro",{}); unlocked_list=set(str(x) for x in aro.get("unlocked_abilities",[]))
            if unlocked: unlocked_list.add(aid)
            else: unlocked_list.discard(aid)
            aro["unlocked_abilities"]=sorted(unlocked_list); _save_world_state(state); return _json(self,{"ok":True,"character":rec})
        if parsed.path == "/api/gm/evolve-aro":
            if session["role"] != "gm": return _json(self,{"error":"forbidden"},403)
            cid=str(body.get("character_id") or ""); name=str(body.get("name") or "").strip(); power=str(body.get("power") or "").strip(); reason=str(body.get("reason") or "Yellow Evolution Stone")
            if not cid or not name or not power: return _json(self,{"error":"character_id, name, and power are required"},400)
            state=_load_world_state(); rec=state.setdefault("character_records",{}).setdefault(cid,{"id":cid,"type":"PLAYER"}); old=rec.setdefault("aro",{}); old.update({"aspect":name,"awakened":True,"color":"yellow","evolution":{"name":name,"power":power,"reason":reason,"at":time.time()},"unlocked_abilities":[]}); _save_world_state(state); return _json(self,{"ok":True,"character":rec})
        if parsed.path == "/api/gm/companion":
            if session["role"] != "gm": return _json(self,{"error":"forbidden"},403)
            state=_load_world_state(); comps=state.setdefault("companions",[]); action=str(body.get("action") or "create"); cid=str(body.get("id") or ("companion-"+uuid.uuid4().hex))
            if action=="delete": comps[:]=[c for c in comps if str(c.get("id"))!=cid]
            else:
                c=next((x for x in comps if str(x.get("id"))==cid),None) or {"id":cid,"name":"Companion"}; c.update({"name":str(body.get("name") or c.get("name")),"owner_id":str(body.get("owner_id") or c.get("owner_id") or ""),"description":str(body.get("description") or ""),"image":body.get("image") if isinstance(body.get("image"),str) else c.get("image"),"updated_at":time.time()});
                if c not in comps: comps.append(c)
            _save_world_state(state); return _json(self,{"ok":True,"companions":comps})
        if parsed.path == "/api/gm/audio":
            if session["role"] != "gm": return _json(self,{"error":"forbidden"},403)
            state=_load_world_state(); action=str(body.get("action") or "list")
            assets=state.setdefault("audio_assets",[])
            if action=="play":
                aid=str(body.get("id") or "")
                item=next((x for x in assets if str(x.get("id"))==aid),None)
                if not item: return _json(self,{"error":"Music track not found."},404)
                state["active_audio"]={"id":aid,"name":item.get("name"),"tags":item.get("tags") or [],"loop":bool(body.get("loop",True)),"nonce":uuid.uuid4().hex,"started_by":uid}
            elif action=="stop": state["active_audio"]=None
            elif action=="delete":
                aid=str(body.get("id") or ""); item=next((x for x in assets if str(x.get("id"))==aid),None)
                if item and item.get("source") == "server-library": return _json(self,{"error":"Bundled library tracks cannot be deleted from the web panel."},400)
                if item:
                    target=Path(DATA_DIR)/"web_audio"/Path(str(item.get("filename"))).name
                    try: target.unlink(missing_ok=True)
                    except Exception: pass
                assets[:]=[x for x in assets if str(x.get("id"))!=aid]
            elif action=="upload":
                data=str(body.get("data") or ""); name=re.sub(r"[^a-zA-Z0-9_.-]","_",str(body.get("name") or "audio.bin"));
                if not data.startswith("data:audio/") or "," not in data: return _json(self,{"error":"Upload must be an audio data URL"},400)
                raw=base64.b64decode(data.split(",",1)[1]);
                if len(raw)>25*1024*1024: return _json(self,{"error":"Audio files are limited to 25 MB"},413)
                folder=Path(DATA_DIR)/"web_audio"; folder.mkdir(parents=True,exist_ok=True); filename=uuid.uuid4().hex+Path(name).suffix.lower(); (folder/filename).write_bytes(raw)
                assets.append({"id":"audio-"+uuid.uuid4().hex,"name":name,"filename":filename,"url":"/media/audio/"+filename,"tags":[str(x) for x in (body.get("tags") or ["custom"]) if str(x).strip()],"source":"upload","at":time.time()})
            elif action=="assign_theme":
                target_type=str(body.get("target_type") or "player").casefold()
                target_id=str(body.get("target_id") or body.get("player_id") or "")
                aid=str(body.get("id") or "")
                item=next((x for x in assets if str(x.get("id"))==aid),None)
                if target_type not in {"player","npc"} or not target_id or not item:
                    return _json(self,{"error":"Choose a player or NPC and a valid track."},400)
                key=f"{target_type}:{target_id}"
                state.setdefault("advanced",{}).setdefault("theme_songs",{})[key]={"audio_id":aid,"target_type":target_type,"target_id":target_id,"assigned_by":uid,"assigned_at":time.time(),"notes":str(body.get("notes") or "")[:500]}
            elif action=="set_main_ost":
                aid=str(body.get("id") or "")
                item=next((x for x in assets if str(x.get("id"))==aid),None)
                if not item: return _json(self,{"error":"Choose a valid music track for the main OST."},404)
                state["main_ost_id"]=aid
            else: return _json(self,{"error":"unknown audio action"},400)
            _save_world_state(state); _broadcast("audio_changed", {"active_audio":state.get("active_audio")}); return _json(self,{"ok":True,"audio_assets":assets,"active_audio":state.get("active_audio"),"main_ost_id":state.get("main_ost_id"),"theme_songs":(state.get("advanced") or {}).get("theme_songs",{})})
        if parsed.path == "/api/gm/economy":
            if session["role"]!="gm": return _json(self,{"error":"forbidden"},403)
            state=_load_world_state(); eco=state.setdefault("economy",{"currency_names":{"vg":"Vesperian Gold"},"balances":{},"prices":[]}); action=str(body.get("action") or "state")
            if action=="transfer":
                pid=str(body.get("player_id") or ""); currency=str(body.get("currency") or "vg"); amount=int(body.get("amount") or 0)
                if not pid: return _json(self,{"error":"player_id is required"},400)
                eco.setdefault("balances",{}).setdefault(pid,{})[currency]=int(eco.setdefault("balances",{}).setdefault(pid,{}).get(currency,0))+amount
            elif action=="price":
                eco.setdefault("prices",[]).append({"item":str(body.get("item") or ""),"currency":str(body.get("currency") or "vg"),"amount":max(0,int(body.get("amount") or 0)),"region":str(body.get("region") or "" )})
            _save_world_state(state); return _json(self,{"ok":True,"economy":eco})
        if parsed.path == "/api/gm/channel":
            if session["role"] != "gm": return _json(self, {"error":"forbidden"}, 403)
            channel_id = str(body.get("channel_id") or "")
            action = str(body.get("action") or "")
            if not channel_id or action not in {"lock", "unlock"}: return _json(self, {"error":"channel_id and lock/unlock action are required"}, 400)
            guild = _BOT.get_guild(int(GAME_GUILD_ID)) if _BOT else None
            channel = guild.get_channel(int(channel_id)) if guild and channel_id.isdigit() else None
            if channel is None: return _json(self, {"error":"channel not found"}, 404)
            async def change_permissions():
                await channel.set_permissions(guild.default_role, send_messages=(action == "unlock"))
            if not _BOT or not _BOT.loop.is_running(): return _json(self, {"error":"Discord bot loop is not running"}, 503)
            fut = concurrent.futures.Future()
            async def runner():
                try:
                    await change_permissions(); fut.set_result(True)
                except Exception as exc:
                    fut.set_exception(exc)
            _BOT.loop.call_soon_threadsafe(lambda: asyncio.create_task(runner()))
            try: fut.result(timeout=10)
            except Exception as exc: return _json(self, {"error":f"Discord channel update failed: {exc}"}, 503)
            campaign_store.audit_event(GAME_GUILD_ID, "gm_channel_permission", "web", actor_id=uid, actor_name=session["user"].get("username"), target_type="channel", target_id=channel_id, details={"action":action,"channel":channel.name})
            return _json(self, {"ok":True,"channel":{"id":channel_id,"name":channel.name,"locked":action=="lock"}})
        if parsed.path == "/api/gm/character":
            if session["role"] != "gm":
                return _json(self, {"error": "forbidden"}, 403)
            action = str(body.get("action") or "update")
            state = _load_world_state()
            records = state.setdefault("character_records", {})
            npcs = state.setdefault("npc_records", {})
            cid = str(body.get("id") or "")
            if action == "delete":
                if not cid:
                    return _json(self, {"error": "id is required"}, 400)
                records.pop(cid, None)
                npcs.pop(cid, None)
            else:
                if not cid:
                    cid = "char-" + uuid.uuid4().hex
                target_type = str(body.get("type") or "PLAYER").upper()
                store = npcs if target_type == "NPC" else records
                old = store.get(cid) or {"id": cid, "type": target_type}
                if target_type == "NPC" and not (body.get("image") or old.get("image")):
                    return _json(self, {"error": "NPCs require a portrait image so the GM can visually control and identify them."}, 400)
                for key in ("name", "age", "sex", "height", "weight", "description", "wears", "status", "location", "image", "timeline"):
                    if key in body:
                        old[key] = body.get(key)
                old["type"] = target_type
                if target_type == "PLAYER" and isinstance(body.get("aro"), dict):
                    aro = body["aro"]
                    if aro.get("aspect") not in ARO_ASPECTS:
                        return _json(self, {"error": "Every player character must have a valid Aro Aspect."}, 400)
                    previous_aro = old.get("aro") if isinstance(old.get("aro"), dict) else {}
                    old["aro"] = {
                        "aspect": aro["aspect"],
                        "growth": max(0, min(100, int(aro.get("growth", 0)))),
                        "color": str(aro.get("color") or previous_aro.get("color") or "blue"),
                        "awakened": bool(aro.get("awakened", previous_aro.get("awakened", False))),
                        "form": aro.get("form", previous_aro.get("form")),
                        "unlocked_abilities": list(aro.get("unlocked_abilities", previous_aro.get("unlocked_abilities", []))),
                    }
                store[cid] = old
            state["version"] = 3
            _save_world_state(state)
            campaign_store.audit_event(GAME_GUILD_ID, "gm_character_updated", "web", actor_id=uid, actor_name=session["user"].get("username"), target_type="character", target_id=cid, details={"action": action})
            return _json(self, {"ok": True, "state": state})

        if parsed.path == "/api/gm/chapter":
            if session["role"] != "gm":
                return _json(self, {"error": "forbidden"}, 403)
            name = str(body.get("name") or "").strip()
            if not name:
                return _json(self, {"error": "Chapter name is required."}, 400)
            state = _load_world_state()
            current = state.get("current_chapter") or {}
            new_id = "chapter-" + uuid.uuid4().hex
            state.setdefault("chapter_history", []).append(current)
            state["current_chapter"] = {"id": new_id, "name": name, "parent_id": current.get("id"), "created_at": time.time()}
            state["version"] = 3
            _save_world_state(state)
            campaign_store.audit_event(GAME_GUILD_ID, "gm_new_chapter", "web", actor_id=uid, actor_name=session["user"].get("username"), details={"name": name, "previous": current.get("name")})
            return _json(self, {"ok": True, "state": state})

        if parsed.path == "/api/gm/emergency":
            if session["role"] != "gm":
                return _json(self, {"error": "forbidden"}, 403)
            state = _load_world_state()
            emergency = state.setdefault("emergency", {})
            for key in ("player_lock", "hide_player_actions", "pause_session"):
                if key in body:
                    emergency[key] = bool(body[key])
            if emergency.get("pause_session"):
                state["session_live"] = False
            state["version"] = 3
            _save_world_state(state)
            _broadcast("world_state", {"state": state})
            _broadcast("session_changed", {"live": bool(state.get("session_live")), "paused": bool(emergency.get("pause_session"))})
            campaign_store.audit_event(GAME_GUILD_ID, "gm_emergency_control", "web", actor_id=uid, actor_name=session["user"].get("username"), details=emergency)
            return _json(self, {"ok": True, "state": state})

        if parsed.path == "/api/gm/event_queue":
            if session["role"] != "gm":
                return _json(self, {"error": "forbidden"}, 403)
            state = _load_world_state()
            queue = state.setdefault("event_queue", [])
            action = str(body.get("action") or "add")
            if action == "add":
                queue.append({
                    "id": "event-" + uuid.uuid4().hex,
                    "title": str(body.get("title") or "Untitled Event"),
                    "description": str(body.get("description") or ""),
                    "location": str(body.get("location") or ""),
                    "trigger": str(body.get("trigger") or "manual"),
                    "status": "queued",
                    "created_at": time.time(),
                })
            elif action == "remove":
                eid = str(body.get("id") or "")
                queue[:] = [x for x in queue if str(x.get("id")) != eid]
            elif action == "trigger":
                eid = str(body.get("id") or "")
                for x in queue:
                    if str(x.get("id")) == eid:
                        x["status"] = "triggered"
                        x["triggered_at"] = time.time()
                        break
            else:
                return _json(self, {"error": "unknown event queue action"}, 400)
            _save_world_state(state)
            return _json(self, {"ok": True, "state": state})

        if parsed.path == "/api/gm/state":
            if session["role"] != "gm":
                return _json(self, {"error": "forbidden"}, 403)
            incoming = body.get("state")
            if not isinstance(incoming, dict):
                return _json(self, {"error": "state must be an object"}, 400)
            current = _load_world_state()
            allowed = {"world_threat","region_danger","danger_enabled","danger_player_overrides","player_regions","regions","font_commands","session_live","story_locked","atmosphere","character_records","npc_records","chapter_history","current_chapter","session_history","event_queue","lore_connections","emergency","ooc_messages","dm_threads","groups","ideas","user_settings","gm_messages","campaign_notifications","companions","audio_assets","active_audio","main_ost_id","ability_catalog","custom_fonts","character_journals","character_inbox","web_items","economy","advanced","surprise_danger"}
            allowed.update({"campaign_name", "campaign_short_name"})
            for key in allowed:
                if key in incoming:
                    current[key] = incoming[key]
            current["version"] = 3
            _save_world_state(current)
            campaign_store.audit_event(GAME_GUILD_ID, "gm_world_state_updated", "web", actor_id=uid, actor_name=session["user"].get("username"), details={"keys": sorted(set(incoming) & allowed)})
            return _json(self, {"ok": True, "state": current})
        if parsed.path == "/api/gm/font":
            if session["role"] != "gm":
                return _json(self, {"error": "forbidden"}, 403)
            action = str(body.get("action") or "upload")
            state = _load_world_state()
            fonts = state.setdefault("custom_fonts", [])
            if action == "delete":
                fid = str(body.get("id") or "")
                item = next((x for x in fonts if str(x.get("id")) == fid), None)
                if item:
                    try: (Path(DATA_DIR) / "web_fonts" / Path(str(item.get("filename"))).name).unlink(missing_ok=True)
                    except Exception: pass
                fonts[:] = [x for x in fonts if str(x.get("id")) != fid]
            else:
                data = str(body.get("data") or "")
                name = re.sub(r"[^a-zA-Z0-9_. -]", "_", str(body.get("name") or "font"))[:80]
                if not data.startswith("data:font/") and not data.startswith("data:application/font"):
                    return _json(self, {"error": "Upload a TTF, OTF, WOFF, or WOFF2 font file."}, 400)
                if "," not in data:
                    return _json(self, {"error": "Invalid font upload."}, 400)
                raw = base64.b64decode(data.split(",",1)[1])
                if len(raw) > 10 * 1024 * 1024:
                    return _json(self, {"error": "Font files are limited to 10 MB."}, 413)
                folder = Path(DATA_DIR) / "web_fonts"; folder.mkdir(parents=True, exist_ok=True)
                ext = Path(name).suffix.lower() or ".woff2"
                filename = uuid.uuid4().hex + ext
                (folder / filename).write_bytes(raw)
                font_id = "font-" + uuid.uuid4().hex
                fonts.append({"id":font_id,"name":name,"filename":filename,"url":"/media/font/"+filename,"at":time.time()})
            _save_world_state(state)
            _broadcast("font_update", {"fonts": fonts})
            return _json(self, {"ok":True,"fonts":fonts})

        if parsed.path == "/api/gm/action":
            if session["role"] != "gm":
                return _json(self, {"error": "forbidden"}, 403)
            action = str(body.get("action") or "")
            current = _load_world_state()
            if action == "session":
                enabled = bool(body.get("enabled"))
                discord_warning = None
                # The website's session state is authoritative for the web client.
                # Do not make it depend on a Discord permission change, a slow DM,
                # or an unavailable Discord cache. Those are synced separately
                # below, so a GM can always start or end the web session.
                sd=current.setdefault("surprise_danger", {"enabled":True,"level":0,"target":"all","until":0,"next_trigger_at":0,"last_trigger_at":0})
                if enabled and sd.get("enabled"):
                    sd["next_trigger_at"]=time.time()+90
                current["session_live"] = enabled
                current.setdefault("session_history", []).append({"id":"session-"+uuid.uuid4().hex,"started":enabled,"at":time.time(),"gm_id":uid,"gm_name":session["user"].get("username"),"chapter_id":(current.get("current_chapter") or {}).get("id"),"chapter_name":(current.get("current_chapter") or {}).get("name")})
                current["session_history"] = current["session_history"][-200:]
                _save_world_state(current)
                _broadcast("session_changed", {"live":enabled})
                if _BOT and _BOT.loop.is_running():
                    guild = _BOT.get_guild(int(GAME_GUILD_ID))
                    if guild is not None:
                        async def session_runner():
                            try:
                                from .features import gm_tools
                                if enabled:
                                    await gm_tools._start_game_now(guild, int(GAME_CHANNEL_ID), started_by=int(uid), briefing={"today":"","title":CAMPAIGN_NAME,"player_ids":[],"check_in_minutes":int(current.get("check_in_minutes",5) or 5)})
                                else:
                                    await gm_tools._end_game_from_web(guild, int(uid))
                            except Exception as exc:
                                print(f"[web] Discord session sync warning: {type(exc).__name__}: {exc}")
                        _BOT.loop.call_soon_threadsafe(lambda: asyncio.create_task(session_runner()))
                    else:
                        discord_warning = "The web session changed, but the Discord guild is not currently available to the bot."
                else:
                    discord_warning = "The web session changed, but the Discord bot is not running."
            elif action == "story_lock":
                locked = bool(body.get("enabled"))
                current["story_locked"] = locked
                if _BOT and _BOT.loop.is_running():
                    guild = _BOT.get_guild(int(GAME_GUILD_ID))
                    if guild:
                        fut = concurrent.futures.Future()
                        async def lock_runner():
                            try:
                                from .features.gm_tools import _set_game_channel_lock
                                await _set_game_channel_lock(guild, locked, int(GAME_CHANNEL_ID))
                                fut.set_result(True)
                            except Exception as exc: fut.set_exception(exc)
                        _BOT.loop.call_soon_threadsafe(lambda: asyncio.create_task(lock_runner()))
                        try: fut.result(timeout=20)
                        except Exception as exc: return _json(self, {"error":f"Game channel lock failed: {exc}"}, 503)
                _broadcast("story_lock_changed", {"locked":locked})
            elif action == "move_player":
                uid_target = str(body.get("user_id") or "")
                region_id = str(body.get("region_id") or "")
                if not uid_target or not region_id:
                    return _json(self, {"error": "user_id and region_id are required"}, 400)
                current.setdefault("player_regions", {})[uid_target] = region_id
            elif action == "surprise_danger":
                sd=current.setdefault("surprise_danger", {"enabled":True,"level":0,"target":"all","until":0,"next_trigger_at":0,"last_trigger_at":0})
                sd["enabled"]=bool(body.get("enabled",True))
                if sd["enabled"] and not sd.get("next_trigger_at"): sd["next_trigger_at"]=time.time()+90
                if not sd["enabled"]: sd.update({"level":0,"until":0,"next_trigger_at":0})
                _broadcast("world_state", {"state":current})
            elif action == "danger":
                target_type = str(body.get("target_type") or "region")
                target_id = str(body.get("target_id") or "")
                enabled = bool(body.get("enabled", True))
                level = max(0, min(100, int(body.get("level", 0))))
                if target_type == "player":
                    current.setdefault("danger_player_overrides", {})[target_id] = {"enabled": enabled, "level": level}
                else:
                    current.setdefault("danger_enabled", {})[target_id] = enabled
                    current.setdefault("region_danger", {})[target_id] = level
            elif action == "font_commands":
                if not isinstance(body.get("commands"), list):
                    return _json(self, {"error": "commands must be a list"}, 400)
                current["font_commands"] = body["commands"]
            elif action == "broadcast":
                title=str(body.get("title") or "GM Broadcast").strip()[:120]
                text=str(body.get("text") or "").strip()[:1900]
                if not text: return _json(self,{"error":"Broadcast text is required."},400)
                created_at=time.time()
                item={"id":"broadcast-"+uuid.uuid4().hex,"name":title,"author_id":uid,"text":text,"kind":"gm","scope":"broadcast","channel_id":str(GAME_CHANNEL_ID),"created_at":created_at,"source":"gm-broadcast"}
                current.setdefault("gm_messages", []).append(item)
                current["gm_messages"] = current["gm_messages"][-500:]
                notice={"id":"notice-"+uuid.uuid4().hex,"title":title,"text":text,"created_at":created_at,"channel_id":str(GAME_CHANNEL_ID)}
                current.setdefault("campaign_notifications", []).append(notice)
                current["campaign_notifications"] = current["campaign_notifications"][-200:]
                try:
                    _send_webhook_to_channel(GAME_CHANNEL_ID,title,text)
                except Exception as exc:
                    # The in-app broadcast must remain usable while Discord is
                    # offline; return the warning after persisting it below.
                    discord_warning=str(exc)
                _broadcast("game_message", {"item":item})
                _broadcast("campaign_notification", {"item":notice})
            elif action == "web_item":
                inv=current.setdefault("web_items",[])
                sub=str(body.get("subaction") or "add")
                if sub=="remove": inv[:]=[x for x in inv if str(x.get("id"))!=str(body.get("id"))]
                else:
                    inv.append({"id":"item-"+uuid.uuid4().hex,"name":str(body.get("name") or "Unnamed Item"),"owner_id":str(body.get("owner_id") or ""),"rarity":str(body.get("rarity") or "Common"),"image":body.get("image") if isinstance(body.get("image"),str) else None,"created_at":time.time()})
            else:
                return _json(self, {"error": "unknown action"}, 400)
            current["version"] = 3
            _save_world_state(current)
            campaign_store.audit_event(GAME_GUILD_ID, "gm_action", "web", actor_id=uid, actor_name=session["user"].get("username"), details={"action": action})
            _broadcast("gm_action", {"action": action})
            return _json(self, {"ok": True, "state": current, "warning": discord_warning if action in {"session", "broadcast"} else None})
        if parsed.path == "/api/channels":
            session = _session_from_request(self)
            if not session:
                return _json(self, {"error": "unauthorized"}, 401)
            guild = _BOT.get_guild(int(GAME_GUILD_ID)) if _BOT else None
            items=[]
            if guild:
                for ch in sorted(getattr(guild, "channels", []) or [], key=lambda x: (getattr(x, "position", 0), getattr(x, "id", 0))):
                    typ=str(getattr(ch, "type", "")).lower()
                    if typ not in {"text", "announcement", "news", "public_thread", "private_thread", "thread"}:
                        continue
                    me=getattr(guild, "me", None)
                    if me:
                        try:
                            perms=ch.permissions_for(me)
                            if not perms.view_channel or not perms.read_message_history:
                                continue
                        except Exception:
                            pass
                    items.append({"id":str(ch.id),"name":getattr(ch,"name","channel"),"category":getattr(getattr(ch,"category",None),"name",None),"type":typ,"is_game":str(ch.id)==str(GAME_CHANNEL_ID),"is_general":str(ch.id)==str(GENERAL_CHANNEL_ID)})
            return _json(self, {"items":items})
        if parsed.path == "/api/channel-messages":
            session = _session_from_request(self)
            if not session:
                return _json(self, {"error": "unauthorized"}, 401)
            params=urllib.parse.parse_qs(parsed.query)
            channel_id=str(params.get("channel_id", [GAME_CHANNEL_ID])[0])
            limit=max(1,min(int(params.get("limit", [150])[0]),500))
            guild = _BOT.get_guild(int(GAME_GUILD_ID)) if _BOT else None
            channel = guild.get_channel(int(channel_id)) if guild and channel_id.isdigit() else None
            if not channel:
                return _json(self,{"error":"channel_not_found"},404)
            me=getattr(guild,"me",None)
            if me:
                try:
                    perms=channel.permissions_for(me)
                    if not perms.view_channel or not perms.read_message_history:
                        return _json(self,{"error":"forbidden"},403)
                except Exception:
                    pass
            live_items=_discord_channel_history(channel_id, limit)
            items=_messages(limit, channel_id)
            # Prefer the live Discord archive for channel content, then merge any
            # locally-created web messages that belong to the selected channel.
            merged={str(x.get("id")):x for x in items if x.get("id")}
            for x in live_items: merged[str(x.get("id"))]=x
            items=list(merged.values())
            items.sort(key=lambda x:str(x.get("created_at") or x.get("at") or ""))
            # GM-only web messages are never exposed through public channel views.
            items=[x for x in items if x.get("scope") != "gmchat" and not x.get("private")]
            state=_load_world_state()
            items += [x for x in (state.get("gm_messages") or []) if x.get("scope")=="broadcast" and str(x.get("channel_id") or GAME_CHANNEL_ID)==channel_id][-limit:]
            if session["role"]=="gm":
                items += [x for x in (state.get("gm_messages") or []) if x.get("scope") not in {"gmchat","broadcast"} and str(x.get("channel_id") or channel_id)==channel_id][-limit:]
                items.sort(key=lambda x:str(x.get("created_at") or x.get("at") or ""))
            return _json(self,{"items":items[-limit:],"channel_id":channel_id})
        if parsed.path == "/api/messages":
            state = _load_world_state()
            if session["role"] != "gm" and (not state.get("session_live") or (state.get("emergency") or {}).get("player_lock")):
                return _json(self, {"error": "The session is currently closed to players."}, 423)
            text = str(body.get("text") or "").strip()
            has_image = isinstance(body.get("image"), str) and body.get("image", "").startswith("data:image/") and "," in body.get("image", "")
            if (not text and not has_image) or len(text) > 2000:
                return _json(self, {"error":"Send a message or an image (message text is limited to 2,000 characters)."}, 400)
            char = _character_for(uid)
            if not char["name"] and session["role"] == "gm":
                char = {**char, "name": str(session["user"].get("username") or "Game Master")}
            if not char["name"]:
                return _json(self, {"error":"No active character is assigned to your Discord account."}, 409)
            advanced_state = state.setdefault("advanced", {})
            char_state = (advanced_state.get("character_states") or {}).get(uid) or {"state":"normal","enabled":False}
            if session["role"] != "gm" and char_state.get("enabled"):
                cstate = str(char_state.get("state") or "normal").lower()
                if cstate in {"silenced","unconscious","stunned","restrained"}:
                    return _json(self,{"error":f"Your character is currently {cstate} and cannot type."},423)
                limit = int(char_state.get("limit") or 0)
                if cstate in {"panic","fear"} and limit and len(text)>limit:
                    return _json(self,{"error":f"Your current state limits messages to {limit} characters."},423)
                if cstate == "drunk":
                    words=text.split()
                    for i in range(0,len(words),4):
                        if words[i]: words[i]=words[i][::-1] if len(words[i])>4 else words[i]
                    text=" ".join(words)
            personas = (advanced_state.get("personas") or {}).get(uid) or []
            persona_id = str(body.get("persona_id") or "")
            persona = next((x for x in personas if str(x.get("id"))==persona_id), None)
            override = (advanced_state.get("persona_overrides") or {}).get(uid)
            display_name = str((persona or {}).get("name") or char.get("name") or session["user"].get("username") or uid)
            if session["role"] != "gm" and override:
                display_name = str((override or {}).get("name") if isinstance(override,dict) else override) or display_name
            visibility = str(body.get("visibility") or "public")
            image = body.get("image")
            channel_id = str(body.get("channel_id") or GAME_CHANNEL_ID)
            if not channel_id.isdigit():
                return _json(self, {"error": "Invalid Discord channel."}, 400)
            audience_ids = [str(x) for x in (body.get("audience_ids") or []) if str(x).strip()] if session["role"] == "gm" else []
            if visibility == "gm":
                if session["role"] != "gm":
                    return _json(self, {"error":"GM-only messages are restricted to GMs."}, 403)
                state = _load_world_state()
                item = {"id":"web-gm-"+uuid.uuid4().hex,"name":display_name,"text":text or "(image)","kind":"gm","private":True,"image":image if isinstance(image,str) and len(image)<2_500_000 else None,"author_id":uid,"created_at":__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),"channel_id":channel_id}
                state.setdefault("gm_messages", []).append(item)
                state["gm_messages"] = state["gm_messages"][-300:]
                _save_world_state(state)
                campaign_store.audit_event(GAME_GUILD_ID, "gm_private_web_message", "web", actor_id=uid, actor_name=session["user"].get("username"), details={"message_id":item["id"]})
                return _json(self, {"ok":True,"message":item})
            if audience_ids:
                item={"id":"web-pov-"+uuid.uuid4().hex,"name":display_name,"text":text,"kind":"gm","scope":"pov","audience_ids":audience_ids,"image":image if isinstance(image,str) and len(image)<2_500_000 else None,"author_id":uid,"created_at":__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),"channel_id":channel_id}
                state=_load_world_state(); state.setdefault("gm_messages",[]).append(item); state["gm_messages"]=state["gm_messages"][-500:]; _save_world_state(state); return _json(self,{"ok":True,"message":item})
            # Optional automatic ambience state detection. It never runs unless the GM enables it.
            adv = state.setdefault("advanced", {}); amb = adv.setdefault("ambience", {})
            low_text = text.casefold()
            if session["role"] == "gm" and amb.get("enabled") and (amb.get("auto_battle") or amb.get("auto_funny")):
                next_state = None
                if amb.get("auto_battle"):
                    if re.search(r"\b(combat|battle|fighting|fight|attacks?|strikes?|charges?|draws? (?:a|his|her|their) (?:sword|weapon)|enemy|boss)\b", low_text):
                        next_state = "action"
                    elif re.search(r"\b(combat|battle|fight) (?:ends?|over)|\b(?:safe|peaceful|resting|stands down)\b", low_text):
                        next_state = "calm"
                if amb.get("auto_funny") and re.search(r"\b(lmao|lol|bonk|falls? over|slips?|faceplants?|stupid|hilarious|what the hell|clown)\b", low_text):
                    next_state = "funny"
                if re.search(r"\b(died|dead|death|passed away|funeral|grief|mourning|heartbroken|lost (?:his|her|their|my) (?:dog|friend|family|mother|father))\b", low_text):
                    next_state = "sad"
                elif re.search(r"\b(killing|murder|massacre|slaughter|bloodshed|one by one|execution|corpses?|evil|ominous|betrayal)\b", low_text):
                    next_state = "dark"
                elif re.search(r"\b(horror|terrified|screaming|haunted|monster|nightmare|lurking|fear)\b", low_text):
                    next_state = "scary"
                if next_state and next_state != amb.get("state"):
                    # _apply_ai_music_mood chooses a valid track from the
                    # category; clients then advance through that category.
                    _apply_ai_music_mood(GAME_GUILD_ID, next_state)
            try:
                sent = _send_webhook_to_channel(channel_id, display_name, text, _discord_avatar_url(session["user"]), image)
            except Exception as exc:
                return _json(self, {"error": str(exc)}, 503)
            mid = str(getattr(sent, "id", None) or (sent.get("id") if isinstance(sent, dict) else None) or f"web-{uuid.uuid4().hex}")
            created = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
            with campaign_store._LOCK, campaign_store._connect() as db:
                db.execute("INSERT OR REPLACE INTO messages(message_id,guild_id,channel_id,author_id,author_name,content,jump_url,created_at,is_priority) VALUES(?,?,?,?,?,?,?,?,?)",
                           (mid, str(GAME_GUILD_ID), channel_id, uid, char["name"], text, "", created, 0))
            out_item={"id": mid, "name": display_name, "text": text, "kind":"public", "persona_id": persona_id or None, "created_at": created, "author_id": uid, "channel_id": channel_id}
            _broadcast("game_message", {"item": out_item})
            # Webhook messages are ignored by the Discord listener to avoid
            # loops, so explicitly send this browser-originated RP to the
            # optional AI lore collector without delaying the response.
            _queue_web_lore_analysis(mid, channel_id, uid, display_name, text, created)
            campaign_store.audit_event(GAME_GUILD_ID, "web_message_sent", "web", actor_id=uid, actor_name=session["user"].get("username"), target_type="message", target_id=mid, details={"character": char["name"]})
            return _json(self, {"ok": True, "message": out_item})
        if parsed.path == "/api/messages/edit":
            mid = str(body.get("id") or "")
            text = str(body.get("text") or "").strip()
            state = _load_world_state()
            for gm_item in state.get("gm_messages") or []:
                if str(gm_item.get("id")) == mid:
                    if session["role"] != "gm": return _json(self, {"error":"forbidden"}, 403)
                    gm_item["text"] = text
                    _save_world_state(state)
                    return _json(self, {"ok":True})
            row = _find_message(mid)
            if not row: return _json(self, {"error":"not_found"}, 404)
            if not text or len(text) > 2000: return _json(self, {"error":"message must be 1-2000 characters"}, 400)
            owner = str(row.get("author_id") or "")
            is_web = not bool(row.get("jump_url"))
            if not is_web or (session["role"] != "gm" and owner != uid):
                return _json(self, {"error":"editing this message is not supported"}, 403)
            # Webhook messages can be edited through their webhook URL.
            try:
                url = _webhook_url()
                if url:
                    hook = url.split("/webhooks/", 1)[1]
                    wid, token = hook.split("/", 1)
                    edit_url = f"https://discord.com/api/webhooks/{wid}/{token}/messages/{mid}"
                    req = urllib.request.Request(edit_url, data=json.dumps({"content": text}).encode(), headers={"Content-Type":"application/json"}, method="PATCH")
                    with urllib.request.urlopen(req, timeout=15): pass
            except Exception as exc:
                print(f"[web] edit warning: {type(exc).__name__}: {exc}")
            with campaign_store._LOCK, campaign_store._connect() as db:
                db.execute("UPDATE messages SET content=? WHERE guild_id=? AND message_id=?", (text, str(GAME_GUILD_ID), mid))
            campaign_store.audit_event(GAME_GUILD_ID, "web_message_edited", "web", actor_id=uid, target_type="message", target_id=mid)
            return _json(self, {"ok": True})
        if parsed.path == "/api/messages/delete":
            mid = str(body.get("id") or "")
            state = _load_world_state()
            gm_items = state.get("gm_messages") or []
            if any(str(x.get("id")) == mid for x in gm_items):
                if session["role"] != "gm": return _json(self, {"error":"forbidden"}, 403)
                state["gm_messages"] = [x for x in gm_items if str(x.get("id")) != mid]
                _save_world_state(state)
                return _json(self, {"ok":True})
            row = _find_message(mid)
            if not row: return _json(self, {"error":"not_found"}, 404)
            owner = str(row.get("author_id") or "")
            is_web = not bool(row.get("jump_url"))
            if session["role"] != "gm" and (not is_web or owner != uid):
                return _json(self, {"error":"forbidden"}, 403)
            channel = _BOT.get_channel(int(GAME_CHANNEL_ID)) if _BOT else None
            try:
                if channel:
                    msg = awaitable_fetch(channel, int(mid))
                    if msg is not None:
                        _BOT.loop.create_task(msg.delete())
            except Exception as exc:
                print(f"[web] delete warning: {type(exc).__name__}: {exc}")
            with campaign_store._LOCK, campaign_store._connect() as db:
                db.execute("DELETE FROM messages WHERE guild_id=? AND message_id=?", (str(GAME_GUILD_ID), mid))
            campaign_store.audit_event(GAME_GUILD_ID, "web_message_deleted", "web", actor_id=uid, target_type="message", target_id=mid, details={"gm": session["role"] == "gm"})
            return _json(self, {"ok": True})
        return _json(self, {"error":"not_found"}, 404)


def awaitable_fetch(channel, mid):
    # Schedule an async Discord fetch without blocking the bot's event loop.
    fut = concurrent.futures.Future()
    async def runner():
        try: fut.set_result(await channel.fetch_message(mid))
        except Exception: fut.set_result(None)
    _BOT.loop.call_soon_threadsafe(lambda: asyncio.create_task(runner()))
    try: return fut.result(timeout=5)
    except Exception: return None


_SURPRISE_THREAD=None
_SURPRISE_STOP=threading.Event()

def _surprise_danger_loop():
    import random
    while not _SURPRISE_STOP.wait(5):
        try:
            state=_load_world_state(); sd=state.setdefault("surprise_danger", {"enabled":True,"level":0,"target":"all","until":0,"next_trigger_at":0,"last_trigger_at":0}); now=time.time()
            if not state.get("session_live") or not sd.get("enabled"):
                if sd.get("level") or sd.get("until"):
                    sd.update({"level":0,"until":0}); _save_world_state(state); _broadcast("world_state", {"state":state})
                continue
            if sd.get("until",0) and now>=float(sd.get("until",0)):
                sd["level"]=0; sd["until"]=0; sd["next_trigger_at"]=now+random.uniform(75,210); _save_world_state(state); _broadcast("world_state", {"state":state})
            if not sd.get("next_trigger_at"):
                sd["next_trigger_at"]=now+random.uniform(60,150); _save_world_state(state)
            elif now>=float(sd.get("next_trigger_at",0)) and not sd.get("level"):
                roll=random.random(); level=random.randint(28,48) if roll<.60 else (random.randint(49,78) if roll<.90 else random.randint(79,100)); duration=random.uniform(8,24)
                sd.update({"level":level,"until":now+duration,"last_trigger_at":now,"next_trigger_at":now+duration+random.uniform(75,210)}); _save_world_state(state); _broadcast("world_state", {"state":state})
        except Exception as exc:
            print(f"[web] surprise danger warning: {type(exc).__name__}: {exc}")

def start(bot=None):
    global _SERVER, _THREAD, _BOT, _SURPRISE_THREAD
    _BOT = bot
    if _SERVER is not None: return
    if not WEB_SESSION_SECRET:
        raise RuntimeError("WEB_SESSION_SECRET/ANONYMOUS_SESSION_SECRET is required for the web client.")
    _index_server_music_library()
    _SERVER = http.server.ThreadingHTTPServer((WEB_HOST, int(WEB_PORT)), Handler)
    _THREAD = threading.Thread(target=_SERVER.serve_forever, name="anonymous-web", daemon=True)
    _THREAD.start()
    _SURPRISE_STOP.clear()
    _SURPRISE_THREAD=threading.Thread(target=_surprise_danger_loop,name="anonymous-surprise-danger",daemon=True)
    _SURPRISE_THREAD.start()
    print(f"Web client online at http://{WEB_HOST}:{WEB_PORT} (public URL: {ANONYMOUS_APP_URL or 'configure ANONYMOUS_APP_URL'})")


def stop():
    global _SERVER, _THREAD, _SURPRISE_THREAD
    _SURPRISE_STOP.set()
    if _SERVER:
        _SERVER.shutdown(); _SERVER.server_close()
    _SERVER = None; _THREAD = None; _SURPRISE_THREAD = None
