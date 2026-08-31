import os
import discord
from discord import app_commands
from discord.ext import commands

from ..state import is_staff


# The web game runs locally with the bot. /test opens the local web server.
GAME_URL = os.getenv("ANONYMOUS_APP_URL", "http://127.0.0.1:18474").rstrip("/")


class TestApp(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="test", description="Open the Anonymous Bot V2 web game.")
    @app_commands.guild_only()
    async def test(self, interaction: discord.Interaction):
        if not is_staff(interaction):
            await interaction.response.send_message(
                "You need GM or server-admin permission to use /test.",
                ephemeral=True,
            )
            return

        if not GAME_URL:
            await interaction.response.send_message(
                "Anonymous Bot V2 is not configured. Start the bot/web server first.",
                ephemeral=True,
            )
            return

        view = discord.ui.View(timeout=300)
        view.add_item(
            discord.ui.Button(
                label="Open Anonymous Bot V2",
                style=discord.ButtonStyle.link,
                url=GAME_URL,
            )
        )
        await interaction.response.send_message(
            "Anonymous Bot V2 is running locally. Open the local game below.",
            view=view,
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(TestApp(bot))


async def register(bot: commands.Bot):
    await bot.add_cog(TestApp(bot))
