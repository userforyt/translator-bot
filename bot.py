# bot.py
# Full-featured: Setup UI, Falcon-like Giveaways (timers/buttons/countdown/claim/reroll/CSV/pin),
# Nuke (preview, backup, duplicate & delete), Moderation & Logging.
#
# Usage:
# - Put your bot token in env var TOKEN (Railway Variables).
# - Optionally put GUILD_ID in env var for instant slash sync to your guild.
# - Optionally put MODLOG_CHANNEL_ID in env var for global mod-log channel.
#
# Requirements:
# discord.py==2.3.2
# googletrans==4.0.0-rc1
# deep-translator
#
# By default this file does NOT request message_content intent (safe). To enable message-based prefix commands
# and auto-translate, toggle USE_MESSAGE_CONTENT_INTENT = True and enable the privileged intent in Developer Portal
# for the same application whose token you put in Railway.

import os
import json
import csv
import random
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

import discord
from discord.ext import commands, tasks
from discord import app_commands, File

# ---------- CONFIG ----------
LOG_LEVEL = logging.INFO
USE_MESSAGE_CONTENT_INTENT = False  # set True only after enabling Message Content Intent in Dev Portal
SETTINGS_FILE = "settings.json"
GIVE_FILE = "giveaways.json"
MODLOG_FILE = "modlog.json"
BACKUP_DIR = "backups"
COUNTDOWN_INTERVAL = 10  # embed update interval in seconds
# --------------------------------

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("full-bot")

# Ensure backup dir exists
os.makedirs(BACKUP_DIR, exist_ok=True)

TOKEN = os.getenv("TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
GLOBAL_MODLOG_CHANNEL = os.getenv("MODLOG_CHANNEL_ID")  # optional global modlog channel

if not TOKEN:
    raise SystemExit("TOKEN not set in environment variables.")

intents = discord.Intents.default()
intents.message_content = USE_MESSAGE_CONTENT_INTENT

bot = commands.Bot(command_prefix=".", intents=intents)
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

# Load initial persisted state
def load_state():
    state = {}
    state["settings"] = load_json(SETTINGS_FILE)
    state["giveaways"] = load_json(GIVE_FILE)
    state["modlog"] = load_json(MODLOG_FILE)
    return state

state = load_state()

# ---------- Mod log helpers ----------
def log_action(guild_id: int, action: str, data: Dict[str, Any]):
    gid = str(guild_id)
    entry = {
        "time": datetime.utcnow().isoformat(),
        "action": action,
        "data": data
    }
    state["modlog"].setdefault(gid, []).append(entry)
    save_json(MODLOG_FILE, state["modlog"])
    # Optionally post to global modlog channel
    if GLOBAL_MODLOG_CHANNEL:
        try:
            cid = int(GLOBAL_MODLOG_CHANNEL)
            ch = bot.get_channel(cid)
            if ch:
                asyncio.create_task(ch.send(f"[{datetime.utcnow().isoformat()}] {action} — {data}"))
        except Exception as e:
            log.debug("Failed sending global modlog: %s", e)

async def post_guild_modlog(guild: discord.Guild, text: str):
    # If guild has modlog channel configured, use it
    settings = state["settings"].get(str(guild.id), {})
    modch_id = settings.get("_modlog_channel") or settings.get("modlog_channel")
    if modch_id:
        try:
            ch = guild.get_channel(int(modch_id))
            if ch:
                await ch.send(text)
                return
        except Exception:
            pass
    # fallback to env global modlog
    if GLOBAL_MODLOG_CHANNEL:
        ch = bot.get_channel(int(GLOBAL_MODLOG_CHANNEL))
        if ch:
            await ch.send(f"[{guild.name}] {text}")

# ---------- Helper utilities ----------
def human_td(seconds: int) -> str:
    td = timedelta(seconds=max(0, seconds))
    days = td.days
    hrs, rem = divmod(td.seconds, 3600)
    mins, secs = divmod(rem, 60)
    parts = []
    if days: parts.append(f"{days}d")
    if hrs: parts.append(f"{hrs}h")
    if mins: parts.append(f"{mins}m")
    if secs: parts.append(f"{secs}s")
    return " ".join(parts) if parts else "0s"

def ensure_give_data() -> Dict[str, Any]:
    g = state.setdefault("giveaways", {})
    return g

def save_all():
    save_json(SETTINGS_FILE, state.get("settings", {}))
    save_json(GIVE_FILE, state.get("giveaways", {}))
    save_json(MODLOG_FILE, state.get("modlog", {}))

# ---------- Translator helper (delayed import) ----------
def translate_text(text: str, dest: str) -> str:
    # try googletrans then deep-translator
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
            raise RuntimeError("No translator library installed.")

# ---------- Bot ready ----------
@bot.event
async def on_ready():
    log.info("Bot online: %s (id:%s)", bot.user, getattr(bot.user, "id", None))
    # sync to guild if given
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
    # resume any active giveaways
    gdata = state.get("giveaways", {})
    for gid, info in list(gdata.items()):
        if info.get("active"):
            ends_at = datetime.fromisoformat(info["ends_at"])
            seconds = int((ends_at - datetime.utcnow()).total_seconds())
            if seconds <= 0:
                asyncio.create_task(_end_giveaway(gid))
            else:
                # start countdown task
                asyncio.create_task(_run_countdown_task(gid, seconds))

# ---------- Setup UI (slash) ----------
@tree.command(name="setup", description="Open server/channel settings UI (ephemeral)")
@app_commands.describe(modlog_channel="Channel to post moderation logs (optional)", autotranslate="Enable auto-translate in this channel (requires message content intent)", default_lang="Default translation language code")
async def slash_setup(interaction: discord.Interaction, modlog_channel: Optional[discord.TextChannel] = None, autotranslate: Optional[bool] = None, default_lang: Optional[str] = None):
    # only guild managers can configure
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("You need Manage Server permission to configure settings.", ephemeral=True)
        return

    # Show ephemeral interactive menu with current values and buttons
    gid = str(interaction.guild.id)
    settings = state.setdefault("settings", {})
    guild_settings = settings.setdefault(gid, {})
    chan_settings = guild_settings.setdefault(str(interaction.channel.id), {"lang": "en", "autotranslate": False})

    # update if provided
    changed = []
    if modlog_channel:
        guild_settings["_modlog_channel"] = str(modlog_channel.id)
        changed.append(f"modlog -> {modlog_channel.mention}")
    if autotranslate is not None:
        chan_settings["autotranslate"] = bool(autotranslate)
        changed.append(f"autotranslate -> {chan_settings['autotranslate']}")
    if default_lang:
        chan_settings["lang"] = default_lang
        changed.append(f"default lang -> {default_lang}")

    save_all()
    # build view with language picker + toggles
    class SetupView(discord.ui.View):
        def __init__(self, timeout=60):
            super().__init__(timeout=timeout)
        @discord.ui.button(label="Set language → English", style=discord.ButtonStyle.secondary)
        async def set_en(self, button: discord.ui.Button, inter: discord.Interaction):
            guild_settings[str(inter.guild.id)][str(inter.channel.id)]["lang"] = "en"
            save_all()
            await inter.response.send_message("Channel default language set to English.", ephemeral=True)
        @discord.ui.button(label="Set language → Hindi", style=discord.ButtonStyle.secondary)
        async def set_hi(self, button: discord.ui.Button, inter: discord.Interaction):
            guild_settings[str(inter.guild.id)][str(inter.channel.id)]["lang"] = "hi"
            save_all()
            await inter.response.send_message("Channel default language set to Hindi.", ephemeral=True)
        @discord.ui.button(label="Toggle Auto-Translate", style=discord.ButtonStyle.primary)
        async def toggle_auto(self, button: discord.ui.Button, inter: discord.Interaction):
            cur = guild_settings[str(inter.guild.id)][str(inter.channel.id)].get("autotranslate", False)
            guild_settings[str(inter.guild.id)][str(inter.channel.id)]["autotranslate"] = not cur
            save_all()
            await inter.response.send_message(f"Auto-translate set to {not cur}. Note: this requires message_content intent to function.", ephemeral=True)
    view = SetupView()
    text = "Server/Channel settings updated." if changed else "Open setup menu to configure channel settings (language & auto-translate)."
    await interaction.response.send_message(text, ephemeral=True, view=view)
    await post_guild_modlog(interaction.guild, f"Settings updated by {interaction.user} — {changed}")
    log_action(interaction.guild.id, "setup", {"by": str(interaction.user.id), "changes": changed})

# ---------- Translate slash + prefix ----------
@tree.command(name="translate", description="Translate a message by ID (ephemeral)")
@app_commands.describe(message_id="Message ID to translate", channel="Channel containing message (optional)", lang="Target language code, e.g. en, hi")
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
        if not getattr(msg, "content", None):
            await interaction.followup.send("Message has no text.", ephemeral=True)
            return
        try:
            translated = translate_text(msg.content, lang)
            await interaction.followup.send(f"**Translation ({lang})**\n{translated}", ephemeral=True)
        except RuntimeError as e:
            await interaction.followup.send(str(e), ephemeral=True)
    except Exception as e:
        await interaction.followup.send("Message not found or fetch failed.", ephemeral=True)

@bot.command(name="t")
async def prefix_translate(ctx: commands.Context, message_id: int, lang: str = "en"):
    if not USE_MESSAGE_CONTENT_INTENT:
        await ctx.reply("Prefix commands disabled (message content intent not enabled). Use /translate.", mention_author=False)
        return
    try:
        msg = await ctx.channel.fetch_message(message_id)
    except Exception:
        await ctx.reply("Message not found.", mention_author=False)
        return
    try:
        translated = translate_text(msg.content, lang)
        try:
            await ctx.author.send(f"**Translation ({lang})**\n{translated}")
            await ctx.reply("Sent translation to your DMs.", mention_author=False)
        except:
            await ctx.reply(f"**Translation ({lang})**\n{translated}", mention_author=False)
    except RuntimeError as e:
        await ctx.reply(str(e), mention_author=False)

# ---------- GIVEAWAY CORE ----------
def _gen_gid() -> str:
    return str(random.randint(100000, 999999))

async def _announce_giveaway(channel: discord.TextChannel, info: dict) -> discord.Message:
    embed = discord.Embed(title="🎉 Giveaway!", description=info["prize"], color=0x57F287)
    embed.add_field(name="Giveaway ID", value=info["id"], inline=True)
    embed.add_field(name="Ends In", value=human_td(info["duration"]), inline=True)
    embed.set_footer(text=f"Winners: {info['winners']}")
    msg = await channel.send(embed=embed)
    await msg.add_reaction("🎉")
    # Add button view for guaranteed entry
    class EnterView(discord.ui.View):
        def __init__(self, gid):
            super().__init__(timeout=None)
            self.gid = gid
        @discord.ui.button(label="Enter Giveaway", style=discord.ButtonStyle.success, emoji="🎉")
        async def enter(self, button: discord.ui.Button, interaction: discord.Interaction):
            # record entrant in giveaway info
            data = state.setdefault("giveaways", {})
            g = data.get(self.gid)
            if not g or not g.get("active"):
                await interaction.response.send_message("This giveaway is closed.", ephemeral=True)
                return
            entrants = set(g.get("entrants", []))
            if str(interaction.user.id) in entrants:
                await interaction.response.send_message("You already entered.", ephemeral=True)
                return
            entrants.add(str(interaction.user.id))
            g["entrants"] = list(entrants)
            save_json(GIVE_FILE, data)
            await interaction.response.send_message("You have entered the giveaway via button. Good luck!", ephemeral=True)
    view = EnterView(info["id"])
    await channel.send("Click to enter:", view=view)
    return msg

async def _run_countdown_task(gid: str, seconds: int):
    # updates the giveaway embed every COUNTDOWN_INTERVAL seconds
    try:
        while seconds > 0:
            info = state["giveaways"].get(gid)
            if not info or not info.get("active"):
                return
            ch = bot.get_channel(int(info["channel_id"]))
            try:
                msg = await ch.fetch_message(int(info["message_id"]))
            except Exception:
                return
            remaining = int((datetime.fromisoformat(info["ends_at"]) - datetime.utcnow()).total_seconds())
            # edit embed
            try:
                embed = msg.embeds[0] if msg.embeds else discord.Embed(title="🎉 Giveaway", description=info["prize"])
                # update field 'Ends In'
                embed.set_field_at(1, name="Ends In", value=human_td(remaining), inline=True)
                await msg.edit(embed=embed)
            except Exception:
                pass
            # sleep small chunk
            await asyncio.sleep(COUNTDOWN_INTERVAL)
            seconds -= COUNTDOWN_INTERVAL
        # time's up
        await _end_giveaway(gid)
    except asyncio.CancelledError:
        return
    except Exception as e:
        log.exception("Countdown task error: %s", e)

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
    # Collect entrants from both stored entrants and reactions
    entrants = set(info.get("entrants", []))  # from button entries
    # reaction entries
    for react in msg.reactions:
        emoji_name = getattr(react.emoji, "name", react.emoji)
        if emoji_name == "🎉":
            async for u in react.users():
                if u.bot:
                    continue
                entrants.add(str(u.id))
    entrants_list = list(entrants)
    winners = []
    if entrants_list:
        k = min(int(info["winners"]), len(entrants_list))
        winners = random.sample(entrants_list, k)
    # announce winners
    if winners:
        mention_text = " ".join(f"<@{w}>" for w in winners)
        await ch.send(f"🎉 Giveaway {gid} ended! Winners: {mention_text}\nPrize: **{info['prize']}**")
    else:
        await ch.send(f"Giveaway {gid} ended. No valid entrants.")
    info["active"] = False
    info["ended_at"] = datetime.utcnow().isoformat()
    save_all()
    # log action
    log_action(int(info["guild_id"]), "giveaway_end", {"id": gid, "winners": winners, "prize": info.get("prize")})
    # handle winner claim: send DM with claim button (optional)
    # create claim buttons for each winner (if any)
    if winners:
        for w in winners:
            try:
                user = await bot.fetch_user(int(w))
                view = discord.ui.View(timeout=60*30)  # 30 minutes to claim
                claimed = {"claimed": False}
                @discord.ui.button(label="Claim Prize", style=discord.ButtonStyle.success)
                async def claim_button(button: discord.ui.Button, interaction: discord.Interaction):
                    if interaction.user.id != int(w):
                        await interaction.response.send_message("Only the winner can claim.", ephemeral=True)
                        return
                    claimed["claimed"] = True
                    await interaction.response.send_message("You claimed the prize. Contact staff to receive it.", ephemeral=True)
                view.add_item(claim_button)
                await user.send(f"🎉 You won giveaway {gid} — Prize: {info['prize']}", view=view)
            except Exception:
                pass

# Slash group for giveaway actions
@given_group := app_commands.Group(name="giveaway", description="Giveaway commands (start/end/reroll/export)")
tree.add_command(given_group)

@given_group.command(name="start", description="Start a giveaway. (Requires Manage Server)")
@app_commands.describe(duration="Duration in seconds", winners="Number of winners", prize="Prize text", pin="Pin the giveaway message")
async def gw_start(interaction: discord.Interaction, duration:int, winners:int, prize:str, pin:Optional[bool]=False):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("You need Manage Server permission.", ephemeral=True)
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
        "ends_at": (datetime.utcnow() + timedelta(seconds=duration)).isoformat(),
        "active": True,
        "guild_id": str(interaction.guild.id),
        "entrants": []
    }
    # announce
    msg = await _announce_giveaway(interaction.channel, info)
    info["message_id"] = str(msg.id)
    state.setdefault("giveaways", {})[gid] = info
    save_all()
    # schedule countdown + end
    asyncio.create_task(_run_countdown_task(gid, duration))
    if pin:
        try:
            await msg.pin()
        except Exception:
            pass
    await interaction.followup.send(f"Giveaway started (ID: {gid}). Ends in {human_td(duration)}", ephemeral=True)
    await post_guild_modlog(interaction.guild, f"Giveaway {gid} started by {interaction.user} — prize: {prize}")
    log_action(interaction.guild.id, "giveaway_start", {"id": gid, "prize": prize, "winners": winners})

@given_gro
