import hashlib
import datetime
import logging
import json
import asyncio
from telegram import InlineQueryResultPhoto, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    InlineQueryHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from config import BOT_TOKEN, CHANNEL_ID, CACHE_FILE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class WaifuBot:
    def __init__(self):
        self.art_messages = []
        self.load_cache()
    
    def load_cache(self):
        """Загружает кэш артов"""
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.art_messages = data.get('messages', [])
                logger.info(f"✅ Загружено {len(self.art_messages)} артов из кэша")
        except FileNotFoundError:
            logger.warning("⚠️ Кэш не найден! Запустите channel_scanner.py")
            self.art_messages = []
        except Exception as e:
            logger.error(f"Ошибка загрузки кэша: {e}")
            self.art_messages = []
    
    def get_daily_seed(self, user_id: int) -> int:
        """Генерирует уникальный сид для пользователя на сегодня"""
        today = datetime.date.today().isoformat()
        raw = f"{user_id}_{today}"
        hash_hex = hashlib.sha256(raw.encode()).hexdigest()
        return int(hash_hex, 16)
    
    def pick_art_for_user(self, user_id: int) -> int:
        """Выбирает арт для пользователя на сегодня"""
        if not self.art_messages:
            self.load_cache()
        
        if not self.art_messages:
            return None
        
        seed = self.get_daily_seed(user_id)
        index = seed % len(self.art_messages)
        return self.art_messages[index]
    
    def refresh_cache(self):
        """Обновляет кэш (вызывается при необходимости)"""
        self.load_cache()


bot_instance = WaifuBot()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    keyboard = [[
        InlineKeyboardButton(
            "💖 Сегодняшняя вайфу",
            switch_inline_query_current_chat="waifu"
        )
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Привет! Я бот с вайфу! \n\n"
        f"В базе {len(bot_instance.art_messages)} артов.\n"
        f"Нажми кнопку ниже, чтобы получить свою вайфу дня!",
        reply_markup=reply_markup
    )


async def refresh_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /refresh - обновляет кэш артов"""
    msg = await update.message.reply_text("🔄 Обновляю кэш артов...")
    
    bot_instance.refresh_cache()
    
    await msg.edit_text(
        f"✅ Кэш обновлён!\n"
        f"Всего артов: {len(bot_instance.art_messages)}"
    )


async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик инлайн-запроса"""
    query = update.inline_query
    user_id = query.from_user.id
    
    art_msg_id = bot_instance.pick_art_for_user(user_id)
    
    if art_msg_id is None:
        await query.answer([
            InlineQueryResultPhoto(
                id="error",
                photo_url="https://via.placeholder.com/400x400?text=No+Arts",
                thumbnail_url="https://via.placeholder.com/400x400?text=No+Arts",
                title="❌ Нет артов",
                caption="В канале пока нет артов или кэш не загружен.\nЗапустите /refresh"
            )
        ])
        return
    
    channel_username = CHANNEL_ID.lstrip("@")
    post_url = f"https://t.me/{channel_username}/{art_msg_id}"
    
    keyboard = [[
        InlineKeyboardButton("🔗 Открыть оригинал", url=post_url)
    ]]
    
    # Создаём инлайн-результат
    # Используем заглушку, т.к. нельзя напрямую получить URL фото из канала
    # Пользователь получит кнопку с ссылкой на пост
    result = InlineQueryResultPhoto(
        id=str(art_msg_id),
        photo_url=f"https://via.placeholder.com/512x512/FFB6C1/000000?text=Waifu+#{art_msg_id}",
        thumbnail_url=f"https://via.placeholder.com/512x512/FFB6C1/000000?text=Waifu+#{art_msg_id}",
        title=f"💖 Твоя вайфу #{art_msg_id}",
        caption=f"Твоя вайфу на {datetime.date.today().strftime('%d.%m.%Y')}!\n\n"
                f"Завтра будет новая! 🌸\n\n"
                f"Нажми кнопку, чтобы увидеть арт 👇",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    await query.answer([result], cache_time=86400)  # кэш на 24 часа


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатия на кнопку"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    art_msg_id = bot_instance.pick_art_for_user(user_id)
    
    if art_msg_id is None:
        await query.message.reply_text(
            "❌ Ошибка: в базе нет артов.\n"
            "Запустите команду /refresh"
        )
        return
    
    channel_username = CHANNEL_ID.lstrip("@")
    post_url = f"https://t.me/{channel_username}/{art_msg_id}"
    
    keyboard = [[
        InlineKeyboardButton("💖 Открыть в канале", url=post_url)
    ]]
    
    await query.message.reply_text(
        f"💖 **Твоя вайфу на {datetime.date.today().strftime('%d.%m.%Y')}!**\n\n"
        f"Завтра будет новая! 🌸\n\n"
        f"ID арта: #{art_msg_id}\n"
        f"Всего артов: {len(bot_instance.art_messages)}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /stats - показывает статистику"""
    await update.message.reply_text(
        f"📊 **Статистика бота:**\n\n"
        f"📁 Артов в базе: {len(bot_instance.art_messages)}\n"
        f"📅 Сегодня: {datetime.date.today().strftime('%d.%m.%Y')}\n"
        f"🔄 Кэш обновляется автоматически",
        parse_mode='Markdown'
    )


def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Обработчики команд
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("refresh", refresh_command))
    application.add_handler(CommandHandler("stats", stats_command))
    
    # Обработчики инлайн и кнопок
    application.add_handler(InlineQueryHandler(inline_query_handler))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    logger.info("🚀 Бот запущен!")
    logger.info(f" Загружено {len(bot_instance.art_messages)} артов")
    logger.info(" Используй /refresh для обновления кэша")
    
    application.run_polling(allowed_updates=["inline_query", "callback_query", "message"])


if __name__ == "__main__":
    main()
