# bot.py
import os
import re
import sys
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------- Config & validação ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
log = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID_STR = os.getenv("CHANNEL_ID", "").strip()
ALLOWED_USERNAME = os.getenv("ALLOWED_USERNAME", "").lstrip("@").strip().lower()

if not TOKEN or not CHANNEL_ID_STR:
    log.error("Faltam variáveis: BOT_TOKEN e/ou CHANNEL_ID.")
    sys.exit(1)

try:
    CHANNEL_ID = int(CHANNEL_ID_STR)
except ValueError:
    log.error("CHANNEL_ID inválido: use o ID numérico do canal (ex.: -1002234...).")
    sys.exit(1)

log.info("Config: canal=%s, allowed_username=%s", CHANNEL_ID, ALLOWED_USERNAME or "(nenhum)")

# Mensagem personalizada para substituir o link da CornerPro
PROMO_MSG = 'Entrem no nosso outro canal grátis! 📲 https://t.me/BrotherDosGreens'
# Pega variações com http/https e possíveis barras finais
CORNER_RE = re.compile(r"https?://(?:www\.)?cornerprobet\.com/?", flags=re.IGNORECASE)

def replace_corner(text: str) -> str:
    return CORNER_RE.sub(PROMO_MSG, text)

# ---------- Handlers ----------
async def forward_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    # Permite APENAS o usuário definido em ALLOWED_USERNAME
    if ALLOWED_USERNAME and (user.username or "").lower() != ALLOWED_USERNAME:
        return

    if not update.effective_message or not update.effective_message.text:
        return

    text = update.effective_message.text
    text = replace_corner(text)

    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=text)
    except Exception as e:
        log.exception("Falha ao enviar para o canal: %s", e)

# ---------- Healthcheck em thread separada ----------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    # Evita log barulhento no console
    def log_message(self, format, *args):
        return

def start_health_server(port: int):
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    log.info("HTTP health em 0.0.0.0:%s", port)
    server.serve_forever()

# ---------- Main ----------
if __name__ == "__main__":
    # Sobe o healthcheck numa thread daemon
    port = int(os.getenv("PORT", "10000"))
    threading.Thread(target=start_health_server, args=(port,), daemon=True).start()

    # Telegram bot
    application = Application.builder().token(TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward_text))

    log.info("Bot rodando (long polling).")
    # NÃO usar asyncio.run aqui; deixar o PTB controlar o loop
    application.run_polling(close_loop=True)
