import asyncio
import logging
import os
import sys
import sqlite3
import random
from datetime import datetime, timedelta
from aiohttp import web

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "").split(",") if id.strip()]
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1002390231804")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "nft0top")
WITHDRAWAL_CHANNEL_ID = os.getenv("WITHDRAWAL_CHANNEL", "-1002390231804")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@Nft_top3")

# Экономика
REF_REWARD = 1.5
DAILY_MIN, DAILY_MAX = 1, 5
CLICK_REWARD = 0.03 

# ========== БАЗА ДАННЫХ ==========
class Database:
    def __init__(self):
        self.path = "bot_data.db"
        self.init_db()

    def get_conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        with self.get_conn() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
                stars REAL DEFAULT 0, referrals INTEGER DEFAULT 0,
                last_daily TEXT, created_at TEXT)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS post_clicks (
                user_id INTEGER, post_id INTEGER, PRIMARY KEY(user_id, post_id))""")
            conn.commit()

    def get_user(self, uid):
        with self.get_conn() as conn:
            return conn.execute("SELECT * FROM users WHERE user_id = ?", (uid,)).fetchone()

    def create_user(self, uid, uname, fname):
        with self.get_conn() as conn:
            conn.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, created_at) VALUES (?, ?, ?, ?)",
                        (uid, uname, fname, datetime.now().isoformat()))
            conn.commit()

    def add_stars(self, uid, amount):
        with self.get_conn() as conn:
            conn.execute("UPDATE users SET stars = stars + ? WHERE user_id = ?", (amount, uid))
            conn.commit()

db = Database()

# ========== СОСТОЯНИЯ ==========
class AdminStates(StatesGroup):
    waiting_broadcast = State()
    waiting_give_data = State() # Для выдачи звезд (ID и сумма)

# ========== КЛАВИАТУРЫ ==========
def main_menu(uid):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
                InlineKeyboardButton(text="📅 Бонус", callback_data="daily"))
    builder.row(InlineKeyboardButton(text="👥 Рефералы", callback_data="referrals"),
                InlineKeyboardButton(text="🏆 Топ", callback_data="top"))
    builder.row(InlineKeyboardButton(text="💎 Вывод", callback_data="withdraw"))
    if uid in ADMIN_IDS:
        builder.row(InlineKeyboardButton(text="👑 Админка", callback_data="admin_panel"))
    return builder.as_markup()

# ========== ОБРАБОТЧИКИ ==========
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

@dp.message(CommandStart())
async def cmd_start(message: Message):
    uid = message.from_user.id
    is_new = db.get_user(uid) is None
    db.create_user(uid, message.from_user.username, message.from_user.first_name)
    
    args = message.text.split()
    if is_new and len(args) > 1 and args[1].isdigit():
        ref_id = int(args[1])
        if ref_id != uid:
            db.add_stars(ref_id, REF_REWARD)
            with db.get_conn() as conn:
                conn.execute("UPDATE users SET referrals = referrals + 1 WHERE user_id = ?", (ref_id,))
            try: await bot.send_message(ref_id, f"💎 У вас новый реферал! +{REF_REWARD} ⭐")
            except: pass

    await message.answer(f"🌟 Привет, {message.from_user.first_name}!\nЗарабатывай звезды и выводи их на баланс.", 
                         reply_markup=main_menu(uid))

@dp.callback_query(F.data == "profile")
async def cb_profile(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    text = (f"👤 <b>Ваш профиль:</b>\n\n"
            f"🆔 ID: <code>{u['user_id']}</code>\n"
            f"⭐ Баланс: <b>{u['stars']:.2f} звезд</b>\n"
            f"👥 Рефералов: <b>{u['referrals']}</b>")
    await call.message.edit_text(text, reply_markup=main_menu(call.from_user.id))

@dp.callback_query(F.data == "referrals")
async def cb_referrals(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={u['user_id']}"
    text = (f"👥 <b>Реферальная система</b>\n\n"
            f"Приглашай друзей и получай <b>{REF_REWARD} ⭐</b> за каждого!\n\n"
            f"🔗 Твоя ссылка:\n<code>{ref_link}</code>\n\n"
            f"Приглашено: <b>{u['referrals']}</b>")
    await call.message.edit_text(text, reply_markup=main_menu(call.from_user.id))

@dp.callback_query(F.data == "top")
async def cb_top(call: CallbackQuery):
    with db.get_conn() as conn:
        top_users = conn.execute("SELECT first_name, stars FROM users ORDER BY stars DESC LIMIT 10").fetchall()
    
    text = "🏆 <b>Топ 10 богатых игроков:</b>\n\n"
    for i, user in enumerate(top_users, 1):
        text += f"{i}. {user['first_name']} — <b>{user['stars']:.2f} ⭐</b>\n"
    
    await call.message.edit_text(text, reply_markup=main_menu(call.from_user.id))

@dp.callback_query(F.data == "daily")
async def cb_daily(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    now = datetime.now()
    if u['last_daily'] and datetime.fromisoformat(u['last_daily']) + timedelta(days=1) > now:
        return await call.answer("❌ Бонус доступен раз в 24 часа!", show_alert=True)
    
    reward = random.randint(DAILY_MIN, DAILY_MAX)
    db.add_stars(u['user_id'], reward)
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET last_daily = ? WHERE user_id = ?", (now.isoformat(), u['user_id']))
    
    await call.answer(f"🎉 Вы получили {reward} ⭐!", show_alert=True)
    await cb_profile(call)

@dp.callback_query(F.data.startswith("claim_"))
async def cb_claim_post(call: CallbackQuery):
    post_id = int(call.data.split("_")[1])
    uid = call.from_user.id
    with db.get_conn() as conn:
        check = conn.execute("SELECT 1 FROM post_clicks WHERE user_id = ? AND post_id = ?", (uid, post_id)).fetchone()
        if check: return await call.answer("❌ Ты уже забирал награду!", show_alert=True)
        conn.execute("INSERT INTO post_clicks VALUES (?, ?)", (uid, post_id))
        conn.commit()
    db.add_stars(uid, CLICK_REWARD)
    await call.answer(f"✅ Начислено {CLICK_REWARD} ⭐!", show_alert=True)

@dp.callback_query(F.data == "withdraw")
async def cb_withdraw(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    if u['stars'] < 15: 
        return await call.answer("❌ Минимум 15 звезд!", show_alert=True)
    
    amount = u['stars']
    try:
        # Пытаемся отправить сообщение ДО обнуления баланса (для безопасности)
        await bot.send_message(
            WITHDRAWAL_CHANNEL_ID, 
            f"💰 <b>ЗАЯВКА НА ВЫВОД</b>\n\n"
            f"👤 Юзер: {call.from_user.full_name}\n"
            f"🆔 ID: <code>{u['user_id']}</code>\n"
            f"💎 Сумма: <b>{amount:.2f} ⭐</b>"
        )
        # Если отправилось — обнуляем баланс в базе
        with db.get_conn() as conn:
            conn.execute("UPDATE users SET stars = 0 WHERE user_id = ?", (u['user_id'],))
        await call.message.answer("✅ Заявка успешно отправлена в канал!")
    except Exception as e:
        logging.error(f"Ошибка вывода: {e}") # Это покажет ошибку в логах Render
        await call.answer(f"⚠ Ошибка: Бот не админ в канале или ID неверный!", show_alert=True)


# --- АДМИНКА ---
@dp.callback_query(F.data == "admin_panel")
async def cb_admin(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: return
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_mail"),
           InlineKeyboardButton(text="💎 Выдать звезды", callback_data="admin_give"))
    kb.row(InlineKeyboardButton(text="📮 Пост в канал", callback_data="admin_post_chan"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="profile"))
    await call.message.edit_text("👑 <b>Админ-панель</b>", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "admin_give")
async def cb_admin_give(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_give_data)
    await call.message.answer("Введите ID юзера и количество звезд через пробел\nПример: <code>1234567 100</code>")

@dp.message(AdminStates.waiting_give_data)
async def process_admin_give(message: Message, state: FSMContext):
    try:
        uid, amount = message.text.split()
        db.add_stars(int(uid), float(amount))
        await message.answer(f"✅ Успешно выдано {amount} ⭐ юзеру {uid}")
    except: await message.answer("❌ Ошибка. Вводите только цифры через пробел.")
    await state.clear()

@dp.callback_query(F.data == "admin_post_chan")
async def cb_admin_post_chan(call: CallbackQuery):
    pid = random.randint(100, 999)
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="💰 Забрать 0.03 ⭐", callback_data=f"claim_{pid}"))
    await bot.send_message(CHANNEL_ID, "📢 <b>Новый пост!</b>\nНажми кнопку ниже для бонуса.", reply_markup=kb.as_markup())
    await call.answer("Отправлено!")

@dp.callback_query(F.data == "admin_mail")
async def cb_admin_mail(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_broadcast)
    await call.message.answer("Введите текст рассылки:")

@dp.message(AdminStates.waiting_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    with db.get_conn() as conn:
        users = conn.execute("SELECT user_id FROM users").fetchall()
    for row in users:
        try: 
            await bot.send_message(row[0], message.text)
            await asyncio.sleep(0.05)
        except: pass
    await message.answer("✅ Готово!")
    await state.clear()

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

