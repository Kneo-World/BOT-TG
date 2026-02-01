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
ADMIN_IDS = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "8364667153").split(",") if id.strip()]
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
    builder.row(InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
                InlineKeyboardButton(text="🎯 Задания", callback_data="tasks"))
    builder.row(InlineKeyboardButton(text="🛒 Магазин", callback_data="shop"), # Новая
                InlineKeyboardButton(text="🎒 Инвентарь", callback_data="inventory")) # Вместо вывода
    builder.row(InlineKeyboardButton(text="🎮 Удача", callback_data="luck"),
                InlineKeyboardButton(text="📅 Бонус", callback_data="daily"))
    builder.row(InlineKeyboardButton(text="👥 Рефералы", callback_data="referrals"),
                InlineKeyboardButton(text="🎁 Промокод", callback_data="use_promo")) # Новая
    builder.row(InlineKeyboardButton(text="🏆 Топ", callback_data="top"),
                InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help"))
    
    if uid in ADMIN_IDS:
        builder.row(InlineKeyboardButton(text="👑 Админка", callback_data="admin_panel"))
    return builder.as_markup()

def get_admin_decision_kb(uid, amount):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Принять", callback_data=f"adm_app_{uid}_{amount}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_rej_{uid}_{amount}"))
    builder.row(InlineKeyboardButton(text="✉️ Написать в ЛС", callback_data=f"adm_chat_{uid}"))
    return builder.as_markup()

# ========== ОБРАБОТЧИКИ ЮЗЕРОВ ==========

@dp.message(CommandStart())
async def cmd_start(message: Message):
    uid = message.from_user.id
    if not db.get_user(uid):
        db.create_user(uid, message.from_user.username, message.from_user.first_name)
        if " " in message.text:
            args = message.text.split()[1]
            if args.startswith("ref"):
                try:
                    ref_id = int(args.replace("ref", ""))
                    if ref_id != uid:
                        db.add_stars(ref_id, REF_REWARD)
                        with db.get_connection() as conn:
                            conn.execute("UPDATE users SET referrals = referrals + 1 WHERE user_id = ?", (ref_id,))
                            conn.commit()
                        try: await bot.send_message(ref_id, f"👥 Реферал! +{REF_REWARD} ⭐")
                        except: pass
                except: pass
    await message.answer(f"🌟 Привет! Зарабатывай звезды и выводи их.", reply_markup=get_main_kb(uid))

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
    await call.message.edit_text("🎯 <b>ЗАДАНИЯ</b>\n\n1. Реферал: 5.0 ⭐\n2. Группа: 1.0 ⭐\n3. Посты в канале: 0.3 ⭐", 
                               reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu")).as_markup())

@dp.callback_query(F.data == "top")
async def cb_top(call: CallbackQuery):
    text = "🏆 <b>ТОП-5 ЛИДЕРОВ</b>\n\n1. MewMarket**** — 1420 ⭐\n2. Usemd**** — 410 ⭐\n3. Admin**** — 350 ⭐\n4. Lols**** — 210 ⭐\n5. fuful**** — 190 ⭐"
    await call.message.edit_text(text, reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu")).as_markup())

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
    kb.row(InlineKeyboardButton(text="💎 Выдать ⭐", callback_data="a_give_stars"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text("👑 <b>АДМИН-МЕНЮ</b>", reply_markup=kb.as_markup())

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
    name, fid, amt = mask_name(generate_fake_user()), generate_fake_id(), random.choice(WITHDRAWAL_OPTIONS)
    await bot.send_message(WITHDRAWAL_CHANNEL_ID, 
                         f"📥 <b>НОВАЯ ЗАЯВКА</b>\n\n👤 Юзер: @{name}\n🆔 ID: <code>{fid}</code>\n💎 Сумма: <b>{amt} ⭐</b>",
                         reply_markup=get_admin_decision_kb(0, amt))
    await call.answer("✅ Фейк создан!")

@dp.callback_query(F.data == "a_post_chan")
async def adm_post_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_channel_post)
    await call.message.answer("Введите текст поста для КАНАЛА (0.3 ⭐):")

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
    if call.from_user.id not in ADMIN_IDS: return await call.answer("❌ Не админ!")
    d = call.data.split("_")
    act, uid, amt = d[1], int(d[2]), float(d[3])
    if act == "app":
        if uid != 0: await bot.send_message(uid, f"🎉 Выплата {amt} ⭐ одобрена!")
        res = "✅ ВЫПЛАЧЕНО"
    else:
        if uid != 0: db.add_stars(uid, amt); await bot.send_message(uid, f"❌ Отклонено. {amt} ⭐ возвращены.")
        res = "❌ ОТКЛОНЕНО"
    await call.message.edit_text(call.message.text + f"\n\n<b>Итог: {res}</b>")

# --- ЦЕНЫ (УВЕЛИЧЕНЫ В 3 РАЗА) ---
GIFTS_PRICES = {
    "🧸 Мишка": 45, "❤️ Сердце": 45,
    "🎁 Подарок": 75, "🌹 Роза": 75,
    "🍰 Тортик": 150, "💐 Букет": 150, "🚀 Ракета": 150, "🍾 Шампанское": 150,
    "🏆 Кубок": 300, "💍 Колечко": 300, "💎 Алмаз": 300
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
    username = call.from_user.username or "NoName"

    with db.get_connection() as conn:
        res = conn.execute("SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?", (uid, item)).fetchone()
        if not res or res['quantity'] <= 0:
            return await call.answer("❌ Предмет не найден!", show_alert=True)
        
        if res['quantity'] > 1:
            conn.execute("UPDATE inventory SET quantity = quantity - 1 WHERE user_id = ? AND item_name = ?", (uid, item))
        else:
            conn.execute("DELETE FROM inventory WHERE user_id = ? AND item_name = ?", (uid, item))
        conn.commit()

    # Уведомление в канал выплат
    await bot.send_message(WITHDRAWAL_CHANNEL_ID, 
        f"📦 <b>ЗАЯВКА НА ВЫВОД ПРЕДМЕТА</b>\n\n👤 Юзер: @{username}\n🆔 ID: <code>{uid}</code>\n🎁 Предмет: <b>{item}</b>")

    await call.message.edit_text(f"🚀 Заявка на вывод <b>{item}</b> отправлена! Админ свяжется с вами.", reply_markup=get_main_kb(uid))

# --- ПРОМОКОДЫ ---
@dp.callback_query(F.data == "use_promo")
async def promo_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(PromoStates.waiting_for_code)
    await call.message.answer("⌨️ Введите промокод:")

@dp.message(PromoStates.waiting_for_code)
async def promo_process(message: Message, state: FSMContext):
    code = message.text
    with db.get_connection() as conn:
        p = conn.execute("SELECT * FROM promo WHERE code = ? AND uses > 0", (code,)).fetchone()
        if p:
            conn.execute("UPDATE promo SET uses = uses - 1 WHERE code = ?", (code,))
            conn.commit()
            if p['reward_type'] == 'stars':
                db.add_stars(message.from_user.id, float(p['reward_value']))
                await message.answer(f"✅ Активировано! +{p['reward_value']} ⭐")
            else: # Если это подарок (например 🌹_Роза)
                item = p['reward_value']
                conn.execute("INSERT INTO inventory (user_id, item_name) VALUES (?, ?)", (message.from_user.id, item))
                conn.commit()
                await message.answer(f"✅ Активировано! Получен подарок: {item}")
        else:
            await message.answer("❌ Код неверный или закончился.")
    await state.clear()

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

