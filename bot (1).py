import hashlib
import json
import logging
import random
import uuid
from datetime import date, timedelta
from difflib import SequenceMatcher
from urllib.parse import urlparse
from telegram import (
    InlineQueryResultCachedPhoto, InlineQueryResultArticle,
    InlineKeyboardButton, InlineKeyboardMarkup, Update,
    MessageEntity, InputMediaPhoto, InputTextMessageContent,
)
from telegram.ext import (
    Application, CommandHandler, InlineQueryHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)
from config import BOT_TOKEN, ADMIN_ID, MOD_CHAT_ID, PUBLISH_CHANNEL

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

STATE_FILE = "state.json"
MAX_ARTS = 10

def u16(s: str) -> int:
    return len(s.encode('utf-16-le')) // 2

# ================= СТЕЙТ =================
def load_state():
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_state(st):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(st, f)

def ensure(st):
    st.setdefault("users", {})
    st.setdefault("messages", {})
    st.setdefault("revealed", {})
    st.setdefault("characters", {})
    st.setdefault("titles", {})
    st.setdefault("next_char_id", 1)
    st.setdefault("next_title_id", 1)
    st.setdefault("pending", {})
    st.setdefault("add_sessions", {})
    return st

def get_user(st, uid):
    ensure(st)
    return st["users"].setdefault(str(uid), {"balance": 0, "streak": 0, "last_checkin": ""})

def set_user(st, uid, d):
    ensure(st)
    st["users"][str(uid)] = d

def msg_key(q):
    # Обычное сообщение (в чате) — есть q.message.
    # Сообщение, отправленное через inline-режим — q.message = None,
    # используем q.inline_message_id.
    if q.message:
        return f"{q.message.chat.id}:{q.message.message_id}"
    return f"inline:{q.inline_message_id}"

# ================= РЕЕСТР / ПОИСК =================
def char_list(st):
    return [c for c in st.get("characters", {}).values() if c.get("arts")]

def fuzzy(query, mapping):
    q = (query or "").strip().lower()
    if not q:
        return []
    out = []
    for i, name in mapping.items():
        n = (name or "").lower()
        ratio = SequenceMatcher(None, q, n).ratio()
        if q in n or n in q or ratio >= 0.6:
            out.append((i, name, max(ratio, 1.0 if (q in n or n in q) else ratio)))
    out.sort(key=lambda x: x[2], reverse=True)
    return out[:8]

def get_daily_character(st, uid):
    chars = char_list(st)
    if not chars:
        return None
    raw = f"{uid}_{date.today().isoformat()}"
    seed = int(hashlib.sha256(raw.encode()).hexdigest(), 16)
    return chars[seed % len(chars)]

def get_daily_art(char, uid):
    arts = char["arts"]
    raw = f"{uid}_{date.today().isoformat()}_art_{char['id']}"
    seed = int(hashlib.sha256(raw.encode()).hexdigest(), 16)
    return arts[seed % len(arts)]

# ================= ССЫЛКА НА ПОСТ В КАНАЛЕ =================
def channel_msg_link(message):
    chat = message.chat
    if chat.username:
        return f"https://t.me/{chat.username}/{message.message_id}"
    cid = str(chat.id)
    if cid.startswith("-100"):
        cid = cid[4:]
    else:
        cid = cid.lstrip("-")
    return f"https://t.me/c/{cid}/{message.message_id}"

def domain_name(url):
    net = urlparse(url).netloc or url
    if net.startswith("www."):
        net = net[4:]
    return net

def build_publish_caption(char_name, title_name, char_id, author_name, arts):
    text = (f" имя: {char_name}\n источник: {title_name}\n id: #{char_id}\n"
            f" автор: {author_name}")
    entities = []
    urls = [a["url"] for a in arts if a.get("url")]
    if urls:
        text += "\n\nИсточники: "
        for i, url in enumerate(urls):
            name = domain_name(url)
            entities.append(MessageEntity(
                type=MessageEntity.TEXT_LINK, offset=u16(text), length=u16(name), url=url))
            text += name
            if i != len(urls) - 1:
                text += ", "
    return text, entities

# ================= ПОДПИСЬ ВАЙФУ =================
def build_caption(char, art, owner_name):
    name = (char.get("name") or "").strip()
    # Ссылка ведёт на пост в общем канале, а не на оригинальный источник из заявки.
    url = art.get("channel_url") or art.get("url")
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

def waifu_kb(price):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"🔄 Обновить ({price})", callback_data="refresh"),
        InlineKeyboardButton("✅ Оставить", callback_data="keep"),
    ]])

REFRESH_PRICE = 500
KB_TIMEOUT = 15  # секунд бездействия, после которых кнопки исчезают

# ================= ТАЙМЕР АВТОСКРЫТИЯ КНОПОК =================
def _job_name(key):
    return f"kb_remove:{key}"

def schedule_kb_removal(context: ContextTypes.DEFAULT_TYPE, key):
    cancel_kb_removal(context, key)
    context.job_queue.run_once(remove_kb_job, KB_TIMEOUT, name=_job_name(key), data=key)

def cancel_kb_removal(context: ContextTypes.DEFAULT_TYPE, key):
    for j in context.job_queue.get_jobs_by_name(_job_name(key)):
        j.schedule_removal()

async def remove_kb_job(context: ContextTypes.DEFAULT_TYPE):
    key = context.job.data
    st = load_state(); ensure(st)
    if key not in st.get("messages", {}):
        return
    try:
        if key.startswith("inline:"):
            inline_id = key.split(":", 1)[1]
            await context.bot.edit_message_reply_markup(inline_message_id=inline_id, reply_markup=None)
        else:
            chat_id_s, message_id_s = key.split(":", 1)
            await context.bot.edit_message_reply_markup(
                chat_id=int(chat_id_s), message_id=int(message_id_s), reply_markup=None)
    except Exception as e:
        logger.error(f"kb auto-remove fail: {e}")
    st["messages"].pop(key, None)
    save_state(st)

# ================= МОДЕРАЦИЯ =================
async def is_mod(bot, uid):
    if uid == ADMIN_ID:
        return True
    try:
        m = await bot.get_chat_member(MOD_CHAT_ID, uid)
        return m.status in ("creator", "administrator")
    except Exception:
        return False

# ================= /start / /add / /setcover =================
async def begin_add(uid, send_coro_text, st):
    ensure(st)
    st["add_sessions"][str(uid)] = {"step": "char_name"}
    save_state(st)
    await send_coro_text("✍️ Введи имя персонажа.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st = load_state(); ensure(st); save_state(st)
    uid = update.effective_user.id
    if context.args and context.args[0] == "add":
        await begin_add(uid, update.message.reply_text, st)
        return
    chars = char_list(st)
    arts = sum(len(c["arts"]) for c in chars)
    text = (f"Привет! Персонажей в базе: {len(chars)}, артов: {arts}.\n"
            f"Напиши @{context.bot.username} в любом чате — "
            f"чтобы вызвать бота.")
    if uid == ADMIN_ID:
        text += "\n\n🛠 Админу: /setcover — поставить обложку-заглушку."
    kb = [
        [InlineKeyboardButton("💖 Сегодняшняя вайфу", switch_inline_query_current_chat="")],
        [InlineKeyboardButton("➕ Добавить вайфу", callback_data="add_start")],
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st = load_state()
    await begin_add(update.effective_user.id, update.message.reply_text, st)

async def setcover(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Только админ.")
        return
    st = load_state(); ensure(st)
    st["awaiting_cover"] = True
    save_state(st)
    await update.message.reply_text("📸 Пришли обложку-картинку следующим сообщением.")

# ================= ФОТО В ЛИЧКЕ =================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    file_id = update.message.photo[-1].file_id
    st = load_state(); ensure(st)
    if uid == ADMIN_ID and st.get("awaiting_cover"):
        st["cover_file_id"] = file_id
        st["awaiting_cover"] = False
        save_state(st)
        await update.message.reply_text("✅ Обложка-заглушка установлена.")
        return
    sess = st.get("add_sessions", {}).get(str(uid))
    if sess and sess.get("step") == "arts":
        _, url = parse_caption(update.message.caption, update.message.caption_entities)
        if not url:
            await update.message.reply_text("⚠️ У этого фото нет ссылки в подписи. Пришли фото заново и добавь ссылку на источник в подпись.")
            return
        sess.setdefault("arts", []).append({"file_id": file_id, "url": url})
        save_state(st)
        n = len(sess["arts"])
        kb = [[InlineKeyboardButton("📨 Отправить на модерацию", callback_data="add_submit"),
               InlineKeyboardButton("❌ Отмена", callback_data="add_cancel")]]
        extra = "" if n < MAX_ARTS else "\n(достигнут лимит 10 фото)"
        await update.message.reply_text(
            f"✅ Принято фото {n}/{MAX_ARTS}.{extra}\nМожешь прислать ещё или нажать «Отправить».",
            reply_markup=InlineKeyboardMarkup(kb))
        return

# ================= ТЕКСТ В ЛИЧКЕ (диалог добавления) =================
ARTS_PROMPT = "📸 Пришли фото арта. В подписи к фото — ссылка на источник.\nМожно до 10 фото."

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    st = load_state(); ensure(st)
    sess = st.get("add_sessions", {}).get(str(uid))
    if not sess:
        return
    t = update.message.text.strip()
    step = sess.get("step")

    if step == "char_name":
        if t.isdigit() and t in st["characters"]:
            ch = st["characters"][t]
            sess.update({"char_id": int(t), "char_name": ch["name"], "title_id": ch["title_id"], "step": "arts"})
            save_state(st)
            await update.message.reply_text(f"Выбран персонаж #{t} {ch['name']}.\n\n{ARTS_PROMPT}")
            return
        hits = fuzzy(t, {i: c["name"] for i, c in st["characters"].items()})
        if hits:
            rows = [[InlineKeyboardButton(f"#{i} {n}", callback_data=f"add_pick_char:{i}")] for i, n, _ in hits]
            rows.append([InlineKeyboardButton("🆕 Это новый персонаж", callback_data="add_new_char")])
            sess["typed_char"] = t
            save_state(st)
            await update.message.reply_text(
                "Найдены похожие персонажи. Выбери нужного или создай нового:",
                reply_markup=InlineKeyboardMarkup(rows))
            return
        sess["char_name"] = t
        sess["step"] = "title_name"
        save_state(st)
        await update.message.reply_text(
            "Персонажа нет в базе — создадим.\nТеперь введи название источника:")
        return

    if step == "title_name":
        if t.isdigit() and t in st["titles"]:
            ti = st["titles"][t]
            sess.update({"title_id": int(t), "title_name": ti["name"], "step": "arts"})
            save_state(st)
            await update.message.reply_text(f"Выбран источник #{t} {ti['name']}.\n\n{ARTS_PROMPT}")
            return
        hits = fuzzy(t, {i: ti["name"] for i, ti in st["titles"].items()})
        if hits:
            rows = [[InlineKeyboardButton(f"#{i} {n}", callback_data=f"add_pick_title:{i}")] for i, n, _ in hits]
            rows.append([InlineKeyboardButton("🆕 Новый источник", callback_data="add_new_title")])
            sess["typed_title"] = t
            save_state(st)
            await update.message.reply_text(
                "Найдены похожие источники. Выбери или создай новый:",
                reply_markup=InlineKeyboardMarkup(rows))
            return
        sess["title_name"] = t
        sess["step"] = "arts"
        save_state(st)
        await update.message.reply_text(ARTS_PROMPT)
        return

    if step == "arts":
        await update.message.reply_text("Сюда нужны фото со ссылкой в подписи. Текст без фото не принимаю 🙂")

# ================= ИНЛАЙН: 3 КАРТОЧКИ =================
async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.inline_query
    st = load_state(); ensure(st)
    cover = st.get("cover_file_id")
    results = []

    if cover:
        results.append(InlineQueryResultCachedPhoto(
            id="waifu", photo_file_id=cover,
            title="Ежедневная вайфу", description="Нажми, чтобы узнать свою",
            caption=" Нажми кнопку ниже, чтобы узнать свою вайфу дня",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💖 Узнать свою вайфу", callback_data="reveal")]])))
    else:
        results.append(InlineQueryResultArticle(
            id="waifu", title="💖 Ежедневная вайфу", description="Нажми, чтобы узнать свою",
            input_message_content=InputTextMessageContent("⚠️ Админ, задай обложку командой /setcover")))

    results.append(InlineQueryResultArticle(
        id="luck", title="🎰 Испытать удачу", description="Ежедневный чек-ин",
        input_message_content=InputTextMessageContent("🎰 Нажми кнопку, чтобы испытать удачу"),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🎰 Испытать удачу", callback_data="checkin")]])))

    results.append(InlineQueryResultArticle(
        id="stats", title="📊 Статистика", description="Серия и баланс",
        input_message_content=InputTextMessageContent("📊 Привет! Нажми кнопку, чтобы посмотреть свою статистику ✨"),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("👤 Пользователь", callback_data="userstat")]])))

    await q.answer(results, is_personal=True, cache_time=300)

# ================= РАСКРЫТЬ ВАЙФУ =================
async def reveal(q, context: ContextTypes.DEFAULT_TYPE):
    await q.answer()
    st = load_state(); ensure(st)
    uid = q.from_user.id
    today = date.today().isoformat()
    rev = st.get("revealed", {}).get(str(uid), {})
    if rev.get("date") == today and str(rev.get("char_id")) in st["characters"]:
        char = st["characters"][str(rev["char_id"])]
        art = random.choice(char["arts"])
    else:
        char = get_daily_character(st, uid)
        if not char:
            await q.edit_message_caption(caption="⚠️ База пуста. Добавь вайфу через ➕ и дождись одобрения.")
            return
        art = get_daily_art(char, uid)
    st["revealed"][str(uid)] = {"date": today, "char_id": char["id"]}
    save_state(st)
    caller = q.from_user.username or q.from_user.first_name or "друг"
    text, ents = build_caption(char, art, caller)
    media = InputMediaPhoto(media=art["file_id"], caption=text, caption_entities=ents)
    await q.edit_message_media(media=media, reply_markup=waifu_kb(REFRESH_PRICE))
    key = msg_key(q)
    st.setdefault("messages", {})[key] = {"owner": uid, "owner_name": caller, "round": 1}
    save_state(st)
    schedule_kb_removal(context, key)

async def refresh(q, context: ContextTypes.DEFAULT_TYPE):
    st = load_state(); ensure(st)
    key = msg_key(q)
    m = st.get("messages", {}).get(key)
    if not m:
        await q.answer("Время вышло, эта вайфу больше не обновляется.", show_alert=True); return
    uid = q.from_user.id
    price = REFRESH_PRICE * m["round"]
    u = get_user(st, uid)
    if u["balance"] < price:
        await q.answer(f"Не хватает монет: нужно {price}, у тебя {u['balance']}.", show_alert=True); return
    chars = char_list(st)
    if not chars:
        await q.answer("База пуста.", show_alert=True); return
    u["balance"] -= price
    set_user(st, uid, u)
    m["round"] += 1
    new_char = random.choice(chars)
    new_art = random.choice(new_char["arts"])
    st["revealed"][str(uid)] = {"date": date.today().isoformat(), "char_id": new_char["id"]}
    text, ents = build_caption(new_char, new_art, m["owner_name"])
    media = InputMediaPhoto(media=new_art["file_id"], caption=text, caption_entities=ents)
    await q.edit_message_media(media=media, reply_markup=waifu_kb(REFRESH_PRICE * m["round"]))
    st["messages"][key] = m
    save_state(st)
    schedule_kb_removal(context, key)  # сбрасываем таймер — ещё одна попытка "в запасе"
    await q.answer()

async def keep(q, context: ContextTypes.DEFAULT_TYPE):
    st = load_state(); ensure(st)
    key = msg_key(q)
    m = st.get("messages", {}).get(key)
    if not m:
        await q.answer("Сообщение не найдено.", show_alert=True); return
    if q.from_user.id != m["owner"]:
        await q.answer("Оставить может только тот, кто вызвал команду.", show_alert=True); return
    cancel_kb_removal(context, key)
    await q.edit_message_reply_markup(reply_markup=None)
    st["messages"].pop(key, None)
    save_state(st)
    await q.answer("Оставлено 💖")

# ================= УДАЧА / СТАТИСТИКА =================
async def checkin(q):
    st = load_state(); ensure(st)
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
    lines = [f"🔥 Подряд: {u['streak']} дн.", f"💰 Выпало: {win}", f"🎁 Бонус серии: +{bonus}"]
    if jackpot:
        lines.append("🎉🎉 ДЖЕКПОТ 100 000! 🎉🎉🎉")
    lines.append(f"🏦 Баланс: {u['balance']}")
    await q.edit_message_text("\n".join(lines))
    await q.answer()

async def userstat(q):
    st = load_state(); ensure(st)
    u = get_user(st, q.from_user.id)
    await q.edit_message_text(f"👤 Твоя статистика\n\n🔥 Серия: {u['streak']} дн.\n🏦 Баланс: {u['balance']} монет")
    await q.answer()

# ================= ДОБАВЛЕНИЕ: CALLBACK =================
async def add_pick_char(q, st, cid):
    sess = st["add_sessions"].get(str(q.from_user.id))
    if not sess:
        await q.answer("Сессия истекла, начни заново.", show_alert=True); return
    ch = st["characters"].get(cid)
    if not ch:
        await q.answer("Персонаж не найден.", show_alert=True); return
    sess.update({"char_id": int(cid), "char_name": ch["name"], "title_id": ch["title_id"], "step": "arts"})
    save_state(st)
    await q.edit_message_text(f"Выбран персонаж #{cid} {ch['name']}.\n\n{ARTS_PROMPT}")

async def add_new_char(q, st):
    sess = st["add_sessions"].get(str(q.from_user.id))
    if not sess:
        await q.answer("Сессия истекла.", show_alert=True); return
    sess["char_name"] = sess.get("typed_char", "")
    sess["step"] = "title_name"
    save_state(st)
    await q.edit_message_text("Создаём нового персонажа.\nВведи название произведения/источника:")

async def add_pick_title(q, st, tid):
    sess = st["add_sessions"].get(str(q.from_user.id))
    if not sess:
        await q.answer("Сессия истекла.", show_alert=True); return
    ti = st["titles"].get(tid)
    if not ti:
        await q.answer("Источник не найден.", show_alert=True); return
    sess.update({"title_id": int(tid), "title_name": ti["name"], "step": "arts"})
    save_state(st)
    await q.edit_message_text(f"Выбран источник #{tid} {ti['name']}.\n\n{ARTS_PROMPT}")

async def add_new_title(q, st):
    sess = st["add_sessions"].get(str(q.from_user.id))
    if not sess:
        await q.answer("Сессия истекла.", show_alert=True); return
    sess["title_name"] = sess.get("typed_title", "")
    sess["step"] = "arts"
    save_state(st)
    await q.edit_message_text(ARTS_PROMPT)

async def add_submit(q, bot, st):
    uid = q.from_user.id
    sess = st["add_sessions"].get(str(uid))
    if not sess or not sess.get("arts"):
        await q.answer("Сначала пришли хотя бы одно фото.", show_alert=True); return
    char_id = sess.get("char_id")
    title_id = sess.get("title_id")
    char_name = sess.get("char_name") or (st["characters"][str(char_id)]["name"] if char_id else "")
    title_name = sess.get("title_name") or (st["titles"][str(title_id)]["name"] if title_id else "")
    author_name = q.from_user.username or q.from_user.first_name or "анон"
    req_id = uuid.uuid4().hex[:8]
    st["pending"][req_id] = {
        "char_id": char_id, "char_name": char_name,
        "title_id": title_id, "title_name": title_name,
        "arts": sess["arts"], "author_id": uid, "author_name": author_name,
    }
    save_state(st)
    cap = (f"🆕 Заявка на вайфу\n\n"
           f"👤 Персонаж: {char_name or '(новый)'}" + (f" #{char_id}" if char_id else "") + "\n"
           f"📚 Источник: {title_name or '(новый)'}" + (f" #{title_id}" if title_id else "") + "\n"
           f"🖼 Артов: {len(sess['arts'])}\n"
           f"✍️ Автор: {author_name} ")
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{req_id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{req_id}")]])
    try:
        arts = sess["arts"]
        if len(arts) == 1:
            # Telegram позволяет прикрепить клавиатуру к одиночному фото.
            await bot.send_photo(MOD_CHAT_ID, arts[0]["file_id"], caption=cap, reply_markup=kb)
        else:
            # sendMediaGroup не поддерживает reply_markup — шлём все арты
            # альбомом, а кнопки approve/reject — отдельным сообщением следом.
            media = [InputMediaPhoto(a["file_id"], caption=(cap if i == 0 else None))
                     for i, a in enumerate(arts)]
            await bot.send_media_group(MOD_CHAT_ID, media)
            await bot.send_message(MOD_CHAT_ID, "👆 Заявка выше. Решение:", reply_markup=kb)
    except Exception as e:
        logger.error(f"mod send fail: {e}")
        await q.answer("Не удалось отправить в чат модерации.", show_alert=True); return
    st["add_sessions"].pop(str(uid), None)
    save_state(st)
    await q.edit_message_text("📨 Заявка отправлена модераторам. Жди одобрения.")
    await q.answer()

async def add_cancel(q, st):
    st["add_sessions"].pop(str(q.from_user.id), None)
    save_state(st)
    await q.edit_message_text("❌ Добавление отменено.")
    await q.answer()

# ================= МОДЕРАЦИЯ: APPROVE / REJECT =================
async def approve(q, bot, st, req_id):
    if not await is_mod(bot, q.from_user.id):
        await q.answer("Только модераторы.", show_alert=True); return
    req = st.get("pending", {}).get(req_id)
    if not req:
        await q.answer("Заявка уже обработана.", show_alert=True); return
        # --- собрать/создать источник ---
    title_id = req.get("title_id")
    if title_id and str(title_id) in st["titles"]:
        title_id = int(title_id)
        title_name = st["titles"][str(title_id)]["name"]
    else:
        title_id = st["next_title_id"]
        st["next_title_id"] = title_id + 1
        title_name = req.get("title_name") or "(без названия)"
        st["titles"][str(title_id)] = {"id": title_id, "name": title_name}
    # --- собрать/создать персонажа ---
    char_id = req.get("char_id")
    if char_id and str(char_id) in st["characters"]:
        char_id = int(char_id)
        ch = st["characters"][str(char_id)]
        char_name = ch["name"]
    else:
        char_id = st["next_char_id"]
        st["next_char_id"] = char_id + 1
        char_name = req.get("char_name") or "(без имени)"
        ch = {"id": char_id, "name": char_name, "title_id": title_id, "arts": []}
        st["characters"][str(char_id)] = ch
    # arts — те же объекты, что попадут в ch["arts"], поэтому дозапись
    # channel_url ниже автоматически сохранится и в базе персонажа.
    arts = req.get("arts", [])
    ch.setdefault("arts", []).extend(arts)
    save_state(st)
    # --- публикация в канал ---
    # Оригинальные ссылки из заявки — для ознакомления, показываются как домен-гиперссылка.
    cap, cap_entities = build_publish_caption(
        char_name, title_name, char_id, req.get('author_name', 'анон'), arts)
    try:
        if len(arts) == 1:
            msg = await bot.send_photo(PUBLISH_CHANNEL, arts[0]["file_id"], caption=cap, caption_entities=cap_entities)
            arts[0]["channel_url"] = channel_msg_link(msg)
        elif len(arts) > 1:
            media = [InputMediaPhoto(a["file_id"],
                                      caption=(cap if i == 0 else None),
                                      caption_entities=(cap_entities if i == 0 else None))
                     for i, a in enumerate(arts)]
            sent = await bot.send_media_group(PUBLISH_CHANNEL, media)
            for a, m in zip(arts, sent):
                a["channel_url"] = channel_msg_link(m)
    except Exception as e:
        logger.error(f"publish fail: {e}")
    st["pending"].pop(req_id, None)
    save_state(st)
    try:
        await q.edit_message_caption(caption="✅ Одобрено.")
    except Exception:
        await q.edit_message_text("✅ Одобрено.")
    await q.answer()


async def reject(q, bot, st, req_id):
    if not await is_mod(bot, q.from_user.id):
        await q.answer("Только модераторы.", show_alert=True); return
    if req_id not in st.get("pending", {}):
        await q.answer("Заявка уже обработана.", show_alert=True); return
    st["pending"].pop(req_id, None)
    save_state(st)
    try:
        await q.edit_message_caption(caption="❌ Отклонено.")
    except Exception:
        await q.edit_message_text("❌ Отклонено.")
    await q.answer()


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    st = load_state(); ensure(st)
    try:
        if data == "reveal":
            await reveal(q, context)
        elif data == "refresh":
            await refresh(q, context)
        elif data == "keep":
            await keep(q, context)
        elif data == "checkin":
            await checkin(q)
        elif data == "userstat":
            await userstat(q)
        elif data == "add_start":
            await begin_add(q.from_user.id, q.message.reply_text, st)
            await q.answer()
        elif data == "add_submit":
            await add_submit(q, context.bot, st)
        elif data == "add_cancel":
            await add_cancel(q, st)
        elif data == "add_new_char":
            await add_new_char(q, st)
        elif data == "add_new_title":
            await add_new_title(q, st)
        elif data.startswith("add_pick_char:"):
            await add_pick_char(q, st, data.split(":", 1)[1])
        elif data.startswith("add_pick_title:"):
            await add_pick_title(q, st, data.split(":", 1)[1])
        elif data.startswith("approve:"):
            await approve(q, context.bot, st, data.split(":", 1)[1])
        elif data.startswith("reject:"):
            await reject(q, context.bot, st, data.split(":", 1)[1])
        else:
            await q.answer()
    except Exception as e:
        logger.error(f"callback error: {e}")
        await q.answer("Ошибка.", show_alert=True)


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("setcover", setcover))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, handle_text))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(CallbackQueryHandler(on_callback))
    logger.info("🚀 Бот запущен!")
    app.run_polling(allowed_updates=["message", "inline_query", "callback_query"])


if __name__ == "__main__":
    main()
