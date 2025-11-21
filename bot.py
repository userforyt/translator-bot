# bot.py — clean minimal slash-only version (NO message intent, NO crashes)

import os
import logging
import discord
from discord.ext import commands

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("translator-minimal")

# -------- INTENTS (SAFE) --------
intents = discord.Intents.default()
intents.message_content = False   # do NOT request privileged intents

bot = commands.Bot(command_prefix=".", intents=intents)

# -------- BOT READY EVENT --------
@bot.event
async def on_ready():
    log.info("Bot online: %s (id:%s)", bot.user, bot.user.id)

    guild_id = os.getenv("GUILD_ID")
    if guild_id:
        try:
            g = discord.Object(id=int(guild_id))
            await bot.tree.sync(guild=g)
            log.info("Slash commands synced to guild %s", guild_id)
            return
        except Exception as e:
            log.warning("Guild sync failed: %s", e)

    try:
        await bot.tree.sync()
        log.info("Slash commands synced globally")
    except Exception as e:
        log.warning("Global sync failed: %s", e)


# -------- /help COMMAND --------
@bot.tree.command(name="help", description="Show help (private)")
async def slash_help(interaction: discord.Interaction):
    msg = (
        "**Translator Bot (Slash-Only Version)**\n"
        "Use `/t` to translate messages.\n"
        "This version does NOT use message content intent, so no auto-translate or .commands.\n"
    )
    await interaction.response.send_message(msg, ephemeral=True)


# -------- /t COMMAND (translate message) --------
@bot.tree.command(name="t", description="Translate a message by ID (private)")
async def slash_t(
    interaction: discord.Interaction,
    message_id: str,
    lang: str = "en",
    channel: discord.TextChannel = None
):
    await interaction.response.defer(ephemeral=True)

    try:
        msg_id = int(message_id)
    except:
        await interaction.followup.send("Message ID must be a number.", ephemeral=True)
        return

    target = channel or interaction.channel

    try:
        msg = await target.fetch_message(msg_id)
    except:
        await interaction.followup.send("Message not found.", ephemeral=True)
        return

    if not msg.content:
        await interaction.followup.send("Message has no text.", ephemeral=True)
        return

    # Try googletrans
    try:
        from googletrans import Translator
        tr = Translator()
        res = tr.translate(msg.content, dest=lang)
        output = f"**Translated ({getattr(res,'src','auto')} → {lang})**\n{res.text}"
        await interaction.followup.send(output, ephemeral=True)
        return
    except:
        pass

    # Try deep-translator fallback
    try:
        from deep_translator import GoogleTranslator
        translated = GoogleTranslator(source="auto", target=lang).translate(msg.content)
        output = f"**Translated (auto → {lang})**\n{translated}"
        await interaction.followup.send(output, ephemeral=True)
        return
    except:
        await interaction.followup.send(
            "Translation libraries not installed. Install googletrans or deep-translator.",
            ephemeral=True
        )


# -------- RUN BOT --------
if __name__ == "__main__":
    TOKEN = os.getenv("TOKEN")
    if not TOKEN:
        raise SystemExit("TOKEN not set in Railway Variables.")
    bot.run(TOKEN)
