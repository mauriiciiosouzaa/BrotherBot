# bot.py
import os
import re
import sys
import time
import logging
import threading
import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters
from telegram.error import TimedOut, RetryAfter, NetworkError, Conflict, InvalidToken

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
log = logging.getLogger(__name__)

# --------- Config (via variáveis de ambiente) ----------
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

# --------- Regras de conteúdo ----------
PROMO_MSG = 'Entrem no nosso outro canal grátis! 📲 https://t.me/BrotherDosGreens'
CORNER_RE = re.compile(r"https?://(?:www\.)?cornerprobet\.com/?", flags=re.IGNORECASE)

def replace_corner(text: str) -> str:
    return CORNER_RE.sub(PROMO_MSG, text)

# --------- Envio com retentativas ----------
async def safe_send(bot, **kwargs):
    """Envia mensagem com retentativas e respeito a flood control/timeouts."""
    for attempt in range(5):
        try:
            return await bot.send_message(**kwargs)
        except RetryAfter as e:
            wait = int(getattr(e, "retry_after", 5)) or 5
            log.warning("Flood control: aguardando %ss ...", wait)
            await asyncio.sleep(wait)
        except (TimedOut, NetworkError) as e:
            wait = min(2 ** attempt, 10)
            log.warning("Timeout/rede (%s). Tentando novamente em %ss ...", type(e).__name__, wait)
            await asyncio.sleep(wait)
        except Exception:
            log.exception("Falha inesperada ao enviar. Tentando novamente ...")
            await asyncio.sleep(2)
    log.error("Desisti de enviar após várias tentativas.")

# --------- Handler principal ----------
async def forward_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    msg = update.effective_message

    if not user or not msg or not msg.text:
        return

    # Encaminhar só do usuário permitido (se configurado)
    if ALLOWED_USERNAME and (user.username or "").lower() != ALLOWED_USERNAME:
        return

    text = replace_corner(msg.text)
    try:
        await safe_send(context.bot, chat_id=CHANNEL_ID, text=text)
    except Exception:
        log.exception("Falha ao enviar para o canal")

# --------- Healthcheck em thread separada ----------
class HealthHandler(BaseHTTPRequestHandler):
    def _send_ok_headers(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        if self.path in ("/", "/health"):
            self._send_ok_headers()
            self.wfile.write(b"OK")
        else:
            self.send_error(404)

    def do_HEAD(self):
        if self.path in ("/", "/health"):
            # UptimeRobot no free usa HEAD; só cabeçalhos, sem corpo
            self._send_ok_headers()
        else:
            self.send_error(404)

    def log_message(self, *args, **kwargs):
        return

def start_health_server(port: int):
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    log.info("HTTP health em 0.0.0.0:%s", port)
    server.serve_forever()

# --------- Boot ----------
def build_app() -> Application:
    app = (
        Application.builder()
        .token(TOKEN)
        .build()
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward_text))
    return app

if __name__ == "__main__":
    # Inicia o healthcheck HTTP (GET/HEAD) para UptimeRobot/Render
    port = int(os.getenv("PORT", "10000"))
    threading.Thread(target=start_health_server, args=(port,), daemon=True).start()

    # Constrói a aplicação do Telegram
    application = build_app()

    # Tenta rodar continuamente; se aparecer 409 (outra instância), aguarda e tenta de novo
    while True:
        log.info("Bot rodando (long polling).")
        try:
            application.run_polling(drop_pending_updates=True, close_loop=True)
        except Conflict as e:
            log.warning("409 Conflict (outra instância usando o mesmo token). Aguardando 30s…")
            time.sleep(30)
            continue
        except InvalidToken:
            log.error("TOKEN inválido. Verifique a variável BOT_TOKEN no Render.")
            sys.exit(1)
        except Exception:
            log.exception("Erro inesperado no run_polling. Reiniciando em 10s …")
            time.sleep(10)
            continue
