import os
import re
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from difflib import SequenceMatcher
from pymongo import MongoClient
import asyncio
from flask import Flask, send_file
from threading import Thread

# ==============================
# Flask server for health checks
# ==============================
app = Flask(__name__)

@app.route('/')
def home():
    return '🎬 Telegram Movie Bot is running!'

@app.route('/health')
def health():
    return {'status': 'ok', 'bot': 'running'}

@app.route("/download-db")
def download_db():
    try:
        return send_file("database.json", as_attachment=True)
    except Exception as e:
        return str(e)

# ==============================
# Environment variables
# ==============================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
PRIVATE_CHANNEL_ID = int(os.environ.get("PRIVATE_CHANNEL_ID", "-1001234567890"))
ADMIN_IDS = [int(id.strip()) for id in os.environ.get("ADMIN_IDS", "123456789").split(",")]
MONGO_URI = os.environ.get("MONGO_URI", "YOUR_MONGO_CONNECTION_STRING_HERE")
DB_NAME = "MovieBot"

# Pagination settings
ITEMS_PER_PAGE = 8  # Number of items to show per page

# ==============================
# Database Class (MongoDB)
# ==============================
class MediaDatabase:
    def __init__(self):
        self.client = MongoClient(MONGO_URI, tls=True, tlsAllowInvalidCertificates=True)
        self.db = self.client[DB_NAME]
        self.movies = self.db["movies"]
        self.series = self.db["series"]

    # Parse filenames to extract metadata
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
            return {
                "type": "series",
                "title": title,
                "season": int(season_episode.group(1)),
                "episode": int(season_episode.group(2)),
                "quality": quality,
                "filename": filename
            }
        else:
            return {
                "type": "movie",
                "title": title,
                "year": year,
                "quality": quality,
                "filename": filename
            }

    # Add file info to MongoDB
    def add_media(self, message_id, filename, file_id):
        parsed = self.parse_filename(filename)
        title_lower = parsed['title'].lower()

        if parsed['type'] == "series":
            series_doc = self.series.find_one({"key": title_lower}) or {
                "key": title_lower,
                "title": parsed['title'],
                "seasons": {}
            }
            seasons = series_doc.get("seasons", {})
            s = str(parsed['season'])
            e = str(parsed['episode'])
            q = parsed['quality']
            seasons.setdefault(s, {}).setdefault(e, {})[q] = {
                "message_id": message_id,
                "file_id": file_id,
                "filename": filename
            }
            self.series.update_one(
                {"key": title_lower},
                {"$set": {"seasons": seasons, "title": parsed['title']}},
                upsert=True
            )
        else:
            movie_doc = self.movies.find_one({"key": title_lower}) or {
                "key": title_lower,
                "title": parsed['title'],
                "year": parsed['year'],
                "qualities": {}
            }
            q = parsed['quality']
            movie_doc["qualities"][q] = {
                "message_id": message_id,
                "file_id": file_id,
                "filename": filename
            }
            self.movies.update_one(
                {"key": title_lower},
                {"$set": movie_doc},
                upsert=True
            )

    # Fuzzy search for titles
    def fuzzy_search(self, query, threshold=0.6):
        query_lower = query.lower()
        results = {"movies": [], "series": []}

        for movie in self.movies.find():
            ratio = SequenceMatcher(None, query_lower, movie["key"]).ratio()
            if ratio >= threshold or query_lower in movie["key"]:
                results["movies"].append({
                    "title": movie["title"],
                    "key": movie["key"],
                    "ratio": ratio,
                    "data": movie
                })

        for s in self.series.find():
            ratio = SequenceMatcher(None, query_lower, s["key"]).ratio()
            if ratio >= threshold or query_lower in s["key"]:
                results["series"].append({
                    "title": s["title"],
                    "key": s["key"],
                    "ratio": ratio,
                    "data": s
                })

        results["movies"].sort(key=lambda x: x["ratio"], reverse=True)
        results["series"].sort(key=lambda x: x["ratio"], reverse=True)
        return results

    # Retrieve file info
    def get_file_info(self, data_type, *args):
        if data_type == "movie":
            movie_key, quality = args
            movie = self.movies.find_one({"key": movie_key})
            return movie["qualities"].get(quality) if movie else None
        elif data_type == "episode":
            series_key, season_num, episode_num, quality = args
            s = self.series.find_one({"key": series_key})
            try:
                return s["seasons"][str(season_num)][str(episode_num)][quality]
            except Exception:
                return None

    # Get all available qualities for a season
    def get_season_qualities(self, series_key, season_num):
        s = self.series.find_one({"key": series_key})
        if not s:
            return []
        season = s['seasons'].get(str(season_num), {})
        qualities = set()
        for episode in season.values():
            qualities.update(episode.keys())
        return sorted(qualities)


# ==============================
# Initialize DB
# ==============================
db = MediaDatabase()

# ==============================
# Bot Handlers
# ==============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎬 Welcome to Movie Bot!\n\n"
        "Search for any movie or web series by typing its name.\n"
        "Example: `breaking bad` or `interstellar`\n\n"
        "/stats - Show database info\n"
    )
    if update.message.from_user.id in ADMIN_IDS:
        text += "\n🔧 /index - Manual index mode"
    await update.message.reply_text(text)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    movie_count = db.movies.count_documents({})
    series_count = db.series.count_documents({})
    await update.message.reply_text(
        f"📊 Movies: {movie_count}\n📺 Series: {series_count}"
    )

async def index_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id not in ADMIN_IDS:
        return await update.message.reply_text("❌ Admins only!")
    await update.message.reply_text(
        "📝 Forward files here from your private channel to index them automatically."
    )

async def handle_media_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id not in ADMIN_IDS:
        return await update.message.reply_text("❌ Not authorized to index files.")
    file_obj = update.message.document or update.message.video
    if not file_obj:
        return
    filename = file_obj.file_name or f"video_{update.message.message_id}.mp4"
    fwd_chat = getattr(update.message, "forward_from_chat", None)
    fwd_msg_id = getattr(update.message, "forward_from_message_id", None)
    if fwd_chat and fwd_chat.id == PRIVATE_CHANNEL_ID and fwd_msg_id:
        db.add_media(fwd_msg_id, filename, file_obj.file_id)
        print(f"✅ Indexed existing file: {filename}")
    else:
        try:
            forwarded = await context.bot.forward_message(
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
    
    # Show first page
    await show_search_results_page(update.message, query, results, 0)

async def show_search_results_page(message, query, results, page):
    """Display paginated search results"""
    all_items = []
    
    # Combine movies and series with type indicator
    for m in results['movies']:
        all_items.append(('movie', m))
    for s in results['series']:
        all_items.append(('series', s))
    
    total_items = len(all_items)
    total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    
    # Calculate slice for current page
    start_idx = page * ITEMS_PER_PAGE
    end_idx = min(start_idx + ITEMS_PER_PAGE, total_items)
    page_items = all_items[start_idx:end_idx]
    
    keyboard = []
    
    # Add "Send All on This Page" button
    if page_items:
        keyboard.append([InlineKeyboardButton("📦 Send All on This Page", callback_data=f"sendpage:{page}:{query}")])
    
    # Add items for current page
    has_movies = False
    has_series = False
    
    for item_type, item in page_items:
        if item_type == 'movie' and not has_movies:
            keyboard.append([InlineKeyboardButton("🎬 MOVIES", callback_data="none")])
            has_movies = True
        elif item_type == 'series' and not has_series:
            keyboard.append([InlineKeyboardButton("📺 SERIES", callback_data="none")])
            has_series = True
        
        if item_type == 'movie':
            keyboard.append([InlineKeyboardButton(
                item['title'], 
                callback_data=f"movie:{item['key']}"
            )])
        else:
            keyboard.append([InlineKeyboardButton(
                item['title'], 
                callback_data=f"series:{item['key']}"
            )])
    
    # Add pagination buttons
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"page:{page-1}:{query}"))
    
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("➡️ Next", callback_data=f"page:{page+1}:{query}"))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    # Add page indicator
    page_info = f"Page {page + 1}/{total_pages}"
    
    text = f"🔍 Results for '{query}'\n{page_info} ({total_items} total results)"
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Send or edit message
    if hasattr(message, 'edit_text'):
        await message.edit_text(text, reply_markup=reply_markup)
    else:
        await message.reply_text(text, reply_markup=reply_markup)

# ==============================
# Button Callback Handlers
# ==============================
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

    elif data.startswith("sendpage:"):
        _, page_str, search_query = data.split(":", 2)
        page = int(page_str)
        await send_all_on_page(query, context, search_query, page)

    elif data.startswith("movie:"):
        movie_key = data.split(":", 1)[1]
        await show_movie_qualities(query, movie_key)

    elif data.startswith("series:"):
        series_key = data.split(":", 1)[1]
        await show_series_seasons(query, series_key)

    elif data.startswith("season:"):
        _, series_key, season_num = data.split(":", 2)
        await show_season_episodes(query, series_key, int(season_num))

    elif data.startswith("sendall:"):
        _, series_key, season_num = data.split(":", 2)
        await show_sendall_qualities(query, series_key, int(season_num))

    elif data.startswith("sendallq:"):
        _, series_key, season_num, quality = data.split(":", 3)
        await send_all_episodes(query, context, series_key, int(season_num), quality)

    elif data.startswith("episode:"):
        _, series_key, season_num, episode_num = data.split(":", 3)
        await show_episode_qualities(query, series_key, int(season_num), int(episode_num))

    elif data.startswith("get:"):
        _, message_id = data.split(":", 1)
    
        # Extract the previous "Back" button callback before changing the message
        prev_keyboard = query.message.reply_markup.inline_keyboard if query.message.reply_markup else []
        back_callback = None
        for row in prev_keyboard:
            for btn in row:
                if "Back to Episodes" in btn.text or "Back to" in btn.text:
                    back_callback = btn.callback_data
                    break
            if back_callback:
                break
    
        await query.edit_message_text("📤 Sending file...")
    
        try:
            await context.bot.copy_message(
                chat_id=query.from_user.id,
                from_chat_id=PRIVATE_CHANNEL_ID,
                message_id=int(message_id)
            )
    
            # Show "File sent!" and restore the back button
            keyboard = []
            if back_callback:
                keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data=back_callback)])
    
            await query.edit_message_text(
                "✅ File sent! You can select another quality or go back:",
                reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
            )
    
            # Remove buttons after 60 seconds
            await asyncio.sleep(60)
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except:
                pass
    
        except Exception as e:
            await query.edit_message_text(f"❌ Error: {e}")

async def send_all_on_page(query, context, search_query, page):
    """Send all files visible on the current search results page"""
    results = db.fuzzy_search(search_query)
    
    all_items = []
    for m in results['movies']:
        all_items.append(('movie', m))
    for s in results['series']:
        all_items.append(('series', s))
    
    # Get items for current page
    start_idx = page * ITEMS_PER_PAGE
    end_idx = min(start_idx + ITEMS_PER_PAGE, len(all_items))
    page_items = all_items[start_idx:end_idx]
    
    await query.edit_message_text(
        f"📤 Sending all files on page {page + 1}...\n\nPlease wait, this may take a moment."
    )
    
    sent_count = 0
    failed_count = 0
    
    for item_type, item in page_items:
        try:
            if item_type == 'movie':
                # Send all qualities of the movie
                movie = db.movies.find_one({"key": item['key']})
                for quality, data in movie['qualities'].items():
                    try:
                        await context.bot.copy_message(
                            chat_id=query.from_user.id,
                            from_chat_id=PRIVATE_CHANNEL_ID,
                            message_id=data['message_id']
                        )
                        sent_count += 1
                        await asyncio.sleep(0.5)
                    except Exception as e:
                        print(f"❌ Error sending movie {item['title']} ({quality}): {e}")
                        failed_count += 1
            
            else:  # series
                # Send first episode of first season in best quality
                s = db.series.find_one({"key": item['key']})
                if s and s['seasons']:
                    first_season = sorted(s['seasons'].keys(), key=lambda x: int(x))[0]
                    season = s['seasons'][first_season]
                    if season:
                        first_ep = sorted(season.keys(), key=lambda x: int(x))[0]
                        episode = season[first_ep]
                        # Get best quality (prefer 1080p, then 720p, then others)
                        quality_priority = ['1080P', '720P', '480P', '4K', '2160P']
                        quality = None
                        for q in quality_priority:
                            if q in episode:
                                quality = q
                                break
                        if not quality:
                            quality = list(episode.keys())[0]
                        
                        try:
                            await context.bot.copy_message(
                                chat_id=query.from_user.id,
                                from_chat_id=PRIVATE_CHANNEL_ID,
                                message_id=episode[quality]['message_id']
                            )
                            sent_count += 1
                            await asyncio.sleep(0.5)
                        except Exception as e:
                            print(f"❌ Error sending series {item['title']}: {e}")
                            failed_count += 1
        
        except Exception as e:
            print(f"❌ Error processing {item['title']}: {e}")
            failed_count += 1
    
    # Show completion message with navigation back
    status_text = f"✅ Successfully sent {sent_count} files!"
    if failed_count > 0:
        status_text += f"\n⚠️ {failed_count} files failed to send."
    
    keyboard = [[InlineKeyboardButton("⬅️ Back to Results", callback_data=f"page:{page}:{search_query}")]]
    
    await query.edit_message_text(
        status_text + "\n\nYou can go back to search results:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    # Remove buttons after 60 seconds
    await asyncio.sleep(60)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except:
        pass

# ==============================
# Inline Display Functions
# ==============================
async def show_movie_qualities(query, movie_key):
    movie = db.movies.find_one({"key": movie_key})
    if not movie:
        return await query.edit_message_text("❌ Movie not found.")
    keyboard = [
        [InlineKeyboardButton(f"📥 {q}", callback_data=f"get:{d['message_id']}")]
        for q, d in movie['qualities'].items()
    ]
    await query.edit_message_text(
        f"🎬 {movie['title']}\nSelect Quality:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_series_seasons(query, series_key):
    s = db.series.find_one({"key": series_key})
    if not s:
        return await query.edit_message_text("❌ Series not found.")
    keyboard = []
    for sn, season in s['seasons'].items():
        ep_count = len(season)
        keyboard.append([InlineKeyboardButton(f"Season {sn} ({ep_count} eps)", callback_data=f"season:{series_key}:{sn}")])
    await query.edit_message_text(
        f"📺 {s['title']}\nSelect Season:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_season_episodes(query, series_key, season_num):
    s = db.series.find_one({"key": series_key})
    season = s['seasons'].get(str(season_num), {})
    keyboard = []
    
    # Add "Send All" button at the top
    keyboard.append([InlineKeyboardButton("📦 Send All Episodes", callback_data=f"sendall:{series_key}:{season_num}")])
    
    # Add individual episode buttons
    for ep in sorted(season.keys(), key=lambda x: int(x)):
        keyboard.append([InlineKeyboardButton(f"Episode {ep}", callback_data=f"episode:{series_key}:{season_num}:{ep}")])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back to Seasons", callback_data=f"series:{series_key}")])
    await query.edit_message_text(
        f"📺 {s['title']} - Season {season_num}\nSelect Episode or Send All:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_sendall_qualities(query, series_key, season_num):
    """Show quality options for sending all episodes"""
    qualities = db.get_season_qualities(series_key, season_num)
    if not qualities:
        return await query.edit_message_text("❌ No episodes found for this season.")
    
    s = db.series.find_one({"key": series_key})
    keyboard = []
    
    for q in qualities:
        keyboard.append([InlineKeyboardButton(f"📥 Send All in {q}", callback_data=f"sendallq:{series_key}:{season_num}:{q}")])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back to Episodes", callback_data=f"season:{series_key}:{season_num}")])
    
    await query.edit_message_text(
        f"📺 {s['title']} - Season {season_num}\nSelect Quality for All Episodes:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def send_all_episodes(query, context, series_key, season_num, quality):
    """Send all episodes of a season in the selected quality"""
    s = db.series.find_one({"key": series_key})
    if not s:
        return await query.edit_message_text("❌ Series not found.")
    
    season = s['seasons'].get(str(season_num), {})
    episodes_to_send = []
    
    # Collect all episodes that have the selected quality
    for ep_num in sorted(season.keys(), key=lambda x: int(x)):
        episode = season[ep_num]
        if quality in episode:
            episodes_to_send.append((int(ep_num), episode[quality]['message_id']))
    
    if not episodes_to_send:
        return await query.edit_message_text(f"❌ No episodes found in {quality} quality.")
    
    # Update message to show progress
    keyboard = [[InlineKeyboardButton("⬅️ Back to Episodes", callback_data=f"season:{series_key}:{season_num}")]]
    await query.edit_message_text(
        f"📤 Sending {len(episodes_to_send)} episodes in {quality}...\n\nPlease wait, this may take a moment.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    # Send all episodes in order
    sent_count = 0
    failed_count = 0
    
    for ep_num, message_id in episodes_to_send:
        try:
            await context.bot.copy_message(
                chat_id=query.from_user.id,
                from_chat_id=PRIVATE_CHANNEL_ID,
                message_id=message_id
            )
            sent_count += 1
            # Small delay to avoid hitting rate limits
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"❌ Error sending episode {ep_num}: {e}")
            failed_count += 1
    
    # Update with final status
    status_text = f"✅ Successfully sent {sent_count} episodes in {quality}!"
    if failed_count > 0:
        status_text += f"\n⚠️ {failed_count} episodes failed to send."
    
    await query.edit_message_text(
        status_text + "\n\nYou can select another option:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    # Remove buttons after 60 seconds
    await asyncio.sleep(60)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except:
        pass

async def show_episode_qualities(query, series_key, season_num, episode_num):
    s = db.series.find_one({"key": series_key})
    episode = s['seasons'][str(season_num)][str(episode_num)]
    keyboard = [
        [InlineKeyboardButton(f"📥 {q}", callback_data=f"get:{d['message_id']}")]
        for q, d in episode.items()
    ]
    keyboard.append([InlineKeyboardButton("⬅️ Back to Episodes", callback_data=f"season:{series_key}:{season_num}")])
    await query.edit_message_text(
        f"📺 {s['title']} S{season_num:02d}E{episode_num:02d}\nSelect Quality:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ==============================
# Main Entrypoint
# ==============================
def main():
    port = int(os.environ.get("PORT", 10000))
    flask_thread = Thread(target=lambda: app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False))
    flask_thread.daemon = True
    flask_thread.start()
    print(f"🌐 Flask running on port {port}")

    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Error: Missing BOT_TOKEN")
        return

    print("🚀 Starting bot...")
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("index", index_channel))
    application.add_handler(MessageHandler((filters.Document.ALL | filters.VIDEO) & ~filters.COMMAND, handle_media_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_media))
    application.add_handler(CallbackQueryHandler(button_callback))
    print("✅ Bot is live and polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
