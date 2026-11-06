import os
import discord
from discord import app_commands
from discord.ext import commands

intents = discord.Intents.default()

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    # Sync slash commands with Discord (may take a few minutes to appear)
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Sync error: {e}")
    print(f"Logged in as {bot.user}")

# /say : Post a normal message as the bot
@bot.tree.command(name="say", description="Post a message as the bot")
@app_commands.describe(text="The message to post")
async def say(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(text)

# /embed : Post an embed as the bot
@bot.tree.command(name="embed", description="Post an embed as the bot")
@app_commands.describe(
    title="Embed title",
    text="Embed description"
)
async def embed(
    interaction: discord.Interaction,
    title: str = "Embed Title",
    text: str = "This is an embed sent by the bot."
):
    embed = discord.Embed(
        title=title,
        description=text,
        color=discord.Color.blue()
    )
    embed.add_field(name="Field 1", value="Value 1", inline=False)
    embed.add_field(name="Field 2", value="Value 2", inline=True)
    embed.set_footer(text="Footer text")
    await interaction.response.send_message(embed=embed)

bot.run(os.environ["DISCORD_TOKEN"])
