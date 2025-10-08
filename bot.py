import os
import re
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ContextTypes, filters
)
from difflib import SequenceMatcher
import asyncio
from flask import Flask
from threading import Thread

# Flask app for health check
app = Flask(__name__)

@app.route('/health')
def health():
    return {'status': 'ok', 'bot': 'running'}

@app.route('/')
def home():
    return 'Telegram Movie Bot is Running!'

# Configuration from environment variables
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
PRIVATE_CHANNEL_ID = int(os.environ.get("PRIVATE_CHANNEL_ID", "-1001234567890"))
ADMIN_IDS = [int(id.strip()) for id in os.environ.get("ADMIN_IDS", "123456789").split(",")]
DATABASE_FILE = "media_database.json"

class MediaDatabase:
    def __init__(self):
        self.data = self.load_database()
    
    def load_database(self):
        """Load existing database or create new one"""
        if os.path.exists(DATABASE_FILE):
            with open(DATABASE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {"movies": {}, "series": {}}
    
    def save_database(self):
        """Save database to file"""
        with open(DATABASE_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
    
    def parse_filename(self, filename):
        """Parse filename to extract title, year, quality, season, episode"""
        # Remove file extension
        name = os.path.splitext(filename)[0]
        
        # Extract quality (1080p, 720p, 480p, 4K, etc.)
        quality_match = re.search(r'(2160p|4K|1080p|720p|480p|360p)', name, re.IGNORECASE)
        quality = quality_match.group(1).upper() if quality_match else "Unknown"
        
        # Check if it's a series (contains S01E01 or similar patterns)
        season_episode = re.search(r'S(\d+)E(\d+)', name, re.IGNORECASE)
        season_only = re.search(r'S(\d+)(?!E)', name, re.IGNORECASE)
        
        if season_episode:
            season = int(season_episode.group(1))
            episode = int(season_episode.group(2))
            # Extract title (everything before season info)
            title = re.split(r'S\d+E\d+', name, flags=re.IGNORECASE)[0].strip()
            title = re.sub(r'[._-]', ' ', title).strip()
            return {
                "type": "series",
                "title": title,
                "season": season,
                "episode": episode,
                "quality": quality,
                "filename": filename
            }
        
        # Extract year
        year_match = re.search(r'(19|20)\d{2}', name)
        year = year_match.group(0) if year_match else None
        
        # Extract title (everything before year or quality)
        if year:
            title = name.split(year)[0]
        else:
            title = re.split(r'(1080p|720p|480p|4K|2160p)', name, flags=re.IGNORECASE)[0]
        
        title = re.sub(r'[._-]', ' ', title).strip()
        
        return {
            "type": "movie",
            "title": title,
            "year": year,
            "quality": quality,
            "filename": filename
        }
    
    def add_media(self, message_id, filename, file_id):
        """Add media to database"""
        parsed = self.parse_filename(filename)
        title_lower = parsed['title'].lower()
        
        if parsed['type'] == "series":
            if title_lower not in self.data['series']:
                self.data['series'][title_lower] = {
                    "title": parsed['title'],
                    "seasons": {}
                }
            
            season_num = parsed['season']
            if season_num not in self.data['series'][title_lower]['seasons']:
                self.data['series'][title_lower]['seasons'][season_num] = {}
            
            episode_num = parsed['episode']
            if episode_num not in self.data['series'][title_lower]['seasons'][season_num]:
                self.data['series'][title_lower]['seasons'][season_num][episode_num] = {}
            
            quality = parsed['quality']
            self.data['series'][title_lower]['seasons'][season_num][episode_num][quality] = {
                "message_id": message_id,
                "file_id": file_id,
                "filename": filename
            }
        else:
            if title_lower not in self.data['movies']:
                self.data['movies'][title_lower] = {
                    "title": parsed['title'],
                    "year": parsed['year'],
                    "qualities": {}
                }
            
            quality = parsed['quality']
            self.data['movies'][title_lower]['qualities'][quality] = {
                "message_id": message_id,
                "file_id": file_id,
                "filename": filename
            }
        
        self.save_database()
    
    def fuzzy_search(self, query, threshold=0.6):
        """Search with fuzzy matching for spelling mistakes"""
        query_lower = query.lower()
        results = {"movies": [], "series": []}
        
        # Search movies
        for title_key, movie_data in self.data['movies'].items():
            ratio = SequenceMatcher(None, query_lower, title_key).ratio()
            if ratio >= threshold or query_lower in title_key:
                results['movies'].append({
                    "title": movie_data['title'],
                    "key": title_key,
                    "ratio": ratio,
                    "data": movie_data
                })
        
        # Search series
        for title_key, series_data in self.data['series'].items():
            ratio = SequenceMatcher(None, query_lower, title_key).ratio()
            if ratio >= threshold or query_lower in title_key:
                results['series'].append({
                    "title": series_data['title'],
                    "key": title_key,
                    "ratio": ratio,
                    "data": series_data
                })
        
        # Sort by relevance
        results['movies'].sort(key=lambda x: x['ratio'], reverse=True)
        results['series'].sort(key=lambda x: x['ratio'], reverse=True)
        
        return results
    
    def get_file_info(self, data_type, *args):
        """Get file information from database"""
        try:
            if data_type == "movie":
                movie_key, quality = args
                return self.data['movies'][movie_key]['qualities'][quality]
            elif data_type == "episode":
                series_key, season_num, episode_num, quality = args
                return self.data['series'][series_key]['seasons'][season_num][episode_num][quality]
        except KeyError:
            return None
        return None

# Initialize database
db = MediaDatabase()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    welcome_text = (
        "🎬 Welcome to Movie Bot!\n\n"
        "Search for any movie or web series by typing the name.\n"
        "Don't worry about spelling mistakes, I'll find what you're looking for!\n\n"
        "Example: Just type 'breaking bad' or 'interstellar'\n\n"
        "Commands:\n"
        "/start - Show this message\n"
        "/stats - Show database statistics"
    )
    
    if update.message.from_user.id in ADMIN_IDS:
        welcome_text += "\n\n🔧 Admin Commands:\n/index - Index new files from channel"
    
    await update.message.reply_text(welcome_text)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show database statistics"""
    movie_count = len(db.data['movies'])
    series_count = len(db.data['series'])
    
    total_episodes = 0
    for series in db.data['series'].values():
        for season in series['seasons'].values():
            total_episodes += len(season)
    
    stats_text = (
        f"📊 Database Statistics:\n\n"
        f"🎬 Movies: {movie_count}\n"
        f"📺 Series: {series_count}\n"
        f"📝 Total Episodes: {total_episodes}\n\n"
        f"🔧 Debug Info:\n"
        f"Bot ID: {context.bot.id}\n"
        f"Your ID: {update.message.from_user.id}\n"
        f"Admin Status: {'Yes ✅' if update.message.from_user.id in ADMIN_IDS else 'No ❌'}"
    )
    
    await update.message.reply_text(stats_text)

async def index_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Index all files from private channel (Admin only)"""
    if update.message.from_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ This command is for admins only!")
        return
    
    await update.message.reply_text(
        "📝 Manual Indexing Instructions:\n\n"
        "1. Forward media files (videos/documents) from your channel to this bot\n"
        "2. The bot will automatically index them\n"
        "3. Each file will be confirmed as it's indexed\n\n"
        "Note: Make sure the bot is an admin in your private channel!"
    )

async def handle_media_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle any media message (forwarded or direct) for indexing"""
    # Check if update has message
    if not update.message:
        return
    
    # Check if user is admin
    if update.message.from_user.id not in ADMIN_IDS:
        await update.message.reply_text(f"❌ Not authorized to index files.\n\nYour ID: {update.message.from_user.id}")
        return
    
    # Get file info from document or video
    file_obj = None
    filename = None
    
    if update.message.document:
        file_obj = update.message.document
        filename = file_obj.file_name
    elif update.message.video:
        file_obj = update.message.video
        filename = file_obj.file_name or f"video_{update.message.message_id}.mp4"
    
    # If no media, ignore
    if not file_obj or not filename:
        return
    
    # 💡 Safely detect forwarded or direct uploads (handles all cases)
    fwd_chat = getattr(update.message, "forward_from_chat", None) or getattr(update.message, "sender_chat", None)
    fwd_msg_id = getattr(update.message, "forward_from_message_id", None)
    
    if fwd_chat and fwd_chat.id == PRIVATE_CHANNEL_ID and fwd_msg_id:
        # ✅ Forwarded from your own private channel
        db.add_media(
            fwd_msg_id,
            filename,
            file_obj.file_id
        )
        print(f"Indexed existing message from private channel: {filename}")
    
    else:
        # 💡 Not from your private channel — forward and index
        try:
            forwarded = await context.bot.forward_message(
                chat_id=PRIVATE_CHANNEL_ID,
                from_chat_id=update.message.chat_id,
                message_id=update.message.message_id
            )
            db.add_media(
                forwarded.message_id,
                filename,
                file_obj.file_id
            )
            print(f"✅ Forwarded and indexed: {filename}")
        except Exception as e:
            print(f"❌ Failed to forward or index {filename}: {e}")

            await update.message.reply_text(
                f"✅ Forwarded and indexed: {filename}\n"
                f"Message ID: {forwarded.message_id}"
            )
        except Exception as e:
            await update.message.reply_text(
                f"❌ Error: {str(e)}\n\n"
                f"Make sure:\n"
                f"1. Bot is admin in channel\n"
                f"2. Channel ID is correct: {PRIVATE_CHANNEL_ID}"
            )

async def search_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search for movies/series"""
    # Check if update has message
    if not update.message or not update.message.text:
        return
    
    query = update.message.text.strip()
    
    # Ignore commands
    if query.startswith('/'):
        return
    
    if len(query) < 2:
        await update.message.reply_text("Please enter at least 2 characters to search.")
        return
    
    results = db.fuzzy_search(query)
    
    if not results['movies'] and not results['series']:
        await update.message.reply_text(
            f"❌ No results found for '{query}'.\n"
            "Try a different spelling or search term."
        )
        return
    
    # Create keyboard with results
    keyboard = []
    
    if results['movies']:
        keyboard.append([InlineKeyboardButton("🎬 MOVIES", callback_data="none")])
        for movie in results['movies'][:10]:  # Limit to 10 results
            year_text = f" ({movie['data']['year']})" if movie['data'].get('year') else ""
            button_text = f"{movie['title']}{year_text}"
            keyboard.append([InlineKeyboardButton(
                button_text, 
                callback_data=f"movie:{movie['key']}"
            )])
    
    if results['series']:
        if keyboard:
            keyboard.append([InlineKeyboardButton("─────────", callback_data="none")])
        keyboard.append([InlineKeyboardButton("📺 WEB SERIES", callback_data="none")])
        for series in results['series'][:10]:
            keyboard.append([InlineKeyboardButton(
                series['title'], 
                callback_data=f"series:{series['key']}"
            )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"🔍 Search results for '{query}':",
        reply_markup=reply_markup
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button clicks"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "none":
        return
    
    if data.startswith("movie:"):
        movie_key = data.split(":", 1)[1]
        await show_movie_qualities(query, movie_key)
    
    elif data.startswith("series:"):
        series_key = data.split(":", 1)[1]
        await show_series_seasons(query, series_key)
    
    elif data.startswith("season:"):
        _, series_key, season_num = data.split(":", 2)
        await show_season_episodes(query, series_key, int(season_num))
    
    elif data.startswith("episode:"):
        _, series_key, season_num, episode_num = data.split(":", 3)
        await show_episode_qualities(query, series_key, int(season_num), int(episode_num))
    
    elif data.startswith("get:"):
        # Send the file directly to the user
        parts = data.split(":")
        
        try:
            # Edit message to show processing
            await query.edit_message_text("📤 Sending file, please wait...")
            
            if len(parts) == 2:
                # Simple format: get:message_id
                _, message_id = parts
                file_info = None
            else:
                # Extended format with file info
                message_id = parts[1]
                file_info = None
            
            # Try to forward the message from private channel
            try:
                forwarded_message = await context.bot.forward_message(
                    chat_id=query.from_user.id,
                    from_chat_id=PRIVATE_CHANNEL_ID,
                    message_id=int(message_id)
                )
                await query.edit_message_text("✅ File sent successfully! Check your messages above.")
                
            except Exception as forward_error:
                # If forwarding fails, try copying the message
                try:
                    copied_message = await context.bot.copy_message(
                        chat_id=query.from_user.id,
                        from_chat_id=PRIVATE_CHANNEL_ID,
                        message_id=int(message_id)
                    )
                    await query.edit_message_text("✅ File sent successfully! Check your messages above.")
                    
                except Exception as copy_error:
                    # If both methods fail, show error
                    await query.edit_message_text(
                        f"❌ Error sending file.\n\n"
                        "This might happen if:\n"
                        "1. The file was deleted from the channel\n"
                        "2. The bot lost admin access to the channel\n"
                        "3. File size exceeds Telegram limits\n\n"
                        "Please contact the admin."
                    )
                    
        except Exception as e:
            await query.edit_message_text(f"❌ Unexpected error: {str(e)}")
    
    elif data.startswith("back:"):
        back_to = data.split(":", 1)[1]
        if back_to.startswith("movie:"):
            movie_key = back_to.split(":", 1)[1]
            await show_movie_qualities(query, movie_key)
        elif back_to.startswith("series:"):
            series_key = back_to.split(":", 1)[1]
            await show_series_seasons(query, series_key)
        elif back_to.startswith("season:"):
            _, series_key, season_num = back_to.split(":", 2)
            await show_season_episodes(query, series_key, int(season_num))

async def show_movie_qualities(query, movie_key):
    """Show available qualities for a movie"""
    if movie_key not in db.data['movies']:
        await query.edit_message_text("❌ Movie not found in database.")
        return
    
    movie = db.data['movies'][movie_key]
    
    keyboard = []
    for quality, file_data in sorted(movie['qualities'].items(), reverse=True):
        keyboard.append([InlineKeyboardButton(
            f"📥 {quality}",
            callback_data=f"get:{file_data['message_id']}"
        )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    year_text = f" ({movie['year']})" if movie.get('year') else ""
    
    await query.edit_message_text(
        f"🎬 {movie['title']}{year_text}\n\nSelect Quality:",
        reply_markup=reply_markup
    )

async def show_series_seasons(query, series_key):
    """Show available seasons for a series"""
    if series_key not in db.data['series']:
        await query.edit_message_text("❌ Series not found in database.")
        return
    
    series = db.data['series'][series_key]
    
    keyboard = []
    for season_num in sorted(series['seasons'].keys()):
        episode_count = len(series['seasons'][season_num])
        keyboard.append([InlineKeyboardButton(
            f"Season {season_num} ({episode_count} episodes)",
            callback_data=f"season:{series_key}:{season_num}"
        )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"📺 {series['title']}\n\nSelect Season:",
        reply_markup=reply_markup
    )

async def show_season_episodes(query, series_key, season_num):
    """Show available episodes for a season"""
    if series_key not in db.data['series']:
        await query.edit_message_text("❌ Series not found in database.")
        return
    
    series = db.data['series'][series_key]
    
    if season_num not in series['seasons']:
        await query.edit_message_text("❌ Season not found in database.")
        return
    
    season = series['seasons'][season_num]
    
    keyboard = []
    for episode_num in sorted(season.keys()):
        keyboard.append([InlineKeyboardButton(
            f"Episode {episode_num}",
            callback_data=f"episode:{series_key}:{season_num}:{episode_num}"
        )])
    
    # Add back button
    keyboard.append([InlineKeyboardButton(
        "⬅️ Back to Seasons",
        callback_data=f"back:series:{series_key}"
    )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"📺 {series['title']} - Season {season_num}\n\nSelect Episode:",
        reply_markup=reply_markup
    )

async def show_episode_qualities(query, series_key, season_num, episode_num):
    """Show available qualities for an episode"""
    if series_key not in db.data['series']:
        await query.edit_message_text("❌ Series not found in database.")
        return
    
    series = db.data['series'][series_key]
    
    if season_num not in series['seasons'] or episode_num not in series['seasons'][season_num]:
        await query.edit_message_text("❌ Episode not found in database.")
        return
    
    episode = series['seasons'][season_num][episode_num]
    
    keyboard = []
    for quality, file_data in sorted(episode.items(), reverse=True):
        keyboard.append([InlineKeyboardButton(
            f"📥 {quality}",
            callback_data=f"get:{file_data['message_id']}"
        )])
    
    # Add back button
    keyboard.append([InlineKeyboardButton(
        "⬅️ Back to Episodes",
        callback_data=f"back:season:{series_key}:{season_num}"
    )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"📺 {series['title']}\nS{season_num:02d}E{episode_num:02d}\n\nSelect Quality:",
        reply_markup=reply_markup
    )

def main():
    """Start the bot"""
    try:
        # Start Flask server in a separate thread for health checks
        port = int(os.environ.get('PORT', 10000))
        flask_thread = Thread(target=lambda: app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False))
        flask_thread.daemon = True
        flask_thread.start()
        print(f"Flask server started on port {port}")
        
        # Validate environment variables
        if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
            print("❌ Error: Please set the BOT_TOKEN environment variable")
            return
        
        print(f"Starting bot...")
        print(f"Admin IDs: {ADMIN_IDS}")
        print(f"Private Channel ID: {PRIVATE_CHANNEL_ID}")
        
        # Start Telegram bot
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("stats", stats))
        application.add_handler(CommandHandler("index", index_channel))
        
        # Media handler for admins
        application.add_handler(MessageHandler(
            (filters.Document.ALL | filters.VIDEO) & ~filters.COMMAND, 
            handle_media_message
        ))
        
        # Text handler for searches
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, 
            search_media
        ))
        
        # Callback handler for buttons
        application.add_handler(CallbackQueryHandler(button_callback))
        
        print("✅ Bot is running...")
        print("Press Ctrl+C to stop")
        
        # Start polling
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        
    except Exception as e:
        print(f"❌ Error starting bot: {e}")
        raise

if __name__ == "__main__":
    main()
