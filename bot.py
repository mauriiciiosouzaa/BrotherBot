import os
import asyncio
import logging
from aiohttp import web
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
log = logging.getLogger(__name__)

# Variáveis definidas no Render (Dashboard > Environment)
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = int(os.environ["CHANNEL_ID"])  # ex.: -1002234789666

# Substituição solicitada
REWRITE_FROM = "https://cornerprobet.com"
REWRITE_TO = "Entrem no nosso outro canal grátis! 📲 https://t.me/BrotherDosGreens"

def _prepare_text(update: Update) -> str | None:
    """Pega texto normal ou legenda e aplica a substituição."""
    m = update.effective_message
    if not m:
        return None
    text = m.text or m.caption
    if not text:
        return None
    return text.replace(REWRITE_FROM, REWRITE_TO)

async def handle_any(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.username if update.effective_user else "desconhecido"
    chat_id = update.effective_chat.id if update.effective_chat else None
    text = _prepare_text(update)
    log.info("Recebida msg de @%s (chat=%s). Tem texto/legenda? %s", user, chat_id, bool(text))

    if not text:
        return  # ignoramos mensagens sem texto/legenda

    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text)
        log.info("Mensagem reenviada para o canal.")
    except Exception as e:
        log.exception("Falha ao enviar para o canal: %s", e)

# Healthcheck para o Render
async def health(_req):
    return web.Response(text="ok")

async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(~filters.COMMAND, handle_any))

    webapp = web.Application()
    webapp.add_routes([web.get("/", health), web.get("/health", health)])
    runner = web.AppRunner(webapp)
    await runner.setup()
    port = int(os.environ.get("PORT", "10000"))
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    log.info("HTTP health em 0.0.0.0:%s", port)

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
