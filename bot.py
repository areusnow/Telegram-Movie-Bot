import logging
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, ChatMemberHandler
from difflib import SequenceMatcher

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = 'YOUR_BOT_TOKEN_HERE'
CHANNEL_USERNAME = '@your_channel_username'  # Must use username format
CACHE_FILE = 'file_cache.json'

# Cache for storing channel files
file_cache = {}

def load_cache():
    """Load cache from file"""
    global file_cache
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            file_cache = json.load(f)
        logger.info(f"Loaded {len(file_cache)} files from cache")
    except FileNotFoundError:
        logger.info("No cache file found, starting with empty cache")
        file_cache = {}
    except Exception as e:
        logger.error(f"Error loading cache: {e}")
        file_cache = {}

def save_cache():
    """Save cache to file"""
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(file_cache, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved {len(file_cache)} files to cache")
    except Exception as e:
        logger.error(f"Error saving cache: {e}")

def similarity(a, b):
    """Calculate similarity ratio between two strings"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

async def channel_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Automatically index files posted in the channel"""
    if not update.channel_post:
        return
    
    msg = update.channel_post
    file_info = None
    
    # Check for different file types
    if msg.document:
        file_info = {
            'name': msg.document.file_name or "Unknown",
            'type': 'document',
            'file_id': msg.document.file_id,
            'size': msg.document.file_size or 0,
            'message_id': msg.message_id,
            'caption': msg.caption or ""
        }
    elif msg.video:
        file_info = {
            'name': msg.video.file_name or msg.caption or "Unknown Video",
            'type': 'video',
            'file_id': msg.video.file_id,
            'size': msg.video.file_size or 0,
            'message_id': msg.message_id,
            'caption': msg.caption or ""
        }
    elif msg.audio:
        file_info = {
            'name': msg.audio.file_name or msg.audio.title or "Unknown Audio",
            'type': 'audio',
            'file_id': msg.audio.file_id,
            'size': msg.audio.file_size or 0,
            'message_id': msg.message_id,
            'caption': msg.caption or ""
        }
    
    if file_info:
        file_cache[str(msg.message_id)] = file_info
        save_cache()
        logger.info(f"Auto-indexed: {file_info['name']} (ID: {msg.message_id})")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message when /start is issued"""
    await update.message.reply_text(
        "🎬 Welcome to Movie Search Bot!\n\n"
        "Send me a movie name and I'll search for it in our channel.\n\n"
        "Commands:\n"
        "/start - Show this message\n"
        "/stats - Show cache statistics\n"
        "/list - List recent files\n\n"
        "Just type a movie name to search!"
    )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show cache statistics"""
    total = len(file_cache)
    
    # Calculate total size
    total_size = sum(info.get('size', 0) for info in file_cache.values())
    total_size_gb = total_size / (1024 ** 3)
    
    # Count by type
    types_count = {}
    for info in file_cache.values():
        file_type = info.get('type', 'unknown')
        types_count[file_type] = types_count.get(file_type, 0) + 1
    
    stats_text = f"📊 Cache Statistics:\n\n"
    stats_text += f"📁 Total files: {total}\n"
    stats_text += f"💾 Total size: {total_size_gb:.2f} GB\n\n"
    stats_text += "File types:\n"
    for ftype, count in types_count.items():
        stats_text += f"  • {ftype}: {count}\n"
    
    await update.message.reply_text(stats_text)

async def list_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List recent files in cache"""
    if not file_cache:
        await update.message.reply_text("⚠️ File cache is empty.")
        return
    
    # Get last 15 files
    sorted_files = sorted(
        file_cache.items(),
        key=lambda x: x[1].get('message_id', 0),
        reverse=True
    )[:15]
    
    list_text = "📋 Recent files:\n\n"
    for i, (msg_id, info) in enumerate(sorted_files, 1):
        size_mb = info.get('size', 0) / (1024 * 1024)
        list_text += f"{i}. {info['name']}\n"
        list_text += f"   💾 {size_mb:.2f} MB | ID: {msg_id}\n\n"
    
    await update.message.reply_text(list_text)

async def search_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search for files based on user query"""
    query = update.message.text.strip()
    
    if len(query) < 2:
        await update.message.reply_text("Please provide a longer search query (at least 2 characters)")
        return
    
    # If cache is empty, inform user
    if not file_cache:
        await update.message.reply_text(
            "⚠️ File cache is empty. Files will be automatically indexed when posted to the channel."
        )
        return
    
    # Search for matches
    matches = []
    for msg_id, file_info in file_cache.items():
        file_name = file_info['name']
        caption = file_info.get('caption', '')
        
        # Check similarity with both filename and caption
        sim_name = similarity(query, file_name)
        sim_caption = similarity(query, caption) if caption else 0
        sim = max(sim_name, sim_caption)
        
        if sim > 0.3:  # Threshold for matching
            matches.append({
                'similarity': sim,
                'file_info': file_info
            })
    
    # Sort by similarity
    matches.sort(key=lambda x: x['similarity'], reverse=True)
    
    if not matches:
        await update.message.reply_text(
            f"❌ No files found matching '{query}'\n\n"
            f"💡 Try different keywords or check spelling."
        )
        return
    
    # Show top 10 results
    results_text = f"🔍 Found {len(matches)} result(s) for '{query}':\n\n"
    keyboard = []
    
    for i, match in enumerate(matches[:10], 1):
        file_info = match['file_info']
        similarity_percent = int(match['similarity'] * 100)
        size_mb = file_info.get('size', 0) / (1024 * 1024)
        
        # Truncate long names
        display_name = file_info['name']
        if len(display_name) > 50:
            display_name = display_name[:47] + "..."
        
        results_text += f"{i}. {display_name}\n"
        results_text += f"   📊 {similarity_percent}% match | 💾 {size_mb:.2f} MB\n\n"
        
        # Create button to get file
        keyboard.append([
            InlineKeyboardButton(
                f"📥 Get #{i}",
                callback_data=f"get_{file_info['message_id']}"
            )
        ])
    
    if len(matches) > 10:
        results_text += f"\n... and {len(matches) - 10} more results"
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(results_text, reply_markup=reply_markup)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button clicks"""
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith('get_'):
        msg_id = int(query.data.split('_')[1])
        
        try:
            # Forward the file from channel to user
            await context.bot.forward_message(
                chat_id=query.message.chat_id,
                from_chat_id=CHANNEL_USERNAME,
                message_id=msg_id
            )
            await query.answer("✅ File sent!", show_alert=True)
        except Exception as e:
            logger.error(f"Error forwarding message: {e}")
            await query.answer(f"❌ Error: {str(e)}", show_alert=True)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors"""
    logger.error(f"Update {update} caused error {context.error}")

def main():
    """Start the bot"""
    # Load cache on startup
    load_cache()
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("list", list_files))
    
    # Channel post handler for auto-indexing
    application.add_handler(MessageHandler(
        filters.ChatType.CHANNEL & (filters.Document.ALL | filters.VIDEO | filters.AUDIO),
        channel_post_handler
    ))
    
    # Search handler
    application.add_
