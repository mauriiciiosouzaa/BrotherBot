import os
import re
import asyncio
import logging
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError

# (A) --- IMPORTS do health server (NOVO) ---
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
# -------------------------------------------

# EXTRA: Scraping para resultado (GREEN/RED)
import requests
from bs4 import BeautifulSoup

# ==============================
# Carregar variáveis do .env
# ==============================
load_dotenv()

# (B) --- Healthcheck HTTP (NOVO) ---
class HealthHandler(BaseHTTPRequestHandler):
    def _ok(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
    def do_GET(self):
        if self.path in ("/", "/health"):
            self._ok()
            self.wfile.write(b"OK")
        else:
            self.send_error(404)
    def do_HEAD(self):
        if self.path in ("/", "/health"):
            self._ok()
        else:
            self.send_error(404)
    def log_message(self, *args, **kwargs):
        return

def start_health_server(port: int):
    logging.info(f"HTTP health em 0.0.0.0:{port}")
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()
# ------------------------------------

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")

# >>> NOVO: origem por ID (prioritário) e DEBUG opcional <<<
ORIGEM_CHAT_ID = int(os.getenv("ORIGEM_CHAT_ID", "0"))
DEBUG = os.getenv("DEBUG", "0") == "1"

SOURCE_BOT = os.getenv("ORIGEM_USERNAME", "") or ""
TARGET_CHAT_ID = int(os.getenv("DESTINO_CHAT_ID", "0"))
MODE = os.getenv("MODE", "copy").strip().lower()   # forward ou copy
STRING_SESSION = os.getenv("STRING_SESSION", "").strip()
REPLACE_FROM = (os.getenv("REPLACE_FROM", "") or "").strip()
REPLACE_TO = (os.getenv("REPLACE_TO", "") or "").strip()
NOTIFY_CHAT_ID = int(os.getenv("NOTIFY_CHAT_ID", "0"))  # <= NOVO

if SOURCE_BOT.startswith("@"):
    SOURCE_BOT = SOURCE_BOT[1:]
SOURCE_BOT = SOURCE_BOT.lower()

SESSION_NAME = "forwarder"

# ==============================
# Logging
# ==============================
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/forwarder.log", encoding="utf-8"),
        logging.StreamHandler()
    ],
)

# ==============================
# Inicializar cliente
# ==============================
if STRING_SESSION:
    client = TelegramClient(
        StringSession(STRING_SESSION), API_ID, API_HASH,
        device_model="CornerForward", system_version="Windows 11", app_version="1.40.0"
    )
else:
    client = TelegramClient(
        SESSION_NAME, API_ID, API_HASH,
        device_model="CornerForward", system_version="Windows 11", app_version="1.40.0"
    )

# ==============================
# Helpers de substituição
# ==============================
def _build_replace_patterns() -> list:
    patterns = []
    if not REPLACE_FROM:
        return patterns

    try:
        patterns.append(re.compile(re.escape(REPLACE_FROM), flags=re.IGNORECASE))
    except re.error:
        pass

    m = re.search(r'([a-z0-9.-]+\.[a-z]{2,})', REPLACE_FROM, re.I)
    if m:
        host = re.escape(m.group(1))
        wide = re.compile(rf'(?:https?://)?(?:www\.)?{host}\S*', flags=re.IGNORECASE)
        patterns.append(wide)

    return patterns

_REPLACE_PATTERNS = _build_replace_patterns()

def replace_text(text: str) -> str:
    if not text:
        return ""
    if not REPLACE_TO or not _REPLACE_PATTERNS:
        return text

    new_text = text
    total_hits = 0
    for pat in _REPLACE_PATTERNS:
        new_text, hits = pat.subn(REPLACE_TO, new_text)
        total_hits += hits

    if total_hits > 0:
        logging.info(f"Substituição aplicada ({total_hits} ocorrência(s)).")
    else:
        logging.info("Nenhuma ocorrência encontrada para substituição.")

    return new_text

# ==============================
# EXTRA: Scraping GREEN/RED
# ==============================
def get_match_result(home_team: str, away_team: str):
    """
    Consulta resultado do jogo via scraping (exemplo: SofaScore).
    Retorna tuple (home_goals, away_goals) ou None.
    """
    try:
        url = f"https://www.sofascore.com/pt/search?q={home_team}%20{away_team}"
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        score_tag = soup.find("div", {"class": "result"})
        if not score_tag:
            return None
        score_text = score_tag.text.strip()
        home_goals, away_goals = map(int, score_text.split("-"))
        return home_goals, away_goals
    except Exception as e:
        logging.warning(f"[SCRAPING] Erro ao buscar resultado: {e}")
        return None

def append_result_status(text: str) -> str:
    """
    Analisa o texto, identifica o jogo e acrescenta GREEN ou RED.
    """
    m = re.search(r"Jogo:\s*(.+?)\sx\s*(.+)", text)
    if not m:
        return text
    home_team, away_team = m.groups()
    result = get_match_result(home_team, away_team)
    if not result:
        return text
    home_goals, away_goals = result
    if (home_goals + away_goals) >= 1:
        return text + "\n\n⛳️✅ GREEN"
    else:
        return text + "\n\n✖️ RED"

# ==============================
# Envio + notificação
# ==============================
async def _notify_if_configured(preview_text: str):
    if NOTIFY_CHAT_ID == 0:
        return
    try:
        msg = f"✅ Encaminhado para {TARGET_CHAT_ID}\nPrévia: {preview_text[:200]}"
        await client.send_message(NOTIFY_CHAT_ID, msg, silent=False, link_preview=False)
    except Exception as e:
        logging.warning(f"Falha ao notificar NOTIFY_CHAT_ID: {e}")

# ==============================
# Funções de cópia e forward
# ==============================
async def _copy_single_message(msg):
    text = msg.message or ""
    new_text = replace_text(text) or None
    if new_text:
        new_text = append_result_status(new_text)

    if msg.media:
        await client.send_file(
            TARGET_CHAT_ID,
            msg.media,
            caption=new_text,
            force_document=False,
            silent=False
        )
    else:
        await client.send_message(
            TARGET_CHAT_ID,
            new_text or "",
            link_preview=True,
            silent=False
        )

async def _handle_copy(event):
    if getattr(event, "messages", None):
        files = []
        caption = None
        for m in event.messages:
            if m.media:
                files.append(m.media)
            if not caption and (m.message or "").strip():
                caption = replace_text(m.message)
                if caption:
                    caption = append_result_status(caption)
        if files:
            await client.send_file(
                TARGET_CHAT_ID,
                files,
                caption=caption or None,
                silent=False
            )
        return
    await _copy_single_message(event.message)

async def _handle_forward(event):
    if REPLACE_FROM:
        logging.info("Substituição ativa: usando COPY em vez de FORWARD")
        await _handle_copy(event)
        return
    await event.forward_to(TARGET_CHAT_ID)

async def _process_event(event):
    try:
        effective_mode = MODE
        if MODE == "forward" and REPLACE_FROM:
            effective_mode = "copy"

        if effective_mode == "copy":
            await _handle_copy(event)
        else:
            await _handle_forward(event)

        preview = (event.raw_text or "").replace("\n", " ")[:120]
        logging.info(f"Enviado → {TARGET_CHAT_ID} | Mode={effective_mode} | Preview='{preview}'")
        await _notify_if_configured(preview)

    except FloodWaitError as fw:
        wait = getattr(fw, "seconds", 5)
        logging.warning(f"FloodWait: aguardando {wait}s")
        await asyncio.sleep(wait + 1)
    except Exception as e:
        logging.exception(f"Erro ao enviar: {e}")

# ==============================
# Filtro de origem
# ==============================
async def _is_from_source(event) -> bool:
    try:
        if ORIGEM_CHAT_ID and event.chat_id == ORIGEM_CHAT_ID:
            if DEBUG:
                logging.info(f"[DEBUG] match por ID da origem: {event.chat_id}")
            return True
    except Exception:
        pass

    sender_user = ""
    chat_user = ""
    try:
        sender = await event.get_sender()
        sender_user = (getattr(sender, "username", "") or "").lower()
    except Exception:
        pass

    try:
        chat = await event.get_chat()
        chat_user = (getattr(chat, "username", "") or "").lower()
    except Exception:
        pass

    if DEBUG:
        logging.info(f"[DEBUG] chat_id={getattr(event, 'chat_id', None)} sender={sender_user!r} chat={chat_user!r} want={SOURCE_BOT!r}")

    return (SOURCE_BOT and (sender_user == SOURCE_BOT or chat_user == SOURCE_BOT))

# ==============================
# Handlers
# ==============================
@client.on(events.NewMessage)
async def on_new_message(event):
    if not await _is_from_source(event):
        return
    try:
        sender = await event.get_sender()
        if not getattr(sender, "bot", False):
            return
    except Exception:
        return
    await _process_event(event)

if hasattr(events, "Album"):
    @client.on(events.Album)
    async def on_album(event):
        if not await _is_from_source(event):
            return
        try:
            sender = await event.get_sender()
            if not getattr(sender, "bot", False):
                return
        except Exception:
            return
        await _process_event(event)

# ==============================
# Main
# ==============================
def main():
    logging.info("Forwarder rodando…")
    port = int(os.getenv("PORT", "10000"))
    threading.Thread(target=start_health_server, args=(port,), daemon=True).start()
    client.start()
    client.run_until_disconnected()

if __name__ == "__main__":
    main()
