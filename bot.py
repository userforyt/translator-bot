# bot.py – NO message_content intent, SLASH COMMAND ONLY, zero crashes

import os
import logging
import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("translator-slash")

# --- Intents (SAFE — does NOT request privileged intents) ---
intents = discord.Intents.default()
# DO NOT enable message_content
intents.message_content = False

bot = commands.Bot(command_prefix=".", intents=intents)

# --- Safe translator wrapper (googletrans or deep-translator) ---
def translate_text(text: str, lang: str):
    try:
        from googletrans import Translator
        tr = Translator()
        res = tr.translate(text, dest=lang)
        return res.text, res.src
    except Exception:
        try:
            from deep_translator import GoogleTranslator
            res = GoogleTranslator(source="auto", target=lang).translate(text)
            return res, "auto"
        except:
            raise RuntimeError("Translation libraries missing.")

# --- Bot ready ---
@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} (id: {bot.user.id})")

    guild_id = os.getenv("GUILD_ID")
    if guild_id:
        try:
            g = discord.Object(id=int(guild_id))
            await bot.tree.sync(guild=g)
            log.info(f"Slash commands synced to guild {guild_id}")
            return
        except Exception as e:
            log.warning(f"Guild sync failed: {e}")

    try:
        await bot.tree.sync()
        log.info("Global slash commands synced")
    except Exception as e:
        log.warning(f"Global slash sync failed: {e}")


# ======================= SLASH COMMANDS ONLY =======================

# /t – translate a message by ID
@bot.tree.command(name="t", description="Translate a message by its ID (private reply).")
@app_commands.describe(
    message_id="The message ID you want to translate",
    lang="Language code (example: en, hi, es)",
    channel="Channel where the message is located (optional)"
)
async def slash_translate(
    interaction: discord.Interaction,
    message_id: str,
    lang: str = "en",
    channel: Optional[discord.TextChannel] = None
):
    await interaction.response.defer(ephemeral=True)

    try:
        mid = int(message_id)
    except:
        await interaction.followup.send("Message ID must be a number.", ephemeral=True)
        return

    target_channel = channel or interaction.channel

    try:
        msg = await target_channel.fetch_message(mid)
    except:
        await interaction.followup.send("Message not found in that channel.", ephemeral=True)
        return

    if not msg.content:
        await interaction.followup.send("Message has no text.", ephemeral=True)
        return

    try:
        translated, src = translate_text(msg.content, lang)
    except Exception as e:
        await interaction.followup.send(f"Translation failed: {e}", ephemeral=True)
        return

    await interaction.followup.send(
        f"**Translated ({src} → {lang})**\n{translated}",
        ephemeral=True
    )


# /help – private help message
@bot.tree.command(name="help", description="Show help for translator bot")
async def slash_help(interaction: discord.Interaction):
    txt = (
        "**Translator Bot Help (Slash-Only Mode)**\n\n"
        "Commands:\n"
        "• `/t message_id:<id> lang:<en>` → Translate message privately\n"
        "• `/help` → Show this\n\n"
        "⚠ Since message_content intent is disabled, message commands like `.t` and auto-translate do NOT work.\n"
    )
    await interaction.response.send_message(txt, ephemeral=True)


# ======================= RUN BOT =======================

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise SystemExit("TOKEN missing in Railway Variables!")

bot.run(TOKEN)        except Exception as e:
            log.warning("Guild sync failed: %s", e)

    try:
        await bot.tree.sync()
        log.info("Slash commands synced (global)")
    except Exception as e:
        log.warning("Global slash sync failed: %s", e)

@bot.event
async def on_message(message: discord.Message):
    # Allow commands to be processed
    await bot.process_commands(message)

    # Ignore bots & DMs
    if message.author.bot or message.guild is None:
        return

    # Auto-translate if enabled
    cfg = await get_channel_cfg(message.guild.id, message.channel.id)
    if not cfg.get("autotranslate", False):
        return

    if not message.content or message.content.strip() == "":
        return

    target = cfg.get("lang", "en")
    try:
        translated, src = translate_with_libs(message.content, dest=target)
        reply_text = f"**Auto-Translation ({src} → {target})**\n{translated}"
        # Cannot be ephemeral; post as a reply publicly
        await message.reply(reply_text, mention_author=False)
    except RuntimeError as e:
        log.error("Auto-translate failed (no translator): %s", e)
    except Exception as e:
        log.exception("Auto-translate failed: %s", e)

# ---------- message (dot) commands ----------
@bot.command(name="t", aliases=["translate"])
async def msg_translate(ctx: commands.Context, message_id: int, lang: Optional[str] = "en"):
    """
    .t <message_id> [lang]  -> DMs the invoker the translation (private)
    """
    await ctx.trigger_typing()
    try:
        msg = await ctx.channel.fetch_message(message_id)
    except Exception:
        await ctx.send("Message not found in this channel. Check ID.", delete_after=8)
        return

    if not msg.content or msg.content.strip() == "":
        await ctx.send("Target message has no text.", delete_after=8)
        return

    try:
        translated, src = translate_with_libs(msg.content, dest=lang)
    except RuntimeError as e:
        await ctx.send("Translation libraries missing. Ask the admin to install googletrans or deep-translator.", delete_after=10)
        log.error("Translate libs missing: %s", e)
        return
    except Exception as e:
        await ctx.send("Translation failed: " + str(e), delete_after=8)
        return

    # DM the invoker
    try:
        dm = await ctx.author.create_dm()
        await dm.send(f"**Translation ({src} → {lang})**\n{translated}")
        await ctx.send("I sent the translation to your DMs.", delete_after=6)
    except Exception:
        # fallback: reply publicly but without mention
        await ctx.reply(f"**Translation ({src} → {lang})**\n{translated}", mention_author=False)

@bot.command(name="setlang")
@commands.has_permissions(manage_guild=True)
async def setlang_cmd(ctx: commands.Context, lang: str):
    cfg = await get_channel_cfg(ctx.guild.id, ctx.channel.id)
    cfg["lang"] = lang
    await save_settings(bot.settings)
    await ctx.send(f"Default language for this channel set to `{lang}`.", delete_after=8)

@bot.command(name="autotranslate")
@commands.has_permissions(manage_guild=True)
async def autotranslate_cmd(ctx: commands.Context, mode: str):
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

@bot.command(name="help")
async def message_help(ctx: commands.Context):
    help_text = (
        "**Translator Bot Help (message commands)**\n\n"
        "`.t <message_id> [lang]` → DM you the translation (private)\n"
        "`.setlang <lang>` → Set channel default language (Manage Server required)\n"
        "`.autotranslate on|off` → Toggle auto-translate for this channel (Manage Server required)\n\n"
        "Use `/t` (slash) to translate privately (ephemeral) and `/setup` to configure channel settings.\n"
        "If slash commands are missing, invite the bot with the `applications.commands` scope and optionally set GUILD_ID in Railway for instant sync."
    )
    await ctx.reply(help_text, mention_author=False)

# ---------- Slash commands ----------
@bot.tree.command(name="t", description="Translate a message by ID (ephemeral only to you).")
@discord.app_commands.describe(message_id="ID of the message to translate", channel="Channel (optional)", lang="Target language (e.g. en, hi)")
async def slash_t(interaction: discord.Interaction, message_id: str, channel: Optional[discord.TextChannel] = None, lang: str = "en"):
    await interaction.response.defer(ephemeral=True)
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
        translated, src = translate_with_libs(msg.content, dest=lang)
        await interaction.followup.send(f"**Translation ({src} → {lang})**\n{translated}", ephemeral=True)
    except RuntimeError:
        await interaction.followup.send("Translation libraries are not installed (googletrans/deep-translator).", ephemeral=True)
    except Exception as e:
        await interaction.followup.send("Translation failed: " + str(e), ephemeral=True)

@bot.tree.command(name="setup", description="Configure default language and auto-translate for this channel (ephemeral).")
@discord.app_commands.describe(lang="Default language code (e.g. en, hi)", autotranslate="Enable automatic translation in this channel")
async def slash_setup(interaction: discord.Interaction, lang: Optional[str] = None, autotranslate: Optional[bool] = None):
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
        await interaction.response.send_message(f"Channel settings: lang=`{cfg.get('lang','en')}`, autotranslate=`{cfg.get('autotranslate',False)}`", ephemeral=True)

@bot.tree.command(name="help", description="Show translator bot help (ephemeral).")
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

# ---------- run ----------
if __name__ == "__main__":
    TOKEN = os.getenv("TOKEN")
    if not TOKEN:
        log.error("TOKEN environment variable not set.")
        raise SystemExit("Missing TOKEN")
    try:
        bot.run(TOKEN)
    except Exception as e:
        log.exception("Bot crashed on start: %s", e)
        raise
