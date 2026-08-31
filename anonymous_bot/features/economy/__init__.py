"""Economy package.

The public interface remains ``features.economy.register(bot)`` so the rest of
the bot does not need to know how the economy is internally organized.
"""

import asyncio
import time

from . import market as _market
from .core import *  # noqa: F401,F403
from .market import *  # noqa: F401,F403
# Re-export internal helpers used by legacy feature modules. Wildcard imports
# intentionally omit underscore-prefixed names.
from .core import _set_vip_cards
from .market import (
    _stock_state, _active_market_events, _ensure_market_updated, _stock_holdings,
    _find_company, _market_sentiment, _vip_stock_holdings, _apply_market_change, _apply_dividends,
)
from .gambling import *  # noqa: F401,F403
# Re-export gambling internals used by the existing /main economy UI.
from .gambling import _gambling_state, _gambling_multiplier, GAMBLING_KEY


async def stock_market_loop(bot):
    """Run the market clock without blocking Discord interactions."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await asyncio.sleep(30)
            for guild in bot.guilds:
                market = _stock_state(guild.id)
                now = time.time()
                if now - float(market.get(STOCK_LAST_UPDATE_KEY, now)) >= STOCK_UPDATE_SECONDS:
                    if _market._active_market_events(market):
                        _market.apply_market_event_cycle(market)
                    else:
                        _market._apply_market_change(market, None, "automatic")
                    _market._apply_dividends(guild.id, market)
                    _market.save_item_data()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Stock market loop error: {type(exc).__name__}: {exc}")


def register(bot):
    """Register the economy/faction command groups exactly once."""
    if not any(getattr(c, "name", None) == GAMBLING_GROUP.name for c in ECONOMY_GROUP.commands):
        ECONOMY_GROUP.add_command(GAMBLING_GROUP)
    if not any(getattr(c, "name", None) == ECONOMY_GROUP.name for c in bot.tree.get_commands()):
        bot.tree.add_command(ECONOMY_GROUP)
    if not any(getattr(c, "name", None) == FACTION_GROUP.name for c in bot.tree.get_commands()):
        bot.tree.add_command(FACTION_GROUP)

    async def start_stock_task():
        if not getattr(bot, "_stock_market_task", None) or bot._stock_market_task.done():
            bot._stock_market_task = asyncio.create_task(stock_market_loop(bot))
            print("Stock market background task started.")

    bot._start_stock_market_task = start_stock_task
