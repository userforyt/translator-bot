import os
import logging
import asyncio
import discord
from discord import app_commands
from googletrans import Translator
from discord.ext import commands

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("translator-bot")

translator = Translator()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=".", intents=intents)


async def safe_sync():
    """Safely sync slash commands with retries."""
    for i in range(3):
        try:
            await bot.tree.sync()
            log.info("Slash commands synced")
            return
        except Exception as e:
            log.warning(f"Sync attempt {i+1} failed: {e}")
            await asyncio.sleep(2)
    log.error("Failed to sync slash commands after 3 retries.")


@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await safe_sync()


def translate_safe(text: str, target: str = "en"):
    """Translate using googletrans with error handling."""
    try:
        result = translator.translate(text, dest=target)
        return result.text, result.src
    except Exception as e:
        log.error(f"Translation failed: {e}")
        raise


@bot.tree.command(
    name="t",
    description="Translate a message privately using its message ID.",
)
@app_commands.describe(
    message_id="The ID of the message you want to translate",
    lang="Target language (en, hi, es, etc.)"
)
async def translate_cmd(interaction: discord.Interaction, message_id: str, lang: str = "en"):

    await interaction.response.defer(ephemeral=True)

    # Validate ID
    if not message_id.isdigit():
        await interaction.followup.send("❌ Message ID must be a number.", ephemeral=True)
        return

    mid = int(message_id)

    # Attempt to fetch message
    try:
        msg = await interaction.channel.fetch_message(mid)
    except:
        await interaction.followup.send("❌ Message not found in this channel.", ephemeral=True)
        return

    # Ensure message has text
    if not msg.content or msg.content.strip() == "":
        await interaction.followup.send("❌ Message has no text to translate.", ephemeral=True)
        return

    # Translate text
    try:
        translated, src = translate_safe(msg.content, target=lang)
    except:
        await interaction.followup.send("❌ Translation failed. Try again later.", ephemeral=True)
        return

    # Reply privately
    await interaction.followup.send(
        f"🌍 **Translated ({src} → {lang})**\n\n{translated}",
        ephemeral=True
    )


if __name__ == "__main__":
    TOKEN = os.getenv("TOKEN")
    if not TOKEN:
        log.error("TOKEN environment variable not set.")
        raise SystemExit("TOKEN missing")

    try:
        bot.run(TOKEN)
    except Exception as e:
        log.error(f"Bot crashed: {e}")
        raise
