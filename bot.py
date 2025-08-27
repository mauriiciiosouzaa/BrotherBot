# bot.py
import os, re, sys, logging, threading, asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters
from telegram.error import TimedOut, RetryAfter, NetworkError, Conflict
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
    for attempt in range(5):
        try:
            return await bot.send_message(**kwargs)
        except RetryAfter as e:
            wait = int(getattr(e, "retry_after", 5)) or 5
            log.warning("Flood control, aguardando %ss ...", wait)
            await asyncio.sleep(wait)
        except (TimedOut, NetworkError):
            wait = min(2 ** attempt, 10)
            log.warning("Timeout/rede. Tentando novamente em %ss ...", wait)
            await asyncio.sleep(wait)
        except Exception:
            log.exception("Falha ao enviar mensagem (tentativa %s)", attempt + 1)
            await asyncio.sleep(1)

async def forward_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg or not msg.text:
        return
    if ALLOWED_USERNAME and (user.username or "").lower() != ALLOWED_USERNAME:
        return

    text = replace_corner(msg.text)
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

# ---------- Tratador global de erros ----------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, Conflict):
        # Outra instância está com getUpdates ativo. Espera e segue tentando.
        log.warning("409 Conflict: outra instância usando o mesmo token. Aguardando 30s…")
        await asyncio.sleep(30)
        return
    log.exception("Erro não tratado no handler: %r", err)

if __name__ == "__main__":
    # Healthcheck
    port = int(os.getenv("PORT", "10000"))
    threading.Thread(target=start_health_server, args=(port,), daemon=True).start()

    # Constrói o app (usa defaults do PTB; sem AIORateLimiter p/ evitar dependência extra)
    application = Application.builder().token(TOKEN).build()

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward_text))
    application.add_error_handler(on_error)

    log.info("Bot rodando (long polling).")
    # Evita reprocessar backlog antigo após reinícios → reduz risco de flood/429
    application.run_polling(drop_pending_updates=True, close_loop=True)
