import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import discord
from discord import app_commands
from discord.ext import commands

# ---------------------------------------------------------
# 1. Dummy HTTP Server (For Render Web Service Port Check)
# ---------------------------------------------------------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass  # Suppress logs

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

# ---------------------------------------------------------
# 2. Bot Setup & Intents
# ---------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True  # Requires Portal ON
intents.members = True          # Requires Portal ON

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------------------------------------------------
# 3. UI: Modals (Pop-up Forms)
# ---------------------------------------------------------
class RedeemModal(discord.ui.Modal, title="Redeem Key"):
    key_input = discord.ui.TextInput(
        label="Access Key",
        style=discord.TextStyle.short,
        placeholder="Paste your key here...",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Add your key validation logic here
        await interaction.response.send_message(
            f"Key `{self.key_input.value}` has been submitted. Verifying...", 
            ephemeral=True
        )

class LinkUserModal(discord.ui.Modal, title="Link User"):
    username_input = discord.ui.TextInput(
        label="Your Username",
        style=discord.TextStyle.short,
        placeholder="Enter your linked username...",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Add your account link logic here
        await interaction.response.send_message(
            f"User `{self.username_input.value}` has been successfully linked.", 
            ephemeral=True
        )

# ---------------------------------------------------------
# 4. UI: Panel View (Interactive Buttons)
# ---------------------------------------------------------
class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # Persistent

    @discord.ui.button(label="Redeem Key", style=discord.ButtonStyle.success, emoji="🔑", custom_id="btn_redeem")
    async def redeem_key(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RedeemModal())

    @discord.ui.button(label="Get Role", style=discord.ButtonStyle.primary, emoji="👤", custom_id="btn_role")
    async def get_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        role_name = "Verified"  # Change this to your actual role name
        role = discord.utils.get(interaction.guild.roles, name=role_name)
        
        if role:
            try:
                await interaction.user.add_roles(role)
                await interaction.response.send_message(
                    f"Successfully gave you the {role_name} role!", ephemeral=True
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "I don't have permission to give you this role. (Check role hierarchy)", ephemeral=True
                )
        else:
            await interaction.response.send_message(f"Role '{role_name}' not found.", ephemeral=True)

    @discord.ui.button(label="Link User", style=discord.ButtonStyle.secondary, emoji="🔗", custom_id="btn_link")
    async def link_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LinkUserModal())

    @discord.ui.button(label="Stats", style=discord.ButtonStyle.primary, emoji="📊", custom_id="btn_stats")
    async def stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="Server Stats", color=discord.Color.green())
        embed.add_field(name="Total Members", value=str(interaction.guild.member_count))
        await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------------------------------------------------------
# 5. Bot Events & Commands
# ---------------------------------------------------------
@bot.event
async def on_ready():
    # Re-register persistent views
    bot.add_view(PanelView())
    
    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Sync error: {e}")
        
    print(f"Logged in as {bot.user}")

# /panel - Posts the Access Panel
@bot.tree.command(name="panel", description="Post the access panel with interactive buttons")
async def panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Panel Access",
        description=(
            "Purchase a valid access key to unlock the panel. Once your key has been purchased, "
            "head over to Redeem, enter your key, and link your account to get started.\n"
            "After your account is linked and verified, you can access the available panel tools "
            "and begin using the teleport features on authorized users/targets.\n\n"
            "**Flow:**\n"
            "`Buy Key ➔ Redeem Key ➔ Link Account ➔ Access Panel ➔ To Teleport Victims`"
        ),
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed, view=PanelView())

# /prices - Posts the Price list
@bot.tree.command(name="prices", description="Post the pricing information")
async def prices(interaction: discord.Interaction):
    embed = discord.Embed(
        title="💰 Prices",
        color=discord.Color.gold()
    )
    embed.add_field(
        name="4 day key:", 
        value="5 Garamas or 4 US$\n\u200b", 
        inline=False
    )
    embed.add_field(
        name="Lifetime key:", 
        value="1 Dragon or 10 US$\n\u200b", 
        inline=False
    )
    embed.add_field(
        name="Invite Rewards:", 
        value="**5 invites** = 1 hour free key\n**10 invites** = 1 day free key", 
        inline=False
    )
    await interaction.response.send_message(embed=embed)

# /say - Posts a normal message
@bot.tree.command(name="say", description="Post a message as the bot")
@app_commands.describe(text="The message to post")
async def say(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(text)

# /embed - Posts a custom embed
@bot.tree.command(name="embed", description="Post a custom embed as the bot")
@app_commands.describe(title="Embed title", text="Embed description")
async def embed_cmd(interaction: discord.Interaction, title: str, text: str):
    embed = discord.Embed(title=title, description=text, color=discord.Color.blue())
    await interaction.response.send_message(embed=embed)

# ---------------------------------------------------------
# 6. Start Services
# ---------------------------------------------------------
if __name__ == "__main__":
    # Start dummy server thread for Render
    threading.Thread(target=run_dummy_server, daemon=True).start()
    # Start Discord bot
    bot.run(os.environ["DISCORD_TOKEN"])
