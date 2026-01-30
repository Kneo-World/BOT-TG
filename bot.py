"""
StarsForQuestion - Бот для заработка виртуальных звезд
ВЕРСИЯ 4.0: MAXIMUM EDITION (Admin Panel + Fake Withdrawals + Inline Decisions)
"""

import asyncio
import logging
import os
import sys
import sqlite3
import random
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from aiohttp import web

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, 
    InlineKeyboardButton, ChatMemberUpdated
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ Ошибка: BOT_TOKEN не найден!")
    sys.exit(1)

CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "nft0top")
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1003326584722")
ADMIN_IDS = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "8364667153").split(",") if id.strip()]
WITHDRAWAL_CHANNEL_ID = os.getenv("WITHDRAWAL_CHANNEL", "-1003891414947")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@Nft_top3")
PORT = int(os.environ.get("PORT", 10000))

# Экономика
DAILY_MIN, DAILY_MAX = 1, 5
LUCK_MIN, LUCK_MAX = 0, 10
LUCK_COOLDOWN = 4 * 60 * 60
REF_REWARD = 5
GROUP_REWARD = 2
WITHDRAWAL_OPTIONS = [15, 25, 50, 100]

# Фейковые настройки
FAKE_MIN_STARS = 15
FAKE_MAX_STARS = 60

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
                user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT,
                stars REAL DEFAULT 0, referrals INTEGER DEFAULT 0, total_earned REAL DEFAULT 0,
                total_withdrawn REAL DEFAULT 0, created_at TIMESTAMP, last_daily TIMESTAMP,
                last_luck TIMESTAMP, ref_code TEXT UNIQUE)""")
            
            conn.execute("""CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL,
                status TEXT DEFAULT 'pending', message_id INTEGER, created_at TIMESTAMP)""")
            
            conn.execute("""CREATE TABLE IF NOT EXISTS bot_stats (
                id INTEGER PRIMARY KEY DEFAULT 1, total_withdrawn REAL DEFAULT 1900)""")
            conn.execute("INSERT OR IGNORE INTO bot_stats (id, total_withdrawn) VALUES (1, 1900)")
            conn.commit()

    def get_user(self, user_id: int):
        with self.get_connection() as conn:
            return conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()

    def create_user(self, user_id, username, first_name, last_name):
        with self.get_connection() as conn:
            ref_code = f"ref{user_id}"
            conn.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, last_name, ref_code, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (user_id, username, first_name, last_name, ref_code, datetime.now().isoformat()))
            conn.commit()

    def add_stars(self, user_id, amount):
        with self.get_connection() as conn:
            conn.execute("UPDATE users SET stars = stars + ?, total_earned = total_earned + ? WHERE user_id = ?", (amount, amount, user_id))
            conn.commit()

    def subtract_stars(self, user_id, amount):
        with self.get_connection() as conn:
            user = self.get_user(user_id)
            if user and user['stars'] >= amount:
                conn.execute("UPDATE users SET stars = stars - ?, total_withdrawn = total_withdrawn + ? WHERE user_id = ?", (amount, amount, user_id))
                conn.commit()
                return True
            return False

    def create_withdrawal(self, user_id, amount):
        with self.get_connection() as conn:
            cursor = conn.execute("INSERT INTO withdrawals (user_id, amount, created_at) VALUES (?, ?, ?)", (user_id, amount, datetime.now().isoformat()))
            conn.commit()
            return cursor.lastrowid

    def update_withdrawal_status(self, wd_id, status, msg_id=None):
        with self.get_connection() as conn:
            if msg_id:
                conn.execute("UPDATE withdrawals SET status = ?, message_id = ? WHERE id = ?", (status, msg_id, wd_id))
            else:
                conn.execute("UPDATE withdrawals SET status = ? WHERE id = ?", (status, wd_id))
            conn.commit()

db = Database()

# ========== СОСТОЯНИЯ ==========
class WithdrawalStates(StatesGroup):
    waiting_amount = State()

class AdminStates(StatesGroup):
    waiting_broadcast = State()
    waiting_fake_name = State()
    waiting_fake_count = State()
    waiting_give_data = State()

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def censor_username(name):
    if not name: return "Аноним****"
    name = name.replace("@", "")
    if len(name) <= 4: return name[:2] + "****"
    return name[:4] + "****"

def get_main_kb(uid):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
                InlineKeyboardButton(text="🎯 Задания", callback_data="tasks"))
    builder.row(InlineKeyboardButton(text="🎮 Удача", callback_data="luck"),
                InlineKeyboardButton(text="📅 Бонус", callback_data="daily"))
    builder.row(InlineKeyboardButton(text="💎 Вывод", callback_data="withdraw"),
                InlineKeyboardButton(text="🏆 Топ", callback_data="top"))
    if uid in ADMIN_IDS:
        builder.row(InlineKeyboardButton(text="👑 Админка", callback_data="admin_panel"))
    return builder.as_markup()

def get_admin_wd_kb(wd_id, uid, amount):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="✅ Принять", callback_data=f"dec_app_{wd_id}_{uid}_{amount}"),
           InlineKeyboardButton(text="❌ Отклонить", callback_data=f"dec_rej_{wd_id}_{uid}_{amount}"))
    return kb.as_markup()

# ========== ОБРАБОТЧИКИ ==========

@dp.message(CommandStart())
async def cmd_start(message: Message):
    uid = message.from_user.id
    db.create_user(uid, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
    
    # Рефералка
    if " " in message.text:
        ref_code = message.text.split()[1]
        if ref_code.startswith("ref"):
            ref_id = int(ref_code.replace("ref", ""))
            if ref_id != uid:
                db.add_stars(ref_id, REF_REWARD)
                try: await bot.send_message(ref_id, f"👥 По вашей ссылке перешел @{message.from_user.username}! +5 ⭐")
                except: pass

    await message.answer(f"🌟 Привет, {message.from_user.first_name}! Готов зарабатывать звезды?", reply_markup=get_main_kb(uid))

@dp.callback_query(F.data == "profile")
async def cb_profile(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    text = (f"👤 <b>Личный кабинет</b>\n\n🆔 ID: <code>{u['user_id']}</code>\n"
            f"⭐ Баланс: <b>{u['stars']:.2f} звезд</b>\n👥 Рефералов: {u['referrals']}\n"
            f"💰 Всего заработано: {u['total_earned']:.2f}")
    await call.message.edit_text(text, reply_markup=get_main_kb(call.from_user.id))

@dp.callback_query(F.data == "withdraw")
async def cb_withdraw_menu(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    if u['stars'] < 15:
        return await call.answer("❌ Нужно минимум 15 звезд!", show_alert=True)
    
    kb = InlineKeyboardBuilder()
    for opt in WITHDRAWAL_OPTIONS:
        if u['stars'] >= opt:
            kb.row(InlineKeyboardButton(text=f"💎 {opt} звезд", callback_data=f"wd_confirm_{opt}"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="profile"))
    await call.message.edit_text("Выберите сумму для вывода:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("wd_confirm_"))
async def cb_wd_process(call: CallbackQuery):
    amount = float(call.data.split("_")[2])
    uid = call.from_user.id
    
    if db.subtract_stars(uid, amount):
        wd_id = db.create_withdrawal(uid, amount)
        masked = censor_username(call.from_user.username or call.from_user.first_name)
        
        # Отправка в канал
        msg = await bot.send_message(
            WITHDRAWAL_CHANNEL_ID,
            f"📥 <b>НОВАЯ ЗАЯВКА #{wd_id}</b>\n\n👤 Юзер: @{masked}\n🆔 ID: <code>{uid}</code>\n💎 Сумма: <b>{amount} ⭐</b>",
            reply_markup=get_admin_wd_kb(wd_id, uid, amount)
        )
        db.update_withdrawal_status(wd_id, "processing", msg.message_id)
        await call.message.edit_text(f"✅ Заявка #{wd_id} на {amount} ⭐ отправлена!\nОжидайте до 24 часов.", reply_markup=get_main_kb(uid))
    else:
        await call.answer("Ошибка баланса!", show_alert=True)

# Логика решения админа в канале
@dp.callback_query(F.data.startswith("dec_"))
async def handle_decision(call: CallbackQuery):
    _, action, wd_id, uid, amount = call.data.split("_")
    uid, amount = int(uid), float(amount)
    
    if action == "app":
        status_text = "✅ ВЫПОЛНЕНО"
        db.update_withdrawal_status(wd_id, "completed")
        try: await bot.send_message(uid, f"🎉 Ваша заявка #{wd_id} на {amount} ⭐ одобрена!")
        except: pass
    else:
        status_text = "❌ ОТКЛОНЕНО"
        db.update_withdrawal_status(wd_id, "rejected")
        db.add_stars(uid, amount) # Возврат
        try: await bot.send_message(uid, f"❌ Заявка #{wd_id} отклонена. {amount} ⭐ возвращены на баланс.")
        except: pass

    await call.message.edit_text(call.message.text + f"\n\n<b>Статус: {status_text}</b>\nАдмин: @{call.from_user.username}")

# ========== АДМИНКА И ФЕЙКИ ==========

@dp.callback_query(F.data == "admin_panel")
async def cb_admin_panel(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: return
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🎭 Свой Фейк", callback_data="adm_fake_one"),
           InlineKeyboardButton(text="🎲 Массовый Фейк", callback_data="adm_fake_mass"))
    kb.row(InlineKeyboardButton(text="📢 Рассылка", callback_data="adm_broadcast"),
           InlineKeyboardButton(text="💎 Выдать ⭐", callback_data="adm_give"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text("👑 <b>АДМИН-ПАНЕЛЬ 4.0</b>", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "adm_fake_one")
async def fake_one(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_fake_name)
    await call.message.answer("Введите ник/имя для фейка:")

@dp.message(AdminStates.waiting_fake_name)
async def process_fake_one(message: Message, state: FSMContext):
    name = censor_username(message.text)
    amt = random.randint(FAKE_MIN_STARS, FAKE_MAX_STARS)
    await bot.send_message(WITHDRAWAL_CHANNEL_ID, f"📥 <b>НОВАЯ ЗАЯВКА</b>\n\n👤 Юзер: @{name}\n🆔 ID: <code>777{random.randint(11,99)}</code>\n💎 Сумма: <b>{amt} ⭐</b>")
    await message.answer("✅ Фейк отправлен")
    await state.clear()

@dp.callback_query(F.data == "adm_fake_mass")
async def fake_mass(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_fake_count)
    await call.message.answer("Сколько разных фейков отправить?")

@dp.message(AdminStates.waiting_fake_count)
async def process_fake_mass(message: Message, state: FSMContext):
    if not message.text.isdigit(): return
    count = int(message.text)
    pool = ["Krypto", "Star", "Rich", "Btc", "Winner", "King", "Mavrodi", "Profit"]
    for _ in range(count):
        name = censor_username(random.choice(pool) + str(random.randint(10, 999)))
        amt = random.randint(FAKE_MIN_STARS, FAKE_MAX_STARS)
        await bot.send_message(WITHDRAWAL_CHANNEL_ID, f"📥 <b>НОВАЯ ЗАЯВКА</b>\n\n👤 Юзер: @{name}\n🆔 ID: <code>888{random.randint(111,999)}</code>\n💎 Сумма: <b>{amt} ⭐</b>")
        await asyncio.sleep(0.4)
    await message.answer(f"✅ {count} фейков в канале!")
    await state.clear()

@dp.callback_query(F.data == "daily")
async def cb_daily(call: CallbackQuery):
    uid = call.from_user.id
    u = db.get_user(uid)
    now = datetime.now()
    if u['last_daily'] and (now - datetime.fromisoformat(u['last_daily'])).days < 1:
        return await call.answer("⏳ Бонус доступен раз в 24 часа!", show_alert=True)
    
    reward = random.randint(DAILY_MIN, DAILY_MAX)
    db.add_stars(uid, reward)
    with db.get_connection() as conn:
        conn.execute("UPDATE users SET last_daily = ? WHERE user_id = ?", (now.isoformat(), uid))
        conn.commit()
    await call.message.answer(f"🎁 Вы получили {reward} ⭐!")

# ========== WEB SERVER (RENDER) ==========
async def handle(request): return web.Response(text="Bot is running")
async def main():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

