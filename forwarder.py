import os
import re
import asyncio
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession

==========================================================
Brother Bot - encaminha alertas do Telegram para outro chat
==========================================================

load_dotenv()

==============================
Leitura de variáveis de ambiente
==============================

def get_int_env(name: str, default: int = 0) -> int:
value = (os.getenv(name, "") or "").strip()

if not value:
    return default

try:
    return int(value)
except ValueError:
    logging.warning(
        "Variável %s inválida: %r. Usando %s.",
        name,
        value,
        default,
    )
    return default

def get_bool_env(name: str, default: bool = False) -> bool:
value = (os.getenv(name, "") or "").strip().lower()

if value in ("1", "true", "yes", "sim", "on"):
    return True

if value in ("0", "false", "no", "nao", "não", "off"):
    return False

return default

def parse_int_list_env(name: str) -> list[int]:
raw = (os.getenv(name, "") or "").strip()

if not raw:
    return []

ids = []

for item in raw.split(","):
    item = item.strip()

    if not item:
        continue

    try:
        ids.append(int(item))
    except ValueError:
        logging.warning("ID inválido em %s: %r", name, item)

return ids
==============================
Configurações
==============================

API_ID = get_int_env("API_ID", 0)
API_HASH = (os.getenv("API_HASH", "") or "").strip()
STRING_SESSION = (os.getenv("STRING_SESSION", "") or "").strip()

IDs dos grupos/canais de origem.
Exemplo no Render:
-1001111111111,-1002222222222

ORIGEM_CHAT_IDS = parse_int_list_env("ORIGEM_CHAT_IDS")

Mantido apenas por compatibilidade com configuração antiga.

ORIGEM_CHAT_ID = get_int_env("ORIGEM_CHAT_ID", 0)

if ORIGEM_CHAT_ID and ORIGEM_CHAT_ID not in ORIGEM_CHAT_IDS:
ORIGEM_CHAT_IDS.append(ORIGEM_CHAT_ID)

Fallback opcional por username.
Normalmente deixe vazio no Render.

ORIGEM_USERNAME = (os.getenv("ORIGEM_USERNAME", "") or "").strip().lower()

if ORIGEM_USERNAME.startswith("@"):
ORIGEM_USERNAME = ORIGEM_USERNAME[1:]

DESTINO_CHAT_ID = get_int_env("DESTINO_CHAT_ID", 0)

MODE = (os.getenv("MODE", "copy") or "copy").strip().lower()

if MODE not in ("copy", "forward"):
logging.warning("MODE inválido: %r. Usando copy.", MODE)
MODE = "copy"

REPLACE_FROM = (os.getenv("REPLACE_FROM", "") or "").strip()
REPLACE_TO = (os.getenv("REPLACE_TO", "") or "").strip()

AUTHOR_LABEL = (
os.getenv("AUTHOR_LABEL", "Brother Bot") or "Brother Bot"
).strip()

CTA_TEXT = (
os.getenv(
"CTA_TEXT",
"Entrem no nosso outro canal grátis! 📲 https://t.me/BrotherDosGreens",
)
or ""
).strip()

OVER_GOL_RULE = (
os.getenv("OVER_GOL_RULE", "buscar uma ODD mínima de 1.70") or ""
).strip()

NOTIFY_CHAT_ID = get_int_env("NOTIFY_CHAT_ID", 0)

DEBUG = get_bool_env("DEBUG", False)

Coloque 1 temporariamente para listar todos os grupos/canais nos logs.
Depois de descobrir os IDs, troque novamente para 0.

LISTAR_CHATS = get_bool_env("LISTAR_CHATS", False)

Deixe 0.
Com 1, o bot só aceita mensagens enviadas por contas marcadas como bot.

REQUIRE_SENDER_BOT = get_bool_env("REQUIRE_SENDER_BOT", False)

SESSION_NAME = "forwarder"

HAS_ALBUM_EVENTS = hasattr(events, "Album")

==============================
Logs
==============================

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
level=logging.INFO,
format="%(asctime)s [%(levelname)s] %(message)s",
handlers=[
logging.FileHandler("logs/forwarder.log", encoding="utf-8"),
logging.StreamHandler(),
],
)

==============================
Healthcheck HTTP para Render
==============================

class HealthHandler(BaseHTTPRequestHandler):
def _ok(self) -> None:
self.send_response(200)
self.send_header("Content-Type", "text/plain; charset=utf-8")
self.end_headers()

def do_GET(self) -> None:
    if self.path in ("/", "/health"):
        self._ok()
        self.wfile.write(b"OK")
    else:
        self.send_error(404)

def do_HEAD(self) -> None:
    if self.path in ("/", "/health"):
        self._ok()
    else:
        self.send_error(404)

def log_message(self, *args) -> None:
    return

def start_health_server(port: int) -> None:
logging.info("HTTP health em 0.0.0.0:%s", port)
HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

==============================
Cliente Telegram
==============================

if STRING_SESSION:
client = TelegramClient(
StringSession(STRING_SESSION),
API_ID,
API_HASH,
device_model="BrotherBot",
system_version="Linux",
app_version="1.0.0",
)
else:
client = TelegramClient(
SESSION_NAME,
API_ID,
API_HASH,
device_model="BrotherBot",
system_version="Linux",
app_version="1.0.0",
)

==============================
Listagem temporária dos chats
==============================

async def listar_chats_disponiveis() -> None:
logging.info("========================================")
logging.info("=== INÍCIO DA LISTA DE CHATS ===")
logging.info("========================================")

try:
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        username = (getattr(entity, "username", "") or "").strip()

        logging.info(
            "[CHAT] id=%s | nome=%r | username=%r | grupo=%s | canal=%s | tipo=%s",
            dialog.id,
            dialog.name or "",
            username,
            bool(getattr(dialog, "is_group", False)),
            bool(getattr(dialog, "is_channel", False)),
            type(entity).__name__,
        )

except Exception as error:
    logging.exception("Erro ao listar chats: %s", error)

logging.info("========================================")
logging.info("=== FIM DA LISTA DE CHATS ===")
logging.info("========================================")
==============================
Edição dos textos
==============================

def build_replace_patterns() -> list:
patterns = []

if not REPLACE_FROM:
    return patterns

patterns.append(
    re.compile(
        re.escape(REPLACE_FROM),
        flags=re.IGNORECASE,
    )
)

match = re.search(
    r"([a-z0-9.-]+\.[a-z]{2,})",
    REPLACE_FROM,
    re.IGNORECASE,
)

if match:
    host = re.escape(match.group(1))

    patterns.append(
        re.compile(
            rf"(?:https?://)?(?:www\.)?{host}\S*",
            flags=re.IGNORECASE,
        )
    )

return patterns

REPLACE_PATTERNS = build_replace_patterns()

def apply_generic_replacements(text: str) -> str:
if not text or not REPLACE_TO or not REPLACE_PATTERNS:
return text or ""

new_text = text
total_hits = 0

for pattern in REPLACE_PATTERNS:
    new_text, hits = pattern.subn(REPLACE_TO, new_text)
    total_hits += hits

if total_hits > 0:
    logging.info(
        "Substituição genérica aplicada: %s ocorrência(s).",
        total_hits,
    )

return new_text

def remove_source_links(text: str) -> str:
"""
Remove links de tevosoares.com.br sem apagar o restante da mensagem.
"""
clean_lines = []

for line in (text or "").splitlines():
    if re.search(
        r"https?://(?:www\.)?tevosoares\.com\.br\S*",
        line,
        flags=re.IGNORECASE,
    ):
        continue

    clean_lines.append(line.rstrip())

return "\n".join(clean_lines).strip()

def finish_with_cta(text: str) -> str:
text = (text or "").strip()

if CTA_TEXT and CTA_TEXT not in text:
    text = f"{text}\n{CTA_TEXT}"

return text.strip()

def clean_instruction(instruction: str) -> str:
instruction = (instruction or "").strip()

instruction = re.sub(
    r";\s*se\s+aluno\(a\).*",
    ".",
    instruction,
    flags=re.IGNORECASE | re.DOTALL,
).strip()

instruction = re.sub(r"\s+", " ", instruction).strip()

if instruction and instruction[-1] not in ".!?":
    instruction += "."

return instruction

def transform_tevo_alert(text: str) -> str | None:
"""
Troca a assinatura Tevo Soares por Brother Bot
e remove o link da origem.
"""
pattern = re.compile(
r"➡\sTevo\s+Soares\s:\s*"
r"(.?)(?:\n\shttps?://(?.)?tevosoares.com.br\S*)?\s*$",
flags=re.IGNORECASE | re.DOTALL,
)

match = pattern.search(text)

if not match:
    return None

instruction = clean_instruction(match.group(1))

base = pattern.sub("", text).strip()
base = remove_source_links(base)

return finish_with_cta(
    f"{base}\n\n➡ {AUTHOR_LABEL}:  {instruction}"
)

def transform_over_gol_alert(text: str) -> str | None:
"""
Coloca a regra padrão nos alertas Over Gol
que ainda não tenham uma assinatura Brother Bot.
"""
if not re.search(
r"Oportunidade\s+para:\s*Over\s+Gol\b",
text,
flags=re.IGNORECASE,
):
return None

if re.search(
    rf"➡\s*{re.escape(AUTHOR_LABEL)}\s*:",
    text,
    flags=re.IGNORECASE,
):
    return text

base = remove_source_links(text)

return finish_with_cta(
    f"{base}\n➡ {AUTHOR_LABEL}:  {OVER_GOL_RULE}"
)

def replace_text(text: str) -> str:
if not text:
return ""

text = text.replace("\r\n", "\n").replace("\r", "\n").strip()

text = apply_generic_replacements(text)

transformed = transform_tevo_alert(text)

if transformed:
    logging.info(
        "Mensagem editada pelo padrão: Tevo Soares → Brother Bot."
    )
    return transformed

transformed = transform_over_gol_alert(text)

if transformed:
    logging.info("Mensagem editada pelo padrão: Over Gol.")
    return transformed

return text
==============================
Notificação opcional
==============================

async def notify_if_configured(preview_text: str) -> None:
if NOTIFY_CHAT_ID == 0:
return

try:
    await client.send_message(
        NOTIFY_CHAT_ID,
        f"✅ Encaminhado para {DESTINO_CHAT_ID}\n"
        f"Prévia: {preview_text[:200]}",
        silent=False,
        link_preview=False,
    )

except Exception as error:
    logging.warning(
        "Falha ao notificar NOTIFY_CHAT_ID: %s",
        error,
    )
==============================
Envio de mensagem
==============================

async def copy_single_message(message) -> None:
original_text = message.message or ""
new_text = replace_text(original_text) or None

if message.media:
    await client.send_file(
        DESTINO_CHAT_ID,
        message.media,
        caption=new_text,
        force_document=False,
        silent=False,
    )
else:
    await client.send_message(
        DESTINO_CHAT_ID,
        new_text or "",
        link_preview=True,
        silent=False,
    )

async def handle_copy(event) -> None:
"""
Encaminha texto, foto, vídeo ou álbum.
"""
if getattr(event, "messages", None):
files = []
caption = None

    for message in event.messages:
        if message.media:
            files.append(message.media)

        if caption is None and (message.message or "").strip():
            caption = replace_text(message.message)

    if files:
        await client.send_file(
            DESTINO_CHAT_ID,
            files,
            caption=caption or None,
            silent=False,
        )

    return

await copy_single_message(event.message)

async def handle_forward(event) -> None:
"""
Encaminha sem editar.
Caso REPLACE_FROM esteja preenchido, usa cópia para permitir a edição.
"""
if REPLACE_FROM:
logging.info(
"Substituição ativa: usando COPY em vez de FORWARD."
)
await handle_copy(event)
return

await event.forward_to(DESTINO_CHAT_ID)

async def process_event(event) -> None:
try:
effective_mode = MODE

    if MODE == "forward" and REPLACE_FROM:
        effective_mode = "copy"

    if effective_mode == "copy":
        await handle_copy(event)
    else:
        await handle_forward(event)

    preview = (
        getattr(event, "raw_text", "") or ""
    ).replace("\n", " ")[:120]

    logging.info(
        "Enviado → %s | Origem=%s | Mode=%s | Preview=%r",
        DESTINO_CHAT_ID,
        getattr(event, "chat_id", None),
        effective_mode,
        preview,
    )

    await notify_if_configured(preview)

except FloodWaitError as flood:
    wait_seconds = getattr(flood, "seconds", 5)

    logging.warning(
        "FloodWait: aguardando %ss.",
        wait_seconds,
    )

    await asyncio.sleep(wait_seconds + 1)

except Exception as error:
    logging.exception("Erro ao enviar: %s", error)
==============================
Filtro dos grupos de origem
==============================

async def is_from_source(event) -> bool:
chat_id = getattr(event, "chat_id", None)

sender_username = ""
chat_username = ""

try:
    sender = await event.get_sender()
    sender_username = (
        getattr(sender, "username", "") or ""
    ).lower()
except Exception:
    pass

try:
    chat = await event.get_chat()
    chat_username = (
        getattr(chat, "username", "") or ""
    ).lower()
except Exception:
    pass

if DEBUG:
    logging.info(
        "[DEBUG] chat_id=%s | sender=%r | chat=%r | "
        "origens_ids=%s | origem_username=%r",
        chat_id,
        sender_username,
        chat_username,
        ORIGEM_CHAT_IDS,
        ORIGEM_USERNAME,
    )

if ORIGEM_CHAT_IDS and chat_id in ORIGEM_CHAT_IDS:
    return True

if ORIGEM_USERNAME and (
    sender_username == ORIGEM_USERNAME
    or chat_username == ORIGEM_USERNAME
):
    return True

return False

async def sender_passes_bot_filter(event) -> bool:
if not REQUIRE_SENDER_BOT:
return True

try:
    sender = await event.get_sender()
    is_bot = bool(getattr(sender, "bot", False))

    if DEBUG:
        logging.info(
            "[DEBUG] REQUIRE_SENDER_BOT ativo. sender.bot=%s",
            is_bot,
        )

    return is_bot

except Exception as error:
    logging.warning(
        "Não consegui verificar se remetente é bot: %s",
        error,
    )
    return False
==============================
Eventos do Telegram
==============================

@client.on(events.NewMessage)
async def on_new_message(event) -> None:
"""
Mensagens de álbum também disparam NewMessage.
Elas serão tratadas apenas pelo handler de Album abaixo.
"""
if HAS_ALBUM_EVENTS and getattr(event.message, "grouped_id", None):
return

if not await is_from_source(event):
    return

if not await sender_passes_bot_filter(event):
    return

await process_event(event)

if HAS_ALBUM_EVENTS:

@client.on(events.Album)
async def on_album(event) -> None:
    if not await is_from_source(event):
        return

    if not await sender_passes_bot_filter(event):
        return

    await process_event(event)
==============================
Inicialização
==============================

def main() -> None:
logging.info("Forwarder rodando…")

if not API_ID:
    logging.warning("API_ID não configurado.")

if not API_HASH:
    logging.warning("API_HASH não configurado.")

if not STRING_SESSION:
    logging.warning(
        "STRING_SESSION não configurado. "
        "No Render, normalmente ela é necessária."
    )

if not DESTINO_CHAT_ID:
    logging.warning("DESTINO_CHAT_ID não configurado.")

if not ORIGEM_CHAT_IDS and not ORIGEM_USERNAME:
    logging.warning(
        "Nenhuma origem configurada. "
        "Configure ORIGEM_CHAT_IDS ou ORIGEM_USERNAME."
    )

logging.info("Origens por ID: %s", ORIGEM_CHAT_IDS)
logging.info(
    "Origem por username: %s",
    ORIGEM_USERNAME or "(vazio)",
)
logging.info("Destino: %s", DESTINO_CHAT_ID)
logging.info("Mode: %s", MODE)
logging.info(
    "Require sender bot: %s",
    REQUIRE_SENDER_BOT,
)
logging.info("Debug: %s", DEBUG)
logging.info("Listar chats: %s", LISTAR_CHATS)

port = get_int_env("PORT", 10000)

threading.Thread(
    target=start_health_server,
    args=(port,),
    daemon=True,
).start()

client.start()

if LISTAR_CHATS:
    client.loop.run_until_complete(
        listar_chats_disponiveis()
    )

client.run_until_disconnected()

if name == "main":
main()
