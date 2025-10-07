import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from difflib import SequenceMatcher

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = '7990282768:AAGKek9lizWmXB8U57CucrjTxo1twG5YI9M'
CHANNEL_ID = '@TheCineVerseX'  # or channel ID like -1001234567890

# Cache for storing channel files
file_cache = {}

def similarity(a, b):
    """Calculate similarity ratio between two strings"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message when /start is issued"""
    await update.message.reply_text(
        "🎬 Welcome to Movie Search Bot!\n\n"
        "Send me a movie name and I'll search for it in our channel.\n\n"
        "Commands:\n"
        "/start - Show this message\n"
        "/refresh - Refresh file cache\n"
        "/stats - Show cache statistics"
    )

async def refresh_cache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refresh the file cache by scanning the channel"""
    await update.message.reply_text("🔄 Refreshing file cache... This may take a while.")
    
    try:
        file_cache.clear()
        offset_id = 0
        total_files = 0
        
        while True:
            # Get messages from channel
            messages = await context.bot.get_chat_history(
                chat_id=CHANNEL_ID,
                limit=100,
                offset_id=offset_id
            )
            
            if not messages:
                break
            
            for msg in messages:
                # Check if message has document/video/audio
                if msg.document:
                    file_name = msg.document.file_name or "Unknown"
                    file_cache[msg.message_id] = {
                        'name': file_name,
                        'type': 'document',
                        'file_id': msg.document.file_id,
                        'size': msg.document.file_size,
                        'message_id': msg.message_id
                    }
                    total_files += 1
                elif msg.video:
                    file_name = msg.video.file_name or msg.caption or "Unknown Video"
                    file_cache[msg.message_id] = {
                        'name': file_name,
                        'type': 'video',
                        'file_id': msg.video.file_id,
                        'size': msg.video.file_size,
                        'message_id': msg.message_id
                    }
                    total_files += 1
                
                offset_id = msg.message_id
            
            if len(messages) < 100:
                break
        
        await update.message.reply_text(
            f"✅ Cache refreshed successfully!\n"
            f"Total files indexed: {total_files}"
        )
    except Exception as e:
        logger.error(f"Error refreshing cache: {e}")
        await update.message.reply_text(f"❌ Error refreshing cache: {str(e)}")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show cache statistics"""
    total = len(file_cache)
    await update.message.reply_text(
        f"📊 Cache Statistics:\n"
        f"Total files indexed: {total}"
    )

async def search_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search for files based on user query"""
    query = update.message.text.strip()
    
    if len(query) < 2:
        await update.message.reply_text("Please provide a longer search query (at least 2 characters)")
        return
    
    # If cache is empty, inform user
    if not file_cache:
        await update.message.reply_text(
            "⚠️ File cache is empty. Please use /refresh to index channel files first."
        )
        return
    
    # Search for matches
    matches = []
    for msg_id, file_info in file_cache.items():
        file_name = file_info['name']
        sim = similarity(query, file_name)
        
        if sim > 0.3:  # Threshold for matching
            matches.append({
                'similarity': sim,
                'file_info': file_info
            })
    
    # Sort by similarity
    matches.sort(key=lambda x: x['similarity'], reverse=True)
    
    if not matches:
        await update.message.reply_text(f"❌ No files found matching '{query}'")
        return
    
    # Show top 10 results
    results_text = f"🔍 Search results for '{query}':\n\n"
    keyboard = []
    
    for i, match in enumerate(matches[:10], 1):
        file_info = match['file_info']
        similarity_percent = int(match['similarity'] * 100)
        size_mb = file_info['size'] / (1024 * 1024) if file_info['size'] else 0
        
        results_text += f"{i}. {file_info['name']}\n"
        results_text += f"   📊 Match: {similarity_percent}% | 💾 Size: {size_mb:.2f} MB\n\n"
        
        # Create button to get file
        keyboard.append([
            InlineKeyboardButton(
                f"Get #{i}",
                callback_data=f"get_{file_info['message_id']}"
            )
        ])
    
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
                from_chat_id=CHANNEL_ID,
                message_id=msg_id
            )
            await query.edit_message_text(
                text=query.message.text + "\n\n✅ File sent!"
            )
        except Exception as e:
            logger.error(f"Error forwarding message: {e}")
            await query.edit_message_text(
                text=query.message.text + f"\n\n❌ Error: {str(e)}"
            )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors"""
    logger.error(f"Update {update} caused error {context.error}")

def main():
    """Start the bot"""
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("refresh", refresh_cache))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_files))
    
    # Register error handler
    application.add_error_handler(error_handler)
    
    # Start the bot
    logger.info("Bot started!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
