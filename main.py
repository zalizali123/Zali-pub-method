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
# 2. In-Memory Database & Settings
# =========================================================
keys_db = {}
linked_users = {} 
whitelist = set()   
blacklist = set()   
user_coins = {} 
user_spins = {} 

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

guess_games = {}

coin_shop_items = {
    "1h": {"name": "1 Hour Key", "price": 100, "type": "key", "hours": 1},
    "1d": {"name": "1 Day Key", "price": 500, "type": "key", "hours": 24},
    "1w": {"name": "1 Week Key", "price": 2000, "type": "key", "hours": 168},
    "lifetime": {"name": "Lifetime Key", "price": 10000, "type": "key", "hours": 0},
    "spin": {"name": "1x Spin Ticket", "price": 50, "type": "spin"}
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

def get_coins(user_id): return user_coins.get(user_id, 0)
def add_coins(user_id, amount): user_coins[user_id] = get_coins(user_id) + amount
def get_spins(user_id): return user_spins.get(user_id, 0)
def add_spins(user_id, amount): user_spins[user_id] = get_spins(user_id) + amount

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

class TicketModal(discord.ui.Modal, title="Open a Ticket"):
    reason = discord.ui.TextInput(
        label="Reason for contact",
        style=discord.TextStyle.paragraph,
        placeholder="Type 'mm' if you need a middleman, or describe your issue...",
        required=True,
        max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user
        reason_text = self.reason.value
        
        access_role_names = role_config["ticket_access_roles"]
        existing_channel = discord.utils.get(guild.channels, name=f"ticket-{user.name.lower()}")
        if existing_channel:
            await interaction.response.send_message(f"❌ You already have an open ticket: {existing_channel.mention}", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
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
            name=f"ticket-{user.name}",
            overwrites=overwrites,
            category=interaction.channel.category 
        )
        
        # チケット作成完了の応答
        await interaction.response.send_message(f"✅ Ticket created: {ticket_channel.mention}", ephemeral=True)

        # Auto MM 判定
        is_mm = "mm" in reason_text.lower() or "middleman" in reason_text.lower()
        
        if is_mm:
            embed = discord.Embed(
                title="🤝 Middleman Request",
                description=f"Hello {user.mention}, your middleman request has been received.\n\n**Your Reason:**\n> {reason_text}\n\nPlease wait a moment while a staff member reviews this. If you can, please provide the following:\n- The other party's Discord/Username\n- What item/amount is being traded",
                color=discord.Color.gold()
            )
        else:
            embed = discord.Embed(
                title="🎫 Support Ticket",
                description=f"Hello {user.mention}, your ticket has been created.\n\n**Your Reason:**\n> {reason_text}\n\nA staff member will be with you shortly.",
                color=discord.Color.green()
            )

        await ticket_channel.send(content=mention_text, embed=embed, view=TicketControlView())

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
        
        can_free_spin = False
        if user_id not in spin_config["last_spin_times"]:
            can_free_spin = True
        else:
            last_time = spin_config["last_spin_times"][user_id]
            elapsed = (now - last_time).total_seconds()
            if elapsed >= spin_config["cooldown_seconds"]:
                can_free_spin = True

        if can_free_spin:
            spin_config["last_spin_times"][user_id] = now
        else:
            spins = get_spins(user_id)
            if spins <= 0:
                remaining = int(spin_config["cooldown_seconds"] - (now - spin_config["last_spin_times"][user_id]).total_seconds())
                await interaction.response.send_message(f"⏳ Please wait `{remaining}` seconds for a free spin, or buy a Spin Ticket in the shop!", ephemeral=True)
                return
            user_spins[user_id] = spins - 1

        button.disabled = True
        await interaction.response.edit_message(content="🎰 **Spinning...**", view=self)
        await asyncio.sleep(1.5)
        
        win_roll = random.randint(1, 100)
        if win_roll > spin_config["win_chance"]:
            add_coins(user_id, 10)
            embed = discord.Embed(title="Spin Result", description="❌ **You didn't win this time.**\nBut you got `10 Coins` for trying!", color=discord.Color.red())
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            prizes = spin_config["prizes"]
            weights = [p["weight"] for p in prizes]
            chosen_prize = random.choices(prizes, weights=weights, k=1)[0]
            
            key = create_key(duration_hours=chosen_prize["hours"])
            dm_success = await send_key_dm(interaction.user, key, chosen_prize["name"])
            
            if dm_success:
                embed = discord.Embed(title="🎉 You Won!", description=f"Congratulations! You won a **{chosen_prize['name']}**.\n**Please check your DMs** to get the key.", color=discord.Color.gold())
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                embed = discord.Embed(title="🎉 You Won! (DM Failed)", description=f"Your DMs are closed, here is your key:\n`{key}`", color=discord.Color.gold())
                await interaction.followup.send(embed=embed, ephemeral=True)
        
        button.disabled = False
        try: await interaction.edit_original_response(content="Click to spin!", view=self)
        except: pass

# =========================================================
# 7. UI: Shop System Views
# =========================================================
class ShopSelect(discord.ui.Select):
    def __init__(self):
        options = []
        for key, item in coin_shop_items.items():
            options.append(discord.SelectOption(
                label=item["name"],
                description=f"Price: {item['price']} Coins",
                value=key
            ))
        super().__init__(placeholder="Select an item to buy...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id in blacklist:
            await interaction.response.send_message("❌ You are blacklisted.", ephemeral=True)
            return

        item_id = self.values[0]
        item_data = coin_shop_items[item_id]
        price = item_data["price"]
        user_id = interaction.user.id
        
        if get_coins(user_id) < price:
            await interaction.response.send_message(f"❌ Not enough coins! You need `{price - get_coins(user_id)}` more.", ephemeral=True)
            return
            
        user_coins[user_id] = get_coins(user_id) - price
        
        if item_data["type"] == "key":
            key = create_key(item_data.get("hours", 0))
            dm_success = await send_key_dm(interaction.user, key, item_data["name"])
            if dm_success:
                await interaction.response.send_message(f"✅ Bought **{item_data['name']}**! Please check your DMs.", ephemeral=True)
            else:
                await interaction.response.send_message(f"✅ Bought **{item_data['name']}**! Here is your key (DMs closed):\n`{key}`", ephemeral=True)
                
        elif item_data["type"] == "spin":
            add_spins(user_id, 1)
            await interaction.response.send_message(f"✅ Bought **{item_data['name']}**! You can now use it on the Spin panel.", ephemeral=True)
            
        elif item_data["type"] == "custom":
            await interaction.response.send_message(f"✅ Bought **{item_data['name']}**! Please contact staff to claim it.", ephemeral=True)

class ShopView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ShopSelect())

# =========================================================
# 8. UI: Ticket System Views
# =========================================================
class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.success, emoji="🎫", custom_id="btn_create_ticket")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in blacklist:
            await interaction.response.send_message("❌ You are blacklisted.", ephemeral=True)
            return
        # モーダルを開く
        await interaction.response.send_modal(TicketModal())

class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="btn_close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Closing ticket in 3 seconds...", ephemeral=True)
        await asyncio.sleep(3)
        await interaction.channel.delete()

# =========================================================
# 9. UI: Link Button & Verify Views
# =========================================================
class LinkButtonView(discord.ui.View):
    def __init__(self, url: str, label: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label=label, style=discord.ButtonStyle.link, url=url, emoji="🎮"))

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
        embed.add_field(name="Coins", value=f"`{get_coins(interaction.user.id)}`", inline=True)
        embed.add_field(name="Spins", value=f"`{get_spins(interaction.user.id)}`", inline=True)
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
    if message.author.bot: return

    if message.channel.id in guess_games:
        game = guess_games[message.channel.id]
        if not game["active"]: return

        try: guess = int(message.content.strip())
        except ValueError: return

        if guess == game["number"]:
            game["active"] = False
            game["winner"] = message.author.id
            
            overwrites = message.channel.overwrites_for(message.guild.default_role)
            overwrites.send_messages = False
            await message.channel.set_permissions(message.guild.default_role, overwrite=overwrites)
            
            prize_name = game["prize"]
            win_embed = discord.Embed(title="🎉 We have a winner!", description=f"{message.author.mention} guessed the correct number: **{game['number']}**\n\n**Prize:** {prize_name}", color=discord.Color.gold())
            
            if prize_name.lower() in ["1h", "1d", "1w", "lifetime"]:
                if prize_name.lower() == "1h": hours = 1
                elif prize_name.lower() == "1d": hours = 24
                elif prize_name.lower() == "1w": hours = 168
                else: hours = 0
                
                key = create_key(duration_hours=hours)
                dm_success = await send_key_dm(message.author, key, f"{prize_name} Key")
                
                if dm_success:
                    win_embed.add_field(name="Your Key", value="Check your DMs!", inline=False)
                else:
                    win_embed.add_field(name="Your Key", value=f"Your DMs are closed, so here it is:\n`{key}`", inline=False)
            
            add_coins(message.author.id, 100)
            win_embed.add_field(name="Bonus", value="+100 Coins!", inline=False)

            await message.channel.send(embed=win_embed)
            
            lock_embed = discord.Embed(title="🔒 Locked", description="This game has ended and the channel is now locked.", color=discord.Color.dark_gray())
            await message.channel.send(embed=lock_embed)
        else:
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
    bot.add_view(ShopView())
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Sync error: {e}")
    print(f"Logged in as {bot.user}")

# エラーハンドリング
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("❌ You do not have permission to use this command. (Administrator only)", ephemeral=True)

# === /roles (一般ユーザーも実行可能) ===
@bot.tree.command(name="roles", description="Get your verified role")
async def roles_cmd(interaction: discord.Interaction):
    if interaction.user.id in blacklist:
        await interaction.response.send_message("❌ You are blacklisted.", ephemeral=True)
        return

    role_name = role_config["verify_role"]
    role = discord.utils.get(interaction.guild.roles, name=role_name)
    
    if role:
        try:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(f"✅ Gave you the `{role_name}` role!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to give you this role.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ Role `{role_name}` not found. Please contact staff.", ephemeral=True)

# === /balance (誰でも使える) ===
@bot.tree.command(name="balance", description="Check your balance")
async def balance_cmd(interaction: discord.Interaction):
    coins = get_coins(interaction.user.id)
    spins = get_spins(interaction.user.id)
    embed = discord.Embed(title="💰 Your Balance", description=f"**Coins:** `{coins}`\n**Spin Tickets:** `{spins}`", color=discord.Color.gold())
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# === ここから下はすべて管理者権限必須 ===

@bot.tree.command(name="shop_setup", description="[ADMIN] Post the coin shop panel")
@app_commands.checks.has_permissions(administrator=True)
async def shop_setup_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛒 Coin Shop",
        description="Select an item from the dropdown below to purchase it with your Coins.",
        color=discord.Color.gold()
    )
    for key, item in coin_shop_items.items():
        embed.add_field(name=item["name"], value=f"`{item['price']}` Coins", inline=True)
    
    await interaction.response.send_message("✅ Shop panel created!", ephemeral=True)
    await interaction.channel.send(embed=embed, view=ShopView())

@bot.tree.command(name="coin_config", description="[ADMIN] Configure coin shop items")
@app_commands.describe(item_id="ID", name="Name", price="Price", item_type="Type", hours="Duration (if key)")
@app_commands.choices(item_type=[
    app_commands.Choice(name="Key", value="key"),
    app_commands.Choice(name="Spin Ticket", value="spin"),
    app_commands.Choice(name="Custom/Other", value="custom")
])
@app_commands.checks.has_permissions(administrator=True)
async def coin_config_cmd(interaction: discord.Interaction, item_id: str, name: str, price: int, item_type: app_commands.Choice[str], hours: int = 0):
    coin_shop_items[item_id] = {"name": name, "price": price, "type": item_type.value, "hours": hours}
    await interaction.response.send_message(f"✅ Configured `{item_id}`: {name} for {price} coins.", ephemeral=True)

@bot.tree.command(name="addcoins", description="[ADMIN] Add coins or spins to a user")
@app_commands.describe(user="User", amount="Amount", currency="Type")
@app_commands.choices(currency=[
    app_commands.Choice(name="Coins", value="coins"),
    app_commands.Choice(name="Spin Tickets", value="spins")
])
@app_commands.checks.has_permissions(administrator=True)
async def addcurrency_cmd(interaction: discord.Interaction, user: discord.User, amount: int, currency: app_commands.Choice[str]):
    if currency.value == "coins":
        add_coins(user.id, amount)
        await interaction.response.send_message(f"✅ Added {amount} coins to {user.mention}.", ephemeral=True)
    else:
        add_spins(user.id, amount)
        await interaction.response.send_message(f"✅ Added {amount} spins to {user.mention}.", ephemeral=True)

@bot.tree.command(name="guess", description="[ADMIN] Start a Guess the Number game")
@app_commands.describe(number="Correct number", prize="Prize")
@app_commands.checks.has_permissions(administrator=True)
async def guess_cmd(interaction: discord.Interaction, number: int, prize: str):
    channel = interaction.channel
    if channel.id in guess_games and guess_games[channel.id]["active"]:
        await interaction.response.send_message("❌ Game already running.", ephemeral=True)
        return
    guess_games[channel.id] = {"number": number, "prize": prize, "active": True, "winner": None}
    
    overwrites = channel.overwrites_for(interaction.guild.default_role)
    overwrites.send_messages = True
    await channel.set_permissions(interaction.guild.default_role, overwrite=overwrites)
    
    embed = discord.Embed(title="🔢 Guess the Number!", description=f"**Prize:** {prize}\n\nType a number to guess!", color=discord.Color.blue())
    await interaction.response.send_message("✅ Game started!", ephemeral=True)
    await channel.send(embed=embed)

@bot.tree.command(name="verify_setup", description="[ADMIN] Post verify panel")
@app_commands.checks.has_permissions(administrator=True)
async def verify_setup(interaction: discord.Interaction):
    embed = discord.Embed(title="✅ Verification", description="Click to verify.", color=discord.Color.green())
    await interaction.response.send_message(embed=embed, view=VerifyView())

@bot.tree.command(name="setchannel", description="[ADMIN] Set log channel")
@app_commands.describe(channel="The channel")
@app_commands.checks.has_permissions(administrator=True)
async def setchannel_cmd(interaction: discord.Interaction, channel: discord.TextChannel):
    log_config["link_log_channel_id"] = channel.id
    await interaction.response.send_message(f"✅ Link logs set to {channel.mention}", ephemeral=True)

@bot.tree.command(name="link", description="[ADMIN] Send a link button")
@app_commands.describe(url="URL", label="Button text")
@app_commands.checks.has_permissions(administrator=True)
async def link_cmd(interaction: discord.Interaction, url: str, label: str = "Game link"):
    embed = discord.Embed(description="Click the button below.", color=discord.Color.dark_theme())
    view = LinkButtonView(url=url, label=label)
    await interaction.response.send_message("✅ Sent!", ephemeral=True)
    await interaction.channel.send(embed=embed, view=view)

@bot.tree.command(name="clear", description="[ADMIN] Clear messages")
@app_commands.describe(amount="Amount")
@app_commands.checks.has_permissions(administrator=True)
async def clear_cmd(interaction: discord.Interaction, amount: int = 100):
    if amount < 1: return
    await interaction.response.defer(ephemeral=True)
    try:
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(f"✅ Deleted {len(deleted)} messages.", ephemeral=True)
    except:
        await interaction.followup.send("❌ Error deleting messages.", ephemeral=True)

@bot.tree.command(name="text", description="[ADMIN] Make bot say something")
@app_commands.describe(message="Message")
@app_commands.checks.has_permissions(administrator=True)
async def text_cmd(interaction: discord.Interaction, message: str):
    await interaction.response.send_message("✅ Sent!", ephemeral=True)
    await interaction.channel.send(message)

@bot.tree.command(name="whitelist", description="[ADMIN] Whitelist a user")
@app_commands.describe(user="User", days="Days (0 for Lifetime)")
@app_commands.checks.has_permissions(administrator=True)
async def whitelist_cmd(interaction: discord.Interaction, user: discord.User, days: int = 0):
    whitelist.add(user.id); blacklist.discard(user.id)
    key = create_key(duration_hours=(days*24) if days > 0 else 0)
    duration_text = "Lifetime" if days == 0 else f"{days} Days"
    embed = discord.Embed(title="✅ Whitelisted!", description=f"Here is your **{duration_text} Key**:\n`{key}`", color=discord.Color.green())
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="unwhitelist", description="[ADMIN] Unwhitelist a user")
@app_commands.describe(user="User")
@app_commands.checks.has_permissions(administrator=True)
async def unwhitelist_cmd(interaction: discord.Interaction, user: discord.User):
    whitelist.discard(user.id)
    await interaction.response.send_message(f"✅ Removed {user.mention} from whitelist.", ephemeral=True)

@bot.tree.command(name="blacklist", description="[ADMIN] Blacklist a user")
@app_commands.describe(user="User")
@app_commands.checks.has_permissions(administrator=True)
async def blacklist_cmd(interaction: discord.Interaction, user: discord.User):
    blacklist.add(user.id); whitelist.discard(user.id)
    await interaction.response.send_message(f"🚫 {user.mention} blacklisted.", ephemeral=True)

@bot.tree.command(name="unblacklist", description="[ADMIN] Unblacklist a user")
@app_commands.describe(user="User")
@app_commands.checks.has_permissions(administrator=True)
async def unblacklist_cmd(interaction: discord.Interaction, user: discord.User):
    blacklist.discard(user.id)
    await interaction.response.send_message(f"🔓 {user.mention} removed from blacklist.", ephemeral=True)

@bot.tree.command(name="set_roles", description="[ADMIN] Set roles")
@app_commands.describe(verified_role="Panel Get Role", verify_role="Verify Button", ticket_roles="Comma separated")
@app_commands.checks.has_permissions(administrator=True)
async def set_roles(interaction: discord.Interaction, verified_role: str = None, verify_role: str = None, ticket_roles: str = None):
    if verified_role: role_config["verified_role"] = verified_role
    if verify_role: role_config["verify_role"] = verify_role
    if ticket_roles: role_config["ticket_access_roles"] = [r.strip() for r in ticket_roles.split(",")]
    await interaction.response.send_message("✅ Roles updated.", ephemeral=True)

@bot.tree.command(name="spin_config", description="[ADMIN] Configure spin odds")
@app_commands.describe(cooldown="Cooldown in seconds", win_chance="Win chance %", weight_1h="1h", weight_1d="1d", weight_1w="1w", weight_lt="lt")
@app_commands.checks.has_permissions(administrator=True)
async def spin_config_cmd(interaction: discord.Interaction, cooldown: int = None, win_chance: int = None, weight_1h: int = None, weight_1d: int = None, weight_1w: int = None, weight_lt: int = None):
    if cooldown is not None: spin_config["cooldown_seconds"] = cooldown
    if win_chance is not None: spin_config["win_chance"] = max(0, min(100, win_chance))
    for p in spin_config["prizes"]:
        if p["name"] == "1 Hour Key" and weight_1h is not None: p["weight"] = weight_1h
        if p["name"] == "1 Day Key" and weight_1d is not None: p["weight"] = weight_1d
        if p["name"] == "1 Week Key" and weight_1w is not None: p["weight"] = weight_1w
        if p["name"] == "Lifetime Key" and weight_lt is not None: p["weight"] = weight_lt
    
    prize_list = "\n".join([f"- {p['name']}: `{p['weight']}`" for p in spin_config["prizes"]])
    embed = discord.Embed(title="⚙️ Spin Settings Updated", description=f"Cooldown: `{spin_config['cooldown_seconds']}s`\nWin Chance: `{spin_config['win_chance']}%`\n{prize_list}", color=discord.Color.green())
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="send_embed", description="[ADMIN] Send custom embed")
@app_commands.describe(title="Title", description="Description", color_hex="Hex", footer="Footer", image_url="Image")
@app_commands.checks.has_permissions(administrator=True)
async def send_embed(interaction: discord.Interaction, title: str, description: str, color_hex: str = "5865F2", footer: str = None, image_url: str = None):
    try: color = discord.Color(int(color_hex.replace("#", ""), 16))
    except: color = discord.Color.blurple()
    embed = discord.Embed(title=title, description=description, color=color)
    if footer: embed.set_footer(text=footer)
    if image_url: embed.set_image(url=image_url)
    await interaction.response.send_message("✅ Sent!", ephemeral=True)
    await interaction.channel.send(embed=embed)

@bot.tree.command(name="spin_setup", description="[ADMIN] Post spin panel")
@app_commands.checks.has_permissions(administrator=True)
async def spin_setup(interaction: discord.Interaction):
    embed = discord.Embed(title="🎰 Spin for Keys", description="Click below to use your free spin or a Spin Ticket to win keys!", color=discord.Color.gold())
    await interaction.response.send_message(embed=embed, view=SpinView())

@bot.tree.command(name="ticket_setup", description="[ADMIN] Post ticket panel")
@app_commands.checks.has_permissions(administrator=True)
async def ticket_setup(interaction: discord.Interaction):
    embed = discord.Embed(title="🎫 Support Tickets", description="Click to open a ticket.", color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed, view=TicketPanelView())

@bot.tree.command(name="generatekey", description="[ADMIN] Generate key")
@app_commands.describe(duration="Duration", custom_days="Custom days")
@app_commands.choices(duration=[
    app_commands.Choice(name="1 Hour", value="1h"),
    app_commands.Choice(name="1 Day", value="1d"),
    app_commands.Choice(name="1 Week", value="1w"),
    app_commands.Choice(name="Lifetime", value="lifetime"),
    app_commands.Choice(name="Custom", value="custom")
])
@app_commands.checks.has_permissions(administrator=True)
async def generatekey(interaction: discord.Interaction, duration: app_commands.Choice[str], custom_days: int = None):
    if duration.value == "lifetime": key = create_key(0); text = "Lifetime"
    elif duration.value == "1h": key = create_key(1); text = "1 Hour"
    elif duration.value == "1d": key = create_key(24); text = "1 Day"
    elif duration.value == "1w": key = create_key(168); text = "1 Week"
    elif duration.value == "custom":
        if not custom_days or custom_days <= 0: return
        key = create_key(custom_days * 24); text = f"{custom_days} Days"
    
    embed = discord.Embed(title="🔑 Key Generated", color=discord.Color.green())
    embed.add_field(name="Key", value=f"`{key}`", inline=False)
    embed.add_field(name="Duration", value=text, inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="panel", description="[ADMIN] Post main panel")
@app_commands.checks.has_permissions(administrator=True)
async def panel(interaction: discord.Interaction):
    embed = discord.Embed(title="Panel Access", description="Win or buy a key to unlock.", color=discord.Color.blue())
    await interaction.response.send_message(embed=embed, view=PanelView())

# =========================================================
# 13. Start
# =========================================================
if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    bot.run(os.environ["DISCORD_TOKEN"])
