import time
import uuid

import discord
from discord import app_commands


from .items import item_state, save_item_data, item_key, find_possessed_item, remove_inventory_item
from .economy import balance, set_balance, vip_points, _set_vip_cards, add_vip_points

# Slash-command groups keep the command tree organized and below Discord's 100 global command limit.
TRADE_GROUP = app_commands.Group(name="trade", description="Player trading commands.")


TRADES = {}
REQUESTS = {}
BOT = None


def _is_participant(trade, user_id):
  return trade and user_id in (trade["initiator_id"], trade["target_id"])


def _owned_inventory_entries(guild_id, user_id):
  """Return every item in the player's inventory, whether held or secured."""
  state = item_state(guild_id)
  return state.get("inventories", {}).get(str(user_id), [])


def _find_owned_inventory_item(guild_id, user_id, name):
  key = item_key(name)
  for entry in _owned_inventory_entries(guild_id, user_id):
    item = entry.get("item", entry)
    if item_key(item.get("name", "")) == key:
      return entry, item
  return None, None


async def trade_item_autocomplete(interaction: discord.Interaction, current: str):
  """Show the player's inventory as Discord autocomplete choices for /trade-add."""
  if interaction.guild is None:
    return []

  current_key = item_key(current)
  choices = []
  seen = set()
  for entry in _owned_inventory_entries(interaction.guild.id, interaction.user.id):
    item = entry.get("item", entry)
    name = str(item.get("name", "")).strip()
    if not name or item_key(name) in seen:
      continue
    if current_key and current_key not in item_key(name):
      continue
    seen.add(item_key(name))
    status = " " if entry.get("secured", False) else ""
    choices.append(app_commands.Choice(name=(name + status)[:100], value=name))
    if len(choices) >= 25:
      break
  return choices


class ParticipantView(discord.ui.View):
  """Base view with a hard participant gate before any button callback runs."""

  def __init__(self, trade_id, timeout):
    super().__init__(timeout=timeout)
    self.trade_id = trade_id

  async def interaction_check(self, interaction: discord.Interaction):
    trade = TRADES.get(self.trade_id)
    if not trade:
      await interaction.response.send_message(" This trade is no longer active.", ephemeral=True)
      return False
    if not _is_participant(trade, interaction.user.id):
      await interaction.response.send_message(" You are not part of this trade.", ephemeral=True)
      return False
    return True

  async def on_timeout(self):
    trade = TRADES.get(self.trade_id)
    if trade:
      TRADES.pop(self.trade_id, None)


class TradeRequestView(discord.ui.View):
  def __init__(self, trade_id):
    super().__init__(timeout=120)
    self.trade_id = trade_id

  async def interaction_check(self, interaction: discord.Interaction):
    trade = TRADES.get(self.trade_id)
    if not trade:
      await interaction.response.send_message(" This trade request has expired.", ephemeral=True)
      return False
    # Only the invited player can interact with the request buttons.
    if interaction.user.id != trade["target_id"]:
      await interaction.response.send_message(
        " Only the invited player can accept or decline this trade.",
        ephemeral=True,
      )
      return False
    return True

  @discord.ui.button(label="Accept Trade", style=discord.ButtonStyle.success)
  async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
    trade = TRADES.get(self.trade_id)
    if not trade:
      return await interaction.response.send_message(" This trade request has expired.", ephemeral=True)

    trade["accepted"] = True
    # Acknowledge immediately, then edit the actual message that contains the buttons.
    await interaction.response.defer()
    await interaction.message.edit(content=trade_message(trade), view=TradeManageView(self.trade_id))

  @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
  async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
    trade = TRADES.pop(self.trade_id, None)
    if not trade:
      return await interaction.response.send_message(" This trade request has expired.", ephemeral=True)

    await interaction.response.defer()
    await interaction.message.edit(content=" **Trade declined.**", view=None)


class TradeManageView(ParticipantView):
  def __init__(self, trade_id):
    super().__init__(trade_id, timeout=600)

  @discord.ui.button(label="Confirm My Side", style=discord.ButtonStyle.success)
  async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
    trade = TRADES.get(self.trade_id)
    if not trade:
      return await interaction.response.send_message(" This trade is no longer active.", ephemeral=True)

    # Confirm the current offer snapshot.
    trade["confirmed"].add(interaction.user.id)
    await interaction.response.defer()

    if len(trade["confirmed"]) < 2:
      await interaction.message.edit(
        content=trade_message(trade) + "\n\n Your side is confirmed. Waiting for the other player.",
        view=self,
      )
      return

    ok, msg = complete_trade(trade)
    TRADES.pop(self.trade_id, None)
    await interaction.message.edit(
      content=(" **TRADE COMPLETE!**\n" + msg) if ok else (" **TRADE FAILED**\n" + msg),
      view=None,
    )

  @discord.ui.button(label="Cancel Trade", style=discord.ButtonStyle.danger)
  async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
    # interaction_check already guarantees this is one of the two traders.
    TRADES.pop(self.trade_id, None)
    await interaction.response.defer()
    await interaction.message.edit(content=" **Trade cancelled.**", view=None)


def trade_message(t):
  def names(uid):
    offered = t["offers"].get(str(uid), [])
    return ", ".join(offered) or "*(nothing)*"
  def cash(uid):
    return int(t.get("vg", {}).get(str(uid), 0))
  def vip(uid):
    return int(t.get("vip", {}).get(str(uid), 0))
  return (
    f" **Trade between <@{t['initiator_id']}> and <@{t['target_id']}>**\n\n"
    f"**<@{t['initiator_id']}> offers:** {names(t['initiator_id'])}\n"
    f" VG: **{cash(t['initiator_id']):,}** | VIP Points: **{vip(t['initiator_id']):,}**\n\n"
    f"**<@{t['target_id']}> offers:** {names(t['target_id'])}\n"
    f" VG: **{cash(t['target_id']):,}** | VIP Points: **{vip(t['target_id']):,}**\n\n"
    "Use `/trade add` for items and `/trade money` for VG or VIP Points. Both players must confirm."
  )


def complete_trade(t):
  guild_id = t["guild_id"]
  state = item_state(guild_id)
  resolved = []

  # Revalidate every offered item immediately before changing ownership.
  for uid, names in t["offers"].items():
    for name in names:
      _, rec = find_possessed_item(guild_id, name)
      if not rec or int(rec.get("owner_id", 0)) != int(uid):
        return False, f"Could not validate **{name}**. The item is missing or no longer owned by the offering player."
      resolved.append((int(uid), rec["item"]))

  a, b = int(t["initiator_id"]), int(t["target_id"])
  ca, cb = int(t.get("vg", {}).get(str(a), 0)), int(t.get("vg", {}).get(str(b), 0))
  va, vb = int(t.get("vip", {}).get(str(a), 0)), int(t.get("vip", {}).get(str(b), 0))
  if ca > balance(guild_id, a) or cb > balance(guild_id, b) or va > vip_points(guild_id, a) or vb > vip_points(guild_id, b):
    return False, "One player no longer has enough VG or VIP Points for the trade."

  # Remove all source inventory entries first. Nothing is changed if validation fails.
  removed = []
  for uid, item in resolved:
    if remove_inventory_item(guild_id, uid, item["id"]) is None:
      # Restore anything already removed if a consistency error occurs.
      now = time.time()
      for old_uid, old_item in removed:
        state["inventories"].setdefault(str(old_uid), []).append(
          old_item | {"claimed_at": now, "held": True}
        )
      return False, f"Ownership data for **{item['name']}** became inconsistent."
    removed.append((uid, item))

  now = time.time()
  for old_owner, item in resolved:
    new_owner = t["target_id"] if old_owner == t["initiator_id"] else t["initiator_id"]
    p = state.setdefault("possessions", {}).get(item["id"])
    was_secured = bool(p.get("secured", False)) if p else bool(item.get("secured", False))
    state["inventories"].setdefault(str(new_owner), []).append(
      item | {
        "claimed_at": now,
        "traded_at": now,
        "traded_from": old_owner,
        "secured": was_secured,
        "held": True,
      }
    )
    if p:
      p["owner_id"] = new_owner
      p["secured"] = was_secured
      p["held"] = True
      p["traded_at"] = now
    else:
      state["possessions"][item["id"]] = {
        "owner_id": new_owner,
        "item": item,
        "secured": was_secured,
        "claimed_at": now,
        "held": True,
        "traded_at": now,
      }

  # VG and VIP Points are exchanged at final confirmation.
  set_balance(guild_id, a, balance(guild_id, a) - ca + cb)
  set_balance(guild_id, b, balance(guild_id, b) - cb + ca)
  _set_vip_cards(guild_id, a, vip_points(guild_id, a) - va + vb)
  _set_vip_cards(guild_id, b, vip_points(guild_id, b) - vb + va)

  save_item_data()
  return True, "All offered items were transferred successfully."


@TRADE_GROUP.command(name="start", description="Start a trade request with another player.")
@app_commands.describe(user="The player you want to trade with.")
async def trade(interaction: discord.Interaction, user: discord.Member):
  if interaction.guild is None:
    return await interaction.response.send_message(
      "Trades can only be started in a server.", ephemeral=True
    )
  if user.bot or user.id == interaction.user.id:
    return await interaction.response.send_message(
      " Choose another human player.", ephemeral=True
    )

  trade_id = uuid.uuid4().hex
  TRADES[trade_id] = {
    "guild_id": interaction.guild.id,
    "initiator_id": interaction.user.id,
    "target_id": user.id,
    "offers": {
      str(interaction.user.id): [],
      str(user.id): [],
    },
    "confirmed": set(),
    "vg": {str(interaction.user.id): 0, str(user.id): 0},
    "vip": {str(interaction.user.id): 0, str(user.id): 0},
    "accepted": False,
    "created": time.time(),
  }

  await interaction.response.send_message(
    f" {user.mention}, **{interaction.user.display_name}** wants to trade with you.\n\n"
    "Press **Accept Trade** to begin.",
    view=TradeRequestView(trade_id),
  )
  msg = await interaction.original_response()
  TRADES[trade_id]["channel_id"] = msg.channel.id
  TRADES[trade_id]["message_id"] = msg.id


def _find_user_trade(guild_id, user_id):
  for trade in TRADES.values():
    if (
      trade["guild_id"] == guild_id
      and trade.get("accepted")
      and user_id in (trade["initiator_id"], trade["target_id"])
    ):
      return trade
  return None


async def _edit_trade_message(trade):
  try:
    channel = BOT.get_channel(trade.get("channel_id"))
    if channel is None:
      channel = await BOT.fetch_channel(trade.get("channel_id"))
    message = await channel.fetch_message(trade.get("message_id"))
    await message.edit(content=trade_message(trade), view=TradeManageView(next(k for k,v in TRADES.items() if v is trade)))
    return True
  except (discord.NotFound, discord.HTTPException, discord.Forbidden, StopIteration):
    return False


@TRADE_GROUP.command(name="add", description="Add one of your items to the active trade.")
@app_commands.describe(item_name="Choose an item from your inventory.")
@app_commands.autocomplete(item_name=trade_item_autocomplete)
async def trade_add(interaction: discord.Interaction, item_name: str):
  if interaction.guild is None:
    return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)

  trade = _find_user_trade(interaction.guild.id, interaction.user.id)
  if not trade:
    return await interaction.response.send_message(
      " You do not have an active accepted trade.", ephemeral=True
    )

  entry, item = _find_owned_inventory_item(interaction.guild.id, interaction.user.id, item_name)
  if not entry or not item:
    return await interaction.response.send_message(" You don't own that item.", ephemeral=True)
  offered = trade["offers"][str(interaction.user.id)]
  if any(item_key(n) == item_key(item["name"]) for n in offered):
    return await interaction.response.send_message("That item is already offered.", ephemeral=True)

  offered.append(item["name"])
  trade["confirmed"].clear()

  await interaction.response.send_message(" Offer updated.", ephemeral=True)
  await _edit_trade_message(trade)


@TRADE_GROUP.command(name="remove", description="Remove one of your items from the active trade.")
@app_commands.describe(item_name="Exact item name to remove.")
async def trade_remove(interaction: discord.Interaction, item_name: str):
  if interaction.guild is None:
    return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)

  trade = _find_user_trade(interaction.guild.id, interaction.user.id)
  if not trade:
    return await interaction.response.send_message(" You do not have an active trade.", ephemeral=True)

  arr = trade["offers"][str(interaction.user.id)]
  new_arr = [n for n in arr if item_key(n) != item_key(item_name)]
  if len(new_arr) == len(arr):
    return await interaction.response.send_message(" That item is not in your current offer.", ephemeral=True)

  trade["offers"][str(interaction.user.id)] = new_arr
  trade["confirmed"].clear()
  await interaction.response.send_message(" Offer updated.", ephemeral=True)
  await _edit_trade_message(trade)



@TRADE_GROUP.command(name="money", description="Add VG, VIP Points, or both to your active trade offer.")
@app_commands.describe(
  amount="Optional VG amount to offer. Use 0 to clear the VG offer.",
  vip_points="Optional VIP Points to offer. Use 0 to clear the VIP offer.",
)
async def trade_money(interaction: discord.Interaction, amount: int = 0, vip_points: int = 0):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  trade = _find_user_trade(interaction.guild.id, interaction.user.id)
  if not trade:
    return await interaction.response.send_message("You do not have an active trade.", ephemeral=True)
  if amount < 0 or vip_points < 0:
    return await interaction.response.send_message("Amounts cannot be negative.", ephemeral=True)
  gid, uid = interaction.guild.id, interaction.user.id
  if amount > balance(gid, uid) or vip_points > globals()["vip_points"](gid, uid):
    return await interaction.response.send_message("You do not have enough VG or VIP Points.", ephemeral=True)
  trade["vg"][str(uid)] = amount
  trade["vip"][str(uid)] = vip_points
  trade["confirmed"].clear()
  await interaction.response.send_message("Currency offer updated.", ephemeral=True)
  await _edit_trade_message(trade)


COMMANDS = [trade, trade_add, trade_remove, trade_money]


def register(bot):
  global BOT
  BOT = bot
  bot.tree.add_command(TRADE_GROUP)