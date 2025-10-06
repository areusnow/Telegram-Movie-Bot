import os
import logging
import json
import sqlite3
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    InlineQueryHandler,
    ContextTypes,
    filters
)
from telegram.constants import ParseMode
import uuid

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class MediaDatabase:
    """Handle all database operations"""
    
    def __init__(self, db_name='media_bot.db'):
        self.db_name = db_name
        self.init_db()
    
    def init_db(self):
        """Initialize database with tables"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # Media table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                type TEXT NOT NULL,
                year INTEGER,
                language TEXT,
                quality TEXT,
                size TEXT,
                file_id TEXT NOT NULL,
                thumbnail_id TEXT,
                description TEXT,
                genre TEXT,
                imdb_rating TEXT,
                uploaded_by INTEGER,
                upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Search index for faster queries
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_title ON media(title)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_type ON media(type)
        ''')
        
        # Admin users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Statistics table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                query TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def add_media(self, media_data):
        """Add new media to database"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO media (title, type, year, language, quality, size, 
                             file_id, thumbnail_id, description, genre, 
                             imdb_rating, uploaded_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            media_data['title'],
            media_data['type'],
            media_data.get('year'),
            media_data.get('language'),
            media_data.get('quality'),
            media_data.get('size'),
            media_data['file_id'],
            media_data.get('thumbnail_id'),
            media_data.get('description'),
            media_data.get('genre'),
            media_data.get('imdb_rating'),
            media_data.get('uploaded_by')
        ))
        
        conn.commit()
        media_id = cursor.lastrowid
        conn.close()
        return media_id
    
    def search_media(self, query, media_type=None, limit=10):
        """Search media by title"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        query_lower = f"%{query.lower()}%"
        
        if media_type:
            cursor.execute('''
                SELECT * FROM media 
                WHERE LOWER(title) LIKE ? AND type = ?
                ORDER BY upload_date DESC
                LIMIT ?
            ''', (query_lower, media_type, limit))
        else:
            cursor.execute('''
                SELECT * FROM media 
                WHERE LOWER(title) LIKE ?
                ORDER BY upload_date DESC
                LIMIT ?
            ''', (query_lower, limit))
        
        results = cursor.fetchall()
        conn.close()
        
        # Convert to list of dictionaries
        columns = ['id', 'title', 'type', 'year', 'language', 'quality', 
                  'size', 'file_id', 'thumbnail_id', 'description', 'genre', 
                  'imdb_rating', 'uploaded_by', 'upload_date']
        
        return [dict(zip(columns, row)) for row in results]
    
    def get_media_by_id(self, media_id):
        """Get specific media by ID"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM media WHERE id = ?', (media_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            columns = ['id', 'title', 'type', 'year', 'language', 'quality', 
                      'size', 'file_id', 'thumbnail_id', 'description', 'genre', 
                      'imdb_rating', 'uploaded_by', 'upload_date']
            return dict(zip(columns, result))
        return None
    
    def log_search(self, user_id, query):
        """Log search queries for analytics"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('INSERT INTO stats (user_id, query) VALUES (?, ?)', 
                      (user_id, query))
        conn.commit()
        conn.close()
    
    def is_admin(self, user_id):
        """Check if user is admin"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM admins WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result is not None
    
    def add_admin(self, user_id, username):
        """Add admin user"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO admins (user_id, username) VALUES (?, ?)', 
                      (user_id, username))
        conn.commit()
        conn.close()
    
    def get_stats(self):
        """Get bot statistics"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM media')
        total_media = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(DISTINCT user_id) FROM stats')
        total_users = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM stats')
        total_searches = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_media': total_media,
            'total_users': total_users,
            'total_searches': total_searches
        }


class MediaBot:
    def __init__(self, token: str, admin_ids: list = None):
        self.token = token
        self.db = MediaDatabase()
        self.app = Application.builder().token(token).build()
        self.admin_ids = admin_ids or []
        self.upload_cache = {}  # Temporary storage for upload process
        self.setup_handlers()
        
        # Add initial admins to database
        for admin_id in self.admin_ids:
            self.db.add_admin(admin_id, "admin")
    
    def setup_handlers(self):
        """Set up all handlers"""
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("help", self.help_command))
        self.app.add_handler(CommandHandler("upload", self.upload_command))
        self.app.add_handler(CommandHandler("stats", self.stats_command))
        self.app.add_handler(CommandHandler("cancel", self.cancel_command))
        
        # File handlers
        self.app.add_handler(MessageHandler(
            filters.Document.ALL | filters.VIDEO, 
            self.handle_file
        ))
        
        # Text message handler for search
        self.app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, 
            self.handle_search
        ))
        
        # Callback query handler for buttons
        self.app.add_handler(CallbackQueryHandler(self.handle_callback))
        
        # Inline query handler
        self.app.add_handler(InlineQueryHandler(self.inline_search))
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user
        
        welcome_text = f"""
🎬 <b>Welcome to Media Search Bot!</b>

Hello {user.first_name}! 👋

🔍 <b>How to Search:</b>
Just type the name of any movie or series and I'll find it for you!

📤 <b>For Admins:</b>
Use /upload to add new content

💡 <b>Inline Mode:</b>
Use @YourBotUsername in any chat to search

Type any movie/series name to start searching!
        """
        
        keyboard = [
            [InlineKeyboardButton("🎬 Search Movies", switch_inline_query_current_chat="")],
            [InlineKeyboardButton("📺 Search Series", switch_inline_query_current_chat="")],
            [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            welcome_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_text = """
📖 <b>Bot Commands & Features</b>

🔍 <b>Searching:</b>
• Type any movie/series name
• Use filters like "Inception 2010"
• Search by genre, year, or language

📤 <b>Admin Commands:</b>
• /upload - Start upload process
• /stats - View bot statistics
• /cancel - Cancel current operation

💡 <b>Tips:</b>
• Use specific names for better results
• Include year for accuracy
• Use inline mode for quick access

🎯 <b>Inline Mode:</b>
Type @YourBotUsername followed by your search in ANY chat!

Need help? Contact: @YourSupportChannel
        """
        
        await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)
    
    async def upload_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /upload command - admin only"""
        user_id = update.effective_user.id
        
        if not self.db.is_admin(user_id):
            await update.message.reply_text("⚠️ This command is for admins only!")
            return
        
        # Initialize upload cache for this user
        self.upload_cache[user_id] = {'step': 'awaiting_file'}
        
        await update.message.reply_text(
            "📤 <b>Upload Process Started</b>\n\n"
            "Please send me the video/file you want to upload.\n\n"
            "Use /cancel to stop.",
            parse_mode=ParseMode.HTML
        )
    
    async def handle_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle uploaded files"""
        user_id = update.effective_user.id
        
        if user_id not in self.upload_cache:
            await update.message.reply_text(
                "⚠️ Please use /upload command first to start the upload process."
            )
            return
        
        if not self.db.is_admin(user_id):
            await update.message.reply_text("⚠️ You don't have permission to upload!")
            return
        
        # Get file info
        if update.message.document:
            file = update.message.document
            file_type = 'movie'
        elif update.message.video:
            file = update.message.video
            file_type = 'movie'
        else:
            await update.message.reply_text("⚠️ Please send a valid video file.")
            return
        
        # Store file info in cache
        self.upload_cache[user_id]['file_id'] = file.file_id
        self.upload_cache[user_id]['file_name'] = file.file_name if hasattr(file, 'file_name') else 'Unknown'
        self.upload_cache[user_id]['size'] = f"{file.file_size / (1024*1024):.2f} MB"
        self.upload_cache[user_id]['step'] = 'awaiting_details'
        
        # Ask for details
        await update.message.reply_text(
            "✅ File received!\n\n"
            "Now send me the details in this format:\n\n"
            "<code>Title: Movie Name\n"
            "Year: 2024\n"
            "Language: English\n"
            "Quality: 1080p\n"
            "Genre: Action, Thriller\n"
            "Rating: 8.5\n"
            "Type: movie</code>\n\n"
            "(Type can be: movie, series, documentary)\n\n"
            "Use /cancel to stop.",
            parse_mode=ParseMode.HTML
        )
    
    async def handle_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle search queries"""
        user_id = update.effective_user.id
        query = update.message.text.strip()
        
        # Check if user is in upload process
        if user_id in self.upload_cache:
            if self.upload_cache[user_id].get('step') == 'awaiting_details':
                await self.process_upload_details(update, context)
                return
        
        # Log search
        self.db.log_search(user_id, query)
        
        # Search in database
        results = self.db.search_media(query, limit=10)
        
        if not results:
            await update.message.reply_text(
                f"❌ No results found for '<b>{query}</b>'\n\n"
                "Try:\n"
                "• Different spelling\n"
                "• More specific search\n"
                "• Year or genre",
                parse_mode=ParseMode.HTML
            )
            return
        
        # Show results
        await update.message.reply_text(
            f"🔍 Found <b>{len(results)}</b> result(s) for '<b>{query}</b>':",
            parse_mode=ParseMode.HTML
        )
        
        for media in results:
            await self.send_media_result(update, media)
    
    async def send_media_result(self, update: Update, media: dict):
        """Send media file with details"""
        caption = f"""
🎬 <b>{media['title']}</b>

📅 Year: {media['year'] or 'N/A'}
🎭 Genre: {media['genre'] or 'N/A'}
⭐️ Rating: {media['imdb_rating'] or 'N/A'}
🌍 Language: {media['language'] or 'N/A'}
📺 Quality: {media['quality'] or 'N/A'}
💾 Size: {media['size'] or 'N/A'}

{media['description'] or ''}
        """
        
        try:
            if media.get('thumbnail_id'):
                # Send with thumbnail
                await update.message.reply_document(
                    document=media['file_id'],
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    thumbnail=media['thumbnail_id']
                )
            else:
                # Send without thumbnail
                await update.message.reply_document(
                    document=media['file_id'],
                    caption=caption,
                    parse_mode=ParseMode.HTML
                )
        except Exception as e:
            logger.error(f"Error sending media: {e}")
            await update.message.reply_text(f"❌ Error sending file: {str(e)}")
    
    async def process_upload_details(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Process upload details from user"""
        user_id = update.effective_user.id
        details_text = update.message.text
        
        # Parse details
        details = {}
        for line in details_text.split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                details[key.strip().lower()] = value.strip()
        
        # Validate required fields
        if 'title' not in details:
            await update.message.reply_text(
                "⚠️ Title is required! Please include 'Title: Movie Name'"
            )
            return
        
        # Create media entry
        media_data = {
            'title': details.get('title', 'Unknown'),
            'type': details.get('type', 'movie'),
            'year': int(details.get('year', 0)) if details.get('year', '').isdigit() else None,
            'language': details.get('language'),
            'quality': details.get('quality'),
            'size': self.upload_cache[user_id]['size'],
            'file_id': self.upload_cache[user_id]['file_id'],
            'genre': details.get('genre'),
            'imdb_rating': details.get('rating'),
            'description': details.get('description'),
            'uploaded_by': user_id
        }
        
        # Save to database
        media_id = self.db.add_media(media_data)
        
        # Clear cache
        del self.upload_cache[user_id]
        
        await update.message.reply_text(
            f"✅ <b>Upload Successful!</b>\n\n"
            f"📄 Title: {media_data['title']}\n"
            f"🆔 Media ID: {media_id}\n\n"
            f"Users can now search for this content!",
            parse_mode=ParseMode.HTML
        )
    
    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel current operation"""
        user_id = update.effective_user.id
        
        if user_id in self.upload_cache:
            del self.upload_cache[user_id]
            await update.message.reply_text("❌ Upload cancelled.")
        else:
            await update.message.reply_text("No operation to cancel.")
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show bot statistics - admin only"""
        user_id = update.effective_user.id
        
        if not self.db.is_admin(user_id):
            await update.message.reply_text("⚠️ This command is for admins only!")
            return
        
        stats = self.db.get_stats()
        
        stats_text = f"""
📊 <b>Bot Statistics</b>

🎬 Total Media: {stats['total_media']}
👥 Total Users: {stats['total_users']}
🔍 Total Searches: {stats['total_searches']}

Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        """
        
        await update.message.reply_text(stats_text, parse_mode=ParseMode.HTML)
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle button callbacks"""
        query = update.callback_query
        await query.answer()
        
        if query.data == "help":
            await self.help_command(update, context)
    
    async def inline_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline queries"""
        query = update.inline_query.query
        
        if not query or len(query) < 2:
            return
        
        # Search database
        results = self.db.search_media(query, limit=20)
        
        inline_results = []
        for media in results:
            description = f"{media['year']} • {media['quality']} • {media['language']}"
            
            inline_results.append(
                InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title=media['title'],
                    description=description,
                    input_message_content=InputTextMessageContent(
                        message_text=f"🎬 {media['title']}\n\nSearching for this movie..."
                    ),
                    thumbnail_url=media.get('thumbnail_url', '')
                )
            )
        
        await update.inline_query.answer(inline_results, cache_time=300)
    
    def run(self):
        """Start the bot"""
        logger.info("🤖 Media Bot is starting...")
        self.app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    # Configuration
    BOT_TOKEN = "7990282768:AAGKek9lizWmXB8U57CucrjTxo1twG5YI9M"  # Replace with your actual token
    ADMIN_IDS = [1285282032]  # Replace with your user ID (just the number, no quotes)
    
    # Create and run bot
    bot = MediaBot(BOT_TOKEN, ADMIN_IDS)
    bot.run()
