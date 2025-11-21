# bot.py - crash-hardened translator using deep-translator
import os, logging, asyncio
from discord import app_commands
from discord.ext import commands
from deep_translator import GoogleTranslator

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("translator-bot")

intents = commands.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=".", intents=intents)

async def safe_sync():
    for i in range(3):
        try:
            await bot.tree.sync()
            log.info("Slash commands synced")
            return
        except Exception as e:
            log.warning("Sync failed (%s) - retrying", e)
            await asyncio.sleep(2)
    log.error("Failed to sync slash commands after retries")

@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} ({getattr(bot.user,'id',None)})")
    await safe_sync()

def translate_text(text, target="en"):
    # deep-translator handles detection itself
    return GoogleTranslator(source='auto', target=target).translate(text)

@bot.tree.command(name="t", description="Translate a message privately using its message ID")
@app_commands.describe(message_id="ID of the message to translate", lang="target language code (en, hi, etc.)")
async def t(interaction: commands.Context, message_id: str, lang: str = "en"):
    await interaction.response.defer(ephemeral=True)
    try:
        mid = int(message_id)
    except:
        await interaction.followup.send("Message ID must be a number.", ephemeral=True)
        return

    try:
        msg = await interaction.channel.fetch_message(mid)
    except Exception:
        await interaction.followup.send("Could not find message (check channel/ID).", ephemeral=True)
        return

    if not msg.content or msg.content.strip() == "":
        await interaction.followup.send("Target message has no text.", ephemeral=True)
        return

    try:
        translated = translate_text(msg.content, target=lang)
        await interaction.followup.send(f"🌍 Translated → {lang}\n\n{translated}", ephemeral=True)
    except Exception as e:
        log.exception("Translation error")
        await interaction.followup.send("Translation service failed. Try again later.", ephemeral=True)

if __name__ == "__main__":
    TOKEN = os.getenv("TOKEN")
    if not TOKEN:
        log.error("TOKEN not set")
        raise SystemExit("Missing TOKEN")
    try:
        bot.run(TOKEN)
    except Exception:
        log.exception("Bot failed to run")
        raise
