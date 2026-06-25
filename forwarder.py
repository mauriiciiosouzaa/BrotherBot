```python
import os
import re
import asyncio
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError


# ==============================
# Carregar variáveis do ambiente
# ==============================
load_dotenv()


# ==============================
# Helpers de ambiente
# ==============================
def get_int_env(name: str, default: int = 0) -> int:
    value = (os.getenv(name, "") or "").strip()

    if not value:
        return default

    try:
        return int(value)
    except ValueError:
        logging.warning(f"Variável {name} inválida: {value!r}. Usando {default}.")
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
            logging.warning(f"ID inválido em {name}: {item!r}")

    return ids


# ==============================
# Variáveis principais
# ==============================
API_ID = get_int_env("API_ID", 0)
API_HASH = (os.getenv("API_HASH", "") or "").strip()
STRING_SESSION = (os.getenv("STRING_SESSION", "") or "").strip()

# Vários grupos/canais de origem, separados por vírgula
ORIGEM_CHAT_IDS = parse_int_list_env("ORIGEM_CHAT_IDS")

# Compatibilidade com a variável antiga
ORIGEM_CHAT_ID = get_int_env("ORIGEM_CHAT_ID", 0)

if ORIGEM_CHAT_ID and ORIGEM_CHAT_ID not in ORIGEM_CHAT_IDS:
    ORIGEM_CHAT_IDS.append(ORIGEM_CHAT_ID)

# Fallback opcional por username
ORIGEM_USERNAME = (os.getenv("ORIGEM_USERNAME", "") or "").strip().lower()

if ORIGEM_USERNAME.startswith("@"):
    ORIGEM_USERNAME = ORIGEM_USERNAME[1:]

DESTINO_CHAT_ID = get_int_env("DESTINO_CHAT_ID", 0)

MODE = (os.getenv("MODE", "copy") or "copy").strip().lower()
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

# Quando ativado, lista nos logs todos os chats que sua conta pode acessar.
# Use temporariamente para descobrir os IDs corretos dos grupos de origem.
LISTAR_CHATS = get_bool_env("LISTAR_CHATS", False)

# Mantenha como 0/FALSE, a menos que tenha certeza de que o remetente é bot.
REQUIRE_SENDER_BOT = get_bool_env("REQUIRE_SENDER_BOT", False)

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
        logging.StreamHandler(),
    ],
)


# ==============================
# Healthcheck HTTP para Render
# ==============================
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


# ==============================
# Inicializar cliente Telegram
# ==============================
if STRING_SESSION:
    client = TelegramClient(
        StringSession(STRING_SESSION),
        API_ID,
        API_HASH,
        device_model="BrotherBot",
        system_version="Windows 11",
        app_version="1.40.0",
    )
else:
    client = TelegramClient(
        SESSION_NAME,
        API_ID,
        API_HASH,
        device_model="BrotherBot",
        system_version="Windows 11",
        app_version="1.40.0",
    )


# ==============================
# Listagem temporária de chats
# ==============================
async def listar_chats_disponiveis():
    logging.info("========================================")
    logging.info("=== INÍCIO DA LISTA DE CHATS ===")
    logging.info("========================================")

    try:
        async for dialog in client.iter_dialogs():
            entity = dialog.entity

            chat_id = dialog.id
            nome = dialog.name or ""
            username = (getattr(entity, "username", "") or "").strip()

            is_group = bool(getattr(dialog, "is_group", False))
            is_channel = bool(getattr(dialog, "is_channel", False))

            logging.info(
                "[CHAT] id=%s | nome=%r | username=%r | grupo=%s | canal=%s | tipo=%s",
                chat_id,
                nome,
                username,
                is_group,
                is_channel,
                type(entity).__name__,
            )

    except Exception as error:
        logging.exception(f"Erro ao listar chats: {error}")

    logging.info("========================================")
    logging.info("=== FIM DA LISTA DE CHATS ===")
    logging.info("========================================")


# ==============================
# Substituição / edição de texto
# ==============================
def _build_replace_patterns() -> list:
    patterns = []

    if not REPLACE_FROM:
        return patterns

    try:
        patterns.append(re.compile(re.escape(REPLACE_FROM), flags=re.IGNORECASE))
    except re.error:
        pass

    match = re.search(r"([a-z0-9.-]+\.[a-z]{2,})", REPLACE_FROM, re.I)

    if match:
        host = re.escape(match.group(1))

        wide = re.compile(
            rf"(?:https?://)?(?:www\.)?{host}\S*",
            flags=re.IGNORECASE,
        )

        patterns.append(wide)

    return patterns


_REPLACE_PATTERNS = _build_replace_patterns()


def _apply_generic_replacements(text: str) -> str:
    """
    Mantém a substituição antiga REPLACE_FROM -> REPLACE_TO,
    caso você ainda queira esconder algum domínio/nome específico.
    """
    if not text or not REPLACE_TO or not _REPLACE_PATTERNS:
        return text or ""

    new_text = text
    total_hits = 0

    for pattern in _REPLACE_PATTERNS:
        new_text, hits = pattern.subn(REPLACE_TO, new_text)
        total_hits += hits

    if total_hits > 0:
        logging.info(
            f"Substituição genérica aplicada: {total_hits} ocorrência(s)."
        )

    return new_text


def _remove_extra_source_links(text: str) -> str:
    """
    Remove links da origem que você não quer divulgar no seu grupo.
    Atualmente remove links contendo tevosoares.com.br.
    """
    lines = []

    for line in (text or "").splitlines():
        if re.search(
            r"https?://(?:www\.)?tevosoares\.com\.br\S*",
            line,
            re.I,
        ):
            continue

        lines.append(line.rstrip())

    return "\n".join(lines).strip()


def _finish_with_cta(text: str) -> str:
    """
    Adiciona o convite do seu canal no final, sem duplicar.
    """
    text = (text or "").strip()

    if CTA_TEXT and CTA_TEXT not in text:
        text = f"{text}\n{CTA_TEXT}"

    return text.strip()


def _clean_instruction(instruction: str) -> str:
    """
    Limpa a instrução original.
    Exemplo:
    'entrar...; abortar...; se aluno(a)...'
    vira:
    'entrar...; abortar....'
    """
    instruction = (instruction or "").strip()

    instruction = re.sub(
        r";\s*se\s+aluno\(a\).*",
        ".",
        instruction,
        flags=re.I | re.S,
    ).strip()

    instruction = re.sub(r"\s+", " ", instruction).strip()

    if instruction and instruction[-1] not in ".!?":
        instruction += "."

    return instruction


def _transform_tevo_alert(text: str) -> str | None:
    """
    Padrão 1:
    Troca '➡ Tevo Soares:' por '➡ Brother Bot:',
    remove curso/método e remove link tevosoares.com.br.
    """
    pattern = re.compile(
        r"➡\s*Tevo\s+Soares\s*:\s*"
        r"(.*?)(?:\n\s*https?://(?:www\.)?tevosoares\.com\.br\S*)?\s*$",
        flags=re.I | re.S,
    )

    match = pattern.search(text)

    if not match:
        return None

    instruction = _clean_instruction(match.group(1))

    base = pattern.sub("", text).strip()
    base = _remove_extra_source_links(base)

    return _finish_with_cta(
        f"{base}\n\n➡ {AUTHOR_LABEL}:  {instruction}"
    )


def _transform_over_gol_alert(text: str) -> str | None:
    """
    Padrão 2:
    Quando for Over Gol e não tiver instrução,
    adiciona a regra padrão.
    """
    if not re.search(
        r"Oportunidade\s+para:\s*Over\s+Gol\b",
        text,
        flags=re.I,
    ):
        return None

    # Evita duplicar a edição caso a mensagem já tenha sido tratada.
    if re.search(
        rf"➡\s*{re.escape(AUTHOR_LABEL)}\s*:",
        text,
        flags=re.I,
    ):
        return text

    base = _remove_extra_source_links(text)

    return _finish_with_cta(
        f"{base}\n➡ {AUTHOR_LABEL}:  {OVER_GOL_RULE}"
    )


def replace_text(text: str) -> str:
    """
    Função principal chamada antes de enviar ao grupo destino.
    """
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()

    # Primeiro aplica a substituição genérica, se estiver configurada.
    text = _apply_generic_replacements(text)

    # Depois tenta transformar alertas que contêm Tevo Soares.
    transformed = _transform_tevo_alert(text)

    if transformed:
        logging.info(
            "Mensagem editada pelo padrão: Tevo Soares → Brother Bot."
        )
        return transformed

    # Depois tenta transformar alertas Over Gol.
    transformed = _transform_over_gol_alert(text)

    if transformed:
        logging.info("Mensagem editada pelo padrão: Over Gol.")
        return transformed

    # Caso não reconheça um padrão, envia como veio.
    return text


# ==============================
# Notificação opcional
# ==============================
async def _notify_if_configured(preview_text: str):
    if NOTIFY_CHAT_ID == 0:
        return

    try:
        msg = (
            f"✅ Encaminhado para {DESTINO_CHAT_ID}\n"
            f"Prévia: {preview_text[:200]}"
        )

        await client.send_message(
            NOTIFY_CHAT_ID,
            msg,
            silent=False,
            link_preview=False,
        )

    except Exception as error:
        logging.warning(
            f"Falha ao notificar NOTIFY_CHAT_ID: {error}"
        )


# ==============================
# Envio
# ==============================
async def _copy_single_message(msg):
    text = msg.message or ""
    new_text = replace_text(text) or None

    if msg.media:
        await client.send_file(
            DESTINO_CHAT_ID,
            msg.media,
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


async def _handle_copy(event):
    # Álbuns com várias mídias.
    if getattr(event, "messages", None):
        files = []
        caption = None

        for message in event.messages:
            if message.media:
                files.append(message.media)

            if not caption and (message.message or "").strip():
                caption = replace_text(message.message)

        if files:
            await client.send_file(
                DESTINO_CHAT_ID,
                files,
                caption=caption or None,
                silent=False,
            )

        return

    await _copy_single_message(event.message)


async def _handle_forward(event):
    # Se houver substituição ativa, precisa copiar em vez de encaminhar.
    if REPLACE_FROM:
        logging.info(
            "Substituição ativa: usando COPY em vez de FORWARD."
        )
        await _handle_copy(event)
        return

    await event.forward_to(DESTINO_CHAT_ID)


async def _process_event(event):
    try:
        effective_mode = MODE

        if MODE == "forward" and REPLACE_FROM:
            effective_mode = "copy"

        if effective_mode == "copy":
            await _handle_copy(event)
        else:
            await _handle_forward(event)

        preview = (
            getattr(event, "raw_text", "") or ""
        ).replace("\n", " ")[:120]

        logging.info(
            f"Enviado → {DESTINO_CHAT_ID} | "
            f"Origem={getattr(event, 'chat_id', None)} | "
            f"Mode={effective_mode} | "
            f"Preview={preview!r}"
        )

        await _notify_if_configured(preview)

    except FloodWaitError as flood:
        wait = getattr(flood, "seconds", 5)

        logging.warning(f"FloodWait: aguardando {wait}s.")

        await asyncio.sleep(wait + 1)

    except Exception as error:
        logging.exception(f"Erro ao enviar: {error}")


# ==============================
# Filtro de origem
# ==============================
async def _is_from_source(event) -> bool:
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
            f"[DEBUG] chat_id={chat_id} | "
            f"sender={sender_username!r} | "
            f"chat={chat_username!r} | "
            f"origens_ids={ORIGEM_CHAT_IDS} | "
            f"origem_username={ORIGEM_USERNAME!r}"
        )

    # Prioridade: IDs configurados dos grupos/canais.
    if ORIGEM_CHAT_IDS and chat_id in ORIGEM_CHAT_IDS:
        return True

    # Fallback opcional por username.
    if ORIGEM_USERNAME and (
        sender_username == ORIGEM_USERNAME
        or chat_username == ORIGEM_USERNAME
    ):
        return True

    return False


async def _sender_passes_bot_filter(event) -> bool:
    if not REQUIRE_SENDER_BOT:
        return True

    try:
        sender = await event.get_sender()
        is_bot = bool(getattr(sender, "bot", False))

        if DEBUG:
            logging.info(
                f"[DEBUG] REQUIRE_SENDER_BOT ativo. sender.bot={is_bot}"
            )

        return is_bot

    except Exception as error:
        logging.warning(
            f"Não consegui verificar se remetente é bot: {error}"
        )
        return False


# ==============================
# Handlers
# ==============================
@client.on(events.NewMessage)
async def on_new_message(event):
    if not await _is_from_source(event):
        return

    if not await _sender_passes_bot_filter(event):
        return

    await _process_event(event)


if hasattr(events, "Album"):

    @client.on(events.Album)
    async def on_album(event):
        if not await _is_from_source(event):
            return

        if not await _sender_passes_bot_filter(event):
            return

        await _process_event(event)


# ==============================
# Main
# ==============================
def main():
    logging.info("Forwarder rodando…")

    if not API_ID:
        logging.warning("API_ID não configurado.")

    if not API_HASH:
        logging.warning("API_HASH não configurado.")

    if not STRING_SESSION:
        logging.warning(
            "STRING_SESSION não configurado. "
            "No Render, normalmente precisa dele."
        )

    if not DESTINO_CHAT_ID:
        logging.warning("DESTINO_CHAT_ID não configurado.")

    if not ORIGEM_CHAT_IDS and not ORIGEM_USERNAME:
        logging.warning(
            "Nenhuma origem configurada. "
            "Configure ORIGEM_CHAT_IDS ou ORIGEM_USERNAME."
        )

    logging.info(f"Origens por ID: {ORIGEM_CHAT_IDS}")
    logging.info(
        f"Origem por username: {ORIGEM_USERNAME or '(vazio)'}"
    )
    logging.info(f"Destino: {DESTINO_CHAT_ID}")
    logging.info(f"Mode: {MODE}")
    logging.info(f"Require sender bot: {REQUIRE_SENDER_BOT}")
    logging.info(f"Debug: {DEBUG}")
    logging.info(f"Listar chats: {LISTAR_CHATS}")

    port = get_int_env("PORT", 10000)

    threading.Thread(
        target=start_health_server,
        args=(port,),
        daemon=True,
    ).start()

    client.start()

    # Quando LISTAR_CHATS=1, mostra todos os chats uma vez no log.
    if LISTAR_CHATS:
        client.loop.run_until_complete(listar_chats_disponiveis())

    # Mantém o robô online, aguardando novas mensagens.
    client.run_until_disconnected()


if __name__ == "__main__":
    main()
```
