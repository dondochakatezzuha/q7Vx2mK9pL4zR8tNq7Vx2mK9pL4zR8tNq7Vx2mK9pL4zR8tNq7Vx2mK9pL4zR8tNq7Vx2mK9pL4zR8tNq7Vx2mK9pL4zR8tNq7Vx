import asyncio
import json
import os
import random
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands

from ..state import conversations, anonymous_messages, get_alias, is_staff, set_anonymous_alias, clear_anonymous_alias, get_user_anonymous_alias
from ..core import campaign_store

# Slash-command groups keep the command tree organized and below Discord's 100 global command limit.
ANONYMOUS_GROUP = app_commands.Group(name="anonymous", description="Anonymous messaging commands.")
ANONYMOUS_ADMIN_GROUP = app_commands.Group(name="admin", description="Admin controls for anonymous identities.", parent=ANONYMOUS_GROUP)

RNG_GROUP = app_commands.Group(name="rng", description="Randomized anonymous actions.")

@RNG_GROUP.command(name="anonymous", description="Send an automatically repliable anonymous DM to a random player in this server.")
@app_commands.describe(
  text="The anonymous message to send.",
  attachment="Optional file to send with the anonymous DM.",
  chaos="Use chaotic/distorted formatting.",
)
async def rng_anonymous(interaction: discord.Interaction, text: str, attachment: discord.Attachment = None, chaos: bool = False):
  """Choose a random non-bot server member and anonymously DM them."""
  guild = interaction.guild
  if guild is None:
    return await interaction.response.send_message("This command can only be used inside a server.", ephemeral=True)

  candidates = [m for m in guild.members if not m.bot and m.id != interaction.user.id]
  if not candidates:
    return await interaction.response.send_message("There are no eligible players to receive the random anonymous DM.", ephemeral=True)

  recipient = random.choice(candidates)
  display_alias = resolve_alias(interaction, None, fallback=None)
  # RNG anonymous DMs are always repliable.
  repliable = True
  conversation_id = str(uuid.uuid4())
  conversations[conversation_id] = {"recipient_id": interaction.user.id, "alias": display_alias, "chaos": chaos}

  try:
    body = chaos_text(text, chaos)
    content = f"**{display_alias}**\n{body}" if display_alias else body
    view = PublicMessageView(conversation_id=conversation_id, repliable=repliable) if repliable else None
    file = await attachment.to_file() if attachment else None
    sent = await recipient.send(content, file=file, view=view)
    _audit_anonymous(interaction, "anonymous_random_dm", sent=sent, target=recipient, details={"alias": display_alias, "chaos": chaos})
    if conversation_id:
      anonymous_messages[sent.id] = {"owner_id": interaction.user.id, "channel_id": sent.channel.id, "conversation_id": conversation_id}
    await interaction.response.send_message("The anonymous DM was sent to a random player.", ephemeral=True)
  except discord.Forbidden:
    if conversation_id:
      conversations.pop(conversation_id, None)
    await interaction.response.send_message("The randomly selected player's DMs are disabled, so the anonymous DM could not be delivered.", ephemeral=True)
  except discord.HTTPException as exc:
    if conversation_id:
      conversations.pop(conversation_id, None)
    print(f"RNG anonymous DM error: {type(exc).__name__}: {exc}")
    await interaction.response.send_message("The random anonymous DM could not be delivered.", ephemeral=True)



BOT = None
bot = None

# Chaotic formatting used by anonymous messages. Keeping this local avoids
# depending on the separate distortion engine.
_ZALGO_UP = ["\u0300", "\u0301", "\u0302", "\u0303", "\u0304", "\u0307", "\u0308", "\u030a", "\u030b", "\u030c", "\u0310", "\u0311", "\u0312", "\u0315", "\u0316", "\u0317", "\u0318", "\u0319", "\u031a", "\u031b", "\u033d", "\u0340", "\u0341", "\u0342", "\u0343", "\u0344", "\u034a", "\u034b", "\u034c", "\u034d", "\u034e", "\u034f"]

def chaos_text(text: str, enabled: bool = False) -> str:
  """Return anonymous text unchanged unless chaos mode is enabled."""
  if not enabled:
    return text
  out = []
  for ch in text:
    out.append(ch)
    if ch.isalpha() and random.random() < 0.18:
      out.extend(random.sample(_ZALGO_UP, k=random.randint(1, 3)))
  result = "".join(out)
  # Light corruption only; keep messages readable and within Discord's limit.
  if len(result) > 1900:
    result = result[:1900] + "…"
  return result

def resolve_alias(interaction, explicit_alias=None, fallback="Anonymous"):
  if explicit_alias:
    return get_alias(explicit_alias)
  if interaction.guild is not None:
    stored = get_user_anonymous_alias(interaction.guild.id, interaction.user.id)
    if stored:
      return stored
  return None if fallback is None else fallback


def _audit_anonymous(interaction, action, sent=None, target=None, details=None):
  try:
    guild = getattr(interaction, "guild", None)
    gid = getattr(guild, "id", None)
    campaign_store.audit_event(gid, action, category="anonymous", actor_id=getattr(getattr(interaction, "user", None), "id", None), actor_name=getattr(getattr(interaction, "user", None), "display_name", None), target_type="message", target_id=getattr(sent, "id", None), details={**(details or {}), "target_id": getattr(target, "id", None)})
  except Exception as exc:
    print(f"Anonymous audit warning: {type(exc).__name__}: {exc}")

class ReplyModal(discord.ui.Modal, title="Anonymous Reply"):

  reply = discord.ui.TextInput(
    label="Your reply",
    placeholder="Type your anonymous reply...",
    style=discord.TextStyle.paragraph,
    required=True,
    max_length=2000
  )

  def __init__(self, conversation_id):
    super().__init__()
    self.conversation_id = conversation_id

  async def on_submit(self, interaction: discord.Interaction):
    conversation = conversations.get(self.conversation_id)

    if conversation is None:
      await interaction.response.send_message(
        "This anonymous conversation no longer exists.",
        ephemeral=True
      )
      return

    recipient_id = conversation["recipient_id"]
    alias = conversation["alias"]
    chaos = bool(conversation.get("chaos", False))

    try:
      recipient = await interaction.client.fetch_user(recipient_id)
    except discord.NotFound:
      await interaction.response.send_message(
        "The other person could not be found.",
        ephemeral=True
      )
      return

    new_conversation_id = str(uuid.uuid4())
    conversations[new_conversation_id] = {
      # Keep the exact same alias for the entire conversation.
      "recipient_id": interaction.user.id,
      "alias": alias,
      "chaos": chaos
    }

    try:
      reply_body = chaos_text(self.reply.value, chaos)
      reply_content = f"**{alias}**\n{reply_body}" if alias else reply_body
      sent = await recipient.send(
        reply_content,
        view=PublicMessageView(conversation_id=new_conversation_id, repliable=True),
      )
      anonymous_messages[sent.id] = {
        "owner_id": interaction.user.id,
        "channel_id": sent.channel.id,
        "conversation_id": new_conversation_id,
      }
      _audit_anonymous(interaction, "anonymous_reply", sent=sent, target=recipient, details={"alias":alias})
      # The reply message is sent once with its Reply button; no post-send edit.

      await interaction.response.send_message(
        "Anonymous reply sent.",
        ephemeral=True
      )
    except discord.Forbidden:
      await interaction.response.send_message(
        "I couldn't send the reply. Their DMs may be disabled.",
        ephemeral=True
      )


# ============================================================
# REPLY BUTTON
# ============================================================

class ReplyView(discord.ui.View):

  def __init__(self, conversation_id):
    super().__init__(timeout=None)
    self.conversation_id = conversation_id

  @discord.ui.button(
    label="Reply",
    style=discord.ButtonStyle.secondary
  )
  async def reply_button(
    self,
    interaction: discord.Interaction,
    button: discord.ui.Button
  ):
    await interaction.response.send_modal(
      ReplyModal(self.conversation_id)
    )


# ============================================================
# PUBLIC ANONYMOUS MESSAGE VIEW
# ============================================================

class PublicReplyButton(discord.ui.Button):
  def __init__(self, conversation_id):
    super().__init__(label="Reply", style=discord.ButtonStyle.secondary)
    self.conversation_id = conversation_id

  async def callback(self, interaction: discord.Interaction):
    await interaction.response.send_modal(ReplyModal(self.conversation_id))


class PublicMessageView(discord.ui.View):
  def __init__(self, conversation_id=None, owner_id=None, repliable=False, message_id=None, channel_id=None):
    super().__init__(timeout=None)
    if repliable and conversation_id:
      self.add_item(PublicReplyButton(conversation_id))


async def whisper(interaction: discord.Interaction, user: discord.User, message: str, alias: str = None):
  alias = resolve_alias(interaction, alias)
  try:
    sent = await user.send(f"**{alias}**\n{message}")
    _audit_anonymous(interaction, "anonymous_dm", sent=sent, target=user, details={"alias": alias, "mode": "dm"})
    await interaction.response.send_message("Whisper delivered anonymously.", ephemeral=True)
  except discord.Forbidden:
    await interaction.response.send_message("I couldn't DM that player.", ephemeral=True)


# ============================================================
# ANONYMOUS FEATURES
# ============================================================

@ANONYMOUS_GROUP.command(
  name="send",
  description="Send an anonymous message to a server channel."
)
@app_commands.describe(
  text="The anonymous message.",
  channel="The server channel where the message should be posted.",
  server="Optional server. In a server, leave this empty to use the current server.",
  alias="Optional custom anonymous name. Leave empty to stay Anonymous.",
  repliable="If true, add a Reply button for anonymous replies.",
  attachment="Optional file to send with the anonymous message.",
  chaos="Use chaotic/distorted formatting."
)
async def anon(interaction: discord.Interaction, text: str, channel: str, server: str = None, alias: str = None, repliable: bool = False, attachment: discord.Attachment = None, chaos: bool = False):
  """Send an anonymous message to a selected text channel."""
  try:
    display_alias = resolve_alias(interaction, alias, fallback=None)
    client = interaction.client
    guild = None
    if server:
      try:
        guild = client.get_guild(int(server))
      except (ValueError, TypeError):
        guild = None
    elif interaction.guild is not None:
      guild = interaction.guild

    if guild is None:
      return await interaction.response.send_message("I couldn't find that server. Use the server selector or run this command inside a server.", ephemeral=True)

    try:
      target = guild.get_channel(int(channel))
    except (ValueError, TypeError):
      target = None

    if not isinstance(target, discord.TextChannel):
      return await interaction.response.send_message("I couldn't find that text channel. Please select a channel from the autocomplete list.", ephemeral=True)

    me = guild.me or guild.get_member(client.user.id if client.user else 0)
    if me is None:
      return await interaction.response.send_message("I couldn't verify my permissions in that server. Please try again.", ephemeral=True)
    perms = target.permissions_for(me)
    if not perms.view_channel or not perms.send_messages:
      return await interaction.response.send_message("I don't have permission to view and send messages in that channel.", ephemeral=True)

    conversation_id = str(uuid.uuid4()) if repliable else None
    if conversation_id:
      conversations[conversation_id] = {"recipient_id": interaction.user.id, "alias": display_alias, "chaos": chaos}

    body = chaos_text(text, chaos)
    content = f"**{display_alias}**\n{body}" if display_alias else body
    view = PublicMessageView(conversation_id=conversation_id, repliable=repliable) if repliable else None
    file = await attachment.to_file() if attachment else None
    sent = await target.send(content, file=file, view=view)
    _audit_anonymous(interaction, "anonymous_message", sent=sent, target=target, details={"alias": display_alias, "chaos": chaos})
    if conversation_id:
      anonymous_messages[sent.id] = {"owner_id": interaction.user.id, "channel_id": target.id, "conversation_id": conversation_id}
    await interaction.response.send_message(f"Anonymous message sent to {target.mention}.", ephemeral=True)
  except discord.Forbidden:
    if 'conversation_id' in locals() and conversation_id:
      conversations.pop(conversation_id, None)
    if not interaction.response.is_done():
      await interaction.response.send_message("I don't have permission to send messages there.", ephemeral=True)
  except discord.HTTPException as exc:
    print(f"Anonymous send HTTP error: {exc}")
    if 'conversation_id' in locals() and conversation_id:
      conversations.pop(conversation_id, None)
    if not interaction.response.is_done():
      await interaction.response.send_message("Discord rejected the anonymous message. Check the selected channel and bot permissions.", ephemeral=True)
  except Exception as exc:
    print(f"Anonymous send error: {type(exc).__name__}: {exc}")
    if 'conversation_id' in locals() and conversation_id:
      conversations.pop(conversation_id, None)
    if not interaction.response.is_done():
      await interaction.response.send_message("Anonymous send failed. Check the bot console for the exact error.", ephemeral=True)


async def resolve_members(guild: discord.Guild, users: str):
  """Resolve Discord @mentions, exact usernames, and exact display names.

  Commas are used to separate multiple users so display names containing
  spaces can still be entered as one name. Mentions are always reliable.
  """
  members = []
  missing = []

  # Pull mentions out first so they are not broken apart by spaces.
  mention_ids = re.findall(r"<@!?(\d+)>", users)
  remaining = re.sub(r"<@!?(\d+)>", "", users)

  for member_id in mention_ids:
    member = guild.get_member(int(member_id))
    if member is None:
      try:
        member = await guild.fetch_member(int(member_id))
      except (discord.NotFound, discord.HTTPException):
        member = None
    if member and member not in members:
      members.append(member)

  # Prefer comma-separated names. If there are no commas, first try the
  # entire input as one username/display name before treating spaces as
  # separators. This fixes names such as "John Smith".
  raw = remaining.strip(" ,")
  if raw:
    if "," in raw:
      names = [part.strip() for part in raw.split(",") if part.strip()]
    else:
      names = [raw]

    for name in names:
      lowered = name.casefold()
      member = next(
        (m for m in guild.members if
         m.name.casefold() == lowered or
         (m.global_name and m.global_name.casefold() == lowered)),
        None,
      )
      if member is None:
        # A second pass supports a username followed by whitespace
        # separated users when exact full-name matching fails.
        parts = name.split()
        if len(parts) > 1:
          for part in parts:
            pl = part.casefold()
            member = next(
              (m for m in guild.members if
               m.name.casefold() == pl or
               (m.global_name and m.global_name.casefold() == pl)),
              None,
            )
            if member is None:
              missing.append(part)
            elif member not in members:
              members.append(member)
          continue
      if member and member not in members:
        members.append(member)
      elif not member:
        missing.append(name)

  return members, missing
async def timed_message(interaction: discord.Interaction, channel: discord.TextChannel, message: str, alias: str = None, minutes: int = 0):
  alias = resolve_alias(interaction, alias)
  if minutes < 1 or minutes > 10080:
    await interaction.response.send_message("Provide a timer from 1 to 10080 minutes.", ephemeral=True)
    return
  if not channel.permissions_for(interaction.guild.me).send_messages:
    await interaction.response.send_message("I can't send messages there.", ephemeral=True)
    return
  try:
    sent = await channel.send(f"**{alias}**\n{message}")
    asyncio.create_task(safe_delete_later(channel.id, sent.id, minutes * 60))
    await interaction.response.send_message(f"Timed message sent. It will disappear in {minutes} minute(s).", ephemeral=True)
  except discord.HTTPException:
    await interaction.response.send_message("I couldn't send the timed message.", ephemeral=True)


@ANONYMOUS_GROUP.command(name="one-time", description="Send an anonymous message with a one-time reveal button.")
@app_commands.describe(user="Who should receive it.", message="The secret message.", alias="Optional custom anonymous name. Leave empty to stay Anonymous.", chaos="Use chaotic/distorted formatting.")
async def one_time_message(interaction: discord.Interaction, user: discord.User, message: str, alias: str = None, chaos: bool = False):
  alias = resolve_alias(interaction, alias)
  try:
    view = OneTimeView()
    sent = await user.send(f" **One-time anonymous message from {alias}**", view=view)
    view.message_id = sent.id
    view.channel_id = sent.channel.id
    view.secret = f"**{alias}**\n{chaos_text(message, chaos)}"
    await interaction.response.send_message("One-time message delivered.", ephemeral=True)
  except discord.Forbidden:
    await interaction.response.send_message("I couldn't DM that player.", ephemeral=True)


class OneTimeView(discord.ui.View):
  def __init__(self):
    super().__init__(timeout=86400)
    self.message_id = None
    self.channel_id = None
    self.secret = None
    self.used = False

  @discord.ui.button(label="Reveal once", style=discord.ButtonStyle.danger)
  async def reveal(self, interaction: discord.Interaction, button: discord.ui.Button):
    if self.used:
      await interaction.response.send_message("This message has already been revealed.", ephemeral=True)
      return
    self.used = True
    button.disabled = True
    await interaction.response.edit_message(content=self.secret, view=self)
    await asyncio.sleep(2)
    try:
      channel = bot.get_channel(self.channel_id) or await bot.fetch_channel(self.channel_id)
      msg = await channel.fetch_message(self.message_id)
      await msg.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
      pass



@ANONYMOUS_ADMIN_GROUP.command(name="set-alias", description="Set or clear a member's anonymous username alias.")
@app_commands.describe(user="The server member whose anonymous alias should be changed.", alias="The alias to use. Leave blank to clear it.")
async def anonymous_set_alias(interaction: discord.Interaction, user: discord.Member, alias: str = None):
  if not is_staff(interaction):
    return await interaction.response.send_message("Admin access required.", ephemeral=True)
  if alias and alias.strip():
    cleaned = get_alias(alias)
    if not cleaned:
      return await interaction.response.send_message("That alias is invalid.", ephemeral=True)
    set_anonymous_alias(interaction.guild.id, user.id, cleaned)
    return await interaction.response.send_message(f"Anonymous alias for **{user.display_name}** is now **{cleaned}**.", ephemeral=True)
  clear_anonymous_alias(interaction.guild.id, user.id)
  await interaction.response.send_message(f"Cleared the stored anonymous alias for **{user.display_name}**.", ephemeral=True)


@ANONYMOUS_ADMIN_GROUP.command(name="alias", description="Check a member's stored anonymous username alias.")
@app_commands.describe(user="The server member to check.")
async def anonymous_alias(interaction: discord.Interaction, user: discord.Member):
  if not is_staff(interaction):
    return await interaction.response.send_message("Admin access required.", ephemeral=True)
  current = get_user_anonymous_alias(interaction.guild.id, user.id)
  await interaction.response.send_message(f"**{user.display_name}** → **{current}**" if current else f"**{user.display_name}** has no stored alias.", ephemeral=True)


async def server_autocomplete(interaction: discord.Interaction, current: str):
  current = current.lower()
  return [app_commands.Choice(name=guild.name[:100], value=str(guild.id))
      for guild in interaction.client.guilds if current in guild.name.lower()][:25]

async def channel_autocomplete(interaction: discord.Interaction, current: str):
  current = current.lower()
  server_value = getattr(interaction.namespace, "server", "")
  try:
    guild = interaction.client.get_guild(int(server_value))
  except (ValueError, TypeError):
    guild = None
  if guild is None:
    return []
  me = guild.me
  if me is None:
    return []
  return [app_commands.Choice(name=f"#{channel.name}"[:100], value=str(channel.id))
      for channel in guild.text_channels
      if current in channel.name.lower() and channel.permissions_for(me).send_messages][:25]

@ANONYMOUS_GROUP.command(name="dm", description="Send an anonymous message to a member through their server DMs. The recipient can reply anonymously.")
@app_commands.describe(text="The anonymous message.", server="The server containing the recipient.", member="The member who should receive the anonymous message.", alias="Optional custom anonymous name. Leave empty to stay Anonymous.", repliable="If true, the recipient gets a Reply button.", attachment="Optional file to send with the anonymous DM.", chaos="Use chaotic/distorted formatting.")
async def anon_dm(interaction: discord.Interaction, text: str, server: str, member: str, alias: str = None, repliable: bool = True, attachment: discord.Attachment = None, chaos: bool = False):
    display_alias = resolve_alias(interaction, alias, fallback=None)
    try: guild = bot.get_guild(int(server))
    except (ValueError, TypeError): guild = None
    if guild is None:
        return await interaction.response.send_message("I couldn't find that server.", ephemeral=True)
    try: target_member = guild.get_member(int(member))
    except (ValueError, TypeError): target_member = None
    if target_member is None:
        return await interaction.response.send_message("I couldn't find that member in the server.", ephemeral=True)
    if target_member.bot:
        return await interaction.response.send_message("Bot accounts cannot receive anonymous DMs through this command.", ephemeral=True)
    conversation_id = str(uuid.uuid4())
    conversations[conversation_id] = {"recipient_id": interaction.user.id, "alias": display_alias, "chaos": chaos}
    try:
        body = chaos_text(text, chaos)
        content = f"**{display_alias}**\n{body}" if display_alias else body
        file = await attachment.to_file() if attachment else None
        sent = await target_member.send(content, file=file, view=PublicMessageView(conversation_id=conversation_id, repliable=True))
        _audit_anonymous(interaction, "anonymous_dm", sent=sent, target=target_member, details={"mode":"dm","alias":display_alias,"repliable":True})
        anonymous_messages[sent.id] = {"owner_id": interaction.user.id, "channel_id": sent.channel.id, "conversation_id": conversation_id, "recipient_id": target_member.id, "guild_id": guild.id}
        _audit_anonymous(interaction, "anonymous_dm", sent=sent, target=target_member, details={"mode":"dm","alias":display_alias,"repliable":repliable})
        await interaction.response.send_message("Anonymous message delivered to the selected member.", ephemeral=True)
    except discord.Forbidden:
        conversations.pop(conversation_id, None)
        await interaction.response.send_message("I couldn't DM that member. Their DMs may be disabled.", ephemeral=True)
    except discord.HTTPException:
        conversations.pop(conversation_id, None)
        await interaction.response.send_message("I couldn't send the anonymous DM.", ephemeral=True)


async def server_autocomplete(interaction: discord.Interaction, current: str):
    current = current.lower()
    return [app_commands.Choice(name=guild.name[:100], value=str(guild.id)) for guild in interaction.client.guilds if current in guild.name.lower()][:25]

async def channel_autocomplete(interaction: discord.Interaction, current: str):
    current = current.lower()
    server_value = getattr(interaction.namespace, "server", None)
    guild = None
    if server_value:
        try: guild = interaction.client.get_guild(int(server_value))
        except (ValueError, TypeError): guild = None
    elif interaction.guild:
        guild = interaction.guild
    if guild is None or guild.me is None: return []
    return [app_commands.Choice(name=f"#{channel.name}"[:100], value=str(channel.id)) for channel in guild.text_channels if current in channel.name.lower() and channel.permissions_for(guild.me).send_messages][:25]

async def member_autocomplete(interaction: discord.Interaction, current: str):
    current = current.lower()
    server_value = getattr(interaction.namespace, "server", None)
    guild = None
    if server_value:
        try: guild = interaction.client.get_guild(int(server_value))
        except (ValueError, TypeError): guild = None
    elif interaction.guild:
        guild = interaction.guild
    if guild is None: return []
    return [app_commands.Choice(name=m.display_name[:100], value=str(m.id)) for m in guild.members if not m.bot and (current in m.display_name.lower() or current in m.name.lower())][:25]

server_autocomplete.__name__ = "server_autocomplete"
channel_autocomplete.__name__ = "channel_autocomplete"
member_autocomplete.__name__ = "member_autocomplete"
anon.autocomplete("server")(server_autocomplete)
anon.autocomplete("channel")(channel_autocomplete)
anon_dm.autocomplete("server")(server_autocomplete)
anon_dm.autocomplete("member")(member_autocomplete)


COMMANDS = [anon, one_time_message, anon_dm]

def register(bot_instance):
  global BOT, bot
  BOT = bot_instance
  bot = bot_instance
  bot.tree.add_command(ANONYMOUS_GROUP)
  bot.tree.add_command(RNG_GROUP)