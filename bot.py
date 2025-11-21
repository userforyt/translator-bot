# bot.py
# Requirements: discord.py (v2.x), googletrans==4.0.0-rc1, aiofiles (for async file IO)
# Keep TOKEN in environment variables (Railway Variables).
# Persisted settings file: settings.json

import os
import json
import asyncio
import logging
from typing import Optional
import discord
from discord.ext import commands
from googletrans import Translator
import aiofiles

# ---------- CONFIG ----------
SETTINGS_FILE = "settings.json"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("translator-bot")
# ----------------------------

translator = Translator()

# Intents
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=".", intents=intents)

# load/save settings (structure: { "<guild_id>": { "<channel_id>": {"lang":"en","autotranslate": False}}})
async def load_settings():
    try:
        async with aiofiles.open(SETTINGS_FILE, mode="r") as f:
            text = await f.read()
            return json.loads(text)
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("Error loading settings.json: %s", e)
        return {}

async def save_settings(data):
    try:
        async with aiofiles.open(SETTINGS_FILE, mode="w") as f:
            await f.write(json.dumps(data, indent=2))
    except Exception as e:
        log.error("Failed to save settings.json: %s", e)

async def get_channel_cfg(guild_id: int, channel_id: int):
    if not hasattr(bot, "settings"):
        bot.settings = await load_settings()
    g = str(guild_id)
    c = str(channel_id)
    if g not in bot.settings:
        bot.settings[g] = {}
    if c not in bot.settings[g]:
        bot.settings[g][c] = {"lang": "en", "autotranslate": False}
    return bot.settings[g][c]

# ---------- Utilities ----------
def translate_text(text: str, dest: str = "en"):
    # googletrans auto-detects source language
    res = translator.translate(text, dest=dest)
    return res.text, res.src

async def ensure_settings_loaded():
    if not hasattr(bot, "settings"):
        bot.settings = await load_settings()

# ---------- Events ----------
@bot.event
async def on_ready():
    await ensure_settings_loaded()
    log.info("Logged in as %s (id:%s)", bot.user, bot.user.id)
    # sync commands to guilds (if needed) — global sync may take time
    try:
        await bot.tree.sync()
        log.info("Slash commands synced")
    except Exception as e:
        log.warning("Slash sync failed: %s", e)

@bot.event
async def on_message(message: discord.Message):
    # Always process commands
    await bot.process_commands(message)

    # Ignore bots and DMs
    if message.author.bot or not message.guild:
        return

    # Auto-translate if enabled for this channel
    cfg = await get_channel_cfg(message.guild.id, message.channel.id)
    if not cfg.get("autotranslate", False):
        return

    # Skip messages with no text
    if not message.content or message.content.strip() == "":
        return

    # Do not auto-translate if the bot itself; already checked
    target = cfg.get("lang", "en")
    try:
        translated, src = translate_text(message.content, dest=target)
        reply_text = f"**Auto-Translation ({src} → {target})**: {translated}"
        # Note: cannot make this ephemeral — interactions only support ephemeral replies.
        # Auto-translate will therefore post publicly as a reply.
        await message.reply(reply_text, mention_author=False)
    except Exception as e:
        log.exception("Auto-translate failed: %s", e)

# ---------- Message (dot) commands ----------
@bot.command(name="t", aliases=["translate"])
async def msg_translate(ctx: commands.Context, message_id: int, lang: Optional[str] = "en"):
    """
    Fallback message command.
    This will DM the invoker privately with translation (because message commands can't be ephemeral).
    Usage: .t <message_id> [lang]
    """
    await ctx.trigger_typing()
    try:
        msg = await ctx.channel.fetch_message(message_id)
    except Exception:
        await ctx.send("Message not found in this channel. Check ID.", delete_after=8)
        return

    if not msg.content or msg.content.strip() == "":
        await ctx.send("The target message has no text.", delete_after=8)
        return

    try:
        translated, src = translate_text(msg.content, dest=lang)
    except Exception as e:
        await ctx.send("Translation service error: " + str(e), delete_after=8)
        return

    # Try to DM the invoker
    try:
        dm = await ctx.author.create_dm()
        await dm.send(f"**Translation ({src} → {lang})**\n{translated}")
        await ctx.send("I sent the translation to your DMs.", delete_after=6)
    except Exception:
        # Last fallback: reply publicly but without mention
        await ctx.reply(f"**Translation ({src} → {lang})**\n{translated}", mention_author=False)

@bot.command(name="setlang")
@commands.has_permissions(manage_guild=True)
async def setlang_cmd(ctx: commands.Context, lang: str):
    """Set default language for this channel. (manage_guild required)"""
    cfg = await get_channel_cfg(ctx.guild.id, ctx.channel.id)
    cfg["lang"] = lang
    await save_settings(bot.settings)
    await ctx.send(f"Default language for this channel set to `{lang}`.", delete_after=8)

@bot.command(name="autotranslate")
@commands.has_permissions(manage_guild=True)
async def autotranslate_cmd(ctx: commands.Context, mode: str):
    """Enable/disable auto translate for the channel. Usage: .autotranslate on|off"""
    mode = mode.lower()
    cfg = await get_channel_cfg(ctx.guild.id, ctx.channel.id)
    if mode in ("on", "true", "1"):
        cfg["autotranslate"] = True
    elif mode in ("off", "false", "0"):
        cfg["autotranslate"] = False
    else:
        await ctx.send("Use `on` or `off`.", delete_after=8)
        return
    await save_settings(bot.settings)
    await ctx.send(f"Auto-translate set to `{cfg['autotranslate']}` for this channel.", delete_after=8)

# ---------- Slash commands (interactions) ----------
# /t -> ephemeral (visible only to invoker)
@bot.tree.command(name="t", description="Translate a message by ID (ephemeral only to you).")
@discord.app_commands.describe(message_id="ID of the message to translate", channel="Channel containing the message (optional)", lang="Target language (e.g. en, hi)")
async def slash_t(interaction: discord.Interaction, message_id: str, channel: discord.TextChannel = None, lang: str = "en"):
    await interaction.response.defer(ephemeral=True)
    # validate ID
    try:
        mid = int(message_id)
    except:
        await interaction.followup.send("Message ID must be a number.", ephemeral=True)
        return

    target_channel = channel or interaction.channel
    if target_channel is None:
        await interaction.followup.send("Channel not found.", ephemeral=True)
        return

    try:
        msg = await target_channel.fetch_message(mid)
    except Exception:
        await interaction.followup.send("Message not found in that channel.", ephemeral=True)
        return

    if not msg.content or msg.content.strip() == "":
        await interaction.followup.send("Target message has no text.", ephemeral=True)
        return

    try:
        translated, src = translate_text(msg.content, dest=lang)
        await interaction.followup.send(f"**Translation ({src} → {lang})**\n{translated}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send("Translation failed: " + str(e), ephemeral=True)

# /setup - configure current channel default language and autotranslate
@bot.tree.command(name="setup", description="Configure default language and auto-translate for this channel (ephemeral).")
@discord.app_commands.describe(lang="Default language code (e.g. en, hi)", autotranslate="Enable automatic translation in this channel")
async def slash_setup(interaction: discord.Interaction, lang: Optional[str] = None, autotranslate: Optional[bool] = None):
    # needs manage_guild or manage_channels? interactions don't enforce ManageGuild by default; check perms manually
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("You need Manage Server permission to run setup.", ephemeral=True)
        return

    cfg = await get_channel_cfg(interaction.guild.id, interaction.channel.id)
    changed = []
    if lang:
        cfg["lang"] = lang
        changed.append(f"default language → `{lang}`")
    if autotranslate is not None:
        cfg["autotranslate"] = bool(autotranslate)
        changed.append(f"autotranslate → `{cfg['autotranslate']}`")

    await save_settings(bot.settings)
    if changed:
        await interaction.response.send_message("Settings updated: " + ", ".join(changed), ephemeral=True)
    else:
        # show current settings
        await interaction.response.send_message(f"Channel settings: lang=`{cfg.get('lang','en')}`, autotranslate=`{cfg.get('autotranslate',False)}`", ephemeral=True)

# /help - ephemeral help message
@bot.tree.command(name="helpme", description="Show translator bot help (ephemeral).")
async def slash_help(interaction: discord.Interaction):
    help_text = (
        "**Translator Bot Help**\n\n"
        "/t message_id:[id] channel:[#channel optional] lang:[en]\n"
        "→ Translate a message by its ID. Result is private (only you see it).\n\n"
        "/setup lang:[en] autotranslate:[true/false]\n"
        "→ Set default target language & toggle automatic translation for this channel.\n\n"
        "Fallback message commands:\n"
        ".t <message_id> [lang] → Bot will DM you the translation (private)\n"
        ".setlang <lang> → Set channel default language (manage server required)\n"
        ".autotranslate on|off → Toggle auto-translate for channel (manage server required)\n\n"
        "Important: Automatic translations triggered by message content cannot be private. Use /t (ephemeral) or .t (DM) for private translations."
    )
    await interaction.response.send_message(help_text, ephemeral=True)

# ---------- start ----------
if __name__ == "__main__":
    TOKEN = os.getenv("TOKEN")
    if not TOKEN:
        log.error("TOKEN environment variable not set.")
        raise SystemExit("Missing TOKEN")
    bot.run(TOKEN)
