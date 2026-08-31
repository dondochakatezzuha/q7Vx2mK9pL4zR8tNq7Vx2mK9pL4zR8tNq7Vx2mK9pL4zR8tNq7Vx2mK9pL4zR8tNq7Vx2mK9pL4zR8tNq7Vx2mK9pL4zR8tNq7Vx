import asyncio
import random
import time
import discord
from discord import app_commands

from ..state import is_staff
from .groups import ADMIN_HELL_GROUP
from .items import item_state, save_item_data

UNDERWORLD_STATUS_KEY = "underworld_status"
_ATMOSPHERE_TASKS = {}

HELL_ATMOSPHERE = [
    "A roll of thunder travels across the black sky. The sound continues long after the clouds have gone silent.",
    "A distant scream rises from somewhere beyond the horizon, then is abruptly cut off.",
    "Thunder cracks directly overhead. For one second, every flame in Hell bends toward the same direction.",
    "Something screams far below the ground. The sound is so deep it feels more like a vibration than a voice.",
    "A low, distant chorus of screams drifts through the smoke. None of the voices sound close enough to find.",
    "The ground gives a slow, heavy tremor. Somewhere in the darkness, something enormous moves beneath the surface.",
    "A bell rings once in the distance. The echo travels across Hell for far too long.",
    "The sky flashes white with silent lightning. A heartbeat later, thunder shakes the entire Area.",
    "For a moment, every scream in Hell stops. The silence lasts only a few seconds before the noise returns all at once.",
    "A distant roar rolls across the horizon. Nothing can be seen, but every shadow seems to turn toward it.",
    "Wind moves through the ash with a sound almost like whispering. Then something far away answers with a scream.",
    "A deep impact shakes the ground somewhere beyond sight. Dust falls from the ruins, but nothing else moves.",
    "The black clouds churn. Thunder murmurs overhead while distant voices rise and fall beneath it.",
    "Something howls across the horizon. The sound is answered by another howl, then another, until the darkness is full of them.",
    "The air suddenly becomes still. In the silence, faint footsteps can be heard somewhere impossibly far away.",
]

ATMOSPHERE_MIN_SECONDS = 30
ATMOSPHERE_MAX_SECONDS = 60


def _gm(guild_id):
    """Persistent Hell state. Hell is now a quiet RPG location; no automatic events."""
    st = item_state(guild_id)
    st.setdefault("hell", {})
    g = st["hell"]
    g.setdefault("channel_id", None)
    return g


def _underworld_state(guild_id):
    return _gm(guild_id).setdefault(UNDERWORLD_STATUS_KEY, {})


def _underworld_mark(guild_id, user_id, reason="GM underworld condemnation"):
    _underworld_state(guild_id)[str(user_id)] = {
        "status": "in_hell",
        "reason": reason,
        "updated_at": time.time(),
    }
    save_item_data()


def _underworld_clear(guild_id, user_id):
    _underworld_state(guild_id).pop(str(user_id), None)
    save_item_data()


def _hell_overwrites(guild):
    """HELL starts private: @everyone gets no access; admins can manually add players."""
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
    # Only the current RPG name is preferred. Legacy names are migrated if found.
    return (
        discord.utils.get(guild.text_channels, name="hell")
        or discord.utils.get(guild.text_channels, name="hell-corruption")
        or discord.utils.get(guild.text_channels, name="g" + "litch-corruption")
    )


async def _ensure_channel(guild):
    channel = _find_channel(guild)
    if channel is None:
        channel = await guild.create_text_channel(
            "hell",
            overwrites=_hell_overwrites(guild),
            reason="Create private HELL area; GMs manually add condemned players",
        )
    else:
        if channel.name != "hell":
            try:
                await channel.edit(name="hell", reason="Rename RPG Hell channel")
            except discord.HTTPException:
                pass
        try:
            await channel.set_permissions(
                guild.default_role,
                view_channel=False,
                reason="Keep HELL private; GMs manually add members",
            )
            if guild.me is not None:
                await channel.set_permissions(
                    guild.me,
                    view_channel=True,
                    send_messages=True,
                    manage_messages=True,
                    manage_webhooks=True,
                    read_message_history=True,
                    reason="Keep bot access to HELL",
                )
        except (discord.Forbidden, discord.HTTPException):
            return None
    _gm(guild.id)["channel_id"] = channel.id
    save_item_data()
    return channel


async def _hell_member_overwrite(channel, member, *, enabled):
    overwrite = discord.PermissionOverwrite(
        view_channel=enabled,
        send_messages=enabled,
        read_message_history=enabled,
        attach_files=enabled,
        embed_links=enabled,
    ) if enabled else None
    return await channel.set_permissions(
        member,
        overwrite=overwrite,
        reason="Grant Underworld access" if enabled else "Remove Underworld access on revive",
    )


def _bold_scene(name, ping_id=None):
    ping = f"<@{ping_id}>" if ping_id else name
    return (
        f"{ping} rises toward a light that feels warmer than anything they have ever known. Their memories of pain begin to disappear. Their fear disappears. For one impossible moment, there is peace.\n\n"
        f"Then they look down. There is no world beneath them. Only an endless darkness filled with countless distant lights. They realize the lights are eyes. Millions of them are staring upward.\n\n"
        f"The light above suddenly dies. The heavens fold inward, and {name} begins to fall. The clouds become ash, then smoke, then darkness. The air fills with sulfur and the distant screaming of countless damned souls.\n\n"
        f"Hell opens beneath them. It has no horizon. Ruined cities stretch endlessly beneath black skies. Rivers of fire carve through mountains of ash. Shapes move inside the smoke, watching, waiting.\n\n"
        f"The screaming never stops. Some voices sound centuries old. Others whisper things {name} remembers saying. Nobody comes to help. Every road eventually curves back toward the place where they began.\n\n"
        f"They run. The landscape never changes. Every shadow seems to belong to something watching them. Then they understand the worst part: Hell isn't trying to kill them. Hell is making sure death cannot save them.\n\n"
        f"Their body finally gives out. Everything becomes silent. Then they breathe. Their eyes open beneath the black sky. The ash is falling again. The screaming has returned. Something is already approaching through the smoke.\n\n"
        f"It remembers them."
    )


def _bold_recall(name, ping_id=None):
    ping = f"<@{ping_id}>" if ping_id else name
    return (
        f"{ping} falls through an endless darkness. There is no ground beneath them, no sky above them, only the fading screams of Hell somewhere impossibly far below.\n\n"
        f"Then a heartbeat.\n\n"
        f"Once.\n\n"
        f"Twice.\n\n"
        f"A third beat tears through the void like thunder. Cracks of brilliant light spread across the darkness around {name}, as if reality itself is breaking apart.\n\n"
        f"{name} suddenly stops falling. For one impossible second, they hang motionless between death and life. Then reality slams back into them.\n\n"
        f"Their heart strikes once with enough force to shake their entire chest. Their lungs violently fill with air. Their eyes snap open. A shockwave bursts outward as they crash onto the ground, scattering dust and debris around them.\n\n"
        f"Strange remnants of the force that pulled them from Hell flicker across their body before vanishing. {name} slowly rises, gasping, staring into a world that suddenly feels impossibly bright and quiet.\n\n"
        f"For a moment, their eyes still reflect the blackness of the pit. Then the final crack in reality closes behind them with a thunderous sound. The light disappears. The air becomes still.\n\n"
        f"They are standing. Alive. Free.\n\n"
        f"But somewhere beyond the veil, something screams their name. Hell has not forgotten them."
    )


async def _send_bold_scene(channel, text, *, allowed_mentions=None):
    """Send a readable, fully-bold scene while staying below Discord's limit."""
    paragraphs = str(text).split("\n\n")
    chunks, current = [], ""
    for para in paragraphs:
        candidate = para if not current else current + "\n\n" + para
        if len(candidate) > 1900 and current:
            chunks.append(current)
            current = para
        else:
            current = candidate
    if current:
        chunks.append(current)
    for chunk in chunks:
        clean = chunk.replace("**", "")
        await channel.send(
            f"**{clean}**",
            allowed_mentions=allowed_mentions or discord.AllowedMentions.none(),
        )


def _stop_hell_atmosphere(guild_id):
    task = _ATMOSPHERE_TASKS.pop(guild_id, None)
    if task and not task.done():
        task.cancel()


def _start_hell_atmosphere(guild_id, bot):
    task = _ATMOSPHERE_TASKS.get(guild_id)
    if not task or task.done():
        _ATMOSPHERE_TASKS[guild_id] = asyncio.create_task(_hell_atmosphere_loop(guild_id, bot))


async def _hell_atmosphere_loop(guild_id, bot):
    """Quietly keep HELL alive with one atmospheric sound every 30-60 seconds.

    No deaths, no automatic targeting, no player pings, and no message copying.
    The loop only speaks when at least one non-bot member has access to HELL.
    """
    try:
        while True:
            guild = bot.get_guild(guild_id)
            if guild is None:
                return
            channel = await _ensure_channel(guild)
            if channel is None:
                await asyncio.sleep(60)
                continue
            members = [m for m in channel.members if not m.bot]
            if members:
                await _send_bold_scene(channel, random.choice(HELL_ATMOSPHERE))
            await asyncio.sleep(random.uniform(ATMOSPHERE_MIN_SECONDS, ATMOSPHERE_MAX_SECONDS))
    except asyncio.CancelledError:
        return
    except Exception as exc:
        print(f"HELL atmosphere warning for {guild_id}: {type(exc).__name__}: {exc}")
    finally:
        _ATMOSPHERE_TASKS.pop(guild_id, None)


async def handle_hell_message(message, bot):
    """HELL message hook. Lore archiving is handled centrally by memory.archive_message()."""
    return


UNDERWORLD_GROUP = app_commands.Group(name="underworld", description="GM-only Underworld controls.")


@UNDERWORLD_GROUP.command(name="user", description="GM only: send a player into the Underworld.")
@app_commands.describe(user="The player to send into the Underworld.")
async def underworld(interaction: discord.Interaction, user: discord.Member):
    if interaction.guild is None or not is_staff(interaction):
        return await interaction.response.send_message("GM/admin only.", ephemeral=True)
    await interaction.response.defer()
    channel = await _ensure_channel(interaction.guild)
    if channel is None:
        return await interaction.followup.send("I could not access HELL. Check my channel permissions.", ephemeral=True)

    await _hell_member_overwrite(channel, user, enabled=True)
    _underworld_mark(interaction.guild.id, user.id)
    _start_hell_atmosphere(interaction.guild.id, interaction.client)
    await interaction.followup.send(f"{user.display_name} was sent to the Underworld.", ephemeral=True)



@ADMIN_HELL_GROUP.command(name="event", description="GM only: trigger an event or scene inside HELL.")
@app_commands.describe(event="The event, scene, omen, encounter, or other GM-written event to post in HELL.")
async def gm_hell_event(interaction: discord.Interaction, event: str):
    if interaction.guild is None or not is_staff(interaction):
        return await interaction.response.send_message("GM/admin only.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    channel = await _ensure_channel(interaction.guild)
    if channel is None:
        return await interaction.followup.send("I could not access HELL. Check my channel permissions.", ephemeral=True)
    text = str(event or "").strip()
    if not text:
        return await interaction.followup.send("You need to provide the HELL event text.", ephemeral=True)
    await _send_bold_scene(channel, text, allowed_mentions=discord.AllowedMentions.none())
    _start_hell_atmosphere(interaction.guild.id, interaction.client)
    await interaction.followup.send("HELL event posted.", ephemeral=True)


@ADMIN_HELL_GROUP.command(name="announce", description="GM only: announce a GM-written message in HELL.")
@app_commands.describe(message="The GM-written message to announce in HELL.")
async def gm_hell_announce(interaction: discord.Interaction, message: str):
    if interaction.guild is None or not is_staff(interaction):
        return await interaction.response.send_message("GM/admin only.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    channel = await _ensure_channel(interaction.guild)
    if channel is None:
        return await interaction.followup.send("I could not access HELL. Check my channel permissions.", ephemeral=True)
    await channel.send(message[:1900], allowed_mentions=discord.AllowedMentions.none())
    await interaction.followup.send("HELL announcement posted.", ephemeral=True)

async def _revive_player(guild, user):
    """Canonical revive implementation shared by /revive and G:."""
    state = _underworld_state(guild.id)
    if str(user.id) not in state:
        return False, f"{user.display_name} is not currently in the Underworld."

    channel = await _ensure_channel(guild)
    if channel is None:
        return False, "I could not access HELL. Check my channel permissions."

    _underworld_clear(guild.id, user.id)
    await _hell_member_overwrite(channel, user, enabled=False)
    if not _underworld_state(guild.id):
        _stop_hell_atmosphere(guild.id)
    return True, f"{user.display_name} was revived and removed from HELL. Their data was preserved."


@app_commands.command(name="revive", description="GM only: revive a player and remove them from HELL while preserving their data.")
@app_commands.describe(user="The player to revive and remove from HELL.")
async def revive(interaction: discord.Interaction, user: discord.Member):
    if interaction.guild is None or not is_staff(interaction):
        return await interaction.response.send_message("GM/admin only.", ephemeral=True)
    await interaction.response.defer()
    _, message = await _revive_player(interaction.guild, user)
    await interaction.followup.send(message, ephemeral=True)


@app_commands.command(name="reincarnate", description="GM only: completely restart a player's stored character data and remove them from HELL.")
@app_commands.describe(user="The player whose stored character data should be reset.")
async def reincarnate(interaction: discord.Interaction, user: discord.Member):
    if interaction.guild is None or not is_staff(interaction):
        return await interaction.response.send_message("GM/admin only.", ephemeral=True)
    if user.bot:
        return await interaction.response.send_message("Bot accounts cannot be reincarnated.", ephemeral=True)
    if user.id == interaction.user.id:
        return await interaction.response.send_message("You cannot reincarnate your own GM/admin account.", ephemeral=True)
    await interaction.response.defer()

    channel = await _ensure_channel(interaction.guild)
    if channel is not None:
        try:
            await _hell_member_overwrite(channel, user, enabled=False)
        except (discord.Forbidden, discord.HTTPException):
            pass
    _underworld_clear(interaction.guild.id, user.id)

    # Reuse the canonical GM player-clear implementation so reincarnation and
    # /admin clear cannot drift apart over time. Lore/campaign history remains.
    from .gm_tools import reset_player_data
    removed = reset_player_data(interaction.guild.id, user.id)
    _stop_hell_atmosphere(interaction.guild.id)

    # If other condemned players remain, their ambience continues.
    if _underworld_state(interaction.guild.id):
        _start_hell_atmosphere(interaction.guild.id, interaction.client)

    detail = ", ".join(removed) if removed else "no stored player data found"
    await interaction.followup.send(
        f"{user.display_name} has been reincarnated. Their character data was reset and they were removed from HELL.\n\nReset: {detail}. Campaign lore/history was preserved.",
        ephemeral=True,
    )


def register(bot):
    # Only these two Hell-related application commands remain.
    bot.tree.add_command(UNDERWORLD_GROUP)
    bot.tree.add_command(revive)
    bot.tree.add_command(reincarnate)


async def restore_hell_states(bot):
    """Restore HELL access after restart; ambience only runs while someone is condemned."""
    for guild in bot.guilds:
        try:
            channel = await _ensure_channel(guild)
            if channel is None:
                continue
            if _underworld_state(guild.id):
                _start_hell_atmosphere(guild.id, bot)
            else:
                _stop_hell_atmosphere(guild.id)
        except Exception as exc:
            print(f"Could not restore HELL state for {guild.name}: {type(exc).__name__}: {exc}")

