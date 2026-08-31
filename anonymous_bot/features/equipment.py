import discord
from discord import app_commands
from .items import item_state, save_item_data, item_key, find_possessed_item, qualitative_stats

# Slash-command groups keep the command tree organized and below Discord's 100 global command limit.
EQUIPMENT_GROUP = app_commands.Group(name="equipment", description="Equipment and combat gear commands.")


SLOTS = ("weapon", "helmet", "chest", "gloves", "boots", "accessory")


def equipment_state(guild_id, user_id):
  st = item_state(guild_id)
  eq = st.setdefault("equipment", {})
  return eq.setdefault(str(user_id), {slot: None for slot in SLOTS})


def armor_slot(item):
  name = str(item.get("name", "")).casefold()
  if any(x in name for x in ("hood", "helm", "mask", "cowl")): return "helmet"
  if any(x in name for x in ("gauntlet", "bracer")): return "gloves"
  if any(x in name for x in ("greave", "boot", "sabat")): return "boots"
  if any(x in name for x in ("ring", "amulet", "talisman", "shield", "buckler", "ward")): return "accessory"
  return "chest"


def slot_for_item(item):
  category = str(item.get("category", "item")).casefold()
  if category == "weapon": return "weapon"
  if category == "armor": return armor_slot(item)
  return "accessory"


def equipped_items(guild_id, user_id):
  eq = equipment_state(guild_id, user_id)
  out = {}
  for slot, item in eq.items():
    if item:
      out[slot] = item
  return out


def combat_stats(guild_id, user_id):
  totals = {"attack":0,"speed":0,"accuracy":0,"defense":0}
  for item in equipped_items(guild_id, user_id).values():
    for k,v in item.get("stats", {}).items(): totals[k] = totals.get(k,0) + int(v or 0)
  return totals


def equipment_lines(guild_id, user_id):
  eq = equipment_state(guild_id, user_id)
  return "\n".join(f"**{slot.title()}:** {(item.get('name') + ' — ' + item.get('rarity','')) if item else 'Empty'}" for slot,item in eq.items())

async def equipment_autocomplete(interaction, current):
  if interaction.guild is None: return []
  inv = item_state(interaction.guild.id).get("inventories", {}).get(str(interaction.user.id), [])
  key = item_key(current); seen=set(); choices=[]
  for entry in inv:
    item=entry.get("item",entry); name=str(item.get("name",""))
    if not name or item_key(name) in seen or (key and key not in item_key(name)): continue
    seen.add(item_key(name)); choices.append(app_commands.Choice(name=name[:100],value=name))
    if len(choices)>=25: break
  return choices

@EQUIPMENT_GROUP.command(name="equip", description="Equip a weapon or armor item for dungeon combat.")
@app_commands.describe(item_name="Choose an owned weapon or armor item.")
@app_commands.autocomplete(item_name=equipment_autocomplete)
async def equip(interaction: discord.Interaction, item_name: str):
  if interaction.guild is None: return await interaction.response.send_message("Server only.", ephemeral=True)
  _, record = find_possessed_item(interaction.guild.id, item_name)
  if not record or int(record.get("owner_id",0)) != interaction.user.id: return await interaction.response.send_message(" You don't own that item.", ephemeral=True)
  item=record["item"]; slot=slot_for_item(item)
  if item.get("category") not in ("weapon","armor"): return await interaction.response.send_message(" Only weapons and armor can be equipped.", ephemeral=True)
  eq=equipment_state(interaction.guild.id,interaction.user.id)
  eq[slot]=item | {"equipped_slot":slot}
  save_item_data()
  await interaction.response.send_message(f" Equipped **{item['name']}** in **{slot.title()}**.", ephemeral=True)

@EQUIPMENT_GROUP.command(name="unequip", description="Remove an equipped item from a slot.")
@app_commands.describe(slot="weapon, helmet, chest, gloves, boots, or accessory")
async def unequip(interaction: discord.Interaction, slot: str):
  if interaction.guild is None: return await interaction.response.send_message("Server only.", ephemeral=True)
  slot=slot.casefold().strip()
  if slot not in SLOTS: return await interaction.response.send_message(f" Slot must be one of: {', '.join(SLOTS)}", ephemeral=True)
  eq=equipment_state(interaction.guild.id,interaction.user.id)
  if not eq.get(slot): return await interaction.response.send_message(" That slot is already empty.", ephemeral=True)
  name=eq[slot]["name"]; eq[slot]=None; save_item_data()
  await interaction.response.send_message(f" Unequipped **{name}** from **{slot.title()}**.", ephemeral=True)

@EQUIPMENT_GROUP.command(name="view", description="View your equipped dungeon gear and total combat stats.")
async def equipment(interaction: discord.Interaction):
  if interaction.guild is None: return await interaction.response.send_message("Server only.", ephemeral=True)
  stats=combat_stats(interaction.guild.id,interaction.user.id)
  text=" **EQUIPMENT**\n\n"+equipment_lines(interaction.guild.id,interaction.user.id)+"\n\n**Combat Stats**\n"+"\n".join(f"{label}: **{value}**" for label,value in qualitative_stats(stats)) 
  await interaction.response.send_message(text, ephemeral=True)

COMMANDS=[equip,unequip,equipment]
def register(bot):
  bot.tree.add_command(EQUIPMENT_GROUP)

@EQUIPMENT_GROUP.command(name="use", description="Use a consumable dungeon item from your inventory.")
@app_commands.describe(item_name="Choose a consumable item.")
@app_commands.autocomplete(item_name=equipment_autocomplete)
async def use_item(interaction: discord.Interaction, item_name: str):
  if interaction.guild is None: return await interaction.response.send_message("Server only.", ephemeral=True)
  from .items import remove_inventory_item
  _, record = find_possessed_item(interaction.guild.id, item_name)
  if not record or int(record.get("owner_id",0)) != interaction.user.id:
    return await interaction.response.send_message(" You don't own that item.", ephemeral=True)
  item=record["item"]; name=item.get("name","").casefold()
  if item.get("category") != "item": return await interaction.response.send_message(" That item is equipment, not a consumable.", ephemeral=True)
  st=item_state(interaction.guild.id); d=st.setdefault("dungeon",{"players":{},"locked":[]}); p=d.setdefault("players",{}).setdefault(str(interaction.user.id),{"floor":1})
  effect=""
  if "phoenix" in name:
    p["death_protection"]=int(p.get("death_protection",0))+1; effect="grants **1 Death Protection**"
  elif "lucky" in name or "charm" in name:
    p["chest_bonus"]=True; effect="boosts the next dungeon chest"
  else:
    return await interaction.response.send_message(" This item has no active use implemented yet.", ephemeral=True)
  remove_inventory_item(interaction.guild.id,interaction.user.id,item["id"]); st.get("possessions",{}).pop(item["id"],None); save_item_data()
  await interaction.response.send_message(f" Used **{item['name']}** — {effect}.", ephemeral=True)

COMMANDS.append(use_item)