import re
import uuid
from datetime import datetime, timezone

import discord
from discord import app_commands

from ..state import is_staff
from .items import ITEM_GROUP
from .groups import ADMIN_ITEM_GROUP
from .items import (
  RARITY_TIER_NAMES, RARITY_POWER, qualitative_stats, normalize_rarity,
  add_custom_catalog_item, add_item, item_state, save_item_data,
  compact_item, build_stats, generate_item_description, ITEM_CATALOG, ITEM_BY_NAME, item_key, item_data
)

TYPE_CHOICES = [
  app_commands.Choice(name="Weapon", value="weapon"),
  app_commands.Choice(name="Armor", value="armor"),
  app_commands.Choice(name="Item", value="item"),
]


def _effect_key(effect: str) -> str:
  return re.sub(r"[^a-z0-9]+", " ", effect.casefold()).strip()


def stats_text(stats):
  rows = qualitative_stats(stats)
  return "\n".join(f"{label}: **{value}**" for label, value in rows) or "No combat stats."


def create_custom_template(name, rarity, category, effect="", description="", attachment_url=None):
  now = datetime.now(timezone.utc).timestamp()
  stats = build_stats(rarity, category, "")
  return {
    "id": f"custom-{uuid.uuid4().hex[:12]}",
    "base_name": name.strip(),
    "name": name.strip(),
    "category": category,
    "rarity": rarity,
    "quality": "Standard",
    "rarity_flavor": "",
    "description": description.strip() or generate_item_description({"base_name": name.strip(), "category": category}, rarity, "Standard", category),
    "effect": "",
    "stats": stats,
    "spawned_at": now,
    "custom_template": True,
    "attachment_url": attachment_url,
  }


def instantiate_custom(template):
  item = dict(template)
  item["id"] = f"custom-instance-{uuid.uuid4().hex[:12]}"
  item["spawned_at"] = datetime.now(timezone.utc).timestamp()
  item["custom_template"] = False
  item["custom_catalog_id"] = template.get("id")
  return item


@ADMIN_ITEM_GROUP.command(name="create", description="Admin: create a custom weapon, armor, or item.")
@app_commands.describe(
  name="Required custom name.",
  rarity="Required rarity.",
  type="What kind of item this is. Defaults to Item.",
  damage="Weapon damage, e.g. 1d8 slashing.",
  properties="Weapon properties, e.g. finesse, reach, two-handed.",
  value="Optional shop/sale value.",
  description="Optional item description.",
)
@app_commands.choices(type=TYPE_CHOICES)
async def create_item(
  interaction: discord.Interaction,
  name: str,
  rarity: str,
  type: app_commands.Choice[str] = None,
  damage: str = "",
  properties: str = "",
  value: int = 0,
  description: str = None,
):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)

  name = name.strip()
  damage = (damage or "").strip()
  properties = (properties or "").strip()
  description = (description or "").strip()
  category = type.value if type else "item"

  rarity = normalize_rarity(rarity)

  if not rarity:
    return await interaction.response.send_message(
      " Unknown rarity. Use `/item rarities` to see the full list.", ephemeral=True
    )

  if not name:
    return await interaction.response.send_message(" A name is required.", ephemeral=True)
  if len(name) > 80:
    return await interaction.response.send_message(" Names must be 80 characters or fewer.", ephemeral=True)
  if len(damage) > 40 or len(properties) > 180:
    return await interaction.response.send_message("Damage must be <=40 chars and properties <=180 chars.", ephemeral=True)
  if value < 0:
    return await interaction.response.send_message("Value cannot be negative.", ephemeral=True)
  if len(description) > 500:
    return await interaction.response.send_message(" Descriptions must be 500 characters or fewer.", ephemeral=True)

  template = create_custom_template(name, rarity, category, "", description)
  template["damage"] = damage
  template["properties"] = properties
  template["value"] = value
  if not add_custom_catalog_item(template):
    return await interaction.response.send_message("An item with that name already exists in the GM Catalog.", ephemeral=True)
  save_item_data()

  embed = discord.Embed(
    title=template["name"],
    description=f"**{template['rarity']}** • {category.title()}",
    colour=discord.Colour.dark_grey(),
  )
  if template.get("description"):
    embed.add_field(name="Description", value=template["description"], inline=False)
  if template.get("damage"):
    embed.add_field(name="Damage", value=template["damage"], inline=True)
  if template.get("properties"):
    embed.add_field(name="Properties", value=template["properties"], inline=False)
  if template.get("value", 0):
    embed.add_field(name="Value", value=f"{template['value']:,}", inline=True)
  embed.add_field(name="Stats", value=stats_text(template["stats"]), inline=False)
  embed.set_footer(text="Saved permanently to the GM Catalog • Ready for Shop Add / DM Spawn / Server Spawn")
  await interaction.response.send_message(
    embed=embed,
    ephemeral=True,
  )



@ADMIN_ITEM_GROUP.command(name="edit", description="GM: edit a custom item saved in the catalog.")
@app_commands.describe(
  item_name="Name of the custom catalog item.",
  name="New name (optional).",
  rarity="New rarity (optional).",
  effect="New special effect (optional).",
  damage="New weapon damage (optional).",
  properties="New weapon properties (optional).",
  value="New value (optional).",
  description="New description (optional).",
)
async def item_edit(
  interaction: discord.Interaction,
  item_name: str,
  name: str = None,
  rarity: str = None,
  effect: str = None,
  damage: str = None,
  properties: str = None,
  value: int = None,
  description: str = None,
):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)

  template = next(
    (x for x in ITEM_CATALOG if x.get("custom_template") and item_key(x.get("name", "")) == item_key(item_name)),
    None,
  )
  if not template:
    return await interaction.response.send_message("Custom catalog item not found. Use `/item catalog` to find it.", ephemeral=True)

  changes = []
  if name is not None:
    new_name = name.strip()
    if not new_name or len(new_name) > 80:
      return await interaction.response.send_message("Name must be 1–80 characters.", ephemeral=True)
    if any(x is not template and x.get("name", "").casefold() == new_name.casefold() for x in ITEM_CATALOG):
      return await interaction.response.send_message("Another catalog item already uses that name.", ephemeral=True)
    old_name = template.get("name", "Unknown")
    ITEM_BY_NAME.pop(old_name.casefold(), None)
    template["name"] = new_name
    template["base_name"] = new_name
    ITEM_BY_NAME[new_name.casefold()] = template
    changes.append(f"name: **{old_name}** → **{new_name}**")

  if rarity is not None:
    rarity_lookup = {r.casefold(): r for r in RARITY_TIER_NAMES}
    new_rarity = rarity_lookup.get(rarity.strip().casefold())
    if not new_rarity:
      return await interaction.response.send_message("Unknown rarity. Use `/item rarities`.", ephemeral=True)
    template["rarity"] = new_rarity
    template["stats"] = build_stats(new_rarity, template.get("category", "item"), template.get("effect", ""))
    changes.append(f"rarity: **{new_rarity}**")

  if effect is not None:
    effect = effect.strip()
    if len(effect) > 160:
      return await interaction.response.send_message("Effect must be 160 characters or fewer.", ephemeral=True)
    template["effect"] = effect
    template["stats"] = build_stats(template.get("rarity", "Common"), template.get("category", "item"), effect)
    changes.append("effect updated")

  if damage is not None:
    damage = damage.strip()
    if len(damage) > 40:
      return await interaction.response.send_message("Damage must be 40 characters or fewer.", ephemeral=True)
    template["damage"] = damage
    changes.append("damage updated")

  if properties is not None:
    properties = properties.strip()
    if len(properties) > 180:
      return await interaction.response.send_message("Properties must be 180 characters or fewer.", ephemeral=True)
    template["properties"] = properties
    changes.append("properties updated")

  if value is not None:
    if value < 0:
      return await interaction.response.send_message("Value cannot be negative.", ephemeral=True)
    template["value"] = value
    changes.append(f"value: **{value:,}**")

  if description is not None:
    description = description.strip()
    if len(description) > 500:
      return await interaction.response.send_message("Description must be 500 characters or fewer.", ephemeral=True)
    template["description"] = description
    changes.append("description updated")

  if not changes:
    return await interaction.response.send_message("No changes supplied.", ephemeral=True)

  # Keep the persistent custom catalog entry in sync with the in-memory template.
  for saved in item_data.setdefault("custom_catalog", []):
    if saved.get("id") == template.get("id"):
      saved.clear()
      saved.update(template)
      break
  save_item_data()

  await interaction.response.send_message(
    f"Updated **{template['name']}** in the custom catalog.\n" + "\n".join(f"• {c}" for c in changes) +
    "\n\nExisting owned copies are unchanged; newly created copies use the updated template.",
    ephemeral=True,
  )


async def catalog_item_autocomplete(interaction: discord.Interaction, current: str):
  current = current.casefold()
  return [
    app_commands.Choice(name=x.get("name", "Unknown")[:100], value=x.get("name", ""))
    for x in ITEM_CATALOG
    if x.get("custom_template") and current in x.get("name", "").casefold()
  ][:25]


async def rarity_autocomplete(interaction: discord.Interaction, current: str):
  # Rarity is intentionally free-text so all 55+ rarities remain usable.
  return []


async def _rarities_command(interaction: discord.Interaction):
  embed = discord.Embed(
    title="Rarity Tiers",
    description="Type the rarity directly in `/item create`. Rarity names are case-insensitive.",
    colour=discord.Colour.dark_grey(),
  )
  embed.add_field(name="All Rarities", value=" → ".join(RARITY_TIER_NAMES), inline=False)
  await interaction.response.send_message(embed=embed, ephemeral=True)


@ITEM_GROUP.command(name="rarities", description="Show all available item rarities.")
async def rarities(interaction: discord.Interaction):
  await _rarities_command(interaction)


item_edit.autocomplete("item_name")(catalog_item_autocomplete)
# The commands above are registered directly on ITEM_GROUP via decorators.
# ITEM_GROUP itself is owned and registered by features.items.register().
# Do not add these subcommands to the bot tree individually; doing so would
# create a duplicate top-level /item command.
def register(bot_instance):
  return None
