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
    "prize_weights": {"1h": 50, "1d": 30, "4d": 20},
    "last_spin_times": {}
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
def create_key(duration_hours=None, duration_days=None):
    chars = string.ascii_uppercase + string.digits
    new_key = ''.join(random.choices(chars, k=16))
    now = datetime.now(timezone.utc)
    expires_at = None
    if duration_hours: expires_at = now + timedelta(hours=duration_hours)
    elif duration_days: expires_at = now + timedelta(days=duration_days)
    keys_db[new_key] = {"expires_at": expires_at, "used_by": None}
    return new_key

# DM送信用の関数（エラー対策込み）
async def send_key_dm(user: discord.User, key: str, prize_name: str):
    embed = discord.Embed(
        title="🎉 You Won!",
        description=f"Congratulations! You won a **{prize_name}**.\n\nYour key (tap to copy):\n```\n{key}\n```\n*Click 'Redeem Key' on the server to use it.*",
        color=discord.Color.gold()
    )
    try:
        await user.send(embed=embed)
        return True
    except discord.Forbidden:
        return False # DMが閉じている場合

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
        await interaction.response.send_message(f"✅ Linked to Roblox user: **{self.username_input.value}**", ephemeral=True)

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
                await interaction.response.send_message(f"⏳ Please wait **{remaining} seconds**.", ephemeral=True)
                return

        spin_config["last_spin_times"][user_id] = now
        
        button.disabled = True
        await interaction.response.edit_message(content="🎰 **Spinning the wheel...**", view=self)
        await asyncio.sleep(1.5)
        
        win_roll = random.randint(1, 100)
        if win_roll > spin_config["win_chance"]:
            embed = discord.Embed(title="Spin Result", description="❌ **You didn't win this time.**", color=discord.Color.red())
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            weights = spin_config["prize_weights"]
            prize = random.choices(list(weights.keys()), weights=list(weights.values()), k=1)[0]
            
            if prize == "1h":
                key = create_key(duration_hours=1)
                prize_name = "1 Hour Key"
            elif prize == "1d":
                key = create_key(duration_days=1)
                prize_name = "1 Day Key"
            else:
                key = create_key(duration_days=4)
                prize_name = "4 Day Key"

            # DM送信を試みる
            dm_success = await send_key_dm(interaction.user, key, prize_name)
            
            if dm_success:
                # DMが送れたら、チャンネルには「当選しました。DMを確認してください」とだけ表示
                embed = discord.Embed(
                    title="🎉 You Won!",
                    description=f"Congratulations! You won a **{prize_name}**.\n**Please check your DMs** to get the key.\n*(If you don't receive a DM, your privacy settings are blocking it.)*",
                    color=discord.Color.gold()
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                # DMが送れなかった場合、泣く泣くチャンネルに表示
                embed = discord.Embed(
                    title="🎉 You Won! (DM Failed)",
                    description=f"Your DMs are closed, so here is your key:\n```\n{key}\n```",
                    color=discord.Color.gold()
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
        
        button.disabled = False
        try:
            await interaction.edit_original_response(content="Click to spin!", view=self)
        except:
            pass

# =========================================================
# 7. UI: Main Panel View
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
        role_name = "Verified"
        role = discord.utils.get(interaction.guild.roles, name=role_name)
        if role:
            try:
                await interaction.user.add_roles(role)
                await interaction.response.send_message(f"✅ Gave you the {role_name} role!", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ No permission.", ephemeral=True)
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
            expiry_text = "Lifetime" if data["expires_at"] is None else data["expires_at"].strftime("%Y-%m-%d %H:%M UTC")
            embed.add_field(name="Active Key", value=f"`{key_code}`", inline=True)
            embed.add_field(name="Expires At", value=expiry_text, inline=True)
        else:
            embed.add_field(name="Active Key", value="None", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

# =========================================================
# 8. Commands
# =========================================================
@bot.event
async def on_ready():
    bot.add_view(PanelView())
    bot.add_view(SpinView())
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"Sync error: {e}")
    print(f"Logged in as {bot.user}")

# === オーナー専用: スピン設定変更 ===
@bot.tree.command(name="spin_config", description="[OWNER] Configure spin wheel settings")
@app_commands.describe(cooldown="Cooldown in seconds", win_chance="Win chance %", weight_1h="1H weight", weight_1d="1D weight", weight_4d="4D weight")
@app_commands.checks.has_permissions(administrator=True)
async def spin_config_cmd(interaction: discord.Interaction, cooldown: int = None, win_chance: int = None, weight_1h: int = None, weight_1d: int = None, weight_4d: int = None):
    if cooldown is not None: spin_config["cooldown_seconds"] = cooldown
    if win_chance is not None: spin_config["win_chance"] = max(0, min(100, win_chance))
    if weight_1h is not None: spin_config["prize_weights"]["1h"] = weight_1h
    if weight_1d is not None: spin_config["prize_weights"]["1d"] = weight_1d
    if weight_4d is not None: spin_config["prize_weights"]["4d"] = weight_4d
    w = spin_config["prize_weights"]
    embed = discord.Embed(title="⚙️ Spin Settings Updated", color=discord.Color.green())
    embed.add_field(name="Cooldown", value=f"{spin_config['cooldown_seconds']}s", inline=True)
    embed.add_field(name="Win Chance", value=f"{spin_config['win_chance']}%", inline=True)
    embed.add_field(name="Weights", value=f"1H:{w['1h']} | 1D:{w['1d']} | 4D:{w['4d']}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# === オーナー専用: 埋め込み投稿 ===
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

# === スピンパネル設置 ===
@bot.tree.command(name="spin_setup", description="[ADMIN] Post the spin wheel panel")
@app_commands.checks.has_permissions(administrator=True)
async def spin_setup(interaction: discord.Interaction):
    embed = discord.Embed(title="🎰 Spin for Keys", description="Click below to spin and win free keys!", color=discord.Color.gold())
    await interaction.response.send_message(embed=embed, view=SpinView())

# === キー生成 ===
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
        key = create_key(duration_days=1)
        duration_text = "1 Day"
    else:
        key = create_key(duration_days=4)
        duration_text = "4 Days"
    
    embed = discord.Embed(title="🔑 Key Generated", color=discord.Color.green())
    embed.add_field(name="Key", value=f"`\n{key}\n`", inline=False)
    embed.add_field(name="Duration", value=duration_text, inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# === メインパネル設置 ===
@bot.tree.command(name="panel", description="Post the main access panel")
async def panel(interaction: discord.Interaction):
    embed = discord.Embed(title="Panel Access", description="Win or buy a key to unlock.\n`Win/Buy ➔ Redeem ➔ Link ➔ Access`", color=discord.Color.blue())
    await interaction.response.send_message(embed=embed, view=PanelView())

# =========================================================
# 9. Start
# =========================================================
if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    bot.run(os.environ["DISCORD_TOKEN"])
