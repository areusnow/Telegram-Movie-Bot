import os
import re
import asyncio
from threading import Thread
from flask import Flask, send_file
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from difflib import SequenceMatcher
from pymongo import MongoClient

# ==========================
# Flask Server for Render
# ==========================
app = Flask(__name__)

@app.route('/')
def home():
    return '🎬 Telegram Movie Bot is running!'

@app.route('/health')
def health():
    return {'status': 'ok', 'bot': 'running'}

# ==========================
# Environment Variables
# ==========================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PRIVATE_CHANNEL_ID = int(os.environ.get("PRIVATE_CHANNEL_ID", "-1001234567890"))
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "123456789").split(",")]
MONGO_URI = os.environ.get("MONGO_URI")
DB_NAME = "MovieBot"
ITEMS_PER_PAGE = 8  # Number of items per page

# ==========================
# MongoDB Class (Simplified)
# ==========================
class MediaDatabase:
    def __init__(self):
        self.client = MongoClient(MONGO_URI, tls=True, tlsAllowInvalidCertificates=True)
        self.db = self.client[DB_NAME]
        self.media = self.db["media"]  # Single collection for all files
        
    def add_media(self, message_id, filename, file_id):
        """Add media file to database"""
        # Clean filename for search
        clean_name = os.path.splitext(filename)[0]
        clean_name = re.sub(r'[._-]', ' ', clean_name).lower()
        
        doc = {
            "filename": filename,
            "search_name": clean_name,
            "message_id": message_id,
            "file_id": file_id
        }
        self.media.insert_one(doc)

    def fuzzy_search(self, query, threshold=0.4):
        """Search for media files matching query"""
        query_lower = query.lower()
        results = []
        
        for item in self.media.find():
            search_name = item.get("search_name", "")
            # Calculate similarity ratio
            ratio = SequenceMatcher(None, query_lower, search_name).ratio()
            
            # Include if ratio exceeds threshold OR query is substring
            if ratio >= threshold or query_lower in search_name:
                results.append({
                    "filename": item["filename"],
                    "message_id": item["message_id"],
                    "file_id": item["file_id"],
                    "ratio": ratio
                })
        
        # Sort by relevance (highest ratio first)
        results.sort(key=lambda x: x["ratio"], reverse=True)
        return results

    def get_total_count(self):
        """Get total number of files"""
        return self.media.count_documents({})

# Initialize DB
db = MediaDatabase()

# ==========================
# Bot Handlers
# ==========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🎬 Welcome to Movie Bot!\n\nSearch for movies/series by typing name.\n/stats - Show database info"
    if update.message.from_user.id in ADMIN_IDS:
        text += "\n/index - Admin index mode"
    await update.message.reply_text(text)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count = db.get_total_count()
    await update.message.reply_text(f"📊 Total Files: {count}")

async def index_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id not in ADMIN_IDS:
        return await update.message.reply_text("❌ Admins only!")
    await update.message.reply_text("📝 Forward files here from your private channel to index them.")

async def handle_media_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle file uploads from admins"""
    if update.message.from_user.id not in ADMIN_IDS:
        return
    
    file_obj = update.message.document or update.message.video
    if not file_obj:
        return
    
    filename = file_obj.file_name or f"video_{update.message.message_id}.mp4"
    
    try:
        # Forward to private channel
        forwarded = await context.bot.copy_message(
            chat_id=PRIVATE_CHANNEL_ID,
            from_chat_id=update.message.chat_id,
            message_id=update.message.message_id
        )
        
        # Add to database
        db.add_media(forwarded.message_id, filename, file_obj.file_id)
        await update.message.reply_text(f"✅ Indexed: {filename}")
    except Exception as e:
        await update.message.reply_text(f"❌ Error indexing {filename}: {e}")

async def search_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle search queries"""
    query = update.message.text.strip()
    if not query or query.startswith('/'):
        return
    
    results = db.fuzzy_search(query)
    
    if not results:
        return await update.message.reply_text(f"❌ No results found for '{query}'")
    
    # Store results in context for pagination
    context.user_data['search_results'] = results
    context.user_data['search_query'] = query
    
    await show_results_page(update.message, query, results, 0)

# ==========================
# Pagination Functions
# ==========================
async def show_results_page(message, query, results, page):
    """Show paginated search results"""
    total_items = len(results)
    total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    
    start_idx = page * ITEMS_PER_PAGE
    end_idx = min((page + 1) * ITEMS_PER_PAGE, total_items)
    page_items = results[start_idx:end_idx]
    
    keyboard = []
    
    # Send All button at top
    keyboard.append([InlineKeyboardButton(
        f"📦 Send All Files on This Page ({len(page_items)})",
        callback_data=f"sendpage:{page}"
    )])
    
    # Individual file buttons
    for item in page_items:
        keyboard.append([InlineKeyboardButton(
            f"📥 {item['filename']}",
            callback_data=f"get:{item['message_id']}"
        )])
    
    # Navigation buttons
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(
            "⬅️ Previous",
            callback_data=f"page:{page-1}"
        ))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(
            "➡️ Next",
            callback_data=f"page:{page+1}"
        ))
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    text = f"🔍 Results for '{query}'\nPage {page+1}/{total_pages} ({total_items} results)"
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# ==========================
# Button Callback Handler
# ==========================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith("page:"):
        # Navigate to different page
        page = int(data.split(":", 1)[1])
        results = context.user_data.get('search_results', [])
        search_query = context.user_data.get('search_query', '')
        
        await query.message.delete()
        await show_results_page(query.message, search_query, results, page)
    
    elif data.startswith("get:"):
        # Send single file
        message_id = int(data.split(":", 1)[1])
        await query.answer("Sending file...")
        
        try:
            await context.bot.copy_message(
                chat_id=query.from_user.id,
                from_chat_id=PRIVATE_CHANNEL_ID,
                message_id=message_id
            )
            await query.answer("✅ File sent!", show_alert=True)
        except Exception as e:
            await query.answer(f"❌ Error: {str(e)}", show_alert=True)
    
    elif data.startswith("sendpage:"):
        # Send all files on current page
        page = int(data.split(":", 1)[1])
        results = context.user_data.get('search_results', [])
        
        start_idx = page * ITEMS_PER_PAGE
        end_idx = min((page + 1) * ITEMS_PER_PAGE, len(results))
        page_items = results[start_idx:end_idx]
        
        await query.answer(f"Sending {len(page_items)} files...")
        
        sent_count = 0
        for item in page_items:
            try:
                await context.bot.copy_message(
                    chat_id=query.from_user.id,
                    from_chat_id=PRIVATE_CHANNEL_ID,
                    message_id=item['message_id']
                )
                sent_count += 1
                await asyncio.sleep(0.5)  # Small delay to avoid rate limits
            except Exception as e:
                print(f"Error sending {item['filename']}: {e}")
        
        await query.answer(f"✅ Sent {sent_count}/{len(page_items)} files!", show_alert=True)

# ==========================
# Main Entrypoint
# ==========================
def main():
    port = int(os.environ.get("PORT", 10000))
    Thread(target=lambda: app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False), daemon=True).start()

    if not BOT_TOKEN:
        print("❌ Missing BOT_TOKEN")
        return

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("index", index_channel))
    application.add_handler(MessageHandler(
        (filters.Document.ALL | filters.VIDEO) & ~filters.COMMAND,
        handle_media_message
    ))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_media))
    application.add_handler(CallbackQueryHandler(button_callback))

    print("🚀 Bot is live...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
