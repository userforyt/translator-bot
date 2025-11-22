import os
import discord
from discord.ext import commands
from discord import app_commands
from deep_translator import GoogleTranslator

TOKEN = os.getenv("TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='.', intents=intents)
tree = bot.tree


# ------------------------------
# UTILITY: TRANSLATION FUNCTION
# ------------------------------
def translate(text: str, target: str):
    try:
        return GoogleTranslator(source="auto", target=target).translate(text)
    except:
        return "Translation failed."


# ------------------------------
# EVENT: BOT READY
# ------------------------------
@bot.event
async def on_ready():
    print(f"Bot online: {bot.user}")

    try:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            await tree.sync(guild=guild)
            print("Slash commands synced to guild.")
        else:
            await tree.sync()
            print("Slash commands synced globally.")
    except Exception as e:
        print("Slash sync error:", e)


# ------------------------------
# 1) FIXED HELP COMMAND (slash)
# ------------------------------
@tree.command(name="help", description="Show all bot commands.")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📘 ColdMoon Translator — Help Menu",
        description="Here are my commands:",
        color=0x00ffae
    )
    embed.add_field(
        name="/translate <message_id> <lang>",
        value="Translate a specific message.",
        inline=False
    )
    embed.add_field(
        name="/giveaway",
        value="Start, end, or reroll giveaways.",
        inline=False
    )
    embed.add_field(
        name=".t <message_id> <lang>",
        value="Prefix translate command.",
        inline=False
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ------------------------------
# 2) TRANSLATE COMMAND (PREFIX)
# ------------------------------
@bot.command()
async def t(ctx, message_id: int, lang: str):
    try:
        msg = await ctx.channel.fetch_message(message_id)
        translated = translate(msg.content, lang)

        await ctx.author.send(
            f"Translated ({lang}):\n**{translated}**"
        )
        await ctx.message.add_reaction("✅")

    except:
        await ctx.send("❌ Couldn't translate.", delete_after=5)


# ------------------------------
# 3) TRANSLATE COMMAND (SLASH)
# ------------------------------
@tree.command(name="translate", description="Translate a message by its ID.")
@app_commands.describe(
    message_id="ID of the message you want to translate",
    language="Target language code (en, hi, ja, fr...)"
)
async def slash_translate(interaction: discord.Interaction, message_id: str, language: str):
    try:
        msg = await interaction.channel.fetch_message(int(message_id))
        translated = translate(msg.content, language)

        await interaction.response.send_message(
            f"**Translated to {language}:**\n{translated}",
            ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(
            "❌ Failed to translate message.",
            ephemeral=True
        )


# ------------------------------
# 4) SLASH GIVEAWAY COMMAND SYSTEM
# ------------------------------
class GiveawayGroup(app_commands.Group):
    """Giveaway command group like Falcon bot."""

    @app_commands.command(name="start", description="Start a giveaway.")
    @app_commands.describe(
        duration="Duration in seconds",
        prize="Giveaway prize"
    )
    async def start(self, interaction: discord.Interaction, duration: int, prize: str):
        embed = discord.Embed(
            title="🎉 Giveaway Started!",
            description=f"**Prize:** {prize}\nReact with 🎉 to enter!\nEnds in {duration} seconds.",
            color=0x00ff99
        )
        msg = await interaction.channel.send(embed=embed)
        await msg.add_reaction("🎉")

        await interaction.response.send_message("Giveaway started!", ephemeral=True)

    @app_commands.command(name="end", description="Ends a giveaway by message ID.")
    async def end(self, interaction: discord.Interaction, message_id: str):
        try:
            msg = await interaction.channel.fetch_message(int(message_id))
            users = await msg.reactions[0].users().flatten()
            users = [u for u in users if not u.bot]

            import random
            winner = random.choice(users)

            await interaction.channel.send(f"🎉 Winner: {winner.mention}")
            await interaction.response.send_message("Giveaway ended!", ephemeral=True)

        except:
            await interaction.response.send_message("❌ Error ending giveaway.", ephemeral=True)


tree.add_command(GiveawayGroup(name="giveaway", description="Giveaway commands"))


# ------------------------------
# RUN BOT
# ------------------------------
bot.run(TOKEN)
