import random
import time
from datetime import datetime, timezone

import discord
from discord import app_commands

from .items import item_state, save_item_data

COMPANION_GROUP = app_commands.Group(name="companion", description="Companion care, growth, memory, and learned words.")
COMPANION_ADMIN_GROUP = app_commands.Group(name="admin", description="GM/admin companion controls.", parent=COMPANION_GROUP)

SPECIES = [
    "Wolf", "Raven", "Fox", "Owl", "Cat", "Hound", "Lizard", "Rabbit", "Sprite", "Voidling",
    "Griffin", "Dragon", "Wyvern", "Phoenix", "Drake", "Hippogriff", "Pegasus", "Dire Wolf", "Frost Wyrm", "Sky Serpent"
]
FLYING_SPECIES = {
    "Raven", "Owl", "Sprite", "Voidling", "Griffin", "Dragon", "Wyvern", "Phoenix",
    "Drake", "Hippogriff", "Pegasus", "Frost Wyrm", "Sky Serpent"
}

# Training remains quick enough to be enjoyable, while growth/evolution is intentionally slower.
TRAIN_COOLDOWN = 5 * 60
MAX_DAILY_TRAINING = 20
COMPANION_DATA_VERSION = 5
NEGLECT_THRESHOLD = 70
_GUILD_CACHE = {}

PERSONAS = {
    "guardian": "Calm, protective, brief, and dependable.",
    "trickster": "Playful, suspicious, and slightly chaotic.",
    "oracle": "Cryptic, observant, and unnervingly certain.",
    "wanderer": "Quiet, curious, and fond of strange observations.",
}

MOODS = ["Happy", "Content", "Excited", "Curious", "Sad", "Lonely", "Afraid", "Angry", "Confused", "Suspicious", "Distressed"]
MOOD_EMOJI = {mood: "" for mood in MOODS}

DAILY_ACTIONS = [
    "tried to fight its own reflection.",
    "stole something and immediately forgot where it put it.",
    "stared at a wall for 17 minutes.",
    "challenged a mailbox to a duel.",
    "found a rock and decided it was their best friend.",
    "fell asleep while standing up.",
    "made a noise that definitely was not natural.",
    "ran away for five minutes and came back holding a stick.",
    "attempted to eat something that was very obviously not food.",
    "judged you silently from across the room.",
]

STAT_NAMES = {
    "health", "max_health", "happiness", "trust", "bond", "neglect", "training", "growth",
    "intelligence", "curiosity", "courage", "loyalty", "independence", "fear", "aggression", "sociability", "age"
}


def _state(guild_id):
    st = item_state(guild_id)
    # Preserve existing companions while migrating older records into the richer state model.
    if st.get("companion_data_version", 0) < COMPANION_DATA_VERSION:
        st["companion_data_version"] = COMPANION_DATA_VERSION
    st.setdefault("companions", {})
    return st["companions"]


def _new_companion():
    now = time.time()
    return {
        "name": "",
        "name_history": [],
        "name_significance": 0,
        "species": "",
        "chosen": False,
        "can_fly": False,
        "form_name": "",
        "evolution_count": 0,
        "evolution_ready": False,
        "health": 100,
        "max_health": 100,
        "happiness": 75,
        "trust": 50,
        "bond": 0,
        "neglect": 0,
        "neglect_alerted": False,
        "training": 0,
        "growth": 0,
        "intelligence": 10,
        "curiosity": 50,
        "courage": 50,
        "loyalty": 50,
        "independence": 50,
        "fear": 0,
        "aggression": 10,
        "sociability": 50,
        "age": 0,
        "mood": "Content",
        "mood_history": [],
        "personality_history": [],
        "learned_words": {},
        "memories": [],
        "relationships": {},
        "persona": "wanderer",
        "last_daily": 0,
        "daily_story": random.choice(DAILY_ACTIONS),
        "rare_behaviors": [],
        "last_rare_behavior": 0,
        "last_training": 0,
        "training_today": 0,
        "training_day": time.strftime("%Y-%m-%d", time.gmtime()),
        "last_state_update": now,
        "last_interaction": now,
        "created_at": now,
    }


def _clamp(v, low=0, high=100):
    try:
        return max(low, min(high, int(v)))
    except (TypeError, ValueError):
        return low


def _companion(guild_id, user_id):
    rows = _state(guild_id)
    uid = str(user_id)
    c = rows.get(uid)
    if not isinstance(c, dict):
        c = _new_companion()
        rows[uid] = c

    defaults = _new_companion()
    for key, value in defaults.items():
        c.setdefault(key, value)
    # Migrate old records safely.
    c["chosen"] = bool(c.get("chosen") or (c.get("species") and c.get("name")))
    c["learned_words"] = c.get("learned_words") if isinstance(c.get("learned_words"), dict) else {}
    c["memories"] = c.get("memories") if isinstance(c.get("memories"), list) else []
    c["mood_history"] = c.get("mood_history") if isinstance(c.get("mood_history"), list) else []
    c["personality_history"] = c.get("personality_history") if isinstance(c.get("personality_history"), list) else []
    c["relationships"] = c.get("relationships") if isinstance(c.get("relationships"), dict) else {}
    c["name_history"] = c.get("name_history") if isinstance(c.get("name_history"), list) else []
    c["rare_behaviors"] = c.get("rare_behaviors") if isinstance(c.get("rare_behaviors"), list) else []
    for stat in STAT_NAMES - {"max_health", "age"}:
        c[stat] = _clamp(c.get(stat, defaults.get(stat, 0)))
    c["max_health"] = max(1, int(c.get("max_health", 100)))
    c["health"] = max(0, min(c["max_health"], int(c.get("health", 100))))
    c["age"] = max(0, int(c.get("age", 0)))
    return c


def _game_checked_in(guild_id, user_id):
    """Neglect only accrues during a live game when this exact player is checked in."""
    st = item_state(guild_id)
    g = st.get("gm_tools", {}) if isinstance(st.get("gm_tools"), dict) else {}
    if not g.get("game_started"):
        return False
    record = g.get("attendance", {}).get(str(user_id), {}) if isinstance(g.get("attendance"), dict) else {}
    return record.get("status") == "checked_in"


def _notify_gms(guild, message):
    """Automatic companion alerts are disabled; companion state is reviewed in /gm."""
    return None

def _record_memory(c, text, category="event", source_user_id=None):
    c.setdefault("memories", []).append({
        "text": text[:500],
        "category": category,
        "at": datetime.now(timezone.utc).isoformat(),
        "source_user_id": source_user_id,
    })
    c["memories"] = c["memories"][-100:]


def _daily(c):
    now = time.time()
    if now - float(c.get("last_daily", 0) or 0) >= 86400:
        c["daily_story"] = random.choice(DAILY_ACTIONS)
        c["last_daily"] = now
        return True
    return False


def _update_state(guild_id, user_id, c):
    # Cache the Guild object when callers provide it through _GUILD_CACHE.
    """Apply elapsed-time changes. Neglect is deliberately gated behind active check-in."""
    now = time.time()
    last = float(c.get("last_state_update", now) or now)
    elapsed = max(0, now - last)
    if elapsed < 60:
        return False
    hours = elapsed / 3600
    changed = False

    checked_in = _game_checked_in(guild_id, user_id)
    if checked_in:
        # During an active session, an unattended companion can become unhappy/neglected.
        neglect_gain = min(8, hours * 1.5)
        if neglect_gain >= 0.1:
            c["neglect"] = _clamp(c.get("neglect", 0) + neglect_gain)
            if c["neglect"] >= NEGLECT_THRESHOLD:
                c["happiness"] = _clamp(c.get("happiness", 75) - min(5, hours * 0.8))
                c["bond"] = _clamp(c.get("bond", 0) - min(2, hours * 0.2))
                if c["neglect"] >= 90:
                    c["mood"] = "Distressed"
                else:
                    c["mood"] = "Lonely"
                if not c.get("neglect_alerted"):
                    c["neglect_alerted"] = True
                    # The DM includes the current mood so the GM can choose an appropriate story response.
                    # This is intentionally only possible while the player is checked into a live session.
                    guild = _GUILD_CACHE.get(str(guild_id))
                    if guild is not None:
                        _notify_gms(guild, (
                            f"COMPANION NEGLECT ALERT\nPlayer ID: {user_id}\nCompanion: {c.get('name') or 'Unnamed'}\n"
                            f"Mood: {c.get('mood', 'Content')}\n"
                            f"Happiness: {int(c.get('happiness', 0))}/100\nTrust: {int(c.get('trust', 0))}/100\n"
                            f"Bond: {int(c.get('bond', 0))}/100\nNeglect: {int(c.get('neglect', 0))}/100\n"
                            "The player is currently checked into a live game. The companion may now be suitable for a GM-directed reaction."
                        ))
            changed = True
        # Being offline/away from the game does not count as neglect. Some stats can still recover.
        c["neglect"] = _clamp(c.get("neglect", 0) - min(5, hours * 0.75))
        if c["neglect"] < 50:
            c["neglect_alerted"] = False
        c["fear"] = _clamp(c.get("fear", 0) - min(4, hours * 0.4))
        c["health"] = min(c["max_health"], int(c.get("health", 100)) + int(hours // 2))
        changed = True
    else:
        # Being offline/away from the game does not count as neglect. Some stats can still recover.
        c["neglect"] = _clamp(c.get("neglect", 0) - min(5, hours * 0.75))
        if c["neglect"] < 50:
            c["neglect_alerted"] = False
        c["fear"] = _clamp(c.get("fear", 0) - min(4, hours * 0.4))
        c["health"] = min(c["max_health"], int(c.get("health", 100)) + int(hours // 2))
        changed = True

    # Companions age with real elapsed time, but never die from age.
    age_gain = hours / 24.0
    if age_gain > 0:
        c["age"] = max(0.0, float(c.get("age", 0) or 0) + age_gain)
        changed = True

    # Rare behavior is deliberately uncommon and remembered. It is not a daily guaranteed event.
    if c.get("chosen") and now - float(c.get("last_rare_behavior", 0) or 0) >= 86400 and random.random() < 0.035:
        rare = random.choice([
            f"{c.get('name') or 'The companion'} remembered a moment you had forgotten.",
            f"{c.get('name') or 'The companion'} stared at someone as if recognizing them from somewhere else.",
            f"{c.get('name') or 'The companion'} repeated a word nobody had taught them.",
            f"{c.get('name') or 'The companion'} quietly left an object somewhere important.",
            f"{c.get('name') or 'The companion'} reacted strongly to a place with no obvious reason.",
            f"{c.get('name') or 'The companion'} used your name without being addressed first.",
        ])
        c.setdefault("rare_behaviors", []).append({"text": rare, "at": datetime.now(timezone.utc).isoformat()})
        c["rare_behaviors"] = c["rare_behaviors"][-25:]
        c["last_rare_behavior"] = now
        _record_memory(c, rare, "rare_behavior")
        changed = True

    # Mood follows current state; mood is volatile, personality is not.
    old_mood = c.get("mood", "Content")
    if c["neglect"] >= 90:
        new_mood = "Distressed"
    elif c["neglect"] >= 70:
        new_mood = "Lonely"
    elif c["fear"] >= 70:
        new_mood = "Afraid"
    elif c["happiness"] >= 85:
        new_mood = "Happy"
    elif c["happiness"] <= 25:
        new_mood = "Sad"
    elif c["curiosity"] >= 80:
        new_mood = "Curious"
    else:
        new_mood = "Content"
    if new_mood != old_mood:
        c["mood"] = new_mood
        c.setdefault("mood_history", []).append({"from": old_mood, "to": new_mood, "at": datetime.now(timezone.utc).isoformat()})
        c["mood_history"] = c["mood_history"][-50:]
        changed = True

    # Growth is accumulated over time from meaningful training/interactions, never by a clock alone.
    if c["growth"] >= 100 and not c.get("evolution_ready"):
        c["evolution_ready"] = True
        changed = True
    c["last_state_update"] = now
    return changed


def _bar(value, maximum=100, length=12):
    value = max(0, min(maximum, int(value)))
    filled = round((value / maximum) * length)
    return "█" * filled + "░" * (length - filled)


def _derived_personality(c):
    traits = {
        "Loyal": c.get("loyalty", 0),
        "Curious": c.get("curiosity", 0),
        "Independent": c.get("independence", 0),
        "Courageous": c.get("courage", 0),
        "Fearful": c.get("fear", 0),
        "Aggressive": c.get("aggression", 0),
        "Social": c.get("sociability", 0),
        "Trusting": c.get("trust", 0),
    }
    return max(traits, key=traits.get)


class CompanionNameModal(discord.ui.Modal, title="Name Your Companion"):
    name = discord.ui.TextInput(label="Companion name", placeholder="Give them a name...", max_length=40, required=True)
    def __init__(self, view):
        super().__init__()
        self.parent_view = view
    async def on_submit(self, interaction: discord.Interaction):
        if not await self.parent_view._guard(interaction): return
        self.parent_view.name = self.name.value.strip()[:40]
        await interaction.response.edit_message(content=self.parent_view.content(), view=self.parent_view)


class CompanionSetupView(discord.ui.View):
    def __init__(self, guild_id, user_id):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.user_id = user_id
        self.species = ""
        self.name = ""
        self.add_item(CompanionSpeciesSelect(self))

    async def _guard(self, interaction):
        if interaction.user.id != self.user_id or interaction.guild is None or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("This setup belongs to someone else.", ephemeral=True)
            return False
        return True

    def content(self):
        return (
            "## Choose Your Companion\n\n"
            "Choose a species and name. Evolution does **not** automatically rename your companion.\n\n"
            f"**Species:** {self.species or 'Not chosen'}\n"
            f"**Name:** {self.name or 'Not chosen'}\n\n"
            "Training is quick enough to use regularly. Learned words are roleplay triggers only."
        )

    @discord.ui.button(label="Choose Name", style=discord.ButtonStyle.secondary, row=1)
    async def choose_name(self, interaction, button):
        if await self._guard(interaction):
            await interaction.response.send_modal(CompanionNameModal(self))

    @discord.ui.button(label="Confirm Companion", style=discord.ButtonStyle.success, row=1)
    async def confirm(self, interaction, button):
        if not await self._guard(interaction): return
        if not self.species or not self.name:
            return await interaction.response.send_message("Choose a species and name first.", ephemeral=True)
        c = _companion(self.guild_id, self.user_id)
        c.update({"name": self.name, "species": self.species, "chosen": True, "can_fly": self.species in FLYING_SPECIES})
        _record_memory(c, f"Companion created as {self.name} ({self.species}).", "origin", self.user_id)
        save_item_data()
        await interaction.response.edit_message(content=companion_text(c), view=CompanionView(self.guild_id, self.user_id))


class CompanionSpeciesSelect(discord.ui.Select):
    def __init__(self, view):
        self.parent_view = view
        super().__init__(placeholder="Choose a species...", options=[discord.SelectOption(label=x, value=x) for x in SPECIES], row=0)
    async def callback(self, interaction):
        if not await self.parent_view._guard(interaction): return
        self.parent_view.species = self.values[0]
        await interaction.response.edit_message(content=self.parent_view.content(), view=self.parent_view)


class CompanionView(discord.ui.View):
    def __init__(self, guild_id, user_id):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.user_id = user_id

    async def _guard(self, interaction):
        if interaction.user.id != self.user_id or interaction.guild is None or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("This companion hub belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Train", style=discord.ButtonStyle.primary)
    async def train(self, interaction, button):
        if not await self._guard(interaction): return
        c = _companion(self.guild_id, self.user_id)
        _GUILD_CACHE[str(self.guild_id)] = interaction.guild
        _update_state(self.guild_id, self.user_id, c)
        if not c.get("chosen"):
            return await interaction.response.send_message("Choose your companion's name and species first.", ephemeral=True)
        now = time.time(); day = time.strftime("%Y-%m-%d", time.gmtime())
        if c.get("training_day") != day:
            c["training_day"] = day; c["training_today"] = 0
        remaining = TRAIN_COOLDOWN - (now - float(c.get("last_training", 0) or 0))
        if remaining > 0:
            minutes = int(remaining // 60); seconds = int(remaining % 60)
            return await interaction.response.send_message(f"Training is on cooldown. Try again in **{minutes}m {seconds}s**.", ephemeral=True)
        if int(c.get("training_today", 0)) >= MAX_DAILY_TRAINING:
            return await interaction.response.send_message("Your companion has reached today's training limit. Come back tomorrow.", ephemeral=True)
        gain = 3
        c["training"] = _clamp(c["training"] + gain)
        c["growth"] = _clamp(c["growth"] + 2)
        c["intelligence"] = _clamp(c["intelligence"] + 1)
        c["curiosity"] = _clamp(c["curiosity"] + (1 if random.random() < .4 else 0))
        c["bond"] = _clamp(c["bond"] + 2)
        c["happiness"] = _clamp(c["happiness"] + 2)
        c["neglect"] = _clamp(c["neglect"] - 2)
        c["last_training"] = now; c["training_today"] = int(c.get("training_today", 0)) + 1; c["last_interaction"] = now
        _record_memory(c, f"Training session completed. Training +{gain}, growth +2.", "training", self.user_id)
        save_item_data()
        if c["growth"] >= 100 and not c.get("evolution_ready"):
            c["evolution_ready"] = True
            _notify_gms(interaction.guild, f"COMPANION READY TO EVOLVE\nPlayer: {interaction.user.mention}\nCompanion: {c['name']}\nCurrent species/form: {c['species']}\nGrowth: {c['growth']}/100\nNo evolution name was assigned automatically; the GM can choose the next form/name.")
        await interaction.response.edit_message(content=companion_text(c, trained=gain), view=CompanionView(self.guild_id, self.user_id))

    @discord.ui.button(label="Feed", style=discord.ButtonStyle.success)
    async def feed(self, interaction, button):
        if not await self._guard(interaction): return
        c = _companion(self.guild_id, self.user_id); _update_state(self.guild_id, self.user_id, c)
        heal = random.randint(8, 18)
        c["health"] = min(c["max_health"], int(c["health"]) + heal); c["bond"] = _clamp(c["bond"] + 2); c["happiness"] = _clamp(c["happiness"] + 5); c["neglect"] = _clamp(c["neglect"] - 4); c["last_interaction"] = time.time()
        _record_memory(c, f"Owner fed the companion and restored {heal} health.", "care", self.user_id); save_item_data()
        await interaction.response.edit_message(content=companion_text(c, action=f"{c['name']} recovered **{heal} HP**."), view=CompanionView(self.guild_id, self.user_id))

    @discord.ui.button(label="Play", style=discord.ButtonStyle.secondary)
    async def play(self, interaction, button):
        if not await self._guard(interaction): return
        c = _companion(self.guild_id, self.user_id); _update_state(self.guild_id, self.user_id, c)
        c["bond"] = _clamp(c["bond"] + 3); c["happiness"] = _clamp(c["happiness"] + 7); c["sociability"] = _clamp(c["sociability"] + 1); c["neglect"] = _clamp(c["neglect"] - 5); c["last_interaction"] = time.time()
        _record_memory(c, "Owner played with the companion.", "care", self.user_id); save_item_data()
        await interaction.response.edit_message(content=companion_text(c, action=random.choice([f"{c['name']} happily played with you.", f"{c['name']} got distracted by something shiny.", f"{c['name']} refused to play normally and invented a game."])), view=CompanionView(self.guild_id, self.user_id))

    @discord.ui.button(label="Rest", style=discord.ButtonStyle.secondary, row=1)
    async def rest(self, interaction, button):
        if not await self._guard(interaction): return
        c = _companion(self.guild_id, self.user_id); _update_state(self.guild_id, self.user_id, c)
        heal = random.randint(4, 12); c["health"] = min(c["max_health"], int(c["health"]) + heal); c["fear"] = _clamp(c["fear"] - 4); c["last_interaction"] = time.time(); save_item_data()
        await interaction.response.edit_message(content=companion_text(c, action=f"{c['name']} rested. **+{heal} HP**."), view=CompanionView(self.guild_id, self.user_id))

    @discord.ui.button(label="Main", style=discord.ButtonStyle.primary, row=2)
    async def main_menu(self, interaction, button):
        if not await self._guard(interaction): return
        from .main_ui import MainView
        view = MainView(self.guild_id, self.user_id, "home")
        await interaction.response.edit_message(content=None, embed=view.home_embed(), view=view)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, row=1)
    async def refresh(self, interaction, button):
        if not await self._guard(interaction): return
        c = _companion(self.guild_id, self.user_id); _update_state(self.guild_id, self.user_id, c); _daily(c); save_item_data()
        await interaction.response.edit_message(content=companion_text(c), view=CompanionView(self.guild_id, self.user_id))


def companion_text(c, action=None, trained=None):
    personality = _derived_personality(c)
    lines = [
        f"## {c['name'] or 'Unnamed'} — {c['species'] or 'Unchosen'}",
        f"**Form:** {c.get('form_name') or c.get('species') or 'Unchosen'}  •  **Age:** {int(c.get('age', 0))}",
        f"**Mood:** **{c.get('mood', 'Content')}**",
        f"**Personality:** **{personality}**  •  Persona: **{str(c.get('persona', 'wanderer')).title()}**",
        f"**Health:** `{_bar(c['health'], c['max_health'])}` **{int(c['health'])}/{int(c['max_health'])}**",
        f"**Happiness:** `{_bar(c['happiness'])}` **{int(c['happiness'])}/100**",
        f"**Trust:** **{int(c['trust'])}/100**  •  **Bond:** **{int(c['bond'])}/100**",
        f"**Neglect:** **{int(c['neglect'])}/100**  •  **Fear:** **{int(c['fear'])}/100**",
        f"**Training:** **{int(c['training'])}/100**  •  **Growth:** **{int(c['growth'])}/100**",
        f"**Intelligence:** {int(c['intelligence'])}  •  **Curiosity:** {int(c['curiosity'])}  •  **Loyalty:** {int(c['loyalty'])}",
        f"**Learned words:** **{len(c.get('learned_words', {}))}**  •  **Memories:** **{len(c.get('memories', []))}**",
        "",
        f"**Today's dumb thing:** {c.get('daily_story', 'Nothing happened. Suspicious.')}",
    ]
    if c.get("evolution_ready"):
        lines += ["", "**Growth milestone reached.** This companion is ready for a GM-directed evolution/form change."]
    if trained is not None: lines.append(f"\n**Training:** +{trained} progress.")
    if action: lines.append(f"\n**Companion:** {action}")
    return "\n".join(lines)


@COMPANION_GROUP.command(name="hub", description="Open your companion hub.")
async def companion_hub(interaction: discord.Interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("Server only.", ephemeral=True)
    c = _companion(interaction.guild.id, interaction.user.id); _GUILD_CACHE[str(interaction.guild.id)] = interaction.guild; _update_state(interaction.guild.id, interaction.user.id, c)
    if not c.get("chosen"):
        setup = CompanionSetupView(interaction.guild.id, interaction.user.id)
        return await interaction.response.send_message(setup.content(), view=setup, ephemeral=True)
    _daily(c); save_item_data()
    await interaction.response.send_message(companion_text(c), view=CompanionView(interaction.guild.id, interaction.user.id), ephemeral=True)


@COMPANION_GROUP.command(name="choose", description="Choose the species for your companion.")
@app_commands.describe(species="The animal or fantasy creature you want.")
@app_commands.choices(species=[app_commands.Choice(name=x, value=x) for x in SPECIES])
async def companion_choose(interaction: discord.Interaction, species: app_commands.Choice[str]):
    if interaction.guild is None: return await interaction.response.send_message("Server only.", ephemeral=True)
    c = _companion(interaction.guild.id, interaction.user.id); chosen = species.value
    c["species"] = chosen; c["can_fly"] = chosen in FLYING_SPECIES; c["chosen"] = bool(c.get("name")); _record_memory(c, f"Species selected: {chosen}.", "origin", interaction.user.id); save_item_data()
    await interaction.response.send_message(f"Your companion species is now **{chosen}**. Use `/companion name` to name them.", ephemeral=True)


@COMPANION_GROUP.command(name="name", description="Change your companion's name.")
@app_commands.describe(name="Your companion's new name.")
async def companion_name(interaction: discord.Interaction, name: str):
    if interaction.guild is None: return await interaction.response.send_message("Server only.", ephemeral=True)
    name = name.strip()[:40]
    if not name: return await interaction.response.send_message("Give your companion a name.", ephemeral=True)
    c = _companion(interaction.guild.id, interaction.user.id); old = c.get("name") or "Unnamed"; c["name"] = name; c["chosen"] = bool(c.get("species")); c.setdefault("name_history", []).append({"old": old, "new": name, "at": datetime.now(timezone.utc).isoformat()}); c["name_significance"] = _clamp(c.get("name_significance", 0) + 10); _record_memory(c, f"Companion renamed from {old} to {name}.", "identity", interaction.user.id); save_item_data()
    await interaction.response.send_message(f"Your companion is now named **{name}**.", ephemeral=True)


@COMPANION_GROUP.command(name="status", description="View your companion's current stats and recent memories.")
async def companion_status(interaction: discord.Interaction):
    if interaction.guild is None: return await interaction.response.send_message("Server only.", ephemeral=True)
    c = _companion(interaction.guild.id, interaction.user.id); _GUILD_CACHE[str(interaction.guild.id)] = interaction.guild; _update_state(interaction.guild.id, interaction.user.id, c); save_item_data()
    recent = c.get("memories", [])[-3:]
    extra = "\n\n**Recent memories:**\n" + "\n".join(f"• {x.get('text','')}" for x in recent) if recent else ""
    await interaction.response.send_message(companion_text(c) + extra, ephemeral=True)


@COMPANION_GROUP.command(name="teach", description="Teach your companion a word and its intended meaning.")
@app_commands.describe(word="The word or phrase to teach.", meaning="What you intend the word to mean.")
async def companion_teach(interaction: discord.Interaction, word: str, meaning: str):
    if interaction.guild is None:
        return await interaction.response.send_message("Server only.", ephemeral=True)
    word = " ".join(word.strip().split())[:80]
    meaning = meaning.strip()[:500]
    if not word or not meaning:
        return await interaction.response.send_message("Word and meaning are required.", ephemeral=True)
    c = _companion(interaction.guild.id, interaction.user.id)
    if not c.get("chosen"):
        return await interaction.response.send_message("Choose your companion before teaching it words.", ephemeral=True)
    c.setdefault("learned_words", {})[word.casefold()] = {
        "display": word, "meaning": meaning, "misunderstood_as": "", "mastery": 0,
        "taught_by": interaction.user.id, "taught_at": datetime.now(timezone.utc).isoformat()
    }
    c["intelligence"] = _clamp(c.get("intelligence", 0) + 1)
    c["bond"] = _clamp(c.get("bond", 0) + 2)
    c["happiness"] = _clamp(c.get("happiness", 0) + 1)
    c["last_interaction"] = time.time()
    _record_memory(c, f"Owner taught the word {word}: intended meaning: {meaning}.", "language", interaction.user.id)
    save_item_data()
    await interaction.response.send_message(
        f"You taught **{c['name']}** the word **{word}**.\n\nIntended meaning: **{meaning}**\n\n"
        "The teaching is saved in companion memory. A GM can review it from **/gm** and add a misunderstanding or story consequence.", ephemeral=True
    )


@COMPANION_ADMIN_GROUP.command(name="stat", description="GM: change one companion stat or identity value.")
@app_commands.describe(user="Player whose companion to edit.", stat="Stat/field to change.", value="New value. Use a number for numeric stats.")
async def admin_companion_stat(interaction: discord.Interaction, user: discord.Member, stat: str, value: str):
    if not _is_admin(interaction): return await interaction.response.send_message("Admin access required.", ephemeral=True)
    key = stat.strip().casefold().replace(" ", "_")
    aliases = {"hp": "health", "maxhp": "max_health", "mood": "mood", "form": "form_name", "species": "species", "name": "name"}
    key = aliases.get(key, key)
    c = _companion(interaction.guild.id, user.id)
    if key == "mood":
        match = next((m for m in MOODS if m.casefold() == value.strip().casefold()), None)
        if not match: return await interaction.response.send_message(f"Mood must be one of: {', '.join(MOODS)}", ephemeral=True)
        old = c.get("mood"); c["mood"] = match; c.setdefault("mood_history", []).append({"from": old, "to": match, "at": datetime.now(timezone.utc).isoformat()})
    elif key in {"name", "species", "form_name"}:
        text = value.strip()[:40]
        if key == "species":
            match = next((x for x in SPECIES if x.casefold() == text.casefold()), None)
            if not match: return await interaction.response.send_message("Unknown species.", ephemeral=True)
            c[key] = match; c["can_fly"] = match in FLYING_SPECIES
        else: c[key] = text
    elif key in STAT_NAMES:
        try: number = int(float(value))
        except ValueError: return await interaction.response.send_message("This stat requires a number.", ephemeral=True)
        if key == "max_health": c[key] = max(1, number); c["health"] = min(c["health"], c[key])
        elif key == "age": c[key] = max(0, number)
        else: c[key] = _clamp(number)
    else:
        valid = ", ".join(sorted(STAT_NAMES | {"mood", "name", "species", "form_name"}))
        return await interaction.response.send_message(f"Unknown stat. Available: {valid}", ephemeral=True)
    _record_memory(c, f"GM changed {key} to {value}.", "gm_change", interaction.user.id); save_item_data()
    await interaction.response.send_message(f"Updated {user.mention}'s companion.\n\n{companion_text(c)}", ephemeral=True)


@COMPANION_ADMIN_GROUP.command(name="inspect", description="GM: inspect a companion's full state and memories.")
async def admin_companion_inspect(interaction: discord.Interaction, user: discord.Member):
    if not _is_admin(interaction): return await interaction.response.send_message("Admin access required.", ephemeral=True)
    c = _companion(interaction.guild.id, user.id); _update_state(interaction.guild.id, user.id, c)
    recent = c.get("memories", [])[-10:]
    memories = "\n".join(f"• {x.get('at','')[:19]} — {x.get('text','')}" for x in recent) or "No memories."
    await interaction.response.send_message(f"**Companion for {user.display_name}**\n\n{companion_text(c)}\n\n**Recent memory**\n{memories}", ephemeral=True)


@COMPANION_ADMIN_GROUP.command(name="teach", description="GM: review or correct a taught word and optionally add a misunderstanding.")
@app_commands.describe(user="Player whose companion learns the word.", word="Exact word or phrase.", meaning="What the word is intended to mean.", misunderstood_as="Optional wrong interpretation the companion may use at first.")
async def admin_companion_teach(interaction: discord.Interaction, user: discord.Member, word: str, meaning: str, misunderstood_as: str | None = None):
    if not _is_admin(interaction): return await interaction.response.send_message("Admin access required.", ephemeral=True)
    word = " ".join(word.strip().split())[:80]; meaning = meaning.strip()[:500]; misunderstood_as = (misunderstood_as or "").strip()[:500]
    if not word or not meaning: return await interaction.response.send_message("Word and meaning are required.", ephemeral=True)
    c = _companion(interaction.guild.id, user.id); c.setdefault("learned_words", {})[word.casefold()] = {"display": word, "meaning": meaning, "misunderstood_as": misunderstood_as, "mastery": 0}
    _record_memory(c, f"Learned word: {word} — intended meaning: {meaning}.", "language", interaction.user.id); save_item_data()
    await interaction.response.send_message(f"Updated {user.mention}'s companion word **{word}**. Misunderstanding: **{misunderstood_as or 'none'}**", ephemeral=True)


@COMPANION_ADMIN_GROUP.command(name="evolve", description="GM: apply a growth evolution/form change without inventing its name.")
@app_commands.describe(user="Player whose companion evolves.", new_species="Optional new species.", new_form_name="Optional new form/name chosen by the GM.")
async def admin_companion_evolve(interaction: discord.Interaction, user: discord.Member, new_species: str | None = None, new_form_name: str | None = None):
    if not _is_admin(interaction): return await interaction.response.send_message("Admin access required.", ephemeral=True)
    c = _companion(interaction.guild.id, user.id)
    if new_species:
        match = next((x for x in SPECIES if x.casefold() == new_species.strip().casefold()), None)
        if not match: return await interaction.response.send_message("Unknown species.", ephemeral=True)
        c["species"] = match; c["can_fly"] = match in FLYING_SPECIES
    if new_form_name is not None: c["form_name"] = new_form_name.strip()[:40]
    c["evolution_count"] = int(c.get("evolution_count", 0)) + 1; c["evolution_ready"] = False; c["growth"] = 0; c["training"] = min(100, c["training"] + 10)
    _record_memory(c, f"GM-directed evolution/form change. New species: {c['species']}. Form name: {c.get('form_name') or 'unchanged'}.", "evolution", interaction.user.id); save_item_data()
    await interaction.response.send_message(f"Evolution applied to {user.mention}. No automatic evolution name was generated.\n\n{companion_text(c)}", ephemeral=True)


@COMPANION_ADMIN_GROUP.command(name="kill", description="GM: permanently mark a companion as dead for lore purposes.")
async def admin_companion_kill(interaction: discord.Interaction, user: discord.Member):
    if not _is_admin(interaction): return await interaction.response.send_message("Admin access required.", ephemeral=True)
    c = _companion(interaction.guild.id, user.id); c["dead"] = True; c["health"] = 0; _record_memory(c, "Companion was marked dead by a GM for lore purposes.", "death", interaction.user.id); save_item_data()
    await interaction.response.send_message(f"{user.mention}'s companion **{c['name']}** is now marked dead in its lore record.", ephemeral=True)


@COMPANION_ADMIN_GROUP.command(name="revive", description="GM: revive a companion and preserve its death memory.")
async def admin_companion_revive(interaction: discord.Interaction, user: discord.Member):
    if not _is_admin(interaction): return await interaction.response.send_message("Admin access required.", ephemeral=True)
    c = _companion(interaction.guild.id, user.id); c["dead"] = False; c["health"] = max(1, min(c["max_health"], 50)); _record_memory(c, "Companion was revived by a GM; its prior death remains in memory.", "revival", interaction.user.id); save_item_data()
    await interaction.response.send_message(f"Revived {user.mention}'s companion. The prior death remains part of its history.", ephemeral=True)


@COMPANION_ADMIN_GROUP.command(name="reset", description="GM: reset a player's companion data.")
async def admin_companion_reset(interaction: discord.Interaction, user: discord.Member):
    if not _is_admin(interaction): return await interaction.response.send_message("Admin access required.", ephemeral=True)
    rows = _state(interaction.guild.id); rows.pop(str(user.id), None); save_item_data()
    await interaction.response.send_message(f"Reset {user.mention}'s companion. A new companion will be created next time they open the hub.", ephemeral=True)


def _is_admin(interaction):
    return interaction.guild is not None and (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild)


async def handle_message(message):
    """Process companion-related learning silently. Companions never speak or auto-send messages."""
    if message.guild is None or message.author.bot:
        return
    c = _companion(message.guild.id, message.author.id)
    _GUILD_CACHE[str(message.guild.id)] = message.guild
    if not c.get("chosen") or c.get("dead"):
        return
    changed = _update_state(message.guild.id, message.author.id, c)
    content = " ".join((message.content or "").strip().casefold().split())
    current_name = str(c.get("name") or "").casefold()
    if current_name and current_name in content:
        c["name_significance"] = _clamp(c.get("name_significance", 0) + 1)
        c["bond"] = _clamp(c.get("bond", 0) + 1)
        c["last_interaction"] = time.time()
        changed = True
    learned = c.get("learned_words", {}).get(content)
    if learned:
        learned["mastery"] = _clamp(learned.get("mastery", 0) + 10)
        c["happiness"] = _clamp(c["happiness"] + 1)
        c["bond"] = _clamp(c["bond"] + 1)
        c["last_interaction"] = time.time()
        wrong = learned.get("misunderstood_as", "")
        if wrong and learned["mastery"] < 70 and random.random() < 0.55:
            _record_memory(c, f"The companion encountered the taught word {learned.get('display', content)} and retained a possible misunderstanding: {wrong}", "language", message.author.id)
        else:
            _record_memory(c, f"The companion practiced the taught word {learned.get('display', content)}.", "language", message.author.id)
        changed = True
    if changed:
        save_item_data()


def register(bot):
    bot.tree.add_command(COMPANION_GROUP)
