import discord
from discord import app_commands
from discord.ext import commands
from googletrans import Translator
import os

translator = Translator()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=".", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        await bot.tree.sync()
        print("Slash commands synced")
    except Exception as e:
        print("Sync error:", e)

@bot.tree.command(name="t", description="Translate a message privately using its message ID")
@app_commands.describe(message_id="ID of the message you want to translate")
@app_commands.describe(lang="language code, example: en, hi, es")
async def t(interaction: discord.Interaction, message_id: str, lang: str = "en"):

    await interaction.response.defer(ephemeral=True)

    try:
        mid = int(message_id)
    except:
        await interaction.followup.send("❌ Message ID must be a number.", ephemeral=True)
        return

    try:
        msg = await interaction.channel.fetch_message(mid)
    except:
        await interaction.followup.send("❌ Message not found in this channel.", ephemeral=True)
        return

    if msg.author.bot:
        await interaction.followup.send("❌ Cannot translate bot messages.", ephemeral=True)
        return

    if msg.content.strip() == "":
        await interaction.followup.send("❌ Message has no text.", ephemeral=True)
        return

    try:
        result = translator.translate(msg.content, dest=lang)
    except Exception as e:
        await interaction.followup.send(f"❌ Translation error: {e}", ephemeral=True)
        return

    await interaction.followup.send(
        f"🌍 **Translated ({result.src} → {lang})**\n\n{result.text}",
        ephemeral=True
    )

if __name__ == "__main__":
    TOKEN = os.getenv("TOKEN")
    bot.run(TOKEN)
