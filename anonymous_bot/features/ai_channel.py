"""Restricted AI channel for the campaign bot.

The #ai channel is available to guild administrators, configured GMs/writers,
and the one configured regular player (Kaizen). Other regular players cannot
interact with the AI. It never changes campaign state by itself.
"""
import asyncio
import re
import time

import discord

from .ai_providers import complete as _ai_complete
from .memory import _store, _best_lore, _session_context, _latest_session_context
from ..core import campaign_store
from ..config import BOT_OWNER_USER_ID, AI_PLAYER_USER_IDS, GM_USER_IDS, GAME_GUILD_ID


AI_CHANNEL_NAME = "ai"

# Prevent the same Discord message from being answered more than once. This is
# durable in campaign state, so a reconnect/restart cannot cause an old #ai
# message to be processed again.
def _mark_ai_message_processed(guild_id, message_id):
    try:
        from .memory import _store
        mem = _store(guild_id)
        settings = mem.setdefault("settings", {})
        seen = settings.setdefault("ai_processed_message_ids", [])
        mid = str(message_id)
        if mid in {str(x) for x in seen}:
            return False
        seen.append(mid)
        settings["ai_processed_message_ids"] = seen[-2000:]
        return True
    except Exception:
        # If state is unavailable, fail open so the AI still works.
        return True



def _is_gm(member):
    """Return whether this user is an explicitly configured campaign GM."""
    if member is None:
        return False
    return str(getattr(member, "id", "")) in {str(x) for x in GM_USER_IDS}


def _is_ai_user(member):
    """Return whether this member may directly interact with the AI.

    Regular-player access is intentionally limited to Kaizen via
    AI_PLAYER_USER_IDS. Guild admins and configured GMs/writers are also allowed.
    """
    if member is None:
        return False
    uid = str(getattr(member, "id", ""))
    return uid in {str(x) for x in AI_PLAYER_USER_IDS} or _is_gm(member) or _is_admin(member)


def _is_admin(member):
    """Robust administrator check for the private #ai channel.

    Discord's cached guild_permissions can occasionally be stale, so also
    accept the guild owner and the configured bot owner.
    """
    if member is None:
        return False
    if int(getattr(member, "id", 0) or 0) == int(BOT_OWNER_USER_ID):
        return True
    guild = getattr(member, "guild", None)
    if guild is not None and int(getattr(guild, "owner_id", 0) or 0) == int(getattr(member, "id", 0) or 0):
        return True
    perms = getattr(member, "guild_permissions", None)
    if perms and getattr(perms, "administrator", False):
        return True
    if guild is not None:
        try:
            channel = discord.utils.find(lambda c: isinstance(c, discord.TextChannel) and c.name.casefold() == AI_CHANNEL_NAME, guild.text_channels)
            effective = channel.permissions_for(member) if channel else None
            if effective and effective.administrator:
                return True
        except Exception:
            pass
    return False


async def ensure_ai_channel(guild):
    """Create #ai if missing and allow admins, GMs/writers, and Kaizen."""
    if not guild.me or not guild.me.guild_permissions.manage_channels:
        print(f"AI channel warning for {guild.name}: bot needs Manage Channels permission.")
        return None

    channel = discord.utils.find(
        lambda c: isinstance(c, discord.TextChannel) and c.name.casefold() == AI_CHANNEL_NAME,
        guild.text_channels,
    )

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
        ),
    }

    # Administrators bypass the default deny. Explicitly allow only configured
    # GMs/writers and the single regular player (Kaizen).
    allowed_ids = {str(x) for x in AI_PLAYER_USER_IDS} | {str(x) for x in GM_USER_IDS}
    for uid in allowed_ids:
        member = guild.get_member(int(uid))
        if member is not None:
            overwrites[member] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )
    reason = "Campaign AI assistant: admins, GMs/writers, and configured test player only"

    try:
        if channel is None:
            channel = await guild.create_text_channel(
                AI_CHANNEL_NAME,
                topic="Administrator-only AI assistant. Ask anything about the campaign or bot.",
                overwrites=overwrites,
                reason=reason,
            )
            print(f"Created admin-only AI channel #{AI_CHANNEL_NAME} in {guild.name}.")
        else:
            # Enforce the intended privacy on an existing #ai channel without
            # changing unrelated permissions.
            everyone = channel.overwrites_for(guild.default_role)
            everyone.view_channel = False
            everyone.send_messages = False
            await channel.set_permissions(guild.default_role, overwrite=everyone, reason=reason)

            allowed_ids = {str(x) for x in AI_PLAYER_USER_IDS} | {str(x) for x in GM_USER_IDS}
            for uid in allowed_ids:
                member = guild.get_member(int(uid))
                if member is not None:
                    await channel.set_permissions(
                        member,
                        view_channel=True, send_messages=True, read_message_history=True,
                        reason=reason,
                    )

            me = channel.overwrites_for(guild.me)
            me.view_channel = True
            me.send_messages = True
            me.read_message_history = True
            me.manage_messages = True
            await channel.set_permissions(guild.me, overwrite=me, reason=reason)
        return channel
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"AI channel warning for {guild.name}: {type(exc).__name__}: {exc}")
        return None


def _profile_context(guild_id, query):
    """Return structured entity/player profiles before noisy raw-message evidence."""
    try:
        mem = _store(guild_id)
    except Exception:
        return ""
    q = (query or "").casefold()
    matches = []
    for key, profile in (mem.get("entity_profiles") or {}).items():
        if not isinstance(profile, dict):
            continue
        name = str(profile.get("name") or "")
        aliases = [str(x) for x in profile.get("aliases") or []]
        hay = " ".join([name, key, *aliases]).casefold()
        if name and name.casefold() in q or any(a.casefold() in q for a in aliases):
            score = 100 if name.casefold() in q else 70
            if profile.get("type") == "player":
                score += 15
            matches.append((score, profile))
    matches.sort(key=lambda x: x[0], reverse=True)
    out = []
    used = set()
    for _, profile in matches[:10]:
        pid = str(profile.get("id") or "")
        if pid in used:
            continue
        used.add(pid)
        lines = [f"[STRUCTURED PROFILE] {profile.get('name','Unknown')} ({profile.get('type','other')})",
                 f"Canon status: {profile.get('status','unknown')}; authority: {profile.get('authority','unknown')}",
                 f"Current status: {profile.get('current_status','unknown')}"]
        for field, label in (("summary","Summary"),("backstory","Backstory"),("origin","Origin"),("role","Role")):
            value = str(profile.get(field) or '').strip()
            if value:
                lines.append(f"{label}: {value[:1200]}")
        facts = [f.get("text") for f in profile.get("facts", [])[-20:] if isinstance(f, dict) and f.get("text")]
        if facts:
            lines.append("Facts: " + " | ".join(str(x)[:500] for x in facts[-12:]))
        rels = [f"{r.get('entity')}: {r.get('relationship')}" for r in profile.get("relationships", [])[-20:] if isinstance(r, dict) and r.get("entity") and r.get("relationship")]
        if rels:
            lines.append("Relationships: " + " | ".join(rels[-12:]))
        if profile.get("sessions"):
            lines.append("Sessions: " + ", ".join(str(x) for x in profile.get("sessions", [])[-30:]))
        if profile.get("first_appearance"):
            fa = profile["first_appearance"]
            lines.append(f"First appearance: Session #{fa.get('session_number','?')} at {fa.get('created_at','unknown')}")
        if profile.get("last_appearance"):
            la = profile["last_appearance"]
            lines.append(f"Last recorded appearance: Session #{la.get('session_number','?')} at {la.get('created_at','unknown')}")
        if profile.get("death"):
            d = profile["death"]
            lines.append(f"Death: Session #{d.get('session_number','?')}; cause: {d.get('cause','unknown')}")
        if profile.get("type") == "player":
            chars = [f"{x.get('name','Unknown')} [{x.get('status','unknown')}] (Session {x.get('session_number','?')})" for x in profile.get("character_history", []) if isinstance(x, dict)]
            if chars:
                lines.append("Character history: " + " | ".join(chars[-20:]))
            lines.append(f"Active character ID: {profile.get('active_character_id') or 'none'}")
        out.append("\n".join(lines))
    return "\n\n".join(out)[:10000]


def _player_activity_context(guild_id, query):
    """Use actual archived activity for questions about a player's last session."""
    q = (query or "").casefold()
    if not re.search(r"\b(last|most recent|latest)\b.*\b(play|played|session)\b|\b(last|most recent|latest)\s+session\b", q):
        return ""
    mem = _store(guild_id)
    candidates = []
    for profile in (mem.get("entity_profiles") or {}).values():
        if not isinstance(profile, dict):
            continue
        name = str(profile.get("name") or "")
        if not name or name.casefold() not in q:
            continue
        if profile.get("type") in {"player", "character"}:
            candidates.append(profile)
    if not candidates:
        return ""
    lines = ["[PLAYER/CHARACTER ACTIVITY — authoritative for 'last time they played' questions]"]
    for profile in candidates[:6]:
        lines.append(f"Player/character: {profile.get('name')} ({profile.get('type')})")
        if profile.get("type") == "player":
            lines.append(f"Active character ID: {profile.get('active_character_id') or 'none'}")
            hist = profile.get("character_history") or []
            if hist:
                lines.append("Character roster: " + " | ".join(f"{x.get('name','Unknown')} [{x.get('status','unknown')}] — started Session {x.get('session_number','?')}" for x in hist[-20:] if isinstance(x, dict)))
            target_id = str(profile.get("discord_user_id") or "")
            rows = [r for r in (mem.get("archive") or []) if target_id and str(r.get("author_id") or "") == target_id]
        else:
            rows = [r for r in (mem.get("archive") or []) if str(r.get("author_name") or "").casefold() == profile.get("name", "").casefold()]
        if rows:
            rows.sort(key=lambda r: r.get("created_at") or "")
            latest_session = next((r.get("session_number") for r in reversed(rows) if r.get("session_number") is not None), None)
            if latest_session is not None:
                lines.append(f"Latest archived session with activity: Session #{latest_session}")
                for r in [r for r in rows if r.get("session_number") == latest_session][-40:]:
                    lines.append(f"- [{r.get('created_at')}] {r.get('author_name')}: {str(r.get('content') or '')[:700]}")
    return "\n".join(lines)[:12000]


def _lore_context(guild_id, query):
    """Pull structured profiles first, then strongest supporting evidence."""
    parts = []
    structured = _profile_context(guild_id, query)
    if structured:
        parts.append(structured)
    activity = _player_activity_context(guild_id, query)
    if activity:
        parts.append(activity)
    try:
        rows = _best_lore(guild_id, query, include_gm=True)
    except Exception:
        rows = []
    seen = set()
    for _, kind, row in rows[:10]:
        key = (kind, str(row.get("message_id") or row.get("id") or row.get("created_at") or ""))
        if key in seen or kind == "profile":
            continue
        seen.add(key)
        if kind == "record":
            text = row.get("description") or ""
            if row.get("gm_notes"):
                text += f" | GM notes: {row.get('gm_notes')}"
            label = f"CAMPAIGN {str(row.get('visibility','canon')).upper()} LORE"
        elif kind == "fact":
            text = row.get("text") or ""
            label = "CAMPAIGN FACT"
        else:
            text = row.get("content") or ""
            author_id = str(row.get("author_id") or "")
            label = "GM/WRITER SERVER EVIDENCE" if author_id in {str(x) for x in GM_USER_IDS} else "PLAYER SERVER EVIDENCE (NOT CANON BY ITSELF)"
        text = re.sub(r"\s+", " ", str(text)).strip()[:650]
        if text:
            parts.append(f"- [{label}] {text}")
    return "\n\n".join(parts)[:16000]


async def _recent_channel_context(channel, limit=16):
    try:
        messages = [m async for m in channel.history(limit=limit, oldest_first=False)]
    except Exception:
        return ""
    messages.reverse()
    lines = []
    for m in messages:
        if m.author.bot:
            continue
        content = (m.content or "").strip()
        if content:
            lines.append(f"{m.author.display_name}: {content[:1200]}")
    return "\n".join(lines)[-9000:]


def _requested_session_number(question):
    """Detect explicit session-history questions without guessing a number."""
    q = (question or "").casefold()
    if re.search(r"\b(first|1st|one|session\s*#?\s*1)\b", q) and "session" in q:
        return 1
    m = re.search(r"\bsession\s*#?\s*(\d+)\b", q)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d+)(?:st|nd|rd|th)\s+session\b", q)
    if m:
        return int(m.group(1))
    return None


def _chronology_context(guild_id, question):
    number = _requested_session_number(question)
    if number is None:
        return ""
    session = _session_context(guild_id, number, include_gm=True)
    if not session:
        return f"[REQUESTED SESSION] Session #{number} is not present in the recorded session history. Do not invent what happened in it."
    lines = [
        f"[REQUESTED SESSION — SESSION #{session['session_number']}]",
        f"Title: {session['title']}",
        f"Started: {session.get('started_at')}",
        f"Ended: {session.get('ended_at')}",
        "This exact session was requested. Use this section before global lore search results.",
    ]
    if session.get("today"):
        lines.append("GM/session briefing: " + str(session["today"])[:2500])
    events = session.get("events") or []
    if events:
        lines.append("Recorded session events:")
        for e in events[:80]:
            lines.append("- " + re.sub(r"\s+", " ", str(e))[:1000])
    recap = session.get("recap") or {}
    if recap:
        for key, label in (("major_events","Major events"),("characters_involved","Characters involved"),("major_discoveries","Major discoveries"),("unresolved_events","Unresolved events"),("new_threats","New threats"),("lore_events","Lore events")):
            vals = recap.get(key) or []
            if vals:
                lines.append(label + ": " + " | ".join(str(x)[:700] for x in vals[:20]))
    if session.get("transcript"):
        lines.append("Recorded transcript evidence (chronological):")
        for row in session["transcript"][-220:]:
            lines.append(f"- [{row.get('created_at')}] {row.get('author_name')}: {row.get('content')}")
    return "\n".join(lines)[:14000]


async def handle_message(message):
    """Respond to every administrator message in #ai."""
    if message.guild is None or message.author.bot:
        return False
    if not isinstance(message.channel, discord.TextChannel):
        return False
    if message.channel.name.casefold() != AI_CHANNEL_NAME:
        return False

    # The channel itself is administrator-only through Discord permissions.
    # Do NOT delete messages here: an administrator's permission cache can be
    # temporarily stale, and deleting the message makes the channel appear
    # broken. If a non-admin somehow reaches the channel, simply ignore it.
    if not _is_ai_user(message.author):
        print(f"AI channel ignored message from unauthorized user {message.author} (ID {message.author.id}) in guild {message.guild.id}.")
        return True

    # Discord can redeliver events during reconnects, and multiple bot workers
    # can otherwise answer the same prompt. Claim the message before doing any
    # AI/network work.
    if not _mark_ai_message_processed(message.guild.id, message.id):
        print(f"AI channel skipped duplicate message {message.id} in guild {message.guild.id}.")
        return True

    # Keep the #ai conversation in the durable campaign database, but do not
    # enqueue it for lore classification; #ai is a control/brainstorming room.
    campaign_store.archive_message(message, priority=False)

    prompt = (message.content or "").strip()
    if not prompt:
        return True

    await message.channel.typing()

    recent = await _recent_channel_context(message.channel, limit=8)
    lore = _lore_context(message.guild.id, prompt)
    chronology = _chronology_context(message.guild.id, prompt)

    gm_user = _is_gm(message.author)
    system = """You are the campaign's AI co-writer, lore assistant, and GM support assistant.

The human Game Master/writer/DM is Draven (also known as Rivic and Rico).
Civic is also an authorized GM/writer. Their Discord IDs are configured in the
bot and their direct statements/corrections are authoritative campaign input.
Draven is the primary GM, but all configured GM/writer identities have authority.
 The AI does NOT outrank him, reinterpret his statements, or
replace his canon with guesses from older records.

SOURCE AUTHORITY (highest to lowest):
1. Clear, serious GM/writer canon statements or corrections from Draven or Civic.
2. Explicit canon records, GM notes, and current campaign/session information.
3. Historical server messages as evidence, with GM/writer messages weighted above player messages.
4. Player statements, which are useful evidence but are NEVER canon by themselves.
5. AI-generated summaries, classifications, and inferences. These are NOT authoritative and can be wrong.

IMPORTANT GM-JOKE RULE:
- Draven and Civic are both GMs/writers, but they also joke, roleplay, speculate,
  exaggerate, troll, and say things that are not intended as canon. Do NOT promote
  an obviously joking, hypothetical, sarcastic, in-character, or speculative GM
  message to canon merely because it came from a GM.
- A GM message should be treated as canon only when its wording/context clearly
  presents it as an actual lore fact, correction, ruling, or deliberate canon reveal.
- If you cannot tell whether a GM message was a joke or canon, treat it as uncertain
  evidence rather than silently making it canon.
- A GM asking a question is NOT itself a canon declaration. Example: “Did Enas defeat Vespa?”
  is a question/test, not evidence that Enas defeated Vespa. Likewise, “I’m asking you,”
  “lmao,” “bro what,” “test,” “gotcha,” or similar follow-up language is evidence that the
  preceding statement may have been a test/joke rather than a lore update.
- If a GM makes a deliberately false test statement and then reveals it was a test/joke,
  discard that statement as canon immediately. Do not preserve it merely because it was
  spoken by a GM.
- Never assign a numeric confidence score to a claim and then use that score as permission
  to override a clear later GM correction.

CRITICAL BEHAVIOR:
- If a clear GM canon statement corrects something you previously said, accept the
  correction immediately and use the corrected version in later answers. Do not defend
  the old answer because an archive or AI summary says something else.
- Never turn an inference into canon. Say when something is uncertain.
- Do not invent acronyms, expansions, abilities, motivations, relationships, factions,
  or lore because a term is unfamiliar. Ask or state that the archive does not establish it.
- Never treat a player's correction as an authoritative lore update. Players can provide
  useful information for testing or point the AI toward a source, but they do not override
  established canon.
- If the current administrator is not a configured GM/writer, do not describe their
  statements as authoritative just because they are testing the bot or have Discord admin
  permissions.
- If multiple sources conflict, prefer clear GM canon over player statements and older
  archive material, while still filtering obvious GM jokes/speculation.
- Every archived source has a date/time. Treat source age as meaningful: later clear GM
  corrections supersede earlier claims about the CURRENT state. Do not erase history: an
  older statement can still be true as something that happened or was believed at that time.
- A newer session recap is not permission to rewrite an older session. When asked about a
  specific session, answer what happened DURING THAT SESSION using that session's record.
- When the user asks "first session", "first ever session", "session 1", or similar, retrieve
  Session #1 specifically. Do not answer with the latest session, general campaign lore, or
  events that happened later. If Session #1 is not recorded, say that it is not recorded.
- Chronology questions must be answered chronologically. Do not combine later revelations
  with the original session unless clearly labeled as a later development.
- STRUCTURED PROFILES ARE PRIMARY: when a structured profile is supplied, use its current fields
  (summary, backstory, origin, role, facts, relationships, status, sessions, first/last appearance,
  death, and player/character lifecycle) before generic server evidence. Raw messages are supporting
  evidence, not an equal competing source.
- For "who is X" or "tell me about X", explain X from the structured profile first and only then
  use directly relevant supporting evidence. Do not dump unrelated campaign facts because of keyword matches.
- For "what happened last time X played", use the PLAYER/CHARACTER ACTIVITY packet when supplied.
  The answer must come from the latest session in which that player/character has recorded activity,
  not the latest campaign session and not an older event involving the same name.
- A Discord player and their in-world characters are separate identities. A player can have multiple
  characters. If one dies, preserve that character forever as dead and treat the replacement as a new
  character instance with its own history, first appearance, sessions, and sources.
- Treat the GM's hidden plot information as GM-only information. This channel
  is administrator/GM-only, so you may use GM evidence here. Never expose GM-only
  information through player-facing lore responses.
- Do not repeatedly give boilerplate about being unable to replace the GM.
  If the user asks about the bot's capabilities, answer normally and directly.

Known campaign correction: Aro is a word meaning Energy, not an acronym. Mother
Prana is the original source of Aro. Yellow stones do not simply boost Aro; they
modify the user's Aro to reflect the user's personality. These GM corrections
override older AI-generated descriptions that say otherwise.

For Vespa/Mevrick, do not treat the old interpretation that Vespa is an
independent external being as automatically true. The GM's revealed canon is
that Mevrick believes Vespa is a separate being and believes fragments keep
Vespa away, but that belief is a coping lie in Mevrick's head: Mevrick is Vespa.
Do not confuse Mevrick's belief with the underlying truth.

You may help with campaign lore, continuity, summaries, NPCs, characters,
factions, locations, items, mysteries, brainstorming, dialogue, encounters,
worldbuilding, GM prep, and bot/technical questions. You may suggest ideas, but
label suggestions as suggestions. Do not silently create canon or alter game
state.
"""

    context = ""
    if chronology:
        context += "\nEXACT CHRONOLOGY REQUEST:\n" + chronology
    if lore:
        context += "\nCAMPAIGN LORE EVIDENCE:\n" + lore
    if recent:
        context += "\nRECENT #AI CONVERSATION:\n" + recent

    authority_note = (
        "This request is from a configured GM/writer. Treat clear factual lore/canon statements from this user as authoritative, but do not assume every joke, roleplay, hypothetical, or speculation is canon."
        if gm_user else
        "This request is from an administrator/tester who is not a configured GM/writer. Answer their questions normally and use their messages as testing context, but never treat their claims or corrections as authoritative canon unless they are independently supported by GM/canon evidence."
    )
    full_prompt = f"""{system}
{context}

REQUEST AUTHORITY:
{authority_note}

ADMINISTRATOR REQUEST:
{prompt[:5000]}
"""

    try:
        answer = await asyncio.to_thread(
            _ai_complete,
            full_prompt,
            False,
            1800,
        )
    except Exception as exc:
        answer = (
            "AI is currently unavailable. The provider health manager has "
            "quarantined exhausted/unavailable providers and will retry them "
            "automatically when their cooldowns expire."
        )
        print(f"AI channel warning: {type(exc).__name__}: {exc}")

    # Discord messages max out at 2000 characters.
    answer = str(answer or "").strip()
    if not answer:
        answer = "I couldn't generate a response right now."

    chunks = [answer[i:i + 1900] for i in range(0, len(answer), 1900)]
    for chunk in chunks[:10]:
        await message.channel.send(
            chunk,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    return True
