import hashlib
import datetime
import json
import logging
from telegram import (
    InlineQueryResultCachedPhoto, InlineKeyboardButton,
    InlineKeyboardMarkup, Update, MessageEntity,
)
from telegram.ext import (
    Application, CommandHandler, InlineQueryHandler,
    MessageHandler, filters, ContextTypes,
)
from config import BOT_TOKEN, DB_FILE

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

def u16(s: str) -> int:
    return len(s.encode('utf-16-le')) // 2

# --- База данных ---
def load_db():
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_db(data):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f)

def add_art(file_id: str, name: str, url):
    db = load_db()
    for item in db:
        if item.get("file_id") == file_id:
            return False
    db.append({"file_id": file_id, "name": name, "url": url})
    save_db(db)
    return True

# --- Логика выбора вайфу ---
def get_daily_waifu(user_id: int):
    db = load_db()
    if not db:
        return None
    today = datetime.date.today().isoformat()
    raw = f"{user_id}_{today}"
    hash_hex = hashlib.sha256(raw.encode()).hexdigest()
    seed = int(hash_hex, 16)
    index = seed % len(db)
    return db[index]

# --- Разбор подписи: имя + ссылка ---
def parse_caption(text, entities):
    text = text or ""
    entities = entities or []
    for e in entities:
        if e.type == MessageEntity.TEXT_LINK and e.url:
            name = text[e.offset:e.offset + e.length].strip()
            return name, e.url
    for e in entities:
        if e.type == MessageEntity.URL:
            url = text[e.offset:e.offset + e.length]
            name = text.replace(url, "").strip()
            return name, url
    return text.strip(), None

# --- Обработчики ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    keyboard = [[InlineKeyboardButton("💖 Сегодняшняя вайфу", switch_inline_query_current_chat="waifu")]]
    await update.message.reply_text(
        f"Привет! В базе {len(db)} артов.\n"
        f"Напиши @{context.bot.username} waifu в любом чате, чтобы получить вайфу!\n\n"
        f"Админу: пришли фотку, в подписи — имя персонажа и ссылка на пост канала.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_id = update.message.photo[-1].file_id
    name, url = parse_caption(update.message.caption, update.message.caption_entities)
    if add_art(file_id, name, url):
        label = name if name else "(без имени)"
        await update.message.reply_text(f"✅ Арт запомнен как «{label}». Всего в базе: {len(load_db())}")
    else:
        await update.message.reply_text("⚠️ Эта фотка уже в базе.")

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query
    user_id = query.from_user.id
    art = get_daily_waifu(user_id)

    if not art:
        await query.answer([], is_personal=True)
        return

    user = query.from_user.username or query.from_user.first_name or "друг"
    name = (art.get("name") or "").strip()
    url = art.get("url")

    prefix = f"Дорогой {user}\nСегодняшняя вайфу: "
    suffix = "✨"
    display_name = name if name else "???"
    caption_text = prefix + display_name + suffix

    caption_entities = []
    if url and name:
        caption_entities.append(MessageEntity(
            type=MessageEntity.TEXT_LINK,
            offset=u16(prefix),
            length=u16(display_name),
            url=url,
        ))

    result = InlineQueryResultCachedPhoto(
        id=art["file_id"],
        photo_file_id=art["file_id"],
        title="Ежедневная вайфу",
        description="Сегодняшняя вайфу...",
        caption=caption_text,
        caption_entities=caption_entities or None,
    )

    await query.answer([result], is_personal=True, cache_time=86400)

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, handle_photo))
    app.add_handler(InlineQueryHandler(inline_query))

    logger.info("🚀 Бот запущен!")
    app.run_polling(allowed_updates=["inline_query", "message"])

if __name__ == "__main__":
    main()
