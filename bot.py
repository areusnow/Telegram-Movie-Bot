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

@app.route('/download-db')
def download_db():
    try:
        return send_file("database.json", as_attachment=True)
    except Exception as e:
        return str(e)

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
# MongoDB Class
# ==========================
class MediaDatabase:
    def __init__(self):
        self.client = MongoClient(MONGO_URI, tls=True, tlsAllowInvalidCertificates=True)
        self.db = self.client[DB_NAME]
        self.movies = self.db["movies"]
        self.series = self.db["series"]

    def parse_filename(self, filename):
        name = os.path.splitext(filename)[0]
        quality_match = re.search(r'(2160p|4K|1080p|720p|480p|360p)', name, re.IGNORECASE)
        quality = quality_match.group(1).upper() if quality_match else "Unknown"
        season_episode = re.search(r'S(\d+)E(\d+)', name, re.IGNORECASE)
        year_match = re.search(r'(19|20)\d{2}', name)
        year = year_match.group(0) if year_match else None
        title = re.split(r'S\d+E\d+|(1080p|720p|480p|4K|2160p)|\d{4}', name, flags=re.IGNORECASE)[0]
        title = re.sub(r'[._-]', ' ', title).strip()

        if season_episode:
            return {"type": "series", "title": title,
                    "season": int(season_episode.group(1)),
                    "episode": int(season_episode.group(2)),
                    "quality": quality, "filename": filename}
        else:
            return {"type": "movie", "title": title, "year": year,
                    "quality": quality, "filename": filename}

    def add_media(self, message_id, filename, file_id):
        parsed = self.parse_filename(filename)
        key = parsed['title'].lower()

        if parsed['type'] == "series":
            doc = self.series.find_one({"key": key}) or {"key": key, "title": parsed['title'], "seasons": {}}
            seasons = doc.get("seasons", {})
            s = str(parsed['season'])
            e = str(parsed['episode'])
            q = parsed['quality']
            seasons.setdefault(s, {}).setdefault(e, {})[q] = {"message_id": message_id, "file_id": file_id, "filename": filename}
            self.series.update_one({"key": key}, {"$set": {"seasons": seasons, "title": parsed['title']}}, upsert=True)
        else:
            doc = self.movies.find_one({"key": key}) or {"key": key, "title": parsed['title'], "year": parsed['year'], "qualities": {}}
            q = parsed['quality']
            doc["qualities"][q] = {"message_id": message_id, "file_id": file_id, "filename": filename}
            self.movies.update_one({"key": key}, {"$set": doc}, upsert=True)

    def fuzzy_search(self, query, threshold=0.6):
        query_lower = query.lower()
        results = {"movies": [], "series": []}
        for movie in self.movies.find():
            ratio = SequenceMatcher(None, query_lower, movie["key"]).ratio()
            if ratio >= threshold or query_lower in movie["key"]:
                results["movies"].append({"title": movie["title"], "key": movie["key"], "ratio": ratio, "data": movie})
        for s in self.series.find():
            ratio = SequenceMatcher(None, query_lower, s["key"]).ratio()
            if ratio >= threshold or query_lower in s["key"]:
                results["series"].append({"title": s["title"], "key": s["key"], "ratio": ratio, "data": s})
        results["movies"].sort(key=lambda x: x["ratio"], reverse=True)
        results["series"].sort(key=lambda x: x["ratio"], reverse=True)
        return results

    def get_season_qualities(self, series_key, season_num):
        s = self.series.find_one({"key": series_key})
        if not s: return []
        season = s['seasons'].get(str(season_num), {})
        qualities = set()
        for ep in season.values():
            qualities.update(ep.keys())
        return sorted(qualities)

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
    await update.message.reply_text(f"📊 Movies: {db.movies.count_documents({})}\n📺 Series: {db.series.count_documents({})}")

async def index_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id not in ADMIN_IDS:
        return await update.message.reply_text("❌ Admins only!")
    await update.message.reply_text("📝 Forward files here from your private channel to index them.")

async def handle_media_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id not in ADMIN_IDS:
        return
    file_obj = update.message.document or update.message.video
    if not file_obj:
        return
    filename = file_obj.file_name or f"video_{update.message.message_id}.mp4"
    try:
        forwarded = await context.bot.copy_message(
            chat_id=PRIVATE_CHANNEL_ID,
            from_chat_id=update.message.chat_id,
            message_id=update.message.message_id
        )
        db.add_media(forwarded.message_id, filename, file_obj.file_id)
        await update.message.reply_text(f"✅ Indexed: {filename}")
    except Exception as e:
        await update.message.reply_text(f"❌ Error indexing {filename}: {e}")

async def search_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    if not query or query.startswith('/'):
        return
    results = db.fuzzy_search(query)
    if not results['movies'] and not results['series']:
        return await update.message.reply_text(f"❌ No results found for '{query}'")
    await show_search_results_page(update.message, query, results, 0)

# ==========================
# Inline Button Functions
# ==========================
async def show_search_results_page(message, query, results, page):
    all_items = [('movie', m) for m in results['movies']] + [('series', s) for s in results['series']]
    total_items = len(all_items)
    total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    start_idx, end_idx = page * ITEMS_PER_PAGE, min((page + 1) * ITEMS_PER_PAGE, total_items)
    page_items = all_items[start_idx:end_idx]

    keyboard = []
    if page_items: keyboard.append([InlineKeyboardButton("📦 Send All on This Page", callback_data=f"sendpage:{page}:{query}")])

    has_movies = has_series = False
    for item_type, item in page_items:
        if item_type == "movie" and not has_movies:
            keyboard.append([InlineKeyboardButton("🎬 MOVIES", callback_data="none")])
            has_movies = True
        elif item_type == "series" and not has_series:
            keyboard.append([InlineKeyboardButton("📺 SERIES", callback_data="none")])
            has_series = True
        display_name = item['data'].get('filename', item['title'])
        keyboard.append([InlineKeyboardButton(display_name, callback_data=f"{item_type}:{item['key']}")])

    nav_buttons = []
    if page > 0: nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"page:{page-1}:{query}"))
    if page < total_pages - 1: nav_buttons.append(InlineKeyboardButton("➡️ Next", callback_data=f"page:{page+1}:{query}"))
    if nav_buttons: keyboard.append(nav_buttons)

    text = f"🔍 Results for '{query}'\nPage {page+1}/{total_pages} ({total_items} results)"
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# ==========================
# Button Callback Handler
# ==========================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "none": return

    if data.startswith("page:"):
        _, page_str, search_query = data.split(":", 2)
        page = int(page_str)
        results = db.fuzzy_search(search_query)
        await show_search_results_page(query.message, search_query, results, page)

    elif data.startswith("movie:"):
        movie_key = data.split(":", 1)[1]
        await show_movie_qualities(query, movie_key)

    elif data.startswith("series:"):
        series_key = data.split(":", 1)[1]
        await show_series_seasons(query, series_key)

    elif data.startswith("get:"):
        # Extract the message_id of the file stored in MongoDB
        _, message_id = data.split(":", 1)
        
        await query.answer("Sending file...")
        try:
            # Send the file to the user without revealing the original source
            await context.bot.copy_message(
                chat_id=query.from_user.id,         # the user
                from_chat_id=PRIVATE_CHANNEL_ID,    # your private channel
                message_id=int(message_id)          # the message ID in your channel
            )
            await query.edit_message_text("✅ File sent!")
        except Exception as e:
            await query.edit_message_text(f"❌ Error sending file: {e}")


# Example helper functions for movie/series buttons
async def show_movie_qualities(query, movie_key):
    movie = db.movies.find_one({"key": movie_key})
    if not movie: return await query.edit_message_text("❌ Movie not found.")
    keyboard = [[InlineKeyboardButton(f"📥 {q}", callback_data=f"get:{d['message_id']}")] for q, d in movie["qualities"].items()]
    await query.edit_message_text(f"🎬 {movie['title']}\nSelect Quality:", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_series_seasons(query, series_key):
    s = db.series.find_one({"key": series_key})
    if not s: return await query.edit_message_text("❌ Series not found.")
    keyboard = [[InlineKeyboardButton(f"Season {sn} ({len(season)} eps)", callback_data=f"season:{series_key}:{sn}")] for sn, season in s['seasons'].items()]
    await query.edit_message_text(f"📺 {s['title']}\nSelect Season:", reply_markup=InlineKeyboardMarkup(keyboard))

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
    application.add_handler(MessageHandler((filters.Document.ALL | filters.VIDEO) & ~filters.COMMAND, handle_media_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_media))
    application.add_handler(CallbackQueryHandler(button_callback))

    print("🚀 Bot is live...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
