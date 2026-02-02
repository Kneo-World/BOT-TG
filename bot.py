"""
StarsForQuestion - ULTIMATE MONOLITH v7.0
Абсолютно все функции: исправленные кнопки, рефералы (2 звезды), 
посты в канал (0.3 звезды), реалистичные фейки и кнопка связи.
"""

import asyncio
import logging
import os
import sqlite3

# Создаем подключение к базе данных
db = sqlite3.connect("users.db", check_same_thread=False)
cursor = db.cursor()

# Создаем таблицу, если её нет
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY
)
""")
db.commit()

import random
import string
from datetime import datetime, timedelta
from aiohttp import web

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage


# ========== КОНФИГУРАЦИЯ ==========
users_db = set() # Это временная база данных в оперативной памяти
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1003326584722") 
raw_admins = os.getenv("ADMIN_IDS", "8364667153")
ADMIN_IDS = [int(id.strip()) for id in raw_admins.split(",") if id.strip()]
WITHDRAWAL_CHANNEL_ID = os.getenv("WITHDRAWAL_CHANNEL", "-1003891414947") 
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@Nft_top3")
PORT = int(os.environ.get("PORT", 10000))

# Экономика
REF_REWARD = 5.0  
VIEW_REWARD = 0.3 
DAILY_MIN, DAILY_MAX = 1, 3
LUCK_MIN, LUCK_MAX = 0, 5
LUCK_COOLDOWN = 6 * 60 * 60
WITHDRAWAL_OPTIONS = [15, 25, 50, 100]

# ========== БАЗА ДАННЫХ ==========
class Database:
    def __init__(self, path="bot_data.db"):
        self.path = path
        self.init_db()
    
    def get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        with self.get_connection() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS promo_history (
            user_id INTEGER, 
            code TEXT, 
            PRIMARY KEY(user_id, code))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, 
                stars REAL DEFAULT 0, referrals INTEGER DEFAULT 0, 
                last_daily TIMESTAMP, last_luck TIMESTAMP, ref_code TEXT UNIQUE)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL,
                status TEXT DEFAULT 'pending', created_at TIMESTAMP)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS post_claims (
                user_id INTEGER, post_id TEXT, PRIMARY KEY(user_id, post_id))""")
            conn.commit()
            conn.execute("ALTER TABLE users ADD COLUMN ref_boost REAL DEFAULT 1.0") # Множитель рефералов 
            conn.execute("""CREATE TABLE IF NOT EXISTS promo (
            code TEXT PRIMARY KEY, reward_type TEXT, reward_value TEXT, uses INTEGER)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS inventory (
            user_id INTEGER, 
            item_name TEXT, 
            quantity INTEGER DEFAULT 1)""")
            # Таблица лотереи: хранит текущий банк и ID участников через запятую
            conn.execute("""CREATE TABLE IF NOT EXISTS lottery 
                            (id INTEGER PRIMARY KEY, pool REAL DEFAULT 0, participants TEXT DEFAULT '')""")
            # Инициализируем первую запись лотереи, если её нет
            conn.execute("INSERT OR IGNORE INTO lottery (id, pool, participants) VALUES (1, 0, '')")
            
            # Добавляем колонку для проверки "активности" реферала
            try:
                conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 0")
                conn.execute("ALTER TABLE users ADD COLUMN total_earned REAL DEFAULT 0")
            except:
                pass # Если колонки уже есть
            conn.commit()
            conn.execute("CREATE TABLE IF NOT EXISTS task_claims (user_id INTEGER, task_id TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS lottery_history (user_id INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
            conn.execute("CREATE TABLE IF NOT EXISTS nfts (id INTEGER PRIMARY KEY, owner_id INTEGER, name TEXT, serial_number INTEGER, stats TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS marketplace (id INTEGER PRIMARY KEY, seller_id INTEGER, nft_id INTEGER, price REAL)")
            conn.execute("CREATE TABLE IF NOT EXISTS daily_streaks (user_id INTEGER PRIMARY KEY, streak INTEGER DEFAULT 0, last_date TEXT)")
            # Для ежедневного бонуса (стрик)
            conn.execute("""CREATE TABLE IF NOT EXISTS daily_bonus 
                    (user_id INTEGER PRIMARY KEY, last_date TEXT, streak INTEGER DEFAULT 0)""")
            # Для хранения созданных дуэлей
            conn.execute("""CREATE TABLE IF NOT EXISTS active_duels 
                    (creator_id INTEGER PRIMARY KEY, amount REAL)""")
            # Для P2P рынка
            conn.execute("""CREATE TABLE IF NOT EXISTS marketplace 
                    (id INTEGER PRIMARY KEY AUTOINCREMENT, seller_id INTEGER, item_name TEXT, price REAL)""")
            conn.execute("CREATE TABLE IF NOT EXISTS task_claims (user_id INTEGER, task_id TEXT)")
            conn.commit()

    def get_user(self, user_id: int):
        with self.get_connection() as conn:
            return conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()

    def create_user(self, user_id, username, first_name):
        with self.get_connection() as conn:
            ref_code = f"ref{user_id}"
            conn.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, ref_code) VALUES (?, ?, ?, ?)",
                        (user_id, username, first_name, ref_code))
            conn.commit()

    def add_stars(self, user_id, amount):
        with self.get_connection() as conn:
            # Умножаем на буст только если это НАЧИСЛЕНИЕ (amount > 0)
            if amount > 0:
                user = self.get_user(user_id)
                boost = user['ref_boost'] if user and 'ref_boost' in user.keys() else 1.0
                amount = float(amount) * boost
            
            conn.execute("UPDATE users SET stars = stars + ? WHERE user_id = ?", (amount, user_id))
            conn.commit()

db = Database()

# ========== СОСТОЯНИЯ (FSM) ==========
class AdminStates(StatesGroup):
    waiting_fake_name = State()
    waiting_give_data = State()
    waiting_broadcast_msg = State()
    waiting_channel_post = State()
    waiting_promo_data = State() # Для создания промокода админом

class PromoStates(StatesGroup):
    waiting_for_code = State() # Для ввода кода юзером
    
# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def mask_name(name):
    if not name: return "User****"
    name = name.replace("@", "")
    return name[:3] + "****" if len(name) > 3 else name + "****"

def generate_fake_id():
    return "".join([str(random.randint(0, 9)) for _ in range(10)])

def generate_fake_user():
    prefixes = ["Kripto", "Star", "Rich", "Trader", "Money", "Lucky", "Alex", "Dmitry", "Zevs"]
    suffixes = ["_top", "777", "X", "_pro", "King", "Off", "Master"]
    return random.choice(prefixes) + random.choice(suffixes)

def get_main_kb(uid):
    builder = InlineKeyboardBuilder()
    
    # Секция: ЗАРАБОТОК
    builder.row(InlineKeyboardButton(text="🎯 Квесты", callback_data="tasks"),
                InlineKeyboardButton(text="👥 Друзья", callback_data="referrals"))
    
    # Секция: КАЗИНО / УДАЧА
    builder.row(InlineKeyboardButton(text="🎰 Удача", callback_data="luck"),
                InlineKeyboardButton(text="🎟 Лотерея", callback_data="lottery"))
    
    # Секция: МАГАЗИН И АККАУНТ
    builder.row(InlineKeyboardButton(text="🛒 Магазин", callback_data="shop"),
                InlineKeyboardButton(text="🎒 Инвентарь", callback_data="inventory"))
    
    # Секция: ПРОЧЕЕ
    builder.row(InlineKeyboardButton(text="🏆 ТОП", callback_data="top"),
                InlineKeyboardButton(text="🎁 Промокод", callback_data="use_promo"))

    if uid in ADMIN_IDS:
        builder.row(InlineKeyboardButton(text="👑 Админ Панель", callback_data="admin_panel"))
        
    return builder.as_markup()

def get_admin_decision_kb(uid, amount):
    builder = InlineKeyboardBuilder()
    # uid — ID юзера, amount — сумма или строка "GIFT"
    builder.row(
        InlineKeyboardButton(text="✅ Принять", callback_data=f"adm_app_{uid}_{amount}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_rej_{uid}_{amount}")
    )
    builder.row(InlineKeyboardButton(text="✉️ Написать в ЛС", callback_data=f"adm_chat_{uid}"))
    return builder.as_markup()

# ========== ОБРАБОТЧИКИ ЮЗЕРОВ ==========

# --- ЗАЩИЩЕННЫЙ СТАРТ ---
@dp.message(CommandStart())
async def cmd_start(message: Message):
    # В самом начале функции cmd_start:
args = message.text.split()
if len(args) > 1 and args[1].startswith("duel"):
    creator_id = int(args[1].replace("duel", ""))
    # Проверяем, есть ли такой создатель и не сам ли это юзер
    if creator_id != message.from_user.id:
        kb = InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="🤝 Принять вызов (5.0 ⭐)", callback_data=f"accept_duel_{creator_id}"),
            InlineKeyboardButton(text="❌ Отказ", callback_data="menu")
        )
        return await message.answer(f"⚔️ Игрок ID:{creator_id} вызывает тебя на дуэль!", reply_markup=kb.as_markup())
    uid = message.from_user.id
    if not db.get_user(uid):
        db.create_user(uid, message.from_user.username, message.from_user.first_name)
        if " " in message.text:
            args = message.text.split()[1]
            if args.startswith("ref"):
                ref_id = int(args.replace("ref", ""))
                if ref_id != uid:
                    # Просто записываем в БД, кто пригласил, но НЕ даем деньги сразу
                    with db.get_connection() as conn:
                        conn.execute("UPDATE users SET referrals = referrals + 1 WHERE user_id = ?", (ref_id,))
                        conn.commit()
                    try: 
                        await bot.send_message(ref_id, "👥 У вас новый реферал! Вы получите 5 ⭐, когда он заработает свои первые 1.0 ⭐.")
                    except: pass
    
    # Красивое приветствие
    text = (
        f"👋 Привет, <b>{message.from_user.first_name}</b>!\n\n"
        "💎 <b>StarsForQuestion</b> — это место, где твоя активность превращается в Telegram Stars.\n\n"
        "🎯 Выполняй задания, крути удачу и забирай подарки!"
    )
    await message.answer(text, reply_markup=get_main_kb(uid))

# --- ФУНКЦИЯ ДОБАВЛЕНИЯ ЗВЕЗД С ПРОВЕРКОЙ (АНТИ-ФЕЙК) ---
def add_stars_secure(user_id, amount, is_task=False):
    """Обертка: если юзер заработал суммарно 1.0, его пригласителю капает бонус"""
    db.add_stars(user_id, amount)
    if amount > 0:
        with db.get_connection() as conn:
            conn.execute("UPDATE users SET total_earned = total_earned + ? WHERE user_id = ?", (amount, user_id))
            user = db.get_user(user_id)
            # Если юзер набрал 1.0 звезду и еще не был активирован
            if user['total_earned'] >= 1.0 and user['is_active'] == 0:
                conn.execute("UPDATE users SET is_active = 1 WHERE user_id = ?", (user_id,))
                # Находим того, кто его пригласил (через ref_code)
                ref_owner_id = user_id # Упрощенно: в твоей БД нужно хранить пригласителя. 
                # СОВЕТ: Для полной защиты добавь колонку 'referred_by' в таблицу users.
                conn.commit()

# ========== ЕЖЕДНЕВНЫЙ БОНУС (СТРИК) ==========
@dp.callback_query(F.data == "daily_bonus")
async def cb_daily_bonus(call: CallbackQuery):
    uid = call.from_user.id
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    
    with db.get_connection() as conn:
        data = conn.execute("SELECT last_date, streak FROM daily_bonus WHERE user_id = ?", (uid,)).fetchone()
        
        if data:
            last_date = datetime.strptime(data['last_date'], "%Y-%m-%d")
            delta = (now.date() - last_date.date()).days
            
            if delta == 0:
                return await call.answer("❌ Бонус уже получен! Приходи завтра.", show_alert=True)
            elif delta == 1:
                new_streak = min(data['streak'] + 1, 7) # Макс 7 дней
            else:
                new_streak = 1 # Сброс, если пропустил день
            conn.execute("UPDATE daily_bonus SET last_date = ?, streak = ? WHERE user_id = ?", (today_str, new_streak, uid))
        else:
            new_streak = 1
            conn.execute("INSERT INTO daily_bonus (user_id, last_date, streak) VALUES (?, ?, ?)", (uid, today_str, new_streak))
        conn.commit()

    reward = round(0.1 * new_streak, 2)
    db.add_stars(uid, reward)
    await call.answer(f"✅ День {new_streak}! Получено: {reward} ⭐", show_alert=True)

# ========== ДУЭЛИ (СТАВКИ) ==========
@dp.callback_query(F.data == "duel_menu")
async def cb_duel_menu(call: CallbackQuery):
    uid = call.from_user.id
    bot_username = (await bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=duel{uid}"
    
    text = (
        "⚔️ <b>ДУЭЛЬНЫЙ КЛУБ</b>\n━━━━━━━━━━━━━━\n"
        "Ставка: <b>5.0 ⭐</b>\n"
        "Победитель получает: <b>9.0 ⭐</b>\n\n"
        "Отправь ссылку другу, чтобы вызвать его на бой:"
    )
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📨 Скинуть ссылку другу", switch_inline_query=link))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    
    # Чтобы юзер мог просто скопировать ссылку
    await call.message.edit_text(f"{text}\n<code>{link}</code>", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("accept_duel_"))
async def cb_accept_duel(call: CallbackQuery):
    opponent_id = call.from_user.id
    creator_id = int(call.data.split("_")[2])
    
    if opponent_id == creator_id:
        return await call.answer("❌ Нельзя играть с самим собой!", show_alert=True)

    user = db.get_user(opponent_id)
    if user['stars'] < 5.0:
        return await call.answer("❌ Недостаточно ⭐ для ставки!", show_alert=True)

    # Списываем ставку у второго игрока (у первого она уже должна быть списана при создании)
    db.add_stars(opponent_id, -5.0)
    
    msg = await call.message.answer("🎲 Бросаем кости...")
    dice = await msg.answer_dice("🎲")
    await asyncio.sleep(3.5)
    
    # Логика: 1-3 победил создатель, 4-6 победил гость
    winner_id = creator_id if dice.dice.value <= 3 else opponent_id
    db.add_stars(winner_id, 9.0)
    
    await call.message.answer(f"🎰 Выпало <b>{dice.dice.value}</b>!\n👑 Победитель: <a href='tg://user?id={winner_id}'>Игрок</a>\nЗачислено: <b>9.0 ⭐</b>")

# --- ЛОТЕРЕЯ ---
@dp.callback_query(F.data == "lottery")
async def cb_lottery(call: CallbackQuery):
    with db.get_connection() as conn:
        data = conn.execute("SELECT pool, participants FROM lottery WHERE id = 1").fetchone()
    
    count = len(data['participants'].split(',')) if data['participants'] else 0
    text = (
        "🎟 <b>ЗВЕЗДНАЯ ЛОТЕРЕЯ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"💰 Текущий банк: <b>{data['pool']:.2f} ⭐</b>\n"
        f"👥 Участников: <b>{count}</b>\n"
        f"🎫 Цена билета: <b>2.0 ⭐</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Победитель забирает 80% банка. Розыгрыш происходит автоматически!</i>"
    )
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="💎 Купить билет", callback_data="buy_ticket"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text(text, reply_markup=kb.as_markup())

@dp.callback_query(F.data == "buy_ticket")
async def cb_buy_ticket(call: CallbackQuery):
    uid = call.from_user.id
    user = db.get_user(uid)
    if user['stars'] < 2:
        return await call.answer("❌ Недостаточно звезд (нужно 2.0)", show_alert=True)
    
    db.add_stars(uid, -2)
    with db.get_connection() as conn:
        conn.execute("UPDATE lottery SET pool = pool + 2, participants = participants || ? WHERE id = 1", (f"{uid},",))
        conn.commit()
    
    # Замени в функции buy_ticket:
await call.message.answer(f"🎟 <b>Билет №{random.randint(1000, 9999)} успешно куплен!</b>\n\nТвой шанс на победу вырос! Следи за каналом выплат.")
    await cb_lottery(call)
    
@dp.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery):
    await call.message.edit_text("⭐ <b>Главное меню</b>", reply_markup=get_main_kb(call.from_user.id))

@dp.callback_query(F.data == "profile")
async def cb_profile(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    await call.message.edit_text(f"👤 <b>Профиль</b>\n\n🆔 ID: <code>{u['user_id']}</code>\n⭐ Баланс: <b>{u['stars']:.2f} ⭐</b>\n👥 Рефералов: {u['referrals']}", 
                               reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu")).as_markup())

@dp.callback_query(F.data == "referrals")
async def cb_referrals(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    ref_link = f"https://t.me/{(await bot.get_me()).username}?start={u['ref_code']}"
    await call.message.edit_text(f"👥 <b>Рефералы</b>\n\nЗа друга: <b>{REF_REWARD} ⭐</b>\n\n🔗 Ссылка:\n<code>{ref_link}</code>", 
                               reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu")).as_markup())

@dp.callback_query(F.data == "daily")
async def cb_daily(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    now = datetime.now()
    if u['last_daily'] and (now - datetime.fromisoformat(u['last_daily'])).days < 1:
        return await call.answer("⏳ Только раз в день!", show_alert=True)
    rew = random.randint(DAILY_MIN, DAILY_MAX)
    db.add_stars(call.from_user.id, rew)
    with db.get_connection() as conn:
        conn.execute("UPDATE users SET last_daily = ? WHERE user_id = ?", (now.isoformat(), call.from_user.id))
        conn.commit()
    await call.answer(f"🎁 +{rew} ⭐", show_alert=True)
    await call.message.edit_text("⭐ <b>Главное меню</b>", reply_markup=get_main_kb(call.from_user.id))

@dp.callback_query(F.data == "luck")
async def cb_luck(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    now = datetime.now()
    if u['last_luck'] and (now - datetime.fromisoformat(u['last_luck'])).total_seconds() < LUCK_COOLDOWN:
        return await call.answer("⏳ Кулдаун 6 часов!", show_alert=True)
    win = random.randint(LUCK_MIN, LUCK_MAX)
    db.add_stars(call.from_user.id, win)
    with db.get_connection() as conn:
        conn.execute("UPDATE users SET last_luck = ? WHERE user_id = ?", (now.isoformat(), call.from_user.id))
        conn.commit()
    await call.answer(f"🎰 +{win} ⭐", show_alert=True)
    await call.message.edit_text("⭐ <b>Главное меню</b>", reply_markup=get_main_kb(call.from_user.id))

@dp.callback_query(F.data == "tasks")
async def cb_tasks(call: CallbackQuery):
    uid = call.from_user.id
    user = db.get_user(uid)
    
    # Считаем количество активных рефералов (те, кто заработал > 1 звезды)
    with db.get_connection() as conn:
        active_refs = conn.execute("SELECT COUNT(*) as cnt FROM users WHERE referred_by = ? AND total_earned >= 1.0", (uid,)).fetchone()['cnt']
        tickets_bought = conn.execute("SELECT COUNT(*) as cnt FROM lottery_history WHERE user_id = ?", (uid,)).fetchone()['cnt']
    
    kb = InlineKeyboardBuilder()
    
    # Квест 1: Стахановец
    status1 = "✅ Готово" if active_refs >= 3 else f"⏳ {active_refs}/3"
    kb.row(InlineKeyboardButton(text=f"📈 Стахановец: {status1}", callback_data="claim_task_1"))
    
    # Квест 2: Ловец удачи
    status2 = "✅ Готово" if tickets_bought >= 5 else f"⏳ {tickets_bought}/5"
    kb.row(InlineKeyboardButton(text=f"🎰 Ловец удачи: {status2}", callback_data="claim_task_2"))
    
    # Квест 3: Видео-отзыв (Ручной)
    kb.row(InlineKeyboardButton(text="📸 Отправить видео-отзыв (100 ⭐)", url="https://t.me/Nft_top3"))
    
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    
    text = (
        "🎯 <b>ЗАДАНИЯ И КВЕСТЫ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "💰 Забирай награды за активность!\n"
        "Награды начисляются моментально."
    )
    await call.message.edit_text(text, reply_markup=kb.as_markup())

# ОБРАБОТКА ЗАБОРА НАГРАДЫ
@dp.callback_query(F.data.startswith("claim_task_"))
async def claim_task(call: CallbackQuery):
    task_num = call.data.split("_")[2]
    uid = call.from_user.id
    
    with db.get_connection() as conn:
        # Проверяем, не забирал ли уже
        check = conn.execute("SELECT 1 FROM task_claims WHERE user_id = ? AND task_id = ?", (uid, task_num)).fetchone()
        if check: return await call.answer("❌ Вы уже получили награду за этот квест!", show_alert=True)
        
        if task_num == "1": # Стахановец
            count = conn.execute("SELECT COUNT(*) as cnt FROM users WHERE referred_by = ? AND total_earned >= 1.0", (uid,)).fetchone()['cnt']
            if count < 3: return await call.answer("❌ Нужно 3 активных реферала!", show_alert=True)
            reward = 15.0
        elif task_num == "2": # Ловец удачи
            count = conn.execute("SELECT COUNT(*) as cnt FROM lottery_history WHERE user_id = ?", (uid,)).fetchone()['cnt']
            if count < 5: return await call.answer("❌ Нужно купить еще билетов!", show_alert=True)
            reward = 3.0
            
        # Выдача
        conn.execute("INSERT INTO task_claims (user_id, task_id) VALUES (?, ?)", (uid, task_num))
        conn.commit()
        db.add_stars(uid, reward)
        await call.answer(f"✅ Начислено {reward} ⭐!", show_alert=True)
        await cb_tasks(call)

# --- РЕАЛЬНЫЙ ТОП ---
@dp.callback_query(F.data == "top")
async def cb_top(call: CallbackQuery):
    with db.get_connection() as conn:
        rows = conn.execute("SELECT first_name, stars FROM users ORDER BY stars DESC LIMIT 10").fetchall()
    
    text = "🏆 <b>ТОП-10 МАГНАТОВ</b>\n━━━━━━━━━━━━━━━━━━\n"
    for i, row in enumerate(rows, 1):
        # Маскируем имя для красоты
        name = row['first_name'][:3] + "***"
        text += f"{i}. {name} — <b>{row['stars']:.1f} ⭐</b>\n"
    
    kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text(text, reply_markup=kb.as_markup())

@dp.callback_query(F.data == "help")
async def cb_help(call: CallbackQuery):
    await call.message.edit_text(f"🆘 <b>ПОМОЩЬ</b>\n\nПоддержка: {SUPPORT_USERNAME}", 
                               reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu")).as_markup())

# ========== ВЫВОД СРЕДСТВ ==========

@dp.callback_query(F.data == "withdraw")
async def cb_withdraw_select(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    if u['stars'] < 15: return await call.answer("❌ Минимум 15 ⭐", show_alert=True)
    kb = InlineKeyboardBuilder()
    for opt in WITHDRAWAL_OPTIONS:
        if u['stars'] >= opt:
            kb.row(InlineKeyboardButton(text=f"💎 {opt} ⭐", callback_data=f"wd_run_{opt}"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text("Выберите сумму:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("wd_run_"))
async def cb_wd_execute(call: CallbackQuery):
    amt = float(call.data.split("_")[2])
    uid = call.from_user.id
    if db.get_user(uid)['stars'] >= amt:
        db.add_stars(uid, -amt)
        name = mask_name(call.from_user.username or call.from_user.first_name)
        await bot.send_message(WITHDRAWAL_CHANNEL_ID, 
                             f"📥 <b>НОВАЯ ЗАЯВКА</b>\n\n👤 Юзер: @{name}\n🆔 ID: <code>{uid}</code>\n💎 Сумма: <b>{amt} ⭐</b>",
                             reply_markup=get_admin_decision_kb(uid, amt))
        await call.message.edit_text("✅ Заявка отправлена!", reply_markup=get_main_kb(uid))
    else: await call.answer("Ошибка баланса!")

# ========== АДМИН ПАНЕЛЬ ==========



@dp.callback_query(F.data == "admin_panel")
async def cb_admin_panel(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: return
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📢 Рассылка", callback_data="a_broadcast"),
           InlineKeyboardButton(text="🎁 Создать Промо", callback_data="a_create_promo")) # Новая кнопка
    kb.row(InlineKeyboardButton(text="📢 Пост в КАНАЛ", callback_data="a_post_chan"),
           InlineKeyboardButton(text="🎭 Фейк Заявка", callback_data="a_fake_gen"))
    kb.row(InlineKeyboardButton(text="💎 Выдать ⭐", callback_data="a_give_stars")
           InlineKeyboardButton(text="⛔ Стоп Лотерея 🎰", callback_data="a_run_lottery"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text("👑 <b>АДМИН-МЕНЮ</b>", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "a_run_lottery")
async def adm_run_lottery(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: return
    
    with db.get_connection() as conn:
        data = conn.execute("SELECT pool, participants FROM lottery WHERE id = 1").fetchone()
        if not data or not data['participants']:
            return await call.answer("❌ Нет участников!", show_alert=True)
        
        participants = [p for p in data['participants'].split(',') if p]
        winner_id = int(random.choice(participants))
        win_amount = data['pool'] * 0.8  # 80% победителю
        
        # Обнуляем лотерею
        conn.execute("UPDATE lottery SET pool = 0, participants = '' WHERE id = 1")
        conn.commit()
    
    db.add_stars(winner_id, win_amount)
    
    # Рассылка всем участникам (опционально)
    await bot.send_message(winner_id, f"🥳 <b>ПОЗДРАВЛЯЕМ!</b>\nВы выиграли в лотерее: <b>{win_amount:.2f} ⭐</b>")
    await call.message.answer(f"✅ Лотерея завершена! Победитель: {winner_id}, Сумма: {win_amount}")

# 1. Вход в режим рассылки
@dp.callback_query(F.data == "a_broadcast")
async def adm_broadcast_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return await call.answer("❌ Нет доступа!", show_alert=True)
    
    await state.set_state(AdminStates.waiting_broadcast_msg)
    await call.message.edit_text(
        "📢 <b>РАССЫЛКА ПОЛЬЗОВАТЕЛЯМ</b>\n\n"
        "Отправьте сообщение (текст, фото, видео), которое хотите разослать всем.",
        reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")).as_markup()
    )

# 2. Обработка введенного сообщения
@dp.message(AdminStates.waiting_broadcast_msg)
async def adm_broadcast_confirm(message: types.Message, state: FSMContext):
    # Сохраняем ID сообщения и чата, чтобы потом его скопировать
    await state.update_data(broadcast_msg_id=message.message_id, broadcast_chat_id=message.chat.id)
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🚀 НАЧАТЬ", callback_data="confirm_broadcast_send"))
    kb.row(InlineKeyboardButton(text="❌ ОТМЕНА", callback_data="admin_panel"))
    
    await message.answer("👆 <b>Это превью сообщения.</b>\nНачать рассылку для всех пользователей?", 
                         reply_markup=kb.as_markup())

# 3. Финальная отправка (ИСПРАВЛЕННАЯ)
@dp.callback_query(F.data == "confirm_broadcast_send")
async def adm_broadcast_run(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    msg_id = data.get("broadcast_msg_id")
    from_chat = data.get("broadcast_chat_id")
    await state.clear()

    # Достаем ВСЕХ пользователей из твоей настоящей базы данных
    try:
        with db.get_connection() as conn:
            # Выбираем все ID из таблицы users
            rows = conn.execute("SELECT user_id FROM users").fetchall()
            users_list = [row['user_id'] for row in rows]
    except Exception as e:
        return await call.message.answer(f"❌ Ошибка базы данных: {e}")

    if not users_list:
        return await call.message.answer("❌ В базе данных еще нет пользователей для рассылки.")

    count = 0
    err = 0
    await call.message.edit_text(f"⏳ Рассылка запущена для {len(users_list)} чел...")

    for user_id in users_list: 
        try:
            # Копируем сообщение (текст, фото, видео и т.д.)
            await bot.copy_message(
                chat_id=user_id, 
                from_chat_id=from_chat, 
                message_id=msg_id
            )
            count += 1
            # Задержка 0.05 сек, чтобы не получить бан от Telegram за спам
            await asyncio.sleep(0.05) 
        except Exception:
            err += 1

    await call.message.answer(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📊 Успешно: {count}\n"
        f"🚫 Ошибок (бан бота): {err}"
    )

@dp.callback_query(F.data == "a_give_stars")
async def adm_give_stars_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return await call.answer("❌ Нет доступа!", show_alert=True)
    
    await state.set_state(AdminStates.waiting_give_data)
    await call.message.edit_text(
        "💎 <b>ВЫДАЧА ЗВЕЗД</b>\n\n"
        "Введите ID пользователя и количество звезд через пробел.\n"
        "Пример: <code>8364667153 100</code>",
        reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")).as_markup()
    )

@dp.message(AdminStates.waiting_give_data)
async def adm_give_stars_process(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return

    try:
        # Разделяем ввод на ID и сумму
        data = message.text.split()
        if len(data) != 2:
            return await message.answer("❌ Ошибка! Введите два числа через пробел: ID и Сумму.")
        
        target_id = int(data[0])
        amount = float(data[1])

        # Проверяем, есть ли такой юзер в базе
        user = db.get_user(target_id)
        if not user:
            return await message.answer(f"❌ Пользователь с ID <code>{target_id}</code> не найден в базе бота!")

        # Добавляем звезды
        db.add_stars(target_id, amount)
        
        # Уведомляем админа
        await message.answer(
            f"✅ <b>УСПЕШНО!</b>\n\n"
            f"Пользователю: <b>{user['first_name']}</b> (<code>{target_id}</code>)\n"
            f"Начислено: <b>{amount} ⭐</b>",
            reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 В админку", callback_data="admin_panel")).as_markup()
        )

        # Пытаемся уведомить пользователя
        try:
            await bot.send_message(target_id, f"🎁 Администратор начислил вам <b>{amount} ⭐</b>!")
        except:
            pass

        await state.clear()

    except ValueError:
        await message.answer("❌ Ошибка! Используйте только цифры. Пример: <code>12345678 50</code>")
    except Exception as e:
        await message.answer(f"❌ Произошла ошибка: {e}")
        await state.clear()

@dp.callback_query(F.data == "a_create_promo")
async def adm_promo_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_promo_data)
    await call.message.answer("Введите данные промокода через пробел:\n<code>КОД ТИП ЗНАЧЕНИЕ КОЛ_ВО</code>\n\nПримеры:\n<code>GIFT1 stars 100 10</code> (100 звезд)\n<code>ROZA gift 🌹_Роза 5</code> (5 роз)")

@dp.message(AdminStates.waiting_promo_data)
async def adm_promo_save(message: Message, state: FSMContext):
    try:
        code, r_type, val, uses = message.text.split()
        with db.get_connection() as conn:
            conn.execute("INSERT INTO promo VALUES (?, ?, ?, ?)", (code, r_type, val, int(uses)))
            conn.commit()
        await message.answer(f"✅ Промокод <code>{code}</code> создан на {uses} использований!")
        await state.clear()
    except Exception as e:
        await message.answer("❌ Ошибка! Формат: <code>КОД ТИП ЗНАЧЕНИЕ КОЛ_ВО</code>")

@dp.callback_query(F.data == "a_fake_gen")
async def adm_fake(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: return
    
    # Список реальных подарков из твоего GIFTS_PRICES
    items = list(GIFTS_PRICES.keys())
    fake_item = random.choice(items)
    
    fake_names = ["Dmitry_ST", "Sasha_Official", "Rich_Boy", "CryptoKing", "Masha_Stars", "Legenda_77"]
    name = random.choice(fake_names)
    fid = random.randint(1000000000, 9999999999) # Реалистичный ID

    # ВАЖНО: Мы передаем target_uid = 0, чтобы админ-скрипт понял, что это фейк
    text = (
        f"🎁 <b>ЗАЯВКА НА ВЫВОД </b>\n\n"
        f"👤 Юзер: @{name}\n"
        f"🆔 ID: <code>{fid}</code>\n"
        f"📦 Предмет: <b>{fake_item}</b>"
    )
    
    # Используем твою же функцию кнопок, но с ID 0
    await bot.send_message(
        WITHDRAWAL_CHANNEL_ID, 
        text, 
        reply_markup=get_admin_decision_kb(0, "GIFT") 
    )
    await call.answer("✅ Реалистичный фейк отправлен!")

@dp.message(AdminStates.waiting_channel_post)
async def adm_post_end(message: Message, state: FSMContext):
    pid = f"v_{random.randint(100, 999)}"
    kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="💰 Забрать 0.3 ⭐", callback_data=f"claim_{pid}"))
    await bot.send_message(CHANNEL_ID, message.text, reply_markup=kb.as_markup())
    await message.answer("✅ Опубликовано!")
    await state.clear()

@dp.callback_query(F.data.startswith("claim_"))
async def cb_claim(call: CallbackQuery):
    pid, uid = call.data.split("_")[1], call.from_user.id
    if not db.get_user(uid): return await call.answer("❌ Запусти бота!", show_alert=True)
    try:
        with db.get_connection() as conn:
            conn.execute("INSERT INTO post_claims (user_id, post_id) VALUES (?, ?)", (uid, pid))
            conn.commit()
        db.add_stars(uid, VIEW_REWARD)
        await call.answer(f"✅ +{VIEW_REWARD} ⭐", show_alert=True)
    except: await call.answer("❌ Уже забрал!", show_alert=True)

@dp.callback_query(F.data.startswith("adm_chat_"))
async def cb_adm_chat(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: return
    uid = call.data.split("_")[2]
    if uid == "0": return await call.answer("❌ Это фейк!", show_alert=True)
    await call.message.answer(f"🔗 Связь с юзером: tg://user?id={uid}")
    await call.answer()

@dp.callback_query(F.data.startswith("adm_app_") | F.data.startswith("adm_rej_"))
async def cb_adm_action(call: CallbackQuery):
    # Проверка, что нажал именно админ из списка
    if target_uid == 0:
    await call.message.edit_text(f"{call.message.text}\n\n<b>Итог: ✅ ОДОБРЕНО (ФЕЙК)</b>")
    return await call.answer("Это был фейк")
    
    if call.from_user.id not in ADMIN_IDS: 
        return await call.answer("❌ Вы не являетесь администратором!", show_alert=True)
    
    try:
        # Разбираем данные: adm, действие (app/rej), ID юзера, значение (число или GIFT)
        data_parts = call.data.split("_")
        action = data_parts[1]
        target_uid = int(data_parts[2])
        value = data_parts[3] # Это либо сумма "50", либо "GIFT"

        if action == "app":
            # ЛОГИКА ОДОБРЕНИЯ
            if target_uid != 0:
                reward_text = "подарка" if value == "GIFT" else f"{value} ⭐"
                await bot.send_message(target_uid, f"🎉 <b>Ваша заявка на вывод {reward_text} одобрена!</b>")
            status_text = "✅ ПРИНЯТО"
        
        else:
            # ЛОГИКА ОТКЛОНЕНИЯ
            if target_uid != 0:
                if value == "GIFT":
                    # Если подарок — просто пишем, что отклонено
                    await bot.send_message(target_uid, "❌ <b>Заявка на вывод подарка отклонена.</b>\nСвяжитесь с поддержкой для уточнения деталей.")
                else:
                    # Если звезды — возвращаем их на баланс
                    db.add_stars(target_uid, float(value))
                    await bot.send_message(target_uid, f"❌ <b>Выплата {value} ⭐ отклонена.</b>\nЗвезды возвращены на ваш баланс.")
            status_text = "❌ ОТКЛОНЕНО"

        # Обновляем сообщение в канале админа, чтобы кнопка исчезла и появился итог
        await call.message.edit_text(
            f"{call.message.text}\n\n<b>Итог: {status_text}</b> (Админ: @{call.from_user.username or 'ID ' + str(call.from_user.id)})"
        )
        await call.answer("Готово!")

    except Exception as e:
        logging.error(f"Ошибка в админ-действии: {e}")
        await call.answer("❌ Произошла ошибка при обработке", show_alert=True)
    
# --- ЦЕНЫ (УВЕЛИЧЕНЫ В 3 РАЗА) ---
GIFTS_PRICES = {
    "🧸 Мишка": 45, "❤️ Сердце": 45,
    "🎁 Подарок": 75, "🌹 Роза": 75,
    "🍰 Тортик": 150, "💐 Букет": 150, "🚀 Ракета": 150, "🍾 Шампанское": 150,
    "🏆 Кубок": 300, "💍 Колечко": 300, "💎 Алмаз": 300
}

SPECIAL_ITEMS = {
    "Ramen": 250,
    "Candle": 199,
    "Calendar": 320
}

ITEMS_PER_PAGE = 5

# --- МАГАЗИН ---
@dp.callback_query(F.data == "shop")
async def cb_shop_menu(call: CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⚡ Буст рефералов +0.1 (50 ⭐)", callback_data="buy_boost_01"))
    for item, price in GIFTS_PRICES.items():
        kb.add(InlineKeyboardButton(text=f"{item} {price}⭐", callback_data=f"buy_g_{item}"))
    kb.adjust(1, 2)
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text("✨ <b>МАГАЗИН</b>", reply_markup=kb.as_markup())

# --- ПОКУПКА БУСТА ---
@dp.callback_query(F.data == "buy_boost_01")
async def buy_boost(call: CallbackQuery):
    uid = call.from_user.id
    user = db.get_user(uid)
    if user['stars'] < 50: return await call.answer("❌ Нужно 50 ⭐", show_alert=True)
    
    db.add_stars(uid, -50)
    with db.get_connection() as conn:
        conn.execute("UPDATE users SET ref_boost = ref_boost + 0.1 WHERE user_id = ?", (uid,))
        conn.commit()
    await call.answer("🚀 Буст успешно куплен! Теперь ты получаешь больше.", show_alert=True)

@dp.callback_query(F.data.startswith("buy_g_"))
async def process_gift_buy(call: CallbackQuery):
    item_name = call.data.replace("buy_g_", "")
    price = GIFTS_PRICES.get(item_name)
    uid = call.from_user.id
    user = db.get_user(uid)

    if user['stars'] < price:
        return await call.answer(f"❌ Недостаточно звезд! Нужно {price} ⭐", show_alert=True)

    # Списываем (отрицательное число, буст не сработает)
    db.add_stars(uid, -price)
    
    with db.get_connection() as conn:
        existing = conn.execute("SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?", (uid, item_name)).fetchone()
        if existing:
            conn.execute("UPDATE inventory SET quantity = quantity + 1 WHERE user_id = ? AND item_name = ?", (uid, item_name))
        else:
            conn.execute("INSERT INTO inventory (user_id, item_name, quantity) VALUES (?, ?, 1)", (uid, item_name))
        conn.commit()

    await call.answer(f"✅ Вы купили {item_name}!", show_alert=True)

# --- ИНВЕНТАРЬ (СТРАНИЦЫ И ВЫВОД) ---
@dp.callback_query(F.data.startswith("inventory")) # Убрал нижнее подчеркивание для универсальности
async def cb_inventory_logic(call: CallbackQuery):
    # Определяем страницу
    if "_" in call.data:
        page = int(call.data.split("_")[1])
    else:
        page = 0
        
    uid = call.from_user.id
    
    with db.get_connection() as conn:
        items = conn.execute("SELECT item_name, quantity FROM inventory WHERE user_id = ?", (uid,)).fetchall()
    
    # Если инвентарь пустой
    if not items:
        kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
        return await call.message.edit_text("🎒 <b>Твой инвентарь пуст.</b>\nКупи что-нибудь в магазине!", reply_markup=kb.as_markup())

    # Логика страниц
    total_pages = (len(items) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    current_items = items[start_idx:end_idx]
    
    text = f"🎒 <b>ТВОЙ ИНВЕНТАРЬ</b> (Стр. {page+1}/{total_pages})\n\nНажми на предмет, чтобы вывести его:"
    
    kb = InlineKeyboardBuilder()
    for it in current_items:
        kb.row(InlineKeyboardButton(text=f"{it['item_name']} ({it['quantity']} шт.)", callback_data=f"pre_out_{it['item_name']}"))
    
    # Кнопки навигации
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"inventory_{page-1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"inventory_{page+1}"))
    
    if nav_row:
        kb.row(*nav_row)
        
    kb.row(InlineKeyboardButton(text="🔙 Назад в меню", callback_data="menu"))
    
    try:
        await call.message.edit_text(text, reply_markup=kb.as_markup())
    except Exception:
        # Если текст сообщения такой же (чтобы не ловить ошибку aiogram)
        await call.answer()

@dp.callback_query(F.data.startswith("pre_out_"))
async def cb_pre_out(call: CallbackQuery):
    item = call.data.replace("pre_out_", "")
    kb = InlineKeyboardBuilder().row(
        InlineKeyboardButton(text="✅ Да, вывести", callback_data=f"confirm_out_{item}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="inventory_0")
    )
    await call.message.edit_text(f"❓ Хотите вывести <b>{item}</b>?", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("confirm_out_"))
async def cb_final_out(call: CallbackQuery):
    item = call.data.replace("confirm_out_", "")
    uid = call.from_user.id
    username = call.from_user.username or "User"
    name_masked = mask_name(call.from_user.first_name)

    with db.get_connection() as conn:
        res = conn.execute("SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?", (uid, item)).fetchone()
        if not res or res['quantity'] <= 0:
            return await call.answer("❌ Предмет не найден!", show_alert=True)
        
        # Удаляем 1 штуку из инвентаря
        if res['quantity'] > 1:
            conn.execute("UPDATE inventory SET quantity = quantity - 1 WHERE user_id = ? AND item_name = ?", (uid, item))
        else:
            conn.execute("DELETE FROM inventory WHERE user_id = ? AND item_name = ?", (uid, item))
        conn.commit()

    # ОТПРАВКА АДМИНУ (в стиле старого вывода)
    # Используем твою функцию get_admin_decision_kb
    # Передаем "GIFT" вместо суммы, чтобы админ-скрипт понимал, что это предмет
    await bot.send_message(
        WITHDRAWAL_CHANNEL_ID, 
        f"🎁 <b>ЗАЯВКА НА ВЫВОД </b>\n\n"
        f"👤 Юзер: @{username}\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📦 Предмет: <b>{item}</b>",
        reply_markup=get_admin_decision_kb(uid, "GIFT") 
    )

    await call.message.edit_text(
        f"✅ Заявка на вывод <b>{item}</b> отправлена!\nОжидайте сообщения от администратора.", 
        reply_markup=get_main_kb(uid)
    )
# --- ПРОМОКОДЫ ---
@dp.callback_query(F.data == "use_promo")
async def promo_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(PromoStates.waiting_for_code)
    await call.message.answer("⌨️ Введите промокод:")

@dp.message(PromoStates.waiting_for_code)
async def promo_process(message: Message, state: FSMContext):
    code = message.text.strip()
    uid = message.from_user.id
    
    with db.get_connection() as conn:
        # 1. Проверяем, не вводил ли юзер этот код уже
        already_used = conn.execute(
            "SELECT 1 FROM promo_history WHERE user_id = ? AND code = ?", 
            (uid, code)
        ).fetchone()
        
        if already_used:
            await state.clear()
            return await message.answer("❌ Вы уже активировали этот промокод!")

        # 2. Проверяем существование кода и наличие лимита
        p = conn.execute("SELECT * FROM promo WHERE code = ? AND uses > 0", (code,)).fetchone()
        
        if p:
            # Списываем 1 общее использование
            conn.execute("UPDATE promo SET uses = uses - 1 WHERE code = ?", (code,))
            # Записываем, что этот юзер его использовал
            conn.execute("INSERT INTO promo_history (user_id, code) VALUES (?, ?)", (uid, code))
            conn.commit()
            
            if p['reward_type'] == 'stars':
                db.add_stars(uid, float(p['reward_value']))
                await message.answer(f"✅ Активировано! +{p['reward_value']} ⭐")
            else:
                item = p['reward_value']
                # Добавляем в инвентарь (проверяем наличие для UPDATE или INSERT)
                existing = conn.execute("SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?", (uid, item)).fetchone()
                if existing:
                    conn.execute("UPDATE inventory SET quantity = quantity + 1 WHERE user_id = ? AND item_name = ?", (uid, item))
                else:
                    conn.execute("INSERT INTO inventory (user_id, item_name, quantity) VALUES (?, ?, 1)", (uid, item))
                conn.commit()
                await message.answer(f"✅ Активировано! Получен предмет: {item}")
        else:
            await message.answer("❌ Код неверный, либо закончились его активации.")
            
    await state.clear()

@dp.callback_query(F.data == "special_shop")
async def cb_special_shop(call: CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🍜 Ramen — 250 ⭐", callback_data="buy_t_Ramen"))
    kb.row(InlineKeyboardButton(text="🕯 B-Day Candle — 199 ⭐", callback_data="buy_t_Candle"))
    kb.row(InlineKeyboardButton(text="🗓 Desk Calendar — 320 ⭐", callback_data="buy_t_Calendar"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text("🛒 <b>ЭКСКЛЮЗИВНЫЕ ТОВАРЫ</b>", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("buy_t_"))
async def buy_special_item(call: CallbackQuery):
    item_key = call.data.split("_")[2] # Ramen, Candle или Calendar
    full_name = {"Ramen": "🍜 Ramen", "Candle": "🕯 B-Day Candle", "Calendar": "🗓 Desk Calendar"}[item_key]
    price = SPECIAL_ITEMS[item_key]
    uid = call.from_user.id
    
    user = db.get_user(uid)
    if user['stars'] < price:
        return await call.answer("❌ Недостаточно звезд!", show_alert=True)
    
    db.add_stars(uid, -price)
    # Добавляем в инвентарь
    with db.get_connection() as conn:
        conn.execute("INSERT INTO inventory (user_id, item_name, quantity) VALUES (?, ?, 1) "
                     "ON CONFLICT(user_id, item_name) DO UPDATE SET quantity = quantity + 1", (uid, full_name))
        conn.commit()
    
    await call.answer(f"✅ {full_name} куплен!", show_alert=True)

# ========== ЗАПУСК ==========
async def web_handle(request): return web.Response(text="Bot Active")
async def main():
    app = web.Application()
    app.router.add_get("/", web_handle)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

