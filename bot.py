# bot.py — Fixed, merged, ready-to-deploy translator + giveaways + nuke + setup UI
# Requirements suggested (requirements.txt):
# discord.py==2.3.2
# googletrans==4.0.0-rc1
# deep-translator
# aiohttp==3.8.4
#
# Put your token in env var TOKEN. Optionally set GUILD_ID for instant slash sync.
# To enable prefix commands and auto-translate: set USE_MESSAGE_CONTENT_INTENT = True AND enable the privileged intent in Developer Portal.

import warnings
warnings.filterwarnings("ignore", message="PyNaCl is not installed")

import os
import json
import csv
import random
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

import discord
from discord.ext import commands
from discord import app_commands, File

# ---------- CONFIG ----------
LOG_LEVEL = logging.INFO
USE_MESSAGE_CONTENT_INTENT = False  # toggle True only after enabling privileged intent & updating token
SETTINGS_FILE = "settings.json"
GIVE_FILE = "giveaways.json"
MODLOG_FILE = "modlog.json"
BACKUP_DIR = "backups"
COUNTDOWN_INTERVAL = 10  # seconds between countdown embed updates
# --------------------------------

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("bot-fixed")

os.makedirs(BACKUP_DIR, exist_ok=True)

TOKEN = os.getenv("TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
GLOBAL_MODLOG_CHANNEL = os.getenv("MODLOG_CHANNEL_ID")

if not TOKEN:
    raise SystemExit("TOKEN not set in environment variables.")

# ---------- Intents & Bot ----------
intents = discord.Intents.default()
intents.message_content = USE_MESSAGE_CONTENT_INTENT
bot = commands.Bot(command_prefix=".", intents=intents)

# Remove default help (fixes CommandRegistrationError when adding custom help)
try:
    bot.remove_command("help")
except Exception:
    pass

tree = bot.tree

# ---------- Persistence helpers ----------
def load_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("Failed to load %s: %s", path, e)
        return {}

def save_json(path: str, data: Dict[str, Any]):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.error("Failed to save %s: %s", path, e)

state = {
    "settings": load_json(SETTINGS_FILE),
    "giveaways": load_json(GIVE_FILE),
    "modlog": load_json(MODLOG_FILE),
}

def save_all():
    save_json(SETTINGS_FILE, state.get("settings", {}))
    save_json(GIVE_FILE, state.get("giveaways", {}))
    save_json(MODLOG_FILE, state.get("modlog", {}))

# ---------- Mod log ----------
def log_action(guild_id: int, action: str, data: Dict[str, Any]):
    gid = str(guild_id)
    entry = {"time": datetime.utcnow().isoformat(), "action": action, "data": data}
    state["modlog"].setdefault(gid, []).append(entry)
    save_json(MODLOG_FILE, state["modlog"])
    # optionally post to global modlog channel
    if GLOBAL_MODLOG_CHANNEL:
        try:
            ch = bot.get_channel(int(GLOBAL_MODLOG_CHANNEL))
            if ch:
                asyncio.create_task(ch.send(f"[{datetime.utcnow().isoformat()}] {action} — {data}"))
        except Exception:
            pass

async def post_guild_modlog(guild: discord.Guild, text: str):
    gsettings = state["settings"].get(str(guild.id), {})
    modch = gsettings.get("_modlog_channel") or gsettings.get("modlog_channel")
    if modch:
        try:
            ch = guild.get_channel(int(modch))
            if ch:
                await ch.send(text)
                return
        except Exception:
            pass
    if GLOBAL_MODLOG_CHANNEL:
        ch = bot.get_channel(int(GLOBAL_MODLOG_CHANNEL))
        if ch:
            await ch.send(f"[{guild.name}] {text}")

# ---------- Utilities ----------
def human_td(seconds: int) -> str:
    seconds = max(0, int(seconds))
    td = timedelta(seconds=seconds)
    days = td.days
    hrs, rem = divmod(td.seconds, 3600)
    mins, secs = divmod(rem, 60)
    parts = []
    if days: parts.append(f"{days}d")
    if hrs: parts.append(f"{hrs}h")
    if mins: parts.append(f"{mins}m")
    if secs or not parts: parts.append(f"{secs}s")
    return " ".join(parts)

def translate_text(text: str, dest: str) -> str:
    # delayed imports to avoid startup crash if libs missing
    try:
        from googletrans import Translator as GT
        tr = GT()
        res = tr.translate(text, dest=dest)
        return getattr(res, "text", str(res))
    except Exception:
        try:
            from deep_translator import GoogleTranslator as DT
            return DT(source="auto", target=dest).translate(text)
        except Exception:
            raise RuntimeError("No translator library installed (googletrans or deep-translator).")

def ensure_settings_for_channel(guild_id: int, channel_id: int) -> Dict[str, Any]:
    gs = state.setdefault("settings", {})
    g = gs.setdefault(str(guild_id), {})
    c = g.setdefault(str(channel_id), {"lang": "en", "autotranslate": False})
    return c

# ---------- On ready ----------
@bot.event
async def on_ready():
    log.info("Bot online: %s (id:%s)", bot.user, getattr(bot.user, "id", None))
    # sync slash commands
    if GUILD_ID:
        try:
            guild_obj = discord.Object(id=int(GUILD_ID))
            await tree.sync(guild=guild_obj)
            log.info("Slash commands synced to guild %s", GUILD_ID)
        except Exception as e:
            log.warning("Guild sync failed: %s", e)
    else:
        try:
            await tree.sync()
            log.info("Global slash sync attempted")
        except Exception as e:
            log.warning("Global slash sync failed: %s", e)

    # resume active giveaways
    for gid, info in list(state.get("giveaways", {}).items()):
        if info.get("active"):
            try:
                ends_at = datetime.fromisoformat(info["ends_at"])
                seconds = int((ends_at - datetime.utcnow()).total_seconds())
                if seconds <= 0:
                    asyncio.create_task(_end_giveaway(gid))
                else:
                    asyncio.create_task(_run_countdown_task(gid, seconds))
            except Exception:
                log.exception("Error resuming giveaway %s", gid)

# ---------- Setup command (ephemeral UI) ----------
@tree.command(name="setup", description="Open channel setup UI (ephemeral)")
@app_commands.describe(modlog_channel="Channel for mod logs (optional)", autotranslate="Enable auto-translate in this channel (requires message content intent)", default_lang="Default translation language code")
async def slash_setup(interaction: discord.Interaction, modlog_channel: Optional[discord.TextChannel] = None, autotranslate: Optional[bool] = None, default_lang: Optional[str] = None):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("You need Manage Server permission to configure settings.", ephemeral=True)
        return

    gid = str(interaction.guild.id)
    gsettings = state.setdefault("settings", {}).setdefault(gid, {})
    channel_settings = gsettings.setdefault(str(interaction.channel.id), {"lang": "en", "autotranslate": False})

    changed = []
    if modlog_channel:
        gsettings["_modlog_channel"] = str(modlog_channel.id)
        changed.append(f"modlog -> {modlog_channel.mention}")
    if autotranslate is not None:
        channel_settings["autotranslate"] = bool(autotranslate)
        changed.append(f"autotranslate -> {channel_settings['autotranslate']}")
    if default_lang:
        channel_settings["lang"] = default_lang
        changed.append(f"default_lang -> {default_lang}")

    save_all()

    class SetupView(discord.ui.View):
        def __init__(self, timeout=60):
            super().__init__(timeout=timeout)

        @discord.ui.button(label="Set English", style=discord.ButtonStyle.secondary)
        async def en(self, button: discord.ui.Button, inter: discord.Interaction):
            gs = state.setdefault("settings", {}).setdefault(str(inter.guild.id), {})
            gs.setdefault(str(inter.channel.id), {})["lang"] = "en"
            save_all()
            await inter.response.send_message("Channel default language set to English.", ephemeral=True)

        @discord.ui.button(label="Set Hindi", style=discord.ButtonStyle.secondary)
        async def hi(self, button: discord.ui.Button, inter: discord.Interaction):
            gs = state.setdefault("settings", {}).setdefault(str(inter.guild.id), {})
            gs.setdefault(str(inter.channel.id), {})["lang"] = "hi"
            save_all()
            await inter.response.send_message("Channel default language set to Hindi.", ephemeral=True)

        @discord.ui.button(label="Toggle Auto-Translate", style=discord.ButtonStyle.primary)
        async def toggle_auto(self, button: discord.ui.Button, inter: discord.Interaction):
            gs = state.setdefault("settings", {}).setdefault(str(inter.guild.id), {})
            cfg = gs.setdefault(str(inter.channel.id), {"lang": "en", "autotranslate": False})
            cfg["autotranslate"] = not cfg.get("autotranslate", False)
            save_all()
            await inter.response.send_message(f"Auto-translate set to {cfg['autotranslate']}. Note: auto-translate needs message_content intent to function.", ephemeral=True)

    view = SetupView()
    text = "Settings updated: " + ", ".join(changed) if changed else "Open setup menu to configure this channel."
    await interaction.response.send_message(text, ephemeral=True, view=view)
    await post_guild_modlog(interaction.guild, f"Settings updated by {interaction.user}: {changed}")
    log_action(interaction.guild.id, "setup", {"by": str(interaction.user.id), "changes": changed})

# ---------- Translate commands ----------
@tree.command(name="translate", description="Translate message by ID (ephemeral)")
@app_commands.describe(message_id="Message ID to translate", channel="Channel containing message (optional)", lang="language code (e.g. en, hi)")
async def slash_translate(interaction: discord.Interaction, message_id: str, channel: Optional[discord.TextChannel] = None, lang: str = "en"):
    await interaction.response.defer(ephemeral=True)
    target = channel or interaction.channel
    try:
        mid = int(message_id)
    except:
        await interaction.followup.send("Message ID must be numeric.", ephemeral=True)
        return
    try:
        msg = await target.fetch_message(mid)
    except Exception:
        await interaction.followup.send("Message not found or cannot fetch.", ephemeral=True)
        return
    content = getattr(msg, "content", None)
    if not content:
        await interaction.followup.send("Target message has no text.", ephemeral=True)
        return
    try:
        translated = translate_text(content, lang)
        await interaction.followup.send(f"**Translation ({lang})**\n{translated}", ephemeral=True)
    except RuntimeError as e:
        await interaction.followup.send(str(e), ephemeral=True)

@bot.command(name="t")
async def prefix_translate(ctx: commands.Context, message_id: int, lang: str = "en"):
    if not USE_MESSAGE_CONTENT_INTENT:
        await ctx.reply("Prefix commands disabled (enable message content intent or use slash).", mention_author=False)
        return
    try:
        msg = await ctx.channel.fetch_message(message_id)
    except Exception:
        await ctx.reply("Message not found.", mention_author=False)
        return
    content = getattr(msg, "content", None)
    if not content:
        await ctx.reply("Target message has no text.", mention_author=False)
        return
    try:
        translated = translate_text(content, lang)
        try:
            await ctx.author.send(f"**Translation ({lang})**\n{translated}")
            await ctx.reply("Sent translation to your DMs.", mention_author=False)
        except Exception:
            await ctx.reply(f"**Translation ({lang})**\n{translated}", mention_author=False)
    except RuntimeError as e:
        await ctx.reply(str(e), mention_author=False)

# ---------- Help ----------
@tree.command(name="help", description="Show help (ephemeral)")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(title="Bot Help", description="Slash-first commands (ephemeral).", color=0x00ffcc)
    embed.add_field(name="/translate <message_id> <lang>", value="Translate message privately", inline=False)
    embed.add_field(name="/setup", value="Open setup UI (managers only)", inline=False)
    embed.add_field(name="/giveaway start/end/reroll/export", value="Giveaway manager commands", inline=False)
    embed.add_field(name="/nuke", value="Duplicate & delete current channel (confirmation)", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.command(name="help")
async def prefix_help(ctx: commands.Context):
    if not USE_MESSAGE_CONTENT_INTENT:
        await ctx.reply("Prefix help disabled (use slash /help).", mention_author=False)
        return
    txt = (
        "Prefix commands:\n"
        ".t <message_id> <lang>\n"
        ".giveaway start <duration> <winners> <prize>\n"
        ".nuke\n"
    )
    await ctx.reply(txt, mention_author=False)

# ---------- GIVEAWAY CORE ----------
def _gen_gid() -> str:
    return str(random.randint(100000, 999999))

async def _announce_giveaway(channel: discord.TextChannel, info: Dict[str, Any]) -> discord.Message:
    embed = discord.Embed(title="🎉 Giveaway!", description=info["prize"], color=0x57F287)
    embed.add_field(name="Giveaway ID", value=info["id"], inline=True)
    embed.add_field(name="Ends In", value=human_td(info["duration"]), inline=True)
    embed.set_footer(text=f"Winners: {info['winners']}")
    msg = await channel.send(embed=embed)
    try:
        await msg.add_reaction("🎉")
    except Exception:
        pass

    class EnterView(discord.ui.View):
        def __init__(self, gid):
            super().__init__(timeout=None)
            self.gid = gid

        @discord.ui.button(label="Enter Giveaway", style=discord.ButtonStyle.success, emoji="🎉")
        async def enter(self, button: discord.ui.Button, interaction: discord.Interaction):
            data = state.setdefault("giveaways", {})
            g = data.get(self.gid)
            if not g or not g.get("active"):
                await interaction.response.send_message("Giveaway closed.", ephemeral=True)
                return
            entrants = set(g.get("entrants", []))
            if str(interaction.user.id) in entrants:
                await interaction.response.send_message("You already entered.", ephemeral=True)
                return
            entrants.add(str(interaction.user.id))
            g["entrants"] = list(entrants)
            save_all()
            await interaction.response.send_message("Entered via button. Good luck!", ephemeral=True)

    view = EnterView(info["id"])
    await channel.send("Click to enter via button:", view=view)
    return msg

async def _run_countdown_task(gid: str, seconds: int):
    try:
        while True:
            info = state["giveaways"].get(gid)
            if not info or not info.get("active"):
                return
            ch = bot.get_channel(int(info["channel_id"]))
            if not ch:
                return
            try:
                msg = await ch.fetch_message(int(info["message_id"]))
            except Exception:
                return
            remaining = int((datetime.fromisoformat(info["ends_at"]) - datetime.utcnow()).total_seconds())
            try:
                embed = msg.embeds[0] if msg.embeds else discord.Embed(title="🎉 Giveaway", description=info["prize"])
                for i, f in enumerate(embed.fields):
                    if f.name == "Ends In":
                        embed.set_field_at(i, name="Ends In", value=human_td(remaining), inline=True)
                        break
                await msg.edit(embed=embed)
            except Exception:
                pass
            if remaining <= 0:
                await _end_giveaway(gid)
                return
            sleep_time = min(COUNTDOWN_INTERVAL, remaining)
            await asyncio.sleep(sleep_time)
    except asyncio.CancelledError:
        return
    except Exception:
        log.exception("Countdown error for giveaway %s", gid)

async def _end_giveaway(gid: str):
    data = state.setdefault("giveaways", {})
    info = data.get(gid)
    if not info or not info.get("active"):
        return
    ch = bot.get_channel(int(info["channel_id"]))
    if not ch:
        info["active"] = False
        save_all()
        return
    try:
        msg = await ch.fetch_message(int(info["message_id"]))
    except Exception:
        info["active"] = False
        save_all()
        return

    entrants = set(info.get("entrants", []))
    for react in msg.reactions:
        emoji = getattr(react.emoji, "name", react.emoji)
        if emoji == "🎉":
            async for u in react.users():
                if u.bot: continue
                entrants.add(str(u.id))

    entrants_list = list(entrants)
    winners = []
    if entrants_list:
        k = min(int(info.get("winners", 1)), len(entrants_list))
        winners = random.sample(entrants_list, k)
    if winners:
        mentions = " ".join(f"<@{w}>" for w in winners)
        await ch.send(f"🎉 Giveaway {gid} ended! Winners: {mentions}\nPrize: **{info['prize']}**")
    else:
        await ch.send(f"Giveaway {gid} ended. No valid entrants.")
    info["active"] = False
    info["ended_at"] = datetime.utcnow().isoformat()
    save_all()
    log_action(int(info["guild_id"]), "giveaway_end", {"id": gid, "winners": winners, "prize": info.get("prize")})

    if winners:
        for w in winners:
            try:
                user = await bot.fetch_user(int(w))
                class ClaimView(discord.ui.View):
                    def __init__(self, uid):
                        super().__init__(timeout=60*30)
                        self.uid = uid
                    @discord.ui.button(label="Claim Prize", style=discord.ButtonStyle.success)
                    async def claim(self, button: discord.ui.Button, interaction: discord.Interaction):
                        if interaction.user.id != self.uid:
                            await interaction.response.send_message("Only the winner can claim.", ephemeral=True)
                            return
                        await interaction.response.send_message("You claimed the prize. Contact staff.", ephemeral=True)
                        self.stop()
                cview = ClaimView(int(w))
                await user.send(f"🎉 You won giveaway {gid} — Prize: {info['prize']}", view=cview)
            except Exception:
                pass

# ---------- Giveaway app_commands.Group ----------
giveaway_group = app_commands.Group(name="giveaway", description="Giveaway commands (start/end/reroll/export)")

@giveaway_group.command(name="start", description="Start a giveaway (Manage Server required)")
@app_commands.describe(duration="Duration in seconds", winners="Number of winners", prize="Prize text", pin="Pin the giveaway message?")
async def gw_start(interaction: discord.Interaction, duration: int, winners: int, prize: str, pin: Optional[bool] = False):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    gid = _gen_gid()
    info = {
        "id": gid,
        "prize": prize,
        "winners": int(winners),
        "duration": int(duration),
        "channel_id": str(interaction.channel.id),
        "message_id": None,
        "ends_at": (datetime.utcnow() + timedelta(seconds=duration)).isoform
