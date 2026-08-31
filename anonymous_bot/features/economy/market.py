"""Stock market and GM market-event subsystem."""

import random
import time

import discord
from discord import app_commands

from ...state import is_staff
from ..items import save_item_data
from .core import *  # noqa: F401,F403
from .core import ECONOMY_GROUP, _set_vip_cards
from ..groups import ADMIN_ECONOMY_GROUP

def _stock_state(guild_id):
  eco = guild_state(guild_id)
  market = eco.setdefault(STOCK_MARKET_KEY, {})
  market.setdefault("index", 0.0)
  market.setdefault(STOCK_COMPANIES_KEY, {})
  market.setdefault(STOCK_HOLDINGS_KEY, {})
  # VIP shares are a separate share class. They never convert through VG.
  market.setdefault("vip_holdings", {})
  market.setdefault("cost_basis", {})
  market.setdefault("vip_cost_basis", {})
  market.setdefault(STOCK_HISTORY_KEY, [])
  market.setdefault(STOCK_LAST_UPDATE_KEY, time.time())
  market.setdefault(STOCK_LAST_DIVIDENDS_KEY, time.time())
  market.setdefault(STOCK_TRADE_FLOW_KEY, {})
  market.setdefault(STOCK_NEWS_KEY, [])
  market.setdefault(MARKET_EVENT_KEY, [])
  # Seed the intended starting market only when a guild has no companies.
  if not market[STOCK_COMPANIES_KEY]:
    for symbol, seed in DEFAULT_COMPANIES.items():
      market[STOCK_COMPANIES_KEY][symbol] = {
        **seed, "symbol": symbol, "last_change": 0.0,
        "vip_price": int(seed.get("vip_price", 1)),
        "vip_dividend": float(seed.get("vip_dividend", 0.0)),
        "history": [round(float(seed["price"]), 2)], "created": time.time()
      }
  return market

def _active_market_events(market):
  now = time.time()
  active = []
  changed = False
  for event in list(market.setdefault(MARKET_EVENT_KEY, [])):
    if float(event.get("expires_at", 0) or 0) > now:
      active.append(event)
    else:
      changed = True
  if changed:
    market[MARKET_EVENT_KEY] = active
  return active

def _stock_change():
  # Ordinary cycles are deliberately modest. Large crashes/booms are GM events,
  # not unexplained random spikes.
  return random.uniform(-3.0, 3.0)

def _apply_trade_pressure(company, shares, side):
  """Record signed demand. The pressure is applied on the next market cycle."""
  symbol = company["symbol"]
  flow = company.setdefault("_pending_flow", 0.0)
  # Log-volume impact uses a bounded square-root curve so bulk orders matter
  # without allowing one trade to destroy the market.
  impact = min(12.0, max(0.02, (max(1, shares) ** 0.5) * 0.08))
  company["_pending_flow"] = flow + (impact if side == "buy" else -impact)
  company["last_trade_at"] = time.time()

def _apply_market_change(market, change=None, source="automatic", affected=None, description=None):
  companies = market.setdefault(STOCK_COMPANIES_KEY, {})
  change = _stock_change() if change is None else float(change)
  market["index"] = max(-100000.0, min(100000.0, float(market.get("index", 0.0)) + change))
  affected = affected or {}
  for key, company in companies.items():
    old = max(0.01, float(company.get("price", 1.0)))
    symbol = str(company.get("symbol", key)).upper()
    flow = float(company.pop("_pending_flow", 0.0) or 0.0)
    # Supply/demand is the dominant short-term input; normal macro noise is small.
    pressure = max(-18.0, min(18.0, flow))
    macro = float(affected.get(symbol, 0.0))
    company_change = pressure + (change * 0.35) + random.uniform(-1.25, 1.25) + macro
    company["price"] = round(max(0.01, old * (1.0 + company_change / 100.0)), 2)
    company["last_change"] = round(company_change, 2)
    hist = company.setdefault("history", [])
    hist.append(company["price"])
    del hist[:-20]
  history = market.setdefault(STOCK_HISTORY_KEY, [])
  history.append({
    "time": time.time(), "change": round(change, 2),
    "index": round(market["index"], 2), "source": source,
    "description": description or ""
  })
  del history[:-50]
  market[STOCK_LAST_UPDATE_KEY] = time.time()

def _apply_dividends(guild_id, market):
  now = time.time()
  last = float(market.get(STOCK_LAST_DIVIDENDS_KEY, now))
  if now - last < DIVIDEND_SECONDS:
    return 0
  periods = min(30, int((now - last) // DIVIDEND_SECONDS))
  if periods <= 0:
    return 0
  total_paid = 0
  companies = market.get(STOCK_COMPANIES_KEY, {})
  holdings = market.get(STOCK_HOLDINGS_KEY, {})
  for symbol, company in companies.items():
    rate = max(0.0, float(company.get("dividend", 0.0) or 0.0))
    if rate <= 0:
      continue
    price = max(0.01, float(company.get("price", 0.0)))
    for uid, user_holdings in holdings.items():
      shares = int(user_holdings.get(company.get("symbol", symbol), 0) or 0)
      if shares <= 0:
        continue
      payout = int(round(price * shares * rate * periods))
      if payout:
        add_money(guild_id, int(uid), payout)
        total_paid += payout
  market["last_dividends"] = last + periods * DIVIDEND_SECONDS
  return total_paid

def _ensure_market_updated(guild_id):
  market = _stock_state(guild_id)
  now = time.time()
  last = float(market.get(STOCK_LAST_UPDATE_KEY, now))
  if now - last >= STOCK_UPDATE_SECONDS:
    steps = min(48, int((now - last) // STOCK_UPDATE_SECONDS))
    for _ in range(steps):
      if _active_market_events(market):
        apply_market_event_cycle(market)
      else:
        _apply_market_change(market, None, "automatic")
    _apply_dividends(guild_id, market)
    save_item_data()
  else:
    _apply_dividends(guild_id, market)
  return market

def _stock_holdings(market, user_id):
  return market.setdefault(STOCK_HOLDINGS_KEY, {}).setdefault(str(user_id), {})

def _vip_stock_holdings(market, user_id):
  return market.setdefault("vip_holdings", {}).setdefault(str(user_id), {})

def _find_company(market, symbol):
  symbol = symbol.strip().upper()
  return next(((key, data) for key, data in market[STOCK_COMPANIES_KEY].items()
               if data.get("symbol", key).upper() == symbol), None)

def _format_stock_company(data):
  return (f"**{data['name']}** (`{data['symbol']}`) — "
          f"**{float(data.get('price', 0)):.2f} VG/share** "
          f"({float(data.get('last_change', 0)):+.2f}%)")

def _market_sentiment(index):
  if index >= 10: return "BULLISH"
  if index <= -10: return "BEARISH"
  return "NEUTRAL"

@ECONOMY_GROUP.command(name="stocks", description="Open the campaign stock market.")
async def stocks(interaction: discord.Interaction):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  market = _ensure_market_updated(interaction.guild.id)
  companies = list(market[STOCK_COMPANIES_KEY].values())
  gainers = sorted(companies, key=lambda c: float(c.get("last_change", 0)), reverse=True)[:3]
  losers = sorted(companies, key=lambda c: float(c.get("last_change", 0)))[:3]
  holdings = _stock_holdings(market, interaction.user.id)
  portfolio = sum(int(round(float(c.get("price", 0)) * int(holdings.get(c.get("symbol"), 0) or 0))) for c in companies)
  lines = [
    "**STOCK MARKET**",
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    f"Market Index: **{float(market.get('index', 0)):+.2f}%** | Sentiment: **{_market_sentiment(float(market.get('index', 0)))}**",
    "",
    "**TOP GAINERS**",
  ]
  lines += [f"• {c['name']}  **{float(c.get('last_change',0)):+.1f}%**" for c in gainers] or ["• None"]
  lines += ["", "**TOP LOSERS**"]
  lines += [f"• {c['name']}  **{float(c.get('last_change',0)):+.1f}%**" for c in losers] or ["• None"]
  lines += ["", "**YOUR PORTFOLIO**", f"• Portfolio Value: **{portfolio:,} VG**",
            f"• Active Holdings: **{sum(1 for s in holdings.values() if int(s) > 0)}**",
            "", "Use the company selector in `/main → Economy → Stocks` for details."]
  await interaction.response.send_message("\n".join(lines), ephemeral=True)

STOCK_PAYMENT_CHOICES = [
  app_commands.Choice(name="VG", value="vg"),
  app_commands.Choice(name="VIP", value="vip"),
]

@ECONOMY_GROUP.command(name="invest", description="Buy shares in a campaign company.")
@app_commands.describe(company="Company ticker/symbol.", shares="Number of shares to buy.", payment="Payment method.")
@app_commands.choices(payment=STOCK_PAYMENT_CHOICES)
async def invest(interaction: discord.Interaction, company: str, shares: int, payment: app_commands.Choice[str] | None = None):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  if shares <= 0:
    return await interaction.response.send_message("Shares must be positive.", ephemeral=True)
  market = _ensure_market_updated(interaction.guild.id)
  found = _find_company(market, company)
  if not found:
    return await interaction.response.send_message("Company not found.", ephemeral=True)
  _, data = found
  symbol = data["symbol"]
  method = payment.value if payment else "vg"
  if method == "vip":
    # VIP shares are a genuinely separate share class. One VIP share costs the
    # company's configured vip_price and is paid only in VIP Points.
    vip_price = max(1, int(data.get("vip_price", 1) or 1))
    cost = vip_price * shares
    owned_vip = vip_points(interaction.guild.id, interaction.user.id)
    if owned_vip < cost:
      return await interaction.response.send_message(
        f"You need **{cost:,} VIP Points** to buy {shares:,} VIP shares of `{symbol}`. "
        f"You have **{owned_vip:,} VIP Points**.", ephemeral=True)
    set_vip = _set_vip_cards
    set_vip(interaction.guild.id, interaction.user.id, owned_vip - cost)
    holdings = _vip_stock_holdings(market, interaction.user.id)
    holdings[symbol] = int(holdings.get(symbol, 0) or 0) + shares
    basis = market.setdefault("vip_cost_basis", {}).setdefault(str(interaction.user.id), {})
    basis[symbol] = int(basis.get(symbol, 0) or 0) + cost
    _apply_trade_pressure(data, shares, "buy")
    save_item_data()
    await interaction.response.send_message(
      f"Bought **{shares:,} VIP shares** of **{data['name']} ({symbol})** at **{vip_price:,} VIP/share**. "
      f"VIP shares are separate from VG shares; no VG conversion was used. "
      f"Demand has been recorded for the next market cycle.", ephemeral=True)
    return

  price = max(0.01, float(data.get("price", 0)))
  cost = int(round(price * shares))
  if balance(interaction.guild.id, interaction.user.id) < cost:
    return await interaction.response.send_message(f"You need **{cost:,} VG**.", ephemeral=True)
  set_balance(interaction.guild.id, interaction.user.id, balance(interaction.guild.id, interaction.user.id) - cost)
  holdings = _stock_holdings(market, interaction.user.id)
  old_shares = int(holdings.get(symbol, 0) or 0)
  holdings[symbol] = old_shares + shares
  basis = market.setdefault("cost_basis", {}).setdefault(str(interaction.user.id), {})
  basis[symbol] = int(basis.get(symbol, 0) or 0) + cost
  _apply_trade_pressure(data, shares, "buy")
  save_item_data()
  await interaction.response.send_message(
    f"Bought **{shares:,} VG shares** of **{data['name']} ({symbol})** at **{price:,.2f} VG/share**. "
    f"Demand has been recorded for the next market cycle. Paid **{cost:,} VG**.", ephemeral=True)

@ECONOMY_GROUP.command(name="sell-stock", description="Sell shares you own.")
@app_commands.describe(company="Company ticker/symbol.", shares="Number of shares to sell.", payout="Payout method.")
@app_commands.choices(payout=STOCK_PAYMENT_CHOICES)
async def sell_stock(interaction: discord.Interaction, company: str, shares: int, payout: app_commands.Choice[str] | None = None):
  if interaction.guild is None:
    return await interaction.response.send_message("Server only.", ephemeral=True)
  if shares <= 0:
    return await interaction.response.send_message("Shares must be positive.", ephemeral=True)
  market = _ensure_market_updated(interaction.guild.id)
  found = _find_company(market, company)
  if not found:
    return await interaction.response.send_message("Company not found.", ephemeral=True)
  _, data = found
  symbol = data["symbol"]
  holdings = _stock_holdings(market, interaction.user.id)
  owned = int(holdings.get(symbol, 0))
  if owned < shares:
    return await interaction.response.send_message(f"You only own **{owned}** shares of `{symbol}`.", ephemeral=True)
  method = payout.value if payout else "vg"
  if method == "vip":
    vip_holdings = _vip_stock_holdings(market, interaction.user.id)
    vip_owned = int(vip_holdings.get(symbol, 0) or 0)
    if vip_owned < shares:
      return await interaction.response.send_message(
        f"You only own **{vip_owned:,} VIP shares** of `{symbol}`.", ephemeral=True)
    vip_price = max(1, int(data.get("vip_price", 1) or 1))
    proceeds = vip_price * shares
    basis = market.setdefault("vip_cost_basis", {}).setdefault(str(interaction.user.id), {})
    total_basis = int(basis.get(symbol, 0) or 0)
    basis_sold = int(round(total_basis * (shares / max(1, vip_owned))))
    basis[symbol] = max(0, total_basis - basis_sold)
    vip_holdings[symbol] = vip_owned - shares
    if vip_holdings[symbol] <= 0:
      vip_holdings.pop(symbol, None)
    add_vip_points(interaction.guild.id, interaction.user.id, proceeds)
    _apply_trade_pressure(data, shares, "sell")
    save_item_data()
    await interaction.response.send_message(
      f"Sold **{shares:,} VIP shares** of **{data['name']} ({symbol})** for **{proceeds:,} VIP Points**. "
      f"VIP share supply has been recorded for the next market cycle.", ephemeral=True)
    return

  proceeds = int(round(max(0.01, float(data.get("price", 0))) * shares))
  basis = market.setdefault("cost_basis", {}).setdefault(str(interaction.user.id), {})
  total_basis = int(basis.get(symbol, 0) or 0)
  basis_sold = int(round(total_basis * (shares / max(1, owned))))
  basis[symbol] = max(0, total_basis - basis_sold)
  holdings[symbol] = owned - shares
  if holdings[symbol] <= 0:
    holdings.pop(symbol, None)
  add_money(interaction.guild.id, interaction.user.id, proceeds)
  _apply_trade_pressure(data, shares, "sell")
  save_item_data()
  await interaction.response.send_message(
    f"Sold **{shares:,} VG shares** of **{data['name']} ({symbol})** for **{proceeds:,} VG**. "
    f"Supply has been recorded for the next market cycle.", ephemeral=True)

# Centralized GM market-event functions. Player-facing crashes/booms are always
# represented by an event/news record, never an unexplained random price shock.
def create_market_event(guild_id, name, description, deltas, duration_cycles=1):
  market = _stock_state(guild_id)
  event = {
    "id": str(int(time.time() * 1000)),
    "name": str(name)[:100],
    "description": str(description)[:1000],
    "deltas": {str(k).upper(): float(v) for k, v in deltas.items()},
    "cycles_remaining": max(1, int(duration_cycles)),
    "created_at": time.time(),
    "expires_at": time.time() + max(1, int(duration_cycles)) * STOCK_UPDATE_SECONDS,
  }
  market[MARKET_EVENT_KEY].append(event)
  market[STOCK_NEWS_KEY].append({"time": time.time(), "title": event["name"], "description": event["description"]})
  market[STOCK_NEWS_KEY] = market[STOCK_NEWS_KEY][-25:]
  return event

def apply_market_event_cycle(market):
  active = _active_market_events(market)
  deltas = {}
  descriptions = []
  for event in active:
    descriptions.append(event.get("name", "Market Event"))
    for symbol, delta in event.get("deltas", {}).items():
      deltas[symbol] = deltas.get(symbol, 0.0) + float(delta)
    event["cycles_remaining"] = max(0, int(event.get("cycles_remaining", 1)) - 1)
  _apply_market_change(
    market, 0.0, "gm_event", affected=deltas,
    description="; ".join(descriptions) if descriptions else None
  )
  for event in list(active):
    if int(event.get("cycles_remaining", 0)) <= 0:
      try: market[MARKET_EVENT_KEY].remove(event)
      except ValueError: pass

async def _admin_stock_move(interaction: discord.Interaction, change: float):
  """Legacy-compatible GM stock movement helper.

  Kept as a compatibility layer for the existing admin UI while the newer
  centralized GM Economy Events panel remains the preferred intervention path.
  """
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  change = float(change)
  if change < -100 or change > 100:
    return await interaction.response.send_message("Movement must be between -100 and +100 percent.", ephemeral=True)
  market = _stock_state(interaction.guild.id)
  _apply_market_change(market, change, "admin")
  save_item_data()
  await interaction.response.send_message(
    f"Stock market moved **{change:+.2f}%** by admin. Current index: **{market['index']:+.2f}%**.",
    ephemeral=True
  )


@ADMIN_ECONOMY_GROUP.command(name="stock-increase", description="Admin: increase the stock market.")
@app_commands.describe(amount="Percentage movement, from 0 to 100.")
async def stock_increase(interaction: discord.Interaction, amount: float):
  if amount < 0:
    return await interaction.response.send_message("Amount must not be negative.", ephemeral=True)
  await _admin_stock_move(interaction, amount)


@ADMIN_ECONOMY_GROUP.command(name="stock-decrease", description="Admin: decrease the stock market.")
@app_commands.describe(amount="Percentage movement, from 0 to 100.")
async def stock_decrease(interaction: discord.Interaction, amount: float):
  if amount < 0:
    return await interaction.response.send_message("Amount must not be negative.", ephemeral=True)
  await _admin_stock_move(interaction, -amount)


@ADMIN_ECONOMY_GROUP.command(name="stock-create", description="Admin: create a company. Prefer the GM Economy Events panel.")
@app_commands.describe(name="Company name.", symbol="Short stock symbol, 2-10 letters.", price="Starting price.")
async def stock_create(interaction: discord.Interaction, name: str, symbol: str, price: float):
  if interaction.guild is None or not is_staff(interaction):
    return await interaction.response.send_message("GM/admin only.", ephemeral=True)
  symbol = symbol.strip().upper()[:10]
  if len(symbol) < 2 or not symbol.isalpha() or price <= 0:
    return await interaction.response.send_message("Use a 2-10 letter symbol and a positive starting price.", ephemeral=True)
  market = _stock_state(interaction.guild.id)
  if _find_company(market, symbol):
    return await interaction.response.send_message("That stock symbol is already in use.", ephemeral=True)
  market[STOCK_COMPANIES_KEY][symbol] = {
    "name": name.strip()[:60], "symbol": symbol, "sector": "Unclassified",
    "risk": "Medium Risk", "price": round(price, 2), "vip_price": 1, "dividend": 0.0, "vip_dividend": 0.0,
    "last_change": 0.0, "history": [round(price, 2)], "created": time.time()
  }
  save_item_data()
  await interaction.response.send_message("Company created. Use **GM → Economy → Events** for future market interventions.", ephemeral=True)
