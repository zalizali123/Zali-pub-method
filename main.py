import os
import threading
import random
import string
import asyncio
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import discord
from discord import app_commands
from discord.ext import commands

# =========================================================
# 1. Dummy HTTP Server
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
# 2. In-Memory Database & Settings
# =========================================================
keys_db = {}
linked_users = {} 
spin_config = {
    "cooldown_seconds": 60,
    "win_chance": 40,
    "prizes": [
        {"name": "1 Day Key", "weight": 100, "hours": 24}
    ],
    "last_spin_times": {}
}

role_config = {
    "verified_role": "Verified",
    "ticket_owner_role": "Support Team"
}

# =========================================================
# 3. Bot Setup
# =========================================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# =========================================================
# 4. Helper Functions
# =========================================================
def create_key(duration_hours=None):
    chars = string.ascii_uppercase + string.digits
    new_key = ''.join(random.choices(chars, k=16))
    now = datetime.now(timezone.utc)
    expires_at = None
    if duration_hours and duration_hours > 0:
        expires_at = now + timedelta(hours=duration_hours)
    keys_db[new_key] = {"expires_at": expires_at, "used_by": None}
    return new_key

async def send_key_dm(user: discord.User, key: str, prize_name: str):
    # DMの時もインラインコード（`）を使ってスッキリさせる
    embed = discord.Embed(
        title="🎉 You Won!",
        description=f"Congratulations! You won a **{prize_name}**.\n\nYour key:\n`{key}`\n\n*Click 'Redeem Key' on the server to use it.*",
        color=discord.Color.gold()
    )
    try:
        await user.send(embed=embed)
        return True
    except discord.Forbidden:
        return False

# =========================================================
# 5. UI: Modals
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
        key = self.key_input.value.strip().upper()
        if key not in keys_db:
            await interaction.response.send_message("❌ Invalid key.", ephemeral=True)
            return
        key_data = keys_db[key]
        if key_data["used_by"] is not None:
            await interaction.response.send_message("❌ This key has already been used.", ephemeral=True)
            return
        if key_data["expires_at"] is not None and datetime.now(timezone.utc) > key_data["expires_at"]:
            await interaction.response.send_message("❌ This key has expired.", ephemeral=True)
            return

        keys_db[key]["used_by"] = interaction.user.id
        await interaction.response.send_message("✅ Key successfully redeemed!", ephemeral=True)

class LinkUserModal(discord.ui.Modal, title="Link Roblox User"):
    username_input = discord.ui.TextInput(label="Roblox Username", style=discord.TextStyle.short, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        linked_users[interaction.user.id] = self.username_input.value
        await interaction.response.send_message(f"✅ Linked to Roblox user: `{self.username_input.value}`", ephemeral=True)

# =========================================================
# 6. UI: Spin Game View
# =========================================================
class SpinView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Spin for Keys", style=discord.ButtonStyle.success, emoji="🎰", custom_id="btn_spin")
    async def spin(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        now = datetime.now(timezone.utc)
        
        if user_id in spin_config["last_spin_times"]:
            last_time = spin_config["last_spin_times"][user_id]
            elapsed = (now - last_time).total_seconds()
            cd = spin_config["cooldown_seconds"]
            if elapsed < cd:
                remaining = int(cd - elapsed)
                await interaction.response.send_message(f"⏳ Please wait `{remaining}` seconds.", ephemeral=True)
                return

        spin_config["last_spin_times"][user_id] = now
        
        button.disabled = True
        await interaction.response.edit_message(content="🎰 **Spinning...**", view=self)
        await asyncio.sleep(1.5)
        
        win_roll = random.randint(1, 100)
        if win_roll > spin_config["win_chance"]:
            embed = discord.Embed(title="Spin Result", description="❌ **You didn't win this time.**", color=discord.Color.red())
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            prizes = spin_config["prizes"]
            weights = [p["weight"] for p in prizes]
            chosen_prize = random.choices(prizes, weights=weights, k=1)[0]
            
            key = create_key(duration_hours=chosen_prize["hours"])
            
            dm_success = await send_key_dm(interaction.user, key, chosen_prize["name"])
            
            if dm_success:
                embed = discord.Embed(
                    title="🎉 You Won!",
                    description=f"Congratulations! You won a **{chosen_prize['name']}**.\n**Please check your DMs** to get the key.",
                    color=discord.Color.gold()
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                # ★ ここでインラインコード（`）を使う ★
                embed = discord.Embed(
                    title="🎉 You Won! (DM Failed)",
                    description=f"Your DMs are closed, here is your key:\n`{key}`",
                    color=discord.Color.gold()
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
        
        button.disabled = False
        try:
            await interaction.edit_original_response(content="Click to spin!", view=self)
        except:
            pass

# =========================================================
# 7. UI: Ticket System Views
# =========================================================
class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.success, emoji="🎫", custom_id="btn_create_ticket")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        owner_role_name = role_config["ticket_owner_role"]
        owner_role = discord.utils.get(guild.roles, name=owner_role_name)
        
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
        
        mention_text = owner_role.mention if owner_role else owner_role_name
        
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
# 8. UI: Main Panel View
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
            
        role_name = role_config["verified_role"]
        role = discord.utils.get(interaction.guild.roles, name=role_name)
        
        if role:
            try:
                await interaction.user.add_roles(role)
                await interaction.response.send_message(f"✅ Gave you the `{role_name}` role!", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ I don't have permission to give you this role.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Role `{role_name}` not found. Please contact staff.", ephemeral=True)

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
        embed.add_field(name="Roblox Account", value=f"`{roblox_name}`", inline=False)
        if user_key_info:
            key_code, data = user_key_info
            expiry_text = "Lifetime" if data["expires_at"] is None else data["expires_at"].strftime("%Y-%m-%d %H:%M UTC")
            embed.add_field(name="Active Key", value=f"`{key_code}`", inline=True)
            embed.add_field(name="Expires At", value=expiry_text, inline=True)
        else:
            embed.add_field(name="Active Key", value="None", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

# =========================================================
# 9. Commands
# =========================================================
@bot.event
async def on_ready():
    bot.add_view(PanelView())
    bot.add_view(SpinView())
    bot.add_view(TicketPanelView())
    bot.add_view(TicketControlView())
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Sync error: {e}")
    print(f"Logged in as {bot.user}")

@bot.tree.command(name="set_roles", description="[OWNER] Set the roles for verification and tickets")
@app_commands.describe(
    verified_role="Role name to give on 'Get Role' (e.g., Verified)",
    ticket_owner_role="Role name to mention in tickets (e.g., Support Team)"
)
@app_commands.checks.has_permissions(administrator=True)
async def set_roles(interaction: discord.Interaction, verified_role: str = None, ticket_owner_role: str = None):
    if verified_role:
        role_config["verified_role"] = verified_role
    if ticket_owner_role:
        role_config["ticket_owner_role"] = ticket_owner_role
        
    embed = discord.Embed(title="⚙️ Role Settings Updated", color=discord.Color.green())
    embed.add_field(name="Verified Role (Get Role)", value=f"`{role_config['verified_role']}`", inline=False)
    embed.add_field(name="Ticket Owner Role", value=f"`{role_config['ticket_owner_role']}`", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="spin_config", description="[OWNER] Configure spin wheel settings")
@app_commands.describe(
    cooldown="Cooldown in seconds", 
    win_chance="Win chance %", 
    prizes="Format: Name:Weight:Hours. e.g., '1 Day:50:24, 4 Days:30:96, Lifetime:20:0' (Use 0 for Lifetime)"
)
@app_commands.checks.has_permissions(administrator=True)
async def spin_config_cmd(interaction: discord.Interaction, cooldown: int = None, win_chance: int = None, prizes: str = None):
    if cooldown is not None: spin_config["cooldown_seconds"] = cooldown
    if win_chance is not None: spin_config["win_chance"] = max(0, min(100, win_chance))
    
    if prizes:
        try:
            new_prizes = []
            for p in prizes.split(","):
                parts = p.strip().split(":")
                if len(parts) == 3:
                    new_prizes.append({
                        "name": parts[0].strip(),
                        "weight": int(parts[1].strip()),
                        "hours": int(parts[2].strip())
                    })
            if new_prizes:
                spin_config["prizes"] = new_prizes
        except ValueError:
            await interaction.response.send_message("❌ Invalid prize format. Use `Name:Weight:Hours`.", ephemeral=True)
            return

    embed = discord.Embed(title="⚙️ Spin Settings Updated", color=discord.Color.green())
    embed.add_field(name="Cooldown", value=f"`{spin_config['cooldown_seconds']}`s", inline=True)
    embed.add_field(name="Win Chance", value=f"`{spin_config['win_chance']}`%", inline=True)
    
    prize_list = "\n".join([f"- {p['name']} (Weight: `{p['weight']}`, Hours: `{p['hours']}`)" for p in spin_config["prizes"]])
    embed.add_field(name="Prizes", value=prize_list, inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="send_embed", description="[OWNER] Send a customized embed")
@app_commands.describe(title="Title", description="Description", color_hex="Hex color (e.g. ff0000)", footer="Footer", image_url="Image URL")
@app_commands.checks.has_permissions(administrator=True)
async def send_embed(interaction: discord.Interaction, title: str, description: str, color_hex: str = "5865F2", footer: str = None, image_url: str = None):
    try:
        color = discord.Color(int(color_hex.replace("#", ""), 16))
    except ValueError:
        await interaction.response.send_message("❌ Invalid HEX color.", ephemeral=True)
        return
    embed = discord.Embed(title=title, description=description, color=color)
    if footer: embed.set_footer(text=footer)
    if image_url: embed.set_image(url=image_url)
    await interaction.response.send_message("✅ Sent!", ephemeral=True)
    await interaction.channel.send(embed=embed)

@bot.tree.command(name="spin_setup", description="[ADMIN] Post the spin wheel panel")
@app_commands.checks.has_permissions(administrator=True)
async def spin_setup(interaction: discord.Interaction):
    embed = discord.Embed(title="🎰 Spin for Keys", description="Click below to spin and win free keys!", color=discord.Color.gold())
    await interaction.response.send_message(embed=embed, view=SpinView())

@bot.tree.command(name="ticket_setup", description="[ADMIN] Post the ticket panel")
@app_commands.checks.has_permissions(administrator=True)
async def ticket_setup(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎫 Support Tickets",
        description="Need help? Click the button below to open a private ticket with our support team.",
        color=discord.Color.blurple()
    )
    await interaction.response.send_message(embed=embed, view=TicketPanelView())

@bot.tree.command(name="generatekey", description="[ADMIN] Generate a key")
@app_commands.describe(duration="Duration")
@app_commands.choices(duration=[
    app_commands.Choice(name="1 Hour", value="1h"),
    app_commands.Choice(name="1 Day", value="1d"),
    app_commands.Choice(name="4 Days", value="4d"),
    app_commands.Choice(name="Lifetime", value="lifetime"),
])
@app_commands.checks.has_permissions(administrator=True)
async def generatekey(interaction: discord.Interaction, duration: app_commands.Choice[str]):
    if duration.value == "lifetime":
        key = create_key()
        duration_text = "Lifetime"
    elif duration.value == "1h":
        key = create_key(duration_hours=1)
        duration_text = "1 Hour"
    elif duration.value == "1d":
        key = create_key(duration_hours=24)
        duration_text = "1 Day"
    else:
        key = create_key(duration_hours=96)
        duration_text = "4 Days"
    
    embed = discord.Embed(title="🔑 Key Generated", color=discord.Color.green())
    embed.add_field(name="Key", value=f"`{key}`", inline=False)
    embed.add_field(name="Duration", value=duration_text, inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="panel", description="Post the main access panel")
async def panel(interaction: discord.Interaction):
    embed = discord.Embed(title="Panel Access", description="Win or buy a key to unlock.\n`Win/Buy ➔ Redeem ➔ Link ➔ Access`", color=discord.Color.blue())
    await interaction.response.send_message(embed=embed, view=PanelView())

# =========================================================
# 10. Start
# =========================================================
if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    bot.run(os.environ["DISCORD_TOKEN"])
