import asyncio
import discord
from discord.ext import commands

from .config import TOKEN
from .features import (
  anonymous,
  items,
  rpg,
  trade,
  create,
  economy,
  dungeon,
  equipment,
  main_ui,
  gm_tools,
  help_ui,
  memory,
  companions,
  hell,
  ai_channel,
  server_lore,
)
from .features.gm import assistant as gemini_gm
from .core import campaign_store, lore_index
from . import web_app


class AnonymousBot(commands.Bot):
  def __init__(self):
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True
    super().__init__(command_prefix="!", intents=intents)


  async def on_app_command_error(self, interaction: discord.Interaction, error: Exception):
    original = getattr(error, "original", error)
    print(f"Application command error in {getattr(interaction.command, 'name', 'unknown')}: {type(original).__name__}: {original}")
    message = "The command encountered an error, but the interaction was handled. Please try again."
    try:
      if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
      else:
        await interaction.response.send_message(message, ephemeral=True)
    except Exception as response_error:
      print(f"Could not send application-command error response: {response_error}")

  async def on_view_error(self, interaction: discord.Interaction, error: Exception, item):
    original = getattr(error, "original", error)
    print(f"Component error in {getattr(item, 'custom_id', type(item).__name__)}: {type(original).__name__}: {original}")
    message = "That action could not be completed. The menu is still available; use Refresh or go Back and try again."
    try:
      if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
      else:
        await interaction.response.send_message(message, ephemeral=True)
    except Exception as response_error:
      print(f"Could not send component error response: {response_error}")

  async def on_modal_error(self, interaction: discord.Interaction, error: Exception):
    original = getattr(error, "original", error)
    print(f"Modal error: {type(original).__name__}: {original}")
    message = "The form could not be completed. Please close it and try again."
    try:
      if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
      else:
        await interaction.response.send_message(message, ephemeral=True)
    except Exception as response_error:
      print(f"Could not send modal error response: {response_error}")

  async def setup_hook(self):
    campaign_store.initialize()
    lore_index.initialize()
    try:
      campaign_store.backup("startup")
    except Exception as exc:
      print(f"Campaign backup warning: {type(exc).__name__}: {exc}")
    anonymous.register(self)
    rpg.register(self)
    items.register(self)
    trade.register(self)
    create.register(self)
    economy.register(self)
    dungeon.register(self)
    equipment.register(self)
    main_ui.register(self)
    gm_tools.register(self)
    gemini_gm.register(self)
    help_ui.register(self)
    memory.register(self)
    companions.register(self)
    hell.register(self)
    # Centralized interaction error handling prevents Discord's
    # "The application did not respond" fallback for command/component errors.
    self.tree.on_error = self.on_app_command_error
    discord.ui.View.on_error = self.on_view_error
    discord.ui.Modal.on_error = self.on_modal_error

  async def on_message(self, message):
    try:
      web_app.record_ooc_discord_message(message)
      web_app.record_game_discord_message(message)
    except Exception as exc:
      print(f"General web bridge warning: {type(exc).__name__}: {exc}")
    if not message.author.bot:
      try:
        server_lore.archive(message)
      except Exception as exc:
        print(f"Server lore archive warning: {type(exc).__name__}: {exc}")
      try:
        if await ai_channel.handle_message(message):
          return
      except Exception as exc:
        print(f"AI channel handler warning: {type(exc).__name__}: {exc}")
    if not message.author.bot:
      try:
        if message.guild is not None and (message.content or "").strip().casefold() == "g: confirm" and not message.author.bot:
          if await gemini_gm.confirm_pending(message):
            return
        if message.guild is not None and (message.content or "").strip().casefold().startswith("g:"):
          if await gemini_gm.handle_gm_message(message, self):
            return
        if message.guild is None:
          await memory.archive_dm_message(message, self)
        else:
          await memory.archive_message(message)
      except Exception as exc:
        print(f"Lore archive warning: {type(exc).__name__}: {exc}")
      try:
        await hell.handle_hell_message(message, self)
        await companions.handle_message(message)
      except Exception as exc:
        print(f"Hell message handler warning: {type(exc).__name__}: {exc}")

  async def on_ready(self):
    # The Discord bot must be online before the web client is exposed. This makes
    # the one-click launcher deterministic: Ollama -> Discord -> website.
    try:
      web_app.start(self)
    except Exception as exc:
      print(f"Web client startup warning: {type(exc).__name__}: {exc}")
    # Ensure the administrator-only #ai channel exists in the configured campaign server.
    try:
      from .config import GAME_GUILD_ID
      for _guild in self.guilds:
        if int(_guild.id) == int(GAME_GUILD_ID):
          await ai_channel.ensure_ai_channel(_guild)
        try:
          from .features.items import item_state
          from .core import lore_index
          profiles = (item_state(_guild.id).get("campaign_memory") or {}).get("entity_profiles") or {}
          lore_index.sync_all_profiles(_guild.id, profiles)
        except Exception as sync_exc:
          print(f"Lore index startup sync warning for {_guild.id}: {type(sync_exc).__name__}: {sync_exc}")
    except Exception as exc:
      print(f"AI channel startup warning: {type(exc).__name__}: {exc}")

    # First remove any old guild-specific command registrations left behind by
    # previous versions of the bot. Guild commands are separate from global
    # commands, so a global sync does NOT delete them.
    if not getattr(self, "_guild_commands_cleared", False):
      for guild in self.guilds:
        try:
          self.tree.clear_commands(guild=guild)
          self.tree.copy_global_to(guild=guild)
          synced_guild = await self.tree.sync(guild=guild)
          print(f"Synced current commands to {guild.name}: {len(synced_guild)} guild commands.")
        except Exception as exc:
          print(f"Guild command cleanup warning for {guild.name}: {type(exc).__name__}: {exc}")
      self._guild_commands_cleared = True

    # Register the current command tree globally. Global commands can take
    # some time to propagate through Discord, but a successful sync replaces
    # the previous global command definitions.
    if not getattr(self, "_global_commands_synced", False):
      try:
        synced = await self.tree.sync()
        self._global_commands_synced = True
        print(f"Globally synced {len(synced)} application commands.")
        print("Global commands:")
        for command in synced:
          print(f"  /{command.name}")
      except Exception as exc:
        print(f"Global command sync warning: {type(exc).__name__}: {exc}")



    # Start the server lore backfill AFTER the bot is online.  It runs in the
    # background so the bot can respond immediately while historical messages
    # are imported.  server_lore.startup() only backfills channels that have
    # not already completed their initial sync.
    if not getattr(self, "_server_lore_task", None) or self._server_lore_task.done():
      async def _run_server_lore_backfill():
        try:
          print("[server-lore] Bot is online; starting background server backfill...")
          await server_lore.startup(self)
          print("[server-lore] Background server backfill finished.")
        except asyncio.CancelledError:
          print("[server-lore] Background server backfill cancelled.")
          raise
        except Exception as exc:
          print(f"[server-lore] Background backfill warning: {type(exc).__name__}: {exc}")
      self._server_lore_task = asyncio.create_task(_run_server_lore_backfill())

    # Restore any scheduled game starts saved before a bot restart.
    try:
      await gm_tools.restore_scheduled_game_starts(self)
    except Exception as exc:
      print(f"Scheduled game restore warning: {type(exc).__name__}: {exc}")

    try:
      await hell.restore_hell_states(self)
    except Exception as exc:
      print(f"Hell restore warning: {type(exc).__name__}: {exc}")

    # Item RNG was removed. GM-created spawn timers are the only automatic spawn system.
    if not getattr(self, "_gm_spawn_task", None) or self._gm_spawn_task.done():
      self._gm_spawn_task = asyncio.create_task(gm_tools.spawn_loop(self))
    try:
      starter = getattr(self, "_start_stock_market_task", None)
      if starter:
        await starter()
    except Exception as exc:
      print(f"Stock market task startup warning: {type(exc).__name__}: {exc}")
    await self.change_presence(
      status=discord.Status.online,
      activity=discord.Activity(
        type=discord.ActivityType.playing,
        name="Anonymous RPG",
        state="Anonymous RPG"
      )
    )
    print(f"Logged in as {self.user}")
    print(f"Economy package loaded from: {economy.__file__}")
    print(f"Stock helpers loaded: dividends={hasattr(economy, '_apply_dividends')}, market_change={hasattr(economy, '_apply_market_change')}")
    print(f"Gambling games loaded: {', '.join(getattr(economy, 'CORE_GAMES', {}).keys()) or 'NONE'}")
    print("Anonymous bot is online!")



def create_bot():
  """Create a fresh Discord client for each process-level run.

  discord.py closes the aiohttp session when a client disconnects. Reusing the
  same Bot instance after a failed connection leaves that session closed and
  causes the restart loop to fail with ``RuntimeError: Session is closed``.
  Always create a new client after an unexpected run() failure.
  """
  return AnonymousBot()


# Kept for modules/tools that import this module-level name. The restart loop
# replaces it with a fresh client whenever bot.run() exits unexpectedly.
bot = create_bot()


def run_bot():
  """Run the bot and safely recover from transient Discord/network failures."""
  global bot
  restart_delay = 5
  max_restart_delay = 60

  while True:
    try:
      # A Discord DNS/connectivity failure can make bot.run() exit before the
      # client ever reaches on_ready. Never reuse that client: its aiohttp
      # session may already be closed.
      bot = create_bot()
      print("Starting Anonymous bot...")
      bot.run(TOKEN, reconnect=True)
      print("Anonymous bot stopped cleanly. Automatic restart skipped.")
      break
    except KeyboardInterrupt:
      print("Manual shutdown detected. Automatic restart skipped.")
      break
    except Exception as exc:
      print(f"Anonymous bot stopped unexpectedly: {type(exc).__name__}: {exc}")
      print(f"Discord/network connection failed. Retrying in {restart_delay} seconds with a fresh client...")
      try:
        import time
        time.sleep(restart_delay)
      except KeyboardInterrupt:
        print("Manual shutdown detected during restart delay. Exiting.")
        break
      restart_delay = min(max_restart_delay, restart_delay * 2)


if __name__ == "__main__":
  run_bot()
