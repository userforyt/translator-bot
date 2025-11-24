# bot.py — Slash-only, crash-hardened translator + giveaways + nuke + setup UI
# This version suppresses the "PyNaCl is not installed, voice will NOT be supported" log line.

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

# ---------- Log filter to remove the exact PyNaCl warning from discord logger ----------
class _DropPyNaClFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            if "PyNaCl is not installed" in msg:
                return False
        except Exception:
            pass
        return True

# Attach the filter specifically to the 'discord' logger so only that message is dropped
dlog = logging.getLogger("discord")
dlog.addFilter(_DropPyNaClFilter())

# ---------- CONFIG ----------
LOG_LEVEL = logging.INFO
USE_MESSAGE_CONTENT_INTENT = False
SETTINGS_FILE = "settings.json"
GIVE_FILE = "giveaways.json"
MODLOG_FILE = "modlog.json"
BACKUP_DIR = "backups"
COUNTDOWN_INTERVAL = 10
# --------------------------------

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("bot-slash")

os.makedirs(BACKUP_DIR, exist_ok=True)

TOKEN = os.getenv("TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
GLOBAL_MODLOG_CHANNEL = os.getenv("MODLOG_CHANNEL_ID")

if not TOKEN:
    raise SystemExit("TOKEN environment variable not set")

# ---------- Intents & Bot ----------
intents = discord.Intents.default()
intents.message_content = USE_MESSAGE_CONTENT_INTENT
bot = commands.Bot(command_prefix=".", intents=intents)
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

# ---------- Minimal features (slash-only) ----------
@bot.event
async def on_ready():
    log.info("Bot online: %s (id:%s)", bot.user, getattr(bot.user, "id", None))
    try:
        if GUILD_ID:
            gobj = discord.Object(id=int(GUILD_ID))
            await tree.sync(guild=gobj)
            log.info("Slash commands synced to guild %s", GUILD_ID)
        else:
            await tree.sync()
            log.info("Global slash sync attempted")
    except Exception as e:
        log.warning("Slash sync failed: %s", e)

@tree.command(name="help", description="Show help (ephemeral)")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(title="Help", description="Slash commands", color=0x00ffcc)
    embed.add_field(name="/translate", value="Translate message by ID", inline=False)
    embed.add_field(name="/setup", value="Channel setup UI (managers only)", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="translate", description="Translate message by ID (ephemeral)")
@app_commands.describe(message_id="Message ID", channel="Channel (optional)", lang="Language code (e.g. en)")
async def slash_translate(interaction: discord.Interaction, message_id: str, channel: Optional[discord.TextChannel] = None, lang: str = "en"):
    await interaction.response.defer(ephemeral=True)
    target = channel or interaction.channel
    try:
        mid = int(message_id)
    except Exception:
        await interaction.followup.send("Message ID must be numeric.", ephemeral=True)
        return
    try:
        msg = await target.fetch_message(mid)
    except Exception:
        await interaction.followup.send("Message not found.", ephemeral=True)
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

# (The rest of your features - giveaways, nuke, setup UI, modlog - can be pasted here
# from the full working file you already have. I kept this sample short to focus on the
# PyNaCl warning suppression. If you want the fully merged large file with the exact
# giveaway/nuke code included, tell me and I'll paste the complete file again.)
#
# ---------- Run ----------
if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except Exception as e:
        log.exception("Bot crashed: %s", e)
        save_all()
        raise
