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
from telethon.tl.types import MessageMediaWebPage

load_dotenv()

def get_int_env(name, default=0):
value = (os.getenv(name, "") or "").strip()

```
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
```

def get_bool_env(name, default=False):
value = (os.getenv(name, "") or "").strip().lower()

```
if value in ("1", "true", "yes", "sim", "on"):
    return True

if value in ("0", "false", "no", "nao", "não", "off"):
    return False

return default
```

def get_int_list_env(name):
raw = (os.getenv(name, "") or "").strip()

```
if not raw:
    return []

values = []

for item in raw.split(","):
    item = item.strip()

    if not item:
        continue

    try:
        values.append(int(item))
    except ValueError:
        logging.warning("ID inválido em %s: %r", name, item)

return values
```

API_ID = get_int_env("API_ID")
API_HASH = (os.getenv("API_HASH", "") or "").strip()
STRING_SESSION = (os.getenv("STRING_SESSION", "") or "").strip()

ORIGEM_CHAT_IDS = get_int_list_env("ORIGEM_CHAT_IDS")
ORIGEM_CHAT_ID = get_int_env("ORIGEM_CHAT_ID")

if ORIGEM_CHAT_ID and ORIGEM_CHAT_ID not in ORIGEM_CHAT_IDS:
ORIGEM_CHAT_IDS.append(ORIGEM_CHAT_ID)

ORIGEM_USERNAME = (os.getenv("ORIGEM_USERNAME", "") or "").strip().lower()

if ORIGEM_USERNAME.startswith("@"):
ORIGEM_USERNAME = ORIGEM_USERNAME[1:]

if ORIGEM_USERNAME in ("0", "none", "null"):
ORIGEM_USERNAME = ""

DESTINO_CHAT_ID = get_int_env("DESTINO_CHAT_ID")

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

NOTIFY_CHAT_ID = get_int_env("NOTIFY_CHAT_ID")
DEBUG = get_bool_env("DEBUG")
LISTAR_CHATS = get_bool_env("LISTAR_CHATS")
REQUIRE_SENDER_BOT = get_bool_env("REQUIRE_SENDER_BOT")
PORT = get_int_env("PORT", 10000)

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
level=logging.INFO,
format="%(asctime)s [%(levelname)s] %(message)s",
handlers=[
logging.FileHandler("logs/forwarder.log", encoding="utf-8"),
logging.StreamHandler(),
],
)

class HealthHandler(BaseHTTPRequestHandler):
def send_ok(self):
self.send_response(200)
self.send_header("Content-Type", "text/plain; charset=utf-8")
self.end_headers()

```
def do_GET(self):
    if self.path in ("/", "/health"):
        self.send_ok()
        self.wfile.write(b"OK")
    else:
        self.send_error(404)

def do_HEAD(self):
    if self.path in ("/", "/health"):
        self.send_ok()
    else:
        self.send_error(404)

def log_message(self, format, *args):
    return
```

def start_health_server():
logging.info("HTTP health em 0.0.0.0:%s", PORT)
HTTPServer(("0.0.0.0", PORT), HealthHandler).serve_forever()

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
"forwarder",
API_ID,
API_HASH,
device_model="BrotherBot",
system_version="Linux",
app_version="1.0.0",
)

def build_replace_patterns():
if not REPLACE_FROM:
return []

```
patterns = [
    re.compile(
        re.escape(REPLACE_FROM),
        flags=re.IGNORECASE,
    )
]

domain = re.search(
    r"([a-z0-9.-]+\.[a-z]{2,})",
    REPLACE_FROM,
    re.IGNORECASE,
)

if domain:
    host = re.escape(domain.group(1))

    patterns.append(
        re.compile(
            rf"(?:https?://)?(?:www\.)?{host}\S*",
            flags=re.IGNORECASE,
        )
    )

return patterns
```

REPLACE_PATTERNS = build_replace_patterns()

def apply_generic_replacements(text):
if not text or not REPLACE_TO or not REPLACE_PATTERNS:
return text or ""

```
result = text
hits_total = 0

for pattern in REPLACE_PATTERNS:
    result, hits = pattern.subn(REPLACE_TO, result)
    hits_total += hits

if hits_total:
    logging.info(
        "Substituição genérica aplicada: %s ocorrência(s).",
        hits_total,
    )

return result
```

def remove_source_links(text):
kept = []

```
for line in (text or "").splitlines():
    if re.search(
        r"https?://(?:www\.)?tevosoares\.com\.br\S*",
        line,
        flags=re.IGNORECASE,
    ):
        continue

    kept.append(line.rstrip())

return "\n".join(kept).strip()
```

def finish_with_cta(text):
result = (text or "").strip()

```
if CTA_TEXT and CTA_TEXT not in result:
    result = f"{result}\n{CTA_TEXT}"

return result.strip()
```

def clean_instruction(instruction):
result = (instruction or "").strip()

```
result = re.sub(
    r";\s*se\s+aluno\(a\).*",
    ".",
    result,
    flags=re.IGNORECASE | re.DOTALL,
).strip()

result = re.sub(r"\s+", " ", result).strip()

if result and result[-1] not in ".!?":
    result += "."

return result
```

def transform_tevo_alert(text):
pattern = re.compile(
r"➡\s*Tevo\s+Soares\s*:\s*"
r"(.*?)(?:\n\s*https?://(?:www.)?tevosoares.com.br\S*)?\s*$",
flags=re.IGNORECASE | re.DOTALL,
)

```
match = pattern.search(text)

if not match:
    return None

instruction = clean_instruction(match.group(1))
base = remove_source_links(pattern.sub("", text).strip())

return finish_with_cta(
    f"{base}\n\n➡ {AUTHOR_LABEL}:  {instruction}"
)
```

def transform_over_gol_alert(text):
if not re.search(
r"Oportunidade\s+para:\s*Over\s+Gol\b",
text,
flags=re.IGNORECASE,
):
return None

```
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
```

def replace_text(text):
if not text:
return ""

```
result = text.replace("\r\n", "\n").replace("\r", "\n").strip()
result = apply_generic_replacements(result)

transformed = transform_tevo_alert(result)

if transformed:
    logging.info(
        "Mensagem editada pelo padrão: Tevo Soares para Brother Bot."
    )
    return transformed

transformed = transform_over_gol_alert(result)

if transformed:
    logging.info("Mensagem editada pelo padrão: Over Gol.")
    return transformed

return result
```

def has_uploadable_media(message):
if not getattr(message, "media", None):
return False

```
return not isinstance(message.media, MessageMediaWebPage)
```

async def list_chats():
logging.info("INICIO_LISTA_CHATS")

```
try:
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        username = (getattr(entity, "username", "") or "").strip()

        logging.info(
            "CHAT id=%s | nome=%r | username=%r | grupo=%s | canal=%s | tipo=%s",
            dialog.id,
            dialog.name or "",
            username,
            bool(getattr(dialog, "is_group", False)),
            bool(getattr(dialog, "is_channel", False)),
            type(entity).__name__,
        )

except Exception as error:
    logging.exception("Erro ao listar chats: %s", error)

logging.info("FIM_LISTA_CHATS")
```

async def notify_if_configured(preview):
if not NOTIFY_CHAT_ID:
return

```
try:
    await client.send_message(
        NOTIFY_CHAT_ID,
        f"Encaminhado para {DESTINO_CHAT_ID}\nPrévia: {preview[:200]}",
        silent=False,
        link_preview=False,
    )

except Exception as error:
    logging.warning("Falha ao notificar: %s", error)
```

async def copy_single_message(message):
new_text = replace_text(message.message or "") or None

```
if has_uploadable_media(message):
    await client.send_file(
        DESTINO_CHAT_ID,
        message.media,
        caption=new_text,
        force_document=False,
        silent=False,
    )
    return

await client.send_message(
    DESTINO_CHAT_ID,
    new_text or "",
    link_preview=True,
    silent=False,
)
```

async def copy_event(event):
if getattr(event, "messages", None):
files = []
caption = None

```
    for message in event.messages:
        if has_uploadable_media(message):
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

    if caption:
        await client.send_message(
            DESTINO_CHAT_ID,
            caption,
            link_preview=True,
            silent=False,
        )

    return

await copy_single_message(event.message)
```

async def forward_event(event):
if REPLACE_FROM:
await copy_event(event)
return

```
await event.forward_to(DESTINO_CHAT_ID)
```

async def process_event(event):
try:
effective_mode = MODE

```
    if MODE == "forward" and REPLACE_FROM:
        effective_mode = "copy"

    if effective_mode == "copy":
        await copy_event(event)
    else:
        await forward_event(event)

    preview = (
        getattr(event, "raw_text", "") or ""
    ).replace("\n", " ")[:120]

    logging.info(
        "Enviado para %s | Origem=%s | Mode=%s | Preview=%r",
        DESTINO_CHAT_ID,
        getattr(event, "chat_id", None),
        effective_mode,
        preview,
    )

    await notify_if_configured(preview)

except FloodWaitError as flood:
    seconds = getattr(flood, "seconds", 5)

    logging.warning(
        "FloodWait: aguardando %ss.",
        seconds,
    )

    await asyncio.sleep(seconds + 1)

except Exception as error:
    logging.exception("Erro ao enviar: %s", error)
```

async def is_from_source(event):
chat_id = getattr(event, "chat_id", None)
sender_username = ""
chat_username = ""

```
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
        "DEBUG chat_id=%s | sender=%r | chat=%r | origens_ids=%s | origem_username=%r",
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
```

async def sender_passes_bot_filter(event):
if not REQUIRE_SENDER_BOT:
return True

```
try:
    sender = await event.get_sender()
    is_bot = bool(getattr(sender, "bot", False))

    if DEBUG:
        logging.info(
            "DEBUG REQUIRE_SENDER_BOT ativo. sender.bot=%s",
            is_bot,
        )

    return is_bot

except Exception as error:
    logging.warning(
        "Não consegui verificar o remetente: %s",
        error,
    )
    return False
```

@client.on(events.NewMessage)
async def on_new_message(event):
if getattr(event.message, "grouped_id", None):
return

```
if not await is_from_source(event):
    return

if not await sender_passes_bot_filter(event):
    return

await process_event(event)
```

if hasattr(events, "Album"):

```
@client.on(events.Album)
async def on_album(event):
    if not await is_from_source(event):
        return

    if not await sender_passes_bot_filter(event):
        return

    await process_event(event)
```

def main():
logging.info("Forwarder rodando.")
logging.info("Origens por ID: %s", ORIGEM_CHAT_IDS)
logging.info(
"Origem por username: %s",
ORIGEM_USERNAME or "(vazio)",
)
logging.info("Destino: %s", DESTINO_CHAT_ID)
logging.info("Mode: %s", MODE)
logging.info("Debug: %s", DEBUG)
logging.info("Listar chats: %s", LISTAR_CHATS)

```
if not API_ID:
    logging.warning("API_ID não configurado.")

if not API_HASH:
    logging.warning("API_HASH não configurado.")

if not STRING_SESSION:
    logging.warning("STRING_SESSION não configurado.")

if not DESTINO_CHAT_ID:
    logging.warning("DESTINO_CHAT_ID não configurado.")

if not ORIGEM_CHAT_IDS and not ORIGEM_USERNAME:
    logging.warning("Nenhuma origem configurada.")

thread = threading.Thread(
    target=start_health_server,
    daemon=True,
)

thread.start()

client.start()

if LISTAR_CHATS:
    client.loop.run_until_complete(list_chats())

client.run_until_disconnected()
```

if **name** == "**main**":
main()
