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
        f"📝 Total Episodes: {total_episodes}"
    )
    
    await update.message.reply_text(stats_text)

async def index_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Index all files from private channel (Admin only)"""
    if update.message.from_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ This command is for admins only!")
        return
    
    msg = await update.message.reply_text("🔄 Starting indexing process...\nThis may take a while.")
    
    try:
        indexed_count = 0
        chat = await context.bot.get_chat(PRIVATE_CHANNEL_ID)
        
        # Iterate through channel messages
        offset_id = 0
        limit = 100
        
        while True:
            try:
                # This is a workaround since get_chat_history doesn't exist
                # We'll need to use a different approach
                await msg.edit_text(
                    f"⚠️ Note: Auto-indexing requires manual forwarding.\n\n"
                    f"Please forward messages from your channel to this bot.\n"
                    f"The bot will automatically index them.\n\n"
                    f"Or use the manual indexing method below."
                )
                break
            except Exception as e:
                await msg.edit_text(f"❌ Error: {str(e)}")
                break
        
    except Exception as e:
        await msg.edit_text(f"❌ Error during indexing: {str(e)}")

async def handle_forwarded_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle forwarded messages for indexing"""
    if update.message.from_user.id not in ADMIN_IDS:
        return
    
    # Check if message is forwarded from the private channel
    if not update.message.forward_from_chat:
        return
    
    if update.message.forward_from_chat.id != PRIVATE_CHANNEL_ID:
        return
    
    # Get file info
    file_obj = None
    filename = None
    
    if update.message.document:
        file_obj = update.message.document
        filename = file_obj.file_name
    elif update.message.video:
        file_obj = update.message.video
        filename = file_obj.file_name or f"video_{update.message.forward_from_message_id}"
    
    if file_obj and filename:
        db.add_media(
            update.message.forward_from_message_id,
            filename,
            file_obj.file_id
        )
        await update.message.reply_text(f"✅ Indexed: {filename}")

async def search_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search for movies/series"""
    query = update.message.text.strip()
    
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
        # Send the actual file link
        _, message_id = data.split(":", 1)
        link = f"https://t.me/c/{str(PRIVATE_CHANNEL_ID)[4:]}/{message_id}"
        
        keyboard = [[InlineKeyboardButton("📥 Download", url=link)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "Click the button below to access the file:",
            reply_markup=reply_markup
        )
    
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
    series = db.data['series'][series_key]
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
    series = db.data['series'][series_key]
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
        f"📺 {series['title']}\nS{season_num}E{episode_num}\n\nSelect Quality:",
        reply_markup=reply_markup
    )

def main():
    """Start the bot"""
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("index", index_channel))
    application.add_handler(MessageHandler(filters.FORWARDED & ~filters.COMMAND, handle_forwarded_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_media))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    print("Bot is running...")
    application.run_polling()

if __name__ == "__main__":
    main()
