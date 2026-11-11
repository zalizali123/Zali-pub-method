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
whitelist = set()   
blacklist = set()   

spin_config = {
    "cooldown_seconds": 60,
    "win_chance": 40,
    "prizes": [
        {"name": "1 Hour Key", "weight": 50, "hours": 1},
        {"name": "1 Day Key", "weight": 30, "hours": 24},
        {"name": "1 Week Key", "weight": 15, "hours": 168},
        {"name": "Lifetime Key", "weight": 5, "hours": 0}
    ],
    "last_spin_times": {}
}

role_config = {
    "verified_role": "Verified",
    "verify_role": "Verified", 
    "ticket_access_roles": ["Support Team"] 
}

log_config = {
    "link_log_channel_id": None
}

# ゲームの状態管理
# { channel_id: {"number": int, "prize": str, "active": bool, "winner": int | None} }
guess_games = {}

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
        if interaction.user.id in blacklist:
            await interaction.response.send_message("❌ You are blacklisted.", ephemeral=True)
            return

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
    username_input = discord.ui.TextInput(label="Roblox ID / Username", style=discord.TextStyle.short, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id in blacklist:
            await interaction.response.send_message("❌ You are blacklisted.", ephemeral=True)
            return
        
        roblox_id = self.username_input.value
        linked_users[interaction.user.id] = roblox_id
        
        await interaction.response.send_message(f"✅ Linked to Roblox: `{roblox_id}`", ephemeral=True)
        
        log_channel_id = log_config.get("link_log_channel_id")
        if log_channel_id:
            log_channel = interaction.guild.get_channel(log_channel_id)
            if log_channel:
                embed = discord.Embed(title="🔗 New User Linked", color=discord.Color.blue())
                embed.add_field(name="Discord User", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=False)
                embed.add_field(name="Roblox ID", value=f"`{roblox_id}`", inline=True)
                embed.set_thumbnail(url=interaction.user.display_avatar.url)
                embed.timestamp = datetime.now(timezone.utc)
                try:
                    await log_channel.send(embed=embed)
                except discord.Forbidden:
                    pass

# =========================================================
# 6. UI: Spin Game View
# =========================================================
class SpinView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Spin for Keys", style=discord.ButtonStyle.success, emoji="🎰", custom_id="btn_spin")
    async def spin(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in blacklist:
            await interaction.response.send_message("❌ You are blacklisted.", ephemeral=True)
            return

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
        if interaction.user.id in blacklist:
            await interaction.response.send_message("❌ You are blacklisted.", ephemeral=True)
            return

        guild = interaction.guild
        access_role_names = role_config["ticket_access_roles"]
        
        existing_channel = discord.utils.get(guild.channels, name=f"ticket-{interaction.user.name.lower()}")
        if existing_channel:
            await interaction.response.send_message(f"❌ You already have an open ticket: {existing_channel.mention}", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        mention_list = []
        for r_name in access_role_names:
            role = discord.utils.get(guild.roles, name=r_name.strip())
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
                mention_list.append(role.mention)
        
        mention_text = ", ".join(mention_list) if mention_list else "No roles configured."

        ticket_channel = await guild.create_text_channel(
            name=f"ticket-{interaction.user.name}",
            overwrites=overwrites,
            category=interaction.channel.category 
        )
        
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
# 8. UI: Link Button View
# =========================================================
class LinkButtonView(discord.ui.View):
    def __init__(self, url: str, label: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.link,
            url=url,
            emoji="🎮"
        ))

# =========================================================
# 9. UI: Verify Panel View
# =========================================================
class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, emoji="✅", custom_id="btn_verify")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in blacklist:
            await interaction.response.send_message("❌ You are blacklisted.", ephemeral=True)
            return

        role_name = role_config["verify_role"]
        role = discord.utils.get(interaction.guild.roles, name=role_name)
        
        if role:
            try:
                await interaction.user.add_roles(role)
                await interaction.response.send_message(f"✅ Verified! You have been given the `{role_name}` role.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ I don't have permission to give you this role.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Role `{role_name}` not found. Please contact staff.", ephemeral=True)

# =========================================================
# 10. UI: Main Panel View
# =========================================================
class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Redeem Key", style=discord.ButtonStyle.success, emoji="🔑", custom_id="btn_redeem")
    async def redeem_key(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in blacklist:
            await interaction.response.send_message("❌ You are blacklisted.", ephemeral=True)
            return
        await interaction.response.send_modal(RedeemModal())

    @discord.ui.button(label="Get Role", style=discord.ButtonStyle.primary, emoji="👤", custom_id="btn_role")
    async def get_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in blacklist:
            await interaction.response.send_message("❌ You are blacklisted.", ephemeral=True)
            return

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
        if interaction.user.id in blacklist:
            await interaction.response.send_message("❌ You are blacklisted.", ephemeral=True)
            return
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
# 11. Events (Guess Game Logic)
# =========================================================
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Guessゲームのチャンネルか確認
    if message.channel.id in guess_games:
        game = guess_games[message.channel.id]
        
        # すでに終了しているゲームなら無視
        if not game["active"]:
            return

        # 数字かどうかチェック
        try:
            guess = int(message.content.strip())
        except ValueError:
            return # 数字以外は無視

        if guess == game["number"]:
            # 正解！
            game["active"] = False
            game["winner"] = message.author.id
            
            # ロック処理（@everyone の送信権限を剥奪）
            overwrites = message.channel.overwrites_for(message.guild.default_role)
            overwrites.send_messages = False
            await message.channel.set_permissions(message.guild.default_role, overwrite=overwrites)
            
            # 景品処理
            prize_name = game["prize"]
            embed = discord.Embed(
                title="🎉 We have a winner!",
                description=f"{message.author.mention} guessed the correct number: **{game['number']}**\n\n**Prize:** {prize_name}\n\nThis channel is now locked.",
                color=discord.Color.gold()
            )
            
            if prize_name.lower() in ["1h", "1d", "1w", "lifetime"]:
                if prize_name.lower() == "1h": hours = 1
                elif prize_name.lower() == "1d": hours = 24
                elif prize_name.lower() == "1w": hours = 168
                else: hours = 0
                
                key = create_key(duration_hours=hours)
                dm_success = await send_key_dm(message.author, key, f"{prize_name} Key")
                
                if dm_success:
                    embed.add_field(name="Your Key", value="Check your DMs!", inline=False)
                else:
                    embed.add_field(name="Your Key", value=f"Your DMs are closed, so here it is:\n`{key}`", inline=False)

            await message.channel.send(embed=embed)
        
        else:
            # ハズレ（ヒントなし）
            await message.add_reaction("❌")

    await bot.process_commands(message)

# =========================================================
# 12. Slash Commands
# =========================================================
@bot.event
async def on_ready():
    bot.add_view(PanelView())
    bot.add_view(SpinView())
    bot.add_view(TicketPanelView())
    bot.add_view(TicketControlView())
    bot.add_view(VerifyView())
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Sync error: {e}")
    print(f"Logged in as {bot.user}")

# === /guess コマンド ===
@bot.tree.command(name="guess", description="[ADMIN] Start a Guess the Number game in a new channel")
@app_commands.describe(
    number="The correct number to guess",
    prize="The prize (e.g., '1d', 'lifetime', '1000 Coins')"
)
@app_commands.checks.has_permissions(administrator=True)
async def guess_cmd(interaction: discord.Interaction, number: int, prize: str):
    guild = interaction.guild
    
    channel_name = "guess-the-number"
    existing = discord.utils.get(guild.channels, name=channel_name)
    if existing:
        await existing.delete()
        
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    
    channel = await guild.create_text_channel(
        name=channel_name,
        overwrites=overwrites,
        topic=f"Guess the number! Prize: {prize}"
    )
    
    guess_games[channel.id] = {
        "number": number,
        "prize": prize,
        "active": True,
        "winner": None
    }
    
    embed = discord.Embed(
        title="🔢 Guess the Number!",
        description=(
            f"**Prize:** {prize}\n\n"
            "Simply type a number in this channel to guess.\n"
            "You can guess as many times as you want!\n"
            "**No hints will be given.** Good luck!"
        ),
        color=discord.Color.blue()
    )
    await channel.send(embed=embed)
    await interaction.response.send_message(f"✅ Game started in {channel.mention}", ephemeral=True)

@bot.tree.command(name="verify_setup", description="[ADMIN] Post the verify panel")
@app_commands.checks.has_permissions(administrator=True)
async def verify_setup(interaction: discord.Interaction):
    embed = discord.Embed(
        title="✅ Verification",
        description="Click the button below to verify yourself and gain access to the server.",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, view=VerifyView())

@bot.tree.command(name="setchannel", description="[OWNER] Set the channel to log user links")
@app_commands.describe(channel="The channel to send link logs to")
@app_commands.checks.has_permissions(administrator=True)
async def setchannel_cmd(interaction: discord.Interaction, channel: discord.TextChannel):
    log_config["link_log_channel_id"] = channel.id
    embed = discord.Embed(
        title="⚙️ Log Channel Set",
        description=f"Link logs will now be sent to {channel.mention}",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="link", description="[ADMIN] Send a message with a clickable link button")
@app_commands.describe(
    url="The URL to link to", 
    label="The text to display on the button (default: Game link)"
)
@app_commands.checks.has_permissions(administrator=True)
async def link_cmd(interaction: discord.Interaction, url: str, label: str = "Game link"):
    embed = discord.Embed(
        description="Click the button below to join the game.",
        color=discord.Color.dark_theme()
    )
    view = LinkButtonView(url=url, label=label)
    await interaction.response.send_message("✅ Link panel sent!", ephemeral=True)
    await interaction.channel.send(embed=embed, view=view)

@bot.tree.command(name="clear", description="Clear messages in the current channel")
@app_commands.describe(amount="Number of messages to delete (default: 100)")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear_cmd(interaction: discord.Interaction, amount: int = 100):
    if amount < 1:
        await interaction.response.send_message("❌ Please specify a number greater than 0.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    try:
        deleted_count = 0
        while amount > 0:
            to_delete = min(amount, 100)
            deleted = await interaction.channel.purge(limit=to_delete)
            deleted_count += len(deleted)
            amount -= to_delete
            if len(deleted) < to_delete:
                break
        embed = discord.Embed(title="🧹 Channel Cleared", description=f"Successfully deleted **{deleted_count}** messages.", color=discord.Color.green())
        await interaction.followup.send(embed=embed, ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("❌ I don't have permission to delete messages here.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ An error occurred: {e}", ephemeral=True)

@bot.tree.command(name="text", description="Make the bot say something as a normal message")
@app_commands.describe(message="The message for the bot to send")
async def text_cmd(interaction: discord.Interaction, message: str):
    await interaction.response.send_message("✅ Message sent!", ephemeral=True)
    await interaction.channel.send(message)

@bot.tree.command(name="whitelist", description="[OWNER] Whitelist a user and give them a key with a specific duration")
@app_commands.describe(user="The user to whitelist", days="Duration in days (0 or blank for Lifetime)")
@app_commands.checks.has_permissions(administrator=True)
async def whitelist_cmd(interaction: discord.Interaction, user: discord.User, days: int = 0):
    whitelist.add(user.id)
    blacklist.discard(user.id)
    duration_hours = days * 24
    key = create_key(duration_hours=duration_hours if days > 0 else 0)
    if days == 0: duration_text = "Lifetime"
    elif days == 1: duration_text = "1 Day"
    else: duration_text = f"{days} Days"
    embed = discord.Embed(
        title="✅ You are Whitelisted!",
        description=f"You have been whitelisted by {interaction.user.mention}.\n\nHere is your **{duration_text} Key**:\n`{key}`\n\n*You can now use this key to redeem and access the panel.*",
        color=discord.Color.green()
    )
    if days == 0: embed.set_footer(text="This key will never expire.")
    else: embed.set_footer(text=f"This key is valid for {days} day(s).")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="unwhitelist", description="[OWNER] Remove a user from the whitelist")
@app_commands.describe(user="The user to remove from whitelist")
@app_commands.checks.has_permissions(administrator=True)
async def unwhitelist_cmd(interaction: discord.Interaction, user: discord.User):
    if user.id in whitelist:
        whitelist.discard(user.id)
        embed = discord.Embed(title="🗑️ User Unwhitelisted", description=f"{user.mention} has been removed from the whitelist.", color=discord.Color.orange())
    else:
        embed = discord.Embed(title="❌ Not Found", description=f"{user.mention} is not in the whitelist.", color=discord.Color.red())
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="blacklist", description="[OWNER] Blacklist a user from using the bot")
@app_commands.describe(user="The user to blacklist")
@app_commands.checks.has_permissions(administrator=True)
async def blacklist_cmd(interaction: discord.Interaction, user: discord.User):
    blacklist.add(user.id)
    whitelist.discard(user.id)
    embed = discord.Embed(title="🚫 User Blacklisted", description=f"{user.mention} has been blacklisted. They can no longer use the panel or commands.", color=discord.Color.red())
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="unblacklist", description="[OWNER] Remove a user from the blacklist")
@app_commands.describe(user="The user to remove from blacklist")
@app_commands.checks.has_permissions(administrator=True)
async def unblacklist_cmd(interaction: discord.Interaction, user: discord.User):
    if user.id in blacklist:
        blacklist.discard(user.id)
        embed = discord.Embed(title="🔓 User Unblacklisted", description=f"{user.mention} has been removed from the blacklist.", color=discord.Color.green())
    else:
        embed = discord.Embed(title="❌ Not Found", description=f"{user.mention} is not in the blacklist.", color=discord.Color.red())
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="set_roles", description="[OWNER] Set the roles for verification and tickets")
@app_commands.describe(
    verified_role="Role name to give on 'Get Role'",
    verify_role="Role name to give on 'Verify' button",
    ticket_roles="Role names to mention in tickets (comma separated, e.g. Support, Admin, Mod)"
)
@app_commands.checks.has_permissions(administrator=True)
async def set_roles(interaction: discord.Interaction, verified_role: str = None, verify_role: str = None, ticket_roles: str = None):
    if verified_role: role_config["verified_role"] = verified_role
    if verify_role: role_config["verify_role"] = verify_role
    if ticket_roles: role_config["ticket_access_roles"] = [r.strip() for r in ticket_roles.split(",")]
    
    embed = discord.Embed(title="⚙️ Role Settings Updated", color=discord.Color.green())
    embed.add_field(name="Verified Role (Panel)", value=f"`{role_config['verified_role']}`", inline=False)
    embed.add_field(name="Verify Role (Verify Button)", value=f"`{role_config['verify_role']}`", inline=False)
    roles_text = ", ".join([f"`{r}`" for r in role_config["ticket_access_roles"]])
    embed.add_field(name="Ticket Access Roles", value=roles_text, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="spin_config", description="[OWNER] Configure spin wheel settings and odds")
@app_commands.describe(
    cooldown="Cooldown in seconds", 
    win_chance="Overall win chance (0-100%)", 
    weight_1h="Weight for 1 Hour Key",
    weight_1d="Weight for 1 Day Key",
    weight_1w="Weight for 1 Week Key",
    weight_lt="Weight for Lifetime Key"
)
@app_commands.checks.has_permissions(administrator=True)
async def spin_config_cmd(interaction: discord.Interaction, cooldown: int = None, win_chance: int = None, weight_1h: int = None, weight_1d: int = None, weight_1w: int = None, weight_lt: int = None):
    if cooldown is not None: spin_config["cooldown_seconds"] = cooldown
    if win_chance is not None: spin_config["win_chance"] = max(0, min(100, win_chance))
    for p in spin_config["prizes"]:
        if p["name"] == "1 Hour Key" and weight_1h is not None: p["weight"] = weight_1h
        if p["name"] == "1 Day Key" and weight_1d is not None: p["weight"] = weight_1d
        if p["name"] == "1 Week Key" and weight_1w is not None: p["weight"] = weight_1w
        if p["name"] == "Lifetime Key" and weight_lt is not None: p["weight"] = weight_lt
    embed = discord.Embed(title="⚙️ Spin Settings Updated", color=discord.Color.green())
    embed.add_field(name="Cooldown", value=f"`{spin_config['cooldown_seconds']}`s", inline=True)
    embed.add_field(name="Win Chance", value=f"`{spin_config['win_chance']}`%", inline=True)
    prize_list = "\n".join([f"- {p['name']}: `{p['weight']}`" for p in spin_config["prizes"]])
    embed.add_field(name="Prize Weights", value=prize_list, inline=False)
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
    embed = discord.Embed(title="🎰 Spin for Keys", description="Click below to spin and win free keys!\n\n**Prizes:**\n1 Hour, 1 Day, 1 Week, Lifetime", color=discord.Color.gold())
    await interaction.response.send_message(embed=embed, view=SpinView())

@bot.tree.command(name="ticket_setup", description="[ADMIN] Post the ticket panel")
@app_commands.checks.has_permissions(administrator=True)
async def ticket_setup(interaction: discord.Interaction):
    embed = discord.Embed(title="🎫 Support Tickets", description="Need help? Click the button below to open a private ticket with our support team.", color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed, view=TicketPanelView())

@bot.tree.command(name="generatekey", description="[ADMIN] Generate a key")
@app_commands.describe(duration="Duration", custom_days="Custom days (if applicable)")
@app_commands.choices(duration=[
    app_commands.Choice(name="1 Hour", value="1h"),
    app_commands.Choice(name="1 Day", value="1d"),
    app_commands.Choice(name="1 Week", value="1w"),
    app_commands.Choice(name="Lifetime", value="lifetime"),
    app_commands.Choice(name="Custom (Use custom_days)", value="custom")
])
@app
