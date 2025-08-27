import os
import asyncio
import logging
from aiohttp import web
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================
# LOGGING
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
log = logging.getLogger(__name__)

# =========================
# CONFIG (definidas no Render)
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")  # no Render
CHANNEL_ID_ENV = os.getenv("CHANNEL_ID")  # no Render (ex: -1002234789666)

if not BOT_TOKEN:
    raise RuntimeError("Faltou a variável de ambiente BOT_TOKEN.")
if not CHANNEL_ID_ENV:
    raise RuntimeError("Faltou a variável de ambiente CHANNEL_ID.")

try:
    CHANNEL_ID = int(CHANNEL_ID_ENV)
except Exception:
    raise RuntimeError("CHANNEL_ID inválido. Use o ID numérico do canal (ex: -1002234789666).")

# Apenas esse usuário pode disparar encaminhamento
ALLOWED_USERNAME = os.getenv("ALLOWED_USERNAME", "MauriiciioDeSouzaa").lower()

# Substituição solicitada
REPLACE_FROM = "https://cornerprobet.com"
REPLACE_TO = "Entrem no nosso outro canal grátis! 📲 https://t.me/BrotherDosGreens"

# =========================
# HANDLERS
# =========================
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Encaminha mensagens APENAS do @MauriiciioDeSouzaa para o canal,
    aplicando a substituição do link se existir. Ignora posts de canal/loop.
    """
    if not update.effective_chat or not update.message:
        return

    chat = update.effective_chat
    msg = update.message

    # Evita loop e ignora mensagens que já vêm de canal
    if chat.type == "channel" or chat.id == CHANNEL_ID:
        return

    # Checa quem enviou
    from_user = msg.from_user
    username = (from_user.username or "").lower() if from_user else ""
    if username != ALLOWED_USERNAME:
        logging.info(f"Mensagem ignorada: autor não autorizado ({username}).")
        return

    # Pega texto/caption
    text = msg.text or msg.caption or ""
    if not text:
        logging.info("Mensagem do autorizado sem texto/caption. Ignorando.")
        return

    # Substituição do link
    if REPLACE_FROM in text:
        text = text.replace(REPLACE_FROM, REPLACE_TO)

    logging.info(f"Mensagem autorizada de @{username}: {text[:160].replace(os.linesep, ' ')}...")

    try:
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
            parse_mode=ParseMode.HTML
        )
        logging.info("Mensagem encaminhada para o canal.")
    except Exception as e:
        logging.exception(f"Falha ao enviar para o canal: {e}")

# =========================
# HEALTH CHECK HTTP (Render)
# =========================
async def handle_root(_request):
    return web.Response(text="OK - BrotherBot")

async def handle_health(_request):
    return web.json_response({"status": "ok"})

async def aiohttp_serve():
    app = web.Application()
    app.add_routes([
        web.get("/", handle_root),
        web.get("/health", handle_health),
    ])
    port = int(os.getenv("PORT", "10000"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"HTTP health em 0.0.0.0:{port}")

# =========================
# STARTUP
# =========================
async def on_start(app):
    # Garante que NÃO há webhook ativo (para o polling funcionar).
    try:
        await app.bot.delete_webhook()
        logging.info("Webhook deletado (ok).")
    except Exception as e:
        logging.warning(f"Não foi possível deletar webhook: {e}")

async def main():
    # Servidor HTTP para o Render
    asyncio.create_task(aiohttp_serve())

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    application.post_init = on_start

    # Captura QUALQUER texto (exceto comandos). Captions também entram via msg.caption no handler.
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), on_text))

    logging.info("Bot rodando (long polling).")
    await application.run_polling(close_loop=False)

if __name__ == "__main__":
    asyncio.run(main())
