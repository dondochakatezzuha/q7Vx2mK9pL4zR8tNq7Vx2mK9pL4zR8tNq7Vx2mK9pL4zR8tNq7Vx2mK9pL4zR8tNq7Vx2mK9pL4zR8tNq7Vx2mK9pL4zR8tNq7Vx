import uuid
import asyncio
from datetime import datetime, timezone

import discord
from discord import app_commands

from ..state import is_staff
from ..config import GM_USER_IDS
from .anonymous import resolve_members
from .items import item_state, save_item_data, item_key, ITEM_CATALOG, add_item, compact_item, custom_catalog_items, _new_instance_id, _fresh_instance_id, _record_discovery, _expire_discovery
from .groups import ADMIN_GROUP, GM_GROUP, ADMIN_GAME_GROUP, ADMIN_ATTENDANCE_GROUP, ADMIN_SESSION_GROUP, ADMIN_BOUNTY_GROUP, ADMIN_REPUTATION_GROUP, ADMIN_ITEM_GROUP, SESSION_GROUP
from . import memory

# Slash-command groups keep the command tree organized and below Discord's 100 global command limit.
GAME_GROUP = app_commands.Group(name="game", description="Game session controls.")
ATTENDANCE_GROUP = app_commands.Group(name="attendance", description="Session attendance controls.")
BOUNTY_GROUP = app_commands.Group(name="bounty", description="Campaign bounty commands.")
REPUTATION_GROUP = app_commands.Group(name="reputation", description="Campaign reputation commands.")
PARTY_GROUP = app_commands.Group(name="party", description="Party commands.")


def _is_gm_member(member: discord.Member | None) -> bool:
  """Whether a guild member is a configured GM or has staff permissions."""
  if member is None:
    return False
  if str(member.id) in {str(uid) for uid in GM_USER_IDS}:
    return True
  permissions = getattr(member, "guild_permissions", None)
  return bool(permissions and (
    permissions.administrator
    or permissions.manage_guild
    or permissions.manage_channels
  ))



def _guild(guild_id):
  st = item_state(guild_id)
  st.setdefault("gm_tools", {})
  g = st["gm_tools"]
  g["_guild_id"] = int(guild_id)
  g.setdefault("bounties", [])
  g.setdefault("reputation", {})
  g.setdefault("parties", {})
  g.setdefault("party_members", {})
  g.setdefault("attendance", {})
  g.setdefault("session_events", [])
  g.setdefault("session_history", [])
  g.setdefault("session_number", 0)
  g.setdefault("dm_spawn", None)
  g.setdefault("server_spawn", None)
  g.setdefault("pending_dm_claim", None)
  g.setdefault("pending_server_claim", None)
  g.setdefault("spawn_history", [])
  g.setdefault("custom_categories", [])
  g.setdefault("consequence_queue", [])
  g.setdefault("server_locked", False)
  return g


async def _set_game_channel_lock(guild: discord.Guild, locked: bool, channel_id: int | None = None):
  """Lock/unlock exactly one channel in the configured campaign server.

  The game channel remains visible/readable. During a lock, players cannot
  send messages. We explicitly neutralize role/member send-message allows in
  this channel because an @everyone deny alone can be overridden by a role or
  member-specific allow.
  """
  from ..config import GAME_CHANNEL_ID, GAME_GUILD_ID

  # The configured channel is only a legacy/default. Active sessions use the channel
  # where /gm-game start was run, and that channel id is persisted in lock state.
  g = _guild(guild.id)
  lock_state = g.setdefault("game_channel_lock_state", {})
  if channel_id is None:
    channel_id = lock_state.get("channel_id") or GAME_CHANNEL_ID
  channel_id = int(channel_id)

  if guild.id != GAME_GUILD_ID:
    raise RuntimeError(
      f"This game control is restricted to server {GAME_GUILD_ID}; received guild {guild.id}."
    )

  channel = guild.get_channel(channel_id)
  if channel is None:
    try:
      channel = await guild.fetch_channel(channel_id)
    except Exception as exc:
      raise RuntimeError(
        f"Game channel {channel_id} could not be fetched "
        f"for guild {guild.id}: {type(exc).__name__}: {exc}"
      ) from exc

  if not isinstance(channel, discord.TextChannel):
    raise RuntimeError(f"Game channel {channel_id} is not a text channel.")
  if channel.guild.id != GAME_GUILD_ID or channel.id != channel_id:
    raise RuntimeError("Refusing to modify a channel outside the campaign server.")

  me = guild.me
  if me is None or not me.guild_permissions.manage_channels:
    raise RuntimeError("The bot needs Manage Channels permission in the game channel.")

  lock_state["channel_id"] = channel_id

  if locked:
    # Save the exact existing send_messages overwrite state once. This lets
    # game start restore the channel exactly instead of guessing later.
    if not lock_state.get("snapshot"):
      snapshot = {"roles": {}, "members": {}}
      for target, overwrite in channel.overwrites.items():
        if isinstance(target, discord.Role):
          snapshot["roles"][str(target.id)] = overwrite.send_messages
        elif isinstance(target, discord.Member):
          snapshot["members"][str(target.id)] = overwrite.send_messages
      lock_state["snapshot"] = snapshot

    # @everyone deny keeps the channel visible/readable but prevents sending.
    everyone = channel.overwrites_for(guild.default_role)
    everyone.send_messages = False
    await channel.set_permissions(
      guild.default_role,
      overwrite=everyone,
      reason="RPG game ended: lock campaign game channel for players",
    )

    # A role/member allow can override @everyone's deny. Neutralize explicit
    # send-message allows on this channel only. Other permissions are preserved.
    for role in list(channel.overwrites.keys()):
      if not isinstance(role, discord.Role) or role == guild.default_role:
        continue
      ow = channel.overwrites_for(role)
      if ow.send_messages is True:
        ow.send_messages = False
        await channel.set_permissions(role, overwrite=ow, reason="RPG game ended: prevent player role from sending")

    # Preserve GM/admin staff access where possible. Explicit member allows
    # override the role-level lock. Discord administrators already bypass this.
    for member in guild.members:
      if member.bot or not _is_gm_member(member):
        continue
      ow = channel.overwrites_for(member)
      ow.send_messages = True
      await channel.set_permissions(member, overwrite=ow, reason="RPG game ended: retain GM staff chat access")

    lock_state["locked"] = True
    g["server_locked"] = True
  else:
    snapshot = lock_state.get("snapshot") or {"roles": {}, "members": {}}

    # Restore only the send_messages values that existed before the lock.
    # View Channel/read history are never touched by this function.
    targets = {guild.default_role.id: guild.default_role}
    targets.update({r.id: r for r in channel.overwrites if isinstance(r, discord.Role)})
    targets.update({m.id: m for m in channel.overwrites if isinstance(m, discord.Member)})

    for target_id, target in targets.items():
      if isinstance(target, discord.Role):
        old_value = snapshot.get("roles", {}).get(str(target_id))
      else:
        old_value = snapshot.get("members", {}).get(str(target_id))
      if target_id == guild.default_role.id:
        old_value = snapshot.get("roles", {}).get(str(target_id))
      if old_value is None and not (
        str(target_id) in snapshot.get("roles", {}) if isinstance(target, discord.Role)
        else str(target_id) in snapshot.get("members", {})
      ):
        # No saved send_messages overwrite existed; remove only that one field.
        ow = channel.overwrites_for(target)
        ow.send_messages = None
      else:
        ow = channel.overwrites_for(target)
        ow.send_messages = old_value
      await channel.set_permissions(target, overwrite=ow, reason="RPG game started: unlock campaign game channel")

    lock_state["locked"] = False
    lock_state["snapshot"] = None
    g["server_locked"] = False

  # Verify the configured channel itself after Discord applies the changes.
  refreshed = await guild.fetch_channel(channel_id)
  everyone_state = refreshed.overwrites_for(guild.default_role).send_messages
  if locked and everyone_state is not False:
    raise RuntimeError(f"Discord did not apply the game-channel lock (got send_messages={everyone_state!r}).")
  if not locked and everyone_state is False:
    raise RuntimeError(f"Discord did not remove the game-channel lock (got send_messages={everyone_state!r}).")

  save_item_data()


async def _ensure_server_lock_state(guild: discord.Guild):
  """Backward-compatible startup recovery; only touches the configured game channel."""
  g = _guild(guild.id)
  if g.get("server_locked") and not g.get("game_started"):
    channel_id = (g.get("game_channel_lock_state") or {}).get("channel_id")
    if not channel_id:
      g["server_locked"] = False
      save_item_data()
      return
    try:
      await _set_game_channel_lock(guild, True, int(channel_id))
    except discord.NotFound:
      # The previously locked channel was deleted. Do not spam the console or
      # keep retrying a channel that can never be restored.
      g["server_locked"] = False
      state = g.setdefault("game_channel_lock_state", {})
      state["locked"] = False
      state["snapshot"] = None
      save_item_data()
    except Exception as exc:
      print(f"Could not restore game-channel lock for {guild.id}: {type(exc).__name__}: {exc}")


def _custom_catalog(guild_id):
  # The GM Catalog is authoritative and shared by the GM/shop/spawn systems.
  return custom_catalog_items()

def _find_custom_item(guild_id, name):
  key = item_key(name)
  return next((x for x in _custom_catalog(guild_id) if item_key(x.get("name", "")) == key), None)

def _instantiate_spawn_item(template):
  item = dict(template)
  import uuid
  item["id"] = _new_instance_id("gm-item")
  item["instance_id"] = item["id"]
  item["spawned_at"] = _now()
  item["custom_template"] = False
  item["custom_catalog_id"] = template.get("id")
  return compact_item(item)

async def _deliver_dm_spawn(bot, guild, spawn):
  gm = _guild(guild.id)
  # If a persisted pending discovery already exists, the previous delivery
  # completed before the bot could clear the scheduled spawn. Never duplicate it.
  existing = gm.get("pending_dm_claim")
  if existing and existing.get("discovery_id") and existing.get("state") in {"UNCLAIMED", "ACTIVE"}:
    return True
  target = guild.get_member(int(spawn.get("user_id", 0)))
  if target is None or target.bot:
    return False
  item = _instantiate_spawn_item(spawn["template"])
  expire_after = int(spawn.get("expire_after", 0) or 0)
  # The GM-selected expiration controls the entire discovery lifetime.
  # 0 means the discovery does not expire. Do not use a hard-coded 30s claim window.
  item_expires = (_now() + expire_after) if expire_after > 0 else None
  claim_expires = item_expires
  if item_expires:
    item["expires_at"] = item_expires

  discovery_id = _new_instance_id("discovery")
  gm = _guild(guild.id)
  discovery = {
    **spawn,
    "discovery_id": discovery_id,
    "item": item,
    "recipient_id": target.id,
    "claim_expires_at": claim_expires,
    "item_expires_at": item_expires,
    "delivered_at": _now(),
    "state": "UNCLAIMED",
  }
  _record_discovery(guild.id, discovery)
  gm["pending_dm_claim"] = discovery
  save_item_data()

  text = spawn.get("message", "").strip()
  lines = [
    f"**{target.mention}**",
    "",
    "**SECRET DISCOVERY**",
    "",
    f"**{item.get('name', 'Unknown')}**",
    f"Type: **{str(item.get('category', item.get('type', 'Item'))).title()}**",
    f"Rarity: **{item.get('rarity', 'Common')}**",
    f"{item.get('description', '') or 'No description.'}",
  ]
  if text:
    lines.extend(["", text[:1500]])
  lines.extend([
    "",
    f"Claim it with `/claim {item.get('name', 'item')}`",
    f"Available for **{expire_after} seconds** after delivery." if expire_after > 0 else "Available until claimed; this discovery does **not expire**.",
    f"Expires <t:{int(item_expires)}:R>." if item_expires else "Expires **never**.",
    "",
    "*This discovery is private.*",
  ])
  content = "\n".join(lines)
  attachment_url = item.get("attachment_url")
  if attachment_url:
    content += f"\n\n{attachment_url}"
  try:
    sent = await target.send(
      content,
      allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )
    gm["pending_dm_claim"]["message_id"] = sent.id
    gm["pending_dm_claim"]["channel_id"] = sent.channel.id
    save_item_data()
  except (discord.Forbidden, discord.HTTPException):
    gm["dm_spawn"] = None
    save_item_data()
    return False
  return True


async def _deliver_server_spawn(bot, guild, spawn):
  gm = _guild(guild.id)
  existing = gm.get("pending_server_claim")
  if existing and existing.get("discovery_id") and existing.get("state") in {"UNCLAIMED", "ACTIVE"}:
    return True
  channel = bot.get_channel(int(spawn.get("channel_id", 0))) if spawn.get("channel_id") else None
  if channel is None and spawn.get("channel_id"):
    try:
      channel = await bot.fetch_channel(int(spawn.get("channel_id")))
    except Exception:
      channel = None
  if channel is None:
    return False

  item = _instantiate_spawn_item(spawn["template"])
  expire_after = int(spawn.get("expire_after", 0) or 0)
  # The GM-selected expiration controls the entire discovery lifetime.
  # 0 means the discovery does not expire. Do not use a hard-coded 30s claim window.
  item_expires = (_now() + expire_after) if expire_after > 0 else None
  claim_expires = item_expires
  if item_expires:
    item["expires_at"] = item_expires

  discovery_id = _new_instance_id("discovery")
  gm = _guild(guild.id)
  discovery = {
    **spawn,
    "discovery_id": discovery_id,
    "item": item,
    "claim_expires_at": claim_expires,
    "item_expires_at": item_expires,
    "delivered_at": _now(),
    "state": "UNCLAIMED",
  }
  _record_discovery(guild.id, discovery)
  gm["pending_server_claim"] = discovery
  save_item_data()

  text = spawn.get("message", "").strip()
  lines = [
    "@everyone",
    "",
    "**SECRET DISCOVERY**",
    "",
    f"**{item.get('name', 'Unknown')}**",
    f"Type: **{str(item.get('category', item.get('type', 'Item'))).title()}**",
    f"Rarity: **{item.get('rarity', 'Common')}**",
    f"{item.get('description', '') or 'No description.'}",
  ]
  if text:
    lines.extend(["", text[:1500]])
  lines.extend([
    "",
    f"Claim it with `/claim {item.get('name', 'item')}`",
    f"Available for **{expire_after} seconds** after delivery." if expire_after > 0 else "Available until claimed; this discovery does **not expire**.",
    f"Expires <t:{int(item_expires)}:R>." if item_expires else "Expires **never**.",
  ])
  attachment_url = item.get("attachment_url")
  if attachment_url:
    lines.extend(["", attachment_url])
  content = "\n".join(lines)
  try:
    sent = await channel.send(
      content,
      allowed_mentions=discord.AllowedMentions(everyone=True, users=False, roles=False),
    )
    gm["pending_server_claim"]["message_id"] = sent.id
    gm["pending_server_claim"]["channel_id"] = sent.channel.id
    save_item_data()
  except discord.HTTPException:
    gm["server_spawn"] = None
    save_item_data()
    return False
  return True


async def _deliver_consequence(bot, guild, record):
  channel = bot.get_channel(int(record.get('channel_id', 0))) if record.get('channel_id') else None
  if channel is None:
    return False
  text = str(record.get('text', '')).strip()
  if not text:
    return False
  target_id = record.get('target_user_id')
  prefix = f"<@{int(target_id)}> " if target_id else ""
  await channel.send(f"{prefix}{text[:1900]}")
  # Consequences become part of campaign history once they occur.
  st = item_state(guild.id)
  mem = st.setdefault('campaign_memory', {})
  mem.setdefault('records', []).append({
    'type': 'consequence',
    'summary': text[:1000],
    'target_user_id': target_id,
    'channel_id': channel.id,
    'at': datetime.now(timezone.utc).isoformat(),
  })
  mem['records'] = mem['records'][-1000:]
  return True

async def spawn_loop(bot):
  await bot.wait_until_ready()
  while not bot.is_closed():
    now = _now()
    changed = False
    for guild in bot.guilds:
      g = _guild(guild.id)
      # Remove expired GM DM-spawned items from inventories/possessions.
      state = item_state(guild.id)
      # Persisted discoveries are authoritative. Expire them on the server side
      # so an old copied /claim command can never revive a dead discovery.
      gm = _guild(guild.id)
      for pending_key in ("pending_dm_claim", "pending_server_claim"):
        pending = gm.get(pending_key)
        if not pending or pending.get("state") not in {"UNCLAIMED", "ACTIVE"}:
          continue
        # Migrate older discoveries that used the old hard-coded 30-second
        # claim timer. The GM-selected item_expires_at is authoritative.
        if "item_expires_at" in pending:
          desired_expiry = float(pending.get("item_expires_at", 0) or 0)
          if float(pending.get("claim_expires_at", 0) or 0) != desired_expiry:
            pending["claim_expires_at"] = desired_expiry
            changed = True
        claim_expires_at = float(pending.get("claim_expires_at", 0) or 0)
        if claim_expires_at > 0 and now >= claim_expires_at:
          await _expire_discovery(guild, pending)
          gm[pending_key] = None
          changed = True
      inventories = state.get("inventories", {})
      possessions = state.get("possessions", {})
      for uid, inv in list(inventories.items()):
        kept = []
        for entry in inv:
          expires_at = entry.get("expires_at")
          if expires_at and now >= float(expires_at):
            possessions.pop(entry.get("id"), None)
          else:
            kept.append(entry)
        inventories[uid] = kept
      for key, deliver in (("dm_spawn", _deliver_dm_spawn), ("server_spawn", _deliver_server_spawn)):
        spawn = g.get(key)
        if not isinstance(spawn, dict):
          continue
        if now < float(spawn.get("execute_at", 0) or 0):
          continue
        try:
          ok = await deliver(bot, guild, spawn)
          if not ok:
            print(f"[gm-spawn] Could not deliver {key} in {guild.name}")
        except Exception as exc:
          print(f"[gm-spawn] {key} failed in {guild.name}: {type(exc).__name__}: {exc}")
        g["spawn_history"].append({"type": key, "at": now, "item": spawn.get("template", {}).get("name"), "user_id": spawn.get("user_id"), "channel_id": spawn.get("channel_id"), "discovery_id": (g.get("pending_dm_claim") or g.get("pending_server_claim") or {}).get("discovery_id")})
        g["spawn_history"] = g["spawn_history"][-50:]
        g[key] = None
        changed = True
      remaining = []
      for consequence in list(g.get("consequence_queue", [])):
        if not isinstance(consequence, dict) or now < float(consequence.get("execute_at", 0) or 0):
          remaining.append(consequence)
          continue
        try:
          ok = await _deliver_consequence(bot, guild, consequence)
          if not ok:
            print(f"[gm-consequence] Could not deliver consequence in {guild.name}")
        except Exception as exc:
          print(f"[gm-consequence] Failed in {guild.name}: {type(exc).__name__}: {exc}")
        changed = True
      g["consequence_queue"] = remaining[-50:]
    if changed:
      save_item_data()
    await asyncio.sleep(2)

def _now():
  return datetime.now(timezone.utc).timestamp()


def _safe_vg(value, default=0):
  """Convert old/new bounty reward formats into a safe integer VG amount."""
  try:
    if isinstance(value, bool):
      return default
    if isinstance(value, (int, float)):
      return max(0, int(value))
    text = str(value or "").strip().replace(",", "")
    if text.upper().endswith("VG"):
      text = text[:-2].strip()
    return max(0, int(float(text))) if text else default
  except (TypeError, ValueError, OverflowError):
    return default


def _normalize_bounty_record(raw, index=0):
  """Return a safe, display-ready bounty record or None for unusable data."""
  if not isinstance(raw, dict):
    return None

  b = raw
  bounty_id = str(b.get("id") or f"legacy-bounty-{index}").strip()
  b["id"] = bounty_id or f"legacy-bounty-{index}"
  b["reward_currency"] = "vip" if str(b.get("reward_currency", "vg")).lower() == "vip" else "vg"
  if b["reward_currency"] == "vip":
    b["reward_vip"] = max(0, int(b.get("reward_vip", b.get("reward", 0)) or 0))
    b["reward_vg"] = 0
    b["reward"] = b["reward_vip"]
  else:
    b["reward_vg"] = _safe_vg(b.get("reward_vg", b.get("reward", 0)))
    b["reward_vip"] = 0
    b["reward"] = b["reward_vg"]

  status = str(b.get("status", "open") or "open").strip().lower()
  b["status"] = status if status in {"open", "pending", "completed", "cancelled"} else "open"

  b["target"] = str(b.get("target") or "Unknown").strip()[:100] or "Unknown"
  b["description"] = str(b.get("description") or "No description provided.").strip()[:800]
  b["faction"] = str(b.get("faction") or "").strip()[:100]

  target_type = str(b.get("target_type", "npc") or "npc").strip().lower()
  b["target_type"] = "player" if target_type == "player" else "npc"

  for key in ("target_user_id", "claimed_by", "created_by", "completed_by"):
    if b.get(key) in (None, "", 0):
      continue
    try:
      b[key] = int(b[key])
    except (TypeError, ValueError):
      b[key] = None

  return b


def _normalize_bounties(g):
  """Normalize the entire bounty collection without allowing one bad record to break the board."""
  raw_rows = g.get("bounties", [])
  if not isinstance(raw_rows, list):
    raw_rows = []
  rows = []
  used_ids = set()
  for index, raw in enumerate(raw_rows):
    normalized = _normalize_bounty_record(raw, index)
    if normalized is None:
      continue
    base_id = str(normalized.get("id") or f"legacy-bounty-{index}")
    bounty_id = base_id
    suffix = 2
    while bounty_id in used_ids:
      bounty_id = f"{base_id}-{suffix}"
      suffix += 1
    normalized["id"] = bounty_id
    used_ids.add(bounty_id)
    rows.append(normalized)
  g["bounties"] = rows
  return rows



def _bounty(g, bounty_id):
  """Return a normalized bounty by ID without ever raising on legacy data."""
  wanted = str(bounty_id or "").strip()
  if not wanted:
    return None
  rows = _normalize_bounties(g)
  wanted_cf = wanted.casefold()
  for b in rows:
    if str(b.get("id", "")).casefold() == wanted_cf:
      return b
  return None

def _party_for(g, user_id):
  pid = g["party_members"].get(str(user_id))
  return g["parties"].get(pid) if pid else None


def _party_id(g):
  return f"party-{uuid.uuid4().hex[:8]}"


TARGET_CHOICES = [
  app_commands.Choice(name="Player", value="player"),
  app_commands.Choice(name="NPC", value="npc"),
]

BOUNTY_CURRENCY_CHOICES = [
  app_commands.Choice(name="VG", value="vg"),
  app_commands.Choice(name="VIP", value="vip"),
]
BOUNTY_CURRENCY_CHOICES = [
  app_commands.Choice(name="VG", value="vg"),
  app_commands.Choice(name="VIP", value="vip"),
]



@ADMIN_GAME_GROUP.command(name="start", description="GM: start the game now or schedule it, with an optional session briefing.")
@app_commands.describe(minutes_from_now="Optional delay in minutes. Leave blank to start immediately.", today="Optional briefing explaining what is happening in today's session.", title="Title for this specific game.", players="Optional comma-separated player mentions/IDs. Leave blank to notify all non-admin players.", check_in_minutes="How many minutes players have to check in after the game starts. Optional; defaults to 5.")
async def start_game(interaction: discord.Interaction, minutes_from_now: int | None = None, today: str | None = None, title: str | None = None, players: str | None = None, check_in_minutes: int | None = 5):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  # A game start can DM many players and send public notifications, which can
  # take longer than Discord's interaction acknowledgement window.
  await interaction.response.defer(ephemeral=True)
  if minutes_from_now is not None and minutes_from_now < 1:
    return await interaction.followup.send("Minutes from now must be at least 1.", ephemeral=True)
  if check_in_minutes is None:
    check_in_minutes = 5
  if check_in_minutes < 0 or check_in_minutes > 1440:
    return await interaction.followup.send("Check-in time must be between 0 and 1440 minutes.", ephemeral=True)
  if today and len(today) > 1500:
    return await interaction.followup.send("Today's briefing must be 1500 characters or fewer.", ephemeral=True)
  if title and len(title) > 100:
    return await interaction.followup.send("The session title must be 100 characters or fewer.", ephemeral=True)
  guild = interaction.guild
  g = _guild(guild.id)
  player_ids = []
  if players:
    selected, missing = await resolve_members(guild, players)
    selected = [m for m in selected if not m.bot and not _is_gm_member(m)]
    if missing:
      return await interaction.followup.send("I couldn't resolve these players: " + ", ".join(missing[:10]), ephemeral=True)
    if not selected:
      return await interaction.followup.send("No valid non-admin players were selected.", ephemeral=True)
    player_ids = [m.id for m in selected]
  briefing = {"today": today or "", "title": title or "", "player_ids": player_ids, "check_in_minutes": int(check_in_minutes)}
  if minutes_from_now is not None:
    run_at = _now() + (minutes_from_now * 60)
    g["scheduled_game_start"] = {"run_at": run_at, "channel_id": int(GAME_CHANNEL_ID), "scheduled_by": interaction.user.id, **briefing}
    save_item_data()
    channel = guild.get_channel(int(GAME_CHANNEL_ID))
    if channel is not None:
      recipients = [guild.get_member(uid) for uid in player_ids] if player_ids else [m for m in guild.members if not m.bot and not _is_gm_member(m)]
      mentions = " ".join(m.mention for m in recipients if m)
      run_at = int(run_at)
      await channel.send(f"#  **{title or 'Untitled Session'}**\n{mentions}\n**Starts:** <t:{run_at}:F> (<t:{run_at}:R>)\n**Check-in:** Players must check in within {check_in_minutes} minute(s) after the game starts.", allowed_mentions=discord.AllowedMentions(users=True, everyone=False, roles=False))
    await interaction.followup.send(
      f"**{guild.name}** is scheduled to start in **{minutes_from_now} minute(s)**.\n\n"
      f"**Title:** {title or 'Untitled Session'}\n"
      f"**Today's briefing:** {today or 'None provided'}\n\n"
      f"**Check-in window:** {check_in_minutes} minute(s)\n"
      "Administrators will not be pinged when the timer ends. The scheduled start will use the channel where this command was run.",
      ephemeral=True,
    )
    _schedule_game_start(_BOT, guild.id, minutes_from_now * 60)
    return
  await _start_game_now(guild, int(GAME_CHANNEL_ID), started_by=interaction.user.id, briefing=briefing)
  await interaction.followup.send(f"**{guild.name}** has started. Non-administrator members have been notified.", ephemeral=True)


class GMAdminDMView(discord.ui.View):
  def __init__(self, guild_id: int):
    super().__init__(timeout=None)
    self.guild_id = guild_id
    button = discord.ui.Button(label='Open GM Panel', style=discord.ButtonStyle.primary, custom_id=f'anonymous_gm_panel:{guild_id}', row=0)
    button.callback = self.open_panel
    self.add_item(button)

  async def open_panel(self, interaction: discord.Interaction):
    if interaction.guild is not None:
      if not is_staff(interaction):
        return await interaction.response.send_message('GM/admin only.', ephemeral=True)
    elif not getattr(interaction.user, 'guild_permissions', None):
      # DM interactions do not carry guild permissions. Verify membership/role against the target guild.
      guild = interaction.client.get_guild(self.guild_id)
      member = guild.get_member(interaction.user.id) if guild else None
      if not member or not (member.guild_permissions.manage_guild or member.guild_permissions.manage_channels or member.guild_permissions.administrator):
        return await interaction.response.send_message('GM/admin only.', ephemeral=True)
    from .admin_ui import AdminPanelView
    view = AdminPanelView(self.guild_id, interaction.user.id)
    view._bot = interaction.client
    await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)


class SessionAttendanceView(discord.ui.View):
  def __init__(self, guild_id: int):
    super().__init__(timeout=None)
    self.guild_id = guild_id

    check_in_btn = discord.ui.Button(label="Check In", style=discord.ButtonStyle.success, custom_id=f"rpg_checkin:{guild_id}", row=0)
    check_out_btn = discord.ui.Button(label="Check Out", style=discord.ButtonStyle.secondary, custom_id=f"rpg_checkout:{guild_id}", row=0)
    main_btn = discord.ui.Button(label="Main", style=discord.ButtonStyle.primary, custom_id=f"rpg_main:{guild_id}", row=0)
    economy_btn = discord.ui.Button(label="Economy", style=discord.ButtonStyle.primary, custom_id=f"rpg_economy:{guild_id}", row=0)
    bounty_btn = discord.ui.Button(label="Bounties", style=discord.ButtonStyle.primary, custom_id=f"rpg_bounties:{guild_id}", row=0)

    check_in_btn.callback = self._check_in
    check_out_btn.callback = self._check_out
    main_btn.callback = self._main
    economy_btn.callback = self._economy
    bounty_btn.callback = self._bounties

    self.add_item(check_in_btn)
    self.add_item(check_out_btn)
    self.add_item(main_btn)
    self.add_item(economy_btn)
    self.add_item(bounty_btn)

  async def _main(self, interaction: discord.Interaction):
    if interaction.guild is None or interaction.guild.id != self.guild_id:
      return await interaction.response.send_message("This session belongs to another server.", ephemeral=True)
    from .main_ui import MainView
    view = MainView(self.guild_id, interaction.user.id, "home")
    await interaction.response.send_message(view.content(), view=view, ephemeral=True)

  async def _economy(self, interaction: discord.Interaction):
    if interaction.guild is None or interaction.guild.id != self.guild_id:
      return await interaction.response.send_message("This session belongs to another server.", ephemeral=True)
    from .main_ui import MainView
    view = MainView(self.guild_id, interaction.user.id, "economy")
    await interaction.response.send_message(view.content(), view=view, ephemeral=True)

  async def _bounties(self, interaction: discord.Interaction):
    if interaction.guild is None or interaction.guild.id != self.guild_id:
      return await interaction.response.send_message("This session belongs to another server.", ephemeral=True)
    from .main_ui import MainView
    view = MainView(self.guild_id, interaction.user.id, "bounties")
    await interaction.response.send_message(view.content(), view=view, ephemeral=True)

  async def _check_in(self, interaction: discord.Interaction):
    if interaction.user.bot: return await interaction.response.send_message("Bots cannot check in.", ephemeral=True)
    if interaction.user.guild_permissions.administrator:
      return await interaction.response.send_message("Administrators do not use player attendance. The GM/admin team is excluded from attendance.", ephemeral=True)
    guild = _BOT.get_guild(self.guild_id) if _BOT else None
    if not guild: return await interaction.response.send_message("The campaign server could not be found.", ephemeral=True)
    g = _guild(self.guild_id)
    if not g.get("game_started"): return await interaction.response.send_message("This session is no longer live.", ephemeral=True)
    session = g.get("current_session", {})
    deadline = float(session.get("check_in_deadline", 0) or 0)
    selected_ids = {int(x) for x in session.get("player_ids", []) if str(x).isdigit()}
    if selected_ids and interaction.user.id not in selected_ids:
      return await interaction.response.send_message("You were not selected for this specific game.", ephemeral=True)
    if deadline and _now() > deadline and str(interaction.user.id) not in g.get("attendance", {}):
      return await interaction.response.send_message(f"Check-in is closed. The {int(session.get('check_in_minutes', 5) or 5)}-minute join window for this game has expired.", ephemeral=True)
    uid = str(interaction.user.id); previous = g["attendance"].get(uid, {})
    g["attendance"][uid] = {"status":"checked_in", "checked_in_at":previous.get("checked_in_at", _now()), "checked_out_at":None, "display_name":interaction.user.display_name}
    save_item_data(); await interaction.response.send_message(" You are **checked in** for today's session.", ephemeral=True)

  async def _check_out(self, interaction: discord.Interaction):
    if interaction.user.bot: return await interaction.response.send_message("Bots cannot check out.", ephemeral=True)
    if interaction.user.guild_permissions.administrator:
      return await interaction.response.send_message("Administrators do not use player attendance. The GM/admin team is excluded from attendance.", ephemeral=True)
    g = _guild(self.guild_id); uid = str(interaction.user.id); record = g["attendance"].get(uid, {})
    record.update({"status":"checked_out", "checked_out_at":_now(), "display_name":interaction.user.display_name})
    g["attendance"][uid] = record; save_item_data()
    await interaction.response.send_message(" You are **checked out** of today's session.", ephemeral=True)

async def _notify_non_admins(guild: discord.Guild, channel: discord.abc.Messageable, *, scheduled: bool = False, briefing: dict | None = None):
  # Fetch the current member list so DMs are not limited by an incomplete local cache.
  try:
    members = [m async for m in guild.fetch_members(limit=None)]
  except (discord.Forbidden, discord.HTTPException) as exc:
    print(f"Could not fetch members for game-start notifications in {guild.name}: {type(exc).__name__}: {exc}")
    members = list(guild.members)

  selected_ids = {int(x) for x in briefing.get("player_ids", []) if str(x).isdigit()}
  recipients = [m for m in members if not m.bot and not _is_gm_member(m) and (not selected_ids or m.id in selected_ids)]
  mentions = [m.mention for m in recipients]
  allowed = discord.AllowedMentions(users=True, everyone=False, roles=False)
  briefing = briefing or {}
  check_in_minutes = int(briefing.get("check_in_minutes", 5) or 5)
  title_text = briefing.get("title") or "Today's Session"
  today_text = briefing.get("today") or "No session briefing was provided. Await the Game Master's instructions."
  title = f"# **{guild.name} HAS STARTED**"
  body = (
    f"The GM has started **{guild.name}**. Check your DMs for your personal notification."
    if not scheduled else
    f"The scheduled start time for **{guild.name}** has arrived. The game is now live. Check your DMs for your personal notification."
  )

  chunks, current = [], ""
  for mention in mentions:
    candidate = f"{current} {mention}".strip()
    if len(candidate) > 1700:
      if current:
        chunks.append(current)
      current = mention
    else:
      current = candidate
  if current:
    chunks.append(current)

  first_content = f"{title}\n{body}\n\n**{title_text}**\n{today_text}\n\n**CHECK-IN WINDOW: {check_in_minutes} MINUTES**\nPlayers must check in within {check_in_minutes} minute(s) of the game start to join this specific game."
  if chunks:
    first_content += f"\n\n{chunks[0]}"
  await channel.send(first_content, allowed_mentions=allowed, view=SessionAttendanceView(guild.id))
  for chunk in chunks[1:]:
    await channel.send(chunk, allowed_mentions=allowed)

  sent = failed = 0
  for member in recipients:
    dm_text = (
      f"**{guild.name} HAS STARTED**\n\n"
      f"**{title_text}**\n{today_text}\n\n"
      f"Head to the RPG channels and await further instructions from the Game Master.\n\n"
      f"**Joining is automatic:** send a meaningful message in the active game channel and you are marked as present.\n\n"
      f"Joining is automatic, including for late arrivals. Meaningful activity in the active game channel marks you present.\n\n"
      f"Use `/attendance check-in` only as an optional manual override, and `/attendance check-out` when you leave."
    )
    try:
      await member.send(dm_text, view=SessionAttendanceView(guild.id))
      sent += 1
    except discord.Forbidden:
      failed += 1
      print(f"Could not DM {member} ({member.id}): DMs are disabled or unavailable.")
    except discord.HTTPException as exc:
      failed += 1
      print(f"Could not DM {member} ({member.id}): {type(exc).__name__}: {exc}")
    await asyncio.sleep(0.15)

  return sent, failed, len(recipients)


def _sync_web_session(guild_id, live):
  try:
    from .. import web_app
    web_app._update_world_state(lambda st: st.update({"session_live": bool(live)}))
    web_app._broadcast("session_changed", {"live": bool(live)})
  except Exception as exc:
    print(f"[web] session sync warning: {type(exc).__name__}: {exc}")

async def _end_game_from_web(guild: discord.Guild, started_by: int):
  """End a session requested from the web GM panel using the same Discord game channel.

  The web control is deliberately a thin adapter around the Discord session state;
  it never locks arbitrary server channels. AI recap/review remains a Discord-command
  feature, while the public GAME ENDED notification is always emitted.
  """
  g = _guild(guild.id)
  if not g.get("game_started"):
    # A previous web-only/partial start can leave Discord unlocked while the
    # Discord session record is absent. End must always lock the real channel.
    await _set_game_channel_lock(guild, True, int(GAME_CHANNEL_ID))
    _sync_web_session(guild.id, False)
    return {"already_closed": True, "channel_id": str(GAME_CHANNEL_ID)}
  channel_id = int(GAME_CHANNEL_ID)
  await _set_game_channel_lock(guild, True, channel_id)
  ended = _now()
  session = dict(g.get("current_session", {}))
  session.update({"session_number": g.get("session_number", 0), "ended_at": ended, "attendance": dict(g.get("attendance", {})), "events": list(g.get("session_events", [])), "next_hint": ""})
  g.setdefault("session_history", []).append(session)
  g["session_history"] = g["session_history"][-50:]
  campaign_store.record_session(guild.id, session)
  g["game_started"] = False
  g["game_ended_at"] = ended
  g["attendance"] = {}
  g["session_events"] = []
  g.pop("current_session", None)
  save_item_data()
  _sync_web_session(guild.id, False)
  channel = guild.get_channel(channel_id)
  if channel is None:
    try: channel = await guild.fetch_channel(channel_id)
    except Exception: channel = None
  if channel is not None:
    title = session.get("title") or f"Session #{session.get('session_number')}"
    player_ids = {int(x) for x in session.get("player_ids", []) if str(x).isdigit()}
    if not player_ids:
      player_ids = {int(x) for x in session.get("attendance", {}).keys() if str(x).isdigit()}
    members = [guild.get_member(x) for x in player_ids]
    members = [m for m in members if m and not m.bot and not m.guild_permissions.administrator]
    mentions = " ".join(m.mention for m in members)
    announcement = f"# **GAME ENDED**\n\n**{title}** has ended. Thank you for playing."
    if mentions: announcement += f"\n\n{mentions}"
    announcement += "\n\n**How was the session?** Use the buttons below to rate it from 1–10 or leave a suggestion."
    try:
      await channel.send(
        announcement,
        allowed_mentions=discord.AllowedMentions(users=True, everyone=False, roles=False),
        view=SessionReviewView(guild.id, session.get("session_number", 0)),
      )
    except (discord.Forbidden, discord.HTTPException) as exc:
      # The lock/state change has already succeeded. A missing permission to
      # post the optional review prompt must never turn a completed session end
      # into a failed web action that leaves the UI claiming it is still live.
      print(f"Session end announcement warning for {guild.id}: {type(exc).__name__}: {exc}")
      return {"channel_id": str(channel_id), "already_closed": False, "announcement_failed": True}
  return {"channel_id": str(channel_id), "already_closed": False, "announcement_failed": False}

async def _start_game_now(guild: discord.Guild, channel_id: int | None = None, scheduled: bool = False, started_by: int | None = None, briefing: dict | None = None):
  g = _guild(guild.id)
  schedule = g.get("scheduled_game_start", {})
  briefing = briefing or {"today": schedule.get("today", ""), "title": schedule.get("title", ""), "check_in_minutes": schedule.get("check_in_minutes", 5)}
  briefing["check_in_minutes"] = int(briefing.get("check_in_minutes", 5) or 5)
  try:
    await _set_game_channel_lock(guild, False, channel_id)
  except Exception as exc:
    print(f"Could not unlock server for {guild.id}: {type(exc).__name__}: {exc}")
    raise RuntimeError(f"Could not unlock configured game channel {GAME_CHANNEL_ID}: {exc}") from exc
  g["game_started"] = True
  g["game_started_at"] = _now()
  g["game_started_by"] = started_by if started_by is not None else schedule.get("scheduled_by")
  g["current_session"] = {"channel_id": int(channel_id) if channel_id is not None else None, "title": briefing.get("title", ""), "today": briefing.get("today", ""), "player_ids": list(briefing.get("player_ids", [])), "started_at": g["game_started_at"], "started_by": g["game_started_by"], "check_in_minutes": int(briefing.get("check_in_minutes", 5) or 5), "check_in_deadline": g["game_started_at"] + (int(briefing.get("check_in_minutes", 5) or 5) * 60)}
  g["session_events"] = []
  g["attendance"] = {}
  # If players already began roleplaying by typing in Discord before the formal
  # /game start system was used, reserve Session #1 for that organic beginning.
  # The first formal /game start then becomes Session #2 instead of overwriting
  # the real first session.
  if int(g.get("session_number", 0)) == 0:
    try:
      lore_settings = memory._store(guild.id).setdefault("settings", {})
      if lore_settings.get("campaign_origin_at"):
        g["session_number"] = 1
    except Exception:
      pass
  g["session_number"] = int(g.get("session_number", 0)) + 1
  g.pop("scheduled_game_start", None)
  save_item_data()
  _sync_web_session(guild.id, True)

  # Use the exact active game channel. Falling back to the system channel would
  # claim that a session started while leaving the actual RPG channel silent.
  announcement_channel_id = int(channel_id) if channel_id is not None else int(GAME_CHANNEL_ID)
  channel = guild.get_channel(announcement_channel_id)
  if channel is None:
    try:
      channel = await guild.fetch_channel(announcement_channel_id)
    except Exception as exc:
      raise RuntimeError(f"Could not find the active game channel for the session announcement: {exc}") from exc
  if not isinstance(channel, discord.TextChannel):
    raise RuntimeError("The active game channel is not a text channel, so the session announcement could not be sent.")
  try:
    sent, failed, total = await _notify_non_admins(guild, channel, scheduled=scheduled, briefing=briefing)
    await _dm_admins_gm_panel(guild, briefing.get("title") or "Untitled Session")
    print(f"Game start notifications for {guild.name}: {sent} DMs sent, {failed} failed, {total} non-admin members.")
    return {"channel_id": str(channel.id), "dm_sent": sent, "dm_failed": failed, "players_notified": total}
  except (discord.Forbidden, discord.HTTPException) as exc:
    # The caller must be able to surface this to the GM. A silent web-only
    # start is worse than a failed start because players never receive notice.
    g["game_started"] = False
    g["game_ended_at"] = _now()
    g["attendance"] = {}
    g["session_events"] = []
    g.pop("current_session", None)
    try:
      await _set_game_channel_lock(guild, True, announcement_channel_id)
    finally:
      save_item_data()
      _sync_web_session(guild.id, False)
    raise RuntimeError(f"Could not publish the game-start announcement in #{getattr(channel, 'name', channel.id)}: {exc}") from exc


async def _dm_non_admins(guild: discord.Guild, message: str):
  for member in [m for m in guild.members if not m.bot and not m.guild_permissions.administrator]:
    try: await member.send(message)
    except (discord.Forbidden, discord.HTTPException): pass
    await asyncio.sleep(0.1)


async def _dm_admins_gm_panel(guild: discord.Guild, session_title: str = "Untitled Session"):
  message = (f"Game started in **{guild.name}**.\n\n"
             f"Session: **{session_title}**\n"
             "Use the button below to open the GM control panel. This control is sent only to server administrators/GM staff.")
  for member in [m for m in guild.members if not m.bot and _is_gm_member(m)]:
    try:
      await member.send(message, view=GMAdminDMView(guild.id))
    except (discord.Forbidden, discord.HTTPException):
      pass
    await asyncio.sleep(0.1)

@ADMIN_GAME_GROUP.command(name="cancel", description="GM: cancel the scheduled game start.")
async def cancel_game(interaction: discord.Interaction):
  if interaction.guild is None or not is_staff(interaction): return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  g = _guild(interaction.guild.id)
  if not g.get("scheduled_game_start"): return await interaction.response.send_message("There is no scheduled game start to cancel.", ephemeral=True)
  g.pop("scheduled_game_start", None); save_item_data()
  task = _SCHEDULED_GAME_TASKS.pop(interaction.guild.id, None)
  if task and not task.done(): task.cancel()
  await interaction.response.send_message("The scheduled game start has been cancelled.", ephemeral=True)

@ATTENDANCE_GROUP.command(name="check-in", description="Check yourself into today's RPG session.")
async def check_in(interaction: discord.Interaction):
  guild = interaction.guild
  if guild is None or interaction.user.bot:
    return await interaction.response.send_message("This command is for players in a server.", ephemeral=True)
  member = guild.get_member(interaction.user.id)
  if member is None:
    try:
      member = await guild.fetch_member(interaction.user.id)
    except (discord.NotFound, discord.HTTPException):
      member = None
  if member is None:
    return await interaction.response.send_message("I couldn't verify your server membership.", ephemeral=True)
  if member.guild_permissions.administrator:
    return await interaction.response.send_message("This command is for players in a server.", ephemeral=True)
  g = _guild(interaction.guild.id)
  if not g.get("game_started"): return await interaction.response.send_message("There is no live game session right now.", ephemeral=True)
  session = g.get("current_session", {})
  deadline = float(session.get("check_in_deadline", 0) or 0)
  selected_ids = {int(x) for x in session.get("player_ids", []) if str(x).isdigit()}
  if selected_ids and interaction.user.id not in selected_ids:
    return await interaction.response.send_message("You were not selected for this specific game.", ephemeral=True)
  if deadline and _now() > deadline and str(interaction.user.id) not in g.get("attendance", {}):
    return await interaction.response.send_message(f"Check-in is closed. The {int(session.get('check_in_minutes', 5) or 5)}-minute join window for this game has expired.", ephemeral=True)
  uid = str(interaction.user.id); previous = g["attendance"].get(uid, {})
  g["attendance"][uid] = {"status":"checked_in", "checked_in_at":previous.get("checked_in_at", _now()), "checked_out_at":None, "display_name":interaction.user.display_name}
  save_item_data(); await interaction.response.send_message(" You are **checked in** for today's session.", ephemeral=True)

@ATTENDANCE_GROUP.command(name="check-out", description="Check yourself out of today's RPG session.")
@app_commands.describe(reason="Optional reason for leaving the session.")
async def check_out(interaction: discord.Interaction, reason: str | None = None):
  guild = interaction.guild
  if guild is None or interaction.user.bot:
    return await interaction.response.send_message("This command is for players in a server.", ephemeral=True)
  member = guild.get_member(interaction.user.id)
  if member is None:
    try:
      member = await guild.fetch_member(interaction.user.id)
    except (discord.NotFound, discord.HTTPException):
      member = None
  if member is None:
    return await interaction.response.send_message("I couldn't verify your server membership.", ephemeral=True)
  if member.guild_permissions.administrator:
    return await interaction.response.send_message("This command is for players in a server.", ephemeral=True)
  g = _guild(interaction.guild.id); uid = str(interaction.user.id); record = g["attendance"].get(uid, {})
  record.update({"status":"checked_out", "checked_out_at":_now(), "display_name":interaction.user.display_name})
  if reason: record["reason"] = reason[:500]
  g["attendance"][uid] = record; save_item_data()
  await interaction.response.send_message("You are **checked out** of today's session.", ephemeral=True)

@ADMIN_ATTENDANCE_GROUP.command(name="view", description="GM: view today's RPG attendance.")
async def attendance(interaction: discord.Interaction):
  if interaction.guild is None or not is_staff(interaction): return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  g = _guild(interaction.guild.id); records = g.get("attendance", {})
  checked_in = [uid for uid,r in records.items() if r.get("status")=="checked_in"]; checked_out = [uid for uid,r in records.items() if r.get("status")=="checked_out"]
  members = [m for m in interaction.guild.members if not m.bot and not m.guild_permissions.administrator]; pending = [m for m in members if str(m.id) not in records]
  lines=["###  Today's Attendance",f"**Expected:** {len(members)}",f"**Checked In:** {len(checked_in)}",f"**Checked Out:** {len(checked_out)}",f"**Not Responded:** {len(pending)}"]
  if checked_in: lines.append("\n** Checked In**\n"+", ".join(f"<@{u}>" for u in checked_in))
  if checked_out: lines.append("\n** Checked Out**\n"+", ".join(f"<@{u}>" for u in checked_out))
  if pending: lines.append("\n** Not Responded**\n"+", ".join(m.mention for m in pending[:40]))
  await interaction.response.send_message("\n".join(lines), ephemeral=True)


class SessionReviewModal(discord.ui.Modal, title="Session Review"):
  rating = discord.ui.TextInput(label="Rating (1-10)", placeholder="10", max_length=2, required=True)
  feedback = discord.ui.TextInput(label="Feedback", placeholder="What did you think of the session?", style=discord.TextStyle.paragraph, max_length=1000, required=False)

  def __init__(self, guild_id: int, session_number: int):
    super().__init__()
    self.guild_id = guild_id
    self.session_number = session_number

  async def on_submit(self, interaction: discord.Interaction):
    try:
      value = int(str(self.rating.value).strip())
    except ValueError:
      return await interaction.response.send_message("Rating must be a number from 1 to 10.", ephemeral=True)
    if value < 1 or value > 10:
      return await interaction.response.send_message("Rating must be between 1 and 10.", ephemeral=True)
    g = _guild(self.guild_id)
    session = None
    for row in reversed(g.get("session_history", [])):
      if int(row.get("session_number", -1)) == self.session_number:
        session = row
        break
    if session is None:
      return await interaction.response.send_message("That session could not be found.", ephemeral=True)
    reviews = session.setdefault("reviews", {})
    reviews[str(interaction.user.id)] = {
      "rating": value,
      "feedback": str(self.feedback.value or "").strip(),
      "user_id": interaction.user.id,
      "display_name": interaction.user.display_name,
      "submitted_at": _now(),
    }
    save_item_data()
    await interaction.response.send_message(f"Your review for **Session #{self.session_number}** was saved. Thank you.", ephemeral=True)


class SessionSuggestionModal(discord.ui.Modal, title="Session Suggestion"):
  suggestion = discord.ui.TextInput(
    label="Suggestion",
    placeholder="What should we improve or add next time?",
    style=discord.TextStyle.paragraph,
    max_length=1500,
    required=True,
  )

  def __init__(self, guild_id: int, session_number: int):
    super().__init__()
    self.guild_id = guild_id
    self.session_number = session_number

  async def on_submit(self, interaction: discord.Interaction):
    g = _guild(self.guild_id)
    session = None
    for row in reversed(g.get("session_history", [])):
      if int(row.get("session_number", -1)) == self.session_number:
        session = row
        break
    if session is None:
      return await interaction.response.send_message("That session could not be found.", ephemeral=True)
    suggestions = session.setdefault("suggestions", [])
    suggestions.append({
      "user_id": interaction.user.id,
      "display_name": interaction.user.display_name,
      "suggestion": str(self.suggestion.value).strip(),
      "submitted_at": _now(),
    })
    save_item_data()
    await interaction.response.send_message(
      f"Your suggestion for **Session #{self.session_number}** was saved. Thank you.",
      ephemeral=True,
    )


class SessionReviewView(discord.ui.View):
  def __init__(self, guild_id: int, session_number: int):
    super().__init__(timeout=None)
    self.guild_id = guild_id
    self.session_number = session_number

  @discord.ui.button(label="Review / Rate Session", style=discord.ButtonStyle.primary, custom_id="rpg_session_review")
  async def review(self, interaction: discord.Interaction, button: discord.ui.Button):
    if interaction.guild is None or interaction.guild.id != self.guild_id:
      return await interaction.response.send_message("This review belongs to another server.", ephemeral=True)
    await interaction.response.send_modal(SessionReviewModal(self.guild_id, self.session_number))

  @discord.ui.button(label="Suggestion", style=discord.ButtonStyle.secondary, custom_id="rpg_session_suggestion")
  async def suggestion(self, interaction: discord.Interaction, button: discord.ui.Button):
    if interaction.guild is None or interaction.guild.id != self.guild_id:
      return await interaction.response.send_message("This session belongs to another server.", ephemeral=True)
    await interaction.response.send_modal(SessionSuggestionModal(self.guild_id, self.session_number))

@ADMIN_GAME_GROUP.command(name="end", description="GM: end today's RPG session, announce it, and optionally tease the next game.")
@app_commands.describe(next_hint="Optional hint about the next game or what is coming next.")
async def end_game(interaction: discord.Interaction, next_hint: str | None = None):
  if interaction.guild is None or not is_staff(interaction): return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  if next_hint and len(next_hint) > 1200: return await interaction.response.send_message("The next-game hint must be 1200 characters or fewer.", ephemeral=True)
  # The session lock is intentionally limited to the one configured game channel.
  await interaction.response.defer(ephemeral=True, thinking=True)
  g = _guild(interaction.guild.id)
  if not g.get("game_started"): return await interaction.followup.send("There is no live game session to end.", ephemeral=True)
  # Security-critical: lock the ONE configured game channel before changing
  # session state. If Discord rejects the permission change, the game stays
  # active rather than leaving the channel exposed.
  try:
    await _set_game_channel_lock(interaction.guild, True, int(GAME_CHANNEL_ID))
  except Exception as exc:
    print(f"Could not lock game channel for {interaction.guild.id}: {type(exc).__name__}: {exc}")
    return await interaction.followup.send(
      "I could not lock the configured game channel, so the session was NOT ended. "
      "No other channel permissions were changed.",
      ephemeral=True,
    )

  ended=_now(); session=dict(g.get("current_session",{})); session.update({"session_number":g.get("session_number",0),"ended_at":ended,"attendance":dict(g.get("attendance",{})),"events":list(g.get("session_events",[])),"next_hint":next_hint or ""})
  g.setdefault("session_history",[]).append(session); g["session_history"]=g["session_history"][-50:]; campaign_store.record_session(guild.id, session); g["game_started"]=False; g["game_ended_at"]=ended; g["attendance"]={}; g["session_events"]=[]; g.pop("current_session",None)
  save_item_data()
  _sync_web_session(interaction.guild.id, False)

  # Gemini only summarizes recorded material; it never writes the campaign story.
  recap = None
  try:
    recap = await memory.generate_session_recap(interaction.guild.id, session)
    if recap:
      session["ai_recap"] = recap
      # Keep the stored history copy synchronized with the recap.
      for row in reversed(g.get("session_history", [])):
        if int(row.get("session_number", -1)) == int(session.get("session_number", -2)):
          row["ai_recap"] = recap
          break
      save_item_data()
  except Exception as exc:
    print(f"Session recap warning for {interaction.guild.id}: {type(exc).__name__}: {exc}")

  # Independent AI review: GM-only. It can disagree with the GM when the
  # recorded evidence supports a player or identifies a fairness concern.
  post_game_review = None
  try:
    post_game_review = await memory.generate_post_game_review(interaction.guild.id, session)
    if post_game_review:
      session["ai_post_game_review"] = post_game_review
      for row in reversed(g.get("session_history", [])):
        if int(row.get("session_number", -1)) == int(session.get("session_number", -2)):
          row["ai_post_game_review"] = post_game_review
          break
      campaign_store.record_session(interaction.guild.id, session)
      save_item_data()
  except Exception as exc:
    print(f"Post-game AI review warning for {interaction.guild.id}: {type(exc).__name__}: {exc}")

  # Public end-of-game announcement. Mention the players who were part of this specific session.
  channel = interaction.guild.get_channel(int(GAME_CHANNEL_ID))
  player_ids = {int(x) for x in session.get("player_ids", []) if str(x).isdigit()}
  if not player_ids:
    player_ids = {int(uid) for uid in session.get("attendance", {}).keys() if str(uid).isdigit()}
  members = [interaction.guild.get_member(uid) for uid in player_ids]
  members = [m for m in members if m and not m.bot and not m.guild_permissions.administrator]
  mentions = " ".join(m.mention for m in members)
  title = session.get("title") or f"Session #{session.get('session_number')}"
  announcement = f"# **GAME ENDED**\n\n**{title}** has ended. Thank you for playing."
  if mentions:
    announcement += f"\n\n{mentions}"
  if next_hint:
    announcement += f"\n\n**Next game hint:**\n{next_hint}"
  announcement += "\n\n**How was the session?** Use the buttons below to rate it or leave a suggestion."
  if channel is not None:
    await channel.send(announcement, allowed_mentions=discord.AllowedMentions(users=True, everyone=False, roles=False), view=SessionReviewView(interaction.guild.id, session.get("session_number", 0)))
  review_note = ""
  if post_game_review:
    score = post_game_review.get("overall_score", 0)
    confidence = post_game_review.get("evidence_confidence", "unknown")
    summary = str(post_game_review.get("summary") or "No summary provided.")[:1500]
    concerns = post_game_review.get("concerns") or []
    suggestions = post_game_review.get("next_session_suggestions") or []
    lines = [
      f"POST-GAME AI REVIEW — Session #{session.get('session_number')}",
      "",
      f"Overall score: {score}/10",
      f"Evidence confidence: {confidence}",
      "",
      "Summary:",
      summary,
    ]
    if concerns:
      lines += ["", "Potential concerns:"] + [f"- {str(x)[:700]}" for x in concerns[:6]]
    if suggestions:
      lines += ["", "Suggestions for next session:"] + [f"- {str(x)[:700]}" for x in suggestions[:6]]
    lines += ["", "This review is AI analysis of recorded campaign material. It does not change canon."]
    review_text = "\n".join(lines)[:3900]
    try:
      await interaction.followup.send(review_text, ephemeral=True)
    except Exception as exc:
      print(f"Could not send post-game AI review: {type(exc).__name__}: {exc}")
  await interaction.followup.send(f"**Session #{session.get('session_number')} has ended.** The server is now locked until an administrator or the bot owner starts the next game. The public end announcement was posted and session reviews are open.", ephemeral=True)
  if recap:
    # Post the AI recap publicly so everyone who played can see the session
    # summary. It is generated strictly from the recorded session material.
    lines = [
      f"# SESSION {session.get('session_number')} — RECAP",
      "",
      f"**{title}**",
      "",
      "## Major Events",
      *(f"• {x}" for x in recap.get("major_events", []) or ["None recorded."]),
      "",
      "## Characters Involved",
      *(f"• {x}" for x in recap.get("characters_involved", []) or ["None identified."]),
      "",
      "## Major Discoveries",
      *(f"• {x}" for x in recap.get("major_discoveries", []) or ["None recorded."]),
      "",
      "## Unresolved Events",
      *(f"• {x}" for x in recap.get("unresolved_events", []) or ["None recorded."]),
      "",
      "## New Threats",
      *(f"• {x}" for x in recap.get("new_threats", []) or ["None identified."]),
      "",
      f"**Lore indexed:** {len(recap.get('lore_events', []))} high-confidence events",
      "",
      "_AI-generated from the recorded session. It does not create or change campaign canon._",
    ]
    recap_text = "\n".join(lines)
    for i in range(0, len(recap_text), 3900):
      await interaction.channel.send(recap_text[i:i+3900], allowed_mentions=discord.AllowedMentions.none())
  else:
    await interaction.channel.send(
      f"**Session #{session.get('session_number')} recap:** No AI recap was available. "
      "The recorded session events remain available to the GM.",
      allowed_mentions=discord.AllowedMentions.none(),
    )

@ADMIN_SESSION_GROUP.command(name="reviews", description="GM: view ratings and suggestions from a completed session.")
@app_commands.describe(session_number="Optional session number; defaults to the most recent completed session.")
async def session_reviews(interaction: discord.Interaction, session_number: int | None = None):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  history = _guild(interaction.guild.id).get("session_history", [])
  if not history:
    return await interaction.response.send_message("No completed sessions have reviews yet.", ephemeral=True)
  session = next((row for row in reversed(history) if session_number is not None and int(row.get("session_number", -1)) == session_number), None) if session_number is not None else history[-1]
  if session is None:
    return await interaction.response.send_message("That session could not be found.", ephemeral=True)

  reviews = list((session.get("reviews") or {}).values())
  suggestions = session.get("suggestions") or []
  lines = [f"### Session #{session.get('session_number')} Feedback", f"**Reviews:** {len(reviews)}", f"**Suggestions:** {len(suggestions)}"]
  if reviews:
    avg = sum(int(r.get("rating", 0)) for r in reviews) / len(reviews)
    lines.append(f"**Average rating:** {avg:.1f}/10")
    for r in reviews[-15:]:
      feedback = discord.utils.escape_markdown(str(r.get("feedback") or "No written feedback."))
      lines.append(f"**{discord.utils.escape_markdown(str(r.get('display_name') or 'Player'))} — {r.get('rating')}/10**\n{feedback}")
  if suggestions:
    lines.append("\n**Suggestions**")
    for item in suggestions[-15:]:
      text = discord.utils.escape_markdown(str(item.get("suggestion") or ""))
      lines.append(f"**{discord.utils.escape_markdown(str(item.get('display_name') or 'Player'))}:** {text}")
  await interaction.response.send_message("\n\n".join(lines), ephemeral=True)

@ADMIN_SESSION_GROUP.command(name="event", description="GM: add an event to the live session log.")
@app_commands.describe(event="What happened during the session.", category="Optional category for the event.")
async def session_event(interaction: discord.Interaction, event: str, category: str | None = None):
  if interaction.guild is None or not is_staff(interaction): return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  g=_guild(interaction.guild.id)
  if not g.get("game_started"): return await interaction.response.send_message("Start the game before adding session events.", ephemeral=True)
  if len(event)>1000: return await interaction.response.send_message("Event must be 1000 characters or fewer.", ephemeral=True)
  g["session_events"].append({"event":event,"category":category or "General","at":_now(),"by":interaction.user.id}); save_item_data()
  await interaction.response.send_message(" Session event recorded.", ephemeral=True)

@ADMIN_SESSION_GROUP.command(name="summary", description="GM: view a summary of the current or most recent session.")
async def session_summary(interaction: discord.Interaction):
  if interaction.guild is None or not is_staff(interaction): return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  g=_guild(interaction.guild.id); live=bool(g.get("game_started")); session=g.get("current_session")
  if not session and g.get("session_history"): session=g["session_history"][-1]
  if not session: return await interaction.response.send_message("No session history exists yet.", ephemeral=True)
  evs=g.get("session_events",[]) if live else session.get("events",[]); attendance_data=g.get("attendance",{}) if live else session.get("attendance",{})
  participated=len([r for r in attendance_data.values() if r.get("status") in ("checked_in","checked_out")])
  lines=[f"###  SESSION #{session.get('session_number',g.get('session_number',0))}",f"**Title:** {session.get('title') or 'Untitled Session'}",f"**Players Participated:** {participated}",f"**Events:** {len(evs)}"]
  if evs: lines.append("\n**Major Events**"); lines.extend(f"• {e.get('event')}" for e in evs[-15:])
  await interaction.response.send_message("\n".join(lines), ephemeral=True)

@ADMIN_SESSION_GROUP.command(name="history", description="GM: browse recent RPG session history.")
async def sessions(interaction: discord.Interaction):
  if interaction.guild is None or not is_staff(interaction): return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  history=_guild(interaction.guild.id).get("session_history",[])
  if not history: return await interaction.response.send_message("No completed sessions yet.", ephemeral=True)
  lines=["###  Recent Sessions"]
  for s in reversed(history[-10:]):
    started=datetime.fromtimestamp(s.get("started_at",_now()),tz=timezone.utc).strftime("%b %d, %Y %H:%M UTC")
    lines.append(f"**Session #{s.get('session_number')}** — {s.get('title') or 'Untitled'} — {started} — {len(s.get('events',[]))} event(s)")
  await interaction.response.send_message("\n".join(lines), ephemeral=True)


_SCHEDULED_GAME_TASKS = {}
_BOT = None


def _schedule_game_start(bot, guild_id: int, delay: float):
  existing = _SCHEDULED_GAME_TASKS.get(guild_id)
  if existing and not existing.done():
    existing.cancel()

  async def runner():
    try:
      if delay > 3600:
        await asyncio.sleep(delay - 3600)
        guild = bot.get_guild(guild_id) if bot else None
        if guild and _guild(guild_id).get("scheduled_game_start"): await _dm_non_admins(guild, f" **{guild.name}** starts in **1 hour**.")
        await asyncio.sleep(3000)
      elif delay > 600:
        await asyncio.sleep(delay - 600)
      if delay > 600:
        guild = bot.get_guild(guild_id) if bot else None
        if guild and _guild(guild_id).get("scheduled_game_start"): await _dm_non_admins(guild, f" **{guild.name}** starts in **10 minutes**.")
        await asyncio.sleep(600)
      else:
        await asyncio.sleep(max(0, delay))
      if bot is None: return
      guild = bot.get_guild(guild_id)
      if guild is None:
        return
      g = _guild(guild_id)
      schedule = g.get("scheduled_game_start")
      if not schedule:
        return
      await _start_game_now(guild, schedule.get("channel_id"), scheduled=True, briefing={"today": schedule.get("today", ""), "title": schedule.get("title", ""), "player_ids": schedule.get("player_ids", []), "check_in_minutes": schedule.get("check_in_minutes", 5)})
    except asyncio.CancelledError:
      pass
    except Exception as exc:
      print(f"Scheduled game start error for guild {guild_id}: {type(exc).__name__}: {exc}")
    finally:
      _SCHEDULED_GAME_TASKS.pop(guild_id, None)

  task = asyncio.create_task(runner())
  _SCHEDULED_GAME_TASKS[guild_id] = task
  return task


async def restore_scheduled_game_starts(bot):
  for guild in bot.guilds:
    await _ensure_server_lock_state(guild)
  now = _now()
  for guild in bot.guilds:
    try:
      g = _guild(guild.id)
      schedule = g.get("scheduled_game_start")
      if not schedule:
        continue
      remaining = float(schedule.get("run_at", now)) - now
      if remaining <= 0:
        await _start_game_now(guild, schedule.get("channel_id"), scheduled=True, briefing={"today": schedule.get("today", ""), "title": schedule.get("title", ""), "player_ids": schedule.get("player_ids", []), "check_in_minutes": schedule.get("check_in_minutes", 5)})
      else:
        _schedule_game_start(bot, guild.id, remaining)
    except Exception as exc:
      print(f"Could not restore scheduled game start for {guild.id}: {type(exc).__name__}: {exc}")


@GAME_GROUP.command(name="status", description="View whether the server game is live or has a scheduled start.")
async def game_status(interaction: discord.Interaction):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  g = _guild(interaction.guild.id)
  schedule = g.get("scheduled_game_start")
  if schedule and not g.get("game_started"):
    remaining = max(0, int(schedule.get("run_at", _now()) - _now()))
    minutes, seconds = divmod(remaining, 60)
    text = f"**Game Status: SCHEDULED**\nStarts in: **{minutes}m {seconds}s**\n**Title:** {schedule.get('title') or 'Untitled Session'}\nAdministrators will not be pinged."
  elif g.get("game_started"):
    started_at = datetime.fromtimestamp(g.get("game_started_at", _now()), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    session = g.get("current_session", {})
    records = g.get("attendance", {})
    checked = len([r for r in records.values() if r.get("status") == "checked_in"])
    text = f"**Game Status: LIVE**\nStarted: **{started_at}**\n**Title:** {session.get('title') or 'Untitled Session'}\n**Checked In:** **{checked}**\nStarted by: <@{g.get('game_started_by', interaction.user.id)}>"
  else:
    text = "**Game Status: Offline**\nThe game has not been started."
  await interaction.response.send_message(text)



@SESSION_GROUP.command(name="history", description="View recent completed RPG sessions.")
async def player_session_history(interaction: discord.Interaction):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  history = _guild(interaction.guild.id).get("session_history", [])
  if not history:
    return await interaction.response.send_message("No completed sessions yet.", ephemeral=True)
  lines = ["### Recent Sessions"]
  for session in reversed(history[-10:]):
    started = datetime.fromtimestamp(session.get("started_at", _now()), tz=timezone.utc).strftime("%b %d, %Y %H:%M UTC")
    lines.append(f"**Session #{session.get('session_number')}** — {session.get('title') or 'Untitled'} — {started}")
  await interaction.response.send_message("\n".join(lines), ephemeral=True)

@SESSION_GROUP.command(name="status", description="View the current RPG session status.")
async def player_session_status(interaction: discord.Interaction):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  g = _guild(interaction.guild.id)
  if g.get("game_started"):
    session = g.get("current_session", {})
    records = g.get("attendance", {})
    checked = len([r for r in records.values() if r.get("status") == "checked_in"])
    await interaction.response.send_message(
      f"**Session #{g.get('session_number', 0)} — LIVE**\n"
      f"**Title:** {session.get('title') or 'Untitled Session'}\n"
      f"**Checked In:** {checked}", ephemeral=True)
  else:
    await interaction.response.send_message("There is no live RPG session.", ephemeral=True)

@ADMIN_BOUNTY_GROUP.command(name="create", description="GM: create a custom player or NPC bounty.")
@app_commands.describe(
  target="Player to hunt, or the NPC's name.",
  reward="VG reward for completing the bounty.",
  description="What the bounty requires or the story behind it.",
  target_type="Player or NPC.",
  faction="Optional faction that issued the bounty.",
)
@app_commands.choices(target_type=TARGET_CHOICES, currency=BOUNTY_CURRENCY_CHOICES)
async def bounty_create(interaction: discord.Interaction, target: str, reward: int, description: str, target_type: app_commands.Choice[str], faction: str = None, currency: app_commands.Choice[str] | None = None):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  if reward <= 0 or len(target.strip()) > 100 or len(description.strip()) > 800:
    return await interaction.response.send_message("Use a positive reward, target <= 100 chars, and description <= 800 chars.", ephemeral=True)
  target_value = target.strip()
  target_user_id = None
  if target_type.value == "player":
    try:
      member = await interaction.guild.fetch_member(int(target_value.strip("<@!>")))
      target_user_id = member.id
      target_value = member.display_name
    except (ValueError, discord.NotFound, discord.HTTPException):
      return await interaction.response.send_message("For a player bounty, use the player's Discord ID or mention.", ephemeral=True)
  reward_currency = currency.value if currency else "vg"
  b = {
    "id": f"bounty-{uuid.uuid4().hex[:10]}",
    "target_type": target_type.value,
    "target": target_value,
    "target_user_id": target_user_id,
    "reward_currency": reward_currency,
    "reward_vg": reward if reward_currency == "vg" else 0,
    "reward_vip": reward if reward_currency == "vip" else 0,
    "reward": reward,
    "description": description.strip(),
    "faction": (faction or "").strip()[:60],
    "created_by": interaction.user.id,
    "created_at": _now(),
    "status": "open",
    "claimed_by": None,
    "completed_at": None,
  }
  _guild(interaction.guild.id)["bounties"].append(b)
  save_item_data()
  await interaction.response.send_message(f"Created **{b['id']}** — **{target_value}** ({target_type.name}) for **{reward:,} {reward_currency.upper()}.", ephemeral=True)


async def bounty_id_autocomplete(interaction: discord.Interaction, current: str):
  if interaction.guild is None:
    return []
  current = current.casefold()
  rows = _normalize_bounties(_guild(interaction.guild.id))
  choices = []
  for b in rows:
    label = f"{b.get('target','Unknown')} — {b.get('reward',0):,} — {b.get('status','open')}"
    if current in b.get("id", "").casefold() or current in b.get("target", "").casefold():
      choices.append(app_commands.Choice(name=label[:100], value=b.get("id", "")))
  return choices[:25]


@BOUNTY_GROUP.command(name="place", description="Place a bounty on another player or an NPC using VG or VIP.")
@app_commands.describe(target="Player mention/ID or NPC name.", reward="Reward amount in the selected currency.", description="What the bounty is for.", target_type="Player or NPC.", currency="Pay with VG or VIP.")
@app_commands.choices(target_type=TARGET_CHOICES, currency=BOUNTY_CURRENCY_CHOICES)
async def bounty_place(interaction: discord.Interaction, target: str, reward: int, description: str, target_type: app_commands.Choice[str], currency: app_commands.Choice[str]):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  if reward <= 0 or len(target.strip()) > 100 or len(description.strip()) > 800:
    return await interaction.response.send_message("Use a positive reward, target <= 100 chars, and description <= 800 chars.", ephemeral=True)
  target_value = target.strip()
  target_user_id = None
  if target_type.value == "player":
    try:
      member = await interaction.guild.fetch_member(int(target_value.strip("<@!>")))
      if member.bot:
        return await interaction.response.send_message("You cannot place a player bounty on a bot.", ephemeral=True)
      if member.id == interaction.user.id:
        return await interaction.response.send_message("You cannot place a bounty on yourself.", ephemeral=True)
      target_user_id = member.id
      target_value = member.display_name
    except (ValueError, discord.NotFound, discord.HTTPException):
      return await interaction.response.send_message("For a player bounty, use the player's Discord ID or mention.", ephemeral=True)
  from .economy import balance, set_balance, vip_points, _set_vip_cards
  cur = currency.value
  if cur == "vg":
    if balance(interaction.guild.id, interaction.user.id) < reward:
      return await interaction.response.send_message(f"You need **{reward:,} VG** to fund this bounty.", ephemeral=True)
    set_balance(interaction.guild.id, interaction.user.id, balance(interaction.guild.id, interaction.user.id) - reward)
  else:
    if vip_points(interaction.guild.id, interaction.user.id) < reward:
      return await interaction.response.send_message(f"You need **{reward:,} VIP** to fund this bounty.", ephemeral=True)
    _set_vip_cards(interaction.guild.id, interaction.user.id, vip_points(interaction.guild.id, interaction.user.id) - reward)
  b = {
    "id": f"bounty-{uuid.uuid4().hex[:10]}", "target_type": target_type.value, "target": target_value,
    "target_user_id": target_user_id, "reward_currency": cur, "reward_vg": reward if cur == "vg" else 0,
    "reward_vip": reward if cur == "vip" else 0, "reward": reward, "description": description.strip(),
    "faction": "", "created_by": interaction.user.id, "created_at": _now(), "status": "open",
    "claimed_by": None, "completed_at": None, "funded_by_player": True, "funder_id": interaction.user.id,
  }
  _guild(interaction.guild.id)["bounties"].append(b)
  save_item_data()
  await interaction.response.send_message(f" **Bounty placed!**\nTarget: **{target_value}**\nReward: **{reward:,} {cur.upper()}**\nBounty ID: `{b['id']}`", ephemeral=True)


@BOUNTY_GROUP.command(name="info", description="View full details for a bounty publicly.")
@app_commands.describe(bounty_id="The bounty ID or target name.")
async def bounty_info(interaction: discord.Interaction, bounty_id: str):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  g = _guild(interaction.guild.id)
  b = _bounty(g, bounty_id.strip())
  if not b:
    # Friendly fallback: allow the visible target name to be used.
    wanted = bounty_id.strip().casefold()
    b = next((row for row in g.get("bounties", []) if row.get("target", "").casefold() == wanted), None)
  if not b:
    return await interaction.response.send_message("Bounty not found.", ephemeral=True)
  status = b.get("status", "unknown").replace("_", " ").title()
  e = discord.Embed(title=f" BOUNTY — {b.get('target','Unknown')}", colour=discord.Colour.dark_red())
  currency = str(b.get("reward_currency", "vg")).upper()
  amount = int(b.get("reward_vip", 0) if currency == "VIP" else b.get("reward_vg", b.get("reward", 0)) or 0)
  e.add_field(name="Reward", value=f" **{amount:,}** {currency}", inline=True)
  e.add_field(name="Target", value=("Player" if b.get("target_type") == "player" else "NPC"), inline=True)
  e.add_field(name="Status", value=status, inline=True)
  e.add_field(name="Description", value=b.get("description") or "No description provided.", inline=False)
  if b.get("faction"):
    e.add_field(name="Issued By", value=f" {b['faction']}", inline=True)
  if b.get("claimed_by"):
    e.add_field(name="Claimant", value=f"<@{b['claimed_by']}>", inline=True)
  e.set_footer(text=f"Bounty ID: {b.get('id','?')} • Use /bounty list to browse the board")
  await interaction.response.send_message(embed=e)


@ADMIN_BOUNTY_GROUP.command(name="edit", description="GM: edit an open or pending bounty.")
@app_commands.describe(
  bounty_id="The bounty ID.",
  target="New target. For a player, use their Discord ID or mention.",
  reward="New VG reward.",
  description="New bounty description.",
  target_type="Player or NPC.",
  faction="New issuing faction; leave blank to clear it.",
)
@app_commands.choices(target_type=TARGET_CHOICES)
async def bounty_edit(
  interaction: discord.Interaction,
  bounty_id: str,
  target: str = None,
  reward: int = None,
  description: str = None,
  target_type: app_commands.Choice[str] = None,
  faction: str = None,
):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  g = _guild(interaction.guild.id)
  b = _bounty(g, bounty_id.strip())
  if not b:
    return await interaction.response.send_message("Bounty not found.", ephemeral=True)
  if b.get("status") not in {"open", "pending"}:
    return await interaction.response.send_message("Only open or pending bounties can be edited.", ephemeral=True)

  changes = []
  if target_type is not None and target_type.value != b.get("target_type") and target is None:
    return await interaction.response.send_message("Changing a bounty between Player and NPC requires a new target.", ephemeral=True)

  if target_type is not None:
    b["target_type"] = target_type.value
    changes.append(f"target type: **{target_type.name}**")

  if target is not None:
    target_value = target.strip()
    if not target_value or len(target_value) > 100:
      return await interaction.response.send_message("Target must be 1–100 characters.", ephemeral=True)
    if b.get("target_type") == "player":
      try:
        member = await interaction.guild.fetch_member(int(target_value.strip("<@!>")))
        b["target_user_id"] = member.id
        target_value = member.display_name
      except (ValueError, discord.NotFound, discord.HTTPException):
        return await interaction.response.send_message("For a player bounty, use the player's Discord ID or mention.", ephemeral=True)
    else:
      b["target_user_id"] = None
    b["target"] = target_value
    changes.append(f"target: **{target_value}**")

  if reward is not None:
    if reward <= 0:
      return await interaction.response.send_message("Reward must be positive.", ephemeral=True)
    b["reward_vg"] = reward
    b["reward"] = reward
    changes.append(f"reward: **{reward:,} VG**")

  if description is not None:
    description = description.strip()
    if len(description) > 800:
      return await interaction.response.send_message("Description must be 800 characters or fewer.", ephemeral=True)
    b["description"] = description
    changes.append("description updated")

  if faction is not None:
    b["faction"] = faction.strip()[:60]
    changes.append("faction updated")

  if not changes:
    return await interaction.response.send_message("No changes supplied.", ephemeral=True)
  b["updated_at"] = _now()
  b["updated_by"] = interaction.user.id
  save_item_data()
  await interaction.response.send_message(f"Updated **{b['id']}**.\n" + "\n".join(f"• {c}" for c in changes), ephemeral=True)


@ADMIN_BOUNTY_GROUP.command(name="cancel", description="GM: cancel a bounty without deleting its history.")
@app_commands.describe(bounty_id="The bounty ID.", reason="Optional reason for cancellation.")
async def bounty_cancel(interaction: discord.Interaction, bounty_id: str, reason: str = None):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  b = _bounty(_guild(interaction.guild.id), bounty_id.strip())
  if not b:
    return await interaction.response.send_message("Bounty not found.", ephemeral=True)
  if b.get("status") in {"completed", "cancelled"}:
    return await interaction.response.send_message(f"That bounty is already {b.get('status')}.", ephemeral=True)
  if b.get("funded_by_player") and b.get("funder_id"):
    from .economy import add_money, add_vip_points
    refund_currency = "vip" if str(b.get("reward_currency", "vg")).lower() == "vip" else "vg"
    refund = int(b.get("reward_vip", 0) if refund_currency == "vip" else b.get("reward_vg", b.get("reward", 0)) or 0)
    (add_vip_points if refund_currency == "vip" else add_money)(interaction.guild.id, int(b["funder_id"]), refund)
    b["refund"] = refund
    b["refund_currency"] = refund_currency
  b["status"] = "cancelled"
  b["cancelled_at"] = _now()
  b["cancelled_by"] = interaction.user.id
  b["cancel_reason"] = (reason or "").strip()[:500]
  save_item_data()
  await interaction.response.send_message(f"Cancelled **{b['id']}**. It remains in `/bounty history`.", ephemeral=True)


@BOUNTY_GROUP.command(name="history", description="View the campaign's bounty history.")
@app_commands.describe(status="Optional status filter: open, pending, completed, or cancelled.")
async def bounty_history(interaction: discord.Interaction, status: str = None):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  all_bounties = _normalize_bounties(_guild(interaction.guild.id))
  if status:
    status = status.strip().casefold()
    valid = {"open", "pending", "completed", "cancelled"}
    if status not in valid:
      return await interaction.response.send_message("Status must be open, pending, completed, or cancelled.", ephemeral=True)
    rows = [b for b in all_bounties if b.get("status") == status]
  else:
    rows = all_bounties
  if not rows:
    return await interaction.response.send_message("No bounties found for that filter.", ephemeral=True)
  rows = list(reversed(rows))
  lines = []
  for b in rows[:30]:
    lines.append(f"**{b['id']}** — **{b.get('target','Unknown')}** [{b.get('target_type','?').upper()}] — **{b.get('reward',0):,}** — `{b.get('status','unknown')}`")
  suffix = f" — `{status}`" if status else ""
  await interaction.response.send_message("### Bounty History" + suffix + "\n" + "\n".join(lines))


def _claim_group(g, user_id, claim_mode):
  """Return (group_id, group_name, member_ids) for a bounty claimant."""
  uid = int(user_id)
  mode = str(claim_mode or "solo").lower()
  if mode == "party":
    party = _party_for(g, uid)
    if not party:
      return None
    members = [int(x) for x in party.get("members", []) if str(x).isdigit()]
    if uid not in members:
      members.append(uid)
    return party.get("id"), party.get("name", "Party"), members
  if mode == "faction":
    # Factions are stored in the economy state, not gm_tools.
    from .economy import guild_state
    factions = guild_state(g.get("_guild_id", 0)).get("factions", {}) if g.get("_guild_id") else {}
    for name, data in factions.items():
      members = [int(x) for x in data.get("members", []) if str(x).isdigit()]
      if uid in members:
        return name, name, members
    return None
  return str(uid), "Solo Hunter", [uid]


def _faction_for(guild_id, user_id):
  from .economy import guild_state
  factions = guild_state(guild_id).get("factions", {})
  uid = int(user_id)
  for name, data in factions.items():
    members = [int(x) for x in data.get("members", []) if str(x).isdigit()]
    if uid in members:
      return name, data
  return None, None


def _claim_target_conflict(guild_id, bounty, member_ids):
  if bounty.get("target_type") != "player" or not bounty.get("target_user_id"):
    return False
  target_id = int(bounty["target_user_id"])
  return target_id in {int(x) for x in member_ids}


class BountyClaimModal(discord.ui.Modal, title="Claim Bounty"):
  proof = discord.ui.TextInput(
    label="How did you complete it?",
    style=discord.TextStyle.paragraph,
    placeholder="Briefly explain what happened...",
    max_length=800,
    required=True,
  )

  def __init__(self, bounty_id, claim_mode="solo"):
    super().__init__()
    self.bounty_id = bounty_id
    self.claim_mode = claim_mode

  async def on_submit(self, interaction: discord.Interaction):
    if interaction.guild is None:
      return await interaction.response.send_message("Server only.", ephemeral=True)
    g = _guild(interaction.guild.id)
    b = _bounty(g, self.bounty_id)
    if not b or b.get("status") != "open":
      return await interaction.response.send_message("That bounty is no longer open.", ephemeral=True)

    claim = _claim_group(g, interaction.user.id, self.claim_mode)
    if not claim:
      label = "a party" if self.claim_mode == "party" else "a faction"
      return await interaction.response.send_message(f"You must be in {label} to claim this way.", ephemeral=True)

    group_id, group_name, member_ids = claim
    if _claim_target_conflict(interaction.guild.id, b, member_ids):
      return await interaction.response.send_message("A member of the claiming group is the target of this bounty.", ephemeral=True)

    b["status"] = "pending"
    b["claim_mode"] = self.claim_mode
    b["claimed_by"] = interaction.user.id
    b["claimant_id"] = group_id
    b["claimant_name"] = group_name
    b["claimant_members"] = member_ids
    b["claim_proof"] = str(self.proof.value)[:800]
    b["claimed_at"] = _now()
    save_item_data()

    if self.claim_mode == "party":
      who = f"party **{group_name}**"
    elif self.claim_mode == "faction":
      who = f"faction **{group_name}**"
    else:
      who = "you"
    await interaction.response.send_message(
      f"Claim submitted for **{b.get('target','Unknown')}** by {who}. The GM will review it.",
      ephemeral=True,
    )


class BountyClaimChoiceView(discord.ui.View):
  """Lets a player choose whether a bounty is claimed solo, by their party, or by their faction."""
  def __init__(self, guild_id, bounty_id):
    super().__init__(timeout=300)
    self.guild_id = guild_id
    self.bounty_id = bounty_id

  @discord.ui.button(label="Claim Solo", style=discord.ButtonStyle.primary, row=0)
  async def solo(self, interaction: discord.Interaction, button):
    await interaction.response.send_modal(BountyClaimModal(self.bounty_id, "solo"))

  @discord.ui.button(label="Claim as Party", style=discord.ButtonStyle.success, row=0)
  async def party(self, interaction: discord.Interaction, button):
    g = _guild(self.guild_id)
    if not _party_for(g, interaction.user.id):
      return await interaction.response.send_message("You are not in a party.", ephemeral=True)
    await interaction.response.send_modal(BountyClaimModal(self.bounty_id, "party"))

  @discord.ui.button(label="Claim as Faction", style=discord.ButtonStyle.success, row=0)
  async def faction(self, interaction: discord.Interaction, button):
    name, _ = _faction_for(self.guild_id, interaction.user.id)
    if not name:
      return await interaction.response.send_message("You are not in a faction.", ephemeral=True)
    await interaction.response.send_modal(BountyClaimModal(self.bounty_id, "faction"))


class BountyBoardView(discord.ui.View):
  def __init__(self, guild_id, status_filter="open"):
    super().__init__(timeout=1800)
    self.guild_id = guild_id
    self.status_filter = status_filter
    self.selected_id = None
    self.add_item(BountyFilterSelect(self))
    self.add_item(BountySelect(self))

  def rows(self):
    rows = _normalize_bounties(_guild(self.guild_id))
    if self.status_filter == "active":
      return [b for b in rows if b.get("status") in {"open", "pending"}]
    if self.status_filter == "all":
      return list(rows)
    return [b for b in rows if b.get("status") == self.status_filter]

  def embed(self):
    rows = self.rows()
    status_name = self.status_filter.replace("_", " ").title()
    e = discord.Embed(
      title=" THE BOUNTY BOARD",
      description=("Wanted contracts issued by the GM.\n"
             "Choose a bounty below to inspect it. Hunters can submit claims; the GM decides whether the claim is valid."),
      colour=discord.Colour.dark_red(),
    )
    if not rows:
      e.add_field(name="No bounties", value="There are no bounties in this section yet.", inline=False)
    else:
      lines = []
      for b in rows[:5]:
        status = b.get("status", "open")
        icon = {"open":"", "pending":"", "completed":"", "cancelled":""}.get(status, "")
        target_type = "PLAYER" if b.get("target_type") == "player" else "NPC"
        faction = f" • {b['faction']}" if b.get("faction") else ""
        lines.append(
          f"{icon} **{b.get('target','Unknown')}** · `{target_type}`\n"
          f" **{int(b.get('reward_vg', b.get('reward', 0))):,}** VG{faction} · `{status.title()}`"
        )
      e.add_field(name=f" {status_name} Contracts", value="\n\n".join(lines), inline=False)
    e.set_footer(text="Select a bounty for details • This board stays in the channel and updates in place")
    return e

  @discord.ui.button(label="Details", style=discord.ButtonStyle.primary, row=2)
  async def details(self, interaction, button):
    b = _bounty(_guild(self.guild_id), self.selected_id or "")
    if not b:
      return await interaction.response.send_message("Select a bounty first.", ephemeral=True)
    e = discord.Embed(title=f" {b.get('target','Unknown')}", colour=discord.Colour.dark_red())
    currency = str(b.get("reward_currency", "vg")).upper()
    amount = int(b.get("reward_vip", 0) if currency == "VIP" else b.get("reward_vg", b.get("reward", 0)) or 0)
    e.add_field(name="Reward", value=f" **{amount:,}** {currency}", inline=True)
    e.add_field(name="Type", value=("Player" if b.get("target_type") == "player" else "NPC"), inline=True)
    e.add_field(name="Status", value=b.get("status", "unknown").title(), inline=True)
    e.add_field(name="Description", value=b.get("description") or "No description provided.", inline=False)
    if b.get("faction"):
      e.add_field(name="Issued By", value=f" {b['faction']}", inline=True)
    if b.get("claimed_by"):
      e.add_field(name="Claimant", value=f"<@{b['claimed_by']}>", inline=True)
    e.set_footer(text="Use Back to return to the bounty board")
    back = BountyBackView(self)
    await interaction.response.edit_message(embed=e, view=back)

  @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, row=2)
  async def claim(self, interaction, button):
    b = _bounty(_guild(self.guild_id), self.selected_id or "")
    if not b or b.get("status") != "open":
      return await interaction.response.send_message("Select an open bounty first.", ephemeral=True)
    await interaction.response.send_message(
      f"How should **{b.get('target','Unknown')}** be claimed?",
      view=BountyClaimChoiceView(self.guild_id, b.get("id")),
      ephemeral=True,
    )

  @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=2)
  async def refresh(self, interaction, button):
    self.refresh_components()
    await interaction.response.edit_message(embed=self.embed(), view=self)



  def refresh_components(self):
    # Rebuild the bounty selector so edits immediately reflect the database.
    for child in list(self.children):
      if isinstance(child, BountySelect):
        self.remove_item(child)
    self.add_item(BountySelect(self))


class BountyFilterSelect(discord.ui.Select):
  def __init__(self, view):
    self.parent_view = view
    options = [
      discord.SelectOption(label="Active", value="active", description="Open and pending bounties"),
      discord.SelectOption(label="Open", value="open", description="Bounties waiting for hunters"),
      discord.SelectOption(label="Pending Claims", value="pending", description="Claims waiting for GM review"),
      discord.SelectOption(label="Completed", value="completed", description="Completed campaign bounties"),
      discord.SelectOption(label="Cancelled", value="cancelled", description="Cancelled bounties"),
      discord.SelectOption(label="All", value="all", description="Full bounty history"),
    ]
    super().__init__(placeholder="Filter the bounty board...", options=options, row=0)

  async def callback(self, interaction):
    self.parent_view.status_filter = self.values[0]
    self.parent_view.selected_id = None
    self.parent_view.refresh_components()
    await interaction.response.edit_message(embed=self.parent_view.embed(), view=self.parent_view)


class BountySelect(discord.ui.Select):
  def __init__(self, view):
    self.parent_view = view
    rows = view.rows()
    options = []
    for b in rows[:25]:
      status = b.get("status", "open")
      options.append(discord.SelectOption(
        label=f"{b.get('target','Unknown')} — {b.get('reward',0):,}"[:100],
        value=str(b.get("id", "")),
        description=f"{status.title()} • {b.get('target_type','npc').upper()}"[:100],
      ))
    if not options:
      options = [discord.SelectOption(label="No bounties available", value="none", description="Create one with /admin bounty create")]
    super().__init__(placeholder="Select a bounty...", options=options, row=1)

  async def callback(self, interaction):
    self.parent_view.selected_id = self.values[0] if self.values[0] != "none" else None
    await interaction.response.edit_message(embed=self.parent_view.embed(), view=self.parent_view)


class BountyBackView(discord.ui.View):
  def __init__(self, board):
    super().__init__(timeout=1800)
    self.board = board

  @discord.ui.button(label="Back to Bounties", style=discord.ButtonStyle.primary, row=0)
  async def back(self, interaction, button):
    self.board.refresh_components()
    await interaction.response.edit_message(embed=self.board.embed(), view=self.board)

  @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=0)
  async def refresh(self, interaction, button):
    self.board.refresh_components()
    await interaction.response.edit_message(embed=self.board.embed(), view=self.board)


@BOUNTY_GROUP.command(name="list", description="View the public campaign bounty board.")
async def bounties(interaction: discord.Interaction):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  board = BountyBoardView(interaction.guild.id, "active")
  # Keep the board public. Private claim proof/errors are ephemeral.
  await interaction.response.send_message(embed=board.embed(), view=board)

@BOUNTY_GROUP.command(name="claim", description="Claim a bounty solo, with your party, or with your faction.")
@app_commands.describe(
  bounty_id="The bounty ID.",
  proof="Short note explaining how the bounty was completed.",
  claim_as="Who is claiming the bounty.",
)
@app_commands.choices(claim_as=[
  app_commands.Choice(name="Solo", value="solo"),
  app_commands.Choice(name="Party", value="party"),
  app_commands.Choice(name="Faction", value="faction"),
])
async def bounty_claim(
  interaction: discord.Interaction,
  bounty_id: str,
  proof: str,
  claim_as: app_commands.Choice[str] | None = None,
):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  mode = claim_as.value if claim_as else "solo"
  g = _guild(interaction.guild.id)
  b = _bounty(g, bounty_id.strip())
  if not b or b.get("status") != "open":
    return await interaction.response.send_message("That bounty does not exist or is not open.", ephemeral=True)
  claim = _claim_group(g, interaction.user.id, mode)
  if not claim:
    return await interaction.response.send_message(
      "You must be in a party/faction to use that claim type.",
      ephemeral=True,
    )
  group_id, group_name, member_ids = claim
  if _claim_target_conflict(interaction.guild.id, b, member_ids):
    return await interaction.response.send_message("A member of the claiming group is the target of this bounty.", ephemeral=True)

  b["status"] = "pending"
  b["claim_mode"] = mode
  b["claimed_by"] = interaction.user.id
  b["claimant_id"] = group_id
  b["claimant_name"] = group_name
  b["claimant_members"] = member_ids
  b["claim_proof"] = proof[:800]
  b["claimed_at"] = _now()
  save_item_data()
  await interaction.response.send_message(
    f"Claim submitted for **{b['id']}** by **{group_name}**. The GM can review it.",
    ephemeral=True,
  )


@ADMIN_BOUNTY_GROUP.command(name="complete", description="GM: approve a bounty claim and pay the hunter.")
@app_commands.describe(bounty_id="The bounty ID.")
async def bounty_complete(interaction: discord.Interaction, bounty_id: str):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  g = _guild(interaction.guild.id)
  b = _bounty(g, bounty_id.strip())
  if not b or b.get("status") != "pending" or not b.get("claimed_by"):
    return await interaction.response.send_message("That bounty has no pending claim.", ephemeral=True)
  if b.get("paid_vg") is not None or b.get("status") == "completed":
    return await interaction.response.send_message("That bounty has already been paid.", ephemeral=True)
  currency = "vip" if str(b.get("reward_currency", "vg")).lower() == "vip" else "vg"
  reward = int(b.get("reward_vip", 0) if currency == "vip" else b.get("reward_vg", b.get("reward", 0)) or 0)
  if reward <= 0:
    return await interaction.response.send_message("This bounty has an invalid reward and cannot be completed.", ephemeral=True)
  from .economy import add_money, guild_state, add_vip_points
  mode = str(b.get("claim_mode", "solo")).lower()
  recipient = b.get("claimant_name") or f"<@{int(b['claimed_by'])}>"

  if mode == "party":
    party_id = str(b.get("claimant_id", ""))
    party = g.get("parties", {}).get(party_id)
    if not party:
      return await interaction.response.send_message("The claiming party no longer exists, so the bounty cannot be paid automatically.", ephemeral=True)
    members = [int(x) for x in party.get("members", []) if str(x).isdigit()]
    if not members:
      return await interaction.response.send_message("The claiming party has no valid members, so the bounty cannot be paid.", ephemeral=True)
    share, remainder = divmod(reward, len(members))
    for member_id in members:
      if currency == "vip": add_vip_points(interaction.guild.id, member_id, share)
      else: add_money(interaction.guild.id, member_id, share)
    if remainder:
      if currency == "vip": add_vip_points(interaction.guild.id, int(party.get("leader_id")), remainder)
      else: add_money(interaction.guild.id, int(party.get("leader_id")), remainder)
    recipient = f"party **{party.get('name', 'Party')}** — {share:,} {currency.upper()} each"
    if remainder:
      recipient += f" (+{remainder:,} {currency.upper()} remainder to the leader)"
    b["payout_members"] = members
    b["payout_share_vg"] = share
  elif mode == "faction":
    faction_name = str(b.get("claimant_id", ""))
    factions = guild_state(interaction.guild.id).get("factions", {})
    faction = next((data for name, data in factions.items() if name == faction_name), None)
    if faction is None:
      return await interaction.response.send_message("The claiming faction no longer exists, so the bounty cannot be paid automatically.", ephemeral=True)
    if currency == "vip":
      faction["treasury_vip"] = int(faction.get("treasury_vip", 0) or 0) + reward
    else:
      faction["treasury"] = int(faction.get("treasury", 0) or 0) + reward
    recipient = f"faction **{faction_name}**"
  else:
    claimant_id = int(b["claimed_by"])
    if currency == "vip": add_vip_points(interaction.guild.id, claimant_id, reward)
    else: add_money(interaction.guild.id, claimant_id, reward)
    recipient = f"<@{claimant_id}>"

  b["status"] = "completed"
  b["completed_by"] = interaction.user.id
  b["completed_at"] = _now()
  b["paid_vg"] = reward if currency == "vg" else 0
  b["paid_vip"] = reward if currency == "vip" else 0
  save_item_data()
  await interaction.response.send_message(f"Completed **{b['id']}**. {recipient} received **{reward:,} {currency.upper()}**.", ephemeral=True)


@ADMIN_BOUNTY_GROUP.command(name="remove", description="GM: remove/cancel a bounty.")
@app_commands.describe(bounty_id="The bounty ID.")
async def bounty_remove(interaction: discord.Interaction, bounty_id: str):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  g = _guild(interaction.guild.id)
  b = _bounty(g, bounty_id.strip())
  if not b:
    return await interaction.response.send_message("Bounty not found.", ephemeral=True)
  b["status"] = "cancelled"
  b["cancelled_at"] = _now()
  save_item_data()
  await interaction.response.send_message(f"Removed **{b['id']}**.", ephemeral=True)


@REPUTATION_GROUP.command(name="view", description="View your reputation with campaign factions.")
async def reputation(interaction: discord.Interaction):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  factions = _guild(interaction.guild.id)["reputation"].get(str(interaction.user.id), {})
  if not factions:
    return await interaction.response.send_message("You have no recorded faction reputation yet.", ephemeral=True)
  await interaction.response.send_message("### Your Reputation\n" + "\n".join(f"**{f}** — **{v:+d}**" for f, v in factions.items()), ephemeral=True)


@ADMIN_REPUTATION_GROUP.command(name="set", description="GM: set a player's reputation with a faction.")
@app_commands.describe(user="Player.", faction="Faction name.", value="New reputation value.")
async def reputation_set(interaction: discord.Interaction, user: discord.Member, faction: str, value: int):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  g = _guild(interaction.guild.id)
  g["reputation"].setdefault(str(user.id), {})[faction.strip()[:60]] = value
  save_item_data()
  await interaction.response.send_message(f"Set {user.mention}'s **{faction}** reputation to **{value:+d}**.", ephemeral=True)


@ADMIN_REPUTATION_GROUP.command(name="add", description="GM: add or subtract faction reputation.")
@app_commands.describe(user="Player.", faction="Faction name.", amount="Amount to add; negative values subtract reputation.")
async def reputation_add(interaction: discord.Interaction, user: discord.Member, faction: str, amount: int):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  g = _guild(interaction.guild.id)
  f = faction.strip()[:60]
  rep = g["reputation"].setdefault(str(user.id), {})
  rep[f] = int(rep.get(f, 0)) + amount
  save_item_data()
  await interaction.response.send_message(f"Changed {user.mention}'s **{f}** reputation by **{amount:+d}** → **{rep[f]:+d}**.", ephemeral=True)


@ADMIN_REPUTATION_GROUP.command(name="player", description="GM: view a player's faction reputation.")
@app_commands.describe(user="Player to inspect.")
async def reputation_view(interaction: discord.Interaction, user: discord.Member):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  rep = _guild(interaction.guild.id)["reputation"].get(str(user.id), {})
  await interaction.response.send_message(f"### Reputation — {user.display_name}\n" + ("\n".join(f"**{f}** — **{v:+d}**" for f, v in rep.items()) or "No recorded reputation."), ephemeral=True)


@PARTY_GROUP.command(name="create", description="Create an adventuring party for the campaign.")
@app_commands.describe(name="Party name.")
async def party_create(interaction: discord.Interaction, name: str):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  g = _guild(interaction.guild.id)
  if _party_for(g, interaction.user.id):
    return await interaction.response.send_message("You are already in a party.", ephemeral=True)
  pid = _party_id(g)
  g["parties"][pid] = {"id": pid, "name": name.strip()[:60] or "Adventurers", "leader_id": interaction.user.id, "members": [interaction.user.id], "treasury": 0, "created_at": _now()}
  g["party_members"][str(interaction.user.id)] = pid
  save_item_data()
  await interaction.response.send_message(f"Created party **{g['parties'][pid]['name']}**. You are the leader.", ephemeral=True)


@PARTY_GROUP.command(name="invite", description="Invite a player to your party.")
@app_commands.describe(user="Player to invite.")
async def party_invite(interaction: discord.Interaction, user: discord.Member):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  g = _guild(interaction.guild.id); party = _party_for(g, interaction.user.id)
  if not party or party["leader_id"] != interaction.user.id:
    return await interaction.response.send_message("Only the party leader can invite players.", ephemeral=True)
  if _party_for(g, user.id):
    return await interaction.response.send_message("That player is already in a party.", ephemeral=True)
  party.setdefault("invites", {})[str(user.id)] = _now()
  save_item_data()
  await interaction.response.send_message(f"Invited {user.mention}. They can use `/party-join {party['id']}`.", ephemeral=True)


@PARTY_GROUP.command(name="join", description="Join a party using its ID after receiving an invite.")
@app_commands.describe(party_id="Party ID from the invitation.")
async def party_join(interaction: discord.Interaction, party_id: str):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  g = _guild(interaction.guild.id); party = g["parties"].get(party_id.strip())
  if not party or interaction.user.id not in [int(x) for x in party.get("invites", {}).keys()]:
    return await interaction.response.send_message("You don't have an invitation to that party.", ephemeral=True)
  if _party_for(g, interaction.user.id):
    return await interaction.response.send_message("You are already in a party.", ephemeral=True)
  party["members"].append(interaction.user.id); party["invites"].pop(str(interaction.user.id), None); g["party_members"][str(interaction.user.id)] = party_id
  save_item_data()
  await interaction.response.send_message(f"You joined **{party['name']}**.", ephemeral=True)


@PARTY_GROUP.command(name="leave", description="Leave your current party.")
async def party_leave(interaction: discord.Interaction):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  g = _guild(interaction.guild.id); party = _party_for(g, interaction.user.id)
  if not party:
    return await interaction.response.send_message("You are not in a party.", ephemeral=True)
  party["members"] = [m for m in party["members"] if m != interaction.user.id]; g["party_members"].pop(str(interaction.user.id), None)
  if party["leader_id"] == interaction.user.id:
    if party["members"]:
      party["leader_id"] = party["members"][0]
    else:
      g["parties"].pop(party["id"], None)
  save_item_data()
  await interaction.response.send_message("You left the party.", ephemeral=True)


@PARTY_GROUP.command(name="info", description="View your party roster.")
async def party_info(interaction: discord.Interaction):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  party = _party_for(_guild(interaction.guild.id), interaction.user.id)
  if not party:
    return await interaction.response.send_message("You are not in a party.", ephemeral=True)
  roster = ", ".join(f"<@{m}>" for m in party["members"])
  await interaction.response.send_message(f"### {party['name']}\nParty ID: `{party['id']}`\nLeader: <@{party['leader_id']}>\nMembers: {roster}\nTreasury: **{int(party.get("treasury", 0) or 0):,} VG**\nBounty rewards are split equally between party members.", ephemeral=True)


@ADMIN_GROUP.command(name="set-class", description="GM: set a player's custom character class name.")
@app_commands.describe(user="The player whose class you want to change.", class_name="The custom class name to assign to the player.")
async def set_player_class(interaction: discord.Interaction, user: discord.Member, class_name: str):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  class_name = " ".join(str(class_name).strip().split())
  if not class_name:
    return await interaction.response.send_message("Class name cannot be empty.", ephemeral=True)
  if len(class_name) > 100:
    return await interaction.response.send_message("Class name must be 100 characters or fewer.", ephemeral=True)
  state = item_state(interaction.guild.id)
  players = state.setdefault("players", {})
  profile = players.setdefault(str(user.id), {})
  profile["class"] = class_name
  # Keep the custom class independent from race and other legacy profile fields.
  profile.pop("location", None)
  save_item_data()
  await interaction.response.send_message(
    f"Set {user.mention}'s class to **{discord.utils.escape_markdown(class_name)}**.",
    ephemeral=True,
  )


def reset_player_data(guild_id, user_id):
  """Reset player-owned campaign state while preserving global lore/history/catalog data."""
  uid = str(user_id)
  state = item_state(guild_id)
  removed = []

  for key in ("inventories", "possessions", "equipment", "objectives", "companions"):
    container = state.get(key)
    if isinstance(container, dict) and uid in container:
      del container[uid]
      removed.append(key)

  economy = state.get("economy", {})
  if isinstance(economy, dict):
    for key in ("balances", "vip_points"):
      container = economy.get(key)
      if isinstance(container, dict) and uid in container:
        del container[uid]
        removed.append("VIP" if key == "vip_points" else "money")
    factions = economy.get("factions", {})
    if isinstance(factions, dict):
      changed = False
      for data in factions.values():
        if isinstance(data, dict) and isinstance(data.get("members"), list) and user_id in data["members"]:
          data["members"] = [x for x in data["members"] if x != user_id]
          changed = True
      if changed:
        removed.append("faction memberships")

  dungeon = state.get("dungeon", {})
  if isinstance(dungeon, dict):
    for key in ("players", "leaderboard", "prestige"):
      container = dungeon.get(key)
      if isinstance(container, dict) and uid in container:
        del container[uid]
        removed.append(f"dungeon {key}")

  gm = state.get("gm_tools", {})
  if isinstance(gm, dict):
    rep = gm.get("reputation", {})
    if isinstance(rep, dict) and uid in rep:
      del rep[uid]
      removed.append("reputation")

    party_id = gm.get("party_members", {}).pop(uid, None) if isinstance(gm.get("party_members"), dict) else None
    if party_id:
      party = gm.get("parties", {}).get(party_id)
      if party:
        party["members"] = [m for m in party.get("members", []) if str(m) != uid]
        party.get("invites", {}).pop(uid, None)
        if str(party.get("leader_id")) == uid:
          party["leader_id"] = party["members"][0] if party.get("members") else None
        if not party.get("members"):
          gm.get("parties", {}).pop(party_id, None)
      removed.append("party membership")

    for bounty in gm.get("bounties", []):
      if bounty.get("claimed_by") == user_id:
        bounty.pop("claimed_by", None); bounty.pop("claim_proof", None); bounty.pop("claimed_at", None)
        if bounty.get("status") == "pending": bounty["status"] = "open"
      if bounty.get("completed_by") == user_id:
        bounty.pop("completed_by", None)

  state.get("players", {}).pop(uid, None) if isinstance(state.get("players"), dict) else None
  try:
    from . import trade as trade_module
    for tid, trade in list(trade_module.TRADES.items()):
      if trade.get("initiator_id") == user_id or trade.get("target_id") == user_id:
        trade_module.TRADES.pop(tid, None)
    for rid, request in list(trade_module.REQUESTS.items()):
      if any(request.get(k) == user_id for k in ("from_id", "to_id", "initiator_id", "target_id")):
        trade_module.REQUESTS.pop(rid, None)
  except Exception:
    pass

  save_item_data()
  return removed


@ADMIN_GROUP.command(name="clear", description="GM: permanently clear all stored campaign data belonging to a player in this server.")
@app_commands.describe(user="The player whose stored information will be erased.")
async def clear_user(interaction: discord.Interaction, user: discord.Member):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  if user.bot:
    return await interaction.response.send_message("Bot accounts are not player records.", ephemeral=True)
  if user.id == interaction.user.id:
    return await interaction.response.send_message("Use `/clear` on another player. This command cannot clear your own GM/admin account.", ephemeral=True)
  removed = reset_player_data(interaction.guild.id, user.id)
  detail = ", ".join(removed) if removed else "no stored player data found"
  await interaction.response.send_message(
    f"Cleared stored campaign data for {user.mention}.\n\nRemoved: {detail}.\n\nCampaign history, catalog templates, and bounty records themselves were preserved.",
    ephemeral=True,
  )


for _bounty_cmd in (bounty_info, bounty_edit, bounty_cancel, bounty_claim, bounty_complete, bounty_remove):
  _bounty_cmd.autocomplete("bounty_id")(bounty_id_autocomplete)

COMMANDS = [
  start_game, cancel_game, check_in, check_out, attendance, end_game, session_event, session_summary, sessions, session_reviews, game_status, bounty_create, bounty_place, bounty_info, bounty_edit, bounties, bounty_claim, bounty_complete, bounty_cancel, bounty_remove, bounty_history,
  reputation, reputation_set, reputation_add, reputation_view,
  party_create, party_invite, party_join, party_leave, party_info, set_player_class, clear_user,
]


def register(bot):
  global _BOT
  _BOT = bot
  for group in (GM_GROUP, GAME_GROUP, ATTENDANCE_GROUP, SESSION_GROUP, BOUNTY_GROUP, REPUTATION_GROUP, PARTY_GROUP):
    bot.tree.add_command(group)
  # Persistent session attendance buttons in player DMs.
  # Views are also attached to new DMs, while existing views continue through their custom IDs.
  for guild in bot.guilds:
    bot.add_view(SessionAttendanceView(guild.id))
