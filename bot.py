import os
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeFilename
import re

# Bot configuration
API_ID = '25923419'  # Get from my.telegram.org
API_HASH = 'fb5eb957660ee81004017afa6629f1ab'  # Get from my.telegram.org
BOT_TOKEN = '7990282768:AAGKek9lizWmXB8U57CucrjTxo1twG5YI9M'  # Get from @BotFather
SOURCE_CHANNEL = '@TheCineVerseX'  # Channel username or ID

# Initialize bot
bot = TelegramClient('movie_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# Cache for storing movie data
movie_cache = {}

async def index_channel():
    """Index all movies from the source channel"""
    print("Indexing channel...")
    movie_cache.clear()
    
    async for message in bot.iter_messages(SOURCE_CHANNEL, limit=None):
        if message.document:
            # Get filename
            filename = None
            for attr in message.document.attributes:
                if isinstance(attr, DocumentAttributeFilename):
                    filename = attr.file_name
                    break
            
            if filename:
                # Store message details
                key = filename.lower()
                movie_cache[key] = {
                    'message_id': message.id,
                    'filename': filename,
                    'caption': message.text or '',
                    'file_size': message.document.size
                }
    
    print(f"Indexed {len(movie_cache)} files")

def search_movies(query):
    """Search for movies matching the query"""
    query = query.lower()
    results = []
    
    for key, data in movie_cache.items():
        # Search in filename and caption
        if query in key or query in data['caption'].lower():
            results.append(data)
    
    return results[:10]  # Return top 10 results

def format_size(size):
    """Format file size to human-readable format"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"

@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    """Handle /start command"""
    await event.respond(
        "🎬 Welcome to Movie Bot!\n\n"
        "Send me a movie name to search.\n"
        "Use /help for more information."
    )

@bot.on(events.NewMessage(pattern='/help'))
async def help_cmd(event):
    """Handle /help command"""
    await event.respond(
        "📖 **How to use:**\n\n"
        "1. Simply type the movie name\n"
        "2. Select from the results\n"
        "3. Get your movie file!\n\n"
        "Commands:\n"
        "/start - Start the bot\n"
        "/help - Show this message\n"
        "/refresh - Refresh movie database (admin only)"
    )

@bot.on(events.NewMessage(pattern='/refresh'))
async def refresh(event):
    """Refresh the movie database"""
    msg = await event.respond("🔄 Refreshing database...")
    await index_channel()
    await msg.edit("✅ Database refreshed!")

@bot.on(events.NewMessage)
async def search(event):
    """Handle movie search queries"""
    query = event.text.strip()
    
    # Ignore commands
    if query.startswith('/'):
        return
    
    # Search for movies
    await event.respond("🔍 Searching...")
    results = search_movies(query)
    
    if not results:
        await event.respond("❌ No movies found matching your query.")
        return
    
    # Send results
    response = f"📽️ **Found {len(results)} result(s):**\n\n"
    
    for i, movie in enumerate(results[:5], 1):
        response += (
            f"{i}. **{movie['filename']}**\n"
            f"   Size: {format_size(movie['file_size'])}\n\n"
        )
    
    await event.respond(response)
    
    # Forward the actual files
    for movie in results[:5]:
        try:
            # Forward the message from source channel
            await bot.forward_messages(
                event.chat_id,
                movie['message_id'],
                SOURCE_CHANNEL
            )
        except Exception as e:
            print(f"Error forwarding message: {e}")

@bot.on(events.CallbackQuery)
async def callback(event):
    """Handle inline button callbacks"""
    pass

async def main():
    """Main function"""
    print("Bot starting...")
    await index_channel()
    print("Bot is running!")
    await bot.run_until_disconnected()

if __name__ == '__main__':
    bot.loop.run_until_complete(main())
