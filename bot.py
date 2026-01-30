"""
StarsForQuestion - MAXIMUM EDITION v4.5
Полный скрипт со всеми функциями: Рефералы, Задания, Удача, Фейки и Админка.
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

# ========== КОНФИГУРАЦИЯ (Берем из Render) ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
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

    def update_withdrawal_status(self, wd_id, status, msg_id=None):
        with self.get_connection() as conn:
            if msg_id:
                conn.execute("UPDATE withdrawals SET status = ?, message_id = ? WHERE id = ?", (status, msg_id, wd_id))
            else:
                conn.execute("UPDATE withdrawals SET status = ? WHERE id = ?", (status, wd_id))
            conn.commit()

db = Database()

# ========== СОСТОЯНИЯ ==========
class AdminStates(StatesGroup):
    waiting_broadcast = State()
    waiting_fake_name = State()
    waiting_fake_count = State()
    waiting_give_data = State()

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

# ========== ОБРАБОТЧИКИ ==========

@dp.message(CommandStart())
async def cmd_start(message: Message):
    uid = message.from_user.id
    user_exists = db.get_user(uid)
    
    if not user_exists:
        db.create_user(uid, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
        if " " in message.text:
            ref_code = message.text.split()[1]
            if ref_code.startswith("ref"):
                try:
                    ref_id = int(ref_code.replace("ref", ""))
                    if ref_id != uid:
                        db.add_stars(ref_id, REF_REWARD)
                        db.add_referral_count(ref_id)
                        try: await bot.send_message(ref_id, f"👥 По вашей ссылке пришел новый игрок! +{REF_REWARD} ⭐")
                        except: pass
                except: pass

    await message.answer(f"🌟 Привет, {message.from_user.first_name}! Здесь ты можешь заработать звезды Телеграм, просто выполняя задания и приглашая друзей.", reply_markup=get_main_kb(uid))

@dp.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery):
    await call.message.edit_text("⭐ <b>Главное меню</b>\n\nВыбери раздел ниже:", reply_markup=get_main_kb(call.from_user.id))

@dp.callback_query(F.data == "profile")
async def cb_profile(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    text = (f"👤 <b>Личный кабинет</b>\n\n"
            f"🆔 ID: <code>{u['user_id']}</code>\n"
            f"⭐ Баланс: <b>{u['stars']:.2f} звезд</b>\n"
            f"👥 Рефералов: {u['referrals']}\n"
            f"💰 Заработано всего: {u['total_earned']:.2f} ⭐")
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data == "referrals")
async def cb_referrals(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    bot_name = (await bot.get_me()).username
    ref_link = f"https://t.me/{bot_name}?start={u['ref_code']}"
    text = (f"👥 <b>Реферальная система</b>\n\n"
            f"За каждого приглашенного друга ты получаешь <b>{REF_REWARD} звезд!</b>\n\n"
            f"🔗 Твоя ссылка:\n<code>{ref_link}</code>\n\n"
            f"📊 Приглашено: {u['referrals']} чел.")
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📢 Поделиться", switch_inline_query=f"\nЗарабатывай звезды вместе со мной! {ref_link}"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data == "luck")
async def cb_luck(call: CallbackQuery):
    uid = call.from_user.id
    u = db.get_user(uid)
    now = datetime.now()
    
    if u['last_luck'] and (now - datetime.fromisoformat(u['last_luck'])).total_seconds() < LUCK_COOLDOWN:
        wait = LUCK_COOLDOWN - (now - datetime.fromisoformat(u['last_luck'])).total_seconds()
        return await call.answer(f"⏳ Удача будет доступна через {int(wait//60)} мин.", show_alert=True)
    
    win = random.randint(LUCK_MIN, LUCK_MAX)
    db.add_stars(uid, win)
    with db.get_connection() as conn:
        conn.execute("UPDATE users SET last_luck = ? WHERE user_id = ?", (now.isoformat(), uid))
        conn.commit()
    
    await call.message.answer(f"🎰 Ты испытал удачу и выиграл <b>{win} звезд!</b>")
    await cb_menu(call)

@dp.callback_query(F.data == "daily")
async def cb_daily(call: CallbackQuery):
    uid = call.from_user.id
    u = db.get_user(uid)
    now = datetime.now()
    if u['last_daily'] and (now - datetime.fromisoformat(u['last_daily'])).days < 1:
        return await call.answer("⏳ Бонус можно взять только завтра!", show_alert=True)
    
    reward = random.randint(DAILY_MIN, DAILY_MAX)
    db.add_stars(uid, reward)
    with db.get_connection() as conn:
        conn.execute("UPDATE users SET last_daily = ? WHERE user_id = ?", (now.isoformat(), uid))
        conn.commit()
    await call.answer(f"🎁 Получено {reward} ⭐!", show_alert=True)
    await cb_menu(call)

@dp.callback_query(F.data == "top")
async def cb_top(call: CallbackQuery):
    text = "🏆 <b>ТОП-10 ИГРОКОВ</b>\n\n"
    names = ["Alex", "Dmitry", "Mariya", "Sasha", "Ivan", "Elena", "Vovik", "Kirill", "Olya", "Gena"]
    for i, name in enumerate(names, 1):
        text += f"{i}. {name}**** — {random.randint(100, 500)} ⭐\n"
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data == "help")
async def cb_help(call: CallbackQuery):
    text = (f"ℹ️ <b>ПОМОЩЬ</b>\n\n"
            f"1. Как заработать? — Выполняй задания и зови друзей.\n"
            f"2. Как вывести? — Набери 15 звезд и жми кнопку Вывод.\n"
            f"3. Техподдержка: {SUPPORT_USERNAME}")
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data == "tasks")
async def cb_tasks(call: CallbackQuery):
    text = (f"🎯 <b>ЗАДАНИЯ</b>\n\n"
            f"1. Подписка на канал @{CHANNEL_USERNAME} (Обязательно)\n"
            f"2. Добавь бота в чат (+{GROUP_REWARD} ⭐)\n"
            f"3. Приглашай друзей (+{REF_REWARD} ⭐)")
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text(text, reply_markup=builder.as_markup())

# ========== ВЫВОД СРЕДСТВ ==========

@dp.callback_query(F.data == "withdraw")
async def cb_withdraw_init(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    if u['stars'] < 15:
        return await call.answer("❌ Минимум 15 звезд для вывода!", show_alert=True)
    
    kb = InlineKeyboardBuilder()
    for opt in WITHDRAWAL_OPTIONS:
        if u['stars'] >= opt:
            kb.row(InlineKeyboardButton(text=f"💎 Вывести {opt} ⭐", callback_data=f"wd_go_{opt}"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text("Выберите сумму для списания:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("wd_go_"))
async def cb_wd_final(call: CallbackQuery):
    amount = float(call.data.split("_")[2])
    uid = call.from_user.id
    
    if db.subtract_stars(uid, amount):
        with db.get_connection() as conn:
            cursor = conn.execute("INSERT INTO withdrawals (user_id, amount, created_at) VALUES (?, ?, ?)", 
                                (uid, amount, datetime.now().isoformat()))
            wd_id = cursor.lastrowid
            conn.commit()

        masked = mask_name(call.from_user.username or call.from_user.first_name)
        
        # Кнопки для админа в канале
        adm_kb = InlineKeyboardBuilder()
        adm_kb.row(InlineKeyboardButton(text="✅ Принять", callback_data=f"adm_app_{wd_id}_{uid}_{amount}"),
                   InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_rej_{wd_id}_{uid}_{amount}"))

        msg = await bot.send_message(
            WITHDRAWAL_CHANNEL_ID,
            f"📥 <b>ЗАЯВКА #{wd_id}</b>\n\n👤 Юзер: @{masked}\n🆔 ID: <code>{uid}</code>\n💎 Сумма: <b>{amount} ⭐</b>",
            reply_markup=adm_kb.as_markup()
        )
        db.update_withdrawal_status(wd_id, "processing", msg.message_id)
        await call.message.edit_text(f"✅ Заявка #{wd_id} на {amount} ⭐ отправлена на модерацию!", reply_markup=get_main_kb(uid))
    else:
        await call.answer("Недостаточно звезд!", show_alert=True)

# Обработка в канале
@dp.callback_query(F.data.startswith("adm_"))
async def handle_admin_action(call: CallbackQuery):
    _, action, wd_id, uid, amount = call.data.split("_")
    uid, amount = int(uid), float(amount)
    
    if action == "app":
        status = "✅ ОДОБРЕНО"
        db.update_withdrawal_status(wd_id, "completed")
        try: await bot.send_message(uid, f"🎉 Твоя заявка на {amount} ⭐ выполнена!")
        except: pass
    else:
        status = "❌ ОТКЛОНЕНО"
        db.update_withdrawal_status(wd_id, "rejected")
        db.add_stars(uid, amount)
        try: await bot.send_message(uid, f"❌ Твоя заявка на {amount} ⭐ отклонена. Звезды возвращены.")
        except: pass

    await call.message.edit_text(call.message.text + f"\n\n<b>Итог: {status}</b>\nАдмин: {call.from_user.first_name}")

# ========== АДМИНКА ==========

@dp.callback_query(F.data == "admin_panel")
async def cb_admin_panel(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: return
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🎭 Фейк (Свой)", callback_data="f_one"),
           InlineKeyboardButton(text="🎲 Фейк (Масс)", callback_data="f_mass"))
    kb.row(InlineKeyboardButton(text="💎 Выдать ⭐", callback_data="f_give"))
    kb.row(InlineKeyboardButton(text="🔙 В меню", callback_data="menu"))
    await call.message.edit_text("👑 <b>АДМИН-ПАНЕЛЬ</b>", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "f_one")
async def adm_fake_one(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_fake_name)
    await call.message.answer("Введите ник для фейковой заявки:")

@dp.message(AdminStates.waiting_fake_name)
async def adm_fake_one_done(message: Message, state: FSMContext):
    name = mask_name(message.text)
    amt = random.randint(FAKE_MIN_STARS, FAKE_MAX_STARS)
    await bot.send_message(WITHDRAWAL_CHANNEL_ID, f"📥 <b>НОВАЯ ЗАЯВКА</b>\n\n👤 Юзер: @{name}\n🆔 ID: <code>777{random.randint(11,99)}</code>\n💎 Сумма: <b>{amt} ⭐</b>")
    await message.answer("✅ Отправлено")
    await state.clear()

@dp.callback_query(F.data == "f_mass")
async def adm_fake_mass(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_fake_count)
    await call.message.answer("Сколько фейков заспамить?")

@dp.message(AdminStates.waiting_fake_count)
async def adm_fake_mass_done(message: Message, state: FSMContext):
    if not message.text.isdigit(): return
    for _ in range(int(message.text)):
        name = mask_name(random.choice(["Kripto", "Star", "User", "Rich"]) + str(random.randint(10,99)))
        amt = random.randint(FAKE_MIN_STARS, FAKE_MAX_STARS)
        await bot.send_message(WITHDRAWAL_CHANNEL_ID, f"📥 <b>НОВАЯ ЗАЯВКА</b>\n\n👤 Юзер: @{name}\n🆔 ID: <code>999{random.randint(11,99)}</code>\n💎 Сумма: <b>{amt} ⭐</b>")
        await asyncio.sleep(0.3)
    await message.answer("✅ Готово")
    await state.clear()

@dp.callback_query(F.data == "f_give")
async def adm_give_stars(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_give_data)
    await call.message.answer("Введи ID и Сумму через пробел (напр: 8364667153 50):")

@dp.message(AdminStates.waiting_give_data)
async def adm_give_stars_done(message: Message, state: FSMContext):
    try:
        uid, amt = message.text.split()
        db.add_stars(int(uid), float(amt))
        await message.answer(f"✅ Выдано {amt} ⭐ пользователю {uid}")
    except: await message.answer("Ошибка формата")
    await state.clear()

# ========== ЗАПУСК ==========
async def web_handle(request): return web.Response(text="Stars Bot Active")
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

