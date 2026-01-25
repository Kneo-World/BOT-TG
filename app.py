import asyncio
import logging
import os
import random
import uuid
from datetime import datetime, timedelta

import aiosqlite
import uvicorn
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command, BaseFilter
from aiogram.types import InlineKeyboardButton, Message, ChatMemberUpdated, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
from fastapi import FastAPI

# --- CONFIGURATION ---
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = "@nft0top" 
CHANNEL_URL = "https://t.me/nft0top"
ADMIN_ID = 12345678  # ЗАМЕНИТЕ НА ВАШ ID (цифрами)
PORT = int(os.getenv("PORT", 8000))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = FastAPI()
DB_PATH = "star_earn.db"

# --- DATABASE LAYER ---
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Таблица пользователей
        await db.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, 
            username TEXT, 
            stars REAL DEFAULT 0,
            referrer_id INTEGER, 
            last_daily TEXT, 
            last_luck TEXT,
            click_power REAL DEFAULT 0.1,
            auto_income REAL DEFAULT 0,
            total_clicks INTEGER DEFAULT 0,
            reg_date TEXT
        )''')
        # Таблица групп
        await db.execute('''CREATE TABLE IF NOT EXISTS groups (
            chat_id INTEGER PRIMARY KEY, 
            added_by INTEGER
        )''')
        # Таблица промокодов
        await db.execute('''CREATE TABLE IF NOT EXISTS promo (
            code TEXT PRIMARY KEY, 
            reward REAL, 
            uses INTEGER DEFAULT 1
        )''')
        await db.commit()

# --- FILTERS & UTILS ---
class IsAdmin(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.from_user.id == ADMIN_ID

async def is_subscribed(user_id: int):
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception:
        return False

# --- KEYBOARDS ---
def get_main_kb():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💎 МАЙНИНГ", callback_data="mine"),
                InlineKeyboardButton(text="🚀 УЛУЧШЕНИЯ", callback_data="upgrades"))
    builder.row(InlineKeyboardButton(text="🎰 УДАЧА", callback_data="luck"),
                InlineKeyboardButton(text="🎁 БОНУС", callback_data="daily"))
    builder.row(InlineKeyboardButton(text="👥 РЕФЕРАЛЫ", callback_data="refs"),
                InlineKeyboardButton(text="🎫 ПРОМО", callback_data="promo_menu"))
    builder.row(InlineKeyboardButton(text="📊 ПРОФИЛЬ", callback_data="stats"),
                InlineKeyboardButton(text="🏆 ТОП", callback_data="top"))
    builder.row(InlineKeyboardButton(text="⚙️ ПОДДЕРЖКА", url="https://t.me/your_admin_tag"))
    return builder.as_markup()

# --- HANDLERS: START & REGISTRATION ---
@dp.message(CommandStart())
async def start_handler(message: Message):
    if message.chat.type != "private": return
    
    uid = message.from_user.id
    uname = message.from_user.username or "NoName"
    args = message.text.split()
    ref_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (uid,)) as c:
            if not await c.fetchone():
                now = datetime.now().strftime("%Y-%m-%d")
                await db.execute(
                    "INSERT INTO users (user_id, username, referrer_id, reg_date) VALUES (?, ?, ?, ?)",
                    (uid, uname, ref_id, now)
                )
                if ref_id and ref_id != uid:
                    await db.execute("UPDATE users SET stars = stars + 5 WHERE user_id = ?", (ref_id,))
                    try:
                        await bot.send_message(ref_id, f"🎊 **+5.0 🌟 за нового реферала!** (@{uname})")
                    except: pass
                await db.commit()

    if not await is_subscribed(uid):
        kb = InlineKeyboardBuilder()
        kb.add(InlineKeyboardButton(text="📢 Подписаться на канал", url=CHANNEL_URL))
        kb.add(InlineKeyboardButton(text="🔄 Проверить подписку", callback_data="check_sub"))
        return await message.answer(
            f"👋 **Привет, {message.from_user.first_name}!**\n\n"
            f"Для работы с ботом необходимо подписаться на наш канал: {CHANNEL_ID}",
            reply_markup=kb.as_markup()
        )

    await message.answer(
        f"🌟 **ДОБРО ПОЖАЛОВАТЬ В STAREARN v2.0**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Здесь ты можешь зарабатывать Звезды, соревноваться с друзьями и становиться богаче!\n\n"
        f"💰 Твой баланс обновляется в реальном времени.\n"
        f"⛏ Начинай кликать или покупай улучшения!",
        reply_markup=get_main_kb(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "check_sub")
async def check_sub_btn(call: CallbackQuery):
    if await is_subscribed(call.from_user.id):
        await call.message.edit_text("✅ **Подписка подтверждена!** Вы получили доступ к функциям.", reply_markup=get_main_kb())
    else:
        await call.answer("❌ Вы не подписаны!", show_alert=True)

# --- HANDLERS: ECONOMY ---
@dp.callback_query(F.data == "mine")
async def mining_process(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT click_power FROM users WHERE user_id = ?", (call.from_user.id,)) as c:
            power = (await c.fetchone())[0]
            await db.execute("UPDATE users SET stars = stars + ?, total_clicks = total_clicks + 1 WHERE user_id = ?", 
                             (power, call.from_user.id))
            await db.commit()
    await call.answer(f"⛏ Клик! +{power} 🌟", show_alert=False)

@dp.callback_query(F.data == "daily")
async def daily_bonus(call: CallbackQuery):
    now = datetime.now().date().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT last_daily FROM users WHERE user_id = ?", (call.from_user.id,)) as c:
            last = await c.fetchone()
            if last and last[0] == now:
                return await call.answer("⏳ Бонус будет доступен завтра!", show_alert=True)
            
            reward = random.randint(1, 10)
            await db.execute("UPDATE users SET stars = stars + ?, last_daily = ? WHERE user_id = ?", 
                             (reward, now, call.from_user.id))
            await db.commit()
            await call.message.answer(f"🎁 **Ежедневная награда:** `{reward}` 🌟\nПриходи завтра!")

@dp.callback_query(F.data == "upgrades")
async def upgrades_menu(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT click_power FROM users WHERE user_id = ?", (call.from_user.id,)) as c:
            power = (await c.fetchone())[0]
    
    price = round(power * 150, 2)
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=f"🔥 Улучшить клик ({price} 🌟)", callback_data=f"buy_click_{price}"))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="to_main"))
    
    await call.message.edit_text(
        f"🚀 **МАГАЗИН УЛУЧШЕНИЙ**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Текущая мощность: `{round(power, 2)}` 🌟 за клик\n\n"
        f"Улучшение увеличит твою прибыль на +0.1 за каждый клик!",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("buy_click_"))
async def buy_click(call: CallbackQuery):
    price = float(call.data.split("_")[2])
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT stars FROM users WHERE user_id = ?", (call.from_user.id,)) as c:
            balance = (await c.fetchone())[0]
            if balance < price:
                return await call.answer("❌ Недостаточно Звезд!", show_alert=True)
            
            await db.execute("UPDATE users SET stars = stars - ?, click_power = click_power + 0.1 WHERE user_id = ?", 
                             (price, call.from_user.id))
            await db.commit()
            await call.answer("✅ Мощность клика увеличена!", show_alert=True)
            await upgrades_menu(call)

# --- HANDLERS: SOCIAL & STATS ---
@dp.callback_query(F.data == "refs")
async def refs_handler(call: CallbackQuery):
    bot_user = await bot.get_me()
    link = f"https://t.me/{bot_user.username}?start={call.from_user.id}"
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users WHERE referrer_id = ?", (call.from_user.id,)) as c:
            count = (await c.fetchone())[0]
    
    text = (
        f"👥 **РЕФЕРАЛЬНАЯ СИСТЕМА**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Приглашай друзей и получай **5.0 🌟** за каждого!\n\n"
        f"📈 Твои приглашения: `{count}` чел.\n"
        f"🔗 Ссылка для друга:\n`{link}`"
    )
    await call.message.answer(text, parse_mode="Markdown")

@dp.callback_query(F.data == "stats")
async def stats_handler(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT stars, click_power, total_clicks, reg_date FROM users WHERE user_id = ?", (call.from_user.id,)) as c:
            res = await c.fetchone()
            text = (
                f"👤 **ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 Баланс: `{round(res[0], 2)}` 🌟\n"
                f"⚡️ Мощность: `{round(res[1], 2)}` 🌟/клик\n"
                f"🖱 Всего кликов: `{res[2]}`\n"
                f"📅 В системе с: `{res[3]}`\n"
                f"🆔 Твой ID: `{call.from_user.id}`"
            )
            await call.message.answer(text, parse_mode="Markdown")

@dp.callback_query(F.data == "top")
async def top_handler(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT username, stars FROM users ORDER BY stars DESC LIMIT 10") as c:
            rows = await c.fetchall()
            text = "🏆 **ГЛОБАЛЬНЫЙ РЕЙТИНГ**\n━━━━━━━━━━━━━━━━━━━━\n"
            for i, row in enumerate(rows, 1):
                name = row[0] if row[0] else f"ID{random.randint(100,999)}"
                text += f"{i}. `@{name}` — {round(row[1], 1)} 🌟\n"
            await call.message.answer(text, parse_mode="Markdown")

# --- SYSTEM: SEND STARS ---
@dp.message(Command("send"))
async def send_stars(message: Message):
    args = message.text.split()
    if len(args) < 3:
        return await message.answer("❌ Используй: `/send [ID] [Сумма]`")
    
    to_id, amount = int(args[1]), float(args[2])
    if amount <= 0: return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT stars FROM users WHERE user_id = ?", (message.from_user.id,)) as c:
            balance = (await c.fetchone())[0]
            if balance < amount:
                return await message.answer("❌ Недостаточно средств!")
            
            await db.execute("UPDATE users SET stars = stars - ? WHERE user_id = ?", (amount, message.from_user.id))
            await db.execute("UPDATE users SET stars = stars + ? WHERE user_id = ?", (amount, to_id))
            await db.commit()
            await message.answer(f"✅ Перевод `{amount}` 🌟 пользователю `{to_id}` выполнен!")
            try:
                await bot.send_message(to_id, f"💰 Вы получили `{amount}` 🌟 от пользователя `{message.from_user.id}`!")
            except: pass

# --- ADMIN PANEL ---
@dp.message(Command("admin"), IsAdmin())
async def admin_panel(message: Message):
    await message.answer(
        "🛠 **АДМИН-ПАНЕЛЬ**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📦 `/addpromo [код] [награда]` - создать промо\n"
        "📢 `/broadcast [текст]` - рассылка\n"
        "💎 `/give [ID] [сумма]` - выдать звезды"
    )

@dp.message(Command("addpromo"), IsAdmin())
async def add_promo(message: Message):
    args = message.text.split()
    code, reward = args[1], float(args[2])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO promo (code, reward) VALUES (?, ?)", (code, reward))
        await db.commit()
    await message.answer(f"✅ Промокод `{code}` на `{reward}` 🌟 создан!")

@dp.callback_query(F.data == "promo_menu")
async def promo_menu(call: CallbackQuery):
    await call.message.answer("🎫 **Введите промокод:**\n(Отправьте его следующим сообщением)")

@dp.message(F.text)
async def use_promo(message: Message):
    code = message.text.strip()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT reward FROM promo WHERE code = ?", (code,)) as c:
            res = await c.fetchone()
            if res:
                await db.execute("UPDATE users SET stars = stars + ? WHERE user_id = ?", (res[0], message.from_user.id))
                await db.execute("DELETE FROM promo WHERE code = ?", (code,))
                await db.commit()
                await message.answer(f"✅ Успешно! +{res[0]} 🌟")
            else:
                pass # Обычный текст игнорируем

# --- RENDER WEB SERVER ---
@app.get("/")
async def root(): return {"status": "StarEarn Bot Alive"}

async def main():
    await init_db()
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, loop="asyncio")
    server = uvicorn.Server(config)
    logger.info("Starting production bot loop...")
    await asyncio.gather(dp.start_polling(bot), server.serve())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot shutdown.")
