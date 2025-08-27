# bot.py
import os
import re
import sys
import logging
import threading
import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters, AIORateLimiter
from telegram.error import TimedOut, RetryAfter, NetworkError
from telegram.request import HTTPXRequest
import httpx

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
    log.error("CHANNEL_ID inválido (use o ID numérico, ex.: -100223...).")
    sys.exit(1)

log.info("Config: canal=%s, allowed_username=%s", CHANNEL_ID, ALLOWED_USERNAME or "(nenhum)")

PROMO_MSG = 'Entrem no nosso outro canal grátis! 📲 https://t.me/BrotherDosGreens'
CORNER_RE = re.compile(r"https?://(?:www\.)?cornerprobet\.com/?", flags=re.IGNORECASE)

def replace_corner(text: str) -> str:
    return CORNER_RE.sub(PROMO_MSG, text)

async def safe_send(bot, **kwargs):
    """Envio com retentativas e respeito a rate limit."""
    # 5 tentativas com backoff exponencial
    for attempt in range(5):
        try:
            return await bot.send_message(**kwargs)
        except RetryAfter as e:
            wait = int(getattr(e, "retry_after", 5)) or 5
            log.warning("Flood control, aguardando %ss ...", wait)
            await asyncio.sleep(wait)
        except (TimedOut, NetworkError) as e:
            wait = min(2 ** attempt, 10)
            log.warning("Timeout/rede (%s). Tentando novamente em %ss ...", type(e).__name__, wait)
            await asyncio.sleep(wait)
    log.error("Desisti de enviar após várias tentativas.")

async def forward_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.effective_message or not update.effective_message.text:
        return
    if ALLOWED_USERNAME and (user.username or "").lower() != ALLOWED_USERNAME:
        return

    text = replace_corner(update.effective_message.text)
    try:
        await safe_send(context.bot, chat_id=CHANNEL_ID, text=text)
    except Exception:
        log.exception("Falha ao enviar para o canal")

# --------- Healthcheck em thread separada ----------
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
    def log_message(self, *args, **kwargs):
        return

def start_health_server(port: int):
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    log.info("HTTP health em 0.0.0.0:%s", port)
    server.serve_forever()

if __name__ == "__main__":
    # Healthcheck
    port = int(os.getenv("PORT", "10000"))
    threading.Thread(target=start_health_server, args=(port,), daemon=True).start()

    # Aumenta timeouts de rede do PTB
    request = HTTPXRequest(timeout=httpx.Timeout(30.0, connect=10.0, read=30.0))
    # Rate limiter automático do PTB
    application = (
        Application.builder()
        .token(TOKEN)
        .request(request)
        .rate_limiter(AIORateLimiter())  # respeita limites globais por padrão
        .build()
    )

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward_text))

    log.info("Bot rodando (long polling).")
    application.run_polling(close_loop=True)
