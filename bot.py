import asyncio
import os
import logging
import random
import sqlite3
from datetime import datetime, timedelta
from aiohttp import web

from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(i.strip()) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip()]
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1002390231804")
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://t.me/nft0top")

FAKE_USERS_BASE = 2450  
FAKE_WITHDRAW_MULT = 12 

# --- БАЗА ДАННЫХ ---
class Database:
    def __init__(self):
        self.db_path = "/data/stars.db" if os.path.exists("/data") else "stars.db"
        self._create_tables()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _create_tables(self):
        with self._get_conn() as conn:
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
                    reg_date TEXT
                )
            """)
            conn.commit()

    def get_user(self, uid):
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM users WHERE user_id = ?", (uid,)).fetchone()

    def get_user_count(self):
        with self._get_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def register_user(self, uid, uname, fname, ref_id=None):
        with self._get_conn() as conn:
            user = self.get_user(uid)
            if not user:
                conn.execute(
                    "INSERT INTO users (user_id, username, first_name, ref_by, reg_date) VALUES (?, ?, ?, ?, ?)",
                    (uid, uname, fname, ref_id, datetime.now().isoformat())
                )
                if ref_id and ref_id != uid:
                    conn.execute("UPDATE users SET stars = stars + 5, referrals_count = referrals_count + 1 WHERE user_id = ?", (ref_id,))
                conn.commit()

    def add_stars(self, uid, amount):
        with self._get_conn() as conn:
            conn.execute("UPDATE users SET stars = stars + ?, total_earned = total_earned + ? WHERE user_id = ?", (amount, amount, uid))
            conn.commit()

db = Database()

# --- ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# --- MIDDLEWARE ---
class SubMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user_id = data['event_from_user'].id
        if user_id in ADMIN_IDS: return await handler(event, data)
        try:
            member = await data['bot'].get_chat_member(CHANNEL_ID, user_id)
            if member.status in ["left", "kicked"]: raise Exception()
        except:
            kb = InlineKeyboardBuilder()
            kb.row(InlineKeyboardButton(text="📢 Подписаться", url=CHANNEL_LINK))
            text = "❌ <b>Доступ ограничен!</b>\nПодпишись на канал, чтобы зарабатывать."
            if isinstance(event, Message): await event.answer(text, reply_markup=kb.as_markup())
            return
        return await handler(event, data)

dp.update.middleware(SubMiddleware())

# --- КЛАВИАТУРЫ И ХЕНДЛЕРЫ ---
def main_kb():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
                InlineKeyboardButton(text="🎁 Бонус", callback_data="daily"))
    builder.row(InlineKeyboardButton(text="💎 Вывод", callback_data="withdraw"))
    return builder.as_markup()

@dp.message(CommandStart())
async def start(message: Message):
    args = message.text.split()
    ref_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    db.register_user(message.from_user.id, message.from_user.username, message.from_user.first_name, ref_id)
    
    f_users = db.get_user_count() + FAKE_USERS_BASE
    await message.answer(
        f"🌟 <b>Привет, {message.from_user.first_name}!</b>\n\n"
        f"👥 Игроков: <code>{f_users}</code>\n"
        f"💰 Выплачено: <code>{f_users * FAKE_WITHDRAW_MULT}</code> ⭐", 
        reply_markup=main_kb()
    )

@dp.callback_query(F.data == "profile")
async def profile(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    text = (
        f"👤 <b>Профиль: {u['first_name']}</b>\n"
        f"🆔 ID: <code>{u['user_id']}</code>\n"
        f"⭐ Баланс: <b>{u['stars']} звезд</b>\n"
        f"👥 Рефералы: {u['referrals_count']}"
    )
    await call.message.edit_text(text, reply_markup=main_kb())

@dp.callback_query(F.data == "daily")
async def daily(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    now = datetime.now()
    if u['last_daily'] and datetime.fromisoformat(u['last_daily']) + timedelta(days=1) > now:
        return await call.answer("❌ Бонус будет доступен позже!", show_alert=True)
    
    reward = random.randint(1, 10)
    db.add_stars(u['user_id'], reward)
    with db._get_conn() as conn:
        conn.execute("UPDATE users SET last_daily = ? WHERE user_id = ?", (now.isoformat(), u['user_id']))
        conn.commit()
    await call.answer(f"✅ Получено {reward} ⭐", show_alert=True)
    await profile(call)

# --- ВЕБ-СЕРВЕР (УБИВАЕТ ОШИБКУ PORT TIMEOUT) ---
async def handle(request):
    return web.Response(text="Bot is running")

async def start_background_tasks():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    # Render передает порт в переменную PORT
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Web server started on port {port}")

# --- ЗАПУСК ---
async def main():
    await start_background_tasks() # Запускаем сервер ПЕРЕД ботом
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
