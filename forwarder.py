import os
import re
import asyncio
import logging
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
import requests  # Para consultar o resultado da aposta

# (A) --- IMPORTS do health server (NOVO) ---
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
# -------------------------------------------

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

ORIGEM_CHAT_ID = int(os.getenv("ORIGEM_CHAT_ID", "0"))
DEBUG = os.getenv("DEBUG", "0") == "1"

SOURCE_BOT = os.getenv("ORIGEM_USERNAME", "") or ""
TARGET_CHAT_ID = int(os.getenv("DESTINO_CHAT_ID", "0"))
MODE = os.getenv("MODE", "copy").strip().lower()
STRING_SESSION = os.getenv("STRING_SESSION", "").strip()
REPLACE_FROM = (os.getenv("REPLACE_FROM", "") or "").strip()
REPLACE_TO = (os.getenv("REPLACE_TO", "") or "").strip()
NOTIFY_CHAT_ID = int(os.getenv("NOTIFY_CHAT_ID", "0"))

# Normalizar username
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
    handlers=[logging.FileHandler("logs/forwarder.log", encoding="utf-8"),
              logging.StreamHandler()])

# ==============================
# Inicializar cliente
# ==============================
if STRING_SESSION:
    client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH,
                            device_model="CornerForward", system_version="Windows 11", app_version="1.40.0")
else:
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH,
                            device_model="CornerForward", system_version="Windows 11", app_version="1.40.0")

# ==============================
# Função para consultar o resultado da aposta
# ==============================
def get_bet_result(jogo, tipo_aposta, tempo):
    """
    Função para consultar o resultado da aposta.
    'jogo' é o nome do jogo, 'tipo_aposta' pode ser 'escanteio' ou 'gol',
    e 'tempo' é o tempo restante ou de intervalo.
    """
    try:
        # Lógica para consultar a aposta (exemplo fictício)
        # Aqui você precisa colocar a lógica para consultar o jogo real e seus dados
        url = f'https://exemplo.com/api/resultados-apostas?jogo={jogo}'
        response = requests.get(url)
        
        if response.status_code == 200:
            data = response.json()
            if tipo_aposta == "escanteio":
                escanteios = data.get('escanteios')  # Exemplo de dados recebidos
                if escanteios > 5:  # Exemplo de condição para aposta "green"
                    return "green"
            elif tipo_aposta == "gol":
                gols = data.get('gols')  # Exemplo de dados de gols
                if gols >= 1:  # Se sair pelo menos 1 gol
                    return "green"
    except Exception as e:
        logging.error(f"Erro ao consultar o resultado da aposta: {e}")
    
    return "red"  # Caso a aposta não tenha sido "green"

# ==============================
# Envio + notificação
# ==============================
async def _notify_if_configured(preview_text: str):
    """Envia alerta pra você após publicar no canal, se NOTIFY_CHAT_ID estiver setado."""
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
async def _handle_copy(event):
    if getattr(event, "messages", None):
        files = []
        caption = None
        for m in event.messages:
            if m.media:
                files.append(m.media)
            if not caption and (m.message or "").strip():
                caption = replace_text(m.message)
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

        # =======================
        # Analisando e Editando a Mensagem
        # =======================
        if event.message and event.message.text:
            original_message = event.message.text
            message_id = event.message.id
            chat_id = event.chat_id  # ID do chat/canal
            
            # Extract game data from the message (using regex or another method)
            jogo = "Zawisza Bydgoszcz"  # For example, extracted from the message
            tipo_aposta = "gol"  # Or "escanteio", depending on the strategy in the message
            tempo = 30  # Extracted from message like "30 '"

            # Verifica o status da aposta
            resultado = get_bet_result(jogo, tipo_aposta, tempo)
            
            # Edita a mensagem com o status "green" ou "red"
            edited_message = f"{original_message} - Resultado: {resultado}"
            await client.edit_message(chat_id, message_id, edited_message)
            logging.info(f"Mensagem editada com o resultado: {edited_message}")

    except FloodWaitError as fw:
        wait = getattr(fw, "seconds", 5)
        logging.warning(f"FloodWait: aguardando {wait}s")
        await asyncio.sleep(wait + 1)
    except Exception as e:
        logging.exception(f"Erro ao enviar: {e}")

# ==============================
# Filtro de origem (PRIORIDADE: ID do chat)
# ==============================
async def _is_from_source(event) -> bool:
    try:
        if ORIGEM_CHAT_ID and event.chat_id == ORIGEM_CHAT_ID:
            return True
    except Exception:
        pass

    return False

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
