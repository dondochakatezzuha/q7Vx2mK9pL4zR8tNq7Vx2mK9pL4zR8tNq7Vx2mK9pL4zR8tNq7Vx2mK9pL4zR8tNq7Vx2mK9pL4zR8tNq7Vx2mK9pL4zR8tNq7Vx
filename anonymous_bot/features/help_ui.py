import discord
from .gm_tools import _normalize_bounties
from discord import app_commands
from .items import item_state
from .economy import guild_state, balance

HELP_GROUP = app_commands.Group(name="help", description="Command guide and command help.")

CATEGORIES = {
  "Campaign": [
    "/main", "/dashboard", "/admin game start", "/admin game cancel", "/admin game end", "/game status",
    "/attendance check-in", "/attendance check-out", "/admin attendance view",
    "/admin session event", "/admin session summary", "/admin session history",
  ],
  "Bounties": [
    "/bounty list", "/bounty place", "/admin bounty create", "/bounty info", "/admin bounty edit", "/bounty claim",
    "/admin bounty complete", "/admin bounty cancel", "/bounty history", "/admin bounty remove",
  ],
  "Items": [
    "/admin item create", "/admin item edit", "/item rarities", "/item claim", "/item info",
    "/item catalog", "/admin item catalog-remove", "/inventory inventory", "/inventory give",
    "/inventory secure", "/inventory unsecure", "/inventory secure-held", "/inventory steal",
    "/inventory rename", "/admin inventory take", "/admin inventory inventory-view",
  ],
  "Item Drops (GM)": [
    "/admin item rng-start", "/admin item rng-stop", "/admin item dm-start", "/admin item dm-stop", "/admin item status",
    "/admin item force-random", "/admin item force", "/admin item dm-force-random", "/admin item dm-force",
    "/admin item dm-time", "/admin item dm-chance",
  ],
  "Factions & Reputation": [
    "/faction info", "/admin faction create", "/faction join", "/faction donate",
    "/reputation view", "/admin reputation set", "/admin reputation add", "/admin reputation player",
  ],
  "Party": [
    "/party create", "/party invite", "/party join", "/party leave", "/party info",
  ],
  "Secrets & Story": [
    "/story secret-channel", "/story traitor-channel", "/story dead-drop",
    "/admin story objective", "/story my-objective", "/admin story objective-complete", "/admin story objective-clear",
    "/admin story start-ballot", "/story ballot-status", "/anonymous send", "/anonymous dm", "/anonymous one-time",
  ],
  "Economy": [
    "/economy balance", "/economy overview", "/economy values", "/economy shop",
    "/economy buy", "/economy sell", "/economy give", "/economy stocks", "/economy invest",
    "/economy sell-stock", "/economy gamble", "/economy gambling", "/gm", "/gm-economy",
    "/admin economy shop-add", "/admin economy shop-vip", "/admin economy shop-remove", "/admin economy shop-price",
    "/admin economy stock-create", "/admin economy stock-increase", "/admin economy stock-decrease",
  ],
  "Companion": [
    "/companion hub", "/companion name",
  ],
  "Dungeon": [
    "/dungeon open", "/dungeon move", "/dungeon event", "/dungeon fight", "/dungeon search",
    "/dungeon leaderboard", "/admin dungeon lock-floor", "/admin dungeon unlock-floor",
  ],
  "Equipment & Trading": [
    "/equipment equip", "/equipment unequip", "/equipment use", "/equipment view",
    "/trade start", "/trade add", "/trade remove", "/trade money",
  ],
  "Memory": [
    "/memory search", "/memory info", "/gm-memory channel", "/gm-memory priority", "/gm-memory backfill", "/gm-memory suggestions", "/gm-memory delete",
  ],
  "Administration": [
    "/admin panel", "/admin gm-panel", "/gm → Hell → Start Hell Event", "/gm → Hell → Lock Hell", "/gm → Hell → Messages On", "/gm → Hell → Messages Off", "/gm → Hell → Message Status", "/admin clear", "/admin item create", "/admin item edit", "/admin item catalog-remove",
  ],
}


DETAILS = {
  "bounty place": "Player: place a funded bounty on another Player or NPC using VG or VIP.",
  "bounty create": "GM: create a custom bounty for a Player or NPC using VG or VIP.",
  "bounty info": "View the target, reward, status, claimant, proof, faction, and creation information for a bounty.",
  "bounty edit": "GM: edit an open or pending bounty without deleting its history.",
  "bounty complete": "GM: approve a claimed bounty and award its reward.",
  "bounty cancel": "GM: cancel a bounty while preserving it in campaign history.",
  "bounty list": "Public campaign bounty board with filters, details, claim buttons, refresh, and back navigation.",
  "bounty history": "Browse open, pending, completed, and cancelled campaign bounties publicly.",
  "game start": "GM: start or schedule a specific game with a title, optional selected player list, start time, and a customizable check-in window (default 5 minutes).",
  "game cancel": "GM: cancel a scheduled start before it begins.",
  "attendance check-in": "Player: mark yourself as participating in today's live session. Administrators are excluded from player attendance.",
  "attendance check-out": "Player: mark yourself as leaving today's session, optionally with a reason.",
  "attendance view": "GM: see expected, checked-in, checked-out, and non-responding players.",
  "game end": "GM: end the live session and permanently save attendance and session events in campaign history.",
  "session event": "GM: record a major story event, decision, discovery, or other session note.",
  "session summary": "GM: view the current or most recent session summary.",
  "session history": "GM: browse recent completed session history.",
  "game status": "View whether the campaign is currently marked as live or offline.",
  "story objective": "GM: secretly assign a player an objective with no reward, VG, a catalog item, or a custom reward.",
  "story objective-complete": "GM: complete a secret objective and automatically grant its stored reward.",
  "item rename": "Use `/inventory rename` to rename an individual owned item without changing the catalog template.",
  "story secret-channel": "Create a private Discord channel for selected players. The creator is automatically included.",
  "main": "Open the main campaign homepage with character, economy, adventure, bounty, party, faction, inventory, and other quick sections.",
  "dashboard": "Open your personal campaign dashboard with VG, inventory, objectives, reputation, party, and recent activity.",
  "economy balance": "View your current VG balance and VIP Points.",
  "economy overview": "Open the economy overview with personal wealth, faction treasury, stock portfolio, and gambling results.",
  "economy values": "View the VG to VIP Point conversion and VIP market value.",
  "economy stocks": "View current companies, stock prices, market movement, and your holdings.",
  "economy invest": "Buy shares in a campaign company using VG or VIP.",
  "economy sell-stock": "Sell shares you own at the current market price and choose VG or VIP payout.",
  "economy gamble": "Wager VG in the house-favored gambling system.",
  "admin-panel": "Open the GM/admin management panel for player inspection and campaign administration.",
  "memory search": "Search saved campaign memory. GM-only records remain hidden from normal players.",
  "memory info": "View a saved campaign memory record.",
  "memory channel": "GM: choose the campaign/story channel for automatic raw message archiving.",
  "memory suggestions": "GM: review archived messages with images or substantial text for possible campaign records.",
  "memory delete": "GM: delete a saved campaign memory record.",
  "hell lock": "GM: lock Hell again.",
  "companion hub": "Open your private companion hub to check health, train, feed, play, rest, and see the companion's daily dumb story.",
}

def _dashboard_text(guild_id, user):
  st = item_state(guild_id)
  inv = st.get("inventories", {}).get(str(user.id), [])
  econ = guild_state(guild_id)
  gm = st.get("gm_tools", {})
  objectives = st.get("objectives", {}).get(str(user.id), []) if isinstance(st.get("objectives"), dict) else []
  objectives = [o for o in objectives if o.get("status", "active") == "active"]
  reps = gm.get("reputation", {}).get(str(user.id), {})
  party_id = gm.get("party_members", {}).get(str(user.id))
  party = gm.get("parties", {}).get(party_id) if party_id else None
  bounty_claims = [b for b in _normalize_bounties(gm) if b.get("claimed_by") == user.id and b.get("status") == "pending"]
  lines = [
    f"### {user.display_name}'s Campaign Dashboard",
    f"**VG:** {balance(guild_id, user.id):,}",
    f"**Inventory:** {len(inv)} item(s)",
    f"**Active objectives:** {len(objectives)}",
    f"**Pending bounty claims:** {len(bounty_claims)}",
    f"**Party:** {party.get('name') if party else 'None'}",
  ]
  if reps:
    lines.append("\n**Reputation**")
    lines.extend(f"• {name}: **{value:+d}**" for name, value in list(reps.items())[:8])
  if party:
    lines.append(f"\n**Party members:** {len(party.get('members', []))}")
  return "\n".join(lines)


class HelpView(discord.ui.View):
  def __init__(self, user_id):
    super().__init__(timeout=600)
    self.user_id = user_id
    options = [discord.SelectOption(label=k, value=k) for k in CATEGORIES]
    self.add_item(HelpSelect(self, options))

  async def guard(self, interaction):
    if interaction.user.id != self.user_id:
      await interaction.response.send_message("This help menu belongs to another player.", ephemeral=True)
      return False
    return True


class HelpSelect(discord.ui.Select):
  def __init__(self, view, options):
    super().__init__(placeholder="Choose a command category...", options=options)
    self.parent_view = view

  async def callback(self, interaction):
    if not await self.parent_view.guard(interaction): return
    category = self.values[0]
    commands = CATEGORIES[category]
    text = f"### {category}\n\n" + "\n".join(f"• `{c}`" for c in commands)
    text += "\n\nUse `/help command` followed by a command name for details." 
    await interaction.response.edit_message(content=text, view=self.parent_view)


@HELP_GROUP.command(name="open", description="Open the categorized command guide.")
async def help_command(interaction: discord.Interaction):
  await interaction.response.send_message(
    "### Anonymous RPG Command Guide\n\nChoose a category below. The GM controls the story; the bot tracks the campaign mechanics.",
    view=HelpView(interaction.user.id), ephemeral=True
  )


@HELP_GROUP.command(name="command", description="Explain one bot command and how to use it.")
@app_commands.describe(command="Command path, such as bounty create or item info.")
async def help_command_detail(interaction: discord.Interaction, command: str):
  key = command.strip().lstrip("/").casefold().replace("/", " ")
  all_commands = {c.lstrip("/").casefold() for values in CATEGORIES.values() for c in values}
  if key not in all_commands:
    return await interaction.response.send_message("I don't have a guide for that command yet. Try `/help open`.", ephemeral=True)
  detail = DETAILS.get(key, "This command is available in the bot. Use Discord's slash-command hints to see its arguments.")
  await interaction.response.send_message(f"### `/{key}`\n\n{detail}", ephemeral=True)


@app_commands.command(name="dashboard", description="Open your personal campaign dashboard.")
async def dashboard(interaction: discord.Interaction):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  await interaction.response.send_message(_dashboard_text(interaction.guild.id, interaction.user), ephemeral=True)


def register(bot):
  bot.tree.add_command(HELP_GROUP)
  bot.tree.add_command(dashboard)
