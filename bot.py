# bot.py (crash-safe temporary)
import os, logging
from discord.ext import commands

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("crash-safe")

intents = commands.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=".", intents=intents)

@bot.event
async def on_ready():
    log.info(f"Bot started: {bot.user} (id: {getattr(bot.user,'id',None)})")

# minimal safe commands so it doesn't crash
@bot.command(name="ping")
async def ping(ctx):
    await ctx.send("pong")

if __name__ == "__main__":
    TOKEN = os.getenv("TOKEN")
    if not TOKEN:
        log.error("TOKEN missing")
        raise SystemExit("Missing TOKEN")
    try:
        bot.run(TOKEN)
    except Exception:
        log.exception("Failed to run bot")
        raise
