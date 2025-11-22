# bot.py — Full translator + giveaways + nuke + help
# Notes:
# - Keep TOKEN in env vars (Railway Variables)
# - Optionally set GUILD_ID for instant slash sync
# - To enable prefix (dot) commands and auto-translate you MUST enable Message Content Intent in Discord Developer Portal
# - Recommended requirements: discord.py==2.3.2, googletrans==4.0.0-rc1, deep-translator

import os
import json
import random
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict

import discord
from discord.ext import commands

# ---------- Configuration ----------
LOG_LEVEL = logging.INFO
SETTINGS_FILE = "settings.json"   # channel settings (lang, autotranslate)
GIVE_FILE = "giveaways.json"      # giveaways persistence
# By default we DO NOT request message_content. Set True if you enabled the privileged intent in Dev Portal.
USE_MESSAGE_CONTENT_INTENT = False
# -----------------------------------

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("translator-bot")

intents = discord.Intents.default()
intents.message_content = USE_MESSAGE_CONTENT_INTENT

bot = commands.Bot(command_prefix=".", intents=intents)

# ---------- Persistence helpers ----------
def load_json_safe(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("Failed to load %s: %s", path, e)
        return {}

def save_json_safe(path: str, data: dict):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.error("Failed to save %s: %s", path, e)

# Settings structure:
# settings = { "<guild_id>": { "<channel_id>": {"lang":"en","autotranslate": False} } }
async def ensure_settings_loaded():
    if not hasattr(bot, "settings"):
        bot.settings = load_json_safe(SETTINGS_FILE)

async def get_channel_settings(guild_id: int, channel_id: int) -> dict:
    await ensure_settings_loaded()
    g = str(guild_id)
    c = str(channel_id)
    if g not in bot.settings:
        bot.settings[g] = {}
    if c not in bot.settings[g]:
        bot.settings[g][c] = {"lang": "en", "autotranslate": False}
    return bot.settings[g][c]

# Giveaways stored as { gid: {id, prize, winners, duration, channel_id, message_id, ends_at_iso, active, starter} }
def load_giveaways() -> dict:
    return load_json_safe(GIVE_FILE)

def save_giveaways(data: dict):
    save_json_safe(GIVE_FILE, data)

# ---------- Utilities ----------
def human_td(seconds: int) -> str:
    td = timedelta(seconds=seconds)
    # Simplify to a human string
    days = td.days
    hrs, rem = divmod(td.seconds, 3600)
    mins, secs = divmod(rem, 60)
    parts = []
    if days: parts.append(f"{days}d")
    if hrs: parts.append(f"{hrs}h")
    if mins: parts.append(f"{mins}m")
    if secs: parts.append(f"{secs}s")
    return " ".join(parts) if parts else "0s"

def translate_with_fallback(text: str, dest: str) -> Tuple[str, str]:
    """
    Try googletrans then deep-translator. Returns (translated_text, src_lang).
    Raises RuntimeError if none available.
    """
    # googletrans attempt
    try:
        from googletrans import Translator as GT
        tr = GT()
        res = tr.translate(text, dest=dest)
        return res.text, getattr(res, "src", "auto")
    except Exception as e:
        log.debug("googletrans failed: %s", e)
    # deep-translator fallback
    try:
        from deep_translator import GoogleTranslator as DT
        out = DT(source="auto", target=dest).translate(text)
        return out, "auto"
    except Exception as e:
        log.debug("deep-translator failed: %s", e)
    raise RuntimeError("Translation libraries not installed (googletrans or deep-translator).")

def bot_has_message_intent() -> bool:
    return bool(bot.intents.message_content)

# ---------- On ready ----------
@bot.event
async def on_ready():
    await ensure_settings_loaded()
    log.info("Logged in as %s (id:%s)", bot.user, getattr(bot.user, "id", None))

    guild_id = os.getenv("GUILD_ID")
    if guild_id:
        try:
            g = discord.Object(id=int(guild_id))
            await bot.tree.sync(guild=g)
            log.info("Slash commands synced to guild %s", guild_id)
        except Exception as e:
            log.warning("Guild sync failed: %s", e)
    else:
        try:
            await bot.tree.sync()
            log.info("Global slash sync attempted")
        except Exception as e:
            log.warning("Global slash sync failed: %s", e)

    # Resume giveaways if any
    data = load_giveaways()
    for gid, info in data.items():
        if info.get("active"):
            try:
                ends_at = datetime.fromisoformat(info["ends_at"])
                secs = int((ends_at - datetime.utcnow()).total_seconds())
                if secs > 0:
                    task = asyncio.create_task(_giveaway_wait_and_end(gid, secs))
                    bot._give_tasks = getattr(bot, "_give_tasks", {})
                    bot._give_tasks[gid] = task
                else:
                    # end immediately
                    asyncio.create_task(_giveaway_end_now(gid))
            except Exception:
                log.exception("Resuming giveaway failed for %s", gid)

# ---------- HELP: slash + prefix ----------
# Remove default help to avoid duplicate if user wants a custom prefix help
try:
    bot.remove_command("help")
except Exception:
    pass

@bot.tree.command(name="help", description="Show help for translator bot (ephemeral)")
async def slash_help(interaction: discord.Interaction):
    txt = (
        "**Translator Bot Help**\n\n"
        "Slash commands (works without message content intent):\n"
        "• `/t message_id:<id> lang:<en> channel:<#optional>` → Translate privately (ephemeral)\n"
        "• `/setup lang:<en> autotranslate:<true/false>` → Configure channel (Manage Server required)\n"
        "• `/giveaway start duration:<seconds> winners:<n> prize:<text>` → Start giveaway (Manage Server required)\n"
        "• `/giveaway end id:<id>` → End giveaway\n"
        "• `/nuke` → Duplicate & delete this channel (confirmation, Manage Channels required)\n\n"
        "Prefix commands (require Message Content Intent):\n"
        "• `.t <message_id> [lang]` → DM you the translation\n"
        "• `.help` → Prefix help\n"
        "• `.giveaway start <seconds> <winners> <prize>`\n"
        "• `.nuke` → prefix nuke\n\n"
        "Note: Automatic message-based translation cannot be ephemeral. Use `/t` for private translation."
    )
    await interaction.response.send_message(txt, ephemeral=True)

@bot.command(name="help")
async def prefix_help(ctx: commands.Context):
    if not bot_has_message_intent():
        await ctx.reply("Prefix commands require Message Content Intent. Use `/help` for slash help.", mention_author=False)
        return
    txt = (
        "**Translator Bot — Prefix Help**\n"
        ".t <message_id> [lang]\n"
        ".setup <lang> <autotranslate on|off>\n"
        ".giveaway start <seconds> <winners> <prize>\n"
        ".nuke\n"
    )
    await ctx.reply(txt, mention_author=False)

# ---------- Translate commands ----------
@bot.tree.command(name="t", description="Translate a message by ID (ephemeral)")
@discord.app_commands.describe(message_id="ID of message", lang="language code (en,hi)", channel="channel (optional)")
async def slash_t(interaction: discord.Interaction, message_id: str, lang: str = "en", channel: Optional[discord.TextChannel] = None):
    await interaction.response.defer(ephemeral=True)
    try:
        mid = int(message_id)
    except:
        await interaction.followup.send("Message ID must be numeric.", ephemeral=True)
        return
    target = channel or interaction.channel
    try:
        msg = await target.fetch_message(mid)
    except Exception:
        await interaction.followup.send("Message not found.", ephemeral=True)
        return
    if not getattr(msg, "content", None):
        await interaction.followup.send("Message has no text.", ephemeral=True)
        return
    try:
        translated, src = translate_with_fallback(msg.content, dest=lang)
        await interaction.followup.send(f"**Translation ({src} → {lang})**\n{translated}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send("Translation failed: " + str(e), ephemeral=True)

@bot.command(name="t")
async def prefix_t(ctx: commands.Context, message_id: int, lang: str = "en"):
    if not bot_has_message_intent():
        await ctx.reply("Prefix commands require Message Content Intent. Use `/t` (slash) instead.", mention_author=False)
        return
    try:
        msg = await ctx.channel.fetch_message(message_id)
    except Exception:
        await ctx.reply("Message not found in this channel.", mention_author=False)
        return
    if not getattr(msg, "content", None):
        await ctx.reply("Target message has no text.", mention_author=False)
        return
    try:
        translated, src = translate_with_fallback(msg.content, dest=lang)
    except Exception as e:
        await ctx.reply("Translation failed: " + str(e), mention_author=False)
        return
    # DM the user privately if possible
    try:
        dm = await ctx.author.create_dm()
        await dm.send(f"**Translation ({src} → {lang})**\n{translated}")
        await ctx.reply("Sent translation to your DMs.", mention_author=False)
    except Exception:
        await ctx.reply(f"**Translation ({src} → {lang})**\n{translated}", mention_author=False)

# ---------- /setup and .setup ----------
@bot.tree.command(name="setup", description="Configure channel default language & autotranslate (ephemeral)")
@discord.app_commands.describe(lang="default language code", autotranslate="enable automatic translations in this channel")
async def slash_setup(interaction: discord.Interaction, lang: Optional[str] = None, autotranslate: Optional[bool] = None):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("You need Manage Server permission to run setup.", ephemeral=True)
        return
    await ensure_settings_loaded()
    g = str(interaction.guild.id)
    c = str(interaction.channel.id)
    if g not in bot.settings:
        bot.settings[g] = {}
    if c not in bot.settings[g]:
        bot.settings[g][c] = {"lang": "en", "autotranslate": False}
    changed = []
    if lang:
        bot.settings[g][c]["lang"] = lang
        changed.append(f"default language -> `{lang}`")
    if autotranslate is not None:
        bot.settings[g][c]["autotranslate"] = bool(autotranslate)
        changed.append(f"autotranslate -> `{bot.settings[g][c]['autotranslate']}`")
    save_json_safe(SETTINGS_FILE, bot.settings)
    if changed:
        await interaction.response.send_message("Settings updated: " + ", ".join(changed), ephemeral=True)
    else:
        cfg = bot.settings[g][c]
        await interaction.response.send_message(f"Channel settings: lang=`{cfg['lang']}`, autotranslate=`{cfg['autotranslate']}`", ephemeral=True)

@bot.command(name="setup")
async def prefix_setup(ctx: commands.Context, lang: Optional[str] = None, autotranslate: Optional[str] = None):
    if not bot_has_message_intent():
        await ctx.reply("Prefix setup requires Message Content Intent. Use `/setup` instead.", mention_author=False)
        return
    if not ctx.author.guild_permissions.manage_guild:
        await ctx.reply("You need Manage Server permission to run setup.", mention_author=False)
        return
    await ensure_settings_loaded()
    g = str(ctx.guild.id); c = str(ctx.channel.id)
    if g not in bot.settings: bot.settings[g] = {}
    if c not in bot.settings[g]: bot.settings[g][c] = {"lang": "en", "autotranslate": False}
    changed = []
    if lang:
        bot.settings[g][c]["lang"] = lang
        changed.append(f"default language -> `{lang}`")
    if autotranslate:
        val = autotranslate.lower() in ("on","true","1","yes")
        bot.settings[g][c]["autotranslate"] = val
        changed.append(f"autotranslate -> `{val}`")
    save_json_safe(SETTINGS_FILE, bot.settings)
    await ctx.reply("Settings updated: " + ", ".join(changed), mention_author=False)

# ---------- Auto-translate on message (ONLY works if message_content intent is enabled) ----------
@bot.event
async def on_message(message: discord.Message):
    # always process commands
    await bot.process_commands(message)
    # ignore bots and DMs
    if message.author.bot or message.guild is None:
        return
    if not bot_has_message_intent():
        return  # cannot read message content without privileged intent
    await ensure_settings_loaded()
    g = str(message.guild.id); c = str(message.channel.id)
    cfg = bot.settings.get(g, {}).get(c)
    if not cfg or not cfg.get("autotranslate"):
        return
    # translate text
    content = message.content or ""
    if not content.strip():
        return
    target = cfg.get("lang", "en")
    try:
        translated, src = translate_with_fallback(content, dest=target)
        await message.reply(f"**Auto-Translation ({src} → {target})**\n{translated}", mention_author=False)
    except Exception as e:
        log.exception("Auto-translate failed: %s", e)

# ---------- Giveaways ----------
def _gen_give_id() -> str:
    return str(random.randint(100000, 999999))

async def _announce_giveaway(channel: discord.TextChannel, info: dict) -> int:
    embed = discord.Embed(title="🎉 Giveaway!", description=info["prize"], color=0x00FF00)
    embed.add_field(name="Giveaway ID", value=info["id"], inline=True)
    embed.add_field(name="Ends In", value=human_td(info["duration"]), inline=True)
    embed.set_footer(text=f"Winners: {info['winners']}")
    msg = await channel.send(embed=embed)
    try:
        await msg.add_reaction("🎉")
    except Exception:
        pass
    return msg.id

async def _giveaway_wait_and_end(gid: str, seconds: int):
    try:
        await asyncio.sleep(seconds)
        await _giveaway_end_now(gid)
    except asyncio.CancelledError:
        log.info("Giveaway %s cancelled", gid)

async def _giveaway_end_now(gid: str):
    data = load_giveaways()
    info = data.get(gid)
    if not info or not info.get("active"):
        return
    channel = bot.get_channel(int(info["channel_id"]))
    if not channel:
        info["active"] = False
        save_giveaways(data)
        return
    try:
        msg = await channel.fetch_message(int(info["message_id"]))
    except Exception:
        info["active"] = False
        save_giveaways(data)
        return
    # collect entrants via reaction
    users = set()
    for react in msg.reactions:
        # match 🎉
        emoji_name = getattr(react.emoji, "name", react.emoji)
        if emoji_name == "🎉":
            async for u in react.users():
                if u.bot: continue
                users.add(u.id)
    winners = []
    if users:
        k = min(info["winners"], len(users))
        winners = random.sample(list(users), k)
    if winners:
        mentions = " ".join(f"<@{uid}>" for uid in winners)
        await channel.send(f"🎉 Giveaway ended! Winners: {mentions}\nPrize: **{info['prize']}**")
    else:
        await channel.send(f"No valid entrants for giveaway {gid}.")
    info["active"] = False
    save_giveaways(data)
    # cancel any scheduled task
    tasks = getattr(bot, "_give_tasks", {})
    t = tasks.pop(gid, None) if tasks else None
    if t:
        t.cancel()

# Slash giveaway group
@bot.tree.command(name="giveaway", description="Giveaway commands")
async def slash_giveaway_top(interaction: discord.Interaction):
    await interaction.response.send_message("Use subcommands: start / end", ephemeral=True)

@slash_giveaway_top.subcommand(name="start", description="Start a giveaway (guild managers only)")
async def slash_giveaway_start(interaction: discord.Interaction, duration: int, winners: int, prize: str):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("You need Manage Server permission.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    gid = _gen_give_id()
    info = {
        "id": gid,
        "prize": prize,
        "winners": int(winners),
        "duration": int(duration),
        "channel_id": str(interaction.channel.id),
        "message_id": None,
        "ends_at": (datetime.utcnow() + timedelta(seconds=duration)).isoformat(),
        "active": True,
        "starter": str(interaction.user.id)
    }
    msgid = await _announce_giveaway(interaction.channel, info)
    info["message_id"] = str(msgid)
    data = load_giveaways()
    data[gid] = info
    save_giveaways(data)
    # schedule
    bot._give_tasks = getattr(bot, "_give_tasks", {})
    bot._give_tasks[gid] = asyncio.create_task(_giveaway_wait_and_end(gid, duration))
    await interaction.followup.send(f"Giveaway started (ID: {gid}). Ends in {human_td(duration)}", ephemeral=True)

@slash_giveaway_top.subcommand(name="end", description="End a giveaway early")
async def slash_giveaway_end(interaction: discord.Interaction, giveaway_id: str):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("You need Manage Server permission.", ephemeral=True)
        return
    data = load_giveaways()
    if giveaway_id not in data:
        await interaction.response.send_message("Giveaway ID not found.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await _giveaway_end_now(giveaway_id)
    await interaction.followup.send("Giveaway ended.", ephemeral=True)

# Prefix giveaway (requires message intent)
@bot.command(name="giveaway")
async def prefix_giveaway(ctx: commands.Context, sub: str, *args):
    if not bot_has_message_intent():
        await ctx.reply("Prefix giveaway requires Message Content Intent. Use slash /giveaway instead.", mention_author=False)
        return
    if sub.lower() == "start":
        try:
            duration = int(args[0]); winners = int(args[1]); prize = " ".join(args[2:])
        except Exception:
            await ctx.reply("Usage: .giveaway start <seconds> <winners> <prize>", mention_author=False)
            return
        if not ctx.author.guild_permissions.manage_guild:
            await ctx.reply("You need Manage Server permission.", mention_author=False)
            return
        gid = _gen_give_id()
        info = {
            "id": gid,
            "prize": prize,
            "winners": int(winners),
            "duration": int(duration),
            "channel_id": str(ctx.channel.id),
            "message_id": None,
            "ends_at": (datetime.utcnow() + timedelta(seconds=duration)).isoformat(),
            "active": True,
            "starter": str(ctx.author.id)
        }
        msgid = await _announce_giveaway(ctx.channel, info)
        info["message_id"] = str(msgid)
        data = load_giveaways()
        data[gid] = info
        save_giveaways(data)
        bot._give_tasks = getattr(bot, "_give_tasks", {})
        bot._give_tasks[gid] = asyncio.create_task(_giveaway_wait_and_end(gid, duration))
        await ctx.reply(f"Giveaway started (ID: {gid}). Ends in {human_td(duration)}", mention_author=False)

# ---------- Nuke command (confirmation via buttons) ----------
class NukeConfirmView(discord.ui.View):
    def __init__(self, author_id: int, timeout: int = 30):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.value: Optional[bool] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the command invoker can confirm/cancel.", ephemeral=True)
            retu
