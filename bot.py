# bot.py — PART 1/2
# Full Discord bot (paste Part1 then Part2)

import os
import json
import csv
import random
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

import discord
from discord.ext import commands
from discord import app_commands, File

# ---------- config ----------
SETTINGS_FILE = "settings.json"
GIVE_FILE = "giveaways.json"
MODLOG_FILE = "modlog.json"
BACKUP_DIR = "backups"

os.makedirs(BACKUP_DIR, exist_ok=True)

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise SystemExit("Missing TOKEN env var")

USE_MESSAGE_CONTENT_INTENT = os.getenv("USE_MSG_CONTENT", "false").lower() == "true"
GUILD_ID = os.getenv("GUILD_ID")  # optional, for guild-only slash sync

# ---------- logging ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("bot")

# ---------- intents ----------
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = USE_MESSAGE_CONTENT_INTENT

# ---------- bot ----------
def _prefix_callable(bot, message):
    if not message.guild:
        return "."
    gs = STATE.get("settings", {}).get(str(message.guild.id), {})
    return gs.get("prefix", ".")
bot = commands.Bot(command_prefix=_prefix_callable, intents=intents, help_command=None)
tree = bot.tree

# ---------- persistence ----------
def load_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_json(path: str, data: Dict[str, Any]):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.error("Failed to save %s: %s", path, e)

STATE = {
    "settings": load_json(SETTINGS_FILE),
    "giveaways": load_json(GIVE_FILE),
    "modlog": load_json(MODLOG_FILE),
    "afk": {}
}

def save_all():
    save_json(SETTINGS_FILE, STATE.get("settings", {}))
    save_json(GIVE_FILE, STATE.get("giveaways", {}))
    save_json(MODLOG_FILE, STATE.get("modlog", {}))

# ---------- helpers ----------
def human_td(seconds: int) -> str:
    s = max(0, int(seconds))
    td = timedelta(seconds=s)
    parts: List[str] = []
    if td.days:
        parts.append(f"{td.days}d")
    h, rem = divmod(td.seconds, 3600)
    m, sec = divmod(rem, 60)
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if sec or not parts: parts.append(f"{sec}s")
    return " ".join(parts)

def translate_text(text: str, dest: str) -> str:
    # try googletrans first, fallback to deep-translator
    try:
        from googletrans import Translator as GT
        t = GT()
        r = t.translate(text, dest=dest)
        return getattr(r, "text", str(r))
    except Exception:
        try:
            from deep_translator import GoogleTranslator as DT
            return DT(source="auto", target=dest).translate(text)
        except Exception:
            raise RuntimeError("No translator library available (install googletrans or deep-translator)")

def log_action(guild_id: int, action: str, data: Dict[str, Any]):
    gid = str(guild_id)
    STATE.setdefault("modlog", {}).setdefault(gid, []).append({
        "time": datetime.utcnow().isoformat(),
        "action": action,
        "data": data
    })
    save_all()
    # also attempt to post to per-guild modlog channel if set
    try:
        gs = STATE.get("settings", {}).get(gid, {})
        ch_id = gs.get("_modlog_channel")
        if ch_id:
            ch = bot.get_channel(int(ch_id))
            if ch:
                asyncio.create_task(ch.send(f"[{datetime.utcnow().isoformat()}] {action} — {data}"))
    except Exception:
        pass

# ---------- on_ready ----------
@bot.event
async def on_ready():
    log.info("Bot ready: %s (%s)", bot.user, bot.user.id)
    # sync slash commands (global unless GUILD_ID set)
    try:
        if GUILD_ID:
            gobj = discord.Object(id=int(GUILD_ID))
            await tree.sync(guild=gobj)
            log.info("Synced slash commands to guild %s", GUILD_ID)
        else:
            await tree.sync()
            log.info("Synced slash commands (global)")
    except Exception as e:
        log.warning("Slash sync problem: %s", e)

# ---------- Setup slash ----------
@tree.command(name="setup", description="Open setup for this channel (managers only)")
@app_commands.describe(default_lang="Default language code (e.g. en)", autotranslate="Enable auto-translate for this channel")
async def slash_setup(interaction: discord.Interaction, default_lang: Optional[str] = None, autotranslate: Optional[bool] = None):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
        return
    gid = str(interaction.guild.id)
    gs = STATE.setdefault("settings", {}).setdefault(gid, {})
    chcfg = gs.setdefault(str(interaction.channel.id), {"lang": "en", "autotranslate": False})
    changed = []
    if default_lang:
        chcfg["lang"] = default_lang
        changed.append(f"default_lang -> {default_lang}")
    if autotranslate is not None:
        chcfg["autotranslate"] = bool(autotranslate)
        changed.append(f"autotranslate -> {chcfg['autotranslate']}")
    save_all()

    class V(discord.ui.View):
        @discord.ui.button(label="Set English", style=discord.ButtonStyle.secondary)
        async def b1(self, button, inter):
            gs = STATE.setdefault("settings", {}).setdefault(str(inter.guild.id), {})
            gs.setdefault(str(inter.channel.id), {})["lang"] = "en"
            save_all()
            await inter.response.send_message("Channel language set to English.", ephemeral=True)

        @discord.ui.button(label="Toggle Auto-Translate", style=discord.ButtonStyle.primary)
        async def b2(self, button, inter):
            gs = STATE.setdefault("settings", {}).setdefault(str(inter.guild.id), {})
            cfg = gs.setdefault(str(inter.channel.id), {"lang": "en", "autotranslate": False})
            cfg["autotranslate"] = not cfg.get("autotranslate", False)
            save_all()
            await inter.response.send_message(f"Auto-translate set to {cfg['autotranslate']}.", ephemeral=True)

    await interaction.response.send_message(
        "Settings updated." if changed else "Open setup menu.",
        ephemeral=True, view=V()
    )
    log_action(interaction.guild.id, "setup", {"by": str(interaction.user.id), "changes": changed})        res = tr.translate(text, dest=dest)
        return getattr(res, "text", str(res))
    except Exception:
        try:
            from deep_translator import GoogleTranslator as DT
            return DT(source="auto", target=dest).translate(text)
        except Exception:
            raise RuntimeError("No translator library installed (googletrans or deep-translator).")

# ---------- Bot setup ----------
intents = discord.Intents.default()
intents.message_content = USE_MESSAGE_CONTENT_INTENT

async def _prefix_callable(bot_inst: commands.Bot, message: discord.Message):
    if message.guild:
        gid = str(message.guild.id)
        gs = state.setdefault("settings", {})
        g = gs.setdefault(gid, {})
        prefix = g.get("prefix") or DEFAULT_PREFIX
    else:
        prefix = DEFAULT_PREFIX
    return [prefix, f"<@!{bot_inst.user.id}> ", f"<@{bot_inst.user.id}> "]

bot = commands.Bot(command_prefix=_prefix_callable, intents=intents, help_command=None)
try:
    bot.remove_command("help")
except Exception:
    pass
tree = bot.tree

# ---------- Modlog helpers ----------
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

# ---------- on_ready ----------
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

# ---------- setup & setprefix ----------
@tree.command(name="setup", description="Open setup UI (managers only)")
@app_commands.describe(
    modlog_channel="Channel for mod logs (optional)",
    autotranslate="Enable auto-translate in this channel",
    default_lang="Default language code (e.g. en)"
)
async def slash_setup(
    interaction: discord.Interaction,
    modlog_channel: Optional[discord.TextChannel] = None,
    autotranslate: Optional[bool] = None,
    default_lang: Optional[str] = None
):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
        return

    gid = str(interaction.guild.id)
    gs = state.setdefault("settings", {}).setdefault(gid, {})
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
            gs = state.setdefault("settings", {}).setdefault(str(inter.guild.id), {})
            gs.setdefault(str(inter.channel.id), {})["lang"] = "en"
            save_all()
            await inter.response.send_message("Channel default language set to English.", ephemeral=True)

        @discord.ui.button(label="Set Hindi", style=discord.ButtonStyle.secondary)
        async def hi_btn(self, button: discord.ui.Button, inter: discord.Interaction):
            gs = state.setdefault("settings", {}).setdefault(str(inter.guild.id), {})
            gs.setdefault(str(inter.channel.id), {})["lang"] = "hi"
            save_all()
            await inter.response.send_message("Channel default language set to Hindi.", ephemeral=True)

        @discord.ui.button(label="Toggle Auto-Translate", style=discord.ButtonStyle.primary)
        async def toggle_btn(self, button: discord.ui.Button, inter: discord.Interaction):
            gs = state.setdefault("settings", {}).setdefault(str(inter.guild.id), {})
            cfg = gs.setdefault(str(inter.channel.id), {"lang": "en", "autotranslate": False})
            cfg["autotranslate"] = not cfg.get("autotranslate", False)
            save_all()
            await inter.response.send_message(f"Auto-translate set to {cfg['autotranslate']}.", ephemeral=True)

    view = SetupView()
    text = "Settings updated: " + ", ".join(changed) if changed else "Open setup menu to configure this channel."
    await interaction.response.send_message(text, ephemeral=True, view=view)
    await post_guild_modlog(interaction.guild, f"Settings updated by {interaction.user}: {changed}")
    log_action(interaction.guild.id, "setup", {"by": str(interaction.user.id), "changes": changed})

@tree.command(name="setprefix", description="Change server prefix (Manage Server required)")
@app_commands.describe(prefix="New prefix (example: . or ! or ?)")
async def slash_setprefix(interaction: discord.Interaction, prefix: str):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
        return
    if not prefix:
        await interaction.response.send_message("Prefix cannot be empty.", ephemeral=True)
        return
    gs = state.setdefault("settings", {}).setdefault(str(interaction.guild.id), {})
    gs["prefix"] = prefix
    save_all()
    await interaction.response.send_message(f"Prefix updated to `{prefix}` (persistent).", ephemeral=True)
    await post_guild_modlog(interaction.guild, f"Prefix changed to {prefix} by {interaction.user}")
    log_action(interaction.guild.id, "prefix_change", {"by": str(interaction.user.id), "prefix": prefix})
    # bot.py — part 2/3

# ---------- help & translate ----------
@tree.command(name="help", description="Show help (public)")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Bot Help",
        description="Commands (slash & prefix). Use /setprefix to change the server prefix.",
        color=0x00ffcc
    )
    embed.add_field(
        name="Translate",
        value="`/translate <message_id> <lang>` — translates a message and posts publicly by default.",
        inline=False
    )
    embed.add_field(
        name="Giveaway",
        value="`/giveaway start <duration_seconds> <winners> <prize>` and subcommands: `/giveaway end`, `/giveaway reroll`, `/giveaway export`",
        inline=False
    )
    embed.add_field(
        name="Nuke",
        value="`/nuke` — preview, confirm, backup & duplicate the channel.",
        inline=False
    )
    embed.add_field(
        name="Prefix",
        value="Change prefix: `/setprefix <prefix>` (Manage Server). Default prefix is `.`",
        inline=False
    )
    embed.set_footer(text="You can also use prefix commands like `.t <msg_id> en` if message content intent is enabled.")
    await interaction.response.send_message(embed=embed, ephemeral=False)

@tree.command(name="translate", description="Translate message by ID (public by default)")
@app_commands.describe(message_id="Message ID to translate", channel="Channel containing message (optional)", lang="Language code (e.g. en)")
async def slash_translate(
    interaction: discord.Interaction,
    message_id: str,
    channel: Optional[discord.TextChannel] = None,
    lang: str = "en"
):
    await interaction.response.defer(ephemeral=False)
    target = channel or interaction.channel
    try:
        mid = int(message_id)
    except Exception:
        await interaction.followup.send("Message ID must be numeric.", ephemeral=True)
        return
    try:
        msg = await target.fetch_message(mid)
    except Exception:
        await interaction.followup.send("Message not found or cannot be fetched.", ephemeral=True)
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

# ---------- prefix help & message processing ----------
@bot.command(name="help")
async def prefix_help(ctx: commands.Context):
    if not USE_MESSAGE_CONTENT_INTENT:
        await ctx.reply("Prefix help disabled because message content intent is not enabled. Use /help instead.", mention_author=False)
        return
    p = (state.get("settings", {}).get(str(ctx.guild.id), {}) or {}).get("prefix") or DEFAULT_PREFIX
    text = (
        f"Prefix: `{p}`\n\n"
        "Translate: `.t <message_id> <lang>`\n"
        "Start giveaway (prefix): `.giveaway_start <duration_seconds> <winners> <prize>` (Manage Server)\n"
        "Nuke (prefix): `.nuke` (Manage Channels)\n"
        "Change prefix (slash): `/setprefix <prefix>`\n"
        "Use `/help` for slash help."
    )
    await ctx.reply(text, mention_author=False)

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    # auto-translate if set for channel
    try:
        if message.guild:
            gs = state.get("settings", {}).get(str(message.guild.id), {})
            ch_cfg = gs.get(str(message.channel.id), {})
            if ch_cfg and ch_cfg.get("autotranslate") and ch_cfg.get("lang"):
                if USE_MESSAGE_CONTENT_INTENT:
                    try:
                        translated = translate_text(message.content, ch_cfg["lang"])
                        await message.channel.send(
                            f"🔁 Translation ({ch_cfg['lang']}): {translated}",
                            reference=message
                        )
                    except Exception:
                        pass
    except Exception:
        log.exception("Auto-translate error")

    # let commands extension handle prefix commands if intent enabled
    if USE_MESSAGE_CONTENT_INTENT:
        await bot.process_commands(message)
    else:
        prefixes = await _prefix_callable(bot, message)
        for p in prefixes:
            if message.content.startswith(p):
                try:
                    await message.channel.send(
                        "Prefix commands are disabled because Message Content Intent is not enabled on this bot. "
                        "Use slash commands or enable the intent and set USE_MSG_CONTENT=true.",
                        delete_after=12
                    )
                except Exception:
                    pass
                break

# ---------- prefix translate command ----------
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
        try:
            await ctx.author.send(f"**Translation ({lang})**\n{translated}")
            await ctx.reply("Sent translation to your DMs.", mention_author=False)
        except Exception:
            await ctx.reply(f"**Translation ({lang})**\n{translated}", mention_author=False)
    except Exception as e:
        await ctx.reply(f"Translation error: {e}", mention_author=False)

# ---------- GIVEAWAY core ----------
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
            await interaction.response.send_message("You entered the giveaway. Good luck!", ephemeral=True)

    view = EnterView(info["id"])
    try:
        await channel.send("Click to enter:", view=view)
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

            # safe embed handling
            if msg.embeds:
                embed = msg.embeds[0]
            else:
                embed = discord.Embed(title="🎉 Giveaway", description=info.get("prize", ""))

            remaining = int((datetime.fromisoformat(info["ends_at"]) - datetime.utcnow()).total_seconds())
            try:
                found = False
                for i, f in enumerate(embed.fields):
                    if f.name == "Ends In":
                        embed.set_field_at(i, name="Ends In", value=human_td(remaining), inline=True)
                        found = True
                        break
                if not found:
                    embed.add_field(name="Ends In", value=human_td(remaining), inline=True)
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
        log.exception("Countdown task error for %s", gid)

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
                if u.bot:
                    continue
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
                    def __init__(self, uid: int):
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
                # bot.py — part 3/3

# ---------- Giveaway commands ----------
giveaway_group = app_commands.Group(name="giveaway", description="Giveaway commands")

@giveaway_group.command(name="start", description="Start a giveaway (Manage Server)")
@app_commands.describe(duration="Duration seconds", winners="Number of winners", prize="Prize text", pin="Pin message?")
async def gw_start(
    interaction: discord.Interaction,
    duration: int,
    winners: int,
    prize: str,
    pin: Optional[bool] = False
):
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
        "ends_at": (datetime.utcnow() + timedelta(seconds=duration)).isoformat(),
        "active": True,
        "guild_id": str(interaction.guild.id),
        "entrants": []
    }
    msg = await _announce_giveaway(interaction.channel, info)
    info["message_id"] = str(msg.id)
    state.setdefault("giveaways", {})[gid] = info
    save_all()
    asyncio.create_task(_run_countdown_task(gid, duration))
    if pin:
        try:
            await msg.pin()
        except Exception:
            pass
    await interaction.followup.send(f"Giveaway started (ID: {gid}). Ends in {human_td(duration)}", ephemeral=True)
    await post_guild_modlog(interaction.guild, f"Giveaway {gid} started by {interaction.user} — {prize}")
    log_action(interaction.guild.id, "giveaway_start", {"id": gid, "prize": prize, "winners": winners})

@giveaway_group.command(name="end", description="End giveaway early (Manage Server)")
async def gw_end(interaction: discord.Interaction, giveaway_id: str):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
        return
    if giveaway_id not in state.get("giveaways", {}):
        await interaction.response.send_message("Giveaway ID not found", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await _end_giveaway(giveaway_id)
    await interaction.followup.send("Giveaway ended.", ephemeral=True)
    await post_guild_modlog(interaction.guild, f"Giveaway {giveaway_id} ended by {interaction.user}")
    log_action(interaction.guild.id, "giveaway_end_manual", {"id": giveaway_id, "by": str(interaction.user.id)})

@giveaway_group.command(name="reroll", description="Reroll winners (Manage Server)")
async def gw_reroll(interaction: discord.Interaction, giveaway_id: str, winners: Optional[int] = None):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
        return
    info = state.get("giveaways", {}).get(giveaway_id)
    if not info:
        await interaction.response.send_message("Giveaway not found", ephemeral=True)
        return
    entrants = set(info.get("entrants", []))
    ch = bot.get_channel(int(info["channel_id"]))
    try:
        msg = await ch.fetch_message(int(info["message_id"]))
        for react in msg.reactions:
            if getattr(react.emoji, "name", react.emoji) == "🎉":
                async for u in react.users():
                    if u.bot:
                        continue
                    entrants.add(str(u.id))
    except Exception:
        pass
    entrants_list = list(entrants)
    if not entrants_list:
        await interaction.response.send_message("No entrants to reroll", ephemeral=True)
        return
    k = winners or info.get("winners", 1)
    k = min(len(entrants_list), int(k))
    new_winners = random.sample(entrants_list, k)
    mentions = " ".join(f"<@{w}>" for w in new_winners)
    await interaction.response.send_message(f"🎉 Reroll winners: {mentions}", ephemeral=True)
    await post_guild_modlog(interaction.guild, f"Giveaway {giveaway_id} rerolled by {interaction.user} — winners: {mentions}")
    log_action(interaction.guild.id, "giveaway_reroll", {"id": giveaway_id, "winners": new_winners})

@giveaway_group.command(name="export", description="Export entrants as CSV (Manage Server)")
async def gw_export(interaction: discord.Interaction, giveaway_id: str):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
        return
    info = state.get("giveaways", {}).get(giveaway_id)
    if not info:
        await interaction.response.send_message("Giveaway not found", ephemeral=True)
        return
    entrants = set(info.get("entrants", []))
    ch = bot.get_channel(int(info["channel_id"]))
    try:
        msg = await ch.fetch_message(int(info["message_id"]))
        for react in msg.reactions:
            if getattr(react.emoji, "name", react.emoji) == "🎉":
                async for u in react.users():
                    if u.bot:
                        continue
                    entrants.add(str(u.id))
    except Exception:
        pass
    csv_path = os.path.join(BACKUP_DIR, f"give_{giveaway_id}_{int(datetime.utcnow().timestamp())}.csv")
    try:
        with open(csv_path, "w", newline='', encoding="utf-8") as cf:
            writer = csv.writer(cf)
            writer.writerow(["user_id", "exported_at"])
            for uid in entrants:
                writer.writerow([uid, datetime.utcnow().isoformat()])
        await interaction.response.send_message("CSV exported — sending file...", ephemeral=True)
        await interaction.followup.send(file=File(csv_path))
        log_action(interaction.guild.id, "giveaway_export", {"id": giveaway_id, "file": csv_path})
    except Exception as e:
        await interaction.response.send_message("Export failed: " + str(e), ephemeral=True)

tree.add_command(giveaway_group)

# ---------- Nuke ----------
class NukeView(discord.ui.View):
    def __init__(self, author_id: int, channel: discord.TextChannel, backup_count: int = 200, timeout: int = 60):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.channel = channel
        self.backup_count = backup_count
        self.value: Optional[bool] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the invoker may confirm/cancel.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm Nuke", style=discord.ButtonStyle.danger)
    async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        self.value = True
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        self.value = False
        self.stop()

async def backup_channel_messages(channel: discord.TextChannel, limit: int = 200) -> str:
    fname = os.path.join(BACKUP_DIR, f"backup_{channel.guild.id}_{channel.id}_{int(datetime.utcnow().timestamp())}.txt")
    try:
        msgs = []
        async for m in channel.history(limit=limit, oldest_first=True):
            t = m.created_at.isoformat()
            author = f"{m.author} ({m.author.id})"
            content = m.content or ""
            msgs.append(f"[{t}] {author}: {content}\n")
        with open(fname, "w", encoding="utf-8") as f:
            f.writelines(msgs)
        return fname
    except Exception:
        log.exception("Backup failed")
        return ""

async def duplicate_and_optionally_delete(channel: discord.TextChannel, actor: discord.Member, delete_old: bool = True):
    guild = channel.guild
    overwrites = channel.overwrites
    category = channel.category
    topic = channel.topic or ""
    name = channel.name
    new = await guild.create_text_channel(name, overwrites=overwrites, topic=topic, category=category)
    try:
        await new.edit(position=channel.position)
    except Exception:
        pass
    await new.send(f"💥 Channel nuked by <@{actor.id}> — this is the fresh copy.")
    if delete_old:
        try:
            await channel.delete(reason=f"Nuked by {actor}")
        except Exception:
            await new.send("Failed to delete old channel.")
    return new

@tree.command(name="nuke", description="Preview then duplicate & optionally delete this channel (Manage Channels)")
@app_commands.describe(backup_count="Messages to backup (default 200)", delete_old="Delete old channel after duplicate?")
async def slash_nuke(interaction: discord.Interaction, backup_count: Optional[int] = 200, delete_old: Optional[bool] = True):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("Manage Channels permission required.", ephemeral=True)
        return
    ch = interaction.channel
    try:
        count = 0
        async for _ in ch.history(limit=1000):
            count += 1
    except Exception:
        count = -1
    desc = f"Channel: {ch.mention}\nMessages approx: {count if count >= 0 else 'unknown'}\nThis will backup last {backup_count} messages."
    view = NukeView(interaction.user.id, ch, backup_count=backup_count)
    await interaction.response.send_message(desc, ephemeral=True, view=view)
    await view.wait()
    if view.value is True:
        backup_path = await backup_channel_messages(ch, limit=backup_count)
        new = await duplicate_and_optionally_delete(ch, interaction.user, delete_old=delete_old)
        if backup_path:
            try:
                await interaction.followup.send("Nuke completed. Backup attached.", ephemeral=True)
                await interaction.followup.send(file=File(backup_path))
            except Exception:
                pass
        else:
            await interaction.followup.send("Nuke completed.", ephemeral=True)
        await post_guild_modlog(interaction.guild, f"Channel {ch.name} nuked by {interaction.user}. Backup: {backup_path}")
        log_action(interaction.guild.id, "nuke", {"channel": str(ch.id), "by": str(interaction.user.id), "backup": backup_path})
    else:
        await interaction.followup.send("Nuke cancelled.", ephemeral=True)

# ---------- Prefix nuke ----------
@bot.command(name="nuke")
async def prefix_nuke(ctx: commands.Context, backup_count: Optional[int] = 200, delete_old: Optional[bool] = True):
    if not USE_MESSAGE_CONTENT_INTENT:
        await ctx.reply("Prefix nuke disabled (Message Content Intent not enabled). Use /nuke.", mention_author=False)
        return
    if not ctx.author.guild_permissions.manage_channels:
        await ctx.reply("Manage Channels permission required.", mention_author=False)
        return
    view = NukeView(ctx.author.id, ctx.channel, backup_count=backup_count)
    msg = await ctx.reply("Confirm nuke? This will backup and duplicate the channel.", view=view)
    await view.wait()
    if view.value is True:
        backup_path = await backup_channel_messages(ctx.channel, limit=backup_count)
        new = await duplicate_and_optionally_delete(ctx.channel, ctx.author, delete_old=delete_old)
        await ctx.send("Nuke completed.")
        if backup_path:
            await ctx.send(file=File(backup_path))
        log_action(ctx.guild.id, "nuke", {"channel": str(ctx.channel.id), "by": str(ctx.author.id), "backup": backup_path})
    else:
        await ctx.send("Nuke cancelled.")

# ---------- Modlog & Dump ----------
@tree.command(name="modlog", description="Show recent moderation logs (ephemeral, Manage Server)")
async def slash_modlog(interaction: discord.Interaction, limit: int = 5):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
        return
    logs = state.get("modlog", {}).get(str(interaction.guild.id), [])
    if not logs:
        await interaction.response.send_message("No modlog entries.", ephemeral=True)
        return
    out = logs[-limit:]
    text = "\n".join(f"{e['time']} — {e['action']} — {e['data']}" for e in out)
    await interaction.response.send_message(f"Recent modlog:\n```\n{text}\n```", ephemeral=True)

@tree.command(name="dump", description="Dump settings & giveaways (ephemeral, Manage Server)")
async def slash_dump(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("Manage Server permission required.", ephemeral=True)
        return
    sfile = os.path.join(BACKUP_DIR, f"settings_{interaction.guild.id}_{int(datetime.utcnow().timestamp())}.json")
    gfile = os.path.join(BACKUP_DIR, f"giveaways_{interaction.guild.id}_{int(datetime.utcnow().timestamp())}.json")
    with open(sfile, "w", encoding="utf-8") as f:
        json.dump(state.get("settings", {}), f, indent=2, ensure_ascii=False)
    with open(gfile, "w", encoding="utf-8") as f:
        json.dump(state.get("giveaways", {}), f, indent=2, ensure_ascii=False)
    await interaction.response.send_message("Dump created — sending...", ephemeral=True)
    await interaction.followup.send("Settings:", file=File(sfile))
    await interaction.followup.send("Giveaways:", file=File(gfile))

# ---------- Graceful shutdown & run ----------
async def _on_shutdown():
    save_all()
    log.info("Saved state on shutdown")

if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        asyncio.run(_on_shutdown())
    except Exception as e:
        log.exception("Bot crashed: %s", e)
        save_all()
        raise
