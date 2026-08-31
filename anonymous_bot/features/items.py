import asyncio
import json
import os
import random
import shutil
import copy
import threading
import time
import re
import uuid
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands

from ..state import is_staff
from ..config import DATA_DIR
from .groups import ADMIN_ITEM_GROUP

# Slash-command groups keep the command tree organized and below Discord's 100 global command limit.
ITEM_GROUP = app_commands.Group(name="item", description="Item catalog, item drops, and item information.")
INVENTORY_GROUP = app_commands.Group(name="inventory", description="Inventory, ownership, and item transfer commands.")


BOT = None
bot = None
# One process-wide transaction lock prevents simultaneous claim/steal operations
# from assigning the same physical item instance to multiple users.
ITEM_ACTION_LOCK = asyncio.Lock()
# Persistent data lives OUTSIDE the code folder so replacing/updating the bot does not wipe campaign memory.
# Optional DATA_DIR can override the location. By default it is a sibling folder
# next to anonymous_bot/, e.g. C:\\Users\\maksi\\Desktop\\anonymous bot\\anonymous_bot_data.
BOT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERSISTENT_DATA_DIR = DATA_DIR
os.makedirs(PERSISTENT_DATA_DIR, exist_ok=True)
ITEM_DATA_FILE = os.path.join(PERSISTENT_DATA_DIR, "anonymous_item_data.json")
LEGACY_ITEM_DATA_FILE = os.path.join(BOT_ROOT, "anonymous_item_data.json")
LEGACY_PERSISTENT_ITEM_DATA_FILE = os.path.join(os.path.dirname(BOT_ROOT), "anonymous_bot_data", "anonymous_item_data.json")

# ============================================================
# ITEM CATALOG / RNG
# ============================================================

ITEM_WEAPON_BASES = [
  "Iron Dagger", "Steel Dagger", "Ranger's Bow", "Hunter's Bow", "Ashwood Longbow",
  "Knight's Sword", "Mercenary Blade", "Dueling Rapier", "Crescent Saber", "War Katana",
  "Executioner's Greatsword", "Old Claymore", "Blacksteel Falchion", "Cavalry Spear",
  "Barbed Spear", "War Glaive", "Oak Halberd", "Stone Maul", "Iron Warhammer", "Flanged Mace",
  "Spiked Morningstar", "Woodcutter's Axe", "Battleaxe", "Heavy Greataxe", "War Pick",
  "Pilgrim's Staff", "Oak Staff", "Runed Wand", "Bone Wand", "Traveler's Grimoire",
  "Chain Flail", "Hunter's Javelin", "Steel Chakram", "Rope Dart", "Twinblade", "Recurve Bow",
  "Hand Crossbow", "Siege Crossbow", "Throwing Knife", "Harpoon", "Short Spear", "Hook Blade",
  "Glass Spear", "Moonsteel Scythe", "Grave Shovel", "Storm Lance", "Frostbrand", "Emberbrand",
]
ITEM_ARMOR_BASES = [
  "Leather Hood", "Wool Hood", "Iron Helm", "Steel Helm", "Traveling Mask", "Half Mask", "Scout Cowl",
  "Iron Breastplate", "Steel Cuirass", "Chain Shirt", "Scale Vest", "Lamellar Vest", "Leather Coat",
  "Traveler's Cloak", "Wool Mantle", "Knight's Cape", "Iron Gauntlets", "Leather Bracers", "Steel Bracers",
  "Iron Greaves", "Steel Greaves", "Travel Boots", "Iron Sabatons", "Wooden Buckler", "Iron Buckler",
  "Steel Shield", "Tower Shield", "Rune Ward", "Bronze Ring", "Silver Ring", "Moonstone Ring",
  "Copper Amulet", "Silver Talisman", "Warden's Mantle", "Battle Robe", "Dueling Coat", "Runic Harness",
  "Dragonhide Vest", "Aegis Plate", "Royal Guard Plate", "Shadow Hood", "Knight Harness", "Pilgrim's Robe",
]
ITEM_ITEM_BASES = [
  "Healing Flask", "Mana Vial", "Antidote", "Phoenix Feather", "Moon Shard", "Sun Shard", "Soul Coin",
  "Old Dungeon Key", "Dungeon Map", "Wayfinder Compass", "Lucky Charm", "Blood Crystal", "Star Fragment",
  "Memory Crystal", "Dragon Scale", "Goblin Crown", "Cursed Dice", "Silver Bell", "Oracle Eye",
  "Black Candle", "Royal Seal", "Frost Heart", "Ember Heart", "Dream Dust", "Shadow Ink", "Teleport Rune",
  "Purity Rune", "Grave Token", "Ancient Tome", "Mystic Seed", "Witch's Mirror", "Dragon Egg", "Soul Lantern",
  "Forbidden Contract", "Broken Crown", "World Tree Seed", "Sealed Letter", "Blacksmith's Token",
  "Alchemist's Vial", "Hunter's Trophy", "Forgotten Coin", "Cracked Hourglass", "Singing Stone",
]

# Neutral, grounded naming pieces. High rarity is represented by the rarity/quality,
# not by stuffing every common item with "Ancient God" style lore.
ITEM_ORIGINS = [
  "from a forgotten roadside cache", "recovered from an abandoned workshop", "kept by a wandering trader",
  "found beneath a ruined watchtower", "carried by an unnamed traveler", "sealed inside an old coffer",
  "left behind in a deserted camp", "taken from a long-closed armory", "recovered from a collapsed shrine",
  "discovered in a flooded cellar", "found in a quiet mountain pass", "salvaged from a ruined caravan",
]

RARITIES = [
  "Common", "Uncommon", "Rare", "Epic", "Legendary", "Mythical",
  "Mythical+", "Mythical++", "Mythical+++", "Dragon", "Dragon+", "RoR-"
]
RARITY_TIER_NAMES = RARITIES[:]


def normalize_rarity(value):
  """Return the canonical rarity for GM/player input. Accepts case, spacing, and
  accidental spaces around plus signs (e.g. mythical +++)."""
  raw = re.sub(r"\s+", " ", str(value or "").strip()).casefold()
  raw = re.sub(r"\s*\+\s*", "+", raw)
  lookup = {re.sub(r"\s*\+\s*", "+", r.casefold()): r for r in RARITIES}
  return lookup.get(raw)

# One unified rarity ladder. There are no legacy rarity pools or hidden cache tiers.

def rarity_power(rarity):
  try:
    i = RARITIES.index(rarity)
  except ValueError:
    i = 0
  return 0.65 + (i / max(1, len(RARITIES)-1)) * 11.35

RARITY_POWER = {r: rarity_power(r) for r in RARITIES}


QUALITY_POOLS = {
  "Standard": [("Mundane", 30.0), ("Crude", 25.0), ("Base", 20.0), ("Standard", 15.0), ("Inspired", 10.0)],
  "Advanced": [("Pristine", 35.0), ("Infused", 25.0), ("Masterful", 20.0), ("Blessed", 15.0), ("Profane", 5.0)],
  "Forbidden": [("Cursed", 40.0), ("Demonic", 30.0), ("Spectral", 20.0), ("Eldritch", 10.0)],
  "Elemental": [("Tempest", 40.0), ("Volcanic", 30.0), ("Glacial", 20.0), ("Ethereal", 10.0)],
  "Ultimate": [("Primal", 80.0), ("Artifact", 20.0)],
}
QUALITY_POOL_CHANCES = [("Standard", 70.0), ("Advanced", 20.0), ("Forbidden", 8.0), ("Elemental", 1.8), ("Ultimate", 0.2)]

EFFECT_CHOICES = []

EFFECT_MODS = {}

def _effect_key(effect):
  return re.sub(r"[^a-z0-9]+", " ", str(effect or "").casefold()).strip()

def build_stats(rarity, category, effect=""):
  p = RARITY_POWER.get(rarity, 1.0)
  if category == "weapon":
    stats = {"attack": round(10*p), "speed": max(1, round(5*p)), "accuracy": round(8*p), "defense": 0}
  elif category == "armor":
    stats = {"attack": 0, "speed": max(1, round(3*p)), "accuracy": 0, "defense": round(10*p)}
  else:
    stats = {"attack": 0, "speed": 0, "accuracy": 0, "defense": 0}
  return {k: max(0, int(v)) for k, v in stats.items()}


# Optional rarity-flavored name editor. It does NOT replace the actual quality roll.
RARITY_NAME_FLAVOR = {r: "" for r in RARITIES}


QUALITY_DESCRIPTION_PHRASES = {
  "Mundane": [
    "A plain example with little refinement, but dependable enough for ordinary use.",
    "Unremarkable in make, with the honest wear of something made to serve a simple purpose.",
  ],
  "Crude": [
    "Roughly made and imperfect, yet sturdy enough to get the job done.",
    "A rough piece of work with uneven construction and a practical, no-frills character.",
  ],
  "Base": [
    "A straightforward piece built around function rather than ornament.",
    "Simple in design and reliable in purpose, with nothing unnecessary added.",
  ],
  "Standard": [
    "A well-made example that meets the expected standard of its kind.",
    "Balanced and dependable, showing competent craftsmanship without excess.",
  ],
  "Inspired": [
    "Carefully conceived and unusually well-balanced, as though its maker had a clear vision.",
    "A thoughtful creation whose design shows uncommon creativity and purpose.",
  ],
  "Pristine": [
    "Exceptionally clean and well-preserved, with every detail maintained to a remarkable degree.",
    "Nearly flawless in condition, its construction showing little sign of wear or neglect.",
  ],
  "Infused": [
    "Its materials carry a subtle magical resonance that lingers beneath the surface.",
    "Faint energy has settled into its make, giving the piece an unmistakably supernatural presence.",
  ],
  "Masterful": [
    "Crafted with exceptional precision, the result of skill far beyond ordinary workmanship.",
    "A superb example of its kind, refined through extraordinary craftsmanship.",
  ],
  "Blessed": [
    "A quiet radiance clings to it, as though it has been touched by a benevolent power.",
    "Something protective rests within it, lending the piece an almost sacred presence.",
  ],
  "Profane": [
    "Its construction bears an unsettling influence that feels deliberately opposed to the natural order.",
    "A disturbing power permeates it, leaving an unmistakably forbidden impression.",
  ],
  "Cursed": [
    "Something is wrong with it. The longer it is held, the harder that feeling is to ignore.",
    "A lingering malice clings to it, as though its history was never meant to be uncovered.",
  ],
  "Demonic": [
    "Its power feels predatory and hostile, carrying an unmistakable infernal taint.",
    "An aggressive supernatural force coils within it, as though something beyond the mortal world left its mark.",
  ],
  "Spectral": [
    "Its edges seem strangely insubstantial, accompanied by a chill that never quite fades.",
    "A ghostlike presence surrounds it, making the object feel only partly anchored to the physical world.",
  ],
  "Eldritch": [
    "Its nature is difficult to comprehend, shaped by a power that does not feel entirely mortal.",
    "There is something deeply unnatural about it, as though its design follows rules from somewhere else.",
  ],
  "Tempest": [
    "Air and violent motion seem to gather around it whenever its power stirs.",
    "A restless stormlike force runs through its construction, never quite becoming still.",
  ],
  "Volcanic": [
    "Heat sleeps beneath its surface, carrying the weight and fury of molten stone.",
    "Its presence suggests deep fire and pressure, as though it was shaped near a living furnace.",
  ],
  "Glacial": [
    "A deep cold radiates from it, leaving the air around it noticeably sharper.",
    "Its surface carries the stillness of ancient ice and a chill that refuses to fade.",
  ],
  "Ethereal": [
    "It seems caught between substance and spirit, impossibly light despite its physical form.",
    "A soft otherworldly quality surrounds it, making its presence difficult to fully grasp.",
  ],
  "Primal": [
    "Its power feels ancient and instinctive, stripped of everything unnecessary.",
    "A raw force lies beneath its form, reminiscent of something older than civilization.",
  ],
  "Artifact": [
    "An extraordinary relic whose construction has survived long beyond the age that produced it.",
    "Its workmanship and lingering power mark it as a genuine artifact rather than an ordinary possession.",
  ],
}

RARITY_DESCRIPTION_PHRASES = {
  "Common": "A dependable item with straightforward power and practical value.",
  "Uncommon": "A noticeably improved item with qualities above ordinary equipment.",
  "Rare": "An uncommon find with strong potential and refined construction.",
  "Epic": "A powerful item whose quality is immediately apparent.",
  "Legendary": "An exceptional item worthy of stories and experienced adventurers.",
  "Mythical": "A remarkably powerful item with qualities rarely encountered.",
  "Mythical+": "An advanced mythical item with power far beyond its normal tier.",
  "Mythical++": "An extraordinarily enhanced mythical item approaching the highest known levels.",
  "Mythical+++": "A near-peak mythical item carrying overwhelming potential.",
  "Dragon": "A fearsome item whose power has reached dragon-level heights.",
  "Dragon+": "An extraordinarily rare dragon-tier item with immense power.",
  "RoR-": "The rarest known tier, bordering on impossible to obtain.",
}


def generate_item_description(item_or_base, rarity=None, quality=None, category=None):
  """Generate a grounded description from rarity + quality without forcing lore onto every item."""
  item = item_or_base if isinstance(item_or_base, dict) else {}
  name = item.get("base_name") or item.get("name") or "item"
  rarity = rarity or item.get("rarity") or "Common"
  quality = quality or item.get("quality") or "Standard"
  category = category or item.get("category") or "item"
  kind = {"weapon": "weapon", "armor": "piece of armor", "item": "item"}.get(category, "item")
  q_text = random.choice(QUALITY_DESCRIPTION_PHRASES.get(quality, QUALITY_DESCRIPTION_PHRASES["Standard"]))
  r_text = RARITY_DESCRIPTION_PHRASES.get(rarity, "")
  return f"A {quality.lower()} {kind} known as {name}. {q_text} {r_text}"

def _rating(value, thresholds, labels):
  value=float(value or 0)
  for threshold, label in reversed(list(zip(thresholds, labels))):
    if value >= threshold:
      return label
  return labels[0]

def qualitative_stats(item_or_stats, category=None):
  """Return only the small set of dark-fantasy combat descriptors shown to players."""
  item = item_or_stats if isinstance(item_or_stats, dict) and "stats" in item_or_stats else {}
  stats = item.get("stats", item_or_stats if isinstance(item_or_stats, dict) else {})
  category = category or item.get("category") or "item"

  damage = _rating(stats.get("attack", 0), [0,12,30,65,120,220], ["very low","low","average","high","very high","insane"])
  speed = _rating(stats.get("speed", 0), [0,8,18,35,65,110], ["very slow","slow","average","fast","very fast","insane"])
  defense = _rating(stats.get("defense", 0), [0,15,35,75,140,250], ["very weak","weak","average","strong","very strong","insane"])
  accuracy = _rating(stats.get("accuracy", 0), [0,12,30,65,120,220], ["very poor","poor","average","accurate","very accurate","perfect"])

  name = str(item.get("name", "")).casefold()
  if any(x in name for x in ("bow","crossbow","javelin","chakram","rope dart","throwing knife","harpoon")):
    range_rating = "very long" if "crossbow" in name else "long"
  elif any(x in name for x in ("spear","lance","halberd","glaive","scythe","staff")):
    range_rating = "medium"
  elif any(x in name for x in ("dagger","knife","rapier","saber","katana","sword","blade","twinblade","hook")):
    range_rating = "short"
  else:
    range_rating = "very short"

  if category == "weapon":
    return [("Damage", damage), ("Range", range_rating), ("Speed", speed), ("Accuracy", accuracy)]
  if category == "armor":
    return [("Defense", defense), ("Mobility", speed)]
  return []

def stats_text(stats, item=None):
  rows=qualitative_stats(item if item else stats)
  return "\n".join(f"{label}: **{value}**" for label,value in rows) or "No combat stats."

# Prefixes are only cosmetic naming. The base item remains searchable by its
# normal catalog name, while a fully generated name can also be accepted.
RARITY_NAME_ALIASES = {
  "dull", "serviceable", "gleaming", "masterwork", "fabled", "relic",
  "heirloom", "everlasting", "abyss-touched", "starforged", "astral",
  "galactic", "nebula-wreathed", "stellar", "voidbound", "timeworn",
  "paradoxical", "singular", "worldpiercer", "ascendant", "sovereign",
  "deathless", "divine", "transcendent", "zenith",
}


def weighted_choice(pool):
  return random.choices([x for x, _ in pool], weights=[w for _, w in pool], k=1)[0]


def roll_rarity():
  # Unified rarity ladder. Early tiers are common; top tiers remain extremely rare.
  weights = [90, 55, 32, 20, 12, 7, 4, 2.5, 1.4, 0.7, 0.25, 0.01]
  return weighted_choice(list(zip(RARITIES, weights)))


def roll_quality():
  pool_name = weighted_choice(QUALITY_POOL_CHANCES)
  return weighted_choice(QUALITY_POOLS[pool_name])


def build_item_catalog():
  items = []
  seen = set()
  serial = 0
  for category, bases in (("weapon", ITEM_WEAPON_BASES), ("armor", ITEM_ARMOR_BASES), ("item", ITEM_ITEM_BASES)):
    for base in bases:
      key = base.casefold()
      if key in seen:
        continue
      seen.add(key)
      serial += 1
      items.append({
        "id": f"item-{serial:05d}", "base_name": base, "name": base, "category": category,
        "description": f"A {base.lower()} {random.choice(ITEM_ORIGINS)}.",
      })
  return items

ITEM_CATALOG = build_item_catalog()
ITEM_BY_NAME = {x["name"].casefold(): x for x in ITEM_CATALOG}


def resolve_base_item(name):
  """Resolve a catalog item from a base name OR a generated rarity-prefixed name.

  Commands such as /inventory take and /item dm-force should not fail just because a
  player copied a generated name like "Masterwork Iron Dagger".
  """
  raw = str(name).strip()
  if not raw:
    return None

  # Exact base-name match.
  direct = ITEM_BY_NAME.get(raw.casefold())
  if direct:
    return direct

  # Normalized match handles punctuation/case differences.
  normalized = re.sub(r"[^a-z0-9]+", "_", raw.casefold()).strip("_")
  for base in ITEM_CATALOG:
    if re.sub(r"[^a-z0-9]+", "_", base["base_name"].casefold()).strip("_") == normalized:
      return base

  # Generated item name: remove one known rarity naming prefix.
  lowered = raw.casefold()
  for prefix in sorted(RARITY_NAME_ALIASES, key=len, reverse=True):
    if lowered.startswith(prefix + " "):
      candidate = raw[len(prefix):].strip()
      direct = ITEM_BY_NAME.get(candidate.casefold())
      if direct:
        return direct
      candidate_key = re.sub(r"[^a-z0-9]+", "_", candidate.casefold()).strip("_")
      for base in ITEM_CATALOG:
        if re.sub(r"[^a-z0-9]+", "_", base["base_name"].casefold()).strip("_") == candidate_key:
          return base

  # Last-resort suffix match lets copied generated names work even if a
  # future naming prefix is added to the catalog.
  matches = [
    base for base in ITEM_CATALOG
    if lowered.endswith(base["base_name"].casefold())
  ]
  if len(matches) == 1:
    return matches[0]

  return None


def decorate_item(base):
  if base.get("custom_template"):
    item = base.copy()
    item["id"] = f"instance-{uuid.uuid4().hex}"
    item["instance_id"] = item["id"]
    item["spawned_at"] = datetime.now(timezone.utc).timestamp()
    item["custom_catalog_id"] = base.get("id")
    item.pop("custom_template", None)
    return item
  item = base.copy()
  # Every generated item gets a unique instance id so duplicate catalog items
  # never overwrite each other in possessions/equipment/trades.
  item["id"] = f"instance-{uuid.uuid4().hex}"
  item["instance_id"] = item["id"]
  item["base_id"] = base.get("id")
  rarity = roll_rarity()
  quality = roll_quality()
  rarity_flavor = RARITY_NAME_FLAVOR[rarity]
  item["rarity"] = rarity
  item["quality"] = quality
  item["rarity_flavor"] = rarity_flavor
  item["name"] = f"{rarity_flavor} {base['base_name']}"
  item["description"] = generate_item_description(item, rarity, quality, base.get("category", "item"))
  item["effect"] = ""
  item["stats"] = build_stats(rarity, base.get("category", "item"), "")
  item["spawned_at"] = datetime.now(timezone.utc).timestamp()
  return item


def load_item_data():
  # One-time migration from older releases where persistent state lived inside
  # the bot code directory. This keeps existing campaign memory on the first
  # upgrade, then all future updates use the external persistent directory.
  if not os.path.exists(ITEM_DATA_FILE):
    legacy_source = next((p for p in (LEGACY_ITEM_DATA_FILE, LEGACY_PERSISTENT_ITEM_DATA_FILE) if os.path.exists(p)), None)
    if legacy_source:
      try:
        shutil.copy2(legacy_source, ITEM_DATA_FILE)
        print(f"[persistence] Migrated campaign data from {legacy_source} to {ITEM_DATA_FILE}")
      except OSError as exc:
        print(f"[persistence] Could not migrate legacy campaign data: {exc}")
  try:
    with open(ITEM_DATA_FILE, "r", encoding="utf-8") as f:
      data = json.load(f)
      if not isinstance(data, dict):
        raise ValueError("persistent data root is not an object")
      return data
  except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
    return {"guilds": {}}

item_data = load_item_data()

# ---------------------------------------------------------------------------
# Legacy rarity migration
# ---------------------------------------------------------------------------
# Older bot versions used a much smaller rarity ladder and could store these
# values directly on inventory/possession records. The new 55-rarity ladder
# is intentionally authoritative, so migrate legacy names once at startup.
# The migration only changes the rarity field; item ownership, IDs, levels,
# stats, descriptions, security, and other custom data are preserved.
LEGACY_RARITY_MAP = {
  "trash": "Common", "crude": "Common", "basic": "Common", "sturdy": "Common",
  "refined": "Uncommon", "elite": "Rare", "flawless": "Epic", "exotic": "Epic",
  "mythic": "Mythical", "relic": "Mythical", "scourged": "Mythical+", "cursed": "Mythical+",
  "corrupted": "Mythical+", "vile": "Mythical++", "blighted": "Mythical++", "malicious": "Mythical++",
  "sinister": "Mythical+++", "nefarious": "Mythical+++", "maleficent": "Mythical+++",
  "demonic": "Dragon", "damned": "Dragon", "abyssal": "Dragon", "profane": "Dragon",
  "diabolical": "Dragon+", "eldritch": "Dragon+", "infernal": "Dragon+", "unholy": "Dragon+",
  "cataclysmic": "Dragon+", "annihilation": "Dragon+", "oblivion": "Dragon+",
  "sanctified": "Dragon+", "blessed": "Dragon+", "hallowed": "Dragon+", "anointed": "Dragon+",
  "radiant": "Dragon+", "ascendant": "Dragon+", "sacrosanct": "Dragon+", "ethereal": "Dragon+",
  "celestial": "Dragon+", "immortal": "Dragon+", "angelic": "Dragon+", "cherubic": "Dragon+",
  "seraphic": "Dragon+", "elysian": "Dragon+", "divine": "Dragon+", "archangelic": "Dragon+",
  "empyrean": "Dragon+", "omnipotent": "Dragon+", "transcendent": "Dragon+", "infinite": "RoR-",
  "cosmic": "Dragon+", "reality": "Dragon+", "god": "RoR-"
}

def migrate_legacy_rarities(data):
  changed=[]
  valid={r.casefold():r for r in RARITIES}
  guilds=data.get("guilds", {})
  for guild in guilds.values():
    inventories=guild.get("inventories", {}) if isinstance(guild,dict) else {}
    for inv in inventories.values():
      for record in inv:
        item=record.get("item", record) if isinstance(record,dict) else {}
        if not isinstance(item,dict): continue
        raw=str(item.get("rarity","Common"))
        canonical=valid.get(raw.casefold()) or LEGACY_RARITY_MAP.get(raw.casefold(), "Common")
        if raw != canonical:
          item["rarity"]=canonical; changed.append((raw,canonical))
        item.pop("item_level", None)
    possessions=guild.get("possessions", {}) if isinstance(guild,dict) else {}
    for possession in possessions.values():
      if isinstance(possession, dict) and isinstance(possession.get("item"), dict):
        possession["item"].pop("item_level", None)
    for custom in guild.get("custom_catalog", []) if isinstance(guild,dict) else []:
      if isinstance(custom, dict):
        custom.pop("item_level", None)
  for custom in data.get("custom_catalog", []):
    if isinstance(custom, dict):
      custom.pop("item_level", None)
  return len(changed), changed

MIGRATED_RARITY_COUNT, MIGRATED_RARITIES = migrate_legacy_rarities(item_data)
if MIGRATED_RARITY_COUNT:
  tmp = ITEM_DATA_FILE + ".tmp"
  with open(tmp, "w", encoding="utf-8") as f:
    json.dump(item_data, f, ensure_ascii=False, indent=2)
  os.replace(tmp, ITEM_DATA_FILE)
if MIGRATED_RARITY_COUNT:
  print(f"[items] Migrated {MIGRATED_RARITY_COUNT} legacy item rarit{"y" if MIGRATED_RARITY_COUNT == 1 else "ies"}.")

# Custom templates survive restarts and are part of the admin catalog.
for custom in item_data.setdefault("custom_catalog", []):
  if not any(x.get("id") == custom.get("id") for x in ITEM_CATALOG):
    ITEM_CATALOG.append(custom)
ITEM_BY_NAME = {x["name"].casefold(): x for x in ITEM_CATALOG}


def custom_catalog_items():
  """Authoritative persistent GM catalog.

  This is intentionally the same catalog used by GM Create Item, Shop, VIP Shop,
  DM Spawn, Server Spawn, and /item catalog.
  """
  return [x for x in item_data.setdefault("custom_catalog", [])
          if isinstance(x, dict) and x.get("custom_template")]


def add_custom_catalog_item(template):
  catalog = item_data.setdefault("custom_catalog", [])
  if any(x.get("name", "").casefold() == template.get("name", "").casefold() for x in catalog):
    return False
  catalog.append(template)
  # Keep the in-memory master catalog synchronized immediately.
  if not any(x.get("id") == template.get("id") for x in ITEM_CATALOG):
    ITEM_CATALOG.append(template)
  ITEM_BY_NAME[template["name"].casefold()] = template
  save_item_data()
  return True




_ITEM_SAVE_LOCK = threading.RLock()

def save_item_data():
  """Persist item/memory state safely when several async handlers save at once.

  Memory archiving and item commands can call this concurrently through
  asyncio.to_thread(). Snapshot the mutable state first, use a unique temp
  file per save, and retry transient Windows file-lock/iteration errors.
  """
  last_error = None
  for attempt in range(6):
    try:
      with _ITEM_SAVE_LOCK:
        snapshot = copy.deepcopy(item_data)
        tmp = ITEM_DATA_FILE + f".{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
        try:
          with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
          os.replace(tmp, ITEM_DATA_FILE)
          return
        finally:
          try:
            if os.path.exists(tmp):
              os.remove(tmp)
          except OSError:
            pass
    except (RuntimeError, PermissionError, OSError) as exc:
      last_error = exc
      if attempt < 5:
        time.sleep(0.05 * (attempt + 1))
        continue
      print(f"[items] save_item_data failed after retries: {type(exc).__name__}: {exc}")
      return
    except Exception as exc:
      print(f"[items] save_item_data failed: {type(exc).__name__}: {exc}")
      return


REMOVED_STAT_KEYS = {"crit", "lifesteal", "regen", "penetration", "control", "power"}


def sanitize_item_stats(item):
  if isinstance(item, dict) and isinstance(item.get("stats"), dict):
    item["stats"] = {k: int(v or 0) for k, v in item["stats"].items() if k not in REMOVED_STAT_KEYS}
  if isinstance(item, dict):
    item.pop("effect", None)
  return item


def item_state(guild_id):
  state = item_data.setdefault("guilds", {}).setdefault(str(guild_id), {
    "public_enabled": False, "public_channel_id": None, "public_next": None, "public_item": None,
    "dm_enabled": False, "dm_next": None, "dm_drop": None, "dm_minutes": 1, "dm_chance": 25,
    "used_ids": [], "obtained_instance_ids": [], "inventories": {}, "possessions": {},
    "active_discoveries": {}, "spawn_history": []
  })
  state.setdefault("obtained_instance_ids", [])
  # Keep the obtained registry bounded while retaining enough history to reject stale claims.
  if len(state["obtained_instance_ids"]) > 10000:
    state["obtained_instance_ids"] = state["obtained_instance_ids"][-10000:]
  # Migrate older single-item formats without deleting existing inventory data.
  if isinstance(state.get("public_item"), dict):
    state["public_item"] = [state["public_item"]]
  drop = state.get("dm_drop")
  if isinstance(drop, dict) and "items" not in drop and drop.get("item"):
    state["dm_drop"] = {k: v for k, v in drop.items() if k != "item"} | {"items": [drop["item"]]}
  if "dm_minutes" not in state:
    state["dm_minutes"] = 1
  # Random item RNG is permanently disabled. Remove any legacy RNG timers/drops
  # so an old saved state cannot resurrect the former system.
  state["public_enabled"] = False
  state["public_next"] = None
  state["dm_enabled"] = False
  state["dm_next"] = None
  state["dm_drop"] = None
  if isinstance(state.get("public_item"), list):
    state["public_item"] = [x for x in state["public_item"] if isinstance(x, dict) and x.get("custom_catalog_id")] or None
  return state


def next_spawn():
  return (datetime.now(timezone.utc) + timedelta(hours=random.uniform(1, 48))).timestamp()


def roll_item(state):
  available = [x for x in ITEM_CATALOG if not x.get("custom_template") and x["id"] not in state.get("used_ids", [])]
  if not available:
    state["used_ids"] = []
    available = ITEM_CATALOG[:]
  item = decorate_item(random.choice(available))
  state.setdefault("used_ids", []).append(item["id"])
  return item


def roll_drop(state, secret_dm=False):
  """Generate a random item drop and update the guild's used-item tracking."""
  if secret_dm:
    premium_pool = [(r, 1.0 + i * 0.8) for i, r in enumerate(RARITIES[4:])]
    count = random.randint(1, 2)
    items = []
    for _ in range(count):
      available = [x for x in ITEM_CATALOG if not x.get("custom_template") and x["id"] not in state.get("used_ids", [])] or ITEM_CATALOG
      base = random.choice(available)
      state.setdefault("used_ids", []).append(base["id"])
      item = decorate_item(base)
      item["rarity"] = weighted_choice(premium_pool)
      item["rarity_flavor"] = RARITY_NAME_FLAVOR.get(item["rarity"], "")
      item["quality"] = roll_quality()
      item["name"] = f"{item.get('rarity_flavor', '')} {base['base_name']}".strip()
      item["description"] = generate_item_description(item, item["rarity"], item["quality"], base.get("category", "item"))
      item["stats"] = build_stats(item["rarity"], base.get("category", "item"), item.get("effect", ""))
      items.append(item)
    return items

  count = random.randint(1, 2)
  items = [roll_item(state) for _ in range(count)]
  bonus_pools = [("Epic", 50.0), ("Mythical+", 15.0), ("Dragon", 4.0), ("Dragon+", 1.0), ("RoR-", 0.05)]
  for pool_name, chance in bonus_pools:
    if random.random() * 100 < chance:
      available = [x for x in ITEM_CATALOG if not x.get("custom_template") and x["id"] not in state.get("used_ids", [])] or ITEM_CATALOG
      base = random.choice(available)
      state.setdefault("used_ids", []).append(base["id"])
      item = decorate_item(base)
      item["rarity"] = weighted_choice({
        "Epic": [("Epic", 1.0), ("Legendary", 0.45), ("Mythical", 0.15)],
        "Mythical+": [("Mythical+", 1.0), ("Mythical++", 0.35)],
        "Dragon": [("Dragon", 1.0), ("Dragon+", 0.2)],
        "Dragon+": [("Dragon+", 1.0), ("RoR-", 0.02)],
        "RoR-": [("RoR-", 1.0)],
      }.get(pool_name, [(pool_name, 1.0)]))
      item["rarity_flavor"] = RARITY_NAME_FLAVOR.get(item["rarity"], "")
      item["quality"] = roll_quality()
      item["name"] = f"{item.get('rarity_flavor', '')} {base['base_name']}".strip()
      item["description"] = generate_item_description(item, item["rarity"], item["quality"], base.get("category", "item"))
      item["stats"] = build_stats(item["rarity"], base.get("category", "item"), item.get("effect", ""))
      items.append(item)
  return items

def compact_item(item):
  keys = ("id", "instance_id", "name", "base_name", "category", "rarity", "quality", "rarity_flavor", "description", "effect", "damage", "properties", "value", "stats", "custom_catalog_id", "attachment_url")
  return {k: item.get(k) for k in keys}


def _new_instance_id(prefix="item"):
  return f"{prefix}-{uuid.uuid4().hex[:12]}"

def _fresh_instance_id(state=None, prefix="item"):
  """Generate an ID that has never been obtained in this guild state."""
  state = state or {}
  used = {str(x) for x in state.get("obtained_instance_ids", [])}
  used.update(str(x) for x in state.get("possessions", {}).keys())
  for rows in state.get("inventories", {}).values():
    for row in rows:
      if isinstance(row, dict) and row.get("id"):
        used.add(str(row.get("id")))
  while True:
    candidate = _new_instance_id(prefix)
    if candidate not in used:
      return candidate


def _discovery_id(item):
  return str(item.get("discovery_id") or item.get("id") or "")


def _set_item_state(guild_id, item_id, state_name, **extra):
  record = item_state(guild_id).setdefault("possessions", {}).get(str(item_id))
  if record is not None:
    record["state"] = state_name
    record.update(extra)
  return record


def _find_discovery(guild_id, discovery_id):
  state = item_state(guild_id)
  return state.setdefault("active_discoveries", {}).get(str(discovery_id))


def _record_discovery(guild_id, record):
  state = item_state(guild_id)
  state.setdefault("active_discoveries", {})[str(record["discovery_id"])] = record
  return record


def _mark_discovery(guild_id, discovery_id, state_name, **extra):
  record = _find_discovery(guild_id, discovery_id)
  if record is None:
    return None
  record["state"] = state_name
  record.update(extra)
  return record


async def _edit_discovery_message(guild, discovery, *, state_name=None, actor=None):
  """Edit the original GM discovery message in-place after every state change."""
  if not discovery:
    return
  channel_id = discovery.get("source_channel_id") or discovery.get("channel_id")
  message_id = discovery.get("source_message_id") or discovery.get("message_id")
  if not channel_id or not message_id or bot is None:
    return
  try:
    channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
    message = await channel.fetch_message(int(message_id))
    item = discovery.get("item", {})
    name = item.get("name", "Unknown")
    status = state_name or discovery.get("state", "UNCLAIMED")
    lines = [
      f"**{name}**",
      f"Type: **{str(item.get('category', 'Item')).title()}**",
      f"Rarity: **{item.get('rarity', 'Common')}**",
      str(item.get("description", "") or "No description."),
      "",
      f"Status: **{status}**",
    ]
    if actor:
      lines.append(f"By: {actor.mention if hasattr(actor, 'mention') else actor}")
    if status == "UNCLAIMED":
      lines.extend(["", f"Claim it with `/claim {name}`", "You have **30 seconds**."])
      expires = discovery.get("item_expires_at")
      lines.append(f"Expires <t:{int(expires)}:R>." if expires else "Expires **never**.")
    elif status in {"CLAIMED", "SECURED", "STOLEN", "TRANSFERRED"}:
      lines.extend(["", f"`/claim {name}`", f"`/steal {name}`", f"`/secure {name}`"])
    elif status in {"EXPIRED", "CANCELLED", "DESTROYED"}:
      lines.extend(["", "This discovery is no longer active."])
    content = "\n".join(lines)
    attachment_url = item.get("attachment_url")
    if attachment_url:
      content += f"\n\n{attachment_url}"
    await message.edit(content=content, allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False))
  except (discord.NotFound, discord.Forbidden, discord.HTTPException):
    pass


async def _expire_discovery(guild, discovery):
  if not discovery or discovery.get("state") not in {"UNCLAIMED", "ACTIVE"}:
    return False
  discovery["state"] = "EXPIRED"
  discovery["expired_at"] = datetime.now(timezone.utc).timestamp()
  await _edit_discovery_message(guild, discovery, state_name="EXPIRED")
  return True


def add_item(guild_id, user_id, item, held=True, **metadata):
  state = item_state(guild_id)
  now = datetime.now(timezone.utc).timestamp()
  user_key = str(user_id)
  item = compact_item(item)
  item_id = str(item.get("id") or "")
  if not item_id:
    item_id = _fresh_instance_id(state)
    item["id"] = item_id
    item["instance_id"] = item_id
  # A physical instance can only be obtained once. Never let a stale discovery
  # or duplicate claim create a second ownership record for the same instance.
  if item_id in state.setdefault("possessions", {}) or item_id in {str(x) for x in state.get("obtained_instance_ids", [])}:
    return None
  inv = state["inventories"].setdefault(user_key, [])
  entry = item | {"claimed_at": now, "secured": False, "held": held, "state": "CLAIMED" if held else "INVENTORY", **metadata}
  inv.append(entry)
  if held:
    possessions = state.setdefault("possessions", {})
    possessions[item["id"]] = {
      "owner_id": int(user_id), "item": item, "secured": False, "claimed_at": now, "held": True,
      "state": "CLAIMED", **metadata
    }
    state.setdefault("obtained_instance_ids", []).append(item["id"])
    return possessions[item["id"]]
  return None


def remove_inventory_item(guild_id, user_id, item_id):
  inv = item_state(guild_id)["inventories"].get(str(user_id), [])
  for i, entry in enumerate(inv):
    if str(entry.get("id")) == str(item_id):
      return inv.pop(i)
  return None


async def item_name_autocomplete(interaction: discord.Interaction, current: str):
  """Show owned item instances, including unique IDs for duplicate names."""
  if interaction.guild is None:
    return []
  state = item_state(interaction.guild.id)
  inventory = state.get("inventories", {}).get(str(interaction.user.id), [])
  current_key = item_key(current)
  choices = []
  for entry in inventory:
    item = entry.get("item", entry)
    name = str(item.get("name", "")).strip()
    item_id = str(item.get("id") or entry.get("id") or "").strip()
    if not name or not item_id:
      continue
    haystack = f"{name} {item_id}".casefold()
    if current_key and current_key not in item_key(name) and current.casefold() not in item_id.casefold():
      continue
    status = "SECURED" if entry.get("secured") else "HELD" if entry.get("held", True) else "INVENTORY"
    label = f"{name} [{item_id[-8:]}] — {status}"[:100]
    choices.append(app_commands.Choice(name=label, value=item_id))
    if len(choices) >= 25:
      break
  return choices


def mark_inventory_item(guild_id, user_id, item_id, **changes):
  for entry in item_state(guild_id)["inventories"].get(str(user_id), []):
    if str(entry.get("id")) == str(item_id):
      entry.update(changes)
      return entry
  return None


def item_key(name):
  return re.sub(r"[^a-z0-9]+", "_", str(name).casefold()).strip("_")


def possession_bucket(guild_id):
  return item_state(guild_id).setdefault("possessions", {})


def _selector_is_instance(record, selector):
  """Return True when selector identifies this exact physical item instance."""
  selector = str(selector or "").strip().casefold()
  item = record.get("item", {}) if isinstance(record, dict) else {}
  ids = {
    str(record.get("id", "")),
    str(item.get("id", "")),
    str(item.get("instance_id", "")),
  }
  return selector and selector in {x.casefold() for x in ids if x}


def find_possessed_item(guild_id, name):
  selector = str(name or "").strip()
  key = item_key(selector)
  rows = list(possession_bucket(guild_id).items())
  # Exact instance IDs always win over a display-name match.
  for stored_key, record in rows:
    if _selector_is_instance(record, selector):
      return stored_key, record
  for stored_key, record in rows:
    if item_key(record.get("item", {}).get("name", "")) == key:
      return stored_key, record
  return None, None

def find_owned_item(guild_id, user_id, name, *, prefer_unsecured=False):
  """Find a player's exact item instance, or a matching name when no ID is supplied."""
  selector = str(name or "").strip()
  key = item_key(selector)
  uid = int(user_id)
  rows = [(k, r) for k, r in possession_bucket(guild_id).items() if int(r.get("owner_id", 0)) == uid]
  # Exact instance IDs are authoritative.
  for stored_key, record in rows:
    if _selector_is_instance(record, selector):
      return stored_key, record
  matches = [(k, r) for k, r in rows if item_key(r.get("item", {}).get("name", "")) == key]
  if prefer_unsecured:
    for stored_key, record in matches:
      if not record.get("secured") and record.get("state") != "SECURED":
        return stored_key, record
  return matches[0] if matches else (None, None)


def _sync_possession_inventory(guild_id, owner_id, item, *, secured=False, held=True, **metadata):
  """Transfer an exact item instance and keep inventory + possession state synchronized."""
  state = item_state(guild_id)
  now = datetime.now(timezone.utc).timestamp()
  item_id = str(item["id"])
  claimed_at = metadata.pop("claimed_at", now)
  for uid, rows in state.setdefault("inventories", {}).items():
    state["inventories"][uid] = [row for row in rows if str(row.get("id")) != item_id]
  entry = dict(item)
  entry.update({"claimed_at": claimed_at, "secured": bool(secured), "held": bool(held), **metadata})
  state["inventories"].setdefault(str(owner_id), []).append(entry)
  possession = state.setdefault("possessions", {}).get(item_id) or {}
  possession.update({
    "owner_id": int(owner_id), "item": item, "secured": bool(secured),
    "held": bool(held), "claimed_at": claimed_at, **metadata
  })
  state["possessions"][item_id] = possession
  return possession


def item_status_text(record):
  if not record:
    return " UNKNOWN"
  if record.get("secured"):
    return " SECURED — cannot be stolen"
  if record.get("stolen_at"):
    return " STOLEN / HELD — can be stolen back"
  return " CLAIMED / HELD — can be stolen"


def item_owner_mention(record):
  owner_id = record.get("owner_id")
  return f"<@{owner_id}>" if owner_id is not None else "Unknown"


def format_item(item):
  q = item.get("quality", "Standard")
  line = f"**{item['name']}** — {item['rarity']} • {q} • {item['category'].title()}"
  if item.get("description"):
    line += f"\n *{item['description']}*"
  return line


def item_lines(items):
  return "\n".join(f"• {format_item(x)}" for x in items)


async def send_dm_offer(guild, state, items, member):
  expires = (datetime.now(timezone.utc) + timedelta(minutes=1)).timestamp()
  state["dm_drop"] = {
    "items": items, "recipient_id": str(member.id), "expires_at": expires,
    "attempt": int((state.get("dm_drop") or {}).get("attempt", 0)) + 1
  }
  try:
    await member.send(
      f"**SECRET DISCOVERY**\n\n{item_lines(items)}\n\n"
      f"Claim an item with `/claim <item name>`\n"
      f"You have **1 minute**.\nExpires <t:{int(expires)}:R>\n\n"
      f"*This discovery is private.*",
      allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )
    save_item_data()
    return True
  except (discord.Forbidden, discord.HTTPException):
    return False


async def pass_dm_to_next(guild, state, items):
  old = (state.get("dm_drop") or {}).get("recipient_id")
  if old and str(old).isdigit():
    old_member = guild.get_member(int(old))
    if old_member:
      try:
        await old_member.send(" **SECRET DISCOVERY EXPIRED**\nYour one-minute opportunity expired. The discovery has moved on.")
      except (discord.Forbidden, discord.HTTPException):
        pass
  eligible = [m for m in guild.members if not m.bot and str(m.id) != str(old)]
  random.shuffle(eligible)
  state["dm_drop"] = None
  for member in eligible:
    if await send_dm_offer(guild, state, items, member):
      return True
  return False


async def create_dm_item(guild, state, items):
  eligible = [m for m in guild.members if not m.bot]
  if not eligible:
    return False
  current = (state.get("dm_drop") or {}).get("recipient_id")
  choices = [m for m in eligible if str(m.id) != str(current)] or eligible
  random.shuffle(choices)
  state["dm_drop"] = None
  for member in choices:
    if await send_dm_offer(guild, state, items, member):
      return True
  state["dm_drop"] = None
  return False


async def announce_public_drop(channel, items):
  try:
    # Import lazily to avoid the items <-> gm_tools import cycle.
    await channel.send(
      "@everyone\n **A mysterious discovery has appeared!**\n\n"
      + item_lines(items) + "\n\n"
      + " **Click the button below to claim. First claim wins.**",
      allowed_mentions=discord.AllowedMentions(everyone=True),
    )
  except discord.HTTPException:
    pass


async def hidden_item_loop():
  await bot.wait_until_ready()
  while not bot.is_closed():
    changed = False
    now = datetime.now(timezone.utc).timestamp()
    for guild in bot.guilds:
      state = item_state(guild.id)
      if state.get("public_enabled") and state.get("public_item") is None:
        if state.get("public_next") is None:
          state["public_next"] = next_spawn(); changed = True
        elif now >= state["public_next"]:
          items = roll_drop(state)
          state["public_item"] = items
          state["public_next"] = None
          changed = True
          channel = bot.get_channel(state.get("public_channel_id")) if state.get("public_channel_id") else guild.system_channel
          if channel:
            await announce_public_drop(channel, items)
      drop = state.get("dm_drop")
      if drop:
        if now >= float(drop.get("expires_at", 0)):
          await pass_dm_to_next(guild, state, drop.get("items", [])); changed = True
      elif state.get("dm_enabled") and now >= float(state.get("dm_next") or 0):
        items = roll_drop(state, secret_dm=True)
        await create_dm_item(guild, state, items)
        state["dm_next"] = next_spawn(); changed = True
    if changed:
      save_item_data()
    await asyncio.sleep(1)


@ADMIN_ITEM_GROUP.command(name="rng-start", description="GM: start hidden public item RNG.")
async def item_rng_start(interaction: discord.Interaction):
  if not is_staff(interaction): return await interaction.response.send_message("GM only.", ephemeral=True)
  s = item_state(interaction.guild.id); s["public_enabled"] = True; s["public_channel_id"] = interaction.channel.id
  if s.get("public_next") is None: s["public_next"] = next_spawn()
  save_item_data(); await interaction.response.send_message(" Hidden public item RNG started.", ephemeral=True)

@ADMIN_ITEM_GROUP.command(name="rng-stop", description="GM: stop hidden public item RNG.")
async def item_rng_stop(interaction: discord.Interaction):
  if not is_staff(interaction): return await interaction.response.send_message("GM only.", ephemeral=True)
  s=item_state(interaction.guild.id); s["public_enabled"]=False; save_item_data(); await interaction.response.send_message(" Hidden public item RNG stopped.", ephemeral=True)

@ADMIN_ITEM_GROUP.command(name="dm-start", description="GM: start automatic secret DM item drops.")
async def item_dm_start(interaction: discord.Interaction):
  if not is_staff(interaction): return await interaction.response.send_message("GM only.", ephemeral=True)
  s=item_state(interaction.guild.id); s["dm_enabled"]=True
  if s.get("dm_next") is None: s["dm_next"]=next_spawn()
  save_item_data(); await interaction.response.send_message(" Secret DM item RNG started. Claim window: **1 minute**.", ephemeral=True)

@ADMIN_ITEM_GROUP.command(name="dm-stop", description="GM: stop automatic secret DM item drops.")
async def item_dm_stop(interaction: discord.Interaction):
  if not is_staff(interaction): return await interaction.response.send_message("GM only.", ephemeral=True)
  s=item_state(interaction.guild.id); s["dm_enabled"]=False; save_item_data(); await interaction.response.send_message(" Secret DM item RNG stopped.", ephemeral=True)

@ADMIN_ITEM_GROUP.command(name="status", description="GM: view hidden item RNG status.")
async def item_status(interaction: discord.Interaction):
  if not is_staff(interaction): return await interaction.response.send_message("GM only.", ephemeral=True)
  s=item_state(interaction.guild.id); dm=s.get("dm_drop"); dm_text="none"
  if dm: dm_text=f"{len(dm.get('items', []))} item(s) → <@{dm['recipient_id']}> expires <t:{int(dm['expires_at'])}:R>"
  public=s.get("public_item") or []
  await interaction.response.send_message(
    f"**Hidden RPG Status**\nPublic RNG: {'ON' if s.get('public_enabled') else 'OFF'}\n"
    f"Active public items: {len(public)}\nDM RNG: {'ON' if s.get('dm_enabled') else 'OFF'}\n"
    f"Secret DM: {dm_text}\nUsed base items: {len(s.get('used_ids', []))}/{len(ITEM_CATALOG)}",
    ephemeral=True,
  )

@ADMIN_ITEM_GROUP.command(name="force-random", description="GM: force a hidden public random item drop now.")
async def item_force_random(interaction: discord.Interaction):
  if not is_staff(interaction):
    return await interaction.response.send_message("GM only.", ephemeral=True)

  # Acknowledge Discord immediately. Everything after this point is guarded
  # so a generation/save/channel error cannot leave the command stuck on
  # “is thinking...”.
  await interaction.response.defer(ephemeral=True, thinking=True)

  try:
    guild = interaction.guild
    if guild is None:
      return await interaction.followup.send("This command can only be used in a server.", ephemeral=True)

    state = item_state(guild.id)
    items = roll_drop(state)
    if not items:
      return await interaction.followup.send("I couldn't generate a random item drop.", ephemeral=True)

    state["public_item"] = items
    state["public_next"] = None
    save_item_data()

    channel_id = state.get("public_channel_id")
    channel = bot.get_channel(channel_id) if channel_id else None
    if channel is None:
      channel = interaction.channel

    if channel is None:
      return await interaction.followup.send(
        f"Generated **{len(items)} item(s)**, but I couldn't find a channel to announce them in.",
        ephemeral=True,
      )

    await announce_public_drop(channel, items)
    await interaction.followup.send(
      f"Forced public drop with **{len(items)} item(s)**.",
      ephemeral=True,
    )
  except Exception as exc:
    print(f"[item-force-random] {type(exc).__name__}: {exc}")
    try:
      await interaction.followup.send(
        "The random item drop failed. Check the bot console for the exact error.",
        ephemeral=True,
      )
    except discord.HTTPException:
      pass

@ADMIN_ITEM_GROUP.command(name="force", description="GM: force a specific item into the public discovery.")
@app_commands.describe(item_name="Exact base item name or generated item name")
async def item_force(interaction: discord.Interaction, item_name: str):
  if not is_staff(interaction): return await interaction.response.send_message("GM only.", ephemeral=True)
  await interaction.response.defer(ephemeral=True)
  base=resolve_base_item(item_name)
  if not base: return await interaction.followup.send("Base item not found.", ephemeral=True)
  s=item_state(interaction.guild.id); item=decorate_item(base); s["public_item"]=[item]; s["public_next"]=None
  if base["id"] not in s.setdefault("used_ids", []): s["used_ids"].append(base["id"])
  save_item_data(); ch=bot.get_channel(s.get("public_channel_id")) or interaction.channel; await announce_public_drop(ch,[item])
  await interaction.followup.send(f"Forced public item: **{item['name']}** ({item['rarity']}).", ephemeral=True)

@ADMIN_ITEM_GROUP.command(name="dm-force-random", description="GM: force a random secret DM item drop now.")
async def item_dm_force_random(interaction: discord.Interaction):
  if not is_staff(interaction): return await interaction.response.send_message("GM only.", ephemeral=True)
  await interaction.response.defer(ephemeral=True)
  s=item_state(interaction.guild.id); items=roll_drop(s, secret_dm=True); await create_dm_item(interaction.guild,s,items); save_item_data()
  await interaction.followup.send(f"Forced secret DM drop with **{len(items)} item(s)**.", ephemeral=True)

@ADMIN_ITEM_GROUP.command(name="dm-force", description="GM: force a specific secret DM item now.")
@app_commands.describe(item_name="Exact base item name or generated item name")
async def item_dm_force(interaction: discord.Interaction, item_name: str):
  if not is_staff(interaction): return await interaction.response.send_message("GM only.", ephemeral=True)
  await interaction.response.defer(ephemeral=True)
  base=resolve_base_item(item_name)
  if not base: return await interaction.followup.send("Base item not found.", ephemeral=True)
  s=item_state(interaction.guild.id); item=decorate_item(base); await create_dm_item(interaction.guild,s,[item]); save_item_data()
  await interaction.followup.send(f"Forced secret DM item: **{item['name']}** ({item['rarity']}).", ephemeral=True)

@ADMIN_ITEM_GROUP.command(name="dm-time", description="GM: set secret DM claim time in minutes.")
@app_commands.describe(minutes="1-60 minutes")
async def item_dm_time(interaction: discord.Interaction, minutes: int):
  if not is_staff(interaction): return await interaction.response.send_message("GM only.", ephemeral=True)
  if not 1<=minutes<=60: return await interaction.response.send_message("Use 1-60 minutes.", ephemeral=True)
  item_state(interaction.guild.id)["dm_minutes"]=minutes
  save_item_data(); await interaction.response.send_message(f" Secret DM timer setting saved as **{minutes} minutes**. Automatic drops currently use the requested **1-minute** window.", ephemeral=True)

@ADMIN_ITEM_GROUP.command(name="dm-chance", description="GM: set the stored secret-drop chance setting.")
@app_commands.describe(percent="0-100 percent")
async def item_dm_chance(interaction: discord.Interaction, percent: float):
  if not is_staff(interaction): return await interaction.response.send_message("GM only.", ephemeral=True)
  if not 0<=percent<=100: return await interaction.response.send_message("Use 0-100 percent.", ephemeral=True)
  item_state(interaction.guild.id)["dm_chance"]=percent; save_item_data(); await interaction.response.send_message(f" Secret DM chance setting saved as **{percent:g}%**.", ephemeral=True)


async def _edit_item_source_message(guild, record, status_line):
  """Update the original GM discovery message after claim/secure/steal."""
  channel_id = record.get("source_channel_id")
  message_id = record.get("source_message_id")
  if not channel_id or not message_id or bot is None:
    return
  try:
    channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
    message_obj = await channel.fetch_message(int(message_id))
    item = record.get("item", {})
    name = item.get("name", "Unknown")
    updated = (
      f"**{name}**\n"
      f"Type: **{str(item.get('category', 'Item')).title()}**\n"
      f"Rarity: **{item.get('rarity', 'Common')}**\n"
      f"{item.get('description', '') or 'No description.'}\n\n"
      f"{status_line}\n\n"
      f"Commands:\n"
      f"`/claim {name}`\n"
      f"`/steal {name}`\n"
      f"`/secure {name}`"
    )
    attachment_url = item.get("attachment_url")
    if attachment_url:
      updated += f"\n\n{attachment_url}"
    await message_obj.edit(content=updated, allowed_mentions=discord.AllowedMentions.none())
  except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
    pass


async def _claim_response(interaction, item, message, ephemeral=False):
  stats = item.get("stats", {})
  stat_lines = stats_text(stats)
  quality = item.get("quality", "Standard")
  description = item.get("description", "")
  details = f"\n\n**{item.get('rarity', 'Common')}** • **{quality}** • **{item.get('category', 'item').title()}**"
  if description:
    details += f"\n **Description:** {description}"
  if item.get("effect"):
    details += f"\n **Effect:** {item['effect']}"
  details += f"\n\n **Stats**\n{stat_lines}"
  message = message + details
  return await interaction.response.send_message(message, ephemeral=ephemeral)


async def claim_item_autocomplete(interaction: discord.Interaction, current: str):
  if not bot:
    return []
  out = []
  needle = str(current or "").casefold()
  seen = set()
  for guild in bot.guilds:
    state = item_state(guild.id)
    candidates = list(state.get("public_item") or [])
    drop = state.get("dm_drop")
    if drop and str(drop.get("recipient_id")) == str(interaction.user.id):
      candidates.extend(drop.get("items", []))
    gm = state.get("gm_tools", {})
    for key in ("pending_server_claim", "pending_dm_claim"):
      pending = gm.get(key) if isinstance(gm, dict) else None
      if pending and str(pending.get("recipient_id", pending.get("user_id", ""))) == str(interaction.user.id) or (key == "pending_server_claim" and pending):
        if pending and pending.get("item"):
          candidates.append(pending.get("item"))
    for item in candidates:
      name = str(item.get("name", "")).strip()
      iid = str(item.get("id") or item.get("instance_id") or "").strip()
      if not name or not iid or iid in seen:
        continue
      if needle and needle not in name.casefold() and needle not in iid.casefold():
        continue
      seen.add(iid)
      out.append(app_commands.Choice(name=f"{name} [{iid[-8:]}]"[:100], value=iid))
      if len(out) >= 25:
        return out
  return out

@ITEM_GROUP.command(name="claim", description="Claim an active public or secret DM item.")
@app_commands.describe(item_name="Item name or exact item instance ID.")
@app_commands.autocomplete(item_name=claim_item_autocomplete)
async def claim_item(interaction: discord.Interaction, item_name: str):
  if not str(item_name or "").strip():
    return await interaction.response.send_message("An item name is required.", ephemeral=True)
  requested = item_key(item_name)
  now = datetime.now(timezone.utc).timestamp()

  async with ITEM_ACTION_LOCK:
    # GM discoveries are authoritative. A persisted discovery ID is the identity;
    # the display name is only the human-friendly lookup key.
    for g in bot.guilds:
      gm = item_state(g.id).get("gm_tools", {})
      if not isinstance(gm, dict):
        continue

      pending = gm.get("pending_dm_claim") if interaction.guild is None else None
      if pending and str(pending.get("recipient_id", pending.get("user_id"))) == str(interaction.user.id):
        if pending.get("state") not in {"UNCLAIMED", "ACTIVE"}:
          return await interaction.response.send_message("Already claimed.")
        if float(pending.get("claim_expires_at", 0) or 0) > 0 and now >= float(pending.get("claim_expires_at", 0) or 0):
          pending["state"] = "EXPIRED"
          await _edit_discovery_message(g, pending, state_name="EXPIRED")
          gm["pending_dm_claim"] = None
          save_item_data()
          return await interaction.response.send_message("This discovery has expired.")
        item = pending.get("item", {})
        if not (item_key(item.get("name", "")) == requested or str(item.get("id", "")).casefold() == str(item_name).casefold() or str(item.get("instance_id", "")).casefold() == str(item_name).casefold()):
          return await interaction.response.send_message("That item is not the active discovery.")
        record = add_item(
          g.id, interaction.user.id, item, held=True,
          source_message_id=pending.get("message_id"),
          source_channel_id=pending.get("channel_id"),
          discovery_id=pending.get("discovery_id"),
        )
        if record is None:
          return await interaction.response.send_message("That item instance has already been obtained or claimed.", ephemeral=True)
        pending["state"] = "CLAIMED"
        pending["claimed_by"] = interaction.user.id
        pending["claimed_at"] = now
        if record:
          record["state"] = "CLAIMED"
        await _edit_discovery_message(g, pending, state_name="CLAIMED", actor=interaction.user)
        gm["pending_dm_claim"] = None
        save_item_data()
        return await interaction.response.send_message(f"Claimed **{item['name']}**.")

      if interaction.guild is not None and interaction.guild.id == g.id:
        pending = gm.get("pending_server_claim")
        if pending:
          if pending.get("state") not in {"UNCLAIMED", "ACTIVE"}:
            return await interaction.response.send_message("Already claimed.")
          if float(pending.get("claim_expires_at", 0) or 0) > 0 and now >= float(pending.get("claim_expires_at", 0) or 0):
            pending["state"] = "EXPIRED"
            await _edit_discovery_message(g, pending, state_name="EXPIRED")
            gm["pending_server_claim"] = None
            save_item_data()
            return await interaction.response.send_message("This discovery has expired.")
          item = pending.get("item", {})
          if not (item_key(item.get("name", "")) == requested or str(item.get("id", "")).casefold() == str(item_name).casefold() or str(item.get("instance_id", "")).casefold() == str(item_name).casefold()):
            return await interaction.response.send_message("That item is not the active discovery.")
          record = add_item(
            g.id, interaction.user.id, item, held=True,
            source_message_id=pending.get("message_id"),
            source_channel_id=pending.get("channel_id"),
            discovery_id=pending.get("discovery_id"),
          )
          pending["state"] = "CLAIMED"
          pending["claimed_by"] = interaction.user.id
          pending["claimed_at"] = now
          if record:
            record["state"] = "CLAIMED"
          await _edit_discovery_message(g, pending, state_name="CLAIMED", actor=interaction.user)
          gm["pending_server_claim"] = None
          save_item_data()
          return await interaction.response.send_message(f"{interaction.user.mention} claimed **{item['name']}**.")

    # Existing public/DM item systems. These are also serialized by the same lock.
    if interaction.guild is not None:
      guild = interaction.guild
      state = item_state(guild.id)
      public_items = state.get("public_item") or []
      for idx, item in enumerate(public_items):
        if item_key(item.get("name", "")) == requested or str(item.get("id", "")).casefold() == str(item_name).casefold() or str(item.get("instance_id", "")).casefold() == str(item_name).casefold():
          record = add_item(guild.id, interaction.user.id, item, held=True)
          if record is None:
            return await interaction.response.send_message("That item instance has already been obtained or claimed.", ephemeral=True)
          public_items.pop(idx)
          state["public_item"] = public_items or None
          if not public_items:
            state["public_next"] = next_spawn() if state.get("public_enabled") else None
          save_item_data()
          return await interaction.response.send_message(f"{interaction.user.mention} claimed **{item['name']}**.")
      drop = state.get("dm_drop")
      if drop and str(drop.get("recipient_id")) == str(interaction.user.id):
        if now >= float(drop.get("expires_at", 0) or 0):
          await pass_dm_to_next(guild, state, drop.get("items", []))
          save_item_data()
          return await interaction.response.send_message("This discovery has expired.", ephemeral=True)
        for idx, item in enumerate(drop.get("items", [])):
          if item_key(item.get("name", "")) == requested or str(item.get("id", "")).casefold() == str(item_name).casefold() or str(item.get("instance_id", "")).casefold() == str(item_name).casefold():
            record = add_item(guild.id, interaction.user.id, item, held=True)
            if record is None:
              return await interaction.response.send_message("That item instance has already been obtained or claimed.", ephemeral=True)
            drop["items"].pop(idx)
            if not drop["items"]:
              state["dm_drop"] = None
            save_item_data()
            return await interaction.response.send_message(f"Claimed **{item['name']}**.", ephemeral=True)
      return await interaction.response.send_message("That item is not currently available to claim.", ephemeral=True)

    matches = []
    for guild in bot.guilds:
      state = item_state(guild.id)
      drop = state.get("dm_drop")
      if drop and str(drop.get("recipient_id")) == str(interaction.user.id):
        matches.append((guild, state, drop))
    if not matches:
      return await interaction.response.send_message("You do not have an active secret discovery to claim.")
    guild, state, drop = matches[0]
    if now >= float(drop.get("expires_at", 0) or 0):
      await pass_dm_to_next(guild, state, drop.get("items", []))
      save_item_data()
      return await interaction.response.send_message("This discovery has expired.")
    for idx, item in enumerate(drop.get("items", [])):
      if item_key(item.get("name", "")) == requested or str(item.get("id", "")).casefold() == str(item_name).casefold() or str(item.get("instance_id", "")).casefold() == str(item_name).casefold():
        record = add_item(guild.id, interaction.user.id, item, held=True)
        if record is None:
          return await interaction.response.send_message("That item instance has already been obtained or claimed.", ephemeral=True)
        drop["items"].pop(idx)
        if not drop["items"]:
          state["dm_drop"] = None
        save_item_data()
        return await interaction.response.send_message(f"Claimed **{item['name']}**.")
    return await interaction.response.send_message("That item is not in your active discovery.")


@INVENTORY_GROUP.command(name="secure", description="Secure a claimed item so nobody can steal it.")
@app_commands.describe(item_name="The item to secure.")
@app_commands.autocomplete(item_name=item_name_autocomplete)
async def secure_item(interaction: discord.Interaction, item_name: str):
  if not str(item_name or "").strip():
    return await interaction.response.send_message("An item name is required.")
  async with ITEM_ACTION_LOCK:
    guild = interaction.guild
    if guild is None:
      guild = next((g for g in bot.guilds if find_owned_item(g.id, interaction.user.id, item_name)[1] is not None), None)
    if guild is None:
      return await interaction.response.send_message("You don't currently possess that item.")
    _, record = find_owned_item(guild.id, interaction.user.id, item_name, prefer_unsecured=True)
    if record is None:
      return await interaction.response.send_message("You don't currently possess that item.")
    if record.get("secured"):
      return await interaction.response.send_message(f"**{record['item']['name']}** is already secured.")
    record["secured"] = True
    record["state"] = "SECURED"
    mark_inventory_item(guild.id, interaction.user.id, record["item"]["id"], secured=True, held=True, state="SECURED")
    discovery = _find_discovery(guild.id, record.get("discovery_id")) if record.get("discovery_id") else None
    if discovery:
      discovery["state"] = "SECURED"
      discovery["secured_by"] = interaction.user.id
      discovery["secured_at"] = datetime.now(timezone.utc).timestamp()
      await _edit_discovery_message(guild, discovery, state_name="SECURED", actor=interaction.user)
    save_item_data()
    return await interaction.response.send_message(f"**{record['item']['name']}** secured by {interaction.user.mention}.")

@INVENTORY_GROUP.command(name="unsecure", description="Unsecure one of your items and put it back into your hands.")
@app_commands.describe(item_name="Choose an item from your inventory to unsecure.")
@app_commands.autocomplete(item_name=item_name_autocomplete)
async def unsecure_item(interaction: discord.Interaction, item_name: str):
  guild = interaction.guild
  if guild is None:
    guild = next((g for g in bot.guilds if find_owned_item(g.id, interaction.user.id, item_name)[1] is not None), None)
  if guild is None:
    return await interaction.response.send_message("You don't currently possess that item.",)
  _, record = find_owned_item(guild.id, interaction.user.id, item_name)
  if record is None:
    return await interaction.response.send_message("You don't currently possess that item.",)
  if not record.get("secured"):
    return await interaction.response.send_message(f"**{record['item']['name']}** is not secured.")
  record["secured"] = False
  record["held"] = True
  record["state"] = "CLAIMED"
  mark_inventory_item(guild.id, interaction.user.id, record["item"]["id"], secured=False, held=True, state="CLAIMED")
  discovery = _find_discovery(guild.id, record.get("discovery_id")) if record.get("discovery_id") else None
  if discovery:
    discovery["state"] = "CLAIMED"
    await _edit_discovery_message(guild, discovery, state_name="CLAIMED", actor=interaction.user)
  save_item_data()
  await interaction.response.send_message(f"**{record['item']['name']}** unsecured.")

@INVENTORY_GROUP.command(name="steal", description="Steal an unsecured item from another player's possession.")
@app_commands.describe(item_name="Item name or exact item instance ID.")
@app_commands.autocomplete(item_name=item_name_autocomplete)
async def steal(interaction: discord.Interaction, item_name: str):
  if not str(item_name or "").strip():
    return await interaction.response.send_message("An item name is required.")
  async with ITEM_ACTION_LOCK:
    guild = interaction.guild
    if guild is None:
      guild = next((g for g in bot.guilds if find_possessed_item(g.id, item_name)[1] is not None), None)
    if guild is None:
      return await interaction.response.send_message("That item is not currently being held by anyone.")
    _, record = find_possessed_item(guild.id, item_name)
    if record is None:
      return await interaction.response.send_message("That item is not currently being held by anyone.")
    item = record["item"]
    old_owner = int(record["owner_id"])
    if old_owner == interaction.user.id:
      return await interaction.response.send_message(f"You already have **{item['name']}**.")
    if record.get("secured") or record.get("state") == "SECURED":
      return await interaction.response.send_message(f"**{item['name']}** is secured and cannot be stolen.")
    removed = remove_inventory_item(guild.id, old_owner, item["id"])
    if removed is None:
      return await interaction.response.send_message("Ownership data is out of sync; steal cancelled.")
    now = datetime.now(timezone.utc).timestamp()
    item_state(guild.id)["inventories"].setdefault(str(interaction.user.id), []).append(
      item | {"claimed_at": record.get("claimed_at", now), "secured": False, "held": True, "stolen_at": now, "state": "STOLEN", "discovery_id": record.get("discovery_id"), "source_message_id": record.get("source_message_id"), "source_channel_id": record.get("source_channel_id")}
    )
    record["owner_id"] = interaction.user.id
    record["secured"] = False
    record["stolen_at"] = now
    record["held"] = True
    record["state"] = "STOLEN"
    discovery = _find_discovery(guild.id, record.get("discovery_id")) if record.get("discovery_id") else None
    if discovery:
      discovery["state"] = "STOLEN"
      discovery["stolen_by"] = interaction.user.id
      discovery["stolen_from"] = old_owner
      discovery["stolen_at"] = now
      await _edit_discovery_message(guild, discovery, state_name="STOLEN", actor=interaction.user)
    save_item_data()
    await interaction.response.send_message(f"{interaction.user.mention} stole **{item['name']}** from <@{old_owner}>.")

@ITEM_GROUP.command(name="info", description="Show the public owner and steal/secure status of an item.")
@app_commands.describe(item_name="Item name or exact item instance ID.")
@app_commands.autocomplete(item_name=item_name_autocomplete)
async def item_info(interaction: discord.Interaction,item_name:str):
  if interaction.guild is None: return await interaction.response.send_message("This command can only be used in a server.",ephemeral=True)
  _,record=find_possessed_item(interaction.guild.id,item_name)
  if record is None:
    for item in item_state(interaction.guild.id).get("public_item") or []:
      if item_key(item.get("name","")) == item_key(item_name):
        embed = discord.Embed(title=item["name"], description=f"**{item['rarity']}** • {item.get('quality','Standard')} • {item.get('category','item').title()}", colour=discord.Colour.dark_grey())
        if item.get("description"): embed.add_field(name="Description", value=item["description"], inline=False)
        if item.get("effect"): embed.add_field(name="Effect", value=item["effect"], inline=False)
        stats = item.get("stats", {})
        embed.add_field(name="Stats", value=stats_text(stats, item), inline=False)
        embed.set_footer(text=f"AVAILABLE • Claim with /claim {item['name']}")
        return await interaction.response.send_message(embed=embed)
    return await interaction.response.send_message(f" **{item_name}** is not currently claimed by a player.")
  item=record["item"]
  stats = item.get("stats", {})
  stat_lines = stats_text(stats, item)
  embed = discord.Embed(title=item["name"], description=f"**{item['rarity']}** • {item.get('quality','Standard')} • {item.get('category','item').title()}", colour=discord.Colour.dark_grey())
  if item.get("description"): embed.add_field(name="Description", value=item["description"], inline=False)
  if item.get("effect"): embed.add_field(name="Effect", value=item["effect"], inline=False)
  embed.add_field(name="Stats", value=stat_lines, inline=False)
  embed.add_field(name="Owner", value=item_owner_mention(record), inline=True)
  embed.add_field(name="Status", value=item_status_text(record), inline=True)
  await interaction.response.send_message(embed=embed)

@INVENTORY_GROUP.command(name="inventory", description="View your RPG inventory privately.")
async def inventory(interaction: discord.Interaction):
  if interaction.guild is None: return await interaction.response.send_message("This command can only be used in a server.",ephemeral=True)
  inv=item_state(interaction.guild.id)["inventories"].get(str(interaction.user.id),[])
  if not inv: return await interaction.response.send_message(" Your inventory is empty.",ephemeral=True)
  possessions=item_state(interaction.guild.id).get("possessions",{}); lines=[]
  for x in inv[-50:]:
    possession=possessions.get(x.get("id")); status=item_status_text(possession) if possession and str(possession.get("owner_id"))==str(interaction.user.id) else (" SECURED" if x.get("secured") else (" INVENTORY — not held" if not x.get("held",True) else " CLAIMED / HELD — can be stolen"))
    lines.append(f"• **{x['name']}** — `{x.get('id', 'unknown')}` — {x['rarity']} • {x.get('quality','Standard')} ({x['category']}) — **{status}**")
  from .economy import balance
  money = balance(interaction.guild.id, interaction.user.id)

  # Discord limits message content to 2,000 characters. Large inventories can
  # easily exceed that, especially now that each physical item instance has
  # its own ID. Split the inventory into safe-sized ephemeral messages instead
  # of letting the entire /inventory command fail with HTTP 400/50035.
  header = f" **VG:** **{money:,}**\n\n **Your Inventory**\n"
  chunks = []
  current = header
  for line in lines:
    addition = line if current.endswith("\n") else "\n" + line
    if len(current) + len(addition) > 1900:
      chunks.append(current)
      current = line
    else:
      current += addition
  if current:
    chunks.append(current)

  await interaction.response.send_message(chunks[0], ephemeral=True)
  for chunk in chunks[1:]:
    await interaction.followup.send(chunk, ephemeral=True)

@INVENTORY_GROUP.command(name="give", description="Give one of your held items to another player.")
@app_commands.describe(item_name="The exact item name to give.",user="The player who should receive the item.")
async def give_item(interaction: discord.Interaction,item_name:str,user:discord.Member):
  if interaction.guild is None: return await interaction.response.send_message("This command can only be used in a server.",ephemeral=True)
  if user.bot: return await interaction.response.send_message(" You cannot give items to bots.",ephemeral=True)
  _,record=find_owned_item(interaction.guild.id, interaction.user.id, item_name)
  if record is None: return await interaction.response.send_message(f" You do not own **{item_name}** as a held item.",ephemeral=True)
  item=record["item"]; now=datetime.now(timezone.utc).timestamp()
  _sync_possession_inventory(
    interaction.guild.id, user.id, item, secured=False, held=True,
    claimed_at=record.get("claimed_at",now), given_at=now, given_by=interaction.user.id
  )
  item_state(interaction.guild.id)["possessions"][item["id"]].pop("stolen_at",None)
  save_item_data()
  await interaction.response.send_message(f" **{interaction.user.mention}** gave **{item['name']}** to {user.mention}!\n Status: **CLAIMED / HELD — can be stolen**")
  try: await user.send(f" **ITEM RECEIVED**\n\n{interaction.user.display_name} gave you **{item['name']}**.\nIt is currently **HELD** and can be stolen.\nUse `/secure-item {item['name']}` to secure it.")
  except (discord.Forbidden,discord.HTTPException): pass

@INVENTORY_GROUP.command(name="secure-held", description="Secure one of your items.")
@app_commands.describe(item_name="Choose an item from your inventory.")
@app_commands.autocomplete(item_name=item_name_autocomplete)
async def secure_alias(interaction: discord.Interaction, item_name: str):
  return await secure_item.callback(interaction, item_name)


@INVENTORY_GROUP.command(name="rename", description="Rename one of your owned items; the new name is saved permanently.")
@app_commands.describe(item_name="Current item name.", new_name="New name for this individual item.")
@app_commands.autocomplete(item_name=item_name_autocomplete)
async def rename_item(interaction: discord.Interaction, item_name: str, new_name: str):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  new_name = new_name.strip()
  if not new_name or len(new_name) > 80:
    return await interaction.response.send_message("New names must be 1–80 characters.", ephemeral=True)
  _, record = find_possessed_item(interaction.guild.id, item_name)
  if not record or int(record.get("owner_id", 0)) != interaction.user.id:
    return await interaction.response.send_message("You don't own that held item.", ephemeral=True)
  item = record["item"]
  item_id = item["id"]
  old_name = item.get("name", "Unknown")
  item["name"] = new_name
  st = item_state(interaction.guild.id)
  for entries in st["inventories"].values():
    for entry in entries:
      if entry.get("id") == item_id:
        entry["name"] = new_name
  for eq in st.get("equipment", {}).values():
    for slot, equipped in eq.items():
      if isinstance(equipped, dict) and equipped.get("id") == item_id:
        equipped["name"] = new_name
  save_item_data()
  await interaction.response.send_message(f"Renamed **{old_name}** → **{new_name}**. The change is saved.", ephemeral=True)


@app_commands.command(name="secure-item", description="Secure one of your items so it cannot be stolen.")
@app_commands.describe(item_name="The item you want to secure.")
@app_commands.autocomplete(item_name=item_name_autocomplete)
async def secure_item_alias(interaction: discord.Interaction, item_name: str):
  return await secure_item.callback(interaction, item_name)


@app_commands.command(name="unsecure-item", description="Remove security from one of your items.")
@app_commands.describe(item_name="The item you want to unsecure.")
@app_commands.autocomplete(item_name=item_name_autocomplete)
async def unsecure_item_alias(interaction: discord.Interaction, item_name: str):
  return await unsecure_item.callback(interaction, item_name)


@app_commands.command(name="give-item", description="Give one of your held items to another player.")
@app_commands.describe(item_name="The exact item name to give.", user="The player who should receive the item.")
async def give_item_top_level(interaction: discord.Interaction, item_name: str, user: discord.Member):
  return await give_item.callback(interaction, item_name, user)


@app_commands.command(name="claim", description="Claim an active public or DM item drop.")
@app_commands.describe(item_name="The item name to claim.")
async def claim_top_level(interaction: discord.Interaction, item_name: str):
  return await claim_item.callback(interaction, item_name)

@app_commands.command(name="secure", description="Secure one of your items so it cannot be stolen.")
@app_commands.describe(item_name="The item you want to secure.")
@app_commands.autocomplete(item_name=item_name_autocomplete)
async def secure_top_level(interaction: discord.Interaction, item_name: str):
  return await secure_item.callback(interaction, item_name)

@app_commands.command(name="unsecure", description="Remove security from one of your items.")
@app_commands.describe(item_name="The item you want to unsecure.")
@app_commands.autocomplete(item_name=item_name_autocomplete)
async def unsecure_top_level(interaction: discord.Interaction, item_name: str):
  return await unsecure_item.callback(interaction, item_name)

@app_commands.command(name="steal", description="Steal an unsecured item from another player's possession.")
@app_commands.describe(item_name="The exact item name you want to steal.")
async def steal_top_level(interaction: discord.Interaction, item_name: str):
  return await steal.callback(interaction, item_name)


@app_commands.command(name="give", description="Give one of your held items to another player.")
@app_commands.describe(item_name="The exact item name to give.", user="The player who should receive the item.")
async def give_top_level(interaction: discord.Interaction, item_name: str, user: discord.Member):
  return await give_item.callback(interaction, item_name, user)

@ITEM_GROUP.command(name="catalog", description="View the persistent item catalog and custom items.")
async def catalog(interaction: discord.Interaction):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  custom = [x for x in ITEM_CATALOG if x.get("custom_template")]
  standard = [x for x in ITEM_CATALOG if not x.get("custom_template")]
  lines = [f"**Custom:** {len(custom)}", f"**Standard:** {len(standard)}"]
  if custom:
    lines.append("\n".join(f"• `{x['id']}` — **{x['name']}** ({x.get('rarity','Common')} {x.get('category','item')})" for x in custom[-25:]))
  await interaction.response.send_message("### Item Catalog\n" + "\n".join(lines), ephemeral=True)


@ADMIN_ITEM_GROUP.command(name="catalog-remove", description="GM: remove a custom item template from the catalog.")
@app_commands.describe(item_name="Custom catalog item name.")
async def catalog_remove(interaction: discord.Interaction, item_name: str):
  from ..state import is_staff
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  custom = next((x for x in ITEM_CATALOG if x.get("custom_template") and item_key(x.get("name", "")) == item_key(item_name)), None)
  if not custom:
    return await interaction.response.send_message("Custom catalog item not found.", ephemeral=True)
  ITEM_CATALOG.remove(custom)
  ITEM_BY_NAME.pop(custom.get("name", "").casefold(), None)
  item_data["custom_catalog"] = [x for x in item_data.get("custom_catalog", []) if x.get("id") != custom.get("id")]
  save_item_data()
  await interaction.response.send_message(f"Removed **{custom['name']}** from the catalog. Existing owned copies are untouched.", ephemeral=True)


@ADMIN_ITEM_GROUP.command(name="reclaim", description="GM: reclaim an item without destroying the item instance.")
@app_commands.describe(item_name="Item name or item instance ID.")
async def gm_reclaim(interaction: discord.Interaction, item_name: str):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  async with ITEM_ACTION_LOCK:
    state = item_state(interaction.guild.id)
    matches = []
    wanted = item_key(item_name)
    for item_id, record in state.setdefault("possessions", {}).items():
      item = record.get("item", {})
      if str(item_id).casefold() == str(item_name).casefold() or item_key(item.get("name", "")) == wanted:
        matches.append((item_id, record))
    if not matches:
      return await interaction.response.send_message("No owned item instance matched that name or ID.", ephemeral=True)
    if len(matches) > 1 and not str(item_name).casefold().startswith("gm-item-"):
      return await interaction.response.send_message("Multiple instances match that name. Use the item instance ID.", ephemeral=True)
    item_id, record = matches[0]
    owner_id = int(record.get("owner_id", 0))
    remove_inventory_item(interaction.guild.id, owner_id, item_id)
    state["possessions"].pop(item_id, None)
    vault = state.setdefault("gm_vault", [])
    vault.append({"item": record.get("item", {}), "reclaimed_at": _now(), "reclaimed_from": owner_id, "discovery_id": record.get("discovery_id")})
    discovery = _find_discovery(interaction.guild.id, record.get("discovery_id")) if record.get("discovery_id") else None
    if discovery:
      discovery["state"] = "TRANSFERRED"
      discovery["reclaimed_by"] = interaction.user.id
      discovery["reclaimed_at"] = _now()
      await _edit_discovery_message(interaction.guild, discovery, state_name="TRANSFERRED", actor=interaction.user)
    save_item_data()
    return await interaction.response.send_message(f"Reclaimed **{record['item']['name']}** from <@{owner_id}>. Instance `{item_id}` is preserved in the GM vault.", ephemeral=True)


@ADMIN_ITEM_GROUP.command(name="ownership", description="GM: look up item ownership and instance information.")
@app_commands.describe(item_name="Item name or exact item instance ID.")
async def gm_ownership(interaction: discord.Interaction, item_name: str):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  state = item_state(interaction.guild.id)
  wanted = item_key(item_name)
  rows = []
  for item_id, record in state.setdefault("possessions", {}).items():
    item = record.get("item", {})
    if str(item_id).casefold() == str(item_name).casefold() or item_key(item.get("name", "")) == wanted:
      rows.append(f"**{item.get('name', 'Unknown')}**\nOwner: <@{record.get('owner_id')}>\nStatus: **{record.get('state', 'CLAIMED')}**\nInstance: `{item_id}`")
  for vault in state.get("gm_vault", []):
    item = vault.get("item", {})
    if item_key(item.get("name", "")) == wanted or str(item.get("id", "")).casefold() == str(item_name).casefold():
      rows.append(f"**{item.get('name', 'Unknown')}**\nOwner: GM vault\nStatus: **TRANSFERRED**\nInstance: `{item.get('id')}`")
  if not rows:
    return await interaction.response.send_message("No matching item instances found.", ephemeral=True)
  return await interaction.response.send_message("\n\n".join(rows[:10]), ephemeral=True)


@ADMIN_ITEM_GROUP.command(name="spawn-history", description="GM: view recent discovery spawn history.")
async def gm_spawn_history(interaction: discord.Interaction):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  history = item_state(interaction.guild.id).get("gm_tools", {}).get("spawn_history", [])[-15:]
  if not history:
    return await interaction.response.send_message("No spawn history.", ephemeral=True)
  lines = []
  for row in reversed(history):
    lines.append(f"{row.get('item', 'Unknown')} — {row.get('type', 'spawn')} — {row.get('discovery_id', 'unknown')}")
  return await interaction.response.send_message("**Recent Spawn History**\n" + "\n".join(lines), ephemeral=True)


@ADMIN_ITEM_GROUP.command(name="undo-spawn", description="GM: cancel the most recent active spawn or reclaim its item.")
async def gm_undo_spawn(interaction: discord.Interaction):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  async with ITEM_ACTION_LOCK:
    gm = item_state(interaction.guild.id).get("gm_tools", {})
    for key in ("pending_server_claim", "pending_dm_claim"):
      discovery = gm.get(key)
      if discovery and discovery.get("state") in {"UNCLAIMED", "ACTIVE"}:
        discovery["state"] = "CANCELLED"
        await _edit_discovery_message(interaction.guild, discovery, state_name="CANCELLED", actor=interaction.user)
        gm[key] = None
        save_item_data()
        return await interaction.response.send_message(f"Cancelled **{discovery.get('item', {}).get('name', 'Unknown')}**.", ephemeral=True)
    return await interaction.response.send_message("There is no active spawn to undo.", ephemeral=True)


@ADMIN_ITEM_GROUP.command(name="destroy", description="GM: permanently destroy one owned item instance.")
@app_commands.describe(item_name="Item name or exact instance ID.")
async def gm_destroy(interaction: discord.Interaction, item_name: str):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  async with ITEM_ACTION_LOCK:
    state = item_state(interaction.guild.id)
    wanted = item_key(item_name)
    matches = [(iid, r) for iid, r in state.setdefault("possessions", {}).items() if str(iid).casefold() == str(item_name).casefold() or item_key(r.get("item", {}).get("name", "")) == wanted]
    if len(matches) != 1:
      return await interaction.response.send_message("Use the exact instance ID when multiple items share a name." if matches else "No matching owned item instance found.", ephemeral=True)
    item_id, record = matches[0]
    remove_inventory_item(interaction.guild.id, int(record.get("owner_id", 0)), item_id)
    state["possessions"].pop(item_id, None)
    discovery = _find_discovery(interaction.guild.id, record.get("discovery_id")) if record.get("discovery_id") else None
    if discovery:
      discovery["state"] = "DESTROYED"
      await _edit_discovery_message(interaction.guild, discovery, state_name="DESTROYED", actor=interaction.user)
    save_item_data()
    return await interaction.response.send_message(f"Destroyed **{record['item']['name']}** instance `{item_id}`.", ephemeral=True)

COMMANDS=[claim_item,secure_item,secure_alias,unsecure_item,steal,claim_top_level,secure_top_level,unsecure_top_level,steal_top_level,secure_item_alias,unsecure_item_alias,give_item_top_level,give_top_level,item_info,inventory,give_item,rename_item,catalog,catalog_remove]

def register(bot_instance):
  global BOT,bot
  BOT=bot_instance; bot=bot_instance
  bot.tree.add_command(ITEM_GROUP)
  bot.tree.add_command(INVENTORY_GROUP)
  for _cmd in (claim_top_level, secure_top_level, unsecure_top_level, steal_top_level, secure_item_alias, unsecure_item_alias, give_item_top_level, give_top_level):
    try:
      bot.tree.add_command(_cmd)
    except discord.app_commands.CommandAlreadyRegistered:
      pass
  # Loop is started once by bot.on_ready; don't create a second loop here.
