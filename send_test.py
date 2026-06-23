import os
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
STRING_SESSION = os.getenv("STRING_SESSION", "")
DESTINO_CHAT_ID = int(os.getenv("DESTINO_CHAT_ID", "0"))

async def main():
    client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)
    await client.start()

    await client.send_message(
        DESTINO_CHAT_ID,
        "✅ Teste do BrotherBot: consegui enviar mensagem neste grupo."
    )

    print(f"Mensagem de teste enviada para {DESTINO_CHAT_ID}")
    await client.disconnect()

asyncio.run(main())
