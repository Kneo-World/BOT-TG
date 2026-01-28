import asyncio
import logging
import os
import sys
import sqlite3
import random
from datetime import datetime, timedelta
from typing import Optional, Union, List

from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
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
from aiogram.exceptions import TelegramBadRequest

# =================================================================
# КОНФИГУРАЦИЯ (Настрой через переменные окружения)
# =================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "ТВОЙ_ТОКЕН_ТУТ")
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1001234567890")  # ID канала для проверки подписки
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://t.me/nft0top")
WITHDRAWAL_LOG_CHANNEL = os.getenv("WITHDRAWAL_CHANNEL", "-1001234567890") # Где админы апрувят
ADMIN_IDS = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "546416518").split(",") if id.strip()]
SUPPORT_USER = "@Nft_top3"

# Настройки экономики (Реальные)
DAILY_REWARDS = (1, 5)
LUCK_REWARDS = (0, 10)
REF_BONUS = 5
GROUP_BONUS = 2
MIN_WITHDRAW = 15

# Настройки накрутки (Для пользователей)
FAKE_USERS_BASE = 1250  # Стартовое число
FAKE_WITHDRAW_MULT = 15 # Множитель выплат
FAKE_ONLINE_RANGE = (40, 120)

# =================================================================
# БАЗА ДАННЫХ (Расширенная архитектура)
# =================================================================
class Database:
    def __init__(self, db_path="stars_pro.db"):
        self.db_path = db_path
        self._create_tables()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _create_tables(self):
        with self._get_conn() as conn:
            # Юзеры
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    stars INTEGER DEFAULT 0,
                    ref_by INTEGER,
                    referrals_count INTEGER DEFAULT 0,
                    total_earned INTEGER DEFAULT 0,
                    last_daily TEXT,
                    last_luck TEXT,
                    reg_date TEXT
                )
            """)
            # Транзакции (для истории и аудита)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount INTEGER,
                    type TEXT,
                    timestamp TEXT
                )
            """)
            # Заявки на вывод
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cashouts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount INTEGER,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT
                )
            """)
            conn.commit()

    # --- Методы Юзера ---
    def register_user(self, uid, uname, fname, ref_id=None):
        with self._get_conn() as conn:
            user = conn.execute("SELECT * FROM users WHERE user_id = ?", (uid,)).fetchone()
            if not user:
                now = datetime.now().isoformat()
                conn.execute(
                    "INSERT INTO users (user_id, username, first_name, ref_by, reg_date) VALUES (?, ?, ?, ?, ?)",
                    (uid, uname, fname, ref_id, now)
                )
                if ref_id:
                    conn.execute("UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id = ?", (ref_id,))
                    self.add_stars(ref_id, REF_BONUS, "referral")
                conn.commit()
                return True
            return False

    def get_user(self, uid):
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM users WHERE user_id = ?", (uid,)).fetchone()

    def add_stars(self, uid, amount, tx_type):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE users SET stars = stars + ?, total_earned = total_earned + ? WHERE user_id = ?",
                (amount, amount, uid)
            )
            conn.execute(
                "INSERT INTO logs (user_id, amount, type, timestamp) VALUES (?, ?, ?, ?)",
                (uid, amount, tx_type, datetime.now().isoformat())
            )
            conn.commit()

    def spend_stars(self, uid, amount, tx_type) -> bool:
        with self._get_conn() as conn:
            user = conn.execute("SELECT stars FROM users WHERE user_id = ?", (uid,)).fetchone()
            if user and user['stars'] >= amount:
                conn.execute("UPDATE users SET stars = stars - ? WHERE user_id = ?", (amount, uid))
                conn.execute(
                    "INSERT INTO logs (user_id, amount, type, timestamp) VALUES (?, ?, ?, ?)",
                    (uid, -amount, tx_type, datetime.now().isoformat())
                )
                conn.commit()
                return True
            return False

    # --- Статистика ---
    def get_global_stats(self):
        with self._get_conn() as conn:
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            earned = conn.execute("SELECT SUM(total_earned) FROM users").fetchone()[0] or 0
            withdrawn = conn.execute("SELECT SUM(amount) FROM cashouts WHERE status = 'approved'").fetchone()[0] or 0
            return {"u": users, "e": earned, "w": withdrawn}

db = Database()

# =================================================================
# MIDDLEWARE (Проверка подписки)
# =================================================================
class SubscriptionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user_id = data['event_from_user'].id
        if user_id in ADMIN_IDS:
            return await handler(event, data)
        
        try:
            member = await data['bot'].get_chat_member(CHANNEL_ID, user_id)
            if member.status in ["left", "kicked"]:
                raise Exception()
        except:
            kb = InlineKeyboardBuilder()
            kb.row(InlineKeyboardButton(text="📢 Подписаться на канал", url=CHANNEL_LINK))
            kb.row(InlineKeyboardButton(text="🔄 Проверить подписку", callback_data="check_sub"))
            
            msg_text = "⚠️ <b>Доступ заблокирован!</b>\n\nЧтобы пользоваться ботом и зарабатывать звезды, подпишись на наш официальный канал."
            if isinstance(event, Message):
                await event.answer(msg_text, reply_markup=kb.as_markup())
            elif isinstance(event, CallbackQuery):
                await event.answer("Сначала подпишись!", show_alert=True)
            return
        
        return await handler(event, data)

# =================================================================
# ЛОГИКА ОТОБРАЖЕНИЯ (FAKE STATS)
# =================================================================
def get_stats_text():
    real = db.get_global_stats()
    f_users = real['u'] + FAKE_USERS_BASE
    f_stars = real['e'] * FAKE_WITHDRAW_MULT + 5000
    online = random.randint(*FAKE_ONLINE_RANGE)
    return f"👥 Игроков: {f_users} | 🟢 Онлайн: {online}\n💰 Выплачено: {f_stars} ⭐"

def main_menu_kb():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="👤 Профиль", callback_data="profile"), 
                InlineKeyboardButton(text="🎯 Задания", callback_data="tasks"))
    builder.row(InlineKeyboardButton(text="🎮 Удача", callback_data="game_luck"), 
                InlineKeyboardButton(text="👥 Рефералы", callback_data="refs"))
    builder.row(InlineKeyboardButton(text="🏆 Топ игроков", callback_data="top_stars"), 
                InlineKeyboardButton(text="📅 Бонус", callback_data="daily_get"))
    builder.row(InlineKeyboardButton(text="💎 Вывести звезды", callback_data="withdraw_start"))
    return builder.as_markup()

# =================================================================
# ХЕНДЛЕРЫ
# =================================================================
dp = Dispatcher(storage=MemoryStorage())
dp.message.outer_middleware(SubscriptionMiddleware())
dp.callback_query.outer_middleware(SubscriptionMiddleware())

@dp.message(CommandStart())
async def cmd_start(message: Message):
    uid = message.from_user.id
    uname = message.from_user.username
    fname = message.from_user.first_name
    
    # Рефералка
    ref_id = None
    args = message.text.split()
    if len(args) > 1 and args[1].isdigit():
        potential_ref = int(args[1])
        if potential_ref != uid:
            ref_id = potential_ref

    db.register_user(uid, uname, fname, ref_id)
    
    await message.answer(
        f"<b>Привет, {fname}! Добро пожаловать в StarsForQuestion!</b>\n\n"
        f"Здесь ты можешь выполнять простые задания и получать настоящие звезды Telegram.\n\n"
        f"📊 {get_stats_text()}",
        reply_markup=main_menu_kb()
    )

@dp.callback_query(F.data == "profile")
async def view_profile(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    # Пофиксили баг отображения: берем данные из БД, а не из объекта call
    text = (
        f"<b>👤 Твой профиль:</b>\n"
        f"──────────────────\n"
        f"🆔 Твой ID: <code>{u['user_id']}</code>\n"
        f"⭐ Баланс: <b>{u['stars']} звезд</b>\n"
        f"👥 Рефералов: {u['referrals_count']}\n"
        f"💰 Заработано всего: {u['total_earned']}\n"
        f"──────────────────\n"
        f"📢 {get_stats_text()}"
    )
    await call.message.edit_text(text, reply_markup=main_menu_kb())

@dp.callback_query(F.data == "daily_get")
async def get_daily(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    now = datetime.now()
    
    if u['last_daily']:
        last = datetime.fromisoformat(u['last_daily'])
        if now < last + timedelta(days=1):
            remaining = (last + timedelta(days=1)) - now
            hours = remaining.seconds // 3600
            return await call.answer(f"⏳ Бонус будет доступен через {hours}ч.", show_alert=True)
    
    reward = random.randint(*DAILY_REWARDS)
    db.add_stars(u['user_id'], reward, "daily")
    with db._get_conn() as conn:
        conn.execute("UPDATE users SET last_daily = ? WHERE user_id = ?", (now.isoformat(), u['user_id']))
        conn.commit()
        
    await call.answer(f"🎉 Поздравляем! Ты получил {reward} ⭐", show_alert=True)
    await view_profile(call)

@dp.callback_query(F.data == "game_luck")
async def game_luck(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    now = datetime.now()
    
    if u['last_luck']:
        last = datetime.fromisoformat(u['last_luck'])
        if now < last + timedelta(hours=4):
            return await call.answer("🎮 Играть можно раз в 4 часа!", show_alert=True)
            
    reward = random.randint(*LUCK_REWARDS)
    db.add_stars(u['user_id'], reward, "luck_game")
    with db._get_conn() as conn:
        conn.execute("UPDATE users SET last_luck = ? WHERE user_id = ?", (now.isoformat(), u['user_id']))
        conn.commit()
    
    if reward > 0:
        await call.answer(f"🎰 Удача! Выпало: {reward} ⭐", show_alert=True)
    else:
        await call.answer("🎰 Пусто... Попробуй позже!", show_alert=True)
    await view_profile(call)

@dp.callback_query(F.data == "withdraw_start")
async def withdraw_start(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    if u['stars'] < MIN_WITHDRAW:
        return await call.answer(f"❌ Минимальный вывод: {MIN_WITHDRAW} ⭐. У тебя пока {u['stars']}.", show_alert=True)
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="✅ Подтвердить заявку", callback_data="withdraw_confirm"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="profile"))
    
    await call.message.edit_text(
        f"<b>💎 Оформление вывода</b>\n\n"
        f"Доступно к выводу: <b>{u['stars']} звезд</b>\n"
        f"Минималка: {MIN_WITHDRAW}\n\n"
        f"<i>После подтверждения заявка уйдет админам. Срок обработки: 1-24 часа.</i>",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data == "withdraw_confirm")
async def withdraw_confirm(call: CallbackQuery, bot: Bot):
    u = db.get_user(call.from_user.id)
    amount = u['stars']
    
    if db.spend_stars(u['user_id'], amount, "withdraw_request"):
        # Создаем запись в БД
        with db._get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO cashouts (user_id, amount, created_at) VALUES (?, ?, ?)",
                (u['user_id'], amount, datetime.now().isoformat())
            )
            wd_id = cur.lastrowid
            conn.commit()
        
        # Инфо админам
        admin_kb = InlineKeyboardBuilder()
        admin_kb.row(InlineKeyboardButton(text="✅ Выплачено", callback_data=f"adm_pay_{wd_id}"))
        
        await bot.send_message(
            WITHDRAWAL_LOG_CHANNEL,
            f"💰 <b>НОВАЯ ЗАЯВКА #{wd_id}</b>\n"
            f"Юзер: {call.from_user.full_name} (@{call.from_user.username})\n"
            f"ID: <code>{u['user_id']}</code>\n"
            f"Сумма: <b>{amount} звезд</b>",
            reply_markup=admin_kb.as_markup()
        )
        
        await call.message.edit_text("🚀 <b>Заявка успешно создана!</b>\nАдмины проверят её в ближайшее время.", reply_markup=main_menu_kb())
    else:
        await call.answer("Ошибка баланса.")

# =================================================================
# АДМИН-ПАНЕЛЬ (РЕАЛЬНЫЕ ДАННЫЕ)
# =================================================================
@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    s = db.get_global_stats()
    text = (
        f"👑 <b>АДМИН-ПАНЕЛЬ (РЕАЛ)</b>\n"
        f"──────────────────\n"
        f"👥 Юзеров в БД: {s['u']}\n"
        f"⭐ Звезд заработано: {s['e']}\n"
        f"💸 Выплачено реально: {s['w']}\n"
        f"──────────────────\n"
        f"Команды:\n"
        f"/give [id] [кол-во] - Выдать звезды"
    )
    await message.answer(text)

@dp.message(Command("give"))
async def cmd_give(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    try:
        args = message.text.split()
        target_id = int(args[1])
        amount = int(args[2])
        db.add_stars(target_id, amount, "admin_gift")
        await message.answer(f"✅ Выдано {amount} звезд юзеру {target_id}")
    except:
        await message.answer("Ошибка. Юзай: /give ID СУММА")

# =================================================================
# ЗАПУСК
# =================================================================
async def main():
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    logging.basicConfig(level=logging.INFO)
    print("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Бот выключен")

