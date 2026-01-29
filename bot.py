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
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1002390231804") # Твой канал
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://t.me/nft0top")
LOG_CHANNEL = os.getenv("WITHDRAWAL_CHANNEL", "-1002390231804") # Куда летят заявки

MIN_WITHDRAW = 15
VIEW_REWARD = 0.03 # Награда за просмотр поста

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
            conn.execute("""CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, first_name TEXT, stars REAL DEFAULT 0,
                ref_by INTEGER, referrals_count INTEGER DEFAULT 0, last_daily TEXT)""")
            # Таблица для отслеживания нажатий на посты (чтобы не абузили)
            conn.execute("""CREATE TABLE IF NOT EXISTS post_clicks (
                user_id INTEGER, post_id INTEGER, PRIMARY KEY(user_id, post_id))""")
            conn.commit()

    def get_user(self, uid):
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM users WHERE user_id = ?", (uid,)).fetchone()

    def register_user(self, uid, fname, ref_id=None):
        with self._get_conn() as conn:
            if not self.get_user(uid):
                conn.execute("INSERT INTO users (user_id, first_name, ref_by) VALUES (?, ?, ?)", (uid, fname, ref_id))
                if ref_id: conn.execute("UPDATE users SET stars = stars + 5, referrals_count = referrals_count + 1 WHERE user_id = ?", (ref_id,))
                conn.commit()

    def add_stars(self, uid, amount):
        with self._get_conn() as conn:
            conn.execute("UPDATE users SET stars = stars + ? WHERE user_id = ?", (amount, uid))
            conn.commit()

db = Database()

# --- МЕХАНИКА И БОТ ---
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# Клавиатура
def main_kb(uid):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="👤 Профиль", callback_data="profile"), InlineKeyboardButton(text="🎁 Бонус", callback_data="daily"))
    kb.row(InlineKeyboardButton(text="💎 Вывод", callback_data="withdraw"))
    if uid in ADMIN_IDS:
        kb.row(InlineKeyboardButton(text="📢 Рассылка поста в канал", callback_data="admin_post"))
    return kb.as_markup()

@dp.message(CommandStart())
async def start(message: Message):
    ref_id = int(message.text.split()[1]) if len(message.text.split()) > 1 and message.text.split()[1].isdigit() else None
    db.register_user(message.from_user.id, message.from_user.first_name, ref_id)
    await message.answer(f"🌟 Привет, {message.from_user.first_name}! Зарабатывай звезды, просматривая посты и приглашая друзей.", reply_markup=main_kb(message.from_user.id))

@dp.callback_query(F.data == "profile")
async def profile(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    await call.message.edit_text(f"👤 <b>Профиль</b>\n\n⭐ Баланс: {u['stars']:.2f} звезд\n👥 Рефералы: {u['referrals_count']}", reply_markup=main_kb(call.from_user.id))

# --- ВЫВОД (ИСПРАВЛЕН) ---
@dp.callback_query(F.data == "withdraw")
async def withdraw(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    if u['stars'] < MIN_WITHDRAW:
        return await call.answer(f"❌ Минимум для вывода: {MIN_WITHDRAW} ⭐", show_alert=True)
    
    # Списываем баланс
    with db._get_conn() as conn:
        conn.execute("UPDATE users SET stars = 0 WHERE user_id = ?", (u['user_id'],))
        conn.commit()

    # Шлем сообщение в канал логов
    try:
        await bot.send_message(LOG_CHANNEL, f"💰 <b>НОВАЯ ЗАЯВКА</b>\n\n👤 Игрок: {call.from_user.full_name} (ID: <code>{u['user_id']}</code>)\n💵 Сумма: <b>{u['stars']:.2f} ⭐</b>")
        await call.message.answer("✅ Заявка отправлена! Ожидайте выплату в течение 24 часов.")
    except Exception as e:
        logging.error(f"Ошибка вывода: {e}")
        await call.answer("⚠️ Ошибка отправки заявки админу. Обратитесь в поддержку.", show_alert=True)

# --- СИСТЕМА ПРОСМОТРОВ (КАК НА КАРТИНКЕ) ---
@dp.callback_query(F.data == "admin_post")
async def send_post_to_channel(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: return
    post_id = random.randint(1000, 9999) # Уникальный ID для этого поста
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="💰 Забрать 0.03 ⭐", callback_data=f"get_v_{post_id}"))
    
    await bot.send_message(CHANNEL_ID, "📢 <b>Новый пост для заработка!</b>\nНажмите на кнопку ниже, чтобы получить награду за просмотр.", reply_markup=kb.as_markup())
    await call.answer("Пост отправлен в канал!", show_alert=True)

@dp.callback_query(F.data.startswith("get_v_"))
async def collect_view_reward(call: CallbackQuery):
    post_id = int(call.data.split("_")[-1])
    uid = call.from_user.id
    
    # Проверяем подписку (Обязательно!)
    try:
        member = await bot.get_chat_member(CHANNEL_ID, uid)
        if member.status in ['left', 'kicked']: raise Exception()
    except:
        return await call.answer("❌ Сначала подпишись на канал!", show_alert=True)

    # Проверяем, не нажимал ли уже
    with db._get_conn() as conn:
        already = conn.execute("SELECT 1 FROM post_clicks WHERE user_id = ? AND post_id = ?", (uid, post_id)).fetchone()
        if already:
            return await call.answer("❌ Вы уже получили награду за этот пост!", show_alert=True)
        
        conn.execute("INSERT INTO post_clicks VALUES (?, ?)", (uid, post_id))
        conn.commit()
    
    db.add_stars(uid, VIEW_REWARD)
    await call.answer(f"✅ Начислено {VIEW_REWARD} ⭐", show_alert=True)

# --- СЕРВЕР И ЗАПУСК ---
async def handle(request): return web.Response(text="Bot Live")
async def main():
    app = web.Application(); app.router.add_get("/", handle)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

