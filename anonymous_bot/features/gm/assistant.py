"""Multi-provider AI-powered GM assistant.

The AI does not write or decide campaign story. It translates GM shorthand into
safe operations over the bot's existing campaign state, and provides GM-only
search/help. Story prose remains authored by the GM and is indexed separately.
"""
import asyncio
import json
import re
import uuid
from datetime import datetime, timezone

import discord
from discord import app_commands

from ...state import is_staff
from ..items import item_state, save_item_data, add_item, resolve_base_item, compact_item, _fresh_instance_id
from ..memory import _ai_enabled, _ai_sync_analyze, _parse_ai_json, _store, _now, _best_lore, _gemini_lore_answer
from ..economy import balance, set_balance

# Only Discord administrators and this explicit GM account may use G:.
GM_COMMAND_USER_ID = 1388446131620548760


def _can_use_g(message):
    """Return True only for Discord admins or the explicit GM account."""
    if message.guild is None or message.author.bot:
        return False
    if int(getattr(message.author, "id", 0) or 0) == GM_COMMAND_USER_ID:
        return True
    perms = getattr(message.author, "guild_permissions", None)
    return bool(perms and getattr(perms, "administrator", False))


async def _delete_g_message(message):
    """Keep G: commands out of the public channel after they are processed."""
    try:
        await message.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


def _member(guild, value):
    value = str(value or "").strip()
    if not value:
        return None
    m = re.fullmatch(r"<@!?(\d+)>", value)
    if m:
        value = m.group(1)
    if value.isdigit():
        return guild.get_member(int(value))
    low = value.casefold()
    exact = next((m for m in guild.members if m.display_name.casefold() == low or m.name.casefold() == low), None)
    if exact:
        return exact
    return next((m for m in guild.members if low in m.display_name.casefold() or low in m.name.casefold()), None)


def _gm_state(guild_id):
    st = item_state(guild_id)
    gm = st.setdefault("gm_tools", {})
    gm.setdefault("gm_ai_log", [])
    gm.setdefault("gm_pending_actions", [])
    gm.setdefault("story_index", [])
    return gm


def _action_summary(a):
    kind = a.get("action", "unknown")
    if kind == "give_vg":
        return f"Give {a.get('amount', 0):,} VG to {a.get('target')}"
    if kind == "give_item":
        return f"Give {a.get('item')} to {a.get('target')}"
    if kind == "session_event":
        return f"Record session event: {a.get('event')}"
    if kind == "memory_add":
        return f"Save campaign memory: {a.get('title')}"
    if kind == "reputation_add":
        return f"Change {a.get('target')}'s reputation with {a.get('faction')} by {a.get('amount')}"
    if kind == "underworld_send":
        return f"Send {a.get('target')} to HELL (Underworld)"
    if kind == "revive":
        return f"Revive {a.get('target')} and remove them from HELL"
    if kind == "hell_event":
        return f"Post HELL event: {a.get('event')}"
    if kind in {"story_secret_channel", "create_channel"}:
        users = a.get("users") or []
        return f"Create private story channel #{a.get('name')} for {', '.join(map(str, users))}"
    if kind == "lock":
        return "Lock the server"
    if kind == "unlock":
        return "Unlock the server"
    if kind == "search":
        return f"Search campaign database for: {a.get('query')}"
    if kind == "lore_answer":
        return f"Explain campaign lore: {a.get('query')}"
    return str(a)


def _player_context(guild):
    return [
        {"id": m.id, "name": m.display_name}
        for m in guild.members if not m.bot
    ][:100]



def _gm_lore_context(guild_id, query):
    """Build a compact GM-only evidence packet for G:."""
    mem = _store(guild_id)
    try:
        relevant = _best_lore(guild_id, query, include_gm=True)
    except Exception:
        relevant = []
    parts = []
    seen = set()
    for _, kind, row in relevant[:14]:
        key = (kind, str(row.get("message_id") or row.get("id") or ""))
        if key in seen:
            continue
        seen.add(key)
        if kind == "record":
            text = row.get("description") or ""
            if row.get("gm_notes"):
                text += f" | GM notes: {row.get('gm_notes')}"
            label = f"CANON {row.get('type','lore')}: {row.get('name','Untitled')}"
        elif kind == "fact":
            text = row.get("text") or ""
            label = "RECORDED FACT / EVIDENCE"
        else:
            text = row.get("content") or ""
            label = "ARCHIVED EVIDENCE"
        text = re.sub(r"\s+", " ", str(text)).strip()[:500]
        if text:
            parts.append(f"- {label}: {text}")
    return "\n".join(parts)[:6500] if parts else "No relevant persistent lore was found."


def _prompt(guild, text, live_session, state):
    """Create a small G: routing prompt instead of dumping the whole command catalog."""
    commands = """You are G:, a GM-only command router for a Discord RPG.
Return ONLY JSON: {"reply":"...","actions":[],"requires_confirmation":false,"clarification":null}.
Never invent campaign facts or pretend an unsupported action executed.

SUPPORTED ACTIONS:
- give_vg {target, amount}
- give_item {target, item, description?, category?, rarity?}
- session_event {event, category?}
- memory_add {title, type, description, visibility?}
- reputation_add {target, faction, amount}
- underworld_send {target, reason?}
- revive {target}
- hell_event {event}
- story_secret_channel {name, users:[]}
- create_channel {name, users:[], topic?}
- lock {}
- unlock {}
- search {query}
- lore_answer {query}
- help {query}

ROUTING:
"send X to hell" / "condemn X" -> underworld_send.
"revive X" / "bring X out of hell" -> revive and execute it.
"give X 500 VG" -> give_vg; "give X ITEM" -> give_item.
"remember for campaign: ..." -> memory_add.
"add this to the session log: ..." -> session_event.
"lock/unlock" -> corresponding action.
"what do we know about X?" / "explain X" / "tell me the lore on X" -> lore_answer.
"search for X" -> search.
For story/private channel requests, use story_secret_channel when clearly a story secret-channel; otherwise create_channel.

IMPORTANT:
- The GM is the author. Never invent story events, consequences, dialogue, lore, quests, or player decisions.
- Use the current GM INPUT only; never reuse a prior request.
- Explicit single-target actions execute directly; do not require confirmation unless broad/destructive or explicitly requested.
- Ask for clarification only when a required target/amount/item/faction is genuinely missing or ambiguous.
- For lore questions, synthesize the supplied evidence; do not dump source messages.
- If no supported action fits, explain briefly rather than inventing one.
"""
    lore_context = _gm_lore_context(guild.id, text)
    members_text = json.dumps(_player_context(guild)[:60], ensure_ascii=False)
    return f"""{commands}
PLAYERS: {members_text}
LIVE SESSION: {live_session}
STATE: {json.dumps(state, ensure_ascii=False)[:3500]}
LORE EVIDENCE:
{lore_context}
GM INPUT: {text[:1800]}"""


def _state_summary(guild_id):
    st = item_state(guild_id)
    gm = st.get("gm_tools", {})
    return {
        "session_number": gm.get("session_number", 0),
        "game_started": gm.get("game_started", False),
        "session_title": (gm.get("current_session") or {}).get("title", ""),
        "bounties": len(gm.get("bounties", [])),
        "campaign_memories": len(st.get("campaign_memory", {}).get("records", [])),
        "known_items": len(st.get("possessions", {})),
        "factions": list((st.get("economy", {}).get("factions") or {}).keys())[:40],
    }


async def _execute(guild, actor, actions):
    results = []
    for action in actions:
        # Gemini may return the action discriminator as either `action` or
        # `type`. Normalize both forms so the executor actually performs the
        # requested bot operation instead of reporting it as unsupported.
        if isinstance(action, dict) and not action.get("action") and action.get("type"):
            action = dict(action)
            action["action"] = action.get("type")
        kind = str(action.get("action", "")).lower().strip() if isinstance(action, dict) else ""
        if kind == "give_vg":
            target = _member(guild, action.get("target"))
            amount = int(action.get("amount", 0) or 0)
            if not target or target.bot or amount <= 0:
                results.append("give_vg failed: valid player and positive amount required")
                continue
            set_balance(guild.id, target.id, balance(guild.id, target.id) + amount)
            results.append(f"Gave {amount:,} VG to {target.display_name}.")

        elif kind == "give_item":
            target = _member(guild, action.get("target"))
            if not target or target.bot:
                results.append("give_item failed: valid player required")
                continue
            name = str(action.get("item") or "Untitled Item").strip()[:100]
            base = resolve_base_item(name)
            if base:
                item = dict(base)
            else:
                item = {
                    "id": f"gm-item-{uuid.uuid4().hex[:10]}",
                    "name": name,
                    "base_name": name,
                    "category": str(action.get("category") or "item")[:30],
                    "rarity": str(action.get("rarity") or "Custom")[:40],
                    "description": str(action.get("description") or "GM-created campaign item.")[:1000],
                    "custom_template": True,
                }
            item = compact_item(item)
            # A fresh physical instance is required when a catalog item is granted.
            item["id"] = _fresh_instance_id(item_state(guild.id))
            item["instance_id"] = item["id"]
            if add_item(guild.id, target.id, item, held=True, given_by_gm=actor.id) is None:
                results.append(f"Could not give {name}; the item instance already exists.")
            else:
                results.append(f"Gave {name} to {target.display_name}.")

        elif kind == "session_event":
            gm = _gm_state(guild.id)
            if not gm.get("game_started"):
                results.append("session_event failed: there is no live game session")
                continue
            event = str(action.get("event") or "").strip()[:1000]
            if not event:
                results.append("session_event failed: empty event")
                continue
            gm.setdefault("session_events", []).append({
                "event": event,
                "category": str(action.get("category") or "General")[:80],
                "at": _now(),
                "by": actor.id,
                "source": "ai_gm",
            })
            results.append("Session event recorded.")

        elif kind == "memory_add":
            mem = _store(guild.id)
            record = {
                "id": f"gm-memory-{uuid.uuid4().hex[:10]}",
                "name": str(action.get("title") or "Untitled memory")[:100],
                "type": str(action.get("type") or "event")[:40],
                "visibility": "gm_only" if str(action.get("visibility", "canon")).lower() == "gm_only" else "canon",
                "description": str(action.get("description") or "")[:2000],
                "gm_notes": "Recorded by GM through G:.",
                "guild_id": guild.id,
                "created_by": actor.id,
                "created_at": _now(),
                "ai_generated": False,
                "source": "gm_g_command",
            }
            mem["records"].append(record)
            mem["records"] = mem["records"][-3000:]
            results.append(f"Saved campaign memory: {record['name']}.")

        elif kind == "reputation_add":
            target = _member(guild, action.get("target"))
            faction = str(action.get("faction") or "").strip()[:80]
            amount = int(action.get("amount", 0) or 0)
            if not target or not faction:
                results.append("reputation_add failed: valid player and faction required")
                continue
            gm = _gm_state(guild.id)
            rep = gm.setdefault("reputation", {})
            user = rep.setdefault(str(target.id), {})
            user[faction] = int(user.get(faction, 0) or 0) + amount
            results.append(f"Changed {target.display_name}'s {faction} reputation by {amount:+d}.")

        elif kind == "underworld_send":
            target = _member(guild, action.get("target"))
            if not target or target.bot:
                results.append("underworld_send failed: valid player required")
                continue
            from ..hell import _ensure_channel, _hell_member_overwrite, _underworld_mark, _start_hell_atmosphere
            channel = await _ensure_channel(guild)
            if channel is None:
                results.append("underworld_send failed: could not access HELL")
                continue
            await _hell_member_overwrite(channel, target, enabled=True)
            _underworld_mark(guild.id, target.id, str(action.get("reason") or "GM underworld condemnation")[:500])
            results.append(f"Sent {target.display_name} to HELL (Underworld).")

        elif kind == "revive":
            target = _member(guild, action.get("target"))
            if not target or target.bot:
                results.append("revive failed: valid player required")
                continue
            from ..hell import _revive_player
            ok, message = await _revive_player(guild, target)
            results.append(message if ok else f"revive failed: {message}")

        elif kind == "story_secret_channel":
            # This is the actual /story secret-channel behavior. G: should
            # perform the same operation for the GM instead of telling them
            # to run the slash command manually.
            name = re.sub(r"[^a-z0-9-]", "-", str(action.get("name") or "secret-channel").strip().casefold())[:90].strip("-")
            if not name:
                name = "secret-channel"
            raw_users = action.get("users") or []
            if not isinstance(raw_users, list):
                raw_users = [raw_users]
            members = []
            missing = []
            for value in raw_users:
                member = _member(guild, value)
                if member and not member.bot:
                    members.append(member)
                else:
                    missing.append(str(value))
            # /story secret-channel always includes the creator.
            if actor and not actor.bot:
                members.append(actor)
            members = list(dict.fromkeys(members))
            if not members:
                results.append("story_secret_channel failed: at least one valid player is required")
                continue
            if not guild.me or not guild.me.guild_permissions.manage_channels:
                results.append("story_secret_channel failed: bot needs Manage Channels permission")
                continue
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True),
            }
            for member in members:
                overwrites[member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            try:
                channel = await guild.create_text_channel(
                    name,
                    overwrites=overwrites,
                    reason=f"RPG /story secret-channel via G: by {actor}",
                )
                # Keep the same in-memory registry used by /story secret-channel.
                try:
                    from ..rpg import secret_channels
                    secret_channels[channel.id] = {member.id for member in members}
                except Exception:
                    pass
                msg = f"Created secret story channel {channel.mention} for {', '.join(m.display_name for m in members if m.id != actor.id)}."
                if missing:
                    msg += f" Could not find: {', '.join(missing[:10])}."
                results.append(msg)
            except discord.Forbidden:
                results.append("story_secret_channel failed: bot needs Manage Channels permission")
            except discord.HTTPException as exc:
                results.append(f"story_secret_channel failed: Discord rejected the channel creation ({exc}).")

        elif kind == "create_channel":
            name = re.sub(r"[^a-z0-9-]", "-", str(action.get("name") or "").strip().casefold())[:90].strip("-")
            if not name:
                results.append("create_channel failed: channel name required")
                continue
            raw_users = action.get("users") or []
            if not isinstance(raw_users, list):
                raw_users = [raw_users]
            members = []
            missing = []
            for value in raw_users:
                member = _member(guild, value)
                if member and not member.bot:
                    members.append(member)
                else:
                    missing.append(str(value))
            if missing:
                results.append(f"create_channel failed: could not find {', '.join(missing)}")
                continue
            if not members:
                results.append("create_channel failed: at least one allowed player is required")
                continue
            if not guild.me or not guild.me.guild_permissions.manage_channels:
                results.append("create_channel failed: bot needs Manage Channels permission")
                continue
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                actor: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            }
            for member in members:
                overwrites[member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
            channel = await guild.create_text_channel(
                name=name,
                topic=str(action.get("topic") or "").strip()[:1024] or None,
                overwrites=overwrites,
                reason=f"GM G: created private channel by {actor.display_name}",
            )
            results.append(f"Created private channel {channel.mention} for {', '.join(m.display_name for m in members)}.")

        elif kind == "hell_event":
            event = str(action.get("event") or "").strip()
            if not event:
                results.append("hell_event failed: event text required")
                continue
            from ..hell import _ensure_channel, _send_bold_scene, _start_hell_atmosphere
            channel = await _ensure_channel(guild)
            if channel is None:
                results.append("hell_event failed: could not access HELL")
                continue
            await _send_bold_scene(channel, event, allowed_mentions=discord.AllowedMentions.none())
            # Atmosphere is optional; the event itself is the GM-controlled action.
            results.append("HELL event posted.")

        elif kind in {"lock", "unlock"}:
            # The existing lock helper lives in gm_tools; import lazily to avoid a cycle.
            from ..gm_tools import _set_server_lock
            await _set_server_lock(guild, kind == "lock")
            results.append("Server locked." if kind == "lock" else "Server unlocked.")

        elif kind == "lore_answer":
            query = str(action.get("query") or "").strip()
            if not query:
                results.append("lore_answer failed: question required")
                continue
            answer = await _gemini_lore_answer(guild.id, query, actor.id, include_gm=True)
            if answer:
                results.append("LORE_ANSWER:" + answer[:3400])
            else:
                results.append("lore_answer failed: the lore AI could not synthesize an answer right now")

        elif kind in {"search", "help"}:
            results.append("search_pending")

        else:
            results.append(f"Unsupported G: action: {kind}")

    save_item_data()
    return results


async def _search(guild_id, query):
    query = str(query or "").strip()
    if not query:
        return "Tell me what you want to search for."
    rows = _best_lore(guild_id, query, include_gm=True)
    if not rows:
        return "No matching campaign records were found."
    lines = []
    for _, kind, row in rows[:8]:
        text = row.get("description") or row.get("content") or row.get("text") or ""
        lines.append(f"**{row.get('name') or kind}** — {text[:500]}")
    return "\n".join(lines)[:3500]


def _fast_route(text):
    """Handle common G: operations without an AI round trip."""
    raw = re.sub(r"\s+", " ", str(text or "").strip())
    low = raw.casefold()
    if low in {"lock", "lock the server", "lock the game"}:
        return [{"action": "lock"}]
    if low in {"unlock", "unlock the server", "unlock the game"}:
        return [{"action": "unlock"}]
    m = re.match(r"revive\s+(.+?)(?:\s+(?:back|out of hell|from hell))?$", raw, re.I)
    if m:
        target = re.sub(r"\s+(?:back|out of hell|from hell)$", "", m.group(1), flags=re.I).strip()
        return [{"action": "revive", "target": target}]
    m = re.match(r"(?:send|put|condemn)\s+(.+?)\s+(?:to|into)\s+(?:the\s+)?(?:hell|underworld)$", raw, re.I)
    if m:
        return [{"action": "underworld_send", "target": m.group(1).strip()}]
    m = re.match(r"(?:give|pay)\s+(.+?)\s+([0-9][0-9,]*)\s*(?:vg|currency)?$", raw, re.I)
    if m:
        return [{"action": "give_vg", "target": m.group(1).strip(), "amount": int(m.group(2).replace(",", ""))}]
    m = re.match(r"(?:remember for campaign|remember):\s*(.+)$", raw, re.I)
    if m:
        body = m.group(1).strip()
        return [{"action": "memory_add", "title": body[:100], "type": "lore", "description": body[:1800], "visibility": "canon"}]
    m = re.match(r"(?:add this to the session log|session log):\s*(.+)$", raw, re.I)
    if m:
        return [{"action": "session_event", "event": m.group(1).strip()[:1800]}]
    m = re.match(r"(?:what do we know about|tell me about|explain|tell me the lore on|lore on)\s+(.+?)\??$", raw, re.I)
    if m:
        return [{"action": "lore_answer", "query": m.group(1).strip()}]
    return None


async def _send_g_private(user, content):
    """DM G: output; message events cannot create Discord ephemeral messages."""
    try:
        return await user.send(content[:3900])
    except (discord.Forbidden, discord.HTTPException):
        return None


def _can_use_g_interaction(interaction):
    if interaction.guild is None or interaction.user.bot:
        return False
    if int(getattr(interaction.user, "id", 0) or 0) == GM_COMMAND_USER_ID:
        return True
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and getattr(perms, "administrator", False))


@app_commands.command(name="g", description="Private GM AI assistant (ephemeral).")
@app_commands.describe(instruction="Your GM instruction")
async def g_command(interaction: discord.Interaction, instruction: str):
    if not _can_use_g_interaction(interaction):
        return await interaction.response.send_message("GM/admin only.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    result = await _process_g_text(interaction.guild, interaction.user, instruction)
    await interaction.followup.send(result[:3900], ephemeral=True)


async def _process_g_text(guild, actor, text):
    fast_actions = _fast_route(text)
    if fast_actions:
        results = await _execute(guild, actor, fast_actions)
        save_item_data()
        lore_answers = [r[len("LORE_ANSWER:"):].strip() for r in results if isinstance(r, str) and r.startswith("LORE_ANSWER:")]
        other = [r for r in results if not (isinstance(r, str) and r.startswith("LORE_ANSWER:"))]
        out = "**G:**"
        if other:
            out += "\n" + "\n".join(f"✓ {r}" for r in other)
        if lore_answers:
            out += "\n\n**Lore explanation**\n" + "\n\n".join(lore_answers)
        return out[:3900]
    if not _ai_enabled():
        return ("**G:** AI is unavailable, but the GM tools are still usable.\n"
                "Supported offline commands include: `lock`, `unlock`, `revive <player>`, "
                "`send <player> to hell`, `give <player> <amount>`, `remember: <fact>`, "
                "`session log: <event>`, and `lore on <topic>`.\n"
                "Use `/gm-game start` and `/gm-game end` normally; they do not require AI.")
    prompt = _prompt(guild, text, _gm_state(guild.id).get("game_started", False), _state_summary(guild.id))
    try:
        raw_result = await asyncio.to_thread(_ai_sync_analyze, prompt)
        data = _parse_ai_json(raw_result)
    except Exception as exc:
        # Never make the GM/game depend on an AI provider. If all providers
        # are exhausted, give the deterministic offline command list instead
        # of returning a provider error that blocks the GM.
        return ("**G:** AI is currently unavailable. Offline GM tools remain active.\n"
                "Try: `lock`, `unlock`, `revive <player>`, `send <player> to hell`, "
                "`give <player> <amount>`, `remember: <fact>`, `session log: <event>`, "
                "or `lore on <topic>`.\n"
                "`/gm-game start` and `/gm-game end` do not use AI.")
    if not isinstance(data, dict):
        return "**G:** The AI returned an invalid command response."
    clarification = str(data.get("clarification") or "").strip()
    reply = str(data.get("reply") or "").strip()
    actions = data.get("actions") if isinstance(data.get("actions"), list) else []
    normalized = []
    for a in actions:
        if isinstance(a, dict):
            a = dict(a)
            if not a.get("action") and a.get("type"):
                a["action"] = a.get("type")
            normalized.append(a)
    actions = normalized
    if clarification:
        return f"**G:** {clarification}"
    if not actions:
        return reply or "**G:** I couldn't map that to an existing GM action."
    direct_actions = {"underworld_send", "hell_event", "give_vg", "give_item", "session_event", "memory_add", "reputation_add", "story_secret_channel", "create_channel", "search", "lore_answer", "help", "revive"}
    requires_confirmation = bool(data.get("requires_confirmation"))
    if len(actions) == 1 and str(actions[0].get("action", "")).lower() in direct_actions:
        if str(actions[0].get("action", "")).lower() in {"underworld_send", "hell_event", "give_vg", "give_item", "session_event", "memory_add", "reputation_add", "story_secret_channel", "create_channel", "revive"}:
            requires_confirmation = False
    preview = "**G: ACTIONS**\n" + "\n".join(f"{i}. {_action_summary(a)}" for i, a in enumerate(actions, 1))
    if requires_confirmation:
        _gm_state(guild.id).setdefault("gm_pending_actions", []).append({"id": uuid.uuid4().hex[:10], "actions": actions, "actor_id": actor.id, "created_at": _now()})
        _gm_state(guild.id)["gm_pending_actions"] = _gm_state(guild.id)["gm_pending_actions"][-10:]
        save_item_data()
        return preview + "\n\n**Confirmation required.** Use `G: confirm` or review the pending action before confirming."
    results = await _execute(guild, actor, actions)
    search_actions = [a for a in actions if str(a.get("action", "")).lower() == "search"]
    search_text = await _search(guild.id, search_actions[0].get("query")) if search_actions else ""
    gm = _gm_state(guild.id)
    gm["gm_ai_log"].append({"at": _now(), "actor_id": actor.id, "input": str(text)[:2000], "actions": actions, "results": results})
    gm["gm_ai_log"] = gm["gm_ai_log"][-200:]
    save_item_data()
    cleaned_results = []
    lore_answers = []
    for r in results:
        if r == "search_pending":
            continue
        if isinstance(r, str) and r.startswith("LORE_ANSWER:"):
            lore_answers.append(r[len("LORE_ANSWER:"):].strip())
        else:
            cleaned_results.append(r)
    out = preview
    if cleaned_results:
        out += "\n\n" + "\n".join(f"✓ {r}" for r in cleaned_results)
    if lore_answers:
        out += "\n\n**Lore explanation**\n" + "\n\n".join(lore_answers)
    if search_text:
        out += "\n\n**Search results**\n" + search_text
    if reply:
        out += "\n\n" + reply
    return out[:3900]


async def handle_gm_message(message, bot):
    if message.guild is None or message.author.bot:
        return False
    raw = (message.content or "").strip()
    if not raw.casefold().startswith("g:"):
        return False
    if not _can_use_g(message):
        return False
    text = raw[2:].strip()
    if not text:
        await _send_g_private(message.author, "**G:** Type the GM instruction after `G:`.")
        await _delete_g_message(message)
        return True
    await _delete_g_message(message)
    result = await _process_g_text(message.guild, message.author, text)
    await _send_g_private(message.author, result)
    return True


async def confirm_pending(message):
    if message.guild is None or message.author.bot:
        return False
    raw = (message.content or "").strip()
    if raw.casefold() != "g: confirm":
        return False
    if not _can_use_g(message):
        return False
    await _delete_g_message(message)
    gm = _gm_state(message.guild.id)
    pending = gm.get("gm_pending_actions") or []
    if not pending:
        await _send_g_private(message.author, "There are no pending G: actions to confirm.")
        return True
    row = pending.pop()
    if int(row.get("actor_id", 0)) != message.author.id:
        await _send_g_private(message.author, "Only the GM/admin who created the pending action set can confirm it.")
        return True
    results = await _execute(message.guild, message.author, row.get("actions", []))
    save_item_data()
    await _send_g_private(message.author, "**G: CONFIRMED**\n" + "\n".join(f"✓ {r}" for r in results))
    return True


def register(bot):
    bot.tree.add_command(g_command)
