"""Skill, risk, and PvP gambling subsystem."""

import random
import time
from datetime import datetime, timezone

import discord
from discord import app_commands

from .core import *  # noqa: F401,F403


# Gambling commands are registered as a subgroup under /economy.
GAMBLING_GROUP = app_commands.Group(
  name="gambling",
  description="Skill, risk, and PvP gambling games."
)

# Shared persistence key; kept here so gambling.py is self-contained.
GAMBLING_KEY = "gambling"

# Canonical game registry. This lives in gambling.py because the /main UI
# imports it directly; keeping a second copy in market.py caused missing-name
# failures when the modules were loaded in a different order.
CORE_GAMES = {
  "dice_poker": {"name": "Dice Poker", "profile": "Skill", "entry_max": None},
  "blackjack": {"name": "Blackjack", "profile": "Skill", "entry_max": None},
  "memory": {"name": "Memory Table", "profile": "Skill", "entry_max": None},
  "high_low": {"name": "High/Low", "profile": "Risk", "entry_max": None},
  "blackjack_duel": {"name": "Blackjack Duel", "profile": "PvP", "entry_max": None},
}


def _refund_timed_out_game(view):
  """Return an uncompleted wager when a player abandons/loses a game view."""
  if getattr(view, "finished", False):
    return
  view.finished = True
  if getattr(view, "currency", "VG") == "VIP":
    add_vip_points(view.guild_id, view.user_id, int(view.wager))
  else:
    add_money(view.guild_id, view.user_id, int(view.wager))
  _record_gambling(view.guild_id, view.user_id, view.wager, 0, 0, False, currency=getattr(view, "currency", "VG"))
  save_item_data()


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

def _record_gambling(guild_id, user_id, wager, profit, vip_reward=0, win=False, currency="VG"):
  data = _gambling_state(guild_id, user_id)
  data["wagered"] = int(data.get("wagered", 0)) + int(wager)
  key = "wagered_vip" if str(currency).upper() == "VIP" else "wagered_vg"
  data[key] = int(data.get(key, 0)) + int(wager)
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

def _currency_name(currency):
  return "VIP Points" if str(currency).upper() == "VIP" else "VG"

def _pay_wager(guild_id, user_id, wager, currency="VG"):
  ok, msg, data = _gambling_check(guild_id, user_id, wager)
  if not ok: return False, msg, data
  currency = str(currency).upper()
  if currency == "VIP":
    current = vip_points(guild_id, user_id)
    if current < wager:
      return False, f"You need **{wager:,} VIP Points**.", data
    # VIP points are stored as the same integer unit used by the economy.
    _set_vip_cards(guild_id, user_id, current - wager)
  else:
    current = balance(guild_id, user_id)
    if current < wager:
      return False, f"You need **{wager:,} VG**.", data
    set_balance(guild_id, user_id, current - wager)
  return True, "", data

def _pay_profit(guild_id, user_id, wager, profit, currency="VG"):
  if profit > 0:
    if str(currency).upper() == "VIP":
      add_vip_points(guild_id, user_id, wager + profit)
    else:
      add_money(guild_id, user_id, wager + profit)

def _currency_choice():
  return [app_commands.Choice(name="VG", value="VG"), app_commands.Choice(name="VIP", value="VIP")]


@ECONOMY_GROUP.command(name="gamble", description="Start a gambling game or view available games.")
@app_commands.describe(game="Optional game to start.", wager="Optional wager.", currency="Currency to gamble with: VG or VIP.")
@app_commands.choices(currency=[app_commands.Choice(name="VG", value="VG"), app_commands.Choice(name="VIP", value="VIP")])
@app_commands.choices(game=[app_commands.Choice(name=v["name"], value=k) for k, v in CORE_GAMES.items()])
async def economy_gamble(interaction: discord.Interaction, game: app_commands.Choice[str] | None = None, wager: int | None = None, currency: app_commands.Choice[str] | None = None):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  if game is None:
    lines = ["**GAMBLING**", "", "Select a game from `/main → Economy → Gambling`, or run `/economy gamble` with a game and wager.", ""]
    lines.extend(f"• **{v['name']}** — {v['profile']} — **no wager cap** (your balance/points are the limit)" for v in CORE_GAMES.values())
    return await interaction.response.send_message("\n".join(lines), ephemeral=True)
  if wager is None:
    return await interaction.response.send_message("Provide a wager and choose **VG** or **VIP**.", ephemeral=True)
  await start_gambling_game(interaction, game.value, wager, (currency.value if currency else "VG"))


async def start_gambling_game(interaction, game: str, wager: int, currency: str = "VG"):
  """Start a game from the /main Economy -> Gambling UI."""
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  game = str(game)
  limits = CORE_GAMES.get(game)
  if not limits:
    return await interaction.response.send_message("That gambling game is unavailable.", ephemeral=True)
  if wager <= 0:
    return await interaction.response.send_message("Wager must be positive.", ephemeral=True)
  currency = str(currency).upper()
  if currency not in {"VG", "VIP"}: currency = "VG"

  gid, uid = interaction.guild.id, interaction.user.id
  if game == "blackjack":
    ok, msg, _ = _pay_wager(gid, uid, wager, currency)
    if not ok: return await interaction.response.send_message(msg, ephemeral=True)
    deck = list(range(1, 14)) * 4
    random.SystemRandom().shuffle(deck)
    player = [deck.pop(), deck.pop()]
    dealer = [deck.pop(), deck.pop()]
    view = BlackjackView(gid, uid, wager, player, dealer, deck, currency)
    return await interaction.response.send_message(view.render(), view=view, ephemeral=True)

  if game == "dice_poker":
    ok, msg, _ = _pay_wager(gid, uid, wager, currency)
    if not ok: return await interaction.response.send_message(msg, ephemeral=True)
    view = DicePokerView(gid, uid, wager, 1, random.SystemRandom().sample(range(1,7), 5), currency)
    return await interaction.response.send_message(view.render(), view=view, ephemeral=True)

  if game == "memory":
    ok, msg, _ = _pay_wager(gid, uid, wager, currency)
    if not ok: return await interaction.response.send_message(msg, ephemeral=True)
    view = MemoryTableView(gid, uid, wager, 1, currency)
    return await interaction.response.send_message(view.render(), view=view, ephemeral=True)

  if game == "high_low":
    ok, msg, _ = _pay_wager(gid, uid, wager, currency)
    if not ok: return await interaction.response.send_message(msg, ephemeral=True)
    a = random.randint(1,13); b = random.randint(1,13)
    return await interaction.response.send_message(
      f"**HIGH/LOW**\n\nFirst card: **{a}**\n\nChoose whether the next card will be higher or lower.",
      view=HighLowChoiceView(gid, uid, wager, a, b, currency), ephemeral=True)

  if game == "blackjack_duel":
    return await interaction.response.send_message(
      "Use **/economy gambling blackjack-duel** for Blackjack Duel so you can select your opponent.",
      ephemeral=True)

class GamblingWagerModal(discord.ui.Modal):
  def __init__(self, game):
    label = CORE_GAMES.get(game, {}).get("name", "Gambling")
    super().__init__(title=f"{label} — Wager")
    self.game = game
    self.wager = discord.ui.TextInput(label="Wager amount", placeholder="Enter VG or VIP amount", required=True, max_length=20)
    self.currency = discord.ui.TextInput(label="Currency", placeholder="VG or VIP", default="VG", required=True, max_length=3)
    self.add_item(self.wager)
    self.add_item(self.currency)

  async def on_submit(self, interaction):
    try:
      wager = int(str(self.wager.value).replace(",", "").strip())
    except ValueError:
      return await interaction.response.send_message("Enter a whole-number wager.", ephemeral=True)
    currency = str(self.currency.value).strip().upper()
    if currency not in {"VG", "VIP"}:
      return await interaction.response.send_message("Currency must be **VG** or **VIP**.", ephemeral=True)
    await start_gambling_game(interaction, self.game, wager, currency)

class HighLowChoiceView(discord.ui.View):
  def __init__(self, guild_id, user_id, wager, first, second, currency="VG"):
    super().__init__(timeout=60)
    self.guild_id, self.user_id, self.wager, self.currency = guild_id, user_id, wager, str(currency).upper()
    self.first, self.second = first, second

  async def choose(self, interaction, higher):
    if interaction.user.id != self.user_id:
      return await interaction.response.send_message("This game belongs to another player.", ephemeral=True)
    if self.second == self.first:
      profit, result = 0, "PUSH"
    else:
      won = self.second > self.first if higher else self.second < self.first
      mult = _gambling_multiplier(_gambling_state(self.guild_id, self.user_id)["wagered"])
      profit = int(round(self.wager * 0.75 * mult * _casino_multiplier(self.guild_id))) if won else -self.wager
      result = "WIN" if won else "LOSS"
    _pay_profit(self.guild_id, self.user_id, self.wager, profit, self.currency)
    vip = _skill_vip(self.guild_id, self.user_id, 150) if profit > 0 else 0
    _record_gambling(self.guild_id, self.user_id, self.wager, profit, vip, profit > 0, self.currency)
    save_item_data()
    await interaction.response.edit_message(
      content=f"**HIGH/LOW — {result}**\n\nFirst card: **{self.first}**\nSecond card: **{self.second}**\nNet: **{profit:+,} VG**\nVIP: **+{vip}**", view=None)

  async def on_timeout(self):
    _refund_timed_out_game(self)

  @discord.ui.button(label="Higher", style=discord.ButtonStyle.primary)
  async def higher(self, interaction, button): await self.choose(interaction, True)

  @discord.ui.button(label="Lower", style=discord.ButtonStyle.secondary)
  async def lower(self, interaction, button): await self.choose(interaction, False)

@GAMBLING_GROUP.command(name="status", description="View gambling limits, performance, and rank.")
async def gambling_status(interaction):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  data = _gambling_state(interaction.guild.id, interaction.user.id)
  wagered = int(data.get("wagered", 0))
  wagered_vg = int(data.get("wagered_vg", 0))
  wagered_vip = int(data.get("wagered_vip", 0))
  rank = "Novice"
  if data.get("games", 0) >= 100: rank = "Veteran"
  elif data.get("games", 0) >= 25: rank = "Regular"
  elif data.get("games", 0) >= 10: rank = "Apprentice"
  await interaction.response.send_message(
    f"**GAMBLING PROFILE**\n\n"
    f"Daily Wagered: **{wagered:,} total units**\nVG Wagered: **{wagered_vg:,}**\nVIP Wagered: **{wagered_vip:,}**\n"
    f"Payout Tier: **{int(_gambling_multiplier(wagered)*100)}%**\n"
    f"Net Result: **{int(data.get('profit',0)):+,} VG**\n"
    f"Wins: **{int(data.get('wins',0)):,} / {int(data.get('games',0)):,}**\n"
    f"VIP from Gambling Today: **{int(data.get('vip_earned',0)):,} / {VIP_DAILY_GAMBLING_CAP:,}**\n"
    f"Gambling Rank: **{rank}**", ephemeral=True)

@GAMBLING_GROUP.command(name="blackjack", description="Play decision-based Blackjack.")
@app_commands.describe(wager="Wager amount.", currency="VG or VIP.")
@app_commands.choices(currency=[app_commands.Choice(name="VG", value="VG"), app_commands.Choice(name="VIP", value="VIP")])
async def gambling_blackjack(interaction, wager: int, currency: app_commands.Choice[str]):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  if wager <= 0:
    return await interaction.response.send_message("Wager must be positive.", ephemeral=True)
  currency = currency.value
  ok, msg, _ = _pay_wager(interaction.guild.id, interaction.user.id, wager, currency)
  if not ok:
    return await interaction.response.send_message(msg, ephemeral=True)
  # Initial hand is intentionally deterministic from secure randomness; all
  # subsequent choices are made by the player through the view.
  deck = list(range(1, 14)) * 4
  random.SystemRandom().shuffle(deck)
  player = [deck.pop(), deck.pop()]
  dealer = [deck.pop(), deck.pop()]
  view = BlackjackView(interaction.guild.id, interaction.user.id, wager, player, dealer, deck, currency)
  await interaction.response.send_message(view.render(), view=view, ephemeral=True)

class BlackjackView(discord.ui.View):
  def __init__(self, guild_id, user_id, wager, player, dealer, deck, currency="VG"):
    super().__init__(timeout=120)
    self.guild_id, self.user_id, self.wager, self.currency = guild_id, user_id, wager, str(currency).upper()
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
    _pay_profit(self.guild_id, self.user_id, self.wager, profit, self.currency)
    _record_gambling(self.guild_id, self.user_id, self.wager, profit, vip, profit > 0, self.currency)
    save_item_data()
    await interaction.response.edit_message(
      content=(f"**BLACKJACK — {result}**\n\nYour hand: **{self.player}** → **{self.total(self.player)}**\n"
               f"Dealer hand: **{self.dealer}** → **{self.total(self.dealer)}**\n"
               f"Net result: **{profit:+,} {self.currency}**\nVIP earned: **+{vip}**"),
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

  async def on_timeout(self):
    _refund_timed_out_game(self)

  @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary)
  async def hit_button(self, interaction, button):
    await self.hit(interaction)

  @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
  async def stand_button(self, interaction, button):
    await self.stand(interaction)

@GAMBLING_GROUP.command(name="dice-poker", description="Play a decision-based Dice Poker ladder.")
@app_commands.describe(wager="Starting wager amount.", currency="VG or VIP.")
@app_commands.choices(currency=[app_commands.Choice(name="VG", value="VG"), app_commands.Choice(name="VIP", value="VIP")])
async def gambling_dice_poker(interaction, wager: int, currency: app_commands.Choice[str]):
  if interaction.guild is None: return await interaction.response.send_message("Server only.", ephemeral=True)
  if wager <= 0: return await interaction.response.send_message("Wager must be positive.", ephemeral=True)
  currency = currency.value
  ok, msg, _ = _pay_wager(interaction.guild.id, interaction.user.id, wager, currency)
  if not ok: return await interaction.response.send_message(msg, ephemeral=True)
  view = DicePokerView(interaction.guild.id, interaction.user.id, wager, 1, random.SystemRandom().sample(range(1,7), 5), currency)
  await interaction.response.send_message(view.render(), view=view, ephemeral=True)

class DicePokerView(discord.ui.View):
  def __init__(self, guild_id, user_id, wager, round_no, dice, currency="VG"):
    super().__init__(timeout=180)
    self.guild_id, self.user_id, self.wager, self.round_no, self.dice, self.currency = guild_id, user_id, wager, round_no, dice, str(currency).upper()
    self.finished = False

  def score(self):
    counts = {}
    for d in self.dice: counts[d] = counts.get(d, 0) + 1
    return max(counts.values())

  def render(self):
    ladder = [0.10, 0.25, 0.50, 1.00, 2.50]
    return (f"**DICE POKER — ROUND {self.round_no}/5**\n\nDice: **{self.dice}**\n"
            f"Current pool: **{int(self.wager*(1+ladder[self.round_no-1])):,} {self.currency}**\n"
            "Choose **Cash Out** or **Risk Next Round**.")

  async def cash(self, interaction):
    if interaction.user.id != self.user_id or self.finished: return
    self.finished = True
    profit = int(round(self.wager * [0.10,0.25,0.50,1.00,2.50][self.round_no-1]))
    mult = _gambling_multiplier(_gambling_state(self.guild_id, self.user_id)["wagered"])
    profit = int(round(profit * mult * _casino_multiplier(self.guild_id)))
    _pay_profit(self.guild_id, self.user_id, self.wager, profit, self.currency)
    vip = _skill_vip(self.guild_id, self.user_id, [0,25,50,100,250][self.round_no-1])
    _record_gambling(self.guild_id, self.user_id, self.wager, profit, vip, profit > 0, self.currency)
    save_item_data()
    await interaction.response.edit_message(content=f"**DICE POKER — CASHED OUT**\nProfit: **+{profit:,} {self.currency}**\nVIP: **+{vip}**", view=None)

  async def risk(self, interaction):
    if interaction.user.id != self.user_id or self.finished: return
    if self.round_no >= 5:
      return await self.cash(interaction)
    self.round_no += 1
    self.dice = random.SystemRandom().sample(range(1,7), 5)
    # Skill is expressed through the cash-out decision: the player controls risk.
    await interaction.response.edit_message(content=self.render(), view=self)

  async def on_timeout(self):
    _refund_timed_out_game(self)

  @discord.ui.button(label="Cash Out", style=discord.ButtonStyle.success)
  async def cash_button(self, interaction, button): await self.cash(interaction)

  @discord.ui.button(label="Risk Next Round", style=discord.ButtonStyle.danger)
  async def risk_button(self, interaction, button): await self.risk(interaction)

@GAMBLING_GROUP.command(name="memory", description="Play the Memory Table progression challenge.")
@app_commands.describe(wager="Starting wager amount.", currency="VG or VIP.")
@app_commands.choices(currency=[app_commands.Choice(name="VG", value="VG"), app_commands.Choice(name="VIP", value="VIP")])
async def gambling_memory(interaction, wager: int, currency: app_commands.Choice[str]):
  if interaction.guild is None: return await interaction.response.send_message("Server only.", ephemeral=True)
  if wager <= 0: return await interaction.response.send_message("Wager must be positive.", ephemeral=True)
  currency = currency.value
  ok, msg, _ = _pay_wager(interaction.guild.id, interaction.user.id, wager, currency)
  if not ok: return await interaction.response.send_message(msg, ephemeral=True)
  view = MemoryTableView(interaction.guild.id, interaction.user.id, wager, 1, currency)
  await interaction.response.send_message(view.render(), view=view, ephemeral=True)

class MemoryTableView(discord.ui.View):
  def __init__(self, guild_id, user_id, wager, level, currency="VG"):
    super().__init__(timeout=180)
    self.guild_id, self.user_id, self.wager, self.level, self.currency = guild_id, user_id, wager, level, str(currency).upper()
    self.target = [random.randint(1,9) for _ in range(level + 2)]
    self.phase = "remember"
    self.failed = False
    self.add_item(MemoryChoiceButton(self))

  def render(self):
    if self.phase == "remember":
      return f"**MEMORY TABLE — LEVEL {self.level}**\n\nMemorize: **{' '.join(map(str,self.target))}**\n\nWhen ready, press **I Remember**."
    return f"**MEMORY TABLE — LEVEL {self.level}**\n\nEnter the sequence in chat with `/economy gambling memory-answer` is not supported in this session; press a number sequence button in order."

  async def on_timeout(self):
    _refund_timed_out_game(self)

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
      _record_gambling(v.guild_id, v.user_id, v.wager, -v.wager, 0, False, v.currency)
      save_item_data()
      return await interaction.response.edit_message(content=f"**MEMORY TABLE — FAILED**\nCorrect sequence: **{' '.join(map(str,v.target))}**\nNet result: **-{v.wager:,} VG**", view=None)
    if v.level >= 5:
      v.stop()
      profit = int(round(v.wager * 1.0 * _gambling_multiplier(_gambling_state(v.guild_id,v.user_id)["wagered"]) * _casino_multiplier(v.guild_id)))
      _pay_profit(v.guild_id, v.user_id, v.wager, profit, v.currency)
      vip = _skill_vip(v.guild_id, v.user_id, 500)
      _record_gambling(v.guild_id, v.user_id, v.wager, profit, vip, True, v.currency)
      save_item_data()
      return await interaction.response.edit_message(content=f"**MEMORY TABLE — LEVEL 5 COMPLETE**\nProfit: **+{profit:,} {v.currency}**\nVIP: **+{vip}**", view=None)
    v.level += 1
    v.target = [random.randint(1,9) for _ in range(v.level + 2)]
    await interaction.response.edit_message(content=v.render(), view=v)

@GAMBLING_GROUP.command(name="high-low", description="Predict whether the next card is higher or lower.")
@app_commands.describe(wager="Wager amount.", currency="VG or VIP.")
@app_commands.choices(currency=[app_commands.Choice(name="VG", value="VG"), app_commands.Choice(name="VIP", value="VIP")])
@app_commands.choices(choice=[
  app_commands.Choice(name="Higher", value="higher"),
  app_commands.Choice(name="Lower", value="lower"),
])
async def gambling_high_low(interaction, wager: int, choice: app_commands.Choice[str], currency: app_commands.Choice[str]):
  if interaction.guild is None: return await interaction.response.send_message("Server only.", ephemeral=True)
  if wager <= 0: return await interaction.response.send_message("Wager must be positive.", ephemeral=True)
  currency = currency.value
  ok, msg, _ = _pay_wager(interaction.guild.id, interaction.user.id, wager, currency)
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
  _pay_profit(interaction.guild.id, interaction.user.id, wager, profit, currency)
  vip = _skill_vip(interaction.guild.id, interaction.user.id, 150) if profit > 0 else 0
  _record_gambling(interaction.guild.id, interaction.user.id, wager, profit, vip, profit > 0, currency)
  save_item_data()
  await interaction.response.send_message(
    f"**HIGH/LOW — {result}**\n\nFirst card: **{a}**\nSecond card: **{b}**\n"
    f"Your call: **{choice.name}**\nNet: **{profit:+,} VG**\nVIP: **+{vip}**", ephemeral=True)

@GAMBLING_GROUP.command(name="blackjack-duel", description="Challenge another player to a peer-to-peer Blackjack wager.")
@app_commands.describe(opponent="Player you are challenging.", wager="Wager amount.", currency="VG or VIP.")
@app_commands.choices(currency=[app_commands.Choice(name="VG", value="VG"), app_commands.Choice(name="VIP", value="VIP")])
async def gambling_blackjack_duel(interaction, opponent: discord.Member, wager: int, currency: app_commands.Choice[str]):
  if interaction.guild is None: return await interaction.response.send_message("Server only.", ephemeral=True)
  if opponent.bot or opponent.id == interaction.user.id:
    return await interaction.response.send_message("Choose another player.", ephemeral=True)
  if wager <= 0:
    return await interaction.response.send_message("Wager must be positive.", ephemeral=True)
  currency = currency.value
  available = (vip_points if currency == "VIP" else balance)
  if available(interaction.guild.id, interaction.user.id) < wager or available(interaction.guild.id, opponent.id) < wager:
    return await interaction.response.send_message(f"Both players must be able to cover the **{currency}** wager.", ephemeral=True)
  # Hold both wagers in the challenge record; no house edge.
  eco = guild_state(interaction.guild.id)
  duels = eco.setdefault("duels", {})
  duel_id = str(int(time.time()*1000))
  duels[duel_id] = {"challenger": interaction.user.id, "opponent": opponent.id, "wager": wager, "created": time.time(), "game": "blackjack_duel", "currency": currency}
  await interaction.response.send_message(
    f"**BLACKJACK DUEL CHALLENGE**\n{interaction.user.mention} challenged {opponent.mention} for **{wager:,} {currency}**.\n"
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
    currency = str(duel.get("currency", "VG")).upper()
    available = (vip_points if currency == "VIP" else balance)
    if available(self.guild_id, duel["challenger"]) < wager or available(self.guild_id, duel["opponent"]) < wager:
      return await interaction.response.send_message(f"One player no longer has enough {currency}.", ephemeral=True)
    if currency == "VIP":
      _set_vip_cards(self.guild_id, duel["challenger"], vip_points(self.guild_id, duel["challenger"]) - wager)
      _set_vip_cards(self.guild_id, duel["opponent"], vip_points(self.guild_id, duel["opponent"]) - wager)
    else:
      set_balance(self.guild_id, duel["challenger"], balance(self.guild_id, duel["challenger"]) - wager)
      set_balance(self.guild_id, duel["opponent"], balance(self.guild_id, duel["opponent"]) - wager)
    winner = random.choice([duel["challenger"], duel["opponent"]])
    loser = duel["opponent"] if winner == duel["challenger"] else duel["challenger"]
    pot = wager * 2
    if currency == "VIP":
      add_vip_points(self.guild_id, winner, pot)
    else:
      add_money(self.guild_id, winner, pot)
    _record_gambling(self.guild_id, winner, wager, wager, 200, True, currency)
    _record_gambling(self.guild_id, loser, wager, -wager, 0, False, currency)
    eco["duels"].pop(self.duel_id, None)
    save_item_data()
    await interaction.response.edit_message(content=f"**BLACKJACK DUEL COMPLETE**\nWinner: <@{winner}>\nPot: **{pot:,} {currency}**\nVIP: **+200**", view=None)
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
