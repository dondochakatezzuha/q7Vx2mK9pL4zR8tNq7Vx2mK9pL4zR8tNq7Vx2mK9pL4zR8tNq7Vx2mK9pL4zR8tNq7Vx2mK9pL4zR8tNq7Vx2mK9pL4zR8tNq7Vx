import asyncio
import random
import time
import discord
from discord import app_commands

from ..state import is_staff
from .items import item_state, save_item_data
from .groups import ADMIN_GLITCH_GROUP

HELL_GROUP = app_commands.Group(name="hell", description="Hellish Area events and anomalies.")
WORLD_NAME = "hell"
# Internal Discord storage name only. Players are told about the Area, not its Discord channel.
GLITCH_HEADERS = ["[REDACTED]", "ERROR://SIGNAL", "NULL_REFERENCE", "ACCESS_DENIED", "// UNKNOWN PROCESS"]
HELL_WHISPERS = [
    "The air tastes of ash. A distant scream rises, then abruptly stops.",
    "The ground trembles beneath your feet as something enormous passes below.",
    "A door opens by itself. Beyond it is only darkness and the sound of breathing.",
    "The screams around you merge into one endless sound. There is nowhere quiet to stand.",
    "A shadow crosses the floor against the direction of the light.",
    "Something is walking through the smoke. It knows exactly where everyone is.",
    "A figure runs through the darkness. Something follows closely behind.",
    "The footsteps stop. A voice whispers a name from directly behind you.",
    "The walls seem to breathe. The sound grows louder when someone tries to leave.",
    "A distant bell rings once. Every creature in the darkness becomes silent.",
    "The sky opens like an enormous wound of light, revealing nothing beyond it.",
    "A river of black fire moves uphill, ignoring every law the living world knows.",
    "Something enormous screams beneath the ground. The Area answers with silence.",
    "A crowd of shadows gathers at the edge of sight. None of them have faces.",
    "The temperature drops suddenly. Something has entered the Area with you.",
    "A voice begs for help from inside the walls. It sounds exactly like someone you know.",
    "The darkness briefly takes the shape of a doorway. Something on the other side knocks.",
    "A distant figure is dragged beyond the horizon. Their voice keeps calling long after they disappear.",
    "The floor becomes warm beneath your feet. Something below is awake.",
    "A creature watches from far away. Every time you look again, it is closer.",
]

HELL_ENVIRONMENTS = [
    "Ash falls like snow across an endless black plain.",
    "A horizon of burning towers stretches farther than sight can reach.",
    "Black rain falls upward into a sky with no stars.",
    "Rivers of molten light divide the landscape into impossible paths.",
    "The ground is covered in footprints that appear before anyone walks there.",
    "A canyon below is filled with millions of overlapping voices.",
    "The clouds hang impossibly low, glowing from something burning above them.",
    "A field of ruined doors stretches in every direction. Every door leads somewhere different.",
    "The Area seems to have no edge. Every road eventually returns to the same place.",
    "A colossal structure disappears into darkness above and below at once.",
]

HELL_CREATURES = [
    "a horned creature with too many joints",
    "a tall figure wrapped in black smoke",
    "a crawling thing with a human voice",
    "a faceless giant carrying a rusted weapon",
    "a pack of thin, silent hunters",
    "a creature whose silhouette keeps changing",
    "an enormous winged shape moving behind the clouds",
    "a pale figure that never seems to blink",
    "a mass of limbs moving as though controlled by one mind",
    "a towering shadow with burning eyes",
]

HELL_ACTIONS = [
    "rushes toward", "circles", "stalks", "drags away", "blocks the path of",
    "stands behind", "watches", "hunts", "surrounds", "follows"
]

# Condemnation cycle: a large library of distinct non-graphic death/rebirth scenes.\n# These are intentionally modular so the same player can experience very different\n# punishments without the bot needing to store a thousand nearly identical strings.\nHELL_DEATHS = [\n    "the ground gives way beneath them and an endless dark swallows their last step",\n    "a door appears behind them, opens by itself, and the darkness on the other side takes them",\n    "the shadows climb over them until their voice is the only thing left, and then even that fades",\n    "a hunting creature catches their trail and drives them into a valley from which no path returns",\n    "the sky suddenly falls silent and a vast shape descends through the ash, taking them from sight",\n    "the road beneath them turns into black water and pulls them beneath its perfectly still surface",\n    "a circle of pale figures closes around them; when it opens again, the condemned is gone",\n    "the walls of the ruins move inward until there is nowhere left to stand",\n    "a bell rings once; every flame dies; when the light returns, they are no longer standing there",\n    "a faceless hunter appears at the end of the road and never stops approaching until the darkness takes them",\n    "the floor becomes a mirror; their reflection reaches upward and drags them beneath it",\n    "a storm of ash blinds them while something enormous circles overhead and finally descends",\n    "the path splits into a hundred roads, and every single one leads them back into the jaws of the same shadow",\n    "their own shadow turns against them, wrapping around their feet and pulling them into the ground",\n    "a burning gate opens beneath them and the realm beyond it refuses to let them return",\n    "the sound of their name travels across the horizon, and every monster in the distance turns toward them",\n    "the air becomes impossible to breathe; they stagger forward until the darkness gently closes over them",\n    "the landscape suddenly becomes weightless, and they fall upward into a sky that has no end",\n    "a procession of silent dead passes through them, and when it is gone, so are they",\n    "a colossal hand emerges from the clouds and carries them beyond the horizon",\n    "the road beneath them stretches endlessly until their strength fails and the darkness claims the rest",\n    "the flames around them bend toward their body as if Hell itself has recognized them",\n    "a creature made of smoke surrounds them; there is a brief flash of light, and then nothing",\n    "the ground opens like a mouth and closes after them",\n    "a voice promises them an escape; they follow it into a doorway that was never there before",\n    "the air fills with footsteps from every direction, and the final pair stops directly beside them",\n    "the ruins collapse around them while something waits patiently beneath the rubble",\n    "their heartbeat becomes a distant drum; the drum stops; Hell begins again",\n    "a black river rises around them and carries them away from every other soul",\n    "a towering figure points toward them and the entire landscape turns against them",\n    "the darkness takes the shape of a cage and slowly closes",\n    "the sky opens and a white-hot light erases their silhouette from the plain",\n    "the floor beneath them becomes a staircase descending forever; eventually there is no longer a figure on it",\n    "a swarm of winged shadows descends; when they pass, only their abandoned belongings remain",\n    "the bells of an unseen cathedral begin ringing faster and faster until the world disappears into blackness",\n    "the Area forgets where they are standing, and their body fades from the place that once held it",\n    "a second version of them steps out of the darkness and reaches for them; both disappear together",\n    "the horizon rushes toward them like a wall, and the impact leaves only silence",\n    "the stars above blink out one by one until the last light goes dark around them",\n    "a nameless creature follows their footsteps backward until it reaches them",\n]\n\nHELL_DEATH_FINISHES = [\n    "Their final sound is swallowed by the silence.",\n    "The darkness closes. Hell does not mourn.",\n    "For one impossible second, there is peace. Then it is gone.",\n    "Nothing remains except footprints that slowly disappear.",\n    "The place remembers the punishment, but not the mercy.",\n    "The silence lasts exactly long enough to make the return worse.",\n    "Hell accepts them without hesitation.",\n    "The landscape closes behind them as though they were never there.",\n    "Their last sight is something smiling in the distance.",\n    "The scream ends, but the echo refuses to.",\n    "The darkness takes them beyond the reach of every other soul.",\n    "Their name becomes another sound among the millions already trapped here.",\n    "The path continues without them.",\n    "The flames dim, waiting for the cycle to begin again.",\n    "There is no escape in the silence that follows.",\n]\n\nHELL_REBIRTHS = [\n    "Then the ash beneath the road begins moving. It gathers into a familiar shape.",\n    "Moments later, they awaken beneath a black sky, exactly where Hell wants them.",\n    "The darkness spits them back onto a distant plain, gasping and alive again.",\n    "Their eyes open inside a ruined cathedral. The bells are already ringing.",\n    "They awaken at the edge of a burning river, with no memory of how far they fell.",\n    "The ground reconstructs the condemned from ash and shadow, and the cycle resumes.",\n    "They return standing beneath the same impossible sky, as though death was only an interruption.",\n    "A distant voice counts backward to one. At one, they are alive again.",\n    "They wake in a field of black flowers. Every flower turns toward them.",\n    "Their breath returns suddenly. The first thing they hear is their own name whispered by something nearby.",\n    "They awaken inside the ruins they just escaped. The doorway is waiting again.",\n    "The darkness releases them onto another road, and every road ahead leads deeper.",\n    "They are rebuilt by the Area's impossible laws and left exactly where the next punishment can find them.",\n    "Their eyes open. The creature that killed them is already waiting in the distance.",\n    "They awaken beneath falling ash with the terrible realization that Hell has reset the scene.",\n    "A pulse travels through the ground. Their body returns with it.",\n    "They wake on a stone altar surrounded by empty footprints.",\n    "The last memory of death fades, but the fear remains.",\n    "They return at the beginning of another road, and somewhere behind them, something laughs.",\n    "The cycle reconstructs them once more. Hell has not finished with them.",\n]\n\nHELL_REBIRTH_STINGS = [\n    "There is no reward for surviving. Only another punishment.",\n    "The cycle has not ended. It has only reset.",\n    "The Area gives them no time to recover.",\n    "Somewhere nearby, something has already noticed them again.",\n    "The next set of footsteps begins.",\n    "Hell remembers every time they died.",\n    "They understand the rule now: death is not an exit.",\n    "The horizon opens another path downward.",\n    "The silence lasts three seconds before the screaming begins again.",\n    "Nothing has changed except that they are alive enough to suffer it again.",\n]\n\n# 40 x 15 x 20 x 10 = 120,000 possible condemnation sequences.\nHELL_DEATH_POOL = []\nfor death in HELL_DEATHS:\n    for finish in HELL_DEATH_FINISHES:\n        for rebirth in HELL_REBIRTHS:\n            for sting in HELL_REBIRTH_STINGS:\n                HELL_DEATH_POOL.append((death, finish, rebirth, sting))\n\n\ndef _condemnation_text(member):\n    name = discord.utils.escape_markdown(member.display_name or member.name)\n    death, finish, rebirth, sting = random.choice(HELL_DEATH_POOL)\n    return (\n        f"{name} is singled out by Hell. {death.capitalize()}. {finish} "\n        f"{rebirth} {sting}"\n    )\n\n\n# The Hell event pool is intentionally generated from independent narrative blocks.
# This gives the system well over 1,000 possible combinations without storing a huge
# repetitive source file.
HELL_MESSAGE_POOL = []
for _environment in HELL_ENVIRONMENTS:
    for _creature in HELL_CREATURES:
        for _action in HELL_ACTIONS:
            HELL_MESSAGE_POOL.append(
                f"{_environment} {_creature.capitalize()} {_action} someone in the distance. The sound of their footsteps suddenly stops."
            )
HELL_MESSAGE_POOL.extend(HELL_WHISPERS)
HELL_MESSAGE_POOL.extend(
    f"{env} {creature.capitalize()} watches from the darkness as the screams of the damned echo endlessly."
    for env in HELL_ENVIRONMENTS for creature in HELL_CREATURES
)
HELL_MESSAGE_POOL.extend(
    [
        "The creature chooses someone in the Area. It rushes forward, catches them, and drags them into the darkness. Their voice is gone. Hell has claimed them.",
        "Something notices someone in the Area. The victim tries to run, but the darkness closes around them. When it recedes, they are gone.",
        "A horned figure steps from the smoke and turns toward someone in the Area. It raises one hand. The screaming stops. Nothing remains but silence.",
        "The ground opens beneath someone in the Area. They fall into an endless darkness while distant voices welcome them below.",
        "A pack of hunters surrounds someone in the Area. The lights fail. When they return, the hunters are gone and the victim is nowhere to be seen.",
        "Something enormous reaches through the smoke toward someone in the Area. They are pulled away despite their desperate struggle. The path behind them closes.",
        "A shadow falls over someone in the Area. They look up. Whatever they see makes them stop fighting. The shadow takes them, and the silence returns.",
        "A creature walks directly toward someone in the Area. It does not hurry. It knows there is nowhere for them to escape.",
        "The screams suddenly become a single voice calling someone in the Area by name. The darkness moves toward them.",
        "Something hunts someone through the burning ruins. Their footsteps become frantic, then suddenly stop. Hell becomes quiet again.",
    ]
)

def _hell_text(text):
    # Every Hell narration is deliberately readable and bold. No Zalgo or
    # decorative corruption is used in Hell because it harms readability.
    return f"**{str(text).replace('**', '')}**"

async def _send_hell(channel, text, *, member=None):
    # Hell may address a condemned player, but it must never create a Discord ping.
    # Use the player's display name as ordinary story text and disable all mentions.
    if member is not None:
        name = discord.utils.escape_markdown(member.display_name or member.name)
        text = text.replace("someone", name, 1)
        if name not in text:
            text = f"{name}: {text}"
    await channel.send(_hell_text(text), allowed_mentions=discord.AllowedMentions.none())


_GLITCH_TASKS = {}
_GLITCH_MESSAGE_TASKS = {}
_GLITCH_WEBHOOKS = {}
MESSAGE_CORRUPTION_DELAY = 12
MESSAGE_CORRUPTION_INTERVAL = 20
MESSAGE_CORRUPTION_STAGES = 5

MANGLE = str.maketrans({"a":"4", "e":"3", "i":"!", "o":"0", "s":"$", "t":"7", "A":"4", "E":"3", "I":"!", "O":"0", "S":"$", "T":"7"})


def _gm(guild_id):
    st = item_state(guild_id)
    st.setdefault("glitch", {})
    g = st["glitch"]
    g.setdefault("channel_id", None)
    g.setdefault("unlocked", False)
    g.setdefault("unlocked_at", None)
    # When the Area is unlocked, player messages can be progressively
    # rewritten by the bot. This is intentionally limited to the dedicated Area
    # channel so normal campaign channels are never modified.
    g.setdefault("message_corruption_enabled", False)
    g.setdefault("memory", [])
    g.setdefault("vocabulary", {})
    g.setdefault("infected_words", {})
    g.setdefault("identity_history", [])
    g.setdefault("event_phase", 0)
    return g


def corrupt(text, intensity=0.18):
    out=[]
    for ch in str(text):
        if ch.isspace():
            out.append(ch); continue
        c=ch
        roll=random.random()
        if roll < intensity * 0.25:
            c=c.translate(MANGLE)
        elif roll < intensity * 0.4 and ch.lower() in "aeiou":
            continue
        out.append(c)
    result="".join(out)
    return result[:1900]



async def _get_glitch_webhook(channel):
    cached = _GLITCH_WEBHOOKS.get(channel.id)
    if cached:
        return cached
    try:
        hooks = await channel.webhooks()
        hook = next((h for h in hooks if h.name == "Hell Relay" and h.token), None)
        if hook is None:
            hook = await channel.create_webhook(name="Hell Relay", reason="Progressive player-message corruption")
        _GLITCH_WEBHOOKS[channel.id] = hook
        return hook
    except (discord.Forbidden, discord.HTTPException):
        return None


def _message_payload(message):
    content = message.content or ""
    if message.attachments:
        attachment_lines = [a.url for a in message.attachments[:10]]
        content = (content + "\n" if content else "") + "\n".join(attachment_lines)
    return content[:1900]


async def _progressively_corrupt_message(message, bot):
    """Relay a player's message through a webhook, then corrupt it in stages."""
    guild = message.guild
    if guild is None:
        return
    g = _gm(guild.id)
    if message.channel.id != g.get("channel_id"):
        return
    if not g.get("unlocked") or not g.get("message_corruption_enabled"):
        return
    if message.author.bot or message.webhook_id:
        return
    original = _message_payload(message)
    if not original:
        return

    # Let the normal message exist briefly before the corruption takes over.
    try:
        await asyncio.sleep(MESSAGE_CORRUPTION_DELAY)
        if not g.get("unlocked") or not g.get("message_corruption_enabled"):
            return
        if message.deleted:
            return
    except asyncio.CancelledError:
        return

    webhook = await _get_glitch_webhook(message.channel)
    if webhook is None:
        return

    try:
        relayed = await webhook.send(
            _hell_text(original),
            username=message.author.display_name[:80],
            avatar_url=message.author.display_avatar.url,
            wait=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        try:
            await message.delete(reason="Glitch corruption engine: replacing player message")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

        # The relay becomes progressively less readable, showing the environment
        # decaying instead of changing everyone's message instantly.
        for stage in range(2, MESSAGE_CORRUPTION_STAGES + 1):
            await asyncio.sleep(MESSAGE_CORRUPTION_INTERVAL)
            if not g.get("unlocked") or not g.get("message_corruption_enabled"):
                break
            intensity = min(0.85, 0.16 + stage * 0.14)
            try:
                await relayed.edit(content=_hell_text(original))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                break
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"Glitch relay warning in {guild.name}: {type(exc).__name__}: {exc}")
    except asyncio.CancelledError:
        pass
    finally:
        _GLITCH_MESSAGE_TASKS.pop(message.id, None)


async def handle_glitch_message(message, bot):
    """Record Area history, track vocabulary/infection, and optionally distort messages."""
    if message.guild is None or message.author.bot:
        return
    g = _gm(message.guild.id)
    if message.channel.id != g.get("channel_id"):
        return
    if not g.get("unlocked"):
        return
    text = (message.content or "").strip()
    if text:
        g.setdefault("memory", []).append({
            "author_id": message.author.id, "author_name": message.author.display_name,
            "text": text[:1000], "at": message.created_at.isoformat(), "channel_id": message.channel.id
        })
        g["memory"] = g["memory"][-1000:]
        words = [w.strip(".,!?;:()[]{}<>\"'").casefold() for w in text.split()]
        for word in [w for w in words if len(w) >= 3][:30]:
            entry = g.setdefault("vocabulary", {}).setdefault(word, {"count": 0, "first_seen": time.time()})
            entry["count"] = int(entry.get("count", 0)) + 1
            if entry["count"] >= 3 and word not in g.setdefault("infected_words", {}):
                g["infected_words"][word] = {"stage": 1, "infected_at": time.time()}
                await _send_hell(message.channel, f"The word **{word}** has been noticed by Hell. Its meaning is no longer entirely yours.")
        for word, data in list(g.get("infected_words", {}).items()):
            if word in text.casefold() and data.get("stage", 1) < 3:
                data["stage"] += 1
                await _send_hell(message.channel, f"The word **{word}** feels wrong now. Something in Hell has begun changing what it means.")
    if g.get("message_corruption_enabled"):
        task = asyncio.create_task(_progressively_corrupt_message(message, bot))
        _GLITCH_MESSAGE_TASKS[message.id] = task


def _hell_overwrites(guild):
    """Make HELL private by default: no @everyone access, bot access only.
    Administrators still see it through Discord's Administrator permission and
    can manually grant individual members access. No members are auto-added.
    """
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }
    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_messages=True,
            manage_webhooks=True,
            read_message_history=True,
        )
    return overwrites


def _find_channel(guild):
    # HELL is the actual Discord channel name. Migrate the old internal names
    # instead of creating duplicate Hell channels.
    channel = discord.utils.get(guild.text_channels, name=WORLD_NAME)
    if channel is None:
        channel = discord.utils.get(guild.text_channels, name="hell-corruption")
    if channel is None:
        channel = discord.utils.get(guild.text_channels, name="glitch-corruption")
    return channel


async def _ensure_channel(guild):
    channel = _find_channel(guild)
    if channel is None:
        channel = await guild.create_text_channel(
            "hell",
            overwrites=_hell_overwrites(guild),
            reason="Create private HELL area; admins manually add condemned players",
        )
    else:
        # Normalize old Hell/Glitch channels into the private HELL channel.
        if channel.name != "hell":
            try:
                await channel.edit(name="hell", reason="Rename RPG Hell channel")
            except discord.HTTPException:
                pass
        try:
            await channel.edit(
                overwrites=_hell_overwrites(guild),
                reason="Keep HELL private; admins manually add members",
            )
        except discord.HTTPException:
            pass
    _gm(guild.id)["channel_id"] = channel.id
    return channel

async def _corruption_loop(guild_id, bot):
    try:
        while _gm(guild_id).get("unlocked"):
            guild=bot.get_guild(guild_id)
            if not guild: break
            channel=await _ensure_channel(guild)
            intensity=min(0.7, 0.15 + (time.time()-float(_gm(guild_id).get("unlocked_at") or time.time()))/900)
            description = random.choice([
                "The Area is no longer obeying normal rules.",
                "Something is rewriting the messages as they arrive.",
                "The air grows colder. Something is awake.",
                "The darkness below the Area has noticed you.",
                "Hell is bleeding through the cracks.",
            ])
            try:
                await _send_hell(channel, description)
                # Hell should feel active rather than like a single scheduled announcement.
                # Send several short in-world signs with randomized pacing.
                burst_count = random.randint(2, 5)
                for _ in range(burst_count):
                    await asyncio.sleep(random.uniform(0.7, 2.2))
                    if not _gm(guild_id).get("unlocked"):
                        break
                    members = [m for m in channel.members if not m.bot] if hasattr(channel, "members") else []
                    target = random.choice(members) if members and random.random() < 0.72 else None
                    if target is not None and random.random() < 0.68:
                        # A targeted condemnation is ordinary story text: the name is
                        # escaped and AllowedMentions.none() prevents any Discord ping.
                        await _send_hell(channel, _condemnation_text(target))
                    else:
                        line = random.choice(HELL_MESSAGE_POOL)
                        await _send_hell(channel, line, member=target)
            except (discord.Forbidden, discord.HTTPException):
                break
            await asyncio.sleep(random.randint(20, 55))
    except asyncio.CancelledError:
        pass
    finally:
        _GLITCH_TASKS.pop(guild_id, None)


@app_commands.command(name="start", description="Start the Hell event without changing access.")
async def start_glitch(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if interaction.guild is None or not is_staff(interaction):
        return await interaction.followup.send("GM/admin only.", ephemeral=True)
    channel = await _ensure_channel(interaction.guild)
    g = _gm(interaction.guild.id)
    g["unlocked"] = True
    g["unlocked_at"] = time.time()
    g["message_corruption_enabled"] = True
    save_item_data()
    old = _GLITCH_TASKS.get(interaction.guild.id)
    if old and not old.done(): old.cancel()
    _GLITCH_TASKS[interaction.guild.id] = asyncio.create_task(_corruption_loop(interaction.guild.id, interaction.client))
    await _send_hell(channel, "The gates of Hell have opened. The Area is no longer a place for the living.")
    await interaction.followup.send(f"Started the Area event. Existing access was left unchanged.", ephemeral=True)


@ADMIN_GLITCH_GROUP.command(name="lock", description="Lock Area anomalies again.")
async def glitch_lock(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if interaction.guild is None or not is_staff(interaction):
        return await interaction.followup.send("GM/admin only.", ephemeral=True)
    channel=await _ensure_channel(interaction.guild)
    g=_gm(interaction.guild.id); g["unlocked"]=False; g["message_corruption_enabled"]=False; save_item_data()
    task=_GLITCH_TASKS.pop(interaction.guild.id, None)
    if task and not task.done(): task.cancel()
    await interaction.followup.send(f"Locked {channel.mention}.", ephemeral=True)


@ADMIN_GLITCH_GROUP.command(name="messages-on", description="Allow Hell to distort player actions within the Area.")
async def glitch_messages_on(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if interaction.guild is None or not is_staff(interaction):
        return await interaction.followup.send("GM/admin only.", ephemeral=True)
    g = _gm(interaction.guild.id)
    if not g.get("unlocked"):
        return await interaction.followup.send("Start Hell from the GM panel first.", ephemeral=True)
    g["message_corruption_enabled"] = True
    save_item_data()
    await interaction.followup.send("Hell narration is now active in the Area.", ephemeral=True)


@ADMIN_GLITCH_GROUP.command(name="messages-off", description="Stop Hell message distortions.")
async def glitch_messages_off(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if interaction.guild is None or not is_staff(interaction):
        return await interaction.followup.send("GM/admin only.", ephemeral=True)
    g = _gm(interaction.guild.id)
    g["message_corruption_enabled"] = False
    save_item_data()
    for message_id, task in list(_GLITCH_MESSAGE_TASKS.items()):
        if not task.done():
            task.cancel()
        _GLITCH_MESSAGE_TASKS.pop(message_id, None)
    await interaction.followup.send("Hell narration is now disabled.", ephemeral=True)


@ADMIN_GLITCH_GROUP.command(name="messages-status", description="Check Hell narration status.")
async def glitch_messages_status(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if interaction.guild is None or not is_staff(interaction):
        return await interaction.followup.send("GM/admin only.", ephemeral=True)
    g = _gm(interaction.guild.id)
    state = "ON" if g.get("message_corruption_enabled") else "OFF"
    await interaction.followup.send(
        f"**Hell narration:** {state}\n"
        f"First corruption: {MESSAGE_CORRUPTION_DELAY}s\n"
        f"Further corruption: every {MESSAGE_CORRUPTION_INTERVAL}s\n"
        f"Stages: {MESSAGE_CORRUPTION_STAGES}",
        ephemeral=True,
    )


@ADMIN_GLITCH_GROUP.command(name="false", description="Create a clearly marked anomalous false message using a stored or chosen name.")
@app_commands.describe(name="Name to display for the anomaly.", text="Text the anomaly should display.")
async def glitch_false_message(interaction: discord.Interaction, name: str, text: str):
    await interaction.response.defer(ephemeral=True)
    if interaction.guild is None or not is_staff(interaction):
        return await interaction.followup.send("GM/admin only.", ephemeral=True)
    channel = await _ensure_channel(interaction.guild)
    g = _gm(interaction.guild.id)
    g.setdefault("memory", []).append({"author_id": None, "author_name": name[:80], "text": text[:1000], "at": time.time(), "anomaly": True})
    g["memory"] = g["memory"][-1000:]
    webhook = await _get_glitch_webhook(channel)
    if webhook:
        await webhook.send(content=_hell_text(text), username=name[:80], wait=False, allowed_mentions=discord.AllowedMentions.none())
    else:
        await channel.send(_hell_text(f"ANOMALOUS MESSAGE — NOT SENT BY THIS USER\n{name[:80]}: {text}"), allowed_mentions=discord.AllowedMentions.none())
    save_item_data()
    await interaction.followup.send("False message created and recorded in Area memory.", ephemeral=True)

@ADMIN_GLITCH_GROUP.command(name="steal", description="Temporarily make the Area speak through a chosen alias.")
@app_commands.describe(name="Alias the anomaly should use.", text="Message to send.")
async def glitch_name_theft(interaction: discord.Interaction, name: str, text: str):
    await interaction.response.defer(ephemeral=True)
    if interaction.guild is None or not is_staff(interaction):
        return await interaction.followup.send("GM/admin only.", ephemeral=True)
    channel = await _ensure_channel(interaction.guild)
    g = _gm(interaction.guild.id)
    g.setdefault("identity_history", []).append({"name": name[:80], "at": time.time(), "used_by": interaction.user.id})
    webhook = await _get_glitch_webhook(channel)
    if webhook:
        await webhook.send(content=_hell_text(text), username=name[:80], wait=False, allowed_mentions=discord.AllowedMentions.none())
    else:
        await channel.send(_hell_text(f"IDENTITY THEFT — ANOMALOUS MESSAGE\n{name[:80]}: {text}"), allowed_mentions=discord.AllowedMentions.none())
    save_item_data()
    await interaction.followup.send("Name-theft event created and recorded in Area memory.", ephemeral=True)

@ADMIN_GLITCH_GROUP.command(name="event", description="Run a staged Hell event.")
@app_commands.describe(phase="Event phase: 1, 2, or 3.")
async def glitch_event(interaction: discord.Interaction, phase: int):
    await interaction.response.defer(ephemeral=True)
    if interaction.guild is None or not is_staff(interaction):
        return await interaction.followup.send("GM/admin only.", ephemeral=True)
    if phase not in (1, 2, 3):
        return await interaction.followup.send("Phase must be 1, 2, or 3.", ephemeral=True)
    channel = await _ensure_channel(interaction.guild)
    g = _gm(interaction.guild.id); g["event_phase"] = phase
    scripts = {
        1: ["Something has begun remembering.", "The Area is remembering things it should not know."],
        2: ["A familiar name appears where nobody typed it.", "The vocabulary is becoming unstable."],
        3: ["The text layer has stopped behaving like a record.", "Something is using the history as if it were a doorway."]
    }
    if phase >= 2 and g.get("memory"):
        remembered = next((m for m in reversed(g["memory"]) if m.get("text") and not m.get("anomaly")), None)
        if remembered:
            scripts[phase].append(f"I remember this: {remembered.get('text','')[:180]}")
    for line in scripts[phase]:
        await _send_hell(channel, line)
        await asyncio.sleep(1)
    save_item_data()
    await interaction.followup.send(f"Hell event phase {phase} started.", ephemeral=True)

@HELL_GROUP.command(name="status", description="Check whether the Area is unlocked.")
async def glitch_status(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if interaction.guild is None:
        return await interaction.followup.send("Server only.", ephemeral=True)
    g=_gm(interaction.guild.id); channel=interaction.guild.get_channel(g.get("channel_id")) if g.get("channel_id") else None
    await interaction.followup.send(f"**Hell Status:** {'UNLOCKED' if g.get('unlocked') else 'LOCKED'}\nArea access: {'Available' if channel else 'Not initialized'}", ephemeral=True)


async def restore_world_states(bot):
    for guild in bot.guilds:
        try:
            g=_gm(guild.id)
            # Hell is always initialized and active. Access remains private until
            # an administrator manually grants members access to the channel.
            channel=await _ensure_channel(guild)
            if not g.get("unlocked"):
                g["unlocked"] = True
                g["unlocked_at"] = time.time()
                g["message_corruption_enabled"] = True
                save_item_data()
            task=_GLITCH_TASKS.get(guild.id)
            if not task or task.done():
                _GLITCH_TASKS[guild.id]=asyncio.create_task(_corruption_loop(guild.id, bot))
        except Exception as exc:
            print(f"Could not restore World for {guild.name}: {type(exc).__name__}: {exc}")

def register(bot):
    bot.tree.add_command(HELL_GROUP)
    # Put the event starter inside the shared /admin hell group.
    # The previous nested /start admin tree collided with another /admin command.
    ADMIN_GLITCH_GROUP.add_command(start_glitch)
