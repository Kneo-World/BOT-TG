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
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

# ==========================================================
# 1. КОНФИГУРАЦИЯ (ОБЯЗАТЕЛЬНО ПРОВЕРЬ В RENDER)
# ==========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(i.strip()) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip()]
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1002390231804")
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://t.me/nft0top")
WITHDRAWAL_LOG_CHANNEL = os.getenv("WITHDRAWAL_CHANNEL", "-1002390231804")

# Экономика
MIN_WITHDRAW = 15
REF_REWARD = 5
DAILY_REWARD = (1, 10)

# Фейк-статистика для пользователей
FAKE_USERS_BASE = 3240  
FAKE_WITHDRAW_MULT = 18 

class AdminStates(StatesGroup):
    mailing = State()
    giving_stars_id = State()
    giving_stars_amount = State()

# ==========================================================
# 2. БАЗА ДАННЫХ
# ==========================================================
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cashouts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount INTEGER,
                    status TEXT DEFAULT 'pending',
                    date TEXT
                )
            """)
            conn.commit()

    def get_user(self, uid):
        with self._get_conn() as conn:
            return conn.execute("SELECT * FROM users WHERE user_id = ?", (uid,)).fetchone()

    def get_all_user_ids(self):
        with self._get_conn() as conn:
            return [row[0] for row in conn.execute("SELECT user_id FROM users").fetchall()]

    def register_user(self, uid, uname, fname, ref_id=None):
        with self._get_conn() as conn:
            user = self.get_user(uid)
            if not user:
                conn.execute(
                    "INSERT INTO users (user_id, username, first_name, ref_by, reg_date) VALUES (?, ?, ?, ?, ?)",
                    (uid, uname, fname, ref_id, datetime.now().isoformat())
                )
                if ref_id and ref_id != uid:
                    conn.execute("UPDATE users SET stars = stars + ?, total_earned = total_earned + ?, referrals_count = referrals_count + 1 WHERE user_id = ?", 
                                (REF_REWARD, REF_REWARD, ref_id))
                conn.commit()

    def add_stars(self, uid, amount):
        with self._get_conn() as conn:
            conn.execute("UPDATE users SET stars = stars + ?, total_earned = total_earned + ? WHERE user_id = ?", (amount, amount, uid))
            conn.commit()

    def spend_stars(self, uid, amount):
        with self._get_conn() as conn:
            user = self.get_user(uid)
            if user and user['stars'] >= amount:
                conn.execute("UPDATE users SET stars = stars - ? WHERE user_id = ?", (amount, uid))
                conn.commit()
                return True
            return False

    def get_top_users(self, limit=10):
        with self._get_conn() as conn:
            return conn.execute("SELECT first_name, stars FROM users ORDER BY stars DESC LIMIT ?", (limit,)).fetchall()

    def get_stats(self):
        with self._get_conn() as conn:
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            cashouts = conn.execute("SELECT COUNT(*) FROM cashouts").fetchone()[0]
            return users, cashouts

db = Database()

# ==========================================================
# 3. ИНИЦИАЛИЗАЦИЯ
# ==========================================================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

class SubMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user_id = data['event_from_user'].id
        if user_id in ADMIN_IDS: return await handler(event, data)
        try:
            member = await data['bot'].get_chat_member(CHANNEL_ID, user_id)
            if member.status in ["left", "kicked"]: raise Exception()
        except:
            kb = InlineKeyboardBuilder()
            kb.row(InlineKeyboardButton(text="📢 ПОДПИСАТЬСЯ", url=CHANNEL_LINK))
            kb.row(InlineKeyboardButton(text="✅ Я ПОДПИСАЛСЯ", callback_data="profile"))
            text = "⚠️ <b>ОШИБКА ДОСТУПА!</b>\n\nПодпишись на канал, чтобы пользоваться ботом."
            if isinstance(event, Message): await event.answer(text, reply_markup=kb.as_markup())
            return
        return await handler(event, data)

dp.update.middleware(SubMiddleware())

# ==========================================================
# 4. КЛАВИАТУРЫ
# ==========================================================
def main_kb(user_id):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="👤 ПРОФИЛЬ", callback_data="profile"),
                InlineKeyboardButton(text="🎁 БОНУС", callback_data="daily"))
    builder.row(InlineKeyboardButton(text="🏆 ТОП", callback_data="top"),
                InlineKeyboardButton(text="👥 РЕФЕРАЛЫ", callback_data="refs"))
    builder.row(InlineKeyboardButton(text="💎 ВЫВОД", callback_data="withdraw_main"))
    if user_id in ADMIN_IDS:
        builder.row(InlineKeyboardButton(text="👑 АДМИН ПАНЕЛЬ", callback_data="admin_panel"))
    return builder.as_markup()

def admin_kb():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📣 РАССЫЛКА", callback_data="admin_mail"))
    builder.row(InlineKeyboardButton(text="💰 ВЫДАТЬ ЗВЕЗДЫ", callback_data="admin_give"))
    builder.row(InlineKeyboardButton(text="🔙 НАЗАД", callback_data="profile"))
    return builder.as_markup()

# ==========================================================
# 5. ХЕНДЛЕРЫ
# ==========================================================

@dp.message(CommandStart())
async def cmd_start(message: Message):
    args = message.text.split()
    ref_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    db.register_user(message.from_user.id, message.from_user.username, message.from_user.first_name, ref_id)
    u_count, _ = db.get_stats()
    f_users = u_count + FAKE_USERS_BASE
    await message.answer(
        f"👋 <b>Привет!</b>\n\nИгроков: <code>{f_users}</code>\nВыплачено: <code>{f_users * FAKE_WITHDRAW_MULT}</code> ⭐", 
        reply_markup=main_kb(message.from_user.id)
    )

@dp.callback_query(F.data == "profile")
async def view_profile(call: CallbackQuery, state: FSMContext):
    await state.clear()
    u = db.get_user(call.from_user.id)
    text = (f"👤 <b>ПРОФИЛЬ:</b>\n"
            f"🆔 ID: <code>{u['user_id']}</code>\n"
            f"⭐ Баланс: <b>{u['stars']} звезд</b>\n"
            f"👥 Рефералы: {u['referrals_count']}")
    await call.message.edit_text(text, reply_markup=main_kb(call.from_user.id))

@dp.callback_query(F.data == "daily")
async def get_daily(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    now = datetime.now()
    if u['last_daily'] and datetime.fromisoformat(u['last_daily']) + timedelta(days=1) > now:
        return await call.answer("❌ Бонус раз в 24 часа!", show_alert=True)
    reward = random.randint(*DAILY_REWARD)
    db.add_stars(u['user_id'], reward)
    with db._get_conn() as conn:
        conn.execute("UPDATE users SET last_daily = ? WHERE user_id = ?", (now.isoformat(), u['user_id']))
        conn.commit()
    await call.answer(f"🎉 Вы получили {reward} звезд!", show_alert=True)
    await view_profile(call, None)

@dp.callback_query(F.data == "top")
async def view_top(call: CallbackQuery):
    top_list = db.get_top_users()
    text = "🏆 <b>ТОП-10 ИГРОКОВ:</b>\n\n"
    for i, user in enumerate(top_list, 1):
        text += f"{i}. {user['first_name']} — <code>{user['stars'] + 150}</code> ⭐\n"
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 НАЗАД", callback_data="profile"))
    await call.message.edit_text(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data == "refs")
async def view_refs(call: CallbackQuery):
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={call.from_user.id}"
    text = f"👥 <b>РЕФЕРАЛЫ</b>\n\nЗа друга: <b>{REF_REWARD} ⭐</b>\n\nСсылка:\n<code>{ref_link}</code>"
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 НАЗАД", callback_data="profile"))
    await call.message.edit_text(text, reply_markup=builder.as_markup())

# --- ВЫВОД ---
@dp.callback_query(F.data == "withdraw_main")
async def withdraw_menu(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    if u['stars'] < MIN_WITHDRAW:
        return await call.answer(f"❌ Минимум: {MIN_WITHDRAW} ⭐", show_alert=True)
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="✅ ПОДТВЕРДИТЬ", callback_data="withdraw_confirm"))
    kb.row(InlineKeyboardButton(text="🔙 НАЗАД", callback_data="profile"))
    await call.message.edit_text(f"💎 <b>ВЫВОД {u['stars']} ЗВЕЗД?</b>", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "withdraw_confirm")
async def withdraw_final(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    amount = u['stars']
    if db.spend_stars(u['user_id'], amount):
        with db._get_conn() as conn:
            cur = conn.execute("INSERT INTO cashouts (user_id, amount, date) VALUES (?, ?, ?)", (u['user_id'], amount, datetime.now().isoformat()))
            oid = cur.lastrowid
            conn.commit()
        await bot.send_message(WITHDRAWAL_LOG_CHANNEL, f"💰 <b>ЗАЯВКА #{oid}</b>\nЮзер: {u['user_id']}\nСумма: {amount} ⭐")
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔙 НАЗАД", callback_data="profile"))
        await call.message.edit_text(f"✅ <b>ЗАЯВКА #{oid} ПРИНЯТА!</b>", reply_markup=builder.as_markup())

# --- АДМИНКА ---
@dp.callback_query(F.data == "admin_panel")
async def open_admin(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: return
    u_count, c_count = db.get_stats()
    await call.message.edit_text(f"👑 <b>АДМИНКА</b>\nЮзеров: {u_count}\nЗаявок: {c_count}", reply_markup=admin_kb())

@dp.callback_query(F.data == "admin_mail")
async def mail_1(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.mailing)
    await call.message.edit_text("📝 <b>Текст рассылки (или 'отмена'):</b>")

@dp.message(AdminStates.mailing)
async def mail_2(message: Message, state: FSMContext):
    if message.text.lower() == "отмена":
        await state.clear()
        return await message.answer("Отменено", reply_markup=main_kb(message.from_user.id))
    users = db.get_all_user_ids()
    await message.answer("🚀 Рассылаю...")
    for uid in users:
        try:
            await bot.send_message(uid, message.text)
            await asyncio.sleep(0.05)
        except: pass
    await state.clear()
    await message.answer("✅ Готово!")

@dp.callback_query(F.data == "admin_give")
async def give_1(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.giving_stars_id)
    await call.message.edit_text("🆔 <b>ID юзера:</b>")

@dp.message(AdminStates.giving_stars_id)
async def give_2(message: Message, state: FSMContext):
    await state.update_data(tid=int(message.text))
    await state.set_state(AdminStates.giving_stars_amount)
    await message.answer("💰 <b>Сколько звезд?</b>")

@dp.message(AdminStates.giving_stars_amount)
async def give_3(message: Message, state: FSMContext):
    data = await state.get_data()
    db.add_stars(data['tid'], int(message.text))
    await state.clear()
    await message.answer("✅ Выдано!")

# ==========================================================
# 7. СЕРВЕР И ЗАПУСК
# ==========================================================
async def handle(request): return web.Response(text="OK")

async def main():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

