"""
StarsForQuestion - FINAL HARD EDITION v5.0
Исправлена безопасность, добавлены кнопки к фейкам, новая админ-команда для постов.
"""

import asyncio
import logging
import os
import sys
import sqlite3
import random
from datetime import datetime, timedelta
from typing import Optional
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
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "nft0top")
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1003326584722")
# Список ID админов, которым разрешено одобрять заявки и заходить в админку
ADMIN_IDS = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "8364667153").split(",") if id.strip()]
WITHDRAWAL_CHANNEL_ID = os.getenv("WITHDRAWAL_CHANNEL", "-1003891414947")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@Nft_top3")
PORT = int(os.environ.get("PORT", 10000))

# ========== УСЛОЖНЕННАЯ ЭКОНОМИКА ==========
DAILY_MIN, DAILY_MAX = 1, 3  # Было 1-5, стало 1-3
LUCK_MIN, LUCK_MAX = 0, 5    # Было 0-10, стало 0-5
LUCK_COOLDOWN = 6 * 60 * 60  # Было 4 часа, стало 6 часов
REF_REWARD = 2               # Было 5, стало 2 (как ты и просил)
POST_VIEW_REWARD = 0.3         # Награда за просмотр поста
GROUP_REWARD = 1             # Награда за группу
WITHDRAWAL_OPTIONS = [15, 25, 50, 100]
FAKE_MIN_STARS, FAKE_MAX_STARS = 15, 60

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
            
            # Таблица для отслеживания просмотров постов, чтобы не забирали дважды
            conn.execute("""CREATE TABLE IF NOT EXISTS post_views (
                user_id INTEGER, post_id TEXT, PRIMARY KEY(user_id, post_id))""")
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

    def add_referral_count(self, referrer_id):
        with self.get_connection() as conn:
            conn.execute("UPDATE users SET referrals = referrals + 1 WHERE user_id = ?", (referrer_id,))
            conn.commit()

    def subtract_stars(self, user_id, amount):
        with self.get_connection() as conn:
            user = self.get_user(user_id)
            if user and user['stars'] >= amount:
                conn.execute("UPDATE users SET stars = stars - ?, total_withdrawn = total_withdrawn + ? WHERE user_id = ?", (amount, amount, user_id))
                conn.commit()
                return True
            return False

db = Database()

# ========== СОСТОЯНИЯ ==========
class AdminStates(StatesGroup):
    waiting_fake_name = State()
    waiting_fake_count = State()
    waiting_give_data = State()
    waiting_post_text = State()

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def mask_name(name):
    if not name: return "User****"
    name = name.replace("@", "")
    if len(name) <= 4: return name[:2] + "****"
    return name[:4] + "****"

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

def get_admin_decision_kb(wd_id, uid, amount):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Принять", callback_data=f"adm_app_{wd_id}_{uid}_{amount}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_rej_{wd_id}_{uid}_{amount}"))
    return builder.as_markup()

# ========== ОБРАБОТЧИКИ ЮЗЕРОВ ==========

@dp.message(CommandStart())
async def cmd_start(message: Message):
    uid = message.from_user.id
    if not db.get_user(uid):
        db.create_user(uid, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
        if " " in message.text:
            ref_code = message.text.split()[1]
            if ref_code.startswith("ref"):
                try:
                    ref_id = int(ref_code.replace("ref", ""))
                    if ref_id != uid:
                        db.add_stars(ref_id, REF_REWARD)
                        db.add_referral_count(ref_id)
                        try: await bot.send_message(ref_id, f"👥 Реферал! +{REF_REWARD} ⭐")
                        except: pass
                except: pass
    await message.answer(f"🌟 Привет! Зарабатывай звезды и выводи их на свой аккаунт.", reply_markup=get_main_kb(uid))

@dp.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery):
    await call.message.edit_text("⭐ <b>Главное меню</b>", reply_markup=get_main_kb(call.from_user.id))

@dp.callback_query(F.data == "profile")
async def cb_profile(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    text = (f"👤 <b>Профиль</b>\n\n🆔 ID: <code>{u['user_id']}</code>\n"
            f"⭐ Баланс: <b>{u['stars']:.2f} ⭐</b>\n👥 Рефералов: {u['referrals']}")
    await call.message.edit_text(text, reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu")).as_markup())

@dp.callback_query(F.data == "referrals")
async def cb_referrals(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    ref_link = f"https://t.me/{(await bot.get_me()).username}?start={u['ref_code']}"
    text = (f"👥 <b>Рефералы</b>\n\nНаграда: <b>{REF_REWARD} ⭐</b>\n\n🔗 Ссылка:\n<code>{ref_link}</code>")
    await call.message.edit_text(text, reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu")).as_markup())

@dp.callback_query(F.data == "daily")
async def cb_daily(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    now = datetime.now()
    if u['last_daily'] and (now - datetime.fromisoformat(u['last_daily'])).days < 1:
        return await call.answer("⏳ Завтра!", show_alert=True)
    reward = random.randint(DAILY_MIN, DAILY_MAX)
    db.add_stars(call.from_user.id, reward)
    with db.get_connection() as conn:
        conn.execute("UPDATE users SET last_daily = ? WHERE user_id = ?", (now.isoformat(), call.from_user.id))
        conn.commit()
    await call.answer(f"🎁 +{reward} ⭐", show_alert=True)
    await cb_menu(call)

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
    await call.answer(f"🎰 Выпало: {win} ⭐", show_alert=True)
    await cb_menu(call)

# ========== ВЫВОД И МОДЕРАЦИЯ ==========

@dp.callback_query(F.data == "withdraw")
async def cb_withdraw_list(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    if u['stars'] < 15: return await call.answer("❌ Нужно 15 ⭐", show_alert=True)
    kb = InlineKeyboardBuilder()
    for opt in WITHDRAWAL_OPTIONS:
        if u['stars'] >= opt:
            kb.row(InlineKeyboardButton(text=f"💎 {opt} звезд", callback_data=f"wd_proc_{opt}"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text("Выберите сумму:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("wd_proc_"))
async def cb_wd_send(call: CallbackQuery):
    amt = float(call.data.split("_")[2])
    uid = call.from_user.id
    if db.subtract_stars(uid, amt):
        with db.get_connection() as conn:
            cur = conn.execute("INSERT INTO withdrawals (user_id, amount, created_at) VALUES (?, ?, ?)", (uid, amt, datetime.now().isoformat()))
            wd_id = cur.lastrowid
            conn.commit()
        
        name = mask_name(call.from_user.username or call.from_user.first_name)
        await bot.send_message(WITHDRAWAL_CHANNEL_ID, 
                             f"📥 <b>ЗАЯВКА #{wd_id}</b>\n\n👤 Юзер: @{name}\n🆔 ID: <code>{uid}</code>\n💎 Сумма: <b>{amt} ⭐</b>",
                             reply_markup=get_admin_decision_kb(wd_id, uid, amt))
        await call.message.edit_text("✅ Заявка отправлена!", reply_markup=get_main_kb(uid))
    else: await call.answer("Ошибка!")

@dp.callback_query(F.data.startswith("adm_"))
async def cb_admin_decide(call: CallbackQuery):
    # ПРОВЕРКА: Только админ может нажимать кнопки
    if call.from_user.id not in ADMIN_IDS:
        return await call.answer("❌ Вы не администратор!", show_alert=True)
    
    _, action, wd_id, uid, amt = call.data.split("_")
    uid, amt = int(uid), float(amt)
    
    if action == "app":
        text = "✅ ОДОБРЕНО"
        try: await bot.send_message(uid, f"🎉 Заявка #{wd_id} на {amt} ⭐ одобрена!")
        except: pass
    else:
        text = "❌ ОТКЛОНЕНО"
        db.add_stars(uid, amt)
        try: await bot.send_message(uid, f"❌ Заявка #{wd_id} на {amt} ⭐ отклонена. Звезды возвращены.")
        except: pass

    await call.message.edit_text(call.message.text + f"\n\n<b>Итог: {text}</b>\nАдмин: {call.from_user.first_name}")

# ========== АДМИН-ПАНЕЛЬ ==========

@dp.callback_query(F.data == "admin_panel")
async def cb_admin_panel(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: return
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🎭 Фейк", callback_data="a_fake"),
           InlineKeyboardButton(text="📢 Рассылка поста", callback_data="a_post"))
    kb.row(InlineKeyboardButton(text="💎 Выдать ⭐", callback_data="a_give"),
           InlineKeyboardButton(text="🔙 Меню", callback_data="menu"))
    await call.message.edit_text("👑 <b>АДМИНКА</b>", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "a_fake")
async def adm_fake(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_fake_name)
    await call.message.answer("Введите ник для фейка:")

@dp.message(AdminStates.waiting_fake_name)
async def adm_fake_done(message: Message, state: FSMContext):
    name = mask_name(message.text)
    amt = random.randint(FAKE_MIN_STARS, FAKE_MAX_STARS)
    # Фейк заявка ТЕПЕРЬ С КНОПКАМИ
    await bot.send_message(WITHDRAWAL_CHANNEL_ID, 
                         f"📥 <b>ЗАЯВКА #FK_{random.randint(100,999)}</b>\n\n👤 Юзер: @{name}\n🆔 ID: <code>777000</code>\n💎 Сумма: <b>{amt} ⭐</b>",
                         reply_markup=get_admin_decision_kb(999, 8364667153, amt))
    await message.answer("✅ Фейк с кнопками в канале!")
    await state.clear()

@dp.callback_query(F.data == "a_post")
async def adm_post_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_post_text)
    await call.message.answer("Введите текст поста для получения звезд:")

@dp.message(AdminStates.waiting_post_text)
async def adm_post_done(message: Message, state: FSMContext):
    text = message.text
    post_id = f"p_{random.randint(1000,9999)}"
    
    # Рассылка всем пользователям
    with db.get_connection() as conn:
        users = conn.execute("SELECT user_id FROM users").fetchall()
    
    count = 0
    kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🎁 Забрать 0.3 ⭐", callback_data=f"get_p_{post_id}"))
    
    for row in users:
        try:
            await bot.send_message(row['user_id'], f"📢 <b>НОВОЕ ЗАДАНИЕ!</b>\n\n{text}", reply_markup=kb.as_markup())
            count += 1
            await asyncio.sleep(0.05)
        except: continue
        
    await message.answer(f"✅ Пост разослан {count} юзерам.")
    await state.clear()

@dp.callback_query(F.data.startswith("get_p_"))
async def cb_get_post_reward(call: CallbackQuery):
    post_id = call.data.replace("get_p_", "")
    uid = call.from_user.id
    try:
        with db.get_connection() as conn:
            conn.execute("INSERT INTO post_views (user_id, post_id) VALUES (?, ?)", (uid, post_id))
            conn.commit()
        db.add_stars(uid, POST_VIEW_REWARD)
        await call.answer(f"✅ Получено {POST_VIEW_REWARD} ⭐ за просмотр!", show_alert=True)
        await call.message.delete()
    except sqlite3.IntegrityError:
        await call.answer("❌ Вы уже забирали бонус!", show_alert=True)

@dp.callback_query(F.data == "tasks")
async def cb_tasks(call: CallbackQuery):
    text = (f"🎯 <b>ЗАДАНИЯ</b>\n\n1. Реферал: {REF_REWARD} ⭐\n2. Группа: {GROUP_REWARD} ⭐\n3. Ждите посты от админа!")
    await call.message.edit_text(text, reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu")).as_markup())

@dp.callback_query(F.data == "top")
async def cb_top(call: CallbackQuery):
    text = "🏆 <b>ТОП ЛИДЕРОВ</b>\n\n"
    for i in range(1, 6):
        text += f"{i}. User{random.randint(10,99)}**** — {random.randint(50, 150)} ⭐\n"
    await call.message.edit_text(text, reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu")).as_markup())

@dp.callback_query(F.data == "help")
async def cb_help(call: CallbackQuery):
    await call.message.edit_text(f"🆘 <b>ПОМОЩЬ</b>\n\nПоддержка: {SUPPORT_USERNAME}", reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu")).as_markup())

@dp.callback_query(F.data == "a_give")
async def adm_give(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_give_data)
    await call.message.answer("Введите ID и Сумму через пробел:")

@dp.message(AdminStates.waiting_give_data)
async def adm_give_done(message: Message, state: FSMContext):
    try:
        uid, amt = message.text.split()
        db.add_stars(int(uid), float(amt))
        await message.answer(f"✅ Выдано {amt} ⭐")
    except: await message.answer("Ошибка")
    await state.clear()

# ========== ЗАПУСК ==========
async def web_handle(request): return web.Response(text="Bot Alive")
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

