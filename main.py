import os
import threading
import random
import string
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import discord
from discord import app_commands
from discord.ext import commands

# =========================================================
# 1. Dummy HTTP Server (For Render Web Service Port)
# =========================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

# =========================================================
# 2. In-Memory Database (Replace with real DB for production)
# =========================================================
# keys = { "KEYCODE": {"expires_at": datetime, "used_by": None} }
keys_db = {}
# linked_users = { discord_user_id: "roblox_username" }
linked_users = {}

# =========================================================
# 3. Bot Setup
# =========================================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# =========================================================
# 4. UI: Modals (Pop-up Forms)
# =========================================================
class RedeemModal(discord.ui.Modal, title="Redeem Key"):
    key_input = discord.ui.TextInput(
        label="Access Key (16 characters)",
        style=discord.TextStyle.short,
        placeholder="Paste your key here...",
        min_length=16,
        max_length=16,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        key = self.key_input.value.upper()
        
        if key not in keys_db:
            await interaction.response.send_message("❌ Invalid key.", ephemeral=True)
            return
            
        key_data = keys_db[key]
        if key_data["used_by"] is not None:
            await interaction.response.send_message("❌ This key has already been used.", ephemeral=True)
            return
            
        if datetime.utcnow() > key_data["expires_at"]:
            await interaction.response.send_message("❌ This key has expired.", ephemeral=True)
            return

        # Mark key as used
        keys_db[key]["used_by"] = interaction.user.id
        
        # Save to linked_users if they have a username, else just mark as authorized
        await interaction.response.send_message(
            f"✅ Key `{key}` successfully redeemed!\nYou can now use the 'Get Role' button to verify.", 
            ephemeral=True
        )

class LinkUserModal(discord.ui.Modal, title="Link Roblox User"):
    username_input = discord.ui.TextInput(
        label="Roblox Username",
        style=discord.TextStyle.short,
        placeholder="Enter your Roblox username...",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        roblox_name = self.username_input.value
        linked_users[interaction.user.id] = roblox_name
        await interaction.response.send_message(
            f"✅ Successfully linked your Discord account to Roblox user: **{roblox_name}**", 
            ephemeral=True
        )

# =========================================================
# 5. UI: Panel View (Buttons)
# =========================================================
class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Redeem Key", style=discord.ButtonStyle.success, emoji="🔑", custom_id="btn_redeem")
    async def redeem_key(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RedeemModal())

    @discord.ui.button(label="Get Role", style=discord.ButtonStyle.primary, emoji="👤", custom_id="btn_role")
    async def get_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if user has a valid redeemed key
        user_has_key = any(data["used_by"] == interaction.user.id for data in keys_db.values())
        
        if not user_has_key:
            await interaction.response.send_message("❌ You must redeem a valid key first before getting the role.", ephemeral=True)
            return
            
        role_name = "Verified"  # Change to your role name
        role = discord.utils.get(interaction.guild.roles, name=role_name)
        
        if role:
            try:
                await interaction.user.add_roles(role)
                await interaction.response.send_message(f"✅ Successfully gave you the {role_name} role!", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ I don't have permission to give you this role.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Role '{role_name}' not found. Please contact staff.", ephemeral=True)

    @discord.ui.button(label="Link User", style=discord.ButtonStyle.secondary, emoji="🔗", custom_id="btn_link")
    async def link_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LinkUserModal())

    @discord.ui.button(label="Stats", style=discord.ButtonStyle.primary, emoji="📊", custom_id="btn_stats")
    async def stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Find user's key info
        user_key_info = None
        for key, data in keys_db.items():
            if data["used_by"] == interaction.user.id:
                user_key_info = (key, data)
                break
                
        roblox_name = linked_users.get(interaction.user.id, "Not linked")
        
        embed = discord.Embed(title="📊 Your Stats", color=discord.Color.blue())
        embed.add_field(name="Roblox Account", value=roblox_name, inline=False)
        
        if user_key_info:
            key_code, data = user_key_info
            expires = data["expires_at"].strftime("%Y-%m-%d %H:%M UTC")
            embed.add_field(name="Active Key", value=f"`{key_code}`", inline=True)
            embed.add_field(name="Expires At", value=expires, inline=True)
        else:
            embed.add_field(name="Active Key", value="None (Please redeem a key)", inline=False)
            
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.secondary, emoji="🎫", custom_id="btn_ticket")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        # Create a private channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        # Optional: Add staff role
        # staff_role = discord.utils.get(guild.roles, name="Staff")
        # if staff_role: overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        ticket_channel = await guild.create_text_channel(
            name=f"ticket-{interaction.user.name}",
            overwrites=overwrites,
            category=interaction.channel.category # Create in same category
        )
        
        embed = discord.Embed(
            title="Ticket Created",
            description=f"Hello {interaction.user.mention}, staff will be with you shortly.\nTo close this ticket, click the button below.",
            color=discord.Color.green()
        )
        await ticket_channel.send(embed=embed, view=TicketControlView())
        await interaction.response.send_message(f"✅ Ticket created: {ticket_channel.mention}", ephemeral=True)

class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Closing ticket...", ephemeral=True)
        await interaction.channel.delete()

# =========================================================
# 6. Commands
# =========================================================
@bot.event
async def on_ready():
    bot.add_view(PanelView())
    # For tickets to work persistently across restarts
    bot.add_view(TicketControlView())
    
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Sync error: {e}")
    print(f"Logged in as {bot.user}")

# /panel
@bot.tree.command(name="panel", description="Post the access panel")
async def panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Panel Access",
        description=(
            "Purchase a valid access key to unlock the panel. Once your key has been purchased, "
            "head over to Redeem, enter your key, and link your account to get started.\n\n"
            "**Flow:**\n"
            "`Buy Key ➔ Redeem Key ➔ Link Account ➔ Access Panel`"
        ),
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed, view=PanelView())

# /prices
@bot.tree.command(name="prices", description="Post the pricing information")
async def prices(interaction: discord.Interaction):
    embed = discord.Embed(title="💰 Prices", color=discord.Color.gold())
    embed.add_field(name="4 day key:", value="5 Garamas or 4 US$\n\u200b", inline=False)
    embed.add_field(name="Lifetime key:", value="1 Dragon or 10 US$\n\u200b", inline=False)
    embed.add_field(name="Invite Rewards:", value="**5 invites** = 1 hour free key\n**10 invites** = 1 day free key", inline=False)
    await interaction.response.send_message(embed=embed)

# /say
@bot.tree.command(name="say", description="Post a message as the bot")
@app_commands.describe(text="The message to post")
async def say(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(text)

# /embed
@bot.tree.command(name="embed", description="Post a custom embed")
@app_commands.describe(title="Embed title", text="Embed description")
async def embed_cmd(interaction: discord.Interaction, title: str, text: str):
    embed = discord.Embed(title=title, description=text, color=discord.Color.blue())
    await interaction.response.send_message(embed=embed)

# /generatekey - Admin only
@bot.tree.command(name="generatekey", description="[ADMIN] Generate a new 16-character access key")
@app_commands.describe(days="Number of days the key is valid (e.g., 4, 30, 365)")
@app_commands.checks.has_permissions(administrator=True)
async def generatekey(interaction: discord.Interaction, days: int):
    # Generate random 16 character string
    chars = string.ascii_uppercase + string.digits
    new_key = ''.join(random.choices(chars, k=16))
    
    expires_at = datetime.utcnow() + timedelta(days=days)
    
    keys_db[new_key] = {
        "expires_at": expires_at,
        "used_by": None
    }
    
    embed = discord.Embed(title="🔑 Key Generated", color=discord.Color.green())
    embed.add_field(name="Key", value=f"`{new_key}`", inline=False)
    embed.add_field(name="Valid For", value=f"{days} days", inline=True)
    embed.add_field(name="Expires At", value=expires_at.strftime("%Y-%m-%d %H:%M UTC"), inline=True)
    embed.set_footer(text="Give this key to the buyer. It can only be redeemed once.")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@generatekey.error
async def generatekey_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)

# =========================================================
# 7. Start Services
# =========================================================
if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    bot.run(os.environ["DISCORD_TOKEN"])
