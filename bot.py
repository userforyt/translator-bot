# bot.py — Full working bot with slash + prefix support, giveaways, nuke, persistent per-guild prefix
# Requirements (example for requirements.txt):
# discord.py==2.3.2
# googletrans==4.0.0-rc1
# deep-translator
# aiohttp==3.8.4
# PyNaCl==1.5.0 (optional — if you add it remove the log filter)
#
# ENV:
# TOKEN -> your bot token
# GUILD_ID -> optional (for quick slash sync)
# USE_MSG_CONTENT -> optional "true" to enable message content intent in code (still requires Developer Portal to be enabled)

import warnings
warnings.filterwarnings("ignore", message="PyNaCl is not installed")

import os
import json
import csv
import random
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Callable, Coroutine

import discord
from discord.ext import commands
from discord import app_commands, File

# ---------- tiny log filter for PyNaCl warning ----------
class _DropPyNaClFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            if "PyNaCl is not installed" in msg:
                return False
        except Exception:
            pass
        return True

logging.getLogger("discord").addFilter(_DropPyNaClFilter())

# ---------- CONFIG ----------
LOG_LEVEL = logging.INFO
# Toggle via env "USE_MSG_CONTENT=true" if you enabled Message Content Intent in Dev Portal.
USE_MESSAGE_CONTENT_INTENT = os.getenv("USE_MSG_CONTENT", "false").lower() == "true"
SETTINGS_FILE = "settings.json"
GIVE_FILE = "giveaways.json"
MODLOG_FILE = "modlog.json"
BACKUP_DIR = "backups"
COUNTDOWN_INTERVAL = 10  # seconds between countdown updates
DEFAULT_PREFIX = "."
# --------------------------------

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("bot-full")

os.makedirs(BACKUP_DIR, exist_ok=True)

TOKEN = os.getenv("TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
GLOBAL_MODLOG_CHANNEL = os.getenv("MODLOG_CHANNEL_ID")

if not TOKEN:
    raise SystemExit("TOKEN environment variable not set")

# ---------- Persistence ----------
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

def get_guild_prefix(guild_id: Optional[int]) -> str:
    if not guild_id:
        return DEFAULT_PREFIX
    gs = state.setdefault("settings", {})
    g = gs.setdefault(str(guild_id), {})
    p = g.get("prefix")
    return p if p else DEFAULT_PREFIX

async def prefix_callable(bot: commands.Bot, message: discord.Message) -> List[str]:
    # returns list of prefixes for commands.Bot to use
    if message.guild:
        p = get_guild_prefix(message.guild.id)
    else:
        p = DEFAULT_PREFIX
    return [p, f"<@!{bot.user.id}> ", f"<@{bot.user.id}> "]

# ---------- Intents & Bot ----------
intents = discord.Intents.default()
intents.message_content = USE_MESSAGE_CONTENT_INTENT
bot = commands.Bot(command_prefix=prefix_callable, intents=intents, help_command=None)
# remove builtin help to avoid conflicts
try:
    bot.remove_command("help")
except Exception:
    pass
tree = bot.tree

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

# ---------- Mod log ----------
def log_action(guild_id: int, action: str, data: Dict[str, Any]):
    gid = str(guild_id)
    entry = {"time": datetime.utcnow().isoformat(), "action": action, "data": data}
    state["modlog"].setdefault(gid, []).append(entry)
    save_json(MODLOG_FILE, state["modlog"])
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

# ---------- On ready ----------
@bot.event
async def on_ready():
    log.info("Bot online: %s (id:%s)", bot.user, getattr(bot.user, "id", None))
    # sync slash commands
    try:
        if GUILD_ID:
            guild_obj = discord.Object(id=int(GUILD_ID))
            await tree.sync(guild=guild_obj)
            log.info("Slash commands synced to guild %s", GUILD_ID)
        else:
            await tree.sync()
            log.info("Global slash sync attempted")
    except Exception as e:
        log.warning("Slash sync failed: %s", e)

    # resume giveaways
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

# ---------- Ensure channel/guild settings ----------
def ensure_guild_settings(guild_id: int) -> Dict[str, Any]:
    gs = state.setdefault("settings", {})
    return gs.setdefault(str(guild_id), {"prefix": DEFAULT_PREFIX, "_modlog_channel": None})

# ---------- Setup command (slash) ----------
@tree.command(name="setup", description="Open setup UI for this channel (managers only)")
@app_commands.describe(modlog_channel="Channel for mod logs (optional)", autotranslate="Enable auto-translate in this channel", default_lang="Default language code (e.g. en)")
async def slash_setup(interaction: discord.Interaction, modlog_channel: Optional[discord.TextChannel] = None, autotranslate: Optional[bool] = None, default_lang: Optional[str] = None):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
        return

    gs = ensure_guild_settings(interaction.guild.id)
    ch_cfg = gs.setdefault(str(interaction.channel.id), {"lang": "en", "autotranslate": False})

    changed = []
    if modlog_channel:
        gs["_modlog_channel"] = str(modlog_channel.id)
        changed.append(f"modlog -> {modlog_channel.mention}")
    if autotranslate is not None:
        ch_cfg["autotranslate"] = bool(autotranslate)
        changed.append(f"autotranslate -> {ch_cfg['autotranslate']}")
    if default_lang:
        ch_cfg["lang"] = default_lang
        changed.append(f"default_lang -> {default_lang}")

    save_all()

    class SetupView(discord.ui.View):
        def __init__(self, timeout: int = 60):
            super().__init__(timeout=timeout)

        @discord.ui.button(label="Set English", style=discord.ButtonStyle.secondary)
        async def en_btn(self, button: discord.ui.Button, inter: discord.Interaction):
            g = state.setdefault("settings", {}).setdefault(str(inter.guild.id), {})
            g.setdefault(str(inter.channel.id), {})["lang"] = "en"
            save_all()
            await inter.response.send_message("Channel default language set to English.", ephemeral=True)

        @discord.ui.button(label="Set Hindi", style=discord.ButtonStyle.secondary)
        async def hi_btn(self, button: discord.ui.Button, inter: discord.Interaction):
            g = state.setdefault("settings", {}).setdefault(str(inter.guild.id), {})
            g.setdefault(str(inter.channel.id), {})["lang"] = "hi"
            save_all()
            await inter.response.send_message("Channel default language set to Hindi.", ephemeral=True)

        @discord.ui.button(label="Toggle Auto-Translate", style=discord.ButtonStyle.primary)
        async def toggle_btn(self, button: discord.ui.Button, inter: discord.Interaction):
            g = state.setdefault("settings", {}).setdefault(str(inter.guild.id), {})
            cfg = g.setdefault(str(inter.channel.id), {"lang": "en", "autotranslate": False})
            cfg["autotranslate"] = not cfg.get("autotranslate", False)
            save_all()
            await inter.response.send_message(f"Auto-translate set to {cfg['autotranslate']}.", ephemeral=True)

    view = SetupView()
    text = "Settings updated: " + ", ".join(changed) if changed else "Open setup menu to configure this channel."
    await interaction.response.send_message(text, ephemeral=True, view=view)
    await post_guild_modlog(interaction.guild, f"Settings updated by {interaction.user}: {changed}")
    log_action(interaction.guild.id, "setup", {"by": str(interaction.user.id), "changes": changed})

# ---------- Prefix change (slash) ----------
@tree.command(name="setprefix", description="Change server command prefix (Manage Server required)")
@app_commands.describe(prefix="New prefix (example: . or ! or ?)")
async def slash_setprefix(interaction: discord.Interaction, prefix: str):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
        return
    if not prefix:
        await interaction.response.send_message("Prefix cannot be empty.", ephemeral=True)
        return
    gs = ensure_guild_settings(interaction.guild.id)
    gs["prefix"] = prefix
    save_all()
    await interaction.response.send_message(f"Prefix updated to `{prefix}` (persistent).", ephemeral=True)
    await post_guild_modlog(interaction.guild, f"Prefix changed to {prefix} by {interaction.user}")
    log_action(interaction.guild.id, "prefix_change", {"by": str(interaction.user.id), "prefix": prefix})

# ---------- Translate slash ----------
@tree.command(name="translate", description="Translate message by ID (public reply option)")
@app_commands.describe(message_id="Message ID to translate", channel="Channel containing message (optional)", lang="Language code (e.g. en)")
async def slash_translate(interaction: discord.Interaction, message_id: str, channel: Optional[discord.TextChannel] = None, lang: str = "en"):
    await interaction.response.defer(ephemeral=False)  # public by default
    target = channel or interaction.channel
    try:
        mid = int(message_id)
    except Exception:
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
        await interaction.followup.send(f"**Translation ({lang})**\n{translated}", ephemeral=False)
    except RuntimeError as e:
        await interaction.followup.send(str(e), ephemeral=True)

# ---------- Help (slash public) ----------
@tree.command(name="help", description="Show help (public)")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(title="Bot Help", description="Commands (slash & prefix). Use `/setprefix` to change prefix.", color=0x00ffcc)
    embed.add_field(name="Translate", value="`/translate <message_id> <lang>` — translates a message and posts publicly by default.", inline=False)
    embed.add_field(name="Giveaway", value="`/giveaway start duration winners prize` and subcommands `/giveaway end`, `/giveaway reroll`, `/giveaway export`", inline=False)
    embed.add_field(name="Nuke", value="`/nuke` — preview, confirm, backup & duplicate the channel.", inline=False)
    embed.add_field(name="Prefix", value="Change prefix: `/setprefix <prefix>` (Manage Server). Default prefix is `.`", inline=False)
    embed.set_footer(text="You can also use prefix commands like `.t <msg_id> en` if message content intent is enabled.")
    await interaction.response.send_message(embed=embed, ephemeral=False)

# ---------- Prefix help (works only if message_content intent is enabled) ----------
@bot.command(name="help")
async def prefix_help(ctx: commands.Context):
    if not USE_MESSAGE_CONTENT_INTENT:
        await ctx.reply("Prefix help disabled because message content intent is not enabled. Use /help instead.", mention_author=False)
        return
    p = get_guild_prefix(ctx.guild.id) if ctx.guild else DEFAULT_PREFIX
    text = (
        f"Prefix: `{p}`\n\n"
        "Translate: `.t <message_id> <lang>`\n"
        "Start giveaway (prefix): `.giveaway_start <duration_seconds> <winners> <prize>` (Manage Server)\n"
        "Nuke (prefix): `.nuke` (Manage Channels)\n"
        "Change prefix (slash): `/setprefix <prefix>`\n"
        "Use `/help` for slash help."
    )
    await ctx.reply(text, mention_author=False)

# ---------- Process messages to support prefix commands when message_content intent is enabled ----------
@bot.event
async def on_message(message: discord.Message):
    # ignore bots
    if message.author.bot:
        return
    # auto-translate if enabled
    try:
        if message.guild:
            gs = state.get("settings", {}).get(str(message.guild.id), {})
            ch_cfg = gs.get(str(message.channel.id), {})
            if ch_cfg and ch_cfg.get("autotranslate") and ch_cfg.get("lang"):
                # only if message_content intent is allowed
                if USE_MESSAGE_CONTENT_INTENT:
                    try:
                        translated = translate_text(message.content, ch_cfg["lang"])
                        await message.channel.send(f"🔁 Translation ({ch_cfg['lang']}): {translated}", reference=message)
                    except Exception:
                        pass
    except Exception:
        log.exception("Auto-translate error")

    # let commands extension handle prefix commands if intent enabled
    if USE_MESSAGE_CONTENT_INTENT:
        await bot.process_commands(message)
    else:
        # if prefix used but intent disabled, reply with instruction
        prefixes = await prefix_callable(bot, message)
        for p in prefixes:
            if message.content.startswith(p):
                # someone tried to use prefix — warn
                try:
                    await message.channel.send("Prefix commands are disabled because Message Content Intent is not enabled on this bot. Use slash commands or enable the intent in Developer Portal and set USE_MSG_CONTENT=true.", delete_after=12)
                except Exception:
                    pass
                break

# ---------- Prefix translate command ----------
@bot.command(name="t")
async def prefix_translate(ctx: commands.Context, message_id: int, lang: str = "en"):
    if not USE_MESSAGE_CONTENT_INTENT:
        await ctx.reply("Prefix translate disabled (message content intent not enabled). Use /translate.", mention_author=False)
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
        await ctx.author.send(f"**Translation ({lang})**\n{translated}")
        await ctx.reply("Sent translation to your DMs.", mention_author=False)
    except Exception as e:
        await ctx.reply(f"Translation error: {e}", mention_author=False)

# ---------- GIVEAWAY: core logic ----------
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
        def __init__(self, gid: str):
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
    try:
        await channel.send("Click to enter via button:", view=view)
    except Exception:
        pass
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
                embed = msg.embeds[0] if msg.embeds else discord.Embed(title="🎉 Giveaway", description=i
