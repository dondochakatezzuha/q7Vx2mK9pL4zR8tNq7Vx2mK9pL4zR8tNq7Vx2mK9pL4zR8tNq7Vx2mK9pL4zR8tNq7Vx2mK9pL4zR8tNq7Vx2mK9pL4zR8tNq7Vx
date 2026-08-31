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
from .anonymous import resolve_members
from .items import item_state, save_item_data, resolve_base_item, decorate_item, add_item
from .economy import add_money, balance
from .groups import ADMIN_STORY_GROUP

# Slash-command groups keep the command tree organized and below Discord's 100 global command limit.
STORY_GROUP = app_commands.Group(name="story", description="Story, objectives, secrets, and ballots.")


BOT = None
bot = None
secret_channels = {}
secret_objectives = {} # legacy in-memory cache; persistent objectives live in item_state()
traitor_channels = {}
dead_drops = {}
votes = {}

def is_staff(interaction: discord.Interaction):
  return interaction.guild is not None and (
    interaction.user.guild_permissions.manage_guild
    or interaction.user.guild_permissions.manage_channels
    or interaction.user.guild_permissions.administrator
  )

def is_staff_member(member: discord.Member | None):
  return member is not None and (
    member.guild_permissions.manage_guild
    or member.guild_permissions.manage_channels
    or member.guild_permissions.administrator
  )


async def safe_delete_later(channel_id: int, message_id: int, seconds: int):
  await asyncio.sleep(seconds)
  try:
    channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    message = await channel.fetch_message(message_id)
    await message.delete()
  except (discord.NotFound, discord.Forbidden, discord.HTTPException):
    pass

@STORY_GROUP.command(name="secret-channel", description="Create a private channel with any players you choose.")
@app_commands.describe(
  name="Name of the secret channel.",
  users="Discord usernames or @mentions, separated by spaces or commas."
)
async def secret_channel(interaction: discord.Interaction, name: str, users: str):
  """Any player can create a secret channel; the creator is always included."""
  guild = interaction.guild
  if guild is None:
    await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
    return

  if guild.me is None or not guild.me.guild_permissions.manage_channels:
    await interaction.response.send_message("I need the Manage Channels permission to create secret channels.", ephemeral=True)
    return

  members, missing = await resolve_members(guild, users)
  if not members:
    await interaction.response.send_message(
      "I couldn't find any of those users. Enter their Discord username(s), for example `PlayerOne PlayerTwo`.",
      ephemeral=True
    )
    return

  # The creator is always able to see their own secret channel.
  members.append(interaction.user)
  members = list(dict.fromkeys(members))

  overwrites = {
    guild.default_role: discord.PermissionOverwrite(view_channel=False),
    guild.me: discord.PermissionOverwrite(
      view_channel=True,
      send_messages=True,
      read_message_history=True,
      manage_messages=True,
    ),
  }
  for member in members:
    overwrites[member] = discord.PermissionOverwrite(
      view_channel=True,
      send_messages=True,
      read_message_history=True,
    )

  try:
    channel = await guild.create_text_channel(
      name[:90],
      overwrites=overwrites,
      reason=f"RPG secret channel created by {interaction.user}"
    )
    secret_channels[channel.id] = {member.id for member in members}

    note = f"Secret channel created: {channel.mention}"
    if missing:
      note += f"\nCouldn't find: {', '.join(missing[:10])}"
    await interaction.response.send_message(note, ephemeral=True)
  except discord.Forbidden:
    await interaction.response.send_message("I need Manage Channels permission.", ephemeral=True)
  except discord.HTTPException:
    await interaction.response.send_message("Discord rejected the channel creation. Try a different channel name.", ephemeral=True)


@STORY_GROUP.command(name="traitor-channel", description="Create a private channel for you and other traitors.")
@app_commands.describe(
  users="Discord usernames or @mentions of the traitors, separated by spaces or commas.",
  name="Optional channel name."
)
async def traitor_channel(interaction: discord.Interaction, users: str, name: str = "traitors"):
  """Any player can create a traitor channel; the creator is always included."""
  guild = interaction.guild
  if guild is None:
    await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
    return

  if guild.me is None or not guild.me.guild_permissions.manage_channels:
    await interaction.response.send_message("I need the Manage Channels permission to create traitor channels.", ephemeral=True)
    return

  members, missing = await resolve_members(guild, users)
  if not members:
    await interaction.response.send_message(
      "I couldn't find any of those users. Enter their Discord username(s), for example `PlayerOne PlayerTwo`.",
      ephemeral=True
    )
    return

  members.append(interaction.user)
  members = list(dict.fromkeys(members))

  overwrites = {
    guild.default_role: discord.PermissionOverwrite(view_channel=False),
    guild.me: discord.PermissionOverwrite(
      view_channel=True,
      send_messages=True,
      read_message_history=True,
      manage_messages=True,
    ),
  }
  for member in members:
    overwrites[member] = discord.PermissionOverwrite(
      view_channel=True,
      send_messages=True,
      read_message_history=True,
    )

  try:
    channel = await guild.create_text_channel(
      name[:90],
      overwrites=overwrites,
      reason=f"RPG traitor channel created by {interaction.user}"
    )
    traitor_channels[channel.id] = {member.id for member in members}

    note = f"Traitor channel created: {channel.mention}"
    if missing:
      note += f"\nCouldn't find: {', '.join(missing[:10])}"
    await interaction.response.send_message(note, ephemeral=True)
  except discord.Forbidden:
    await interaction.response.send_message("I need Manage Channels permission.", ephemeral=True)
  except discord.HTTPException:
    await interaction.response.send_message("Discord rejected the channel creation. Try a different channel name.", ephemeral=True)


@STORY_GROUP.command(name="dead-drop", description="Leave an anonymous message for another player to discover in DMs.")
@app_commands.describe(user="Who receives the dead drop.", message="The message.", alias="Optional custom anonymous name. Leave empty to stay Anonymous.", minutes="Minutes until it disappears; 0 means no timer.")
async def dead_drop(interaction: discord.Interaction, user: discord.User, message: str, alias: str = None, minutes: int = 0):
  alias = get_alias(alias) or "Anonymous"
  if minutes < 0 or minutes > 10080:
    await interaction.response.send_message("Minutes must be between 0 and 10080.", ephemeral=True)
    return
  try:
    sent = await user.send(f" **DEAD DROP**\n**{alias}**\n{message}")
    dead_drops[sent.id] = {"sender": interaction.user.id, "recipient": user.id}
    if minutes:
      asyncio.create_task(safe_delete_later(sent.channel.id, sent.id, minutes * 60))
    await interaction.response.send_message("Dead drop delivered anonymously.", ephemeral=True)
  except discord.Forbidden:
    await interaction.response.send_message("I couldn't DM that player.", ephemeral=True)
@ADMIN_STORY_GROUP.command(name="objective", description="Give a secret objective with an optional reward. GM/admin only.")
@app_commands.describe(
  user="The player receiving the objective.",
  objective="Their secret objective.",
  reward_type="Reward type: none, VG, item, or custom text.",
  reward_vg="VG reward. Only used when reward type is VG.",
  reward_item="Catalog item reward. Only used when reward type is item.",
  reward_text="Custom reward description. Only used when reward type is custom text."
)
@app_commands.choices(reward_type=[
  app_commands.Choice(name="No reward", value="none"),
  app_commands.Choice(name="VG", value="vg"),
  app_commands.Choice(name="Catalog item", value="item"),
  app_commands.Choice(name="Custom reward", value="text"),
])
async def objective(
  interaction: discord.Interaction,
  user: discord.User,
  objective: str,
  reward_type: app_commands.Choice[str] = None,
  reward_vg: int = 0,
  reward_item: str = None,
  reward_text: str = None,
):
  if not is_staff(interaction):
    await interaction.response.send_message("Only the GM/administrators can assign secret objectives.", ephemeral=True)
    return
  if interaction.guild is None:
    await interaction.response.send_message("Server only.", ephemeral=True)
    return
  if user.bot:
    await interaction.response.send_message("Objectives cannot be assigned to bots.", ephemeral=True)
    return
  if not objective.strip():
    await interaction.response.send_message("The objective cannot be empty.", ephemeral=True)
    return

  reward_kind = reward_type.value if reward_type else "none"
  reward = {"type": reward_kind}
  reward_line = "No reward"

  if reward_kind == "vg":
    if reward_vg <= 0:
      await interaction.response.send_message("Enter a positive `reward_vg` amount for a VG reward.", ephemeral=True)
      return
    reward["amount"] = int(reward_vg)
    reward_line = f"{reward_vg:,} VG"
  elif reward_kind == "item":
    if not reward_item:
      await interaction.response.send_message("Enter a catalog item in `reward_item`.", ephemeral=True)
      return
    base = resolve_base_item(reward_item)
    if not base:
      await interaction.response.send_message("That reward item was not found in the catalog. Use `/catalog` to check available items.", ephemeral=True)
      return
    reward["item"] = base.get("name")
    reward_line = f"Catalog item: **{base.get('name')}**"
  elif reward_kind == "text":
    if not reward_text or not reward_text.strip():
      await interaction.response.send_message("Enter a `reward_text` for a custom reward.", ephemeral=True)
      return
    reward["text"] = reward_text.strip()[:500]
    reward_line = reward["text"]

  state = item_state(interaction.guild.id)
  objectives = state.setdefault("objectives", {})
  key = str(user.id)
  objective_record = {
    "id": str(uuid.uuid4()),
    "user_id": user.id,
    "objective": objective.strip()[:2000],
    "reward": reward,
    "status": "active",
    "assigned_by": interaction.user.id,
    "assigned_at": datetime.now(timezone.utc).timestamp(),
    "completed_at": None,
  }
  objectives[key] = objective_record
  save_item_data()

  try:
    await user.send(
      f" **SECRET OBJECTIVE**\n{objective_record['objective']}\n\n"
      f" **REWARD**\n{reward_line}\n\n"
      "Complete the objective and the GM can use `/objective-complete` to award it."
    )
    await interaction.response.send_message(
      f"Secret objective assigned to {user.mention}. Reward: **{reward_line}**.", ephemeral=True
    )
  except discord.Forbidden:
    await interaction.response.send_message(
      f"Objective saved, but I couldn't DM {user.mention}. Reward: **{reward_line}**.", ephemeral=True
    )


@STORY_GROUP.command(name="my-objective", description="View your current secret RPG objective and reward.")
async def my_objective(interaction: discord.Interaction):
  if interaction.guild is None:
    await interaction.response.send_message("Server only.", ephemeral=True)
    return
  record = item_state(interaction.guild.id).setdefault("objectives", {}).get(str(interaction.user.id))
  if not record or record.get("status") != "active":
    await interaction.response.send_message("You don't currently have a secret objective.", ephemeral=True)
    return
  reward = record.get("reward", {"type": "none"})
  kind = reward.get("type", "none")
  if kind == "vg":
    reward_line = f"{int(reward.get('amount', 0)):,} VG"
  elif kind == "item":
    reward_line = f"Catalog item: **{reward.get('item', 'Unknown item')}**"
  elif kind == "text":
    reward_line = reward.get("text", "Custom reward")
  else:
    reward_line = "No reward"
  await interaction.response.send_message(
    f" **SECRET OBJECTIVE**\n{record.get('objective', 'Unknown objective')}\n\n **REWARD**\n{reward_line}",
    ephemeral=True,
  )


@ADMIN_STORY_GROUP.command(name="objective-complete", description="GM/admin: complete a player's objective and award its reward.")
@app_commands.describe(user="The player whose objective was completed.")
async def objective_complete(interaction: discord.Interaction, user: discord.User):
  if not is_staff(interaction):
    await interaction.response.send_message("Only the GM/administrators can complete objectives.", ephemeral=True)
    return
  if interaction.guild is None:
    await interaction.response.send_message("Server only.", ephemeral=True)
    return
  state = item_state(interaction.guild.id)
  objectives = state.setdefault("objectives", {})
  record = objectives.get(str(user.id))
  if not record or record.get("status") != "active":
    await interaction.response.send_message("That player has no active objective.", ephemeral=True)
    return

  reward = record.get("reward", {"type": "none"})
  kind = reward.get("type", "none")
  reward_line = "No reward"

  if kind == "vg":
    amount = int(reward.get("amount", 0))
    if amount > 0:
      add_money(interaction.guild.id, user.id, amount)
      reward_line = f"{amount:,} VG"
  elif kind == "item":
    item_name = reward.get("item")
    base = resolve_base_item(item_name) if item_name else None
    if not base:
      await interaction.response.send_message(
        f"The objective reward item **{item_name or 'Unknown'}** no longer exists in the catalog. Objective was not completed.",
        ephemeral=True,
      )
      return
    item = decorate_item(base)
    add_item(interaction.guild.id, user.id, item, held=True)
    reward_line = f"**{item.get('name', base.get('name'))}**"
  elif kind == "text":
    reward_line = reward.get("text", "Custom reward")

  record["status"] = "completed"
  record["completed_at"] = datetime.now(timezone.utc).timestamp()
  record["completed_by"] = interaction.user.id
  save_item_data()

  try:
    await user.send(f" **OBJECTIVE COMPLETE**\nYour secret objective has been completed.\n\n **REWARD**\n{reward_line}")
  except discord.Forbidden:
    pass
  await interaction.response.send_message(
    f"Completed {user.mention}'s objective and awarded: **{reward_line}**.", ephemeral=True
  )


@ADMIN_STORY_GROUP.command(name="objective-clear", description="GM/admin: clear a player's active secret objective without awarding it.")
@app_commands.describe(user="The player whose objective should be cleared.")
async def objective_clear(interaction: discord.Interaction, user: discord.User):
  if not is_staff(interaction):
    await interaction.response.send_message("Only the GM/administrators can clear objectives.", ephemeral=True)
    return
  if interaction.guild is None:
    await interaction.response.send_message("Server only.", ephemeral=True)
    return
  objectives = item_state(interaction.guild.id).setdefault("objectives", {})
  record = objectives.get(str(user.id))
  if not record or record.get("status") != "active":
    await interaction.response.send_message("That player has no active objective.", ephemeral=True)
    return
  record["status"] = "cleared"
  record["cleared_at"] = datetime.now(timezone.utc).timestamp()
  record["cleared_by"] = interaction.user.id
  save_item_data()
  await interaction.response.send_message(f"Cleared {user.mention}'s secret objective without awarding its reward.", ephemeral=True)


class VoteView(discord.ui.View):
  def __init__(self, vote_id):
    super().__init__(timeout=None)
    self.vote_id = vote_id
    vote = votes[vote_id]
    for index, option in enumerate(vote["options"]):
      button = discord.ui.Button(
        label=option[:80],
        style=discord.ButtonStyle.secondary,
        custom_id=f"vote:{vote_id}:{index}"
      )
      button.callback = self.make_callback(index)
      self.add_item(button)

  def make_callback(self, index):
    async def callback(interaction: discord.Interaction):
      vote = votes.get(self.vote_id)
      if not vote or vote["closed"]:
        await interaction.response.send_message("This ballot is closed.", ephemeral=True)
        return
      vote["choices"][interaction.user.id] = index
      await interaction.response.send_message("Your vote was recorded anonymously.", ephemeral=True)
    return callback


async def close_vote(vote_id):
  vote = votes.get(vote_id)
  if not vote:
    return
  await asyncio.sleep(vote["duration"] * 60)
  if vote["closed"]:
    return

  vote["closed"] = True
  counts = [0] * len(vote["options"])
  for choice in vote["choices"].values():
    counts[choice] += 1
  results = "\n".join(f"**{option}** — {counts[i]} vote(s)" for i, option in enumerate(vote["options"]))

  channel = bot.get_channel(vote["channel_id"])
  if channel:
    # Disable the voting buttons on the original ballot message.
    if vote.get("message_id"):
      try:
        message = await channel.fetch_message(vote["message_id"])
        closed_view = VoteView(vote_id)
        for child in closed_view.children:
          child.disabled = True
        await message.edit(view=closed_view)
      except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass

    await channel.send(
      f" **SECRET BALLOT CLOSED**\n**{vote['question']}**\n{results}"
    )


@ADMIN_STORY_GROUP.command(name="start-ballot", description="Start an anonymous secret ballot; individual votes stay hidden.")
@app_commands.describe(
  question="What are players voting on?",
  options="Options separated by |",
  minutes="How long the ballot stays open."
)
async def start_ballot(interaction: discord.Interaction, question: str, options: str, minutes: int = 10):
  if not is_staff(interaction):
    await interaction.response.send_message("Only the GM/administrators can start ballots.", ephemeral=True)
    return

  choices = [x.strip() for x in options.split("|") if x.strip()][:5]
  if len(choices) < 2 or minutes < 1 or minutes > 10080:
    await interaction.response.send_message("Provide 2-5 options and a duration from 1 to 10080 minutes.", ephemeral=True)
    return

  vote_id = str(uuid.uuid4())
  close_at = int(time.time() + minutes * 60)
  votes[vote_id] = {
    "question": question,
    "options": choices,
    "choices": {},
    "closed": False,
    "duration": minutes,
    "close_at": close_at,
    "channel_id": interaction.channel.id,
    "message_id": None,
  }

  countdown = f"<t:{close_at}:R>"
  await interaction.response.send_message(
    f" **SECRET BALLOT**\n**{question}**\n\n"
    f"Vote below. Individual choices are hidden.\n"
    f" **Closes:** {countdown}\n"
    f" **Ballot ID:** `{vote_id}`",
    view=VoteView(vote_id)
  )

  # Store the original message so its buttons can be disabled when the
  # ballot closes.
  try:
    original = await interaction.original_response()
    votes[vote_id]["message_id"] = original.id
  except discord.HTTPException:
    pass

  asyncio.create_task(close_vote(vote_id))


@STORY_GROUP.command(name="ballot-status", description="Show how many anonymous votes have been cast without revealing choices.")
@app_commands.describe(vote_id="The ballot ID shown when the ballot is created.")
async def ballot_status(interaction: discord.Interaction, vote_id: str):
  vote = votes.get(vote_id)
  if not vote:
    await interaction.response.send_message("Ballot not found.", ephemeral=True)
    return

  if vote["closed"]:
    countdown = "Closed"
  else:
    countdown = f"<t:{vote['close_at']}:R>"

  await interaction.response.send_message(
    f"**{vote['question']}**\n"
    f"Votes cast: {len(vote['choices'])}\n"
    f"Status: {'closed' if vote['closed'] else 'open'}\n"
    f" Closes: {countdown}",
    ephemeral=True
  )

COMMANDS = [secret_channel, traitor_channel, dead_drop, objective, my_objective, objective_complete, objective_clear, start_ballot, ballot_status]

def register(bot_instance):
  global BOT, bot
  BOT = bot_instance
  bot = bot_instance
  bot.tree.add_command(STORY_GROUP)