# forwarder.py
import os
import re
import asyncio
import logging
import sqlite3
import time
import html
import threading

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError

# health server
from http.server import BaseHTTPRequestHandler, HTTPServer

# scraping
import aiohttp
from bs4 import BeautifulSoup

# -----------------------------
# Load env
# -----------------------------
load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
STRING_SESSION = os.getenv("STRING_SESSION", "").strip()

ORIGEM_CHAT_ID = int(os.getenv("ORIGEM_CHAT_ID", "0"))
ORIGEM_USERNAME = (os.getenv("ORIGEM_USERNAME", "") or "").lstrip("@").strip().lower()
DEBUG = os.getenv("DEBUG", "0") == "1"

TARGET_CHAT_ID = int(os.getenv("DESTINO_CHAT_ID", "0"))
MODE = os.getenv("MODE", "copy").strip().lower()   # 'forward' or 'copy'

REPLACE_FROM = (os.getenv("REPLACE_FROM", "") or "").strip()
REPLACE_TO = (os.getenv("REPLACE_TO", "") or "").strip()

NOTIFY_CHAT_ID = int(os.getenv("NOTIFY_CHAT_ID", "0"))

PORT = int(os.getenv("PORT", "10000"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "90"))  # secs between scrapes
DB_PATH = os.getenv("DB_PATH", "forwarder_games.db")

# Rule config (string, adjustable). Default behaviour (heuristic)
# Examples:
# RULE=auto  (use automatic detection based on parsed prediction in the message)
RULE = os.getenv("RULE", "auto").strip().lower()

# normalize
if ORIGEM_USERNAME.startswith("@"):
    ORIGEM_USERNAME = ORIGEM_USERNAME[1:]
ORIGEM_USERNAME = ORIGEM_USERNAME.lower()

# -----------------------------
# Logging
# -----------------------------
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/forwarder.log", encoding="utf-8"),
        logging.StreamHandler()
    ],
)

# -----------------------------
# Health server
# -----------------------------
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

# -----------------------------
# Telethon client init
# -----------------------------
SESSION_NAME = "forwarder"
if STRING_SESSION:
    client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH,
                            device_model="CornerForward", system_version="Windows 11", app_version="1.40.0")
else:
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH,
                            device_model="CornerForward", system_version="Windows 11", app_version="1.40.0")

# -----------------------------
# Replacement helpers
# -----------------------------
CORNER_RE = None
if REPLACE_FROM:
    try:
        CORNER_RE = re.compile(re.escape(REPLACE_FROM), flags=re.IGNORECASE)
    except re.error:
        CORNER_RE = None

def replace_text(text: str) -> str:
    if not text:
        return ""
    if not REPLACE_TO or not CORNER_RE:
        return text
    new_text, hits = CORNER_RE.subn(REPLACE_TO, text)
    if hits:
        logging.info(f"Substituição aplicada ({hits} ocorrências).")
    return new_text

# -----------------------------
# SQLite tracking
# -----------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tracked_games (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dest_chat_id INTEGER NOT NULL,
        dest_msg_id INTEGER NOT NULL,
        source_url TEXT,
        home TEXT,
        away TEXT,
        prediction TEXT,
        status TEXT DEFAULT 'pending',  -- pending, green, red, cancelled
        last_checked INTEGER,
        note TEXT
    )
    """)
    conn.commit()
    conn.close()

def add_tracked(dest_chat_id:int, dest_msg_id:int, source_url:str=None, home:str=None, away:str=None, prediction:str=None, note:str=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
      INSERT INTO tracked_games (dest_chat_id, dest_msg_id, source_url, home, away, prediction, last_checked, note)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (dest_chat_id, dest_msg_id, source_url, home, away, prediction, int(time.time()), note))
    conn.commit()
    conn.close()
    logging.info(f"Rastreado: dest_msg_id={dest_msg_id} url={source_url} {home} x {away} pred={prediction}")

def mark_status(game_id:int, status:str, note:str=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE tracked_games SET status=?, note=?, last_checked=? WHERE id=?", (status, note or "", int(time.time()), game_id))
    conn.commit()
    conn.close()

# -----------------------------
# Scraping helpers
# -----------------------------
_SCORE_RE = re.compile(r'(\d{1,2})\s*[x:]\s*(\d{1,2})')
_CORNERS_RE = re.compile(r'corner[s]?\D*(\d{1,2})', re.I)

async def fetch_page_text(session, url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9"
    }
    try:
        async with session.get(url, headers=headers, timeout=20) as resp:
            text = await resp.text()
            return text
    except Exception as e:
        logging.warning(f"Fetch fail {url}: {e}")
        return ""

async def parse_score_from_text(text):
    if not text:
        return None
    # 1) regex basic "digit x digit"
    m = _SCORE_RE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    # 2) attempt find "homeScore" / "awayScore" json-like
    m2 = re.search(r'home.*?score.*?[:=]\s*"?(\d+)"?.*?away.*?score.*?[:=]\s*"?(\d+)"?', text, re.I | re.S)
    if m2:
        return int(m2.group(1)), int(m2.group(2))
    return None

async def parse_corners_from_text(text):
    if not text:
        return None
    # look for patterns: "Corners: Home 4 - Away 3" or "home corners 4 away corners 3"
    # try find two numbers nearby 'corner'
    m_all = re.findall(r'(\d{1,2})', text)
    # heuristic: find phrases with 'corner'
    m = re.search(r'(?:corners|corner).*?(\d{1,2}).*?(\d{1,2})', text, re.I | re.S)
    if m:
        return int(m.group(1)), int(m.group(2))
    # fallback: find first 'corner' occurrence and number near it
    m2 = _CORNERS_RE.search(text)
    if m2:
        try:
            return (int(m2.group(1)), 0)
        except:
            return None
    return None

# -----------------------------
# Prediction parser (from the forwarded message text)
# -----------------------------
# tries to detect type of prediction (over corners N, winner HOME/AWAY/DRAW, total goals, etc.)
def parse_prediction_from_text(text: str):
    if not text:
        return None
    t = text.lower()
    # try to find "over" patterns (e.g., over 3.5 corners, over 2.5)
    m = re.search(r'over\s+([0-9]+(?:\.[05])?)\s*(corners|escanteios|gols|goals)?', t)
    if m:
        val = m.group(1)
        unit = m.group(2) or "corners"
        return {"type": "over", "value": float(val), "unit": unit}
    # "under" patterns
    m = re.search(r'under\s+([0-9]+(?:\.[05])?)\s*(corners|escanteios|gols|goals)?', t)
    if m:
        val = m.group(1)
        unit = m.group(2) or "corners"
        return {"type": "under", "value": float(val), "unit": unit}
    # winner prediction: "vitória X", "home win", "1x2: 1", or "team A x team B" and "pick: TeamA"
    m = re.search(r'pick[:\-]?\s*([^\n\r/]+)', text, re.I)
    if m:
        pick = m.group(1).strip()
        return {"type": "pick", "value": pick}
    # fallback: try detect "escanteio" mention + number
    m = re.search(r'escanteio[s]?\s*(\d+)', t)
    if m:
        return {"type": "corners_exact", "value": int(m.group(1))}
    return None

# -----------------------------
# Decide green/red based on parsed prediction and scraped data
# -----------------------------
async def decide_outcome(prediction, score_tuple, corners_tuple):
    """
    prediction: dict from parse_prediction_from_text
    score_tuple: (home_goals, away_goals) or None
    corners_tuple: (home_corners, away_corners) or None
    returns: "green" or "red" or None (if undecided)
    """
    if not prediction:
        # default fallback: if match finished (we have score), mark green if any goal (example)
        if score_tuple:
            if score_tuple[0] + score_tuple[1] >= 1:
                return "green"
            else:
                return "red"
        return None

    ptype = prediction.get("type")
    if ptype == "over" and prediction.get("unit") and "corner" in prediction.get("unit"):
        # we need total corners
        if corners_tuple:
            total = corners_tuple[0] + corners_tuple[1]
            # compare with threshold (float)
            thr = float(prediction.get("value", 0))
            # consider .5 thresholds: if total >= thr => green (for over)
            if total >= thr:
                return "green"
            else:
                return "red"
        else:
            return None

    if ptype == "under" and prediction.get("unit") and "corner" in prediction.get("unit"):
        if corners_tuple:
            total = corners_tuple[0] + corners_tuple[1]
            thr = float(prediction.get("value", 0))
            if total < thr:
                return "green"
            else:
                return "red"
        else:
            return None

    if ptype == "pick":
        # try to match team name to winner in score
        pick = prediction.get("value", "").lower()
        if score_tuple and pick:
            h, a = score_tuple
            if h == a:
                winner = "draw"
            elif h > a:
                winner = "home"
            else:
                winner = "away"
            # rough matching by checking if pick contains home/away or "home"/"away"
            # This requires that we also saved home/away names in DB (we do). We'll compare later in poller using saved home/away text.
            return None  # leave decision to poller where we can compare names
    # other types fallback
    return None

# -----------------------------
# Message edit helper
# -----------------------------
async def update_message_mark(dest_chat_id, dest_msg_id, mark_text):
    try:
        orig = await client.get_messages(dest_chat_id, ids=dest_msg_id)
        if orig:
            new_text = (orig.message or "") + "\n\n" + mark_text
            await client.edit_message(dest_chat_id, dest_msg_id, new_text)
            logging.info(f"Edited message {dest_msg_id} in {dest_chat_id} with {mark_text}")
    except Exception:
        logging.exception(f"Failed to edit message {dest_msg_id}")

# -----------------------------
# Poller task
# -----------------------------
async def poller_task():
    await asyncio.sleep(5)
    init_db()
    logging.info("Poller iniciado (scraping automático). Intervalo: %ss", POLL_INTERVAL)
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute("SELECT id, dest_chat_id, dest_msg_id, source_url, home, away, prediction FROM tracked_games WHERE status='pending'")
                rows = cur.fetchall()
                conn.close()

                if not rows:
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                for row in rows:
                    game_id, dest_chat_id, dest_msg_id, source_url, home, away, prediction_json = row
                    # parse prediction_json (it was inserted as text)
                    prediction = None
                    try:
                        if prediction_json:
                            # it's a simple str representation; we'll attempt eval-safe parse
                            prediction = eval(prediction_json) if isinstance(prediction_json, str) and prediction_json.startswith("{") else None
                    except Exception:
                        prediction = None

                    text_page = ""
                    score = None
                    corners = None

                    # 1) if URL available, fetch it
                    if source_url:
                        text_page = await fetch_page_text(session, source_url)
                        score = await parse_score_from_text(text_page)
                        corners = await parse_corners_from_text(text_page)

                    # 2) if not found and we have home+away, try searching CornerPro (heuristic)
                    if (not score or not corners) and home and away:
                        q = f"{home} {away}"
                        try:
                            search_url = f"https://cornerprobet.com/search?query={aiohttp.helpers.quote(q)}"
                            page = await fetch_page_text(session, search_url)
                            bs = BeautifulSoup(page, "html.parser")
                            # try to find a link with both team names
                            a = bs.find("a", href=True, text=re.compile(rf"{re.escape(home)}.*{re.escape(away)}", re.I))
                            if a:
                                page_url = a['href']
                                if page_url.startswith("/"):
                                    page_url = "https://cornerprobet.com" + page_url
                                text_page = await fetch_page_text(session, page_url)
                                # re-parse
                                if not score:
                                    score = await parse_score_from_text(text_page)
                                if not corners:
                                    corners = await parse_corners_from_text(text_page)
                        except Exception:
                            logging.exception("Erro buscando no cornerpro")

                    # 3) fallback: try to find any score in page text
                    if not score and text_page:
                        score = await parse_score_from_text(text_page)

                    # 4) Decide
                    outcome = None
                    # if prediction was stored and type pick, we may need to compare names (we'll fetch orig home/away from DB)
                    # Let's get the stored home/away strings
                    stored_home = home
                    stored_away = away
                    # attempt basic decision via decide_outcome
                    outcome = await decide_outcome(prediction, score, corners)

                    # special handling for pick type: compare names
                    if not outcome and prediction and prediction.get("type") == "pick" and score and (stored_home or stored_away):
                        # determine winner from score
                        h, a = score
                        if h == a:
                            winner = "draw"
                        elif h > a:
                            winner = "home"
                        else:
                            winner = "away"
                        # compare pick string with home/away stored
                        pick_raw = (prediction.get("value") or "").lower()
                        # match by substring
                        picked_home = stored_home and pick_raw in stored_home.lower()
                        picked_away = stored_away and pick_raw in stored_away.lower()
                        if (winner == "home" and picked_home) or (winner == "away" and picked_away):
                            outcome = "green"
                        else:
                            outcome = "red"

                    # finalization: if we got an outcome, edit message and mark DB
                    if outcome in ("green", "red"):
                        mark_text = "✅✅✅✅✅✅✅✅✅✅✅✅" if outcome == "green" else "✖️"
                        try:
                            await update_message_mark(dest_chat_id, dest_msg_id, mark_text)
                        except Exception:
                            logging.exception("Erro editando mensagem")
                        mark_status(game_id, outcome, f"score={score} corners={corners}")
                        # small delay to avoid flood
                        await asyncio.sleep(1)

                    else:
                        # not decided yet: update last_checked
                        conn = sqlite3.connect(DB_PATH)
                        cur = conn.cursor()
                        cur.execute("UPDATE tracked_games SET last_checked=? WHERE id=?", (int(time.time()), game_id))
                        conn.commit()
                        conn.close()

                await asyncio.sleep(POLL_INTERVAL)

            except Exception:
                logging.exception("Erro no poller")
                await asyncio.sleep(min(300, POLL_INTERVAL))

# -----------------------------
# Forwarding / processing
# -----------------------------
async def _notify_if_configured(preview_text: str):
    if NOTIFY_CHAT_ID == 0:
        return
    try:
        msg = f"✅ Encaminhado para {TARGET_CHAT_ID}\nPrévia: {preview_text[:200]}"
        await client.send_message(NOTIFY_CHAT_ID, msg, silent=False, link_preview=False)
    except Exception as e:
        logging.warning(f"Falha ao notificar NOTIFY_CHAT_ID: {e}")

async def _copy_single_message(msg):
    text = msg.message or ""
    new_text = replace_text(text) or None

    if msg.media:
        sent = await client.send_file(
            TARGET_CHAT_ID,
            msg.media,
            caption=new_text,
            force_document=False,
            silent=False
        )
    else:
        sent = await client.send_message(
            TARGET_CHAT_ID,
            new_text or "",
            link_preview=True,
            silent=False
        )
    return sent

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
            sent = await client.send_file(
                TARGET_CHAT_ID,
                files,
                caption=caption or None,
                silent=False
            )
            return sent
        return None
    sent = await _copy_single_message(event.message)
    return sent

async def _handle_forward(event):
    if REPLACE_FROM:
        logging.info("Substituição ativa: usando COPY em vez de FORWARD")
        sent = await _handle_copy(event)
        return sent
    # forward_to returns messages; we will forward and return a message object if possible
    forwarded = await event.forward_to(TARGET_CHAT_ID)
    return forwarded

async def _process_event(event):
    try:
        effective_mode = MODE
        if MODE == "forward" and REPLACE_FROM:
            effective_mode = "copy"

        sent = None
        if effective_mode == "copy":
            sent = await _handle_copy(event)
        else:
            sent = await _handle_forward(event)

        preview = (event.raw_text or "").replace("\n", " ")[:200]
        logging.info(f"Enviado → {TARGET_CHAT_ID} | Mode={effective_mode} | Preview='{preview}'")

        # save tracking: extract url/home/away/prediction
        try:
            # determine dest message id
            dest_id = None
            if isinstance(sent, list) and sent:
                dest_id = sent[0].id
            elif hasattr(sent, "id"):
                dest_id = getattr(sent, "id", None)
            elif hasattr(sent, "message") and getattr(sent, "message"):
                dest_id = sent.message.id if hasattr(sent.message, "id") else None

            # heuristics to extract url and teams from original event
            raw_text = event.raw_text or ""
            m_link = re.search(r'(https?://\S+)', raw_text)
            source_url = m_link.group(1) if m_link else None

            # try to extract "Jogo: Team A x Team B" or "Team A x Team B"
            m_game = re.search(r'Jogo:\s*(.+?)\s*[xX]\s*(.+)', raw_text)
            if not m_game:
                m_game = re.search(r'([A-Za-z0-9.\- ]{2,60})\s*[xX]\s*([A-Za-z0-9.\- ]{2,60})', raw_text)

            home = None
            away = None
            if m_game:
                home = m_game.group(1).strip()
                away = m_game.group(2).strip()

            prediction = parse_prediction_from_text(raw_text)
            # store prediction as string (repr) to recover later
            if dest_id:
                add_tracked(TARGET_CHAT_ID, dest_id, source_url=source_url, home=home, away=away, prediction=repr(prediction), note=preview)

        except Exception:
            logging.exception("Falha ao salvar tracking")

        # notify if configured
        await _notify_if_configured(preview)

    except FloodWaitError as fw:
        wait = getattr(fw, "seconds", 5)
        logging.warning(f"FloodWait: aguardando {wait}s")
        await asyncio.sleep(wait + 1)
    except Exception:
        logging.exception("Erro ao processar evento")

# -----------------------------
# Source filter: only from configured origin (by chat id or username)
# -----------------------------
async def _is_from_source(event) -> bool:
    # by chat id
    try:
        if ORIGEM_CHAT_ID and getattr(event, "chat_id", None) == ORIGEM_CHAT_ID:
            if DEBUG:
                logging.info(f"[DEBUG] match por ID da origem: {event.chat_id}")
            return True
    except Exception:
        pass

    # fallback by username
    try:
        sender = await event.get_sender()
        sender_user = (getattr(sender, "username", "") or "").lower()
    except Exception:
        sender_user = ""

    try:
        chat = await event.get_chat()
        chat_user = (getattr(chat, "username", "") or "").lower()
    except Exception:
        chat_user = ""

    if DEBUG:
        logging.info(f"[DEBUG] chat_id={getattr(event, 'chat_id', None)} sender={sender_user!r} chat={chat_user!r} want={ORIGEM_USERNAME!r}")

    return (ORIGEM_USERNAME and (sender_user == ORIGEM_USERNAME or chat_user == ORIGEM_USERNAME))

# -----------------------------
# Only accept messages from bots (sender.bot == True)
# -----------------------------
@client.on(events.NewMessage)
async def on_new_message(event):
    if not await _is_from_source(event):
        return

    # only from actual bot accounts
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

# -----------------------------
# Main
# -----------------------------
def main():
    logging.info("Forwarder rodando…")
    threading.Thread(target=start_health_server, args=(PORT,), daemon=True).start()

    # start poller as background task after client starts
    async def start_client_and_poller():
        await client.start()
        # create poller task
        asyncio.create_task(poller_task())
        await client.run_until_disconnected()

    # run the async entry
    asyncio.get_event_loop().run_until_complete(start_client_and_poller())

if __name__ == "__main__":
    main()
