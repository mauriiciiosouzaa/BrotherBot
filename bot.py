import os
import asyncio
import logging
from aiohttp import web
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
log = logging.getLogger(__name__)

# Variáveis de ambiente (defina no Render)
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = int(os.environ["CHANNEL_ID"])  # exemplo: -1002234789666

# Reescrita pedida
REWRITE_FROM = "https://cornerprobet.com"
REWRITE_TO = "Entrem no nosso outro canal grátis! 📲 https://t.me/BrotherDosGreens"

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message.text or ""

    # Aplica a reescrita
    msg = msg.replace(REWRITE_FROM, REWRITE_TO)

    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=msg)
        log.info("Mensagem reenviada para o canal.")
    except Exception as e:
        log.exception("Falha ao enviar para o canal: %s", e)

async def health(_req):
    return web.Response(text="ok")

async def main():
    # Telegram
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Servidor HTTP (necessário no Render)
    webapp = web.Application()
    webapp.add_routes([web.get("/", health), web.get("/health", health)])
    runner = web.AppRunner(webapp)
    await runner.setup()
    port = int(os.environ.get("PORT", "10000"))
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    log.info("HTTP health em 0.0.0.0:%s", port)

    # Inicia o bot (long polling)
    await app.initialize()
    await app.start()
    log.info("Bot rodando (long polling).")

    try:
        await asyncio.Event().wait()
    finally:
        await app.stop()
        await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
