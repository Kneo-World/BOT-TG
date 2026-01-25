import asyncio
import logging
import os
import random
from datetime import datetime, timedelta

import aiosqlite
import uvicorn
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardButton, Message, ChatMemberUpdated
from aiogram.utils.keyboard import InlineKeyboardBuilder
from fastapi import FastAPI

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = "@nft0top"  # Замените на юзернейм вашего канала
CHANNEL_URL = "https://t.me/nft0top"
PORT = int(os.getenv("PORT", 8000))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота и сервера
bot = Bot(token=TOKEN)
dp = Dispatcher()
app = FastAPI()
DB_PATH = "db.sqlite3"

# --- ВЕБ-СЕРВЕР (Keep-alive) ---
@app.get("/")
async def root():
    return {"status": "alive", "info": "StarBot is running"}

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, 
            username TEXT, 
            stars INTEGER DEFAULT 0,
            referrer_id INTEGER, 
            last_daily TEXT, 
            last_luck TEXT
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS groups (
            chat_id INTEGER PRIMARY KEY, 
            added_by INTEGER
        )''')
        await db.commit()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
async def is_subscribed(user_id: int):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logger.error(f"Error checking sub: {e}")
        return False

def get_main_kb():
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🎁 Ежедневный бонус", callback_data="daily"),
           InlineKeyboardButton(text="🎰 Удача", callback_data="luck"))
    kb.row(InlineKeyboardButton(text="👥 Рефералы", callback_data="refs"),
           InlineKeyboardButton(text="📊 Статистика", callback_data="stats"))
    kb.row(InlineKeyboardButton(text="🏆 ТОП", callback_data="top"))
    return kb.as_markup()

# --- ОБРАБОТЧИКИ (HANDLERS) ---

@dp.message(CommandStart())
async def start_cmd(message: Message):
    if message.chat.type != "private":
        return

    user_id = message.from_user.id
    username = message.from_user.username or "User"
    
    # Реферальная логика
    args = message.text.split()
    ref_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user_exists = await cursor.fetchone()
            
            if not user_exists:
                # Если реферал сам по себе не существует, создаем
                if ref_id and ref_id != user_id:
                    await db.execute("INSERT INTO users (user_id, username, referrer_id) VALUES (?, ?, ?)",
                                     (user_id, username, ref_id))
                    await db.execute("UPDATE users SET stars = stars + 5 WHERE user_id = ?", (ref_id,))
                    try:
                        await bot.send_message(ref_id, f"🎁 У вас новый реферал @{username}! +5 Звезд 🌟")
                    except: pass
                else:
                    await db.execute("INSERT INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
                await db.commit()

    # Проверка подписки
    if not await is_subscribed(user_id):
        kb = InlineKeyboardBuilder()
        kb.add(InlineKeyboardButton(text="📢 Подписаться на канал", url=CHANNEL_URL))
        kb.add(InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub"))
        return await message.answer(f"🚀 Чтобы начать зарабатывать, подпишитесь на {CHANNEL_ID}", reply_markup=kb.as_markup())

    await message.answer("🌟 **Добро пожаловать в StarEarn!**\n\nЗдесь ты можешь собирать Звезды и приглашать друзей.", reply_markup=get_main_kb(), parse_mode="Markdown")

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(call: types.CallbackQuery):
    if await is_subscribed(call.from_user.id):
        await call.answer("✅ Подписка подтверждена!")
        await call.message.edit_text("🌟 Добро пожаловать! Выберите действие:", reply_markup=get_main_kb())
    else:
        await call.answer("❌ Вы все еще не подписаны!", show_alert=True)

@dp.callback_query(F.data == "daily")
async def daily_bonus(call: types.CallbackQuery):
    now = datetime.now().date().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT last_daily FROM users WHERE user_id = ?", (call.from_user.id,)) as cursor:
            res = await cursor.fetchone()
            if res and res[0] == now:
                return await call.answer("❌ Бонус уже получен. Ждите завтра!", show_alert=True)
            
            reward = random.randint(1, 5)
            await db.execute("UPDATE users SET stars = stars + ?, last_daily = ? WHERE user_id = ?", 
                             (reward, now, call.from_user.id))
            await db.commit()
            await call.message.answer(f"🎁 Поздравляем! Вы получили {reward} 🌟 за ежедневный вход!")

@dp.callback_query(F.data == "luck")
async def luck_game(call: types.CallbackQuery):
    user_id = call.from_user.id
    now = datetime.now()
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT last_luck FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                last_time = datetime.fromisoformat(row[0])
                if now < last_time + timedelta(hours=4):
                    diff = (last_time + timedelta(hours=4) - now)
                    return await call.answer(f"⏳ Кран перезаряжается. Ждите {diff.seconds // 60} мин.", show_alert=True)
            
            win = random.choices([0, 1, 3, 10], weights=[50, 30, 15, 5])[0]
            await db.execute("UPDATE users SET stars = stars + ?, last_luck = ? WHERE user_id = ?", 
                             (win, now.isoformat(), user_id))
            await db.commit()
            msg = f"🎰 Удача: +{win} 🌟!" if win > 0 else "🎰 Удача не на вашей стороне. 0 🌟"
            await call.message.answer(msg)

@dp.callback_query(F.data == "stats")
async def stats(call: types.CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT stars, (SELECT COUNT(*) FROM users WHERE referrer_id = ?) FROM users WHERE user_id = ?", 
                             (call.from_user.id, call.from_user.id)) as cursor:
            res = await cursor.fetchone()
            bot_info = await bot.get_me()
            text = (f"👤 **Ваш профиль:**\n\n"
                    f"💰 Баланс: {res[0]} Звезд\n"
                    f"👥 Приглашено: {res[1]} чел.\n\n"
                    f"🔗 Реф. ссылка:\n`https://t.me/{bot_info.username}?start={call.from_user.id}`")
            await call.message.answer(text, parse_mode="Markdown")

@dp.callback_query(F.data == "top")
async def top_players(call: types.CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT username, stars FROM users ORDER BY stars DESC LIMIT 10") as cursor:
            rows = await cursor.fetchall()
            text = "🏆 **ТОП-10 МАГНАТОВ:**\n\n"
            for i, row in enumerate(rows, 1):
                name = f"@{row[0]}" if row[0] else f"ID{i*123}"
                text += f"{i}. {name} — {row[1]} 🌟\n"
            await call.message.answer(text, parse_mode="Markdown")

# --- ГРУППОВОЙ КВЕСТ ---
@dp.my_chat_member()
async def on_added_to_group(event: ChatMemberUpdated):
    if event.new_chat_member.status in ["member", "administrator"]:
        try:
            count = await bot.get_chat_member_count(event.chat.id)
            if count >= 10:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("INSERT OR IGNORE INTO groups (chat_id, added_by) VALUES (?, ?)", 
                                     (event.chat.id, event.from_user.id))
                    await db.execute("UPDATE users SET stars = stars + 2 WHERE user_id = ?", (event.from_user.id,))
                    await db.commit()
                    await bot.send_message(event.chat.id, "✅ Групповой квест выполнен! Тот, кто добавил бота, получил +2 🌟")
        except Exception as e:
            logger.error(f"Group error: {e}")

# --- ЗАПУСК ---
async def main():
    await init_db()
    
    # Конфигурация FastAPI сервера для работы внутри цикла asyncio
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, loop="asyncio")
    server = uvicorn.Server(config)

    logger.info("Starting bot and server...")
    
    # Одновременный запуск Polling и Web-сервера
    await asyncio.gather(
        dp.start_polling(bot),
        server.serve()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")
