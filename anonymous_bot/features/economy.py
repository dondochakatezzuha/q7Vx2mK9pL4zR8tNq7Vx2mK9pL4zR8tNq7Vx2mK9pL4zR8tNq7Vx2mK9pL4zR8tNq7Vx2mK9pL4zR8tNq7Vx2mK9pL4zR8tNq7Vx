import time
import random
import asyncio
from datetime import datetime, timezone
import discord
from discord import app_commands
from .items import item_state, save_item_data, item_key, find_possessed_item, remove_inventory_item, ITEM_CATALOG, resolve_base_item, custom_catalog_items
from ..state import is_staff
from .groups import ADMIN_ECONOMY_GROUP, ADMIN_FACTION_GROUP

# Slash-command groups keep the command tree organized and below Discord's 100 global command limit.
ECONOMY_GROUP = app_commands.Group(name="economy", description="Vespasian Gold and shop commands.")
FACTION_GROUP = app_commands.Group(name="faction", description="Faction commands.")


# Campaign currency system
# VG = Vespasian Gold. VIP Cards are a separate high-value item/currency-equivalent.
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
      "`/economy sell-stock` to sell shares, and `/economy gamble` to gamble VG.",
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
    lines.append(f"**{data.get('name', 'Unknown')}** — **{int(data.get('price', 0)):,} VG**")
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
  from .items import decorate_item, add_item
  item = decorate_item(entry["item"])
  add_item(interaction.guild.id, interaction.user.id, item, held=True)
  data.pop(next(k for k, v in data.items() if v is entry), None)
  save_item_data()
  await interaction.response.send_message(
    f"Bought **{item['name']}** from the VIP shop. Price: **{cards_price} VIP Card{'s' if cards_price != 1 else ''}** / **{cost:,} VG premium value**.\n{payment_msg}",
    ephemeral=True,
  )


@ADMIN_ECONOMY_GROUP.command(name="shop-add", description="Admin: add an item to the shop.")
@app_commands.describe(item_name="Item from the catalog.", price="Purchase price.")
async def shop_add(interaction: discord.Interaction, item_name: str, price: int):
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
  guild_state(interaction.guild.id)["shop"][item_key(base["name"])] = {"name": base["name"], "price": price, "item": base}
  save_item_data(); await interaction.response.send_message(f" Added **{base['name']}** to the shop for **{price:,}**.", ephemeral=True)

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
  ok, payment_msg = spend_economy_value(interaction.guild.id, interaction.user.id, int(entry["price"]))
  if not ok:
    return await interaction.response.send_message(payment_msg, ephemeral=True)
  from .items import decorate_item, add_item
  item = decorate_item(entry["item"])
  add_item(interaction.guild.id, interaction.user.id, item, held=True)
  shop_data.pop(next(k for k,v in shop_data.items() if v is entry), None)
  save_item_data(); await interaction.response.send_message(f" Bought **{item['name']}** for **{entry['price']:,}**. Balance: **{balance(interaction.guild.id, interaction.user.id):,}**.\n The shop listing has been removed.", ephemeral=True)

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

def _stock_state(guild_id):
  eco = guild_state(guild_id)
  market = eco.setdefault(STOCK_MARKET_KEY, {})
  market.setdefault("index", 0.0)
  market.setdefault(STOCK_COMPANIES_KEY, {})
  market.setdefault(STOCK_HOLDINGS_KEY, {})
  market.setdefault("cost_basis", {})
  market.setdefault(STOCK_HISTORY_KEY, [])
  market.setdefault(STOCK_LAST_UPDATE_KEY, time.time())
  market.setdefault(STOCK_LAST_DIVIDENDS_KEY, time.time())
  market.setdefault(STOCK_TRADE_FLOW_KEY, {})
  market.setdefault(STOCK_NEWS_KEY, [])
  market.setdefault(MARKET_EVENT_KEY, [])
  # Seed the intended starting market only when a guild has no companies.
  if not market[STOCK_COMPANIES_KEY]:
    for symbol, seed in DEFAULT_COMPANIES.items():
      market[STOCK_COMPANIES_KEY][symbol] = {
        **seed, "symbol": symbol, "last_change": 0.0,
        "history": [round(float(seed["price"]), 2)], "created": time.time()
      }
  return market

def _active_market_events(market):
  now = time.time()
  active = []
  changed = False
  for event in list(market.setdefault(MARKET_EVENT_KEY, [])):
    if float(event.get("expires_at", 0) or 0) > now:
      active.append(event)
    else:
      changed = True
  if changed:
    market[MARKET_EVENT_KEY] = active
  return active

def _stock_change():
  # Ordinary cycles are deliberately modest. Large crashes/booms are GM events,
  # not unexplained random spikes.
  return random.uniform(-3.0, 3.0)

def _apply_trade_pressure(company, shares, side):
  """Record signed demand. The pressure is applied on the next market cycle."""
  symbol = company["symbol"]
  flow = company.setdefault("_pending_flow", 0.0)
  # Log-volume impact uses a bounded square-root curve so bulk orders matter
  # without allowing one trade to destroy the market.
  impact = min(12.0, max(0.02, (max(1, shares) ** 0.5) * 0.08))
  company["_pending_flow"] = flow + (impact if side == "buy" else -impact)
  company["last_trade_at"] = time.time()

def _apply_market_change(market, change=None, source="automatic", affected=None, description=None):
  companies = market.setdefault(STOCK_COMPANIES_KEY, {})
  change = _stock_change() if change is None else float(change)
  market["index"] = max(-100000.0, min(100000.0, float(market.get("index", 0.0)) + change))
  affected = affected or {}
  for key, company in companies.items():
    old = max(0.01, float(company.get("price", 1.0)))
    symbol = str(company.get("symbol", key)).upper()
    flow = float(company.pop("_pending_flow", 0.0) or 0.0)
    # Supply/demand is the dominant short-term input; normal macro noise is small.
    pressure = max(-18.0, min(18.0, flow))
    macro = float(affected.get(symbol, 0.0))
    company_change = pressure + (change * 0.35) + random.uniform(-1.25, 1.25) + macro
    company["price"] = round(max(0.01, old * (1.0 + company_change / 100.0)), 2)
    company["last_change"] = round(company_change, 2)
    hist = company.setdefault("history", [])
    hist.append(company["price"])
    del hist[:-20]
  history = market.setdefault(STOCK_HISTORY_KEY, [])
  history.append({
    "time": time.time(), "change": round(change, 2),
    "index": round(market["index"], 2), "source": source,
    "description": description or ""
  })
  del history[:-50]
  market[STOCK_LAST_UPDATE_KEY] = time.time()

def _apply_dividends(guild_id, market):
  now = time.time()
  last = float(market.get(STOCK_LAST_DIVIDENDS_KEY, now))
  if now - last < DIVIDEND_SECONDS:
    return 0
  periods = min(30, int((now - last) // DIVIDEND_SECONDS))
  if periods <= 0:
    return 0
  total_paid = 0
  companies = market.get(STOCK_COMPANIES_KEY, {})
  holdings = market.get(STOCK_HOLDINGS_KEY, {})
  for symbol, company in companies.items():
    rate = max(0.0, float(company.get("dividend", 0.0) or 0.0))
    if rate <= 0:
      continue
    price = max(0.01, float(company.get("price", 0.0)))
    for uid, user_holdings in holdings.items():
      shares = int(user_holdings.get(company.get("symbol", symbol), 0) or 0)
      if shares <= 0:
        continue
      payout = int(round(price * shares * rate * periods))
      if payout:
        add_money(guild_id, int(uid), payout)
        total_paid += payout
  market["last_dividends"] = last + periods * DIVIDEND_SECONDS
  return total_paid

def _ensure_market_updated(guild_id):
  market = _stock_state(guild_id)
  now = time.time()
  last = float(market.get(STOCK_LAST_UPDATE_KEY, now))
  if now - last >= STOCK_UPDATE_SECONDS:
    steps = min(48, int((now - last) // STOCK_UPDATE_SECONDS))
    for _ in range(steps):
      if _active_market_events(market):
        apply_market_event_cycle(market)
      else:
        _apply_market_change(market, None, "automatic")
    _apply_dividends(guild_id, market)
    save_item_data()
  else:
    _apply_dividends(guild_id, market)
  return market

def _stock_holdings(market, user_id):
  return market.setdefault(STOCK_HOLDINGS_KEY, {}).setdefault(str(user_id), {})

def _find_company(market, symbol):
  symbol = symbol.strip().upper()
  return next(((key, data) for key, data in market[STOCK_COMPANIES_KEY].items()
               if data.get("symbol", key).upper() == symbol), None)

def _format_stock_company(data):
  return (f"**{data['name']}** (`{data['symbol']}`) — "
          f"**{float(data.get('price', 0)):.2f} VG/share** "
          f"({float(data.get('last_change', 0)):+.2f}%)")

def _market_sentiment(index):
  if index >= 10: return "BULLISH"
  if index <= -10: return "BEARISH"
  return "NEUTRAL"

@ECONOMY_GROUP.command(name="stocks", description="Open the campaign stock market.")
async def stocks(interaction: discord.Interaction):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  market = _ensure_market_updated(interaction.guild.id)
  companies = list(market[STOCK_COMPANIES_KEY].values())
  gainers = sorted(companies, key=lambda c: float(c.get("last_change", 0)), reverse=True)[:3]
  losers = sorted(companies, key=lambda c: float(c.get("last_change", 0)))[:3]
  holdings = _stock_holdings(market, interaction.user.id)
  portfolio = sum(int(round(float(c.get("price", 0)) * int(holdings.get(c.get("symbol"), 0) or 0))) for c in companies)
  lines = [
    "**STOCK MARKET**",
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    f"Market Index: **{float(market.get('index', 0)):+.2f}%** | Sentiment: **{_market_sentiment(float(market.get('index', 0)))}**",
    "",
    "**TOP GAINERS**",
  ]
  lines += [f"• {c['name']}  **{float(c.get('last_change',0)):+.1f}%**" for c in gainers] or ["• None"]
  lines += ["", "**TOP LOSERS**"]
  lines += [f"• {c['name']}  **{float(c.get('last_change',0)):+.1f}%**" for c in losers] or ["• None"]
  lines += ["", "**YOUR PORTFOLIO**", f"• Portfolio Value: **{portfolio:,} VG**",
            f"• Active Holdings: **{sum(1 for s in holdings.values() if int(s) > 0)}**",
            "", "Use the company selector in `/main → Economy → Stocks` for details."]
  await interaction.response.send_message("\n".join(lines), ephemeral=True)

STOCK_PAYMENT_CHOICES = [
  app_commands.Choice(name="VG", value="vg"),
  app_commands.Choice(name="VIP", value="vip"),
]

@ECONOMY_GROUP.command(name="invest", description="Buy shares in a campaign company.")
@app_commands.describe(company="Company ticker/symbol.", shares="Number of shares to buy.", payment="Payment method.")
@app_commands.choices(payment=STOCK_PAYMENT_CHOICES)
async def invest(interaction: discord.Interaction, company: str, shares: int, payment: app_commands.Choice[str] | None = None):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  if shares <= 0:
    return await interaction.response.send_message("Shares must be positive.", ephemeral=True)
  market = _ensure_market_updated(interaction.guild.id)
  found = _find_company(market, company)
  if not found:
    return await interaction.response.send_message("Company not found.", ephemeral=True)
  _, data = found
  price = max(0.01, float(data.get("price", 0)))
  cost = int(round(price * shares))
  method = payment.value if payment else "vg"
  if method == "vip":
    ok, payment_msg = spend_economy_value(interaction.guild.id, interaction.user.id, cost)
  else:
    if balance(interaction.guild.id, interaction.user.id) < cost:
      ok, payment_msg = False, f"You need **{cost:,} VG**."
    else:
      set_balance(interaction.guild.id, interaction.user.id, balance(interaction.guild.id, interaction.user.id) - cost)
      ok, payment_msg = True, f"Paid **{cost:,} VG**."
  if not ok:
    return await interaction.response.send_message(payment_msg, ephemeral=True)
  holdings = _stock_holdings(market, interaction.user.id)
  symbol = data["symbol"]
  old_shares = int(holdings.get(symbol, 0) or 0)
  holdings[symbol] = old_shares + shares
  basis = market.setdefault("cost_basis", {}).setdefault(str(interaction.user.id), {})
  basis[symbol] = int(basis.get(symbol, 0) or 0) + cost
  _apply_trade_pressure(data, shares, "buy")
  save_item_data()
  await interaction.response.send_message(
    f"Bought **{shares:,} shares** of **{data['name']} ({symbol})** at **{price:,.2f} VG/share**. "
    f"Demand has been recorded for the next market cycle. {payment_msg}", ephemeral=True)

@ECONOMY_GROUP.command(name="sell-stock", description="Sell shares you own.")
@app_commands.describe(company="Company ticker/symbol.", shares="Number of shares to sell.", payout="Payout method.")
@app_commands.choices(payout=STOCK_PAYMENT_CHOICES)
async def sell_stock(interaction: discord.Interaction, company: str, shares: int, payout: app_commands.Choice[str] | None = None):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  if shares <= 0:
    return await interaction.response.send_message("Shares must be positive.", ephemeral=True)
  market = _ensure_market_updated(interaction.guild.id)
  found = _find_company(market, company)
  if not found:
    return await interaction.response.send_message("Company not found.", ephemeral=True)
  _, data = found
  symbol = data["symbol"]
  holdings = _stock_holdings(market, interaction.user.id)
  owned = int(holdings.get(symbol, 0))
  if owned < shares:
    return await interaction.response.send_message(f"You only own **{owned}** shares of `{symbol}`.", ephemeral=True)
  proceeds = int(round(max(0.01, float(data.get("price", 0))) * shares))
  basis = market.setdefault("cost_basis", {}).setdefault(str(interaction.user.id), {})
  total_basis = int(basis.get(symbol, 0) or 0)
  basis_sold = int(round(total_basis * (shares / max(1, owned))))
  basis[symbol] = max(0, total_basis - basis_sold)
  holdings[symbol] = owned - shares
  if holdings[symbol] <= 0:
    holdings.pop(symbol, None)
  method = payout.value if payout else "vg"
  if method == "vip":
    cards, remainder = divmod(proceeds, VG_PER_VIP_FACE_VALUE)
    if cards > 0: add_vip_points(interaction.guild.id, interaction.user.id, cards)
    if remainder: add_money(interaction.guild.id, interaction.user.id, remainder)
    payout_text = f"{cards} VIP + {remainder:,} VG remainder"
  else:
    add_money(interaction.guild.id, interaction.user.id, proceeds)
    payout_text = f"{proceeds:,} VG"
  _apply_trade_pressure(data, shares, "sell")
  save_item_data()
  await interaction.response.send_message(
    f"Sold **{shares:,} shares** of **{data['name']} ({symbol})** for **{proceeds:,} VG value**. "
    f"Supply has been recorded for the next market cycle. Payout: **{payout_text}**.", ephemeral=True)

# Centralized GM market-event functions. Player-facing crashes/booms are always
# represented by an event/news record, never an unexplained random price shock.
def create_market_event(guild_id, name, description, deltas, duration_cycles=1):
  market = _stock_state(guild_id)
  event = {
    "id": str(int(time.time() * 1000)),
    "name": str(name)[:100],
    "description": str(description)[:1000],
    "deltas": {str(k).upper(): float(v) for k, v in deltas.items()},
    "cycles_remaining": max(1, int(duration_cycles)),
    "created_at": time.time(),
    "expires_at": time.time() + max(1, int(duration_cycles)) * STOCK_UPDATE_SECONDS,
  }
  market[MARKET_EVENT_KEY].append(event)
  market[STOCK_NEWS_KEY].append({"time": time.time(), "title": event["name"], "description": event["description"]})
  market[STOCK_NEWS_KEY] = market[STOCK_NEWS_KEY][-25:]
  return event

def apply_market_event_cycle(market):
  active = _active_market_events(market)
  deltas = {}
  descriptions = []
  for event in active:
    descriptions.append(event.get("name", "Market Event"))
    for symbol, delta in event.get("deltas", {}).items():
      deltas[symbol] = deltas.get(symbol, 0.0) + float(delta)
    event["cycles_remaining"] = max(0, int(event.get("cycles_remaining", 1)) - 1)
  _apply_market_change(
    market, 0.0, "gm_event", affected=deltas,
    description="; ".join(descriptions) if descriptions else None
  )
  for event in list(active):
    if int(event.get("cycles_remaining", 0)) <= 0:
      try: market[MARKET_EVENT_KEY].remove(event)
      except ValueError: pass

@ADMIN_ECONOMY_GROUP.command(name="stock-create", description="Admin: create a company. Prefer the GM Economy Events panel.")
@app_commands.describe(name="Company name.", symbol="Short stock symbol, 2-10 letters.", price="Starting price.")
async def stock_create(interaction: discord.Interaction, name: str, symbol: str, price: float):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  symbol = symbol.strip().upper()[:10]
  if len(symbol) < 2 or not symbol.isalpha() or price <= 0:
    return await interaction.response.send_message("Use a 2-10 letter symbol and a positive starting price.", ephemeral=True)
  market = _stock_state(interaction.guild.id)
  if _find_company(market, symbol):
    return await interaction.response.send_message("That stock symbol is already in use.", ephemeral=True)
  market[STOCK_COMPANIES_KEY][symbol] = {
    "name": name.strip()[:60], "symbol": symbol, "sector": "Unclassified",
    "risk": "Medium Risk", "price": round(price, 2), "dividend": 0.0,
    "last_change": 0.0, "history": [round(price, 2)], "created": time.time()
  }
  save_item_data()
  await interaction.response.send_message("Company created. Use **GM → Economy → Events** for future market interventions.", ephemeral=True)


# ============================================================
# SKILL GAMBLING
# ============================================================
GAMBLING_KEY = "gambling"
VIP_DAILY_GAMBLING_CAP = 7500
PAYOUT_TIERS = ((250_000, 1.00), (500_000, 0.90), (750_000, 0.75), (1_000_000, 0.60))

# Exactly five games are enabled for initial deployment. The broader design can
# add the other named games later without changing the accounting framework.
CORE_GAMES = {
  "dice_poker": {"name": "Dice Poker", "profile": "Skill", "entry_max": 100_000},
  "blackjack": {"name": "Blackjack", "profile": "Skill", "entry_max": 100_000},
  "memory": {"name": "Memory Table", "profile": "Skill", "entry_max": 50_000},
  "high_low": {"name": "High/Low", "profile": "Risk", "entry_max": 100_000},
  "blackjack_duel": {"name": "Blackjack Duel", "profile": "PvP", "entry_max": 250_000},
}

GAMBLING_GROUP = app_commands.Group(name="gambling", description="Skill-based VG games and PvP wagers.")

def _gambling_state(guild_id, user_id):
  eco = guild_state(guild_id)
  root = eco.setdefault(GAMBLING_KEY, {})
  data = root.setdefault(str(user_id), {})
  now = datetime.now(timezone.utc)
  day = now.strftime("%Y-%m-%d")
  if data.get("day") != day:
    data.clear()
    data.update({"day": day, "wagered": 0, "profit": 0, "vip_earned": 0, "wins": 0, "games": 0})
  return data

def _gambling_multiplier(wagered):
  wagered = int(wagered)
  if wagered < 250_000: return 1.00
  if wagered < 500_000: return 0.90
  if wagered < 750_000: return 0.75
  return 0.60

def _casino_multiplier(guild_id):
  now=time.time()
  events=guild_state(guild_id).get("casino_events", [])
  mult=1.0
  for event in events:
    if float(event.get("expires_at",0) or 0) > now and event.get("mode")=="blood_moon":
      mult *= 1.25
  return mult

def casino_event_status(guild_id):
  now=time.time()
  events=[e for e in guild_state(guild_id).get("casino_events",[]) if float(e.get("expires_at",0) or 0)>now]
  guild_state(guild_id)["casino_events"]=events
  return events

def _gambling_check(guild_id, user_id, amount):
  data = _gambling_state(guild_id, user_id)
  if amount <= 0:
    return False, "Wager must be positive.", data
  # There is intentionally no daily gambling wager cap or lockout.
  # Payout degradation and the separate VIP achievement cap remain active.
  return True, "", data

def _record_gambling(guild_id, user_id, wager, profit, vip_reward=0, win=False):
  data = _gambling_state(guild_id, user_id)
  data["wagered"] = int(data.get("wagered", 0)) + int(wager)
  data["profit"] = int(data.get("profit", 0)) + int(profit)
  data["vip_earned"] = int(data.get("vip_earned", 0)) + int(vip_reward)
  data["games"] = int(data.get("games", 0)) + 1
  data["wins"] = int(data.get("wins", 0)) + (1 if win else 0)
  # VIP is achievement-based only. There is no VG->VIP gambling conversion.
  if vip_reward:
    add_vip_points(guild_id, user_id, vip_reward)

def _skill_vip(guild_id, user_id, amount):
  data = _gambling_state(guild_id, user_id)
  remaining = max(0, VIP_DAILY_GAMBLING_CAP - int(data.get("vip_earned", 0)))
  reward = min(remaining, int(amount))
  return reward

def _pay_wager(guild_id, user_id, wager):
  ok, msg, data = _gambling_check(guild_id, user_id, wager)
  if not ok: return False, msg, data
  if balance(guild_id, user_id) < wager:
    return False, f"You need **{wager:,} VG**.", data
  set_balance(guild_id, user_id, balance(guild_id, user_id) - wager)
  return True, "", data

def _pay_profit(guild_id, user_id, wager, profit):
  if profit > 0:
    add_money(guild_id, user_id, wager + profit)

@GAMBLING_GROUP.command(name="status", description="View gambling limits, performance, and rank.")
async def gambling_status(interaction):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  data = _gambling_state(interaction.guild.id, interaction.user.id)
  wagered = int(data.get("wagered", 0))
  rank = "Novice"
  if data.get("games", 0) >= 100: rank = "Veteran"
  elif data.get("games", 0) >= 25: rank = "Regular"
  elif data.get("games", 0) >= 10: rank = "Apprentice"
  await interaction.response.send_message(
    f"**GAMBLING PROFILE**\n\n"
    f"Daily Wagered: **{wagered:,} VG**\n"
    f"Payout Tier: **{int(_gambling_multiplier(wagered)*100)}%**\n"
    f"Net Result: **{int(data.get('profit',0)):+,} VG**\n"
    f"Wins: **{int(data.get('wins',0)):,} / {int(data.get('games',0)):,}**\n"
    f"VIP from Gambling Today: **{int(data.get('vip_earned',0)):,} / {VIP_DAILY_GAMBLING_CAP:,}**\n"
    f"Gambling Rank: **{rank}**", ephemeral=True)

@GAMBLING_GROUP.command(name="blackjack", description="Play decision-based Blackjack.")
@app_commands.describe(wager="VG wager. Use Hit/Stand decisions during the hand.")
async def gambling_blackjack(interaction, wager: int):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  if wager > 100_000:
    return await interaction.response.send_message("Blackjack entry is capped at **100,000 VG**.", ephemeral=True)
  ok, msg, _ = _pay_wager(interaction.guild.id, interaction.user.id, wager)
  if not ok:
    return await interaction.response.send_message(msg, ephemeral=True)
  # Initial hand is intentionally deterministic from secure randomness; all
  # subsequent choices are made by the player through the view.
  deck = list(range(1, 14)) * 4
  random.SystemRandom().shuffle(deck)
  player = [deck.pop(), deck.pop()]
  dealer = [deck.pop(), deck.pop()]
  view = BlackjackView(interaction.guild.id, interaction.user.id, wager, player, dealer, deck)
  await interaction.response.send_message(view.render(), view=view, ephemeral=True)

class BlackjackView(discord.ui.View):
  def __init__(self, guild_id, user_id, wager, player, dealer, deck):
    super().__init__(timeout=120)
    self.guild_id, self.user_id, self.wager = guild_id, user_id, wager
    self.player, self.dealer, self.deck = player, dealer, deck
    self.finished = False

  def total(self, hand):
    total = sum(min(x, 10) for x in hand)
    aces = sum(1 for x in hand if x == 1)
    while aces and total + 10 <= 21:
      total += 10; aces -= 1
    return total

  def render(self):
    return (f"**BLACKJACK**\n\nYour hand: **{self.player}** → **{self.total(self.player)}**\n"
            f"Dealer shows: **{min(self.dealer[0],10)}**\n\n"
            "Choose **Hit** or **Stand**. The result is based only on the actual cards and your decisions.")

  async def finish(self, interaction, result, profit, vip):
    if self.finished: return
    self.finished = True
    _pay_profit(self.guild_id, self.user_id, self.wager, profit)
    _record_gambling(self.guild_id, self.user_id, self.wager, profit, vip, profit > 0)
    save_item_data()
    await interaction.response.edit_message(
      content=(f"**BLACKJACK — {result}**\n\nYour hand: **{self.player}** → **{self.total(self.player)}**\n"
               f"Dealer hand: **{self.dealer}** → **{self.total(self.dealer)}**\n"
               f"Net result: **{profit:+,} VG**\nVIP earned: **+{vip}**"),
      view=None)

  async def hit(self, interaction):
    if interaction.user.id != self.user_id: return
    self.player.append(self.deck.pop())
    if self.total(self.player) > 21:
      mult = _gambling_multiplier(_gambling_state(self.guild_id, self.user_id)["wagered"])
      return await self.finish(interaction, "BUST", -self.wager, 0)
    await interaction.response.edit_message(content=self.render(), view=self)

  async def stand(self, interaction):
    if interaction.user.id != self.user_id: return
    while self.total(self.dealer) < 17:
      self.dealer.append(self.deck.pop())
    pt, dt = self.total(self.player), self.total(self.dealer)
    mult = _gambling_multiplier(_gambling_state(self.guild_id, self.user_id)["wagered"])
    if dt > 21 or pt > dt:
      profit = int(round(self.wager * mult * _casino_multiplier(self.guild_id)))
      vip = _skill_vip(self.guild_id, self.user_id, 25)
      return await self.finish(interaction, "WIN", profit, vip)
    if pt == dt:
      return await self.finish(interaction, "PUSH", 0, 0)
    return await self.finish(interaction, "LOSS", -self.wager, 0)

  @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary)
  async def hit_button(self, interaction, button):
    await self.hit(interaction)

  @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
  async def stand_button(self, interaction, button):
    await self.stand(interaction)

@GAMBLING_GROUP.command(name="dice-poker", description="Play a decision-based Dice Poker ladder.")
@app_commands.describe(wager="Starting VG wager.")
async def gambling_dice_poker(interaction, wager: int):
  if interaction.guild is None: return await interaction.response.send_message("Server only.", ephemeral=True)
  if wager > 100_000: return await interaction.response.send_message("Dice Poker entry is capped at 100,000 VG.", ephemeral=True)
  ok, msg, _ = _pay_wager(interaction.guild.id, interaction.user.id, wager)
  if not ok: return await interaction.response.send_message(msg, ephemeral=True)
  view = DicePokerView(interaction.guild.id, interaction.user.id, wager, 1, random.SystemRandom().sample(range(1,7), 5))
  await interaction.response.send_message(view.render(), view=view, ephemeral=True)

class DicePokerView(discord.ui.View):
  def __init__(self, guild_id, user_id, wager, round_no, dice):
    super().__init__(timeout=180)
    self.guild_id, self.user_id, self.wager, self.round_no, self.dice = guild_id, user_id, wager, round_no, dice
    self.finished = False

  def score(self):
    counts = {}
    for d in self.dice: counts[d] = counts.get(d, 0) + 1
    return max(counts.values())

  def render(self):
    ladder = [0.10, 0.25, 0.50, 1.00, 2.50]
    return (f"**DICE POKER — ROUND {self.round_no}/5**\n\nDice: **{self.dice}**\n"
            f"Current pool: **{int(self.wager*(1+ladder[self.round_no-1])):,} VG**\n"
            "Choose **Cash Out** or **Risk Next Round**.")

  async def cash(self, interaction):
    if interaction.user.id != self.user_id or self.finished: return
    self.finished = True
    profit = int(round(self.wager * [0.10,0.25,0.50,1.00,2.50][self.round_no-1]))
    mult = _gambling_multiplier(_gambling_state(self.guild_id, self.user_id)["wagered"])
    profit = int(round(profit * mult * _casino_multiplier(self.guild_id)))
    _pay_profit(self.guild_id, self.user_id, self.wager, profit)
    vip = _skill_vip(self.guild_id, self.user_id, [0,25,50,100,250][self.round_no-1])
    _record_gambling(self.guild_id, self.user_id, self.wager, profit, vip, profit > 0)
    save_item_data()
    await interaction.response.edit_message(content=f"**DICE POKER — CASHED OUT**\nProfit: **+{profit:,} VG**\nVIP: **+{vip}**", view=None)

  async def risk(self, interaction):
    if interaction.user.id != self.user_id or self.finished: return
    if self.round_no >= 5:
      return await self.cash(interaction)
    self.round_no += 1
    self.dice = random.SystemRandom().sample(range(1,7), 5)
    # Skill is expressed through the cash-out decision: the player controls risk.
    await interaction.response.edit_message(content=self.render(), view=self)

  @discord.ui.button(label="Cash Out", style=discord.ButtonStyle.success)
  async def cash_button(self, interaction, button): await self.cash(interaction)

  @discord.ui.button(label="Risk Next Round", style=discord.ButtonStyle.danger)
  async def risk_button(self, interaction, button): await self.risk(interaction)

@GAMBLING_GROUP.command(name="memory", description="Play the Memory Table progression challenge.")
@app_commands.describe(wager="Starting VG wager.")
async def gambling_memory(interaction, wager: int):
  if interaction.guild is None: return await interaction.response.send_message("Server only.", ephemeral=True)
  if wager > 50_000: return await interaction.response.send_message("Memory Table entry is capped at 50,000 VG.", ephemeral=True)
  ok, msg, _ = _pay_wager(interaction.guild.id, interaction.user.id, wager)
  if not ok: return await interaction.response.send_message(msg, ephemeral=True)
  view = MemoryTableView(interaction.guild.id, interaction.user.id, wager, 1)
  await interaction.response.send_message(view.render(), view=view, ephemeral=True)

class MemoryTableView(discord.ui.View):
  def __init__(self, guild_id, user_id, wager, level):
    super().__init__(timeout=180)
    self.guild_id, self.user_id, self.wager, self.level = guild_id, user_id, wager, level
    self.target = [random.randint(1,9) for _ in range(level + 2)]
    self.phase = "remember"
    self.failed = False
    self.add_item(MemoryChoiceButton(self))

  def render(self):
    if self.phase == "remember":
      return f"**MEMORY TABLE — LEVEL {self.level}**\n\nMemorize: **{' '.join(map(str,self.target))}**\n\nWhen ready, press **I Remember**."
    return f"**MEMORY TABLE — LEVEL {self.level}**\n\nEnter the sequence in chat with `/economy gambling memory-answer` is not supported in this session; press a number sequence button in order."

class MemoryChoiceButton(discord.ui.Button):
  def __init__(self, parent):
    super().__init__(label="I Remember", style=discord.ButtonStyle.primary)
    self.parent_view = parent
  async def callback(self, interaction):
    if interaction.user.id != self.parent_view.user_id: return
    # The Discord component cannot securely accept arbitrary multi-digit text
    # without a modal; use a modal to capture the player's sequence.
    await interaction.response.send_modal(MemoryAnswerModal(self.parent_view))

class MemoryAnswerModal(discord.ui.Modal):
  def __init__(self, parent):
    super().__init__(title=f"Memory Table — Level {parent.level}")
    self.parent_view = parent
    self.answer = discord.ui.TextInput(label="Sequence", placeholder="e.g. 3 8 1 9", required=True, max_length=50)
    self.add_item(self.answer)
  async def on_submit(self, interaction):
    v = self.parent_view
    if interaction.user.id != v.user_id: return
    try: answer = [int(x) for x in str(self.answer.value).replace(",", " ").split()]
    except ValueError: answer = []
    if answer != v.target:
      v.failed = True
      v.stop()
      _record_gambling(v.guild_id, v.user_id, v.wager, -v.wager, 0, False)
      save_item_data()
      return await interaction.response.edit_message(content=f"**MEMORY TABLE — FAILED**\nCorrect sequence: **{' '.join(map(str,v.target))}**\nNet result: **-{v.wager:,} VG**", view=None)
    if v.level >= 5:
      v.stop()
      profit = int(round(v.wager * 1.0 * _gambling_multiplier(_gambling_state(v.guild_id,v.user_id)["wagered"]) * _casino_multiplier(v.guild_id)))
      _pay_profit(v.guild_id, v.user_id, v.wager, profit)
      vip = _skill_vip(v.guild_id, v.user_id, 500)
      _record_gambling(v.guild_id, v.user_id, v.wager, profit, vip, True)
      save_item_data()
      return await interaction.response.edit_message(content=f"**MEMORY TABLE — LEVEL 5 COMPLETE**\nProfit: **+{profit:,} VG**\nVIP: **+{vip}**", view=None)
    v.level += 1
    v.target = [random.randint(1,9) for _ in range(v.level + 2)]
    await interaction.response.edit_message(content=v.render(), view=v)

@GAMBLING_GROUP.command(name="high-low", description="Predict whether the next card is higher or lower.")
@app_commands.describe(wager="VG wager.")
@app_commands.choices(choice=[
  app_commands.Choice(name="Higher", value="higher"),
  app_commands.Choice(name="Lower", value="lower"),
])
async def gambling_high_low(interaction, wager: int, choice: app_commands.Choice[str]):
  if interaction.guild is None: return await interaction.response.send_message("Server only.", ephemeral=True)
  if wager > 100_000: return await interaction.response.send_message("High/Low entry is capped at 100,000 VG.", ephemeral=True)
  ok, msg, _ = _pay_wager(interaction.guild.id, interaction.user.id, wager)
  if not ok: return await interaction.response.send_message(msg, ephemeral=True)
  a = random.randint(1,13); b = random.randint(1,13)
  if a == b:
    profit = 0
    result = "PUSH — the cards were equal."
  else:
    won = (b > a) if choice.value == "higher" else (b < a)
    mult = _gambling_multiplier(_gambling_state(interaction.guild.id, interaction.user.id)["wagered"])
    profit = int(round(wager * 0.75 * mult * _casino_multiplier(interaction.guild.id))) if won else -wager
    result = "WIN" if won else "LOSS"
  _pay_profit(interaction.guild.id, interaction.user.id, wager, profit)
  vip = _skill_vip(interaction.guild.id, interaction.user.id, 150) if profit > 0 else 0
  _record_gambling(interaction.guild.id, interaction.user.id, wager, profit, vip, profit > 0)
  save_item_data()
  await interaction.response.send_message(
    f"**HIGH/LOW — {result}**\n\nFirst card: **{a}**\nSecond card: **{b}**\n"
    f"Your call: **{choice.name}**\nNet: **{profit:+,} VG**\nVIP: **+{vip}**", ephemeral=True)

@GAMBLING_GROUP.command(name="blackjack-duel", description="Challenge another player to a peer-to-peer Blackjack wager.")
@app_commands.describe(opponent="Player you are challenging.", wager="VG wager.")
async def gambling_blackjack_duel(interaction, opponent: discord.Member, wager: int):
  if interaction.guild is None: return await interaction.response.send_message("Server only.", ephemeral=True)
  if opponent.bot or opponent.id == interaction.user.id:
    return await interaction.response.send_message("Choose another player.", ephemeral=True)
  if wager <= 0 or wager > 250_000:
    return await interaction.response.send_message("Duel wagers must be between 1 and 250,000 VG.", ephemeral=True)
  if balance(interaction.guild.id, interaction.user.id) < wager or balance(interaction.guild.id, opponent.id) < wager:
    return await interaction.response.send_message("Both players must be able to cover the wager.", ephemeral=True)
  # Hold both wagers in the challenge record; no house edge.
  eco = guild_state(interaction.guild.id)
  duels = eco.setdefault("duels", {})
  duel_id = str(int(time.time()*1000))
  duels[duel_id] = {"challenger": interaction.user.id, "opponent": opponent.id, "wager": wager, "created": time.time(), "game": "blackjack_duel"}
  await interaction.response.send_message(
    f"**BLACKJACK DUEL CHALLENGE**\n{interaction.user.mention} challenged {opponent.mention} for **{wager:,} VG**.\n"
    "The opponent must accept to lock the wagers.", ephemeral=False,
    view=DuelChallengeView(interaction.guild.id, duel_id))

class DuelChallengeView(discord.ui.View):
  def __init__(self, guild_id, duel_id):
    super().__init__(timeout=120); self.guild_id, self.duel_id = guild_id, duel_id
  @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
  async def accept(self, interaction, button):
    eco = guild_state(self.guild_id); duel = eco.get("duels", {}).get(self.duel_id)
    if not duel or interaction.user.id != duel["opponent"]:
      return await interaction.response.send_message("Only the challenged player can accept.", ephemeral=True)
    wager = int(duel["wager"])
    if balance(self.guild_id, duel["challenger"]) < wager or balance(self.guild_id, duel["opponent"]) < wager:
      return await interaction.response.send_message("One player no longer has enough VG.", ephemeral=True)
    set_balance(self.guild_id, duel["challenger"], balance(self.guild_id, duel["challenger"]) - wager)
    set_balance(self.guild_id, duel["opponent"], balance(self.guild_id, duel["opponent"]) - wager)
    winner = random.choice([duel["challenger"], duel["opponent"]])
    loser = duel["opponent"] if winner == duel["challenger"] else duel["challenger"]
    pot = wager * 2
    add_money(self.guild_id, winner, pot)
    _record_gambling(self.guild_id, winner, wager, wager, 200, True)
    _record_gambling(self.guild_id, loser, wager, -wager, 0, False)
    eco["duels"].pop(self.duel_id, None)
    save_item_data()
    await interaction.response.edit_message(content=f"**BLACKJACK DUEL COMPLETE**\nWinner: <@{winner}>\nPot: **{pot:,} VG**\nVIP: **+200**", view=None)
  @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
  async def decline(self, interaction, button):
    eco = guild_state(self.guild_id)
    duel = eco.get("duels", {}).get(self.duel_id)
    if duel and interaction.user.id == duel["opponent"]:
      eco["duels"].pop(self.duel_id, None)
      save_item_data()
      await interaction.response.edit_message(content="The duel was declined.", view=None)
    else:
      await interaction.response.send_message("Only the challenged player can decline.", ephemeral=True)

# Register the nested gambling group under Economy.
# register() adds this group to the bot's global tree.
async def stock_market_loop(bot):
  await bot.wait_until_ready()
  while not bot.is_closed():
    try:
      await asyncio.sleep(30)
      for guild in bot.guilds:
        market = _stock_state(guild.id)
        now = time.time()
        if now - float(market.get(STOCK_LAST_UPDATE_KEY, now)) >= STOCK_UPDATE_SECONDS:
          if _active_market_events(market):
            apply_market_event_cycle(market)
          else:
            _apply_market_change(market, None, "automatic")
          _apply_dividends(guild.id, market)
          save_item_data()
    except asyncio.CancelledError:
      raise
    except Exception as exc:
      print(f"Stock market loop error: {type(exc).__name__}: {exc}")


def register(bot):
  if not any(getattr(c, "name", None) == GAMBLING_GROUP.name for c in ECONOMY_GROUP.commands):
    ECONOMY_GROUP.add_command(GAMBLING_GROUP)
  # Register the player-facing groups exactly once. The stock loop is started
  # from on_ready so it always uses the bot's active running event loop.
  if not any(getattr(c, "name", None) == ECONOMY_GROUP.name for c in bot.tree.get_commands()):
    bot.tree.add_command(ECONOMY_GROUP)
  if not any(getattr(c, "name", None) == FACTION_GROUP.name for c in bot.tree.get_commands()):
    bot.tree.add_command(FACTION_GROUP)

  async def start_stock_task():
    if not getattr(bot, "_stock_market_task", None) or bot._stock_market_task.done():
      bot._stock_market_task = asyncio.create_task(stock_market_loop(bot))

  bot._start_stock_market_task = start_stock_task
