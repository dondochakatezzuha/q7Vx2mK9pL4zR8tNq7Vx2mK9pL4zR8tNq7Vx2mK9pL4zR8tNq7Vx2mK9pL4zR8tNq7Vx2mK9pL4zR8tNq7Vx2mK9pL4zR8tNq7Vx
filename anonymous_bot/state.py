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

from .config import GM_USER_IDS
from discord import app_commands

conversations = {}
anonymous_messages = {}

# Persistent per-user anonymous aliases: guild_id -> user_id -> alias
anonymous_aliases = {}


# ============================================================
# ANONYMOUS NAMES
# ============================================================

ALIASES = [
  "The Void",
  "The Hollow",
  "The Nameless",
  "The Forgotten",
  "The Stranger",
  "The Wanderer",
  "The Watcher",
  "The Shadow",
  "The Phantom",
  "The Wraith",
  "The Specter",
  "The Reaper",
  "The Raven",
  "The Crow",
  "The Black Wolf",
  "The Pale Man",
  "The Lost One",
  "The Faceless",
  "The Unknown",
  "The Silent One",
  "The Dead Eye",
  "The Dark One",
  "The Drowned",
  "The Forsaken",
  "The Exile",
  "The Outcast",
  "The Nameless One",
  "The Empty",
  "The Last One",
  "The Unseen",
  "The Sleeper",
  "The Lurker",
  "The Vagrant",
  "The Heretic",
  "The Ashen",
  "The Hollowed",
  "The Veiled",
  "The Cursed",
  "The Banished",
  "The Black Hand",
  "The Red Eye",
  "The Pale Shadow",
  "The Dark Walker",
  "The Silent Walker",
  "The Nightborn",
  "The Graveborn",
  "The Voidborn",
  "The Duskborn",
  "The Bloodless",
  "The Cold One",
  "The Unmarked",
  "The Unspoken",
  "The Unremembered",
  "The Last Whisper",
  "The Black Raven",
  "The Dead Raven",
  "The Hollow Wolf",
  "The Midnight Man",
  "The Faceless One",
  "The Nameless Shadow",
  "The Forgotten Shadow",
  "The Voice",
  "The Whisper",
  "The Black Whisper",
  "The Unknown Man",
  "The Unknown One",
  "The Empty Man",
  "The Pale Stranger",
  "The Dark Stranger",
  "The Silent Stranger",
  "The Void Walker",
  "The Shadow Walker",
  "The Night Walker",
  "The Grave Walker",
  "The Forgotten One"
]


def create_alias():
  return random.choice(ALIASES)


def clean_custom_alias(alias):
  """Validate and clean a user-provided anonymous alias."""
  if not alias:
    return None

  alias = alias.strip()

  if not alias:
    return None

  # Keep aliases Discord-friendly and prevent very long display names.
  alias = alias[:80]

  # Remove Discord mentions so a custom alias cannot ping users/roles.
  alias = discord.utils.escape_mentions(alias)

  return alias


def get_alias(custom_alias=None):
  """Anonymous names are always explicitly chosen by the sender."""
  return clean_custom_alias(custom_alias)


# ============================================================
# USERNAME-BASED ANONYMOUS ALIASES
# ============================================================
def set_anonymous_alias(guild_id, user_id, alias):
  anonymous_aliases.setdefault(str(guild_id), {})[str(user_id)] = clean_custom_alias(alias)

def clear_anonymous_alias(guild_id, user_id):
  anonymous_aliases.setdefault(str(guild_id), {}).pop(str(user_id), None)

def get_user_anonymous_alias(guild_id, user_id):
  return anonymous_aliases.get(str(guild_id), {}).get(str(user_id))


# ============================================================
# RANDOM PROMPT CATEGORIES
# ============================================================


# Runtime state shared by the anonymous feature modules.
conversations = {}
anonymous_messages = {}


def is_staff(interaction: discord.Interaction):
  """GM/admin access, including every configured campaign GM."""
  if interaction.guild is None:
    return False
  user = getattr(interaction, "user", None)
  if user is None:
    return False
  if str(getattr(user, "id", "")) in {str(uid) for uid in GM_USER_IDS}:
    return True
  perms = getattr(user, "guild_permissions", None)
  if perms is None:
    return False
  return bool(
    getattr(perms, "manage_guild", False)
    or getattr(perms, "manage_channels", False)
    or getattr(perms, "administrator", False)
  )
