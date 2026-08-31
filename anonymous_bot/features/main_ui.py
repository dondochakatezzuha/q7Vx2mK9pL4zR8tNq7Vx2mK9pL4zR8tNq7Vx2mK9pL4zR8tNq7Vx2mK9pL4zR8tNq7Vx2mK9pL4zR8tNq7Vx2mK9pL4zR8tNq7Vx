"""Player-facing /main dashboard and navigation UI.

Keeps all /main views, selects, modals, and render helpers together so the
player dashboard can be maintained independently from GM/admin UI.
"""

import discord
from discord import app_commands
from .items import item_state, item_status_text, qualitative_stats, save_item_data
from .economy import (guild_state, balance, vip_points, _ensure_market_updated, _stock_holdings, _vip_stock_holdings, _find_company, _market_sentiment,
                      spend_economy_value, convert_vg_to_vip, convert_vip_to_vg, VG_PER_VIP_FACE_VALUE, VIP_EFFECTIVE_VG_VALUE, VIP_SHOP_KEY,
                      _gambling_state, _gambling_multiplier)
from .economy.gambling import GamblingWagerModal, CORE_GAMES
from .equipment import equipment_lines, combat_stats
from .gm_tools import _guild as _bounty_guild, _normalize_bounties, _bounty, BountyClaimModal, BountyClaimChoiceView


def _gm_state(guild_id):
    st = item_state(guild_id)
    g = st.setdefault("gm_tools", {})
    g.setdefault("bounties", [])
    g.setdefault("reputation", {})
    g.setdefault("parties", {})
    g.setdefault("party_members", {})
    return g


def _player_faction(guild_id, user_id):
    factions = guild_state(guild_id).get("factions", {})
    for name, data in factions.items():
        if user_id in data.get("members", []):
            return name, data
    return None, None


def _party(guild_id, user_id):
    g = _gm_state(guild_id)
    pid = g.get("party_members", {}).get(str(user_id))
    return g.get("parties", {}).get(pid) if pid else None


def _objectives(guild_id, user_id):
    value = item_state(guild_id).get("objectives", {}).get(str(user_id), [])
    if isinstance(value, dict):
        return [value] if value else []
    return value if isinstance(value, list) else []


def _player_bounties(guild_id, user_id):
    rows = _normalize_bounties(_gm_state(guild_id))
    return [b for b in rows if b.get("claimed_by") == user_id and b.get("status") == "pending"]


def _portfolio_value(guild_id, user_id):
    market = _ensure_market_updated(guild_id)
    holdings = _stock_holdings(market, user_id)
    companies = market.get("companies", {})
    total = 0
    count = 0
    for symbol, shares in holdings.items():
        try: shares = int(shares)
        except (TypeError, ValueError): continue
        if shares <= 0: continue
        found = _find_company(market, symbol)
        if found:
            company = found[1]
            total += int(round(float(company.get("price", 0)) * shares))
            count += shares
    return total, count


def _stats_text(guild_id, user_id):
    faction_name, faction_data = _player_faction(guild_id, user_id)
    party = _party(guild_id, user_id)
    dungeon = item_state(guild_id).get("dungeon", {}).get("players", {}).get(str(user_id), {})
    inv = item_state(guild_id).get("inventories", {}).get(str(user_id), [])
    profile = item_state(guild_id).get("players", {}).get(str(user_id), {})
    level = profile.get("level", profile.get("lvl", "Unknown"))
    character_class = profile.get("class", profile.get("race", "Unknown"))
    pending = _player_bounties(guild_id, user_id)
    active_bounties = [b for b in _normalize_bounties(_gm_state(guild_id)) if b.get("status") == "open"]
    portfolio, shares = _portfolio_value(guild_id, user_id)
    faction_text = faction_name or "No faction"
    faction_treasury = int((faction_data or {}).get("treasury", 0) or 0)
    party_name = party.get("name") if party else "No party"
    floor = dungeon.get("floor", 1)
    active_bounty = active_bounties[0].get("target", "None") if active_bounties else "None"

    return (
        "**REGNUM OF REGALIA**\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Welcome back, <@{user_id}>.\n\n"
        "**CHARACTER**\n"
        f"Class: **{character_class}**\n"
        "**ECONOMY**\n"
        f"{balance(guild_id, user_id):,} VG\n"
        f"{vip_points(guild_id, user_id):,} VIP Points\n"
        f"Stock Portfolio: **{portfolio:,} VG**\n"
        f"Faction Treasury: **{faction_treasury:,} VG**\n"
        f"Faction: **{faction_text}**\n\n"
        "**ADVENTURE**\n"
        f"Active Bounty: **{active_bounty}**\n"
        f"Party: **{party_name}**\n"
        f"Dungeon Floor: **{floor}**\n"
        f"Inventory: **{len(inv)} items**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Use the menu below to explore."
    )


from .companions import CompanionView
class ShopSelect(discord.ui.Select):
    def __init__(self, view):
        self.parent_view = view
        guild_id = view.guild_id
        user_id = view.user_id
        shop = guild_state(guild_id).get("shop", {})
        options = [discord.SelectOption(label=data["name"][:100], value=key, description=f"{int(data.get('price', 0)):,} VG") for key, data in list(shop.items())[:25]]
        if not options:
            options = [discord.SelectOption(label="Shop is empty", value="__empty", description="No items are available right now.")]
        super().__init__(placeholder="Choose an item to buy...", options=options)
        self.guild_id = guild_id; self.user_id = user_id

    async def callback(self, interaction):
        if not await self.parent_view.check(interaction):
            return
        key = self.values[0]
        if key == "__empty":
            return await interaction.response.send_message("The shop is empty.", ephemeral=True)
        shop = guild_state(self.guild_id)["shop"]
        entry = shop.get(key)
        if not entry:
            return await interaction.response.send_message("That shop listing is no longer available.", ephemeral=True)
        ok, payment_msg = spend_economy_value(self.guild_id, self.user_id, int(entry["price"]))
        if not ok:
            return await interaction.response.send_message(payment_msg, ephemeral=True)
        from .items import decorate_item, add_item, save_item_data
        from .economy import add_vip_points, vip_points
        if entry.get("type") == "vip_points":
            add_vip_points(self.guild_id, self.user_id, 1)
            save_item_data()
            await interaction.response.edit_message(content=f"**Purchased 1 VIP Point**\nPaid **{entry['price']:,} VG value**.\nVIP Points: **{vip_points(self.guild_id, self.user_id):,}**\nBalance: **{balance(self.guild_id, self.user_id):,} VG**", view=MainView(self.guild_id, self.user_id, "home"))
            return
        item = decorate_item(entry["item"])
        add_item(self.guild_id, self.user_id, item, held=True)
        shop.pop(key, None); save_item_data()
        await interaction.response.edit_message(content=f"**Purchased {item['name']}**\nPaid **{entry['price']:,} VG value.\nBalance: **{balance(self.guild_id, self.user_id):,} VG**", view=MainView(self.guild_id, self.user_id, "home"))


class VIPShopSelect(discord.ui.Select):
    def __init__(self, view):
        self.parent_view = view
        data = guild_state(view.guild_id).get(VIP_SHOP_KEY, {})
        options = [
            discord.SelectOption(
                label=str(entry.get("name", "Unknown"))[:100],
                value=str(key),
                description=f"{int(entry.get('price', 0))} VIP Card(s)"[:100],
            )
            for key, entry in list(data.items())[:25]
        ]
        if not options:
            options = [discord.SelectOption(label="VIP shop is empty", value="__empty", description="No VIP items are available.")]
        super().__init__(placeholder="Choose a VIP item...", options=options, row=1)

    async def callback(self, interaction):
        if not await self.parent_view.check(interaction):
            return
        key = self.values[0]
        if key == "__empty":
            return await interaction.response.send_message("The VIP shop is empty.", ephemeral=True)
        data = guild_state(self.parent_view.guild_id).get(VIP_SHOP_KEY, {})
        entry = data.get(key)
        if not entry:
            return await interaction.response.send_message("That VIP listing is no longer available.", ephemeral=True)
        cards_price = int(entry.get("price", 0))
        cost = cards_price * VIP_EFFECTIVE_VG_VALUE
        ok, payment_msg = spend_economy_value(
            self.parent_view.guild_id, self.parent_view.user_id, cost, vip_card_value=VIP_EFFECTIVE_VG_VALUE
        )
        if not ok:
            return await interaction.response.send_message(payment_msg, ephemeral=True)
        from .items import decorate_item, add_item
        item = decorate_item(entry["item"])
        add_item(self.parent_view.guild_id, self.parent_view.user_id, item, held=True)
        data.pop(key, None)
        save_item_data()
        self.parent_view.page = "vip_shop"
        self.parent_view.rebuild()
        await interaction.response.edit_message(
            content=f"Purchased **{item['name']}** from the VIP Shop.\nPrice: **{cards_price} VIP Card(s)**.\n{payment_msg}",
            view=self.parent_view
        )


class MainBountySelect(discord.ui.Select):
    def __init__(self, view):
        self.parent_view = view
        gm = _bounty_guild(view.guild_id)
        rows = [b for b in _normalize_bounties(gm) if b.get("status") == "open"]
        options = []
        for b in rows[:25]:
            options.append(discord.SelectOption(
                label=f"{b.get('target','Unknown')} — {int(b.get('reward_vip',0) if str(b.get('reward_currency','vg')).lower() == 'vip' else b.get('reward_vg',0)):,} {str(b.get('reward_currency','vg')).upper()}"[:100],
                value=str(b.get("id")),
                description=f"{('PLAYER' if b.get('target_type') == 'player' else 'NPC')} • Open"[:100],
            ))
        if not options:
            options = [discord.SelectOption(label="No open bounties", value="__none", description="There are no bounties available.")]
        super().__init__(placeholder="Choose a bounty...", options=options, row=1)

    async def callback(self, interaction):
        if not await self.parent_view.check(interaction): return
        self.parent_view.selected_bounty_id = None if self.values[0] == "__none" else self.values[0]
        self.parent_view.rebuild()
        await interaction.response.edit_message(**self.parent_view.render())


class MainBountyViewMixin:
    selected_bounty_id = None

    def add_bounty_controls(self):
        self.add_item(MainBountySelect(self))

        details = discord.ui.Button(label="View Details", style=discord.ButtonStyle.primary, row=2)
        claim = discord.ui.Button(label="Accept Bounty", style=discord.ButtonStyle.success, row=2)

        async def details_callback(interaction):
            if not await self.check(interaction): return
            b = _bounty(_bounty_guild(self.guild_id), self.selected_bounty_id or "")
            if not b:
                return await interaction.response.send_message("Select an open bounty first.", ephemeral=True)
            desc = str(b.get("description") or "No description provided.")[:3500]
            text = (
                f"**BOUNTY — {b.get('target','Unknown')}**\n\n"
                f"**Reward:** {int(b.get('reward_vip',0) if str(b.get('reward_currency','vg')).lower() == 'vip' else b.get('reward_vg',0)):,} {str(b.get('reward_currency','vg')).upper()}\n"
                f"**Type:** {'Player' if b.get('target_type') == 'player' else 'NPC'}\n"
                f"**Status:** {str(b.get('status','open')).title()}\n\n"
                f"**Description**\n{desc}"
            )
            self._bounty_detail = True
            await interaction.response.edit_message(content=text, view=self)

        async def claim_callback(interaction):
            if not await self.check(interaction): return
            b = _bounty(_bounty_guild(self.guild_id), self.selected_bounty_id or "")
            if not b or b.get("status") != "open":
                return await interaction.response.send_message("Select an open bounty first.", ephemeral=True)
            if b.get("target_type") == "player" and b.get("target_user_id") == interaction.user.id:
                return await interaction.response.send_message("You cannot accept a bounty placed on yourself.", ephemeral=True)
            await interaction.response.send_message(
                f"How should **{b.get('target','Unknown')}** be claimed?",
                view=BountyClaimChoiceView(self.guild_id, b.get("id")),
                ephemeral=True,
            )

        details.callback = details_callback
        claim.callback = claim_callback
        self.add_item(details)
        self.add_item(claim)



class BankAmountModal(discord.ui.Modal):
    def __init__(self, main_view, direction):
        super().__init__(title="Convert VG to VIP" if direction == "to_vip" else "Convert VIP to VG")
        self.main_view = main_view
        self.direction = direction
        label = "VG amount" if direction == "to_vip" else "VIP Card amount"
        self.amount = discord.ui.TextInput(label=label, placeholder="Enter an amount", required=True, max_length=20)
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        if not await self.main_view.check(interaction):
            return
        try:
            amount = int(str(self.amount.value).replace(",", "").strip())
        except ValueError:
            return await interaction.response.send_message("Enter a whole number.", ephemeral=True)
        if self.direction == "to_vip":
            return await interaction.response.send_message(
                "VIP Points are performance-based and cannot be purchased with VG.",
                ephemeral=True
            )
        return await interaction.response.send_message(
            "VIP Points are non-cash progression and cannot be converted into VG.",
            ephemeral=True
        )
        if not ok:
            return await interaction.response.send_message(message, ephemeral=True)
        save_item_data()
        self.main_view.page = "bank"
        self.main_view.rebuild()
        await interaction.response.edit_message(**self.main_view.render())



class StockCompanySelect(discord.ui.Select):
    def __init__(self, view):
        self.parent_view = view
        market = _ensure_market_updated(view.guild_id)
        options = []
        for key, c in list(market.get("companies", {}).items())[:25]:
            options.append(discord.SelectOption(
                label=f"{c.get('name','Unknown')} ({c.get('symbol',key)})"[:100],
                value=str(c.get("symbol", key)),
                description=f"{float(c.get('price',0)):,.2f} VG/share • {float(c.get('last_change',0)):+.2f}%"
            ))
        if not options:
            options = [discord.SelectOption(label="No companies", value="__none", description="The market has no listed companies.")]
        super().__init__(placeholder="Select a company to inspect...", options=options, row=1)

    async def callback(self, interaction):
        if not await self.parent_view.check(interaction): return
        if self.values[0] == "__none":
            return await interaction.response.send_message("No companies are listed.", ephemeral=True)
        self.parent_view.selected_stock = self.values[0]
        self.parent_view.rebuild()
        await interaction.response.edit_message(**self.parent_view.render())

class GamblingGameSelect(discord.ui.Select):
    def __init__(self, view):
        self.parent_view = view
        options = [
            discord.SelectOption(label=data["name"], value=key, description=f"{data['profile']} • Max {data['entry_max']:,} VG")
            for key, data in CORE_GAMES.items()
        ]
        super().__init__(placeholder="Select a game...", options=options, row=1)
    async def callback(self, interaction):
        if not await self.parent_view.check(interaction): return
        game_key = self.values[0]
        self.parent_view.selected_game = game_key
        await interaction.response.send_modal(GamblingWagerModal(game_key))

class EconomyActionButton(discord.ui.Button):
    def __init__(self, main_view, label, action, row):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=row)
        self.main_view = main_view
        self.action = action

    async def callback(self, interaction):
        if not await self.main_view.check(interaction):
            return
        if self.action == "bank":
            self.main_view.navigate("bank")
            return await interaction.response.edit_message(**self.main_view.render())
        if self.action == "shop":
            self.main_view.navigate("shop")
            return await interaction.response.edit_message(**self.main_view.render())
        if self.action in {"stocks", "invest", "sell", "gamble", "gambling", "history"}:
            # Keep every Main-panel action inside the existing ephemeral panel.
            # Navigation also records the previous page so Back returns here.
            self.main_view.navigate(self.action)
            return await interaction.response.edit_message(**self.main_view.render())

class MainView(MainBountyViewMixin, discord.ui.View):
    def __init__(self, guild_id, user_id, page="home", history=None):
        super().__init__(timeout=None)
        self.guild_id = guild_id; self.user_id = user_id; self.page = page
        self.history = list(history or [])
        self.selected_bounty_id = None
        self.selected_stock = None
        self.selected_game = None
        self._bounty_detail = False
        self.rebuild()

    def navigate(self, page):
        """Navigate to a page while remembering the page we came from.

        Home is a true root destination: selecting Main clears the navigation
        history so Back cannot immediately send the player back into the page
        they just left.
        """
        if page == self.page:
            return
        if page == "home":
            self.history.clear()
        else:
            self.history.append(self.page)
        self.page = page
        self.selected_bounty_id = None
        self.selected_stock = None
        self.selected_game = None
        self._bounty_detail = False
        self.rebuild()

    def go_back(self):
        """Return to the immediately previous page, falling back to home."""
        if self.history:
            self.page = self.history.pop()
        else:
            self.page = "home"
        self.selected_bounty_id = None
        self.selected_stock = None
        self.selected_game = None
        self._bounty_detail = False
        self.rebuild()

    async def check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This menu belongs to another player.", ephemeral=True); return False
        return True

    def home_embed(self):
        gid = self.guild_id; uid = self.user_id
        faction_name, faction_data = _player_faction(gid, uid)
        party = _party(gid, uid)
        dungeon = item_state(gid).get("dungeon", {}).get("players", {}).get(str(uid), {})
        inv = item_state(gid).get("inventories", {}).get(str(uid), [])
        profile = item_state(gid).get("players", {}).get(str(uid), {})
        character_class = profile.get("class", profile.get("race", "Unknown"))
        level = profile.get("level", profile.get("lvl", "Unknown"))
        portfolio, shares = _portfolio_value(gid, uid)
        faction_treasury = int((faction_data or {}).get("treasury", 0) or 0)
        active = [b for b in _normalize_bounties(_gm_state(gid)) if b.get("status") == "open"]
        active_bounty = active[0].get("target", "None") if active else "None"
        embed = discord.Embed(
            title="REGNUM OF REGALIA",
            description=f"**Character Dashboard**\nWelcome back, <@{uid}>.\n\nUse the navigation panels below to manage your character.",
            color=discord.Color.dark_grey()
        )
        embed.add_field(name="CHARACTER PROFILE", value=(
            f"Class: **{character_class}**\n"
            f"Level: **{level}**\n"
            f"Faction: **{faction_name or 'No faction'}**\n"
            f"Party: **{party.get('name') if party else 'No party'}**"
        ), inline=True)
        embed.add_field(name="FINANCES", value=(
            f"VG Balance: **{balance(gid, uid):,} VG**\n"
            f"VIP Points: **{vip_points(gid, uid):,}**\n"
            f"Stock Portfolio: **{portfolio:,} VG**\n"
            f"Faction Treasury: **{faction_treasury:,} VG**"
        ), inline=True)
        embed.add_field(name="ADVENTURE", value=(
            f"Dungeon Floor: **{dungeon.get('floor', 1)}**\n"
            f"Inventory: **{len(inv)} items**\n"
            f"Active Bounty: **{active_bounty}**\n"
            f"Shares Owned: **{shares:,}**"
        ), inline=True)
        embed.add_field(name="CAMPAIGN STATUS", value=(
            "Your profile is synchronized with the campaign database.\n"
            "Select a section below to inspect or manage your character."
        ), inline=False)
        embed.set_footer(text="Regnum of Regalia • Character Control Panel")
        return embed

    def render(self):
        if self.page == "home":
            return {"embed": self.home_embed(), "content": None, "view": self}
        return {"content": self.content(), "embed": None, "view": self}

    def content(self):
        uid = self.user_id; gid = self.guild_id
        if self.page == "home":
            return _stats_text(gid, uid)
        if self.page == "inventory":
            inv = item_state(gid).get("inventories", {}).get(str(uid), [])
            lines = [f"**{x.get('name', x.get('item',{}).get('name','Unknown'))}**" for x in inv[-50:]]
            return "**INVENTORY**\n\n" + ("\n".join(lines) if lines else "No items.")
        if self.page == "equipment":
            stats = combat_stats(gid, uid)
            return "**EQUIPMENT**\n\n" + equipment_lines(gid, uid) + "\n\n**Combat Stats**\n" + "\n".join(f"{label}: **{value}**" for label,value in qualitative_stats(stats))
        if self.page == "reputation":
            rep = _gm_state(gid).get("reputation", {}).get(str(uid), {})
            return "**REPUTATION**\n\n" + ("\n".join(f"**{faction}**: {int(value):+d}" for faction,value in rep.items()) if rep else "No recorded reputation.")
        if self.page == "objectives":
            objs = _objectives(gid, uid)
            lines = [f"**{o.get('title', o.get('name','Objective'))}** — {o.get('status','active')}" if isinstance(o,dict) else f"**{o}**" for o in objs]
            return "**OBJECTIVES**\n\n" + ("\n".join(lines) if lines else "No active objectives.")
        if self.page == "economy":
            market = _ensure_market_updated(gid)
            portfolio, shares = _portfolio_value(gid, uid)
            holdings = _stock_holdings(market, uid)
            companies = market.get("companies", {})
            gainers = sorted(companies.values(), key=lambda c: float(c.get("last_change", 0)), reverse=True)[:3]
            losers = sorted(companies.values(), key=lambda c: float(c.get("last_change", 0)))[:3]
            basis = market.get("cost_basis", {}).get(str(uid), {})
            net = 0
            for symbol, amount in holdings.items():
                found = _find_company(market, symbol)
                if found:
                    net += int(round(float(found[1].get("price", 0)) * int(amount))) - int(basis.get(symbol, 0) or 0)
            sentiment = _market_sentiment(float(market.get("index", 0)))
            return (
                "**ECONOMY**\n\n"
                f"VG Balance: **{balance(gid, uid):,} VG**\n"
                f"VIP Points: **{vip_points(gid, uid):,}**\n\n"
                "**STOCK MARKET**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Market Index: **{float(market.get('index',0)):+.2f}%** | Sentiment: **{sentiment}**\n\n"
                "**TOP GAINERS**\n" +
                ("\n".join(f"• {c.get('name','Unknown')}  **{float(c.get('last_change',0)):+.1f}%**" for c in gainers) or "• None") +
                "\n\n**TOP LOSERS**\n" +
                ("\n".join(f"• {c.get('name','Unknown')}  **{float(c.get('last_change',0)):+.1f}%**" for c in losers) or "• None") +
                f"\n\n**YOUR PORTFOLIO**\n• Portfolio Value: **{portfolio:,} VG**\n"
                f"• Net Gain/Loss: **{net:+,} VG**\n• Active Holdings: **{sum(1 for x in holdings.values() if int(x)>0)}**\n\n"
                "**GAMBLING**\n"
                f"Daily Wagered: **{int(_gambling_state(gid,uid).get('wagered',0)):,} VG**\n"
                f"Payout Tier: **{int(_gambling_multiplier(int(_gambling_state(gid,uid).get('wagered',0)))*100)}%**"
            )
        if self.page == "stocks":
            market = _ensure_market_updated(gid)
            if self.selected_stock:
                found = _find_company(market, self.selected_stock)
                if found:
                    _, c = found
                    holdings = _stock_holdings(market, uid)
                    vip_holdings = _vip_stock_holdings(market, uid)
                    owned = int(holdings.get(c.get("symbol"), 0) or 0)
                    vip_owned = int(vip_holdings.get(c.get("symbol"), 0) or 0)
                    basis = int(market.get("cost_basis", {}).get(str(uid), {}).get(c.get("symbol"), 0) or 0)
                    vip_basis = int(market.get("vip_cost_basis", {}).get(str(uid), {}).get(c.get("symbol"), 0) or 0)
                    value = int(round(float(c.get("price",0))*owned))
                    vip_price = max(1, int(c.get("vip_price", 1) or 1))
                    history = c.get("history", [])[-5:]
                    ticks = []
                    for i in range(1, len(history)):
                        prev = max(0.01,float(history[i-1]))
                        ticks.append(f"{(float(history[i])/prev-1)*100:+.1f}%")
                    return (
                        f"**{c.get('name','Unknown').upper()}** (`{c.get('symbol','')}`)\n\n"
                        f"Current Price: **{float(c.get('price',0)):,.2f} VG** ({float(c.get('last_change',0)):+.1f}%)\n"
                        f"Your VG Position: **{owned:,} shares** (Value: **{value:,} VG**)\n"
                        f"Sector & Risk Profile: **{c.get('sector','Unclassified')} | {c.get('risk','Medium Risk')}**\n"
                        f"Dividend: **{float(c.get('dividend',0))*100:.2f}% / 24h**\n"
                        f"Cost Basis: **{basis:,} VG**\n"
                        f"VIP Share Class: **{vip_price:,} VIP/share**\n"
                        f"Your VIP Position: **{vip_owned:,} VIP shares** (Value: **{vip_owned * vip_price:,} VIP Points**)\n"
                        f"VIP Cost Basis: **{vip_basis:,} VIP Points**\n"
                        f"Recent History: **{' | '.join(ticks[-5:]) if ticks else 'No history'}**\n\n"
                        "VG shares and VIP shares are separate. Buying VIP shares uses VIP Points directly; there is no VG conversion. "
                        "Player trade pressure is applied on the next market cycle."
                    )
            companies = market.get("companies", {})
            return "**STOCK MARKET**\n\nSelect a company below to inspect price, position, risk, dividend, and recent ticks."
        if self.page == "gambling":
            data = _gambling_state(gid, uid)
            wagered = int(data.get("wagered",0))
            return (
                "**GAMBLING HALL**\n\n"
                f"Daily Wagered: **{wagered:,} VG**\n"
                f"Current Payout Tier: **{int(_gambling_multiplier(wagered)*100)}%**\n"
                f"Net Result Today: **{int(data.get('profit',0)):+,} VG**\n"
                f"VIP Earned Today: **{int(data.get('vip_earned',0)):,}**\n\n"
                "**CORE GAMES**\n" +
                "\n".join(f"• **{x['name']}** — {x['profile']} — max {x['entry_max']:,} VG" for x in CORE_GAMES.values()) +
                "\n\nVIP is earned from achievements and performance, not purchased with VG."
            )
        if self.page == "stock_detail":
            self.page = "stocks"
            return self.content()
        if self.page == "history":
            history = item_state(gid).get("economy", {}).get("transactions", {}).get(str(uid), [])
            lines = []
            for entry in history[-10:]:
                if isinstance(entry, dict):
                    lines.append(str(entry.get("text", entry.get("type", "Transaction"))))
                else:
                    lines.append(str(entry))
            return "**TRANSACTION HISTORY**\n\n" + ("\n".join(lines) if lines else "No transactions recorded.")
        if self.page == "bank":
            vg = balance(gid, uid)
            vip = vip_points(gid, uid)
            return (
                "**BANK**\n\n"
                f"VG Balance: **{vg:,} VG**\n"
                f"VIP Cards: **{vip:,}**\n\n"
                f"Conversion Rate: **{VG_PER_VIP_FACE_VALUE:,} VG = 1 VIP Card**\n"
                f"Reverse Rate: **1 VIP Card = {VG_PER_VIP_FACE_VALUE:,} VG**\n\n"
                "Normal Shop: **1 VIP Card = 60,000 VG**.\n"
                "VIP Shop: **1 VIP Card = 300,000 VG purchasing power**.\n"
                "The Bank always converts at the normal 60,000 VG rate.\n"
            )
        if self.page == "bounties":
            # The main dashboard should show the actual campaign bounty board,
            # not only bounties this player has already claimed.
            gm = _bounty_guild(gid)
            rows = [b for b in _normalize_bounties(gm) if b.get("status") in {"open", "pending"}]
            lines = []
            for b in rows[:12]:
                status = str(b.get("status", "open")).title()
                target_type = "Player" if b.get("target_type") == "player" else "NPC"
                marker = " ← selected" if str(b.get("id")) == str(self.selected_bounty_id) else ""
                lines.append(f"**{b.get('target', 'Unknown')}** — **{int(b.get('reward_vip', 0) if str(b.get('reward_currency','vg')).lower() == 'vip' else b.get('reward_vg', 0)):,} {str(b.get('reward_currency','vg')).upper()}** · {target_type} · {status}{marker}")
            claims = _player_bounties(gid, uid)
            result = "**BOUNTY BOARD**\n\n" + ("\n".join(lines) if lines else "No active bounties right now.")
            if claims:
                claim_lines = [f"**{b.get('target', 'Unknown')}** — **{int(b.get('reward_vip', 0) if str(b.get('reward_currency','vg')).lower() == 'vip' else b.get('reward_vg', b.get('reward', 0))):,} {str(b.get('reward_currency','vg')).upper()}** — Pending" for b in claims[:5]]
                result += "\n\n**YOUR PENDING CLAIMS**\n" + "\n".join(claim_lines)
            return result
        if self.page == "party":
            party = _party(gid, uid)
            if not party: return "**PARTY**\n\nYou are not currently in a party."
            roster = "\n".join(f"<@{m}>" for m in party.get("members", []))
            return f"**{party.get('name','Party')}**\n\nLeader: <@{party.get('leader_id')}>\n\n**Members**\n{roster}"
        if self.page == "faction":
            name, data = _player_faction(gid, uid)
            if not name: return "**FACTION**\n\nYou are not currently in a faction."
            return f"**{name}**\n\nMembers: **{len(data.get('members', []))}**\nTreasury: **{data.get('treasury', 0):,}** VG"
        if self.page == "lore":
            return "**LORE**\n\nUse `/lore ask` when you want the archive to explain a character, event, location, or other campaign detail. The answer will separate confirmed information from uncertainty."
        if self.page == "attendance":
            gm = _gm_state(gid)
            record = gm.get("attendance", {}).get(str(uid), {}) if isinstance(gm.get("attendance"), dict) else {}
            session = gm.get("current_session", {}) if isinstance(gm.get("current_session"), dict) else {}
            return ("**ATTENDANCE**\n\n"
                    f"Game status: **{'Live' if gm.get('game_started') else 'Offline'}**\n"
                    f"Your status: **{record.get('status', 'Not checked in').replace('_', ' ').title()}**\n"
                    f"Session: **{session.get('title') or 'No active session'}**")
        if self.page == "companion":
            try:
                from .companions import _companion, companion_text
                c = _companion(gid, uid)
                if not c.get("chosen"):
                    return "**COMPANION**\n\nNo companion has been chosen yet. Open `/companion hub` to choose one."
                return companion_text(c)
            except Exception:
                return "**COMPANION**\n\nUse `/companion hub` to manage your companion."
        if self.page == "shop":
            shop = guild_state(gid).get("shop", {})
            lines = [f"**{v['name']}** — **{v['price']:,} VG**" for v in shop.values()]
            return ("**SHOP**\n\n" + ("\n".join(lines) if lines else "The shop is empty.") +
                    f"\n\nBalance: **{balance(gid,uid):,} VG**\nVIP Cards: **{vip_points(gid,uid):,}**\n\nUse the buttons below to buy from the normal shop or open the VIP Shop.")
        if self.page == "vip_shop":
            data = guild_state(gid).get(VIP_SHOP_KEY, {})
            lines = [f"**{v.get('name','Unknown')}** — **{int(v.get('price',0))} VIP Card(s)**" for v in data.values()]
            return ("**VIP SHOP**\n\n" + ("\n".join(lines) if lines else "The VIP shop is empty.") +
                    f"\n\nYour VIP Cards: **{vip_points(gid,uid):,}**\nEach VIP Card has **{VIP_EFFECTIVE_VG_VALUE:,} VG** of VIP Shop purchasing power.")
        return _stats_text(gid, uid)

    def add_economy_controls(self):
        self.add_item(EconomyActionButton(self, "Stocks", "stocks", 1))
        self.add_item(EconomyActionButton(self, "Gambling", "gambling", 1))
        self.add_item(EconomyActionButton(self, "Invest", "invest", 2))
        self.add_item(EconomyActionButton(self, "Sell Stocks", "sell", 2))
        self.add_item(EconomyActionButton(self, "Shop", "shop", 2))
        self.add_item(EconomyActionButton(self, "Transaction History", "history", 2))
        self.add_item(EconomyActionButton(self, "Bank", "bank", 2))

    def add_bank_controls(self):
        info = discord.ui.Button(label="VIP is Performance-Based", style=discord.ButtonStyle.secondary, row=1, disabled=True)
        self.add_item(info)

    def rebuild(self):
        self.clear_items()
        if self.page == "home":
            self.add_item(MainManagementSelect(self))
            self.add_item(MainWorldSelect(self))
        else:
            self.add_item(MainBackSelect(self))
            self.add_item(MainBackButton(self))
            if self.page == "bounties":
                self.add_bounty_controls()
            elif self.page == "economy":
                self.add_economy_controls()
            elif self.page == "stocks":
                self.add_item(StockCompanySelect(self))
            elif self.page == "gambling":
                self.add_item(GamblingGameSelect(self))
            elif self.page == "bank":
                self.add_bank_controls()
            elif self.page == "companion":
                self.add_item(MainCompanionButton(self))
            elif self.page == "shop":
                self.add_item(ShopSelect(self))
                self.add_item(MainShopButton(self, "VIP Shop", "vip_shop", 2))
            elif self.page == "vip_shop":
                self.add_item(VIPShopSelect(self))
        refresh = discord.ui.Button(label="Refresh", style=discord.ButtonStyle.secondary, row=4)

        async def refresh_callback(interaction):
            if not await self.check(interaction):
                return
            self.selected_bounty_id = None
            self._bounty_detail = False
            self.rebuild()
            await interaction.response.edit_message(**self.render())

        refresh.callback = refresh_callback
        self.add_item(refresh)

class MainBackButton(discord.ui.Button):
    def __init__(self, view):
        super().__init__(label="Back", style=discord.ButtonStyle.secondary, row=3)
        self.parent_view = view
    async def callback(self, interaction):
        if not await self.parent_view.check(interaction):
            return
        self.parent_view.go_back()
        await interaction.response.edit_message(**self.parent_view.render())


class MainShopButton(discord.ui.Button):
    def __init__(self, view, label, page, row):
        super().__init__(label=label, style=discord.ButtonStyle.primary, row=row)
        self.parent_view = view
        self.page = page
    async def callback(self, interaction):
        if not await self.parent_view.check(interaction):
            return
        self.parent_view.navigate(self.page)
        await interaction.response.edit_message(**self.parent_view.render())


class MainCompanionButton(discord.ui.Button):
    def __init__(self, view):
        super().__init__(label="Open Companion Hub", style=discord.ButtonStyle.primary, row=2)
        self.parent_view = view
    async def callback(self, interaction):
        if not await self.parent_view.check(interaction): return
        # Replace the current /main panel instead of creating a second message.
        c = __import__("anonymous_bot.features.companions", fromlist=["_companion", "companion_text"])
        companion = c._companion(self.parent_view.guild_id, self.parent_view.user_id)
        await interaction.response.edit_message(
            content=c.companion_text(companion),
            embed=None,
            view=CompanionView(self.parent_view.guild_id, self.parent_view.user_id)
        )


class MainManagementSelect(discord.ui.Select):
    def __init__(self, view):
        self.parent_view = view
        options = [
            discord.SelectOption(label="Economy", value="economy", description="Balance, stocks, gambling, and transactions."),
            discord.SelectOption(label="Bank", value="bank", description="Convert VG and VIP holdings."),
            discord.SelectOption(label="Inventory", value="inventory", description="Inspect carried items and storage."),
            discord.SelectOption(label="Equipment", value="equipment", description="Inspect equipped gear and combat statistics."),
            discord.SelectOption(label="Reputation", value="reputation", description="Review faction and world reputation."),
            discord.SelectOption(label="Objectives", value="objectives", description="Review active campaign objectives."),
            discord.SelectOption(label="Bounties", value="bounties", description="Review available campaign bounties."),
        ]
        super().__init__(placeholder="Management and Progression", options=options, row=0)

    async def callback(self, interaction):
        if not await self.parent_view.check(interaction): return
        self.parent_view.navigate(self.values[0])
        await interaction.response.edit_message(**self.parent_view.render())


class MainWorldSelect(discord.ui.Select):
    def __init__(self, view):
        self.parent_view = view
        options = [
            discord.SelectOption(label="Party", value="party", description="Review your current party and members."),
            discord.SelectOption(label="Faction", value="faction", description="Review faction membership and treasury."),
            discord.SelectOption(label="Companion", value="companion", description="Manage your chosen companion."),
            discord.SelectOption(label="Shop", value="shop", description="Browse the campaign shop."),
            discord.SelectOption(label="Lore", value="lore", description="Review campaign lore and archives."),
            discord.SelectOption(label="Attendance", value="attendance", description="Review session and attendance status."),
        ]
        super().__init__(placeholder="World and Campaign", options=options, row=1)

    async def callback(self, interaction):
        if not await self.parent_view.check(interaction): return
        self.parent_view.navigate(self.values[0])
        await interaction.response.edit_message(**self.parent_view.render())


class MainBackSelect(discord.ui.Select):
    def __init__(self, view):
        self.parent_view = view
        super().__init__(placeholder="Navigate...", options=[
            discord.SelectOption(label="Main", value="home"),
            discord.SelectOption(label="Economy", value="economy"),
            discord.SelectOption(label="Bank", value="bank"),
            discord.SelectOption(label="Inventory", value="inventory"),
            discord.SelectOption(label="Equipment", value="equipment"),
            discord.SelectOption(label="Reputation", value="reputation"),
            discord.SelectOption(label="Objectives", value="objectives"),
            discord.SelectOption(label="Bounties", value="bounties"),
            discord.SelectOption(label="Party", value="party"),
            discord.SelectOption(label="Faction", value="faction"),
            discord.SelectOption(label="Companion", value="companion"),
            discord.SelectOption(label="Shop", value="shop"),
            discord.SelectOption(label="Lore", value="lore"),
            discord.SelectOption(label="Attendance", value="attendance"),
        ], row=0)

    async def callback(self, interaction):
        if not await self.parent_view.check(interaction): return
        self.parent_view.navigate(self.values[0])
        await interaction.response.edit_message(**self.parent_view.render())


@app_commands.command(name="main", description="Open your campaign profile and RPG dashboard.")
async def main(interaction: discord.Interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("Server only.", ephemeral=True)
    view = MainView(interaction.guild.id, interaction.user.id, "home")
    await interaction.response.send_message(embed=view.home_embed(), view=view, ephemeral=True)


def register(bot):
    bot.tree.add_command(main)
