import time
import random
import re
import asyncio
from datetime import datetime, timezone
import discord
from discord import app_commands
from ..items import item_state, save_item_data, item_key, find_possessed_item, remove_inventory_item, ITEM_CATALOG, resolve_base_item, custom_catalog_items
from ...state import is_staff
from ..groups import ADMIN_ECONOMY_GROUP, ADMIN_FACTION_GROUP

# Slash-command groups keep the command tree organized and below Discord's 100 global command limit.
ECONOMY_GROUP = app_commands.Group(name="economy", description="Vesperian Gold, custom currency, and shop commands.")
FACTION_GROUP = app_commands.Group(name="faction", description="Faction commands.")


# Campaign currency system
# VG = Vesperian Gold. VIP Cards are a separate high-value item/currency-equivalent.
CURRENCY_KEY = "vg"
CURRENCY_LABEL = "VG"
LEGACY_CURRENCY_KEY = "currency"
VG_PER_VIP_FACE_VALUE = 60_000
VIP_EFFECTIVE_VG_VALUE = 300_000
VIP_VALUE_MULTIPLIER = 5
VIP_AUTO_CONVERSION_VG = VG_PER_VIP_FACE_VALUE
VIP_CARDS_KEY = "vip_points"
LEGACY_VIP_CARDS_KEY = "vip_cards"


def format_vg(amount):
  return f"{int(amount):,} VG"


def vip_value_text(count=1):
  count = int(count)
  return (f"{count} VIP Card{'s' if count != 1 else ''} = "
          f"{count * VG_PER_VIP_FACE_VALUE:,} VG normal value / "
          f"{count * VIP_EFFECTIVE_VG_VALUE:,} VG VIP-shop purchasing power")
SHOP_KEY = "shop"
VIP_SHOP_KEY = "vip_shop"
FACTION_KEY = "factions"
CUSTOM_CURRENCIES_KEY = "currencies"

RARITY_SELL_VALUES = {
  "Trash": 2, "Crude": 4, "Basic": 6, "Common": 10, "Sturdy": 14, "Uncommon": 20,
  "Refined": 28, "Rare": 40, "Elite": 55, "Epic": 75, "Flawless": 100, "Exotic": 135,
  "Legendary": 180, "Mythic": 240, "Relic": 320, "Scourged": 420, "Cursed": 550, "Corrupted": 700,
  "Vile": 875, "Blighted": 1100, "Malicious": 1400, "Sinister": 1750, "Nefarious": 2200,
  "Maleficent": 2750, "Demonic": 3400, "Damned": 4200, "Abyssal": 5200, "Profane": 6400,
  "Diabolical": 7800, "Eldritch": 9500, "Infernal": 11500, "Unholy": 14000, "Cataclysmic": 17000,
  "Annihilation": 20500, "Oblivion": 24500, "Sanctified": 29000, "Blessed": 34000, "Hallowed": 40000,
  "Anointed": 47000, "Radiant": 55000, "Ascendant": 65000, "Sacrosanct": 76000, "Ethereal": 90000,
  "Celestial": 105000, "Immortal": 122000, "Angelic": 142000, "Cherubic": 165000, "Seraphic": 192000,
  "Elysian": 225000, "Divine": 260000, "Archangelic": 300000, "Empyrean": 350000,
  "Omnipotent": 410000, "Transcendent": 480000, "Infinite": 600000,
}


def guild_state(guild_id):
  st = item_state(guild_id)
  st.setdefault("economy", {"balances": {}, "shop": {}, "factions": {}})
  eco = st["economy"]
  eco.setdefault("balances", {})
  eco.setdefault("shop", {})
  eco.setdefault(VIP_SHOP_KEY, {})
  eco.setdefault("factions", {})
  eco.setdefault(VIP_CARDS_KEY, {})
  # Custom currencies are additive: creating one never replaces VG or another currency.
  eco.setdefault(CUSTOM_CURRENCIES_KEY, {})
  for currency_id, currency in list(eco[CUSTOM_CURRENCIES_KEY].items()):
    if isinstance(currency, dict):
      currency.setdefault("id", str(currency_id))
      currency.setdefault("name", str(currency_id))
      currency.setdefault("symbol", str(currency.get("name", currency_id))[:4].upper())
      currency.setdefault("value_vg", 1)
      currency.setdefault("description", "")
      currency.setdefault("balances", {})
  legacy_vip = eco.pop(LEGACY_VIP_CARDS_KEY, None)
  if isinstance(legacy_vip, dict):
    for uid, amount in legacy_vip.items():
      try:
        eco[VIP_CARDS_KEY][str(uid)] = int(eco[VIP_CARDS_KEY].get(str(uid), 0) or 0) + max(0, int(amount))
      except (TypeError, ValueError):
        continue
  # VIP conversion is explicit through the Bank page. Do not automatically
  # convert balances merely because they reach 60,000 VG.

  # Migrate the old generic currency balance to VG exactly once.
  legacy = eco.pop(LEGACY_CURRENCY_KEY, None)
  if isinstance(legacy, dict):
    for uid, amount in legacy.items():
      key = str(uid)
      if key not in eco["balances"]:
        try:
          eco["balances"][key] = max(0, int(amount))
        except (TypeError, ValueError):
          eco["balances"][key] = 0
  return eco


def vip_points(guild_id, user_id):
  return int(guild_state(guild_id)[VIP_CARDS_KEY].get(str(user_id), 0) or 0)


def vip_cards(guild_id, user_id):
  return vip_points(guild_id, user_id)


def add_vip_points(guild_id, user_id, amount):
  current = vip_points(guild_id, user_id)
  guild_state(guild_id)[VIP_CARDS_KEY][str(user_id)] = current + max(0, int(amount))


def _set_vip_cards(guild_id, user_id, amount):
  guild_state(guild_id)[VIP_CARDS_KEY][str(user_id)] = max(0, int(amount))

def spend_economy_value(guild_id, user_id, cost, vip_card_value=VG_PER_VIP_FACE_VALUE):
  """Pay a VG-denominated price using VG and/or VIP Cards.

  Normal economy purchases use the VIP Card base value of 60,000 VG.
  A special VIP-shop purchase can pass vip_card_value=300,000 VG because
  the VIP shop gives each card five times the normal purchasing power.
  """
  cost = int(cost)
  if cost <= 0:
    return False, "The cost must be positive."
  vg = balance(guild_id, user_id)
  vip = vip_points(guild_id, user_id)
  if vg >= cost:
    set_balance(guild_id, user_id, vg - cost)
    return True, f"Paid **{cost:,} VG**."
  remaining = cost - vg
  vip_used = (remaining + vip_card_value - 1) // vip_card_value
  if vip_used > vip:
    total = vg + vip * vip_card_value
    return False, f"You need **{cost:,} VG of spending value**, but you only have **{total:,} VG of spending value**."
  change = vip_used * vip_card_value - remaining
  _set_vip_cards(guild_id, user_id, vip - vip_used)
  set_balance(guild_id, user_id, change)
  return True, (
    f"Paid **{vg:,} VG** + **{vip_used} VIP Card{'s' if vip_used != 1 else ''}**. "
    f"Unused VIP value returned as **{change:,} VG**."
  )

def economy_spending_value(guild_id, user_id):
  """Normal economy spending value. VIP Cards are worth 60,000 VG here."""
  return balance(guild_id, user_id) + vip_points(guild_id, user_id) * VG_PER_VIP_FACE_VALUE


def _normalize_balance(guild_id, user_id):
  eco = guild_state(guild_id)
  uid = str(user_id)
  try:
    amount = max(0, int(eco["balances"].get(uid, 100)))
  except (TypeError, ValueError):
    amount = 100
  eco["balances"][uid] = amount
  return amount

def balance(guild_id, user_id):
  return _normalize_balance(guild_id, user_id)

def set_balance(guild_id, user_id, amount):
  guild_state(guild_id)["balances"][str(user_id)] = max(0, int(amount))

def convert_vg_to_vip(guild_id, user_id, vg_amount):
  vg_amount = int(vg_amount)
  if vg_amount <= 0:
    return False, "The VG amount must be positive."
  current = balance(guild_id, user_id)
  cards, remainder = divmod(vg_amount, VG_PER_VIP_FACE_VALUE)
  if cards <= 0:
    return False, f"You need at least **{VG_PER_VIP_FACE_VALUE:,} VG** to convert into a VIP Card."
  if current < vg_amount:
    return False, f"You only have **{current:,} VG**."
  set_balance(guild_id, user_id, current - vg_amount)
  add_vip_points(guild_id, user_id, cards)
  remaining_balance = balance(guild_id, user_id)
  return True, f"Converted **{vg_amount:,} VG** into **{cards} VIP Card{'s' if cards != 1 else ''}**. Remaining VG: **{remaining_balance:,} VG**."

def convert_vip_to_vg(guild_id, user_id, cards):
  cards = int(cards)
  if cards <= 0:
    return False, "The VIP Card amount must be positive."
  current = vip_points(guild_id, user_id)
  if current < cards:
    return False, f"You only have **{current} VIP Card{'s' if current != 1 else ''}."
  _set_vip_cards(guild_id, user_id, current - cards)
  amount = cards * VG_PER_VIP_FACE_VALUE
  set_balance(guild_id, user_id, balance(guild_id, user_id) + amount)
  return True, f"Converted **{cards} VIP Card{'s' if cards != 1 else ''}** into **{amount:,} VG**."

def add_money(guild_id, user_id, amount):
  set_balance(guild_id, user_id, balance(guild_id, user_id) + int(amount))



def _currency_key(value):
  return re.sub(r"[^a-z0-9]+", "-", str(value).strip().casefold()).strip("-")


def custom_currencies(guild_id):
  return guild_state(guild_id)[CUSTOM_CURRENCIES_KEY]


def get_custom_currency(guild_id, currency):
  key = _currency_key(currency)
  data = custom_currencies(guild_id)
  if key in data:
    return data[key]
  return next((v for v in data.values() if _currency_key(v.get("name", "")) == key or _currency_key(v.get("symbol", "")) == key), None)


def custom_currency_balance(guild_id, user_id, currency):
  entry = get_custom_currency(guild_id, currency)
  if not entry:
    return None
  try:
    return max(0, int(entry.setdefault("balances", {}).get(str(user_id), 0)))
  except (TypeError, ValueError):
    entry.setdefault("balances", {})[str(user_id)] = 0
    return 0


def set_custom_currency_balance(guild_id, user_id, currency, amount):
  entry = get_custom_currency(guild_id, currency)
  if not entry:
    return False
  entry.setdefault("balances", {})[str(user_id)] = max(0, int(amount))
  return True


def add_custom_currency(guild_id, user_id, currency, amount):
  current = custom_currency_balance(guild_id, user_id, currency)
  if current is None:
    return False
  set_custom_currency_balance(guild_id, user_id, currency, current + int(amount))
  return True


def remove_custom_currency(guild_id, user_id, currency, amount):
  current = custom_currency_balance(guild_id, user_id, currency)
  if current is None or int(amount) < 0 or current < int(amount):
    return False
  set_custom_currency_balance(guild_id, user_id, currency, current - int(amount))
  return True


def custom_currency_spending_value(guild_id, user_id, currency, amount):
  """Return whether the player can pay an exact amount of a custom currency."""
  entry = get_custom_currency(guild_id, currency)
  if not entry:
    return False, "Currency not found."
  amount = int(amount)
  if amount <= 0:
    return False, "Price must be positive."
  current = custom_currency_balance(guild_id, user_id, currency)
  if current < amount:
    return False, f"You only have **{current:,} {entry.get('symbol', entry.get('name', 'units'))}**."
  set_custom_currency_balance(guild_id, user_id, currency, current - amount)
  return True, f"Paid **{amount:,} {entry.get('symbol', entry.get('name', 'units'))}**."


@ECONOMY_GROUP.command(name="currencies", description="View all campaign currencies and their VG worth.")
async def list_currencies(interaction: discord.Interaction):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  lines = [
    f"**{entry.get('name', 'Unknown')} ({entry.get('symbol', '')})** — 1 = **{float(entry.get('value_vg', 0)):g} VG**\n{entry.get('description', '')}"
    for entry in custom_currencies(interaction.guild.id).values()
  ]
  lines.insert(0, f"**Vesperian Gold (VG)** — 1 VG = **1 VG**\nThe standard campaign currency remains active.")
  await interaction.response.send_message("**CAMPAIGN CURRENCIES**\n\n" + "\n\n".join(lines), ephemeral=True)


@ADMIN_ECONOMY_GROUP.command(name="currency-create", description="GM: add a new campaign currency without removing existing currencies.")
@app_commands.describe(name="Currency name.", symbol="Short symbol.", value_vg="How much 1 unit is worth in VG.", description="What the currency is used for.")
async def currency_create(interaction: discord.Interaction, name: str, symbol: str, value_vg: float, description: str = ""):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  if not name.strip() or not symbol.strip() or value_vg < 0:
    return await interaction.response.send_message("Provide a valid name, symbol, and non-negative VG value.", ephemeral=True)
  key = _currency_key(name)
  if key in {"vg", "vesperian-gold", "vperian-gold"} or get_custom_currency(interaction.guild.id, name):
    return await interaction.response.send_message("That currency already exists. Use currency-edit instead.", ephemeral=True)
  custom_currencies(interaction.guild.id)[key] = {
    "id": key, "name": name.strip(), "symbol": symbol.strip().upper()[:8],
    "value_vg": float(value_vg), "description": description.strip(), "balances": {}
  }
  save_item_data()
  await interaction.response.send_message(
    f"Added **{name.strip()} ({symbol.strip().upper()[:8]})**. Existing currencies, including **VG**, were not changed.",
    ephemeral=True,
  )


@ADMIN_ECONOMY_GROUP.command(name="currency-edit", description="GM: edit a campaign currency's value, name, symbol, or description.")
@app_commands.describe(currency="Existing custom currency.", name="New name.", symbol="New symbol.", value_vg="New VG worth per unit.", description="New description.")
async def currency_edit(interaction: discord.Interaction, currency: str, name: str = None, symbol: str = None, value_vg: float = None, description: str = None):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  entry = get_custom_currency(interaction.guild.id, currency)
  if not entry:
    return await interaction.response.send_message("Custom currency not found. VG is protected and cannot be replaced.", ephemeral=True)
  if value_vg is not None and value_vg < 0:
    return await interaction.response.send_message("VG worth cannot be negative.", ephemeral=True)
  if name is not None and name.strip():
    entry["name"] = name.strip()
  if symbol is not None and symbol.strip():
    entry["symbol"] = symbol.strip().upper()[:8]
  if value_vg is not None:
    entry["value_vg"] = float(value_vg)
  if description is not None:
    entry["description"] = description.strip()
  save_item_data()
  await interaction.response.send_message(f"Updated **{entry['name']} ({entry['symbol']})**.", ephemeral=True)


@ADMIN_ECONOMY_GROUP.command(name="currency-give", description="GM: give a custom currency to a player.")
@app_commands.describe(user="Player receiving the currency.", currency="Custom currency.", amount="Amount to give.")
async def currency_give(interaction: discord.Interaction, user: discord.Member, currency: str, amount: int):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  if user.bot or amount <= 0:
    return await interaction.response.send_message("Invalid player or amount.", ephemeral=True)
  entry = get_custom_currency(interaction.guild.id, currency)
  if not entry:
    return await interaction.response.send_message("Custom currency not found.", ephemeral=True)
  add_custom_currency(interaction.guild.id, user.id, currency, amount)
  save_item_data()
  await interaction.response.send_message(f"Gave **{amount:,} {entry['symbol']}** to {user.mention}. VG remains separate.", ephemeral=True)


@ADMIN_ECONOMY_GROUP.command(name="currency-remove", description="GM: remove a custom currency from a player.")
@app_commands.describe(user="Player.", currency="Custom currency.", amount="Amount to remove.")
async def currency_remove(interaction: discord.Interaction, user: discord.Member, currency: str, amount: int):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  if amount <= 0:
    return await interaction.response.send_message("Amount must be positive.", ephemeral=True)
  entry = get_custom_currency(interaction.guild.id, currency)
  if not entry:
    return await interaction.response.send_message("Custom currency not found.", ephemeral=True)
  current = custom_currency_balance(interaction.guild.id, user.id, currency)
  if current < amount:
    return await interaction.response.send_message(f"{user.mention} only has **{current:,} {entry['symbol']}**.", ephemeral=True)
  remove_custom_currency(interaction.guild.id, user.id, currency, amount)
  save_item_data()
  await interaction.response.send_message(f"Removed **{amount:,} {entry['symbol']}** from {user.mention}.", ephemeral=True)


@ADMIN_ECONOMY_GROUP.command(name="currency-balances", description="GM: inspect balances for every custom currency.")
async def currency_balances(interaction: discord.Interaction):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  lines = []
  for entry in custom_currencies(interaction.guild.id).values():
    balances = [f"<@{uid}>: {int(amount):,} {entry['symbol']}" for uid, amount in entry.get("balances", {}).items() if int(amount or 0) > 0]
    lines.append(f"**{entry['name']} ({entry['symbol']})** — " + (", ".join(balances) if balances else "No balances"))
  await interaction.response.send_message("**CUSTOM CURRENCY BALANCES**\n\n" + ("\n".join(lines) if lines else "No custom currencies exist."), ephemeral=True)


def money_autocomplete(interaction, current):
  return []

@ECONOMY_GROUP.command(name="balance", description="View your VG and VIP Points.")
async def economy_balance(interaction: discord.Interaction):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  try:
    vg = balance(interaction.guild.id, interaction.user.id)
    vip = vip_points(interaction.guild.id, interaction.user.id)
    await interaction.response.send_message(
      f"**ECONOMY**\n\nVG: **{vg:,}**\nVIP Points: **{vip:,}**\n\n1 VIP Point = **{VG_PER_VIP_FACE_VALUE:,} VG** face value.",
      ephemeral=True,
    )
  except Exception as exc:
    print(f"Economy balance error: {type(exc).__name__}: {exc}")
    if not interaction.response.is_done():
      await interaction.response.send_message("The economy data could not be loaded. Please try again.", ephemeral=True)

@ECONOMY_GROUP.command(name="overview", description="Open the economy overview.")
async def economy_overview(interaction: discord.Interaction):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  try:
    vg = balance(interaction.guild.id, interaction.user.id)
    vip = vip_points(interaction.guild.id, interaction.user.id)
    market = _stock_state(interaction.guild.id)
    companies = market.get(STOCK_COMPANIES_KEY, {})
    await interaction.response.send_message(
      "**ECONOMY**\n\n"
      f"VG: **{vg:,}**\n"
      f"VIP Points: **{vip:,}**\n"
      f"Stock Market Index: **{float(market.get('index', 0)):+.2f}%**\n"
      f"Companies: **{len(companies)}**\n\n"
      "Use `/economy stocks` to view companies, `/economy invest` to buy shares, "
      "`/economy sell-stock` to sell shares, and `/economy gamble` or `/economy gambling` to gamble VG.",
      ephemeral=True,
    )
  except Exception as exc:
    print(f"Economy overview error: {type(exc).__name__}: {exc}")
    if not interaction.response.is_done():
      await interaction.response.send_message("The economy page could not be loaded. Please try again.", ephemeral=True)

@ECONOMY_GROUP.command(name="give", description="Give VG, VIP Points, or both to another player.")
@app_commands.describe(
  user="Player receiving the currency.",
  amount="Optional amount of VG to give.",
  vip_points="Optional number of VIP Points to give.",
)
async def give_money(interaction, user: discord.Member, amount: int | None = None, vip_points: int | None = None):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  if user.bot or user.id == interaction.user.id:
    return await interaction.response.send_message("Invalid recipient.", ephemeral=True)
  if amount is None and vip_points is None:
    return await interaction.response.send_message("Provide VG, VIP Points, or both.", ephemeral=True)
  if amount is not None and amount <= 0:
    return await interaction.response.send_message("VG amount must be positive.", ephemeral=True)
  if vip_points is not None and vip_points <= 0:
    return await interaction.response.send_message("VIP Points must be positive.", ephemeral=True)
  gid, uid = interaction.guild.id, interaction.user.id
  vg = balance(gid, uid)
  vip = vip_points_fn = globals()["vip_points"](gid, uid)
  if amount is not None and vg < amount:
    return await interaction.response.send_message(f"You only have **{vg:,} VG**.", ephemeral=True)
  if vip_points is not None and vip < vip_points:
    return await interaction.response.send_message(f"You only have **{vip:,} VIP Points**.", ephemeral=True)
  set_balance(gid, uid, vg - (amount or 0))
  _set_vip_cards(gid, uid, vip - (vip_points or 0))
  add_money(gid, user.id, amount or 0)
  if vip_points:
    add_vip_points(gid, user.id, vip_points)
  save_item_data()
  parts = []
  if amount:
    parts.append(f"**{amount:,} VG**")
  if vip_points:
    parts.append(f"**{vip_points:,} VIP Point{'s' if vip_points != 1 else ''}**")
  await interaction.response.send_message(
    f"You gave {' + '.join(parts)} to {user.mention}.\n"
    f"Your balance: **{balance(gid, uid):,} VG** | **{globals()['vip_points'](gid, uid):,} VIP Points**."
  )

@ADMIN_ECONOMY_GROUP.command(name="add", description="Admin: add VG, VIP Points, or both to a player.")
@app_commands.describe(user="Player.", amount="Optional VG amount.", vip_points="Optional VIP Points.")
async def add_money_command(interaction: discord.Interaction, user: discord.Member, amount: int | None = None, vip_points: int | None = None):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  if amount is None and vip_points is None:
    return await interaction.response.send_message("Provide VG, VIP Points, or both.", ephemeral=True)
  if amount is not None and amount <= 0:
    return await interaction.response.send_message("VG amount must be positive.", ephemeral=True)
  if vip_points is not None and vip_points <= 0:
    return await interaction.response.send_message("VIP Points must be positive.", ephemeral=True)
  if amount:
    add_money(interaction.guild.id, user.id, amount)
  if vip_points:
    add_vip_points(interaction.guild.id, user.id, vip_points)
  save_item_data()
  await interaction.response.send_message(
    f"Added **{amount or 0:,} VG** and **{vip_points or 0:,} VIP Points** to {user.mention}.",
    ephemeral=True,
  )

@ADMIN_ECONOMY_GROUP.command(name="remove", description="Admin: remove VG, VIP Points, or both from a player.")
@app_commands.describe(user="Player.", amount="Optional VG amount.", vip_points="Optional VIP Points.")
async def remove_money_command(interaction: discord.Interaction, user: discord.Member, amount: int | None = None, vip_points: int | None = None):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  if amount is None and vip_points is None:
    return await interaction.response.send_message("Provide VG, VIP Points, or both.", ephemeral=True)
  if amount is not None and amount < 0 or vip_points is not None and vip_points < 0:
    return await interaction.response.send_message("Amounts cannot be negative.", ephemeral=True)
  gid, uid = interaction.guild.id, user.id
  vg, vip = balance(gid, uid), globals()["vip_points"](gid, uid)
  if (amount or 0) > vg or (vip_points or 0) > vip:
    return await interaction.response.send_message(
      f"Insufficient balance. Player has **{vg:,} VG** and **{vip:,} VIP Points**.", ephemeral=True
    )
  set_balance(gid, uid, vg - (amount or 0))
  _set_vip_cards(gid, uid, vip - (vip_points or 0))
  save_item_data()
  await interaction.response.send_message(
    f"Removed **{amount or 0:,} VG** and **{vip_points or 0:,} VIP Points** from {user.mention}.",
    ephemeral=True,
  )

@ECONOMY_GROUP.command(name="values", description="View the VG and VIP Card exchange values.")
async def economy_values(interaction: discord.Interaction):
  await interaction.response.send_message(
    "**Campaign Currency Values**\n\n"
    f"**1 VIP Point** = **{VG_PER_VIP_FACE_VALUE:,} VG** face value.\n"
    f"**{VIP_AUTO_CONVERSION_VG:,} VG can be converted into 1 VIP Point at the Bank**.\n"
    "Normal shops treat each VIP Card as **60,000 VG**, the standard exchange value.\n"
    f"VIP shops are different: each VIP Card has **{VIP_EFFECTIVE_VG_VALUE:,} VG purchasing power** there (5x the normal value).\n"
    f"**8 VIP Cards** = **{8 * VG_PER_VIP_FACE_VALUE:,} VG** normal value, or **{8 * VIP_EFFECTIVE_VG_VALUE:,} VG** VIP-shop purchasing power.\n\n"
    "VG is the standard campaign currency. VIP Cards are a high-status currency whose purchasing value depends on the shop." ,
    ephemeral=True,
  )

@ECONOMY_GROUP.command(name="shop", description="View the normal shop. VIP Cards can cover VG costs with change returned as VG.")
async def shop(interaction: discord.Interaction):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  shop_data = guild_state(interaction.guild.id)[SHOP_KEY]
  lines = []
  for key, data in shop_data.items():
    if data.get("type") == "vip_points":
      continue
    currency_key = data.get("currency", "vg")
    currency_entry = get_custom_currency(interaction.guild.id, currency_key) if currency_key != "vg" else None
    symbol = currency_entry.get("symbol", currency_key) if currency_entry else "VG"
    lines.append(f"**{data.get('name', 'Unknown')}** — **{int(data.get('price', 0)):,} {symbol}**")
  vg = balance(interaction.guild.id, interaction.user.id)
  vip = vip_points(interaction.guild.id, interaction.user.id)
  text = (
    "**NORMAL SHOP**\n\n"
    + ("\n".join(lines) if lines else "*The shop is empty.*")
    + f"\n\nVG: **{vg:,} VG**"
    + f"\nVIP Cards: **{vip:,}**"
    + f"\nVIP Card base value: **{VG_PER_VIP_FACE_VALUE:,} VG** each"
    + "\nVIP Cards can be used to pay normal-shop VG prices; any unused VIP value is returned as VG change."
    + "\nUse `/economy buy` with the item name to purchase."
  )
  await interaction.response.send_message(text, ephemeral=True)

@ECONOMY_GROUP.command(name="vip-shop", description="View the high-status VIP shop.")
async def vip_shop(interaction: discord.Interaction):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  data = guild_state(interaction.guild.id)[VIP_SHOP_KEY]
  lines = []
  for entry in data.values():
    lines.append(f"**{entry.get('name', 'Unknown')}** — **{int(entry.get('price', 0))} VIP Card{'s' if int(entry.get('price', 0)) != 1 else ''}**")
  vip = vip_points(interaction.guild.id, interaction.user.id)
  text = (
    "**VIP SHOP**\n\n"
    + ("\n".join(lines) if lines else "*The VIP shop is empty.*")
    + f"\n\nYour VIP Cards: **{vip:,}**"
    + "\nVIP Cards are reserved for high-status purchases."
    + "\nUse `/economy vip-buy` with the item name to purchase."
  )
  await interaction.response.send_message(text, ephemeral=True)

@ADMIN_ECONOMY_GROUP.command(name="vip-shop-add", description="Admin: add an item to the VIP shop.")
@app_commands.describe(item_name="Item from the catalog.", price="VIP Card price.")
async def vip_shop_add(interaction: discord.Interaction, item_name: str, price: int):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  base = resolve_base_item(item_name)
  if not base:
    return await interaction.response.send_message("Item not found in the catalog.", ephemeral=True)
  if price <= 0:
    return await interaction.response.send_message("Price must be positive.", ephemeral=True)
  guild_state(interaction.guild.id)[VIP_SHOP_KEY][item_key(base["name"])] = {"name": base["name"], "price": int(price), "item": base}
  save_item_data()
  await interaction.response.send_message(f"Added **{base['name']}** to the VIP shop for **{price} VIP Card{'s' if price != 1 else ''}**.", ephemeral=True)

@ADMIN_ECONOMY_GROUP.command(name="vip-shop-remove", description="Admin: remove an item from the VIP shop.")
@app_commands.describe(item_name="VIP shop item to remove.")
async def vip_shop_remove(interaction: discord.Interaction, item_name: str):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  data = guild_state(interaction.guild.id)[VIP_SHOP_KEY]
  key = next((k for k, v in data.items() if item_key(v.get("name", "")) == item_key(item_name)), None)
  if not key:
    return await interaction.response.send_message("That item is not in the VIP shop.", ephemeral=True)
  removed = data.pop(key)
  save_item_data()
  await interaction.response.send_message(f"Removed **{removed.get('name', 'Unknown')}** from the VIP shop.", ephemeral=True)

@ADMIN_ECONOMY_GROUP.command(name="vip-shop-price", description="Admin: change a VIP shop item's price.")
@app_commands.describe(item_name="VIP shop item.", price="New VIP Card price.")
async def vip_shop_price(interaction: discord.Interaction, item_name: str, price: int):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  if price <= 0:
    return await interaction.response.send_message("Price must be positive.", ephemeral=True)
  data = guild_state(interaction.guild.id)[VIP_SHOP_KEY]
  key = next((k for k, v in data.items() if item_key(v.get("name", "")) == item_key(item_name)), None)
  if not key:
    return await interaction.response.send_message("That item is not in the VIP shop.", ephemeral=True)
  data[key]["price"] = int(price)
  save_item_data()
  await interaction.response.send_message(f"**{data[key]['name']}** now costs **{price} VIP Card{'s' if price != 1 else ''}**.", ephemeral=True)

@ADMIN_ECONOMY_GROUP.command(name="vip-shop-list", description="Admin: list VIP shop items.")
async def vip_shop_list(interaction: discord.Interaction):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  data = guild_state(interaction.guild.id)[VIP_SHOP_KEY]
  lines = [f"{v.get('name', 'Unknown')} — {int(v.get('price', 0))} VIP Card(s)" for v in data.values()]
  await interaction.response.send_message("**VIP SHOP**\n\n" + ("\n".join(lines) if lines else "The VIP shop is empty."), ephemeral=True)

@ECONOMY_GROUP.command(name="vip-buy", description="Buy an item from the VIP shop using VG or VIP Cards.")
@app_commands.describe(item_name="VIP shop item to purchase.")
async def vip_shop_buy(interaction: discord.Interaction, item_name: str):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  data = guild_state(interaction.guild.id)[VIP_SHOP_KEY]
  entry = next((v for v in data.values() if item_key(v.get("name", "")) == item_key(item_name)), None)
  if not entry:
    return await interaction.response.send_message("That item is not in the VIP shop.", ephemeral=True)
  cards_price = int(entry.get("price", 0))
  cost = cards_price * VIP_EFFECTIVE_VG_VALUE
  ok, payment_msg = spend_economy_value(
    interaction.guild.id, interaction.user.id, cost, vip_card_value=VIP_EFFECTIVE_VG_VALUE
  )
  if not ok:
    return await interaction.response.send_message(payment_msg, ephemeral=True)
  from ..items import decorate_item, add_item
  item = decorate_item(entry["item"])
  add_item(interaction.guild.id, interaction.user.id, item, held=True)
  data.pop(next(k for k, v in data.items() if v is entry), None)
  save_item_data()
  await interaction.response.send_message(
    f"Bought **{item['name']}** from the VIP shop. Price: **{cards_price} VIP Card{'s' if cards_price != 1 else ''}** / **{cost:,} VG premium value**.\n{payment_msg}",
    ephemeral=True,
  )


@ADMIN_ECONOMY_GROUP.command(name="shop-add", description="Admin: add an item to the shop.")
@app_commands.describe(item_name="Item from the catalog.", price="Purchase price.", currency="Currency to use. Defaults to VG.")
async def shop_add(interaction: discord.Interaction, item_name: str, price: int, currency: str = "VG"):
  if interaction.guild is None or not is_staff(interaction): return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  base = resolve_base_item(item_name)
  if not base:
    # GM-created templates are the authoritative custom catalog.
    custom = next(
      (x for x in custom_catalog_items()
       if item_key(x.get("name", "")) == item_key(item_name)),
      None,
    )
    if custom:
      base = custom
  if not base:
    return await interaction.response.send_message(
      "Item not found in the GM Catalog. Create it with **GM Create Item** first.",
      ephemeral=True,
    )
  if price <= 0: return await interaction.response.send_message(" Price must be positive.", ephemeral=True)
  currency_entry = None
  currency_key = "vg"
  if _currency_key(currency) not in {"vg", "vesperian-gold"}:
    currency_entry = get_custom_currency(interaction.guild.id, currency)
    if not currency_entry:
      return await interaction.response.send_message("Currency not found. Create it with **/gm-economy currency-create** first.", ephemeral=True)
    currency_key = currency_entry["id"]
  guild_state(interaction.guild.id)["shop"][item_key(base["name"])] = {
    "name": base["name"], "price": price, "currency": currency_key, "item": base
  }
  save_item_data(); await interaction.response.send_message(
    f" Added **{base['name']}** to the shop for **{price:,} {currency_entry['symbol'] if currency_entry else 'VG'}**.", ephemeral=True)

@ADMIN_ECONOMY_GROUP.command(name="shop-remove", description="Admin: remove an item from the shop.")
@app_commands.describe(item_name="Shop item to remove.")
async def shop_remove(interaction: discord.Interaction, item_name: str):
  if interaction.guild is None or not is_staff(interaction): return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  shop_data = guild_state(interaction.guild.id)["shop"]
  key = next((k for k,v in shop_data.items() if item_key(v["name"]) == item_key(item_name)), None)
  if not key: return await interaction.response.send_message(" That item is not in the shop.", ephemeral=True)
  data = shop_data.pop(key); save_item_data(); await interaction.response.send_message(f" Removed **{data['name']}** from the shop.", ephemeral=True)

@ADMIN_ECONOMY_GROUP.command(name="shop-price", description="Admin: change a shop item's price.")
@app_commands.describe(item_name="Shop item.", price="New purchase price.")
async def shop_price(interaction: discord.Interaction, item_name: str, price: int):
  if interaction.guild is None or not is_staff(interaction): return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  if price <= 0: return await interaction.response.send_message(" Price must be positive.", ephemeral=True)
  shop_data = guild_state(interaction.guild.id)["shop"]
  key = next((k for k,v in shop_data.items() if item_key(v["name"]) == item_key(item_name)), None)
  if not key: return await interaction.response.send_message(" That item is not in the shop.", ephemeral=True)
  shop_data[key]["price"] = price; save_item_data(); await interaction.response.send_message(f" **{shop_data[key]['name']}** now costs **{price:,}**.", ephemeral=True)

@ECONOMY_GROUP.command(name="buy", description="Buy an item from the shop.")
@app_commands.describe(item_name="Item to purchase.")
async def shop_buy(interaction: discord.Interaction, item_name: str):
  if interaction.guild is None: return await interaction.response.send_message("Server only.", ephemeral=True)
  shop_data = guild_state(interaction.guild.id)["shop"]
  entry = next((v for v in shop_data.values() if v.get("type") != "vip_points" and item_key(v.get("name", "")) == item_key(item_name)), None)
  if not entry: return await interaction.response.send_message(" That item is not in the shop.", ephemeral=True)
  currency_key = entry.get("currency", "vg")
  if currency_key == "vg":
    ok, payment_msg = spend_economy_value(interaction.guild.id, interaction.user.id, int(entry["price"]))
    symbol = "VG"
  else:
    currency_entry = get_custom_currency(interaction.guild.id, currency_key)
    if not currency_entry:
      return await interaction.response.send_message("This shop item references a currency that no longer exists.", ephemeral=True)
    ok, payment_msg = custom_currency_spending_value(interaction.guild.id, interaction.user.id, currency_key, int(entry["price"]))
    symbol = currency_entry["symbol"]
  if not ok:
    return await interaction.response.send_message(payment_msg, ephemeral=True)
  from ..items import decorate_item, add_item
  item = decorate_item(entry["item"])
  add_item(interaction.guild.id, interaction.user.id, item, held=True)
  shop_data.pop(next(k for k,v in shop_data.items() if v is entry), None)
  save_item_data(); await interaction.response.send_message(
    f" Bought **{item['name']}** for **{entry['price']:,} {symbol}**.\n{payment_msg}\nThe shop listing has been removed.",
    ephemeral=True
  )

@ECONOMY_GROUP.command(name="sell", description="Sell one of your owned items for its item value or rarity value.")
@app_commands.describe(item_name="Item to sell.")
async def sell(interaction: discord.Interaction, item_name: str):
  if interaction.guild is None: return await interaction.response.send_message("Server only.", ephemeral=True)
  _, record = find_possessed_item(interaction.guild.id, item_name)
  if not record or int(record.get("owner_id",0)) != interaction.user.id: return await interaction.response.send_message(" You don't own that item.", ephemeral=True)
  item = record["item"]
  # GM-created catalog items are valid sale items too. Prefer an explicit
  # template value when supplied, otherwise fall back to the rarity value.
  explicit_value = item.get("value")
  try:
    value = int(explicit_value) if explicit_value is not None else RARITY_SELL_VALUES.get(item.get("rarity"), 2)
  except (TypeError, ValueError):
    value = RARITY_SELL_VALUES.get(item.get("rarity"), 2)
  value = max(1, value)
  if remove_inventory_item(interaction.guild.id, interaction.user.id, item["id"]) is None: return await interaction.response.send_message(" Ownership data is out of sync.", ephemeral=True)
  item_state(interaction.guild.id).get("possessions",{}).pop(item["id"], None)
  add_money(interaction.guild.id, interaction.user.id, value); save_item_data()
  await interaction.response.send_message(f" Sold **{item['name']}** ({item['rarity']}) for **{value:,}** VG. Balance: **{balance(interaction.guild.id,interaction.user.id):,}**.")

@FACTION_GROUP.command(name="info", description="View your faction and its treasury.")
async def faction(interaction: discord.Interaction):
  if interaction.guild is None: return await interaction.response.send_message("Server only.", ephemeral=True)
  factions = guild_state(interaction.guild.id)["factions"]
  found = next(((name,data) for name,data in factions.items() if interaction.user.id in data.get("members",[])), None)
  if not found: return await interaction.response.send_message(" You are not in a faction. An admin can use `/faction create`.", ephemeral=True)
  name,data = found; members = data.get("members",[])
  await interaction.response.send_message(f" **{name}**\nMembers: **{len(members)}**\nFaction treasury: **{data.get('treasury',0):,}** VG", ephemeral=True)

@ADMIN_FACTION_GROUP.command(name="create", description="Admin: create a faction.")
@app_commands.describe(name="Faction name.")
async def create_faction(interaction: discord.Interaction, name: str):
  if interaction.guild is None or not is_staff(interaction): return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  name=name.strip()[:50]
  if not name: return await interaction.response.send_message(" Name required.", ephemeral=True)
  factions=guild_state(interaction.guild.id)["factions"]
  if name.casefold() in {n.casefold() for n in factions}: return await interaction.response.send_message(" That faction already exists.", ephemeral=True)
  # Admins create the faction without automatically joining it.
  factions[name]={"members":[],"treasury":0,"vip_points":0}
  save_item_data()
  await interaction.response.send_message(f" Created **{name}**. You did not join it.", ephemeral=True)

@ADMIN_FACTION_GROUP.command(name="force-join", description="Admin: force a player into a faction.")
@app_commands.describe(user="Player to force into the faction.", faction="Faction name.")
async def faction_force_join(interaction: discord.Interaction, user: discord.Member, faction: str):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)

  faction_name = faction.strip()[:50]
  if not faction_name:
    return await interaction.response.send_message("Faction name required.", ephemeral=True)

  factions = guild_state(interaction.guild.id)["factions"]
  found = next(((name, data) for name, data in factions.items()
                if name.casefold() == faction_name.casefold()), None)
  if not found:
    return await interaction.response.send_message("Faction not found.", ephemeral=True)

  actual_name, data = found
  # A player can only belong to one faction. Remove them from any previous one.
  for other_data in factions.values():
    members = other_data.setdefault("members", [])
    if user.id in members:
      members.remove(user.id)

  data.setdefault("members", [])
  if user.id not in data["members"]:
    data["members"].append(user.id)

  save_item_data()
  await interaction.response.send_message(
    f" Forced {user.mention} to join **{actual_name}**.",
    ephemeral=True
  )

@FACTION_GROUP.command(name="join", description="Join a faction.")
@app_commands.describe(name="Faction name.")
async def join_faction(interaction: discord.Interaction, name: str):
  if interaction.guild is None: return await interaction.response.send_message("Server only.", ephemeral=True)
  factions=guild_state(interaction.guild.id)["factions"]
  data=next((v for n,v in factions.items() if n.casefold()==name.casefold()),None)
  if not data: return await interaction.response.send_message(" Faction not found.", ephemeral=True)
  for other in factions.values():
    if interaction.user.id in other.get("members",[]): other["members"].remove(interaction.user.id)
  data.setdefault("members",[]).append(interaction.user.id); save_item_data(); await interaction.response.send_message(f" You joined the faction.", ephemeral=True)

@ADMIN_FACTION_GROUP.command(name="add-money", description="Admin: add VG, VIP Points, or both to a faction treasury.")
@app_commands.describe(faction="Faction name.", amount="Optional VG amount.", vip_points="Optional VIP Points.")
async def faction_add_money(interaction: discord.Interaction, faction: str, amount: int | None = None, vip_points: int | None = None):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  if amount is None and vip_points is None:
    return await interaction.response.send_message("Provide VG, VIP Points, or both.", ephemeral=True)
  if (amount is not None and amount < 0) or (vip_points is not None and vip_points < 0):
    return await interaction.response.send_message("Amounts cannot be negative.", ephemeral=True)
  factions = guild_state(interaction.guild.id)["factions"]
  found = next(((n, d) for n, d in factions.items() if n.casefold() == faction.casefold()), None)
  if not found:
    return await interaction.response.send_message("Faction not found.", ephemeral=True)
  data = found[1]
  data["treasury"] = int(data.get("treasury", 0) or 0) + (amount or 0)
  data["vip_points"] = int(data.get("vip_points", 0) or 0) + (vip_points or 0)
  save_item_data()
  await interaction.response.send_message(
    f"Added **{amount or 0:,} VG** and **{vip_points or 0:,} VIP Points** to **{found[0]}**.", ephemeral=True
  )

@ADMIN_FACTION_GROUP.command(name="remove-money", description="Admin: remove VG, VIP Points, or both from a faction treasury.")
@app_commands.describe(faction="Faction name.", amount="Optional VG amount.", vip_points="Optional VIP Points.")
async def faction_remove_money(interaction: discord.Interaction, faction: str, amount: int | None = None, vip_points: int | None = None):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  if amount is None and vip_points is None:
    return await interaction.response.send_message("Provide VG, VIP Points, or both.", ephemeral=True)
  if (amount is not None and amount < 0) or (vip_points is not None and vip_points < 0):
    return await interaction.response.send_message("Amounts cannot be negative.", ephemeral=True)
  factions = guild_state(interaction.guild.id)["factions"]
  found = next(((n, d) for n, d in factions.items() if n.casefold() == faction.casefold()), None)
  if not found:
    return await interaction.response.send_message("Faction not found.", ephemeral=True)
  data = found[1]
  treasury = int(data.get("treasury", 0) or 0)
  faction_vip = int(data.get("vip_points", 0) or 0)
  if (amount or 0) > treasury or (vip_points or 0) > faction_vip:
    return await interaction.response.send_message(
      f"Insufficient faction funds. Treasury: **{treasury:,} VG** | **{faction_vip:,} VIP Points**.", ephemeral=True
    )
  data["treasury"] = treasury - (amount or 0)
  data["vip_points"] = faction_vip - (vip_points or 0)
  save_item_data()
  await interaction.response.send_message(
    f"Removed **{amount or 0:,} VG** and **{vip_points or 0:,} VIP Points** from **{found[0]}**.", ephemeral=True
  )

@FACTION_GROUP.command(name="donate", description="Donate VG, VIP Points, or both to your faction.")
@app_commands.describe(
  amount="Optional amount of VG to donate.",
  vip_points="Optional number of VIP Points to donate."
)
async def donate_faction(
  interaction: discord.Interaction,
  amount: int | None = None,
  vip_points: int | None = None,
):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)

  # At least one currency must be supplied.
  if amount is None and vip_points is None:
    return await interaction.response.send_message(
      "Provide an amount of VG, VIP Points, or both.", ephemeral=True
    )

  if amount is not None and amount <= 0:
    return await interaction.response.send_message("VG amount must be positive.", ephemeral=True)
  if vip_points is not None and vip_points <= 0:
    return await interaction.response.send_message("VIP Points must be positive.", ephemeral=True)

  factions = guild_state(interaction.guild.id)["factions"]
  found = next(
    ((n, d) for n, d in factions.items() if interaction.user.id in d.get("members", [])),
    None,
  )
  if not found:
    return await interaction.response.send_message("You are not in a faction.", ephemeral=True)

  gid = interaction.guild.id
  uid = interaction.user.id
  current_vg = balance(gid, uid)
  current_vip = globals()["vip_points"](gid, uid)

  if amount is not None and current_vg < amount:
    return await interaction.response.send_message(
      f"You don't have enough VG. Your balance is **{current_vg:,} VG**.", ephemeral=True
    )
  if vip_points is not None and current_vip < vip_points:
    return await interaction.response.send_message(
      f"You don't have enough VIP Points. You have **{current_vip:,} VIP Points**.", ephemeral=True
    )

  # Apply both transfers atomically after all balance checks pass.
  if amount is not None:
    set_balance(gid, uid, current_vg - amount)
  if vip_points is not None:
    _set_vip_cards(gid, uid, current_vip - vip_points)

  faction = found[1]
  faction["treasury"] = int(faction.get("treasury", 0) or 0) + (amount or 0)
  faction["vip_points"] = int(faction.get("vip_points", 0) or 0) + (vip_points or 0)
  save_item_data()

  parts = []
  if amount:
    parts.append(f"**{amount:,} VG**")
  if vip_points:
    parts.append(f"**{vip_points:,} VIP Point{'s' if vip_points != 1 else ''}**")

  await interaction.response.send_message(
    f"Donated {' + '.join(parts)} to **{found[0]}**.\n"
    f"Your balance: **{current_vg - (amount or 0):,} VG** | "
    f"VIP Points: **{current_vip - (vip_points or 0):,}**\n"
    f"Faction treasury: **{int(faction.get('treasury', 0) or 0):,} VG** | "
    f"Faction VIP Points: **{int(faction.get('vip_points', 0) or 0):,}**"
  )

COMMANDS=[give_money,add_money_command,remove_money_command,shop,shop_add,shop_remove,shop_price,shop_buy,sell,
           faction,create_faction,faction_force_join,join_faction,faction_add_money,faction_remove_money,donate_faction]
# ============================================================
# STOCK MARKET + GAMBLING
# ============================================================

STOCK_MARKET_KEY = "stock_market"
STOCK_COMPANIES_KEY = "companies"
STOCK_HOLDINGS_KEY = "holdings"
STOCK_HISTORY_KEY = "history"
STOCK_LAST_UPDATE_KEY = "last_update"
STOCK_LAST_DIVIDENDS_KEY = "last_dividends"
STOCK_TRADE_FLOW_KEY = "trade_flow"
STOCK_NEWS_KEY = "news"
STOCK_UPDATE_SECONDS = 240
DIVIDEND_SECONDS = 86400
DEFAULT_COMPANIES = {
  "BLACKIRON": {"name": "Blackiron Mining", "sector": "Mining", "risk": "High Risk", "price": 2400.0, "dividend": 0.0},
  "REGALIA": {"name": "Regalia Bank", "sector": "Banking", "risk": "Low Risk", "price": 3200.0, "dividend": 0.025},
  "ASHEN": {"name": "Ashen Caravan", "sector": "Logistics", "risk": "Medium Risk", "price": 1800.0, "dividend": 0.0},
  "GRAVEGLASS": {"name": "Graveglass", "sector": "Arcane Materials", "risk": "Medium Risk", "price": 950.0, "dividend": 0.0},
}
MARKET_EVENT_KEY = "market_events"

