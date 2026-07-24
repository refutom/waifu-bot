import hashlib
import datetime
import json
import logging
import random
from datetime import date, timedelta
from telegram import (
    InlineQueryResultCachedPhoto, InlineQueryResultArticle,
    InlineKeyboardButton, InlineKeyboardMarkup, Update,
    MessageEntity, InputMediaPhoto, InputTextMessageContent,
)
from telegram.ext import (
    Application, CommandHandler, InlineQueryHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)
from config import BOT_TOKEN, DB_FILE, ADMIN_ID

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

STATE_FILE = "state.json"

def u16(s: str) -> int:
    return len(s.encode('utf-16-le')) // 2

# --- База артов (НЕ ТРОГАЛ) ---
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

# --- Логика выбора вайфу дня (НЕ ТРОГАЛ) ---
def get_daily_waifu(user_id: int):
    db = load_db()
    if not db:
        return None
    today = date.today().isoformat()
    raw = f"{user_id}_{today}"
    hash_hex = hashlib.sha256(raw.encode()).hexdigest()
    seed = int(hash_hex, 16)
    index = seed % len(db)
    return db[index]

# --- Разбор подписи (НЕ ТРОГАЛ) ---
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

# --- Подпись под вайфу (имя = ссылка) ---
def build_caption(item, owner_name):
    name = (item.get("name") or "").strip()
    url = item.get("url")
    prefix = f"Дорогой {owner_name}\nСегодняшняя вайфу: "
    suffix = "✨"
    display = name if name else "???"
    text = prefix + display + suffix
    ents = []
    if url and name:
        ents.append(MessageEntity(
            type=MessageEntity.TEXT_LINK,
            offset=u16(prefix), length=u16(display), url=url))
    return text, ents

# --- Состояние (балансы, серии, обложка, сообщения) ---
def load_state():
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_state(st):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(st, f)

def get_user(st, uid):
    st.setdefault("users", {})
    return st["users"].setdefault(str(uid), {"balance": 0, "streak": 0, "last_checkin": ""})

def set_user(st, uid, d):
    st.setdefault("users", {})[str(uid)] = d

def msg_key(q):
    return f"{q.message.chat.id}:{q.message.message_id}"

def waifu_kb(price, locked):
    if locked:
        return None
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"🔄 Обновить ({price})", callback_data="refresh"),
        InlineKeyboardButton("✅ Оставить", callback_data="keep"),
    ]])

# --- /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    keyboard = [[InlineKeyboardButton("💖 Сегодняшняя вайфу", switch_inline_query_current_chat="")]]
    text = (f"Привет! В базе {len(db)} артов.\n"
            f"Нажми кнопку или напиши @{context.bot.username} в любом чате — "
            f"выпадут вайфу, удача и статистика.")
    if update.effective_user.id == ADMIN_ID:
        text += "\n\n🛠 Админу: /setcover — поставить обложку-заглушку."
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# --- /setcover (только админ) ---
async def setcover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Только админ.")
        return
    st = load_state()
    st["awaiting_cover"] = True
    save_state(st)
    await update.message.reply_text("📸 Пришли обложку-картинку следующим сообщением.")

# --- Приём фото в личке: обложка (админ) или арт (пока оставлено) ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    file_id = update.message.photo[-1].file_id
    st = load_state()
    if uid == ADMIN_ID and st.get("awaiting_cover"):
        st["cover_file_id"] = file_id
        st["awaiting_cover"] = False
        save_state(st)
        await update.message.reply_text("✅ Обложка-заглушка установлена.")
        return
    name, url = parse_caption(update.message.caption, update.message.caption_entities)
    if add_art(file_id, name, url):
        label = name if name else "(без имени)"
        await update.message.reply_text(f"✅ Арт запомнен как «{label}». Всего в базе: {len(load_db())}")
    else:
        await update.message.reply_text("⚠️ Эта фотка уже в базе.")

# --- Инлайн: три карточки ---
async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.inline_query
    st = load_state()
    cover = st.get("cover_file_id")
    results = []

    if cover:
        results.append(InlineQueryResultCachedPhoto(
            id="waifu", photo_file_id=cover,
            title="💖 Ежедневная вайфу",
            description="Нажми, чтобы узнать свою",
            caption="💖 Нажми кнопку ниже, чтобы узнать свою вайфу дня",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💖 Узнать свою вайфу", callback_data="reveal")]])))
    else:
        results.append(InlineQueryResultArticle(
            id="waifu", title="💖 Ежедневная вайфу",
            description="Нажми, чтобы узнать свою",
            input_message_content=InputTextMessageContent(
                "⚠️ Админ, задай обложку командой /setcover")))

    results.append(InlineQueryResultArticle(
        id="luck", title="🎰 Испытать удачу",
        description="Ежедневный чек-ин",
        input_message_content=InputTextMessageContent("🎰 Нажми кнопку, чтобы испытать удачу"),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🎰 Испытать удачу", callback_data="checkin")]])))

    results.append(InlineQueryResultArticle(
        id="stats", title="📊 Статистика",
        description="Серия и баланс",
        input_message_content=InputTextMessageContent(
            "📊 Привет! Нажми кнопку, чтобы посмотреть свою статистику ✨"),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("👤 Пользователь", callback_data="userstat")]])))

    await q.answer(results, is_personal=True, cache_time=300)

# --- Раскрыть вайфу (тап по заглушке) ---
async def reveal(q):
    await q.answer()
    db = load_db()
    if not db:
        await q.edit_message_caption(caption="⚠️ База артов пуста. Админ, добавь арты.")
        return
    uid = q.from_user.id
    caller = q.from_user.username or q.from_user.first_name or "друг"
    item = get_daily_waifu(uid)
    text, ents = build_caption(item, caller)
    media = InputMediaPhoto(media=item["file_id"], caption=text, caption_entities=ents)
    await q.edit_message_media(media=media, reply_markup=waifu_kb(500, False))
    st = load_state()
    st.setdefault("messages", {})[msg_key(q)] = {
        "owner": uid, "owner_name": caller, "round": 1, "locked": False}
    save_state(st)

# --- Обновить (меняет персонажа/арт, цена растёт) ---
async def refresh(q):
    st = load_state()
    m = st.get("messages", {}).get(msg_key(q))
    if not m:
        await q.answer("Сообщение не найдено.", show_alert=True); return
    if m["locked"]:
        await q.answer("Вайфу уже оставлена, обновить нельзя.", show_alert=True); return
    uid = q.from_user.id
    price = 500 * m["round"]
    u = get_user(st, uid)
    if u["balance"] < price:
        await q.answer(f"Не хватает монет: нужно {price}, у тебя {u['balance']}.", show_alert=True)
        return
    u["balance"] -= price
    set_user(st, uid, u)
    m["round"] += 1
    db = load_db()
    new_item = random.choice(db)
    text, ents = build_caption(new_item, m["owner_name"])
    media = InputMediaPhoto(media=new_item["file_id"], caption=text, caption_entities=ents)
    await q.edit_message_media(media=media, reply_markup=waifu_kb(500 * m["round"], False))
    st["messages"][msg_key(q)] = m
    save_state(st)
    await q.answer()

# --- Оставить (только вызвавший) ---
async def keep(q):
    st = load_state()
    m = st.get("messages", {}).get(msg_key(q))
    if not m:
        await q.answer("Сообщение не найдено.", show_alert=True); return
    if q.from_user.id != m["owner"]:
        await q.answer("Оставить может только тот, кто вызвал вайфу.", show_alert=True); return
    m["locked"] = True
    st["messages"][msg_key(q)] = m
    save_state(st)
    await q.edit_message_reply_markup(reply_markup=None)
    await q.answer("Оставлено 💖")

# --- Чек-ин удачи ---
async def checkin(q):
    st = load_state()
    uid = q.from_user.id
    u = get_user(st, uid)
    today = date.today().isoformat()
    yest = (date.today() - timedelta(days=1)).isoformat()
    if u["last_checkin"] == today:
        await q.answer("Сегодня ты уже испытывал удачу 💫", show_alert=True); return
    u["streak"] = u["streak"] + 1 if u["last_checkin"] == yest else 1
    u["last_checkin"] = today
    jackpot = random.random() < 0.01
    win = 100000 if jackpot else random.randint(20, 2500)
    bonus = 10 * u["streak"]
    u["balance"] += win + bonus
    set_user(st, uid, u)
    save_state(st)
    lines = [f"🔥 Подряд: {u['streak']} дн.",
             f"💰 Выпало: {win}",
             f"🎁 Бонус серии: +{bonus}"]
    if jackpot:
        lines.append("🎉🎉 ДЖЕКПОТ 100 000! 🎉🎉🎉")
    lines.append(f"🏦 Баланс: {u['balance']}")
    await q.edit_message_text("\n".join(lines))
    await q.answer()

# --- Статистика пользователя ---
async def userstat(q):
    st = load_state()
    u = get_user(st, q.from_user.id)
    await q.edit_message_text(
        f"👤 Твоя статистика\n\n🔥 Серия: {u['streak']} дн.\n🏦 Баланс: {u['balance']} монет")
    await q.answer()

# --- Диспетчер кнопок ---
async def dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    if data == "reveal":
        await reveal(q)
    elif data == "refresh":
        await refresh(q)
    elif data == "keep":
        await keep(q)
    elif data == "checkin":
        await checkin(q)
    elif data == "userstat":
        await userstat(q)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setcover", setcover))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, handle_photo))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(CallbackQueryHandler(dispatch))
    logger.info("🚀 Бот запущен!")
    app.run_polling(allowed_updates=["message", "inline_query", "callback_query"])

if __name__ == "__main__":
    main()
