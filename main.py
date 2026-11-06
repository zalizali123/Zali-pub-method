import os
import threading
import random
import string
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import asyncio

import discord
from discord import app_commands
from discord.ext import commands

# =========================================================
# 1. Dummy HTTP Server (For Render)
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
# 2. In-Memory Database
# =========================================================
keys_db = {}
linked_users = {} 

# =========================================================
# 3. Bot Setup
# =========================================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# =========================================================
# 4. UI: Modals
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
        # Discordの自動大文字化仕様を回避するため、すべて大文字に変換して比較
        key = self.key_input.value.strip().upper()
        
        if key not in keys_db:
            await interaction.response.send_message("❌ Invalid key. Please check for typos.", ephemeral=True)
            return
            
        key_data = keys_db[key]
        
        if key_data["used_by"] is not None:
            await interaction.response.send_message("❌ This key has already been used.", ephemeral=True)
            return
            
        if key_data["expires_at"] is not None:
            if datetime.now(timezone.utc) > key_data["expires_at"]:
                await interaction.response.send_message("❌ This key has expired.", ephemeral=True)
                return

        keys_db[key]["used_by"] = interaction.user.id
        await interaction.response.send_message(
            f"✅ Key successfully redeemed!\nYou can now click **Get Role** to verify.", 
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
            f"✅ Linked to Roblox user: **{roblox_name}**", 
            ephemeral=True
        )

# =========================================================
# 5. UI: Main Panel View
# =========================================================
class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Redeem Key", style=discord.ButtonStyle.success, emoji="🔑", custom_id="btn_redeem")
    async def redeem_key(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RedeemModal())

    @discord.ui.button(label="Get Role", style=discord.ButtonStyle.primary, emoji="👤", custom_id="btn_role")
    async def get_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_has_key = any(data["used_by"] == interaction.user.id for data in keys_db.values())
        
        if not user_has_key:
            await interaction.response.send_message("❌ You must redeem a valid key first.", ephemeral=True)
            return
            
        role_name = "Verified" # Change to your role name
        role = discord.utils.get(interaction.guild.roles, name=role_name)
        
        if role:
            try:
                await interaction.user.add_roles(role)
                await interaction.response.send_message(f"✅ Gave you the {role_name} role!", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ I don't have permission to give you this role.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Role '{role_name}' not found.", ephemeral=True)

    @discord.ui.button(label="Link User", style=discord.ButtonStyle.secondary, emoji="🔗", custom_id="btn_link")
    async def link_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LinkUserModal())

    @discord.ui.button(label="Stats", style=discord.ButtonStyle.primary, emoji="📊", custom_id="btn_stats")
    async def stats(self, interaction: discord.Interaction, button: discord.ui.Button):
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
            if data["expires_at"] is None:
                expiry_text = "Lifetime (Never expires)"
            else:
                expiry_text = data["expires_at"].strftime("%Y-%m-%d %H:%M UTC")
                
            embed.add_field(name="Active Key", value=f"`{key_code}`", inline=True)
            embed.add_field(name="Expires At", value=expiry_text, inline=True)
        else:
            embed.add_field(name="Active Key", value="None", inline=False)
            
        await interaction.response.send_message(embed=embed, ephemeral=True)

# =========================================================
# 6. UI: Ticket System Views
# =========================================================
class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.success, emoji="🎫", custom_id="btn_create_ticket")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        owner_role = discord.utils.get(guild.roles, name="Support Team") # Change to your owner role
        
        existing_channel = discord.utils.get(guild.channels, name=f"ticket-{interaction.user.name.lower()}")
        if existing_channel:
            await interaction.response.send_message(f"❌ You already have an open ticket: {existing_channel.mention}", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        if owner_role:
            overwrites[owner_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        ticket_channel = await guild.create_text_channel(
            name=f"ticket-{interaction.user.name}",
            overwrites=overwrites,
            category=interaction.channel.category 
        )
        
        mention_text = owner_role.mention if owner_role else "Support Team"
        
        embed = discord.Embed(
            title="Ticket Created",
            description=f"Hello {interaction.user.mention},\nThank you for reaching out. A staff member will be with you shortly.\n\n{mention_text}, please assist this user.",
            color=discord.Color.green()
        )
        
        await ticket_channel.send(content=mention_text, embed=embed, view=TicketControlView())
        await interaction.response.send_message(f"✅ Ticket created: {ticket_channel.mention}", ephemeral=True)

class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="btn_close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Closing ticket in 3 seconds...", ephemeral=True)
        await asyncio.sleep(3)
        await interaction.channel.delete()

# =========================================================
# 7. Commands
# =========================================================
@bot.event
async def on_ready():
    bot.add_view(PanelView())
    bot.add_view(TicketPanelView())
    bot.add_view(TicketControlView())
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Sync error: {e}")
    print(f"Logged in as {bot.user}")

# === キー生成コマンド ===
@bot.tree.command(name="generatekey", description="[ADMIN] Generate a new 16-character access key")
@app_commands.describe(
    duration="Select the duration for this key",
    custom_days="(Optional) Enter custom days if 'Custom' is selected"
)
@app_commands.choices(duration=[
    app_commands.Choice(name="1 Hour", value="1h"),
    app_commands.Choice(name="1 Day", value="1d"),
    app_commands.Choice(name="4 Days", value="4d"),
    app_commands.Choice(name="7 Days", value="7d"),
    app_commands.Choice(name="30 Days", value="30d"),
    app_commands.Choice(name="Lifetime (Permanent)", value="lifetime"),
    app_commands.Choice(name="Custom (Use custom_days)", value="custom"),
])
@app_commands.checks.has_permissions(administrator=True)
async def generatekey(interaction: discord.Interaction, duration: app_commands.Choice[str], custom_days: int = None):
    # 大文字と数字のみで生成（Discordのモーダル仕様と完全に一致させる）
    chars = string.ascii_uppercase + string.digits
    new_key = ''.join(random.choices(chars, k=16))
    
    now = datetime.now(timezone.utc)
    expires_at = None
    duration_text = ""

    if duration.value == "lifetime":
        expires_at = None
        duration_text = "Lifetime (Permanent)"
    elif duration.value == "1h":
        expires_at = now + timedelta(hours=1)
        duration_text = "1 Hour"
    elif duration.value == "1d":
        expires_at = now + timedelta(days=1)
        duration_text = "1 Day"
    elif duration.value == "4d":
        expires_at = now + timedelta(days=4)
        duration_text = "4 Days"
    elif duration.value == "7d":
        expires_at = now + timedelta(days=7)
        duration_text = "7 Days"
    elif duration.value == "30d":
        expires_at = now + timedelta(days=30)
        duration_text = "30 Days"
    elif duration.value == "custom":
        if not custom_days or custom_days <= 0:
            await interaction.response.send_message("❌ Please provide a valid `custom_days` value greater than 0.", ephemeral=True)
            return
        expires_at = now + timedelta(days=custom_days)
        duration_text = f"{custom_days} Days"
    
    keys_db[new_key] = {
        "expires_at": expires_at,
        "used_by": None
    }
    
    embed = discord.Embed(title="🔑 Key Generated", color=discord.Color.green())
    embed.add_field(name="Key", value=f"```\n{new_key}\n```", inline=False)
    embed.add_field(name="Duration", value=duration_text, inline=True)
    
    if expires_at:
        embed.add_field(name="Expires At", value=expires_at.strftime("%Y-%m-%d %H:%M UTC"), inline=True)
    else:
        embed.add_field(name="Expires At", value="Never", inline=True)
        
    embed.set_footer(text="Only the buyer should see this. It can only be redeemed once.")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# === チケットパネル作成コマンド ===
@bot.tree.command(name="ticket_setup", description="[ADMIN] Post the ticket panel")
@app_commands.checks.has_permissions(administrator=True)
async def ticket_setup(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎫 Support Tickets",
        description="Need help? Click the button below to open a private ticket with our support team.",
        color=discord.Color.blurple()
    )
    await interaction.response.send_message(embed=embed, view=TicketPanelView())

# === その他のコマンド ===
@bot.tree.command(name="panel", description="Post the access panel")
async def panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Panel Access",
        description="Purchase a valid access key to unlock the panel.\n**Flow:**\n`Buy Key ➔ Redeem Key ➔ Link Account ➔ Access Panel`",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed, view=PanelView())

@bot.tree.command(name="prices", description="Post the pricing information")
async def prices(interaction: discord.Interaction):
    embed = discord.Embed(title="💰 Prices", color=discord.Color.gold())
    embed.add_field(name="4 day key:", value="5 Garamas or 4 US$\n\u200b", inline=False)
    embed.add_field(name="Lifetime key:", value="1 Dragon or 10 US$\n\u200b", inline=False)
    embed.add_field(name="Invite Rewards:", value="**5 invites** = 1 hour free key\n**10 invites** = 1 day free key", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="say", description="Post a message as the bot")
@app_commands.describe(text="The message to post")
async def say(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(text)

@bot.tree.command(name="embed", description="Post a custom embed")
@app_commands.describe(title="Embed title", text="Embed description")
async def embed_cmd(interaction: discord.Interaction, title: str, text: str):
    embed = discord.Embed(title=title, description=text, color=discord.Color.blue())
    await interaction.response.send_message(embed=embed)

# =========================================================
# 8. Start
# =========================================================
if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    bot.run(os.environ["DISCORD_TOKEN"])
