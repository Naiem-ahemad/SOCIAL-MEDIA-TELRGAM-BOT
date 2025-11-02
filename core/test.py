from pyrogram import Client
import asyncio, os
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

CHANNEL = -1003236616614

app = Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

async def main():
    await app.start()
    chat = await app.get_chat(CHANNEL)
    print(chat.id, chat.title)
    await app.stop()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
