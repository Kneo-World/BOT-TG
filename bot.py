import asyncio
import logging
import os
import sqlite3
import random
from datetime import datetime
from aiohttp import web

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
WITHDRAWAL_CHAT = os.getenv("WITHDRAWAL_CHANNEL", "-1003891414947")
ADMIN_IDS = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "8364667153").split(",") if id.strip()]

# Настройки фейков (можно менять цифры тут)
FAKE_MIN_STARS = 15
FAKE_MAX_STARS = 60

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
                created_at TEXT)""")
            conn.commit()

    def get_user(self, uid):
        with self.get_conn() as conn:
            return conn.execute("SELECT * FROM users WHERE user_id = ?", (uid,)).fetchone()

    def add_stars(self, uid, amount):
        with self.get_conn() as conn:
            conn.execute("UPDATE users SET stars = stars + ? WHERE user_id = ?", (amount, uid))
            conn.commit()

db = Database()
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# ========== СОСТОЯНИЯ ==========
class AdminStates(StatesGroup):
    waiting_give_data = State()
    waiting_fake_name = State()
    waiting_fake_count = State()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def mask_name(name):
    if not name: return "User****"
    name = name.replace("@", "")
    if len(name) <= 3: return name + "***"
    return name[:3] + "***" + name[-1:]

def get_admin_withdraw_kb(user_id, amount):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="✅ Принять", callback_data=f"adm_wd_app_{user_id}_{amount}"),
           InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_wd_rej_{user_id}_{amount}"))
    return kb.as_markup()

# ========== ОБРАБОТЧИКИ ==========

@dp.message(CommandStart())
async def cmd_start(message: Message):
    uid = message.from_user.id
    if not db.get_user(uid):
        with db.get_conn() as conn:
            conn.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, created_at) VALUES (?, ?, ?, ?)",
                        (uid, message.from_user.username, message.from_user.first_name, datetime.now().isoformat()))
            conn.commit()
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
           InlineKeyboardButton(text="💎 Вывод", callback_data="withdraw"))
    if uid in ADMIN_IDS:
        kb.row(InlineKeyboardButton(text="👑 Админка", callback_data="admin_panel"))
    
    await message.answer(f"🌟 Привет, {message.from_user.first_name}!", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "withdraw")
async def cb_withdraw(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    if u['stars'] < 15:
        return await call.answer("❌ Минимум 15 звезд!", show_alert=True)
    
    amount = round(u['stars'], 2)
    masked = mask_name(call.from_user.username or call.from_user.first_name)
    
    try:
        await bot.send_message(
            chat_id=WITHDRAWAL_CHAT,
            text=f"💰 <b>НОВАЯ ЗАЯВКА</b>\n\n👤 Юзер: @{masked}\n🆔 ID: <code>{u['user_id']}</code>\n💎 Сумма: <b>{amount} ⭐</b>",
            reply_markup=get_admin_withdraw_kb(u['user_id'], amount)
        )
        with db.get_conn() as conn:
            conn.execute("UPDATE users SET stars = 0 WHERE user_id = ?", (u['user_id'],))
            conn.commit()
        await call.message.answer("✅ Заявка отправлена!")
    except Exception as e:
        await call.answer(f"⚠ Ошибка: {e}", show_alert=True)

@dp.callback_query(F.data.startswith("adm_wd_"))
async def handle_admin_decision(call: CallbackQuery):
    _, _, action, uid, amount = call.data.split("_")
    if action == "app":
        status = "✅ ОДОБРЕНО"
        try: await bot.send_message(uid, f"🎉 Заявка на {amount} ⭐ одобрена!")
        except: pass
    else:
        status = "❌ ОТКЛОНЕНО"
        db.add_stars(int(uid), float(amount))
        try: await bot.send_message(uid, f"❌ Заявка на {amount} ⭐ отклонена. Баланс возвращен.")
        except: pass
    await call.message.edit_text(call.message.text + f"\n\n<b>Статус: {status}</b>")

# --- НОВАЯ АДМИНКА ---
@dp.callback_query(F.data == "admin_panel")
async def cb_admin(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS: return
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🎭 Фейк (Свой ник)", callback_data="admin_fake_custom"))
    kb.row(InlineKeyboardButton(text="🎲 Фейк (Рандом x5)", callback_data="admin_fake_multi"))
    kb.row(InlineKeyboardButton(text="💎 Выдать звезды", callback_data="admin_give"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="profile"))
    await call.message.edit_text("👑 <b>Панель управления</b>", reply_markup=kb.as_markup())

# Логика своего ника для фейка
@dp.callback_query(F.data == "admin_fake_custom")
async def fake_custom_step1(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_fake_name)
    await call.message.answer("Введите ник для фейка (например: MySuperUser):")

@dp.message(AdminStates.waiting_fake_name)
async def fake_custom_step2(message: Message, state: FSMContext):
    name = mask_name(message.text)
    amount = random.randint(FAKE_MIN_STARS, FAKE_MAX_STARS)
    await bot.send_message(WITHDRAWAL_CHAT, 
        f"💰 <b>НОВАЯ ЗАЯВКА</b>\n\n👤 Юзер: @{name}\n🆔 ID: <code>777{random.randint(1000,9999)}</code>\n💎 Сумма: <b>{amount} ⭐</b>")
    await message.answer(f"✅ Фейк для {name} отправлен!")
    await state.clear()

# Логика массовых фейков
@dp.callback_query(F.data == "admin_fake_multi")
async def fake_multi_step1(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_fake_count)
    await call.message.answer("Сколько фейковых заявок отправить за раз?")

@dp.message(AdminStates.waiting_fake_count)
async def fake_multi_step2(message: Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("Введите число!")
    
    count = int(message.text)
    names = ["Rich", "King", "Star", "Lucky", "Crypto", "Owner", "Best", "TopG"]
    
    for _ in range(count):
        fake_name = mask_name(random.choice(names) + str(random.randint(100, 999)))
        amount = random.randint(FAKE_MIN_STARS, FAKE_MAX_STARS)
        await bot.send_message(WITHDRAWAL_CHAT, 
            f"💰 <b>НОВАЯ ЗАЯВКА</b>\n\n👤 Юзер: @{fake_name}\n🆔 ID: <code>777{random.randint(1000,9999)}</code>\n💎 Сумма: <b>{amount} ⭐</b>")
        await asyncio.sleep(0.5) # Пауза чтобы ТГ не забанил
        
    await message.answer(f"✅ Успешно отправлено {count} фейков!")
    await state.clear()

@dp.callback_query(F.data == "admin_give")
async def cb_admin_give(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_give_data)
    await call.message.answer("Введите ID и сумму через пробел:")

@dp.message(AdminStates.waiting_give_data)
async def process_give(message: Message, state: FSMContext):
    try:
        uid, amt = message.text.split()
        db.add_stars(int(uid), float(amt))
        await message.answer("✅ Выдано!")
    except: await message.answer("❌ Ошибка!")
    await state.clear()

async def handle(request): return web.Response(text="Live")
async def main():
    app = web.Application(); app.router.add_get("/", handle)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

