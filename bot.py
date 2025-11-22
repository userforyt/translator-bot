# bot.py
# Slash-first translator + giveaway + nuke + improved help
# Requirements:
#   discord.py==2.3.2
#   googletrans==4.0.0-rc1
#   deep-translator
# Keep TOKEN and optional GUILD_ID in Railway Variables

import os
import json
import random
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List

import discord
from discord.ext import commands, tasks

log = logging.getLogger("translator-bot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# ---------- Intents ----------
intents = discord.Intents.default()
# Do NOT enable message_content unless you have enabled it in Developer Portal for this app
# If you want prefix commands to work, set this to True AND enable the intent in Dev Portal.
intents.message_content = False

bot = commands.Bot(command_prefix=".", intents=intents)

# ---------- persistence ----------
GIVE_FILE = "giveaways.json"

def load_giveaways():
    try:
        with open(GIVE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_giveaways(data):
    try:
        with open(GIVE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.error("Failed to save giveaways: %s", e)

# In-memory active tasks
active_give_tasks = {}

# ---------- utilities ----------
def translate_with_fallback(text: str, dest: str):
    # Try googletrans then deep-translator
    try:
        from googletrans import Translator
        tr = Translator()
        res = tr.translate(text, dest=dest)
        return res.text, getattr(res, "src", "auto")
    except Exception:
        try:
            from deep_translator import GoogleTranslator
            res = GoogleTranslator(source="auto", target=dest).translate(text)
            return res, "auto"
        except Exception as e:
            raise RuntimeError("No translation libraries available.")

def human_td(seconds: int) -> str:
    td = timedelta(seconds=seconds)
    return str(td)

def ensure_prefix_commands_available(ctx):
    # Check if message_content intent is present at runtime
    if not bot.intents.message_content:
        return False
    return True

# ---------- ready / sync ----------
@bot.event
async def on_ready():
    log.info("Logged in as %s (id:%s)", bot.user, bot.user.id)
    # guild sync for instant visibility (if provided)
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

    # resume any active giveaways from file
    data = load_giveaways()
    for gid, info in data.items():
        if info.get("active"):
            ends_at = datetime.fromisoformat(info["ends_at"])
            secs = int((ends_at - datetime.utcnow()).total_seconds())
            if secs > 0:
                log.info("Resuming giveaway %s ends in %s", gid, human_td(secs))
                task = asyncio.create_task(giveaway_wait_and_end(gid, secs))
                active_give_tasks[gid] = task
            else:
                # expired while offline -> schedule immediate end
                asyncio.create_task(giveaway_end_now(gid))

# ---------- HELP (slash + message) ----------
@bot.tree.command(name="help", description="Show translator bot help (ephemeral)")
async def slash_help(interaction: discord.Interaction):
    help_text = (
        "**Translator Bot — Help**\n\n"
        "Slash commands (works without message_content intent):\n"
        "• `/t message_id:<id> lang:<en> channel:<#optional>` → Translate message privately (ephemeral).\n"
        "• `/setup lang:<en> autotranslate:<true/false>` → Configure channel (requires Manage Server).\n"
        "• `/giveaway start duration:<seconds> prize:<text> winners:<n>` → Start giveaway.\n"
        "• `/giveaway end id:<giveaway_id>` → End giveaway early.\n"
        "• `/nuke` → Safe channel duplicate & delete (requires Manage Channels).\n\n"
        "Prefix commands (require Message Content Intent enabled):\n"
        "• `.t <message_id> <lang>` → DM translation to you.\n        `.help` → Message help.\n"
        "• `.giveaway start <seconds> <winners> <prize>` → Start giveaway (prefix).\n"
        "• `.nuke` → prefix nuke (will ask to confirm.)\n\n"
        "Important: Automatic message-based translations cannot be private; use `/t` or prefix `.t` for private translations.\n"
    )
    await interaction.response.send_message(help_text, ephemeral=True)

@bot.command(name="help")
async def prefix_help(ctx: commands.Context):
    if not ensure_prefix_commands_available(ctx):
        await ctx.reply("Prefix commands require Message Content Intent enabled. Use `/help` for slash commands.", mention_author=False)
        return
    txt = (
        "**Translator Bot — Prefix Help**\n"
        ".t <message_id> [lang]\n"
        ".giveaway start <seconds> <winners> <prize>\n"
        ".nuke\n"
    )
    await ctx.reply(txt, mention_author=False)

# ---------- /t and .t ----------
@bot.tree.command(name="t", description="Translate a message by ID (ephemeral to you)")
@discord.app_commands.describe(message_id="Message ID", lang="Target language code (en,hi)", channel="Channel (optional)")
async def slash_t(interaction: discord.Interaction, message_id: str, lang: str = "en", channel: Optional[discord.TextChannel] = None):
    await interaction.response.defer(ephemeral=True)
    try:
        mid = int(message_id)
    except:
        await interaction.followup.send("Message ID must be a number.", ephemeral=True)
        return
    target = channel or interaction.channel
    try:
        msg = await target.fetch_message(mid)
    except Exception:
        await interaction.followup.send("Message not found in that channel.", ephemeral=True)
        return
    if not getattr(msg, "content", None):
        await interaction.followup.send("Message has no text.", ephemeral=True)
        return
    try:
        translated, src = translate_with_fallback(msg.content, lang)
        await interaction.followup.send(f"**Translated ({src} → {lang})**\n{translated}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send("Translation failed: " + str(e), ephemeral=True)

@bot.command(name="t")
async def prefix_t(ctx: commands.Context, message_id: int, lang: str = "en"):
    if not ensure_prefix_commands_available(ctx):
        await ctx.reply("Prefix commands require Message Content Intent. Use `/t` (slash) instead.", mention_author=False)
        return
    try:
        msg = await ctx.channel.fetch_message(message_id)
    except Exception:
        await ctx.reply("Message not found.", mention_author=False)
        return
    if not msg.content:
        await ctx.reply("Target message has no text.", mention_author=False)
        return
    try:
        translated, src = translate_with_fallback(msg.content, lang)
        # DM the requester privately
        try:
            dm = await ctx.author.create_dm()
            await dm.send(f"**Translation ({src} → {lang})**\n{translated}")
            await ctx.reply("Sent translation to your DMs.", mention_author=False)
        except:
            await ctx.reply(f"**Translation ({src} → {lang})**\n{translated}", mention_author=False)
    except Exception as e:
        await ctx.reply("Translation failed: " + str(e), mention_author=False)

# ---------- /setup (store per-channel settings) ----------
@bot.tree.command(name="setup", description="Configure channel default language & autotranslate (ephemeral)")
@discord.app_commands.describe(lang="Default language code", autotranslate="Enable auto-translate for this channel")
async def slash_setup(interaction: discord.Interaction, lang: Optional[str] = None, autotranslate: Optional[bool] = None):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("You need Manage Server permission to run setup.", ephemeral=True)
        return
    # load settings file
    settings = load_giveaways()  # reuse file? better to have separate settings, but keep minimal: use giveaways.json for persistence keys
    # we store channel settings under settings["_channels"]
    channels = settings.get("_channels", {})
    g = str(interaction.guild.id)
    c = str(interaction.channel.id)
    if g not in channels:
        channels[g] = {}
    if c not in channels[g]:
        channels[g][c] = {"lang": "en", "autotranslate": False}
    if lang:
        channels[g][c]["lang"] = lang
    if autotranslate is not None:
        channels[g][c]["autotranslate"] = bool(autotranslate)
    settings["_channels"] = channels
    save_giveaways(settings)
    changed = []
    if lang:
        changed.append(f"default language -> `{lang}`")
    if autotranslate is not None:
        changed.append(f"autotranslate -> `{channels[g][c]['autotranslate']}`")
    if changed:
        await interaction.response.send_message("Settings updated: " + ", ".join(changed), ephemeral=True)
    else:
        await interaction.response.send_message(f"Channel settings: lang=`{channels[g][c]['lang']}`, autotranslate=`{channels[g][c]['autotranslate']}`", ephemeral=True)

# ---------- Giveaway system (slash + prefix) ----------
def gen_give_id():
    return str(random.randint(100000, 999999))

async def giveaway_announce(channel: discord.TextChannel, info):
    embed = discord.Embed(title="🎉 Giveaway!", description=info["prize"], color=0x00FF00)
    embed.add_field(name="Giveaway ID", value=info["id"], inline=True)
    embed.add_field(name="Ends In", value=human_td(info["duration"]), inline=True)
    embed.set_footer(text=f"Winners: {info['winners']}")
    msg = await channel.send(embed=embed)
    await msg.add_reaction("🎉")
    return msg.id

async def giveaway_wait_and_end(gid: str, seconds: int):
    try:
        await asyncio.sleep(seconds)
        await giveaway_end_now(gid)
    except asyncio.CancelledError:
        log.info("Giveaway %s cancelled", gid)

async def giveaway_end_now(gid: str):
    data = load_giveaways()
    info = data.get(gid)
    if not info:
        return
    if not info.get("active"):
        return
    channel = bot.get_channel(int(info["channel_id"]))
    try:
        msg = await channel.fetch_message(int(info["message_id"]))
    except Exception:
        info["active"] = False
        save_giveaways(data)
        return
    # collect entrants
    users = set()
    for react in msg.reactions:
        if getattr(react.emoji, "name", react.emoji) == "🎉" or react.emoji == "🎉":
            async for u in react.users():
                if u.bot:
                    continue
                users.add(u.id)
    winners = []
    users_list = list(users)
    if users_list:
        k = min(info["winners"], len(users_list))
        winners = random.sample(users_list, k)
    # announce
    if winners:
        mentions = " ".join(f"<@{uid}>" for uid in winners)
        await channel.send(f"🎉 Giveaway ended! Winners: {mentions}\nPrize: **{info['prize']}**")
    else:
        await channel.send(f"No valid entrants for giveaway {gid}.")
    info["active"] = False
    save_giveaways(data)
    # cancel task if present
    task = active_give_tasks.pop(gid, None)
    if task:
        task.cancel()

# Slash: start giveaway
@bot.tree.command(name="giveaway", description="Manage giveaways")
async def slash_giveaway(interaction: discord.Interaction):
    # Top-level no-op; we add subcommands below
    await interaction.response.send_message("Use subcommands: start/end/list", ephemeral=True)

@slash_giveaway.subcommand(name="start", description="Start a giveaway")
async def giveaway_start(interaction: discord.Interaction, duration: int, winners: int, prize: str):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("You need Manage Server permission to start giveaways.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    gid = gen_give_id()
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
    msgid = await giveaway_announce(interaction.channel, info)
    info["message_id"] = str(msgid)
    data = load_giveaways()
    data[gid] = info
    save_giveaways(data)
    # schedule
    task = asyncio.create_task(giveaway_wait_and_end(gid, duration))
    active_give_tasks[gid] = task
    await interaction.followup.send(f"Giveaway started (ID: {gid}). Ends in {human_td(duration)}", ephemeral=True)

@slash_giveaway.subcommand(name="end", description="End a giveaway early")
async def giveaway_end(interaction: discord.Interaction, giveaway_id: str):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("You need Manage Server permission to end giveaways.", ephemeral=True)
        return
    data = load_giveaways()
    if giveaway_id not in data:
        await interaction.response.send_message("Giveaway ID not found.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await giveaway_end_now(giveaway_id)
    await interaction.followup.send("Giveaway ended.", ephemeral=True)

# Prefix giveaway start
@bot.command(name="giveaway")
async def prefix_giveaway(ctx: commands.Context, sub: str, *args):
    if not ensure_prefix_commands_available(ctx):
        await ctx.reply("Prefix giveaway commands require Message Content Intent. Use slash /giveaway instead.", mention_author=False)
        return
    if sub.lower() == "start":
        try:
            duration = int(args[0]); winners = int(args[1]); prize = " ".join(args[2:])
        except:
            await ctx.reply("Usage: .giveaway start <seconds> <winners> <prize>", mention_author=False)
            return
        if not ctx.author.guild_permissions.manage_guild:
            await ctx.reply("You need Manage Server permission.", mention_author=False)
            return
        gid = gen_give_id()
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
        msgid = await giveaway_announce(ctx.channel, info)
        info["message_id"] = str(msgid)
        data = load_giveaways()
        data[gid] = info
        save_giveaways(data)
        task = asyncio.create_task(giveaway_wait_and_end(gid, duration))
        active_give_tasks[gid] = task
        await ctx.reply(f"Giveaway started (ID: {gid}). Ends in {human_td(duration)}", mention_author=False)

# ---------- Nuke command (slash + prefix) ----------
class ConfirmNuke(discord.ui.View):
    def __init__(self, author_id: int, timeout: int = 30):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.value: Optional[bool] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # only the original author can confirm
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the command invoker can confirm/cancel.", ephemeral=True)
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

async def duplicate_channel_and_delete(channel: discord.TextChannel, actor: discord.Member):
    guild = channel.guild
    overwrites = channel.overwrites
    # create new channel with same name + "-nuked" prefix or keep same name
    name = channel.name
    # find position
    position = channel.position
    # create with same category
    category = channel.category
    new = await guild.create_text_channel(name, overwrites=overwrites, topic=channel.topic or "", category=category)
    try:
        await new.edit(position=position)
    except Exception:
        pass
    # mention actor and send notice
    await new.send(f"💥 Channel nuked by <@{actor.id}> — this is the fresh copy.")
    # delete old channel
    try:
        await channel.delete(reason=f"Nuked by {actor}")
    except Exception as e:
        await new.send(f"Failed to delete old channel: {e}")

@bot.tree.command(name="nuke", description="Duplicate and delete this channel (confirmation required)")
async def slash_nuke(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("You need Manage Channels permission to nuke.", ephemeral=True)
        return
    await interaction.response.send_message("Are you sure you want to nuke this channel? This will duplicate and delete the channel.", ephemeral=True, view=ConfirmNuke(interaction.user.id))
    # The view will handle confirmation by author; but we need to wait for the view to stop and then act
    view = ConfirmNuke(interaction.user.id)
    await interaction.followup.send("Waiting for confirmation...", ephemeral=True, view=view)
    await view.wait()
    if view.value is True:
        # perform nuke
        await duplicate_channel_and_delete(interaction.channel, interaction.user)
        await interaction.followup.send("Nuke completed.", ephemeral=True)
    else:
        await interaction.followup.send("Nuke cancelled.", ephemeral=True)

@bot.command(name="nuke")
async def prefix_nuke(ctx: commands.Context):
    if not ensure_prefix_commands_available(ctx):
        await ctx.reply("Prefix nuke requires Message Content Intent. Use `/nuke` instead.", mention_author=False)
        return
    if not ctx.author.guild_permissions.manage_channels:
        await ctx.reply("You need Manage Channels permission.", mention_author=False)
        return
    view = ConfirmNuke(ctx.author.id)
    msg = await ctx.reply("Are you sure you want to nuke this channel? Confirm below.", mention_author=False, view=view)
    await view.wait()
    if view.value is True:
        await duplicate_channel_and_delete(ctx.channel, ctx.author)
        await ctx.reply("Nuke completed.", mention_author=False)
    else:
        await ctx.reply("Nuke cancelled.", mention_author=False)

# ---------- run ----------
if __name__ == "__main__":
    TOKEN = os.getenv("TOKEN")
    if not TOKEN:
        log.error("TOKEN not set in env")
        raise SystemExit("Missing TOKEN")
    bot.run(TOKEN)
