import os
import discord
from discord.ext import commands
from googletrans import Translator

# ------------------------
# INTENTS (FIXED)
# ------------------------
intents = discord.Intents.default()
intents.message_content = True  # REQUIRED to read message text

bot = commands.Bot(command_prefix=".", intents=intents)
translator = Translator()

# ------------------------
# BOT READY EVENT
# ------------------------
@bot.event
async def on_ready():
    print(f"Bot is online as {bot.user}")

# ------------------------
# TRANSLATE COMMAND
# Usage: .t <message_id> <target_language>
# Example: .t 1234567890 en
# ------------------------
@bot.command()
async def t(ctx, message_id: int, target_language: str = "en"):
    try:
        # Fetch message by ID from same channel
        msg = await ctx.channel.fetch_message(message_id)

        # Translate text
        translation = translator.translate(msg.content, dest=target_language)

        # Reply ONLY visible to the user (ephemeral style)
        await ctx.reply(
            f"**Translated ({translation.src} → {target_language})**:\n{translation.text}",
            mention_author=False
        )

    except Exception as e:
        await ctx.reply(f"❌ Error: {str(e)}", mention_author=False)

# ------------------------
# START BOT
# ------------------------
TOKEN = os.getenv("TOKEN")
bot.run(TOKEN)
