from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeFilename
import json
import asyncio
import os
from datetime import datetime

# Configuration
API_ID = int(os.getenv('API_ID', '25923419'))
API_HASH = os.getenv('API_HASH', 'fb5eb957660ee81004017afa6629f1ab')
BOT_TOKEN = os.getenv('7990282768:AAGKek9lizWmXB8U57CucrjTxo1twG5YI9M')
SOURCE_CHANNEL = os.getenv('SOURCE_CHANNEL', '@TheCineVerseX')
UPDATE_INTERVAL = 3600  # Update every hour (in seconds)

# GitHub auto-commit (optional)
ENABLE_AUTO_COMMIT = os.getenv('ENABLE_AUTO_COMMIT', 'false').lower() == 'true'

async def index_channel(client):
    """Index all files from the channel"""
    print(f"[{datetime.now()}] Indexing channel: {SOURCE_CHANNEL}")
    
    movie_data = {}
    count = 0
    
    try:
        async for message in client.iter_messages(SOURCE_CHANNEL, limit=None):
            if message.document:
                filename = None
                
                for attr in message.document.attributes:
                    if isinstance(attr, DocumentAttributeFilename):
                        filename = attr.file_name
                        break
                
                if filename:
                    count += 1
                    movie_data[filename] = {
                        'message_id': message.id,
                        'filename': filename,
                        'caption': message.text or '',
                        'size': message.document.size,
                        'date': message.date.isoformat() if message.date else None
                    }
        
        # Save to JSON
        with open('movies.json', 'w', encoding='utf-8') as f:
            json.dump(movie_data, f, ensure_ascii=False, indent=2)
        
        print(f"✅ Indexed {count} files")
        
        # Auto-commit to GitHub (if enabled)
        if ENABLE_AUTO_COMMIT:
            os.system('git add movies.json')
            os.system(f'git commit -m "Auto-update: {count} movies - {datetime.now()}"')
            os.system('git push')
            print("📤 Pushed to GitHub")
        
        return count
        
    except Exception as e:
        print(f"❌ Error indexing: {e}")
        return 0

async def main():
    """Main loop - continuously update"""
    print("🤖 Starting Auto-Indexer...")
    print(f"Update interval: {UPDATE_INTERVAL} seconds")
    
    if not BOT_TOKEN:
        print("❌ ERROR: BOT_TOKEN environment variable not set!")
        print("Get your bot token from @BotFather on Telegram")
        return
    
    # Create client with bot token (NO PHONE NEEDED!)
    client = TelegramClient('bot_session', API_ID, API_HASH)
    
    # Start with bot token - no interactive login required!
    await client.start(bot_token=BOT_TOKEN)
    print("✅ Connected to Telegram as bot")
    
    while True:
        try:
            await index_channel(client)
            print(f"⏰ Next update in {UPDATE_INTERVAL/60} minutes\n")
            await asyncio.sleep(UPDATE_INTERVAL)
        except KeyboardInterrupt:
            print("\n👋 Stopping indexer...")
            break
        except Exception as e:
            print(f"❌ Error: {e}")
            await asyncio.sleep(30)  # Wait 30 seconds on error
    
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
