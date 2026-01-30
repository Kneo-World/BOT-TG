"""
StarsForQuestion - ULTIMATE MONOLITH v7.0
Абсолютно все функции: исправленные кнопки, рефералы (2 звезды), 
посты в канал (0.3 звезды), реалистичные фейки и кнопка связи.
"""

import asyncio
import logging
import os
import sqlite3
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
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1003326584722") 
ADMIN_IDS = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "8364667153").split(",") if id.strip()]
WITHDRAWAL_CHANNEL_ID = os.getenv("WITHDRAWAL_CHANNEL", "-1003891414947") 
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@Nft_top3")
PORT = int(os.environ.get("PORT", 10000))

# Экономика
REF_REWARD = 2.0  
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
            conn.execute("UPDATE users SET stars = stars + ? WHERE user_id = ?", (amount, user_id))
            conn.commit()

db = Database()

# ========== СОСТОЯНИЯ (FSM) ==========
class AdminStates(StatesGroup):
    waiting_fake_name = State()
    waiting_give_data = State()
    waiting_channel_post = State()

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
    builder.row(InlineKeyboardButton(text="🎮 Удача", callback_data="luck"),
                InlineKeyboardButton(text="📅 Бонус", callback_data="daily"))
    builder.row(InlineKeyboardButton(text="👥 Рефералы", callback_data="referrals"),
                InlineKeyboardButton(text="🏆 Топ", callback_data="top"))
    builder.row(InlineKeyboardButton(text="💎 Вывод", callback_data="withdraw"),
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
    await message.answer(f"🌟 Привет! Зарабатывай звезды и выводи их на свой счет.", reply_markup=get_main_kb(uid))

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
    await call.message.edit_text("🎯 <b>ЗАДАНИЯ</b>\n\n1. Реферал: 2.0 ⭐\n2. Группа: 1.0 ⭐\n3. Посты в канале: 0.3 ⭐", 
                               reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu")).as_markup())

@dp.callback_query(F.data == "top")
async def cb_top(call: CallbackQuery):
    text = "🏆 <b>ТОП-5 ЛИДЕРОВ</b>\n\n1. Kripto**** — 520 ⭐\n2. User01**** — 410 ⭐\n3. Admin**** — 350 ⭐\n4. Lucky**** — 210 ⭐\n5. Star**** — 190 ⭐"
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
    kb.row(InlineKeyboardButton(text="📢 Пост в КАНАЛ", callback_data="a_post_chan"))
    kb.row(InlineKeyboardButton(text="🎭 Фейк Заявка", callback_data="a_fake_gen"))
    kb.row(InlineKeyboardButton(text="💎 Выдать ⭐", callback_data="a_give_stars"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text("👑 <b>АДМИН-МЕНЮ</b>", reply_markup=kb.as_markup())

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

