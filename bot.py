from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeFilename
import json
import asyncio
import os
from datetime import datetime

# Configuration
API_ID = int(os.getenv('API_ID', '25923419'))
API_HASH = os.getenv('API_HASH', 'fb5eb957660ee81004017afa6629f1ab')
PHONE = os.getenv('PHONE', '+918080202210')  # Your phone number
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
    
    # Create client
    client = TelegramClient('indexer_session', API_ID, API_HASH)
    
    # Start client (first time will ask for phone code)
    await client.start(phone=PHONE)
    print("✅ Connected to Telegram")
    
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
            await asyncio.sleep(60)  # Wait 1 minute on error
    
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
