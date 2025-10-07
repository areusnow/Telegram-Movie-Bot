from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeFilename
import json
import asyncio
import os
from datetime import datetime
from aiohttp import web

# Try to load .env file if it exists (for local testing only)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, that's fine on Render

# Configuration
API_ID = int(os.getenv('API_ID', '25923419'))
API_HASH = os.getenv('API_HASH', 'fb5eb957660ee81004017afa6629f1ab')

# Debug: Print all environment variables
print("=" * 50)
print("DEBUG: Environment Variables")
print("=" * 50)
for key in sorted(os.environ.keys()):
    if 'TOKEN' in key or 'API' in key:
        value = os.environ[key]
        # Mask token for security
        masked = value[:10] + "..." if len(value) > 10 else value
        print(f"{key} = {masked}")
print("=" * 50)

BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    # Try alternative names in case of typo
    BOT_TOKEN = os.getenv('bot_token') or os.getenv('BOTTOKEN') or os.getenv('TOKEN')
    
print(f"BOT_TOKEN loaded: {BOT_TOKEN is not None}")
print(f"BOT_TOKEN length: {len(BOT_TOKEN) if BOT_TOKEN else 0}")

SOURCE_CHANNEL = os.getenv('SOURCE_CHANNEL', '@TheCineVerseX')
UPDATE_INTERVAL = int(os.getenv('UPDATE_INTERVAL', '3600'))  # Update every hour

# GitHub auto-commit (optional)
ENABLE_AUTO_COMMIT = os.getenv('ENABLE_AUTO_COMMIT', 'false').lower() == 'true'

async def index_channel(client):
    """Index all files from the channel"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 📥 Indexing channel: {SOURCE_CHANNEL}")
    
    movie_data = {}
    count = 0
    
    try:
        print("   🔄 Fetching messages...")
        message_count = 0
        async for message in client.iter_messages(SOURCE_CHANNEL, limit=None):
            message_count += 1
            if message_count % 100 == 0:
                print(f"   📊 Processed {message_count} messages, found {count} files so far...")
            
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
        
        print(f"   ✅ Total messages processed: {message_count}")
        print(f"   ✅ Files with documents: {count}")
        
        # Save to JSON
        print("   💾 Saving to movies.json...")
        with open('movies.json', 'w', encoding='utf-8') as f:
            json.dump(movie_data, f, ensure_ascii=False, indent=2)
        
        print(f"   ✅ Saved {count} files to movies.json")
        
        # Show sample
        if count > 0:
            print(f"   📋 Sample files:")
            for i, (filename, _) in enumerate(list(movie_data.items())[:3]):
                print(f"      {i+1}. {filename}")
        
        # Auto-commit to GitHub (if enabled)
        if ENABLE_AUTO_COMMIT:
            print("   📤 Pushing to GitHub...")
            os.system('git config user.name "Auto Indexer Bot"')
            os.system('git config user.email "bot@render.com"')
            os.system('git add movies.json')
            os.system(f'git commit -m "Auto-update: {count} movies - {datetime.now()}"')
            result = os.system('git push')
            if result == 0:
                print("   ✅ Pushed to GitHub successfully")
            else:
                print("   ⚠️  Git push failed (this is normal if no changes)")
        
        return count
        
    except Exception as e:
        print(f"   ❌ Error indexing: {e}")
        import traceback
        traceback.print_exc()
        return 0

async def main():
    """Main loop - continuously update"""
    print("🤖 Starting Auto-Indexer...")
    print(f"Update interval: {UPDATE_INTERVAL} seconds")
    print(f"Target channel: {SOURCE_CHANNEL}")
    
    # Validate BOT_TOKEN
    if not BOT_TOKEN:
        print("❌ ERROR: BOT_TOKEN environment variable not set!")
        print("Get your bot token from @BotFather on Telegram")
        return
    
    print(f"BOT_TOKEN: {'✅ Set' if BOT_TOKEN else '❌ Not set'}")
    
    # Create client with bot token (NO PHONE NEEDED!)
    client = TelegramClient('indexer_session', API_ID, API_HASH)
    
    try:
        # Start with bot token - no interactive login required!
        print("🔄 Connecting to Telegram...")
        await client.start(bot_token=BOT_TOKEN)
        print("✅ Connected to Telegram as bot")
        
        # Get bot info
        me = await client.get_me()
        print(f"📱 Bot username: @{me.username}")
        print(f"📱 Bot ID: {me.id}")
        
        # Try to access the channel first
        print(f"\n🔍 Attempting to access channel: {SOURCE_CHANNEL}")
        try:
            entity = await client.get_entity(SOURCE_CHANNEL)
            print(f"✅ Channel found: {entity.title}")
            print(f"   Channel ID: {entity.id}")
        except Exception as e:
            print(f"❌ Cannot access channel: {e}")
            print("   Make sure the bot is added as an admin to the channel!")
            return
        
        # Start web server for Render health check
        async def health_check(request):
            return web.Response(text=f"✅ Bot is running!\n\nBot: @{me.username}\nChannel: {SOURCE_CHANNEL}\nUpdate Interval: {UPDATE_INTERVAL}s")
        
        app = web.Application()
        app.router.add_get('/', health_check)
        runner = web.AppRunner(app)
        await runner.setup()
        
        port = int(os.getenv('PORT', 10000))
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        print(f"✅ Web server started on port {port}")
        
        print("\n" + "="*50)
        print("Starting indexing loop...")
        print("="*50 + "\n")
        
        while True:
            try:
                print(f"\n⏱️  [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting index...")
                count = await index_channel(client)
                print(f"✅ Indexing complete: {count} files")
                print(f"⏰ Next update in {UPDATE_INTERVAL/60} minutes\n")
                await asyncio.sleep(UPDATE_INTERVAL)
            except KeyboardInterrupt:
                print("\n👋 Stopping indexer...")
                break
            except Exception as e:
                print(f"❌ Error in main loop: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(60)  # Wait 1 minute on error
    
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await client.disconnect()
        print("👋 Disconnected from Telegram")

if __name__ == '__main__':
    asyncio.run(main())
