"""
StarEarnBot - бот для заработка виртуальных звезд
Всё в одном файле для простого деплоя на Render
"""

import asyncio
import logging
import os
import sys
import sqlite3
import random
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, 
    InlineKeyboardButton, ChatMemberUpdated
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ Ошибка: BOT_TOKEN не найден в переменных окружения!")
    print("📝 Добавьте BOT_TOKEN в настройках Render")
    sys.exit(1)

# Настройки
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "nft0top")
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1001234567890")
ADMIN_IDS = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "").split(",") if id.strip()]
PORT = int(os.getenv("PORT", 10000))

# Экономика
DAILY_MIN = 1
DAILY_MAX = 5
LUCK_MIN = 0
LUCK_MAX = 10
LUCK_COOLDOWN = 4 * 60 * 60  # 4 часа
REF_REWARD = 5  # Награда за реферала
GROUP_REWARD = 2  # Награда за добавление в группу

# ========== БАЗА ДАННЫХ ==========
class Database:
    """Упрощенная база данных SQLite"""
    
    def __init__(self, path="bot_data.db"):
        self.path = path
        self.init_db()
    
    def init_db(self):
        """Инициализация таблиц"""
        with sqlite3.connect(self.path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    stars INTEGER DEFAULT 0,
                    referrals INTEGER DEFAULT 0,
                    total_earned INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_daily TIMESTAMP,
                    last_luck TIMESTAMP,
                    is_subscribed BOOLEAN DEFAULT 0
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS referrals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    referrer_id INTEGER,
                    referred_id INTEGER UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount INTEGER,
                    type TEXT,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.commit()
    
    async def get_user(self, user_id: int):
        """Получить пользователя"""
        with sqlite3.connect(self.path) as conn:
            cursor = conn.execute(
                "SELECT * FROM users WHERE user_id = ?", 
                (user_id,)
            )
            return cursor.fetchone()
    
    async def create_user(self, user_id: int, username: str, first_name: str, last_name: str):
        """Создать нового пользователя"""
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """INSERT OR IGNORE INTO users 
                (user_id, username, first_name, last_name) 
                VALUES (?, ?, ?, ?)""",
                (user_id, username, first_name, last_name)
            )
            conn.commit()
    
    async def add_stars(self, user_id: int, amount: int):
        """Добавить звезды"""
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "UPDATE users SET stars = stars + ?, total_earned = total_earned + ? WHERE user_id = ?",
                (amount, amount, user_id)
            )
            conn.commit()
    
    async def record_transaction(self, user_id: int, amount: int, trans_type: str, desc: str = ""):
        """Записать транзакцию"""
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """INSERT INTO transactions 
                (user_id, amount, type, description) 
                VALUES (?, ?, ?, ?)""",
                (user_id, amount, trans_type, desc)
            )
            conn.commit()
    
    async def update_last_daily(self, user_id: int):
        """Обновить время последнего ежедневного бонуса"""
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "UPDATE users SET last_daily = ? WHERE user_id = ?",
                (datetime.now().isoformat(), user_id)
            )
            conn.commit()
    
    async def update_last_luck(self, user_id: int):
        """Обновить время последней игры"""
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "UPDATE users SET last_luck = ? WHERE user_id = ?",
                (datetime.now().isoformat(), user_id)
            )
            conn.commit()
    
    async def add_referral(self, referrer_id: int, referred_id: int):
        """Добавить реферала"""
        with sqlite3.connect(self.path) as conn:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO referrals 
                    (referrer_id, referred_id) 
                    VALUES (?, ?)""",
                    (referrer_id, referred_id)
                )
                if conn.total_changes > 0:
                    conn.execute(
                        "UPDATE users SET referrals = referrals + 1 WHERE user_id = ?",
                        (referrer_id,)
                    )
                conn.commit()
                return True
            except:
                return False
    
    async def get_top_users(self, limit=10):
        """Получить топ пользователей"""
        with sqlite3.connect(self.path) as conn:
            cursor = conn.execute(
                "SELECT user_id, username, first_name, stars FROM users ORDER BY stars DESC LIMIT ?",
                (limit,)
            )
            return cursor.fetchall()

# ========== ИНИЦИАЛИЗАЦИЯ ==========
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
db = Database()

# ========== КЛАВИАТУРЫ ==========
def main_menu():
    """Главное меню"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
        InlineKeyboardButton(text="🎯 Задания", callback_data="tasks")
    )
    builder.row(
        InlineKeyboardButton(text="🎮 Удача", callback_data="luck"),
        InlineKeyboardButton(text="👥 Рефералы", callback_data="referrals")
    )
    builder.row(
        InlineKeyboardButton(text="🏆 Топ", callback_data="top"),
        InlineKeyboardButton(text="📅 Ежедневный", callback_data="daily")
    )
    return builder.as_markup()

def subscription_kb():
    """Клавиатура для проверки подписки"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📢 Подписаться", url=f"https://t.me/{CHANNEL_USERNAME}")
    )
    builder.row(
        InlineKeyboardButton(text="✅ Проверить", callback_data="check_sub")
    )
    return builder.as_markup()

def back_to_menu():
    """Кнопка назад"""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 В меню", callback_data="menu"))
    return builder.as_markup()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
async def check_subscription(user_id: int) -> bool:
    """Проверить подписку на канал"""
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return False

def generate_stars(count: int) -> str:
    """Сгенерировать отображение звезд"""
    if count <= 0:
        return "☆"
    full = min(count, 5)
    stars = "★" * full
    if count > 5:
        stars += f" (+{count-5})"
    return stars

def format_time(seconds: int) -> str:
    """Форматировать время"""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}ч {minutes}м"

# ========== ОБРАБОТЧИКИ КОМАНД ==========
@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Команда /start"""
    user = message.from_user
    
    # Регистрация пользователя
    await db.create_user(user.id, user.username, user.first_name, user.last_name)
    
    # Обработка реферального кода
    ref_code = None
    if len(message.text.split()) > 1:
        try:
            ref_code = int(message.text.split()[1])
            if ref_code != user.id:
                await db.add_referral(ref_code, user.id)
                await db.add_stars(ref_code, REF_REWARD)
                await db.record_transaction(ref_code, REF_REWARD, "referral", f"Реферал: {user.id}")
        except:
            pass
    
    # Проверка подписки
    if not await check_subscription(user.id):
        await message.answer(
            "📢 <b>Для использования бота необходимо подписаться на канал!</b>\n\n"
            f"Канал: @{CHANNEL_USERNAME}\n"
            "Подпишитесь и нажмите кнопку проверки:",
            reply_markup=subscription_kb()
        )
        return
    
    # Приветствие
    await message.answer(
        f"⭐ <b>Добро пожаловать, {user.first_name}!</b>\n\n"
        "<b>StarEarnBot</b> - система заработка виртуальных звезд!\n\n"
        "🎯 <b>Зарабатывайте звезды:</b>\n"
        "• 📅 Ежедневные бонусы\n"
        "• 🎮 Мини-игра 'Удача'\n"
        "• 👥 Приглашайте друзей\n"
        "• 💬 Добавляйте бота в группы\n\n"
        "🏆 <b>Соревнуйтесь с другими в топе!</b>",
        reply_markup=main_menu()
    )
    
    # Сообщение о реферале
    if ref_code:
        await message.answer(f"🎉 Вы зарегистрировались по реферальной ссылке! Реферер получил +{REF_REWARD} звезд!")

@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    """Команда /profile"""
    user_data = await db.get_user(message.from_user.id)
    if not user_data:
        await message.answer("Сначала используйте /start")
        return
    
    stars_display = generate_stars(user_data[4])
    
    text = f"""
👤 <b>Личный кабинет</b>

🆔 ID: <code>{user_data[0]}</code>
📛 Имя: {user_data[2] or 'Не указано'}

⭐ Звезды: {user_data[4]} {stars_display}
👥 Рефералы: {user_data[5]}
💰 Всего заработано: {user_data[6]}

📅 Регистрация: {user_data[7][:10]}
"""
    await message.answer(text, reply_markup=back_to_menu())

@dp.message(Command("daily"))
async def cmd_daily(message: Message):
    """Ежедневный бонус"""
    user_id = message.from_user.id
    user_data = await db.get_user(user_id)
    
    if not user_data:
        await message.answer("Сначала используйте /start")
        return
    
    # Проверка времени
    last_daily = user_data[8]
    if last_daily:
        last_time = datetime.fromisoformat(last_daily)
        if (datetime.now() - last_time).days < 1:
            next_time = last_time + timedelta(days=1)
            wait = next_time - datetime.now()
            await message.answer(f"⏳ Вы уже получали бонус сегодня!\nСледующий через: {format_time(wait.seconds)}")
            return
    
    # Начисление бонуса
    reward = random.randint(DAILY_MIN, DAILY_MAX)
    await db.add_stars(user_id, reward)
    await db.record_transaction(user_id, reward, "daily", "Ежедневный бонус")
    await db.update_last_daily(user_id)
    
    stars_display = generate_stars(reward)
    await message.answer(f"🎉 <b>Ежедневный бонус!</b>\n\nВы получили: +{reward} {stars_display}\n\nЗаходите завтра!", reply_markup=back_to_menu())

@dp.message(Command("luck"))
async def cmd_luck(message: Message):
    """Игра 'Удача'"""
    user_id = message.from_user.id
    user_data = await db.get_user(user_id)
    
    if not user_data:
        await message.answer("Сначала используйте /start")
        return
    
    # Проверка кулдауна
    last_luck = user_data[9]
    if last_luck:
        last_time = datetime.fromisoformat(last_luck)
        seconds_passed = (datetime.now() - last_time).total_seconds()
        if seconds_passed < LUCK_COOLDOWN:
            wait = LUCK_COOLDOWN - seconds_passed
            await message.answer(f"⏳ Игра доступна раз в 4 часа!\nСледующая игра через: {format_time(wait)}")
            return
    
    # Игра
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎰 Испытать удачу!", callback_data="play_luck"))
    builder.row(InlineKeyboardButton(text="🔙 В меню", callback_data="menu"))
    
    await message.answer(
        "🎮 <b>Мини-игра 'Удача'</b>\n\n"
        "Попробуйте удачу и выиграйте звезды!\n"
        "Награда: от 0 до 10 звезд!\n"
        "Играть можно раз в 4 часа.",
        reply_markup=builder.as_markup()
    )

@dp.message(Command("referral"))
async def cmd_referral(message: Message):
    """Реферальная система"""
    user_id = message.from_user.id
    ref_link = f"https://t.me/{(await bot.get_me()).username}?start={user_id}"
    
    user_data = await db.get_user(user_id)
    ref_count = user_data[5] if user_data else 0
    
    text = f"""
👥 <b>Реферальная система</b>

🔗 <b>Ваша ссылка:</b>
<code>{ref_link}</code>

📊 <b>Статистика:</b>
• Приглашено: {ref_count} человек
• Заработано: {ref_count * REF_REWARD} звезд

🎯 <b>Как работает:</b>
1. Отправьте другу вашу ссылку
2. Друг нажимает и начинает общение с ботом
3. Вы получаете +{REF_REWARD} звезд сразу!

💰 <b>Зарабатывайте больше - приглашайте больше!</b>
"""
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔗 Копировать ссылку", callback_data=f"copy_{ref_link}"))
    builder.row(InlineKeyboardButton(text="📢 Поделиться", switch_inline_query=f"Зарабатывай звезды со мной! {ref_link}"))
    builder.row(InlineKeyboardButton(text="🔙 В меню", callback_data="menu"))
    
    await message.answer(text, reply_markup=builder.as_markup())

@dp.message(Command("top"))
async def cmd_top(message: Message):
    """Топ игроков"""
    top_users = await db.get_top_users(10)
    
    if not top_users:
        await message.answer("Топ пока пуст! Будьте первым!")
        return
    
    text = "🏆 <b>Топ игроков по звездам</b>\n\n"
    
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    
    for i, (user_id, username, first_name, stars) in enumerate(top_users[:10]):
        medal = medals[i] if i < len(medals) else f"{i+1}."
        name = username or first_name or f"User{user_id}"
        stars_display = generate_stars(stars)
        text += f"{medal} {name}: {stars} {stars_display}\n"
    
    text += "\n🎯 <i>Выполняйте задания и поднимайтесь в топе!</i>"
    
    await message.answer(text, reply_markup=back_to_menu())

@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Справка"""
    text = """
ℹ️ <b>Помощь по StarEarnBot</b>

<b>Основные команды:</b>
/start - Начать работу с ботом
/profile - Ваш профиль и баланс
/daily - Ежедневный бонус
/luck - Мини-игра "Удача"
/referral - Реферальная система
/top - Топ игроков
/help - Эта справка

<b>Как зарабатывать звезды:</b>
1. 📅 Забирайте ежедневный бонус
2. 🎮 Играйте в "Удачу" раз в 4 часа
3. 👥 Приглашайте друзей по реферальной ссылке
4. 💬 Добавляйте бота в группы (от 10 участников)

<b>Важно:</b>
• Для доступа к боту нужно подписаться на канал
• Звезды виртуальные, для развлечения
• Наслаждайтесь игрой!
"""
    await message.answer(text, reply_markup=back_to_menu())

# ========== ОБРАБОТЧИКИ CALLBACK ==========
@dp.callback_query(F.data == "check_sub")
async def callback_check_sub(callback: CallbackQuery):
    """Проверка подписки"""
    if await check_subscription(callback.from_user.id):
        await callback.message.edit_text(
            "✅ <b>Отлично! Вы подписаны!</b>\n\nТеперь вам доступны все функции бота!",
            reply_markup=main_menu()
        )
        await callback.answer("Подписка подтверждена!")
    else:
        await callback.answer("❌ Вы еще не подписались!", show_alert=True)

@dp.callback_query(F.data == "play_luck")
async def callback_play_luck(callback: CallbackQuery):
    """Играть в удачу"""
    user_id = callback.from_user.id
    user_data = await db.get_user(user_id)
    
    if not user_data:
        await callback.answer("Ошибка!", show_alert=True)
        return
    
    # Проверка кулдауна
    last_luck = user_data[9]
    if last_luck:
        last_time = datetime.fromisoformat(last_luck)
        if (datetime.now() - last_time).total_seconds() < LUCK_COOLDOWN:
            await callback.answer("Игра доступна раз в 4 часа!", show_alert=True)
            return
    
    # Генерация выигрыша
    reward = random.randint(LUCK_MIN, LUCK_MAX)
    
    # Начисление
    await db.add_stars(user_id, reward)
    await db.record_transaction(user_id, reward, "luck", "Мини-игра 'Удача'")
    await db.update_last_luck(user_id)
    
    # Результат
    if reward == 0:
        result = "😔 Не повезло... Вы не выиграли звезд"
    elif reward < 5:
        result = f"🎉 Неплохо! Вы выиграли {reward} звезд"
    elif reward < 8:
        result = f"🎊 Отлично! Вы выиграли {reward} звезд!"
    else:
        result = f"🔥 ДЖЕКПОТ! {reward} звезд!"
    
    await callback.message.edit_text(
        f"{result}\n\n🎮 Следующая игра через 4 часа!",
        reply_markup=back_to_menu()
    )
    await callback.answer(f"Вы выиграли {reward} звезд!")

@dp.callback_query(F.data.startswith("copy_"))
async def callback_copy(callback: CallbackQuery):
    """Копирование ссылки"""
    ref_link = callback.data[5:]
    await callback.answer(f"Ссылка скопирована!\n{ref_link}", show_alert=True)

@dp.callback_query(F.data == "menu")
async def callback_menu(callback: CallbackQuery):
    """Возврат в меню"""
    await callback.message.edit_text(
        "⭐ <b>Главное меню StarEarnBot</b>\n\nВыберите действие:",
        reply_markup=main_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "profile")
async def callback_profile(callback: CallbackQuery):
    """Профиль из меню"""
    await cmd_profile(callback.message)
    await callback.answer()

@dp.callback_query(F.data == "daily")
async def callback_daily(callback: CallbackQuery):
    """Ежедневный бонус из меню"""
    await cmd_daily(callback.message)
    await callback.answer()

@dp.callback_query(F.data == "luck")
async def callback_luck(callback: CallbackQuery):
    """Игра из меню"""
    await cmd_luck(callback.message)
    await callback.answer()

@dp.callback_query(F.data == "referrals")
async def callback_referrals(callback: CallbackQuery):
    """Рефералы из меню"""
    await cmd_referral(callback.message)
    await callback.answer()

@dp.callback_query(F.data == "top")
async def callback_top(callback: CallbackQuery):
    """Топ из меню"""
    await cmd_top(callback.message)
    await callback.answer()

@dp.callback_query(F.data == "tasks")
async def callback_tasks(callback: CallbackQuery):
    """Задания из меню"""
    text = """
🎯 <b>Доступные задания</b>

1. 📢 <b>Подписка на канал</b>
   • Подпишитесь на @{CHANNEL_USERNAME}
   • Награда: доступ ко всем функциям
   
2. 👥 <b>Пригласите друга</b>
   • Используйте реферальную ссылку
   • Награда: +{REF_REWARD} звезд за каждого
   
3. 🎮 <b>Сыграйте в "Удачу"</b>
   • Доступно раз в 4 часа
   • Награда: 0-10 звезд
   
4. 📅 <b>Ежедневный бонус</b>
   • Заходите каждый день
   • Награда: 1-5 звезд
   
5. 💬 <b>Добавьте бота в группу</b>
   • Добавьте бота в группу от 10 человек
   • Награда: +{GROUP_REWARD} звезд
   
⭐ <b>Выполняйте задания и зарабатывайте!</b>
    """.format(CHANNEL_USERNAME=CHANNEL_USERNAME, REF_REWARD=REF_REWARD, GROUP_REWARD=GROUP_REWARD)
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="👥 Рефералы", callback_data="referrals"))
    builder.row(InlineKeyboardButton(text="🎮 Удача", callback_data="luck"))
    builder.row(InlineKeyboardButton(text="📅 Ежедневный", callback_data="daily"))
    builder.row(InlineKeyboardButton(text="🔙 В меню", callback_data="menu"))
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()

# ========== ОБРАБОТЧИК ГРУПП ==========
@dp.chat_member()
async def chat_member_update(update: ChatMemberUpdated):
    """Добавление бота в группу"""
    if update.new_chat_member.status == "member":
        try:
            chat_id = update.chat.id
            member_count = await bot.get_chat_member_count(chat_id)
            
            if member_count >= 10:
                # Получаем администраторов
                admins = await bot.get_chat_administrators(chat_id)
                for admin in admins:
                    if not admin.user.is_bot:
                        user_id = admin.user.id
                        # Награждаем
                        await db.add_stars(user_id, GROUP_REWARD)
                        await db.record_transaction(
                            user_id, GROUP_REWARD, "group", 
                            f"Добавление в группу {chat_id}"
                        )
                        
                        # Уведомление (если можем отправить)
                        try:
                            await bot.send_message(
                                user_id,
                                f"🎉 <b>Бонус за добавление бота в группу!</b>\n\n"
                                f"Вы добавили бота в группу\n"
                                f"На ваш счет начислено +{GROUP_REWARD} звезд!"
                            )
                        except:
                            pass
                
                # Приветствие в группе
                await bot.send_message(
                    chat_id,
                    f"👋 <b>Приветствую участников!</b>\n\n"
                    f"Я <b>StarEarnBot</b> - бот для заработка звезд!\n\n"
                    f"Напишите мне в ЛС: @{(await bot.get_me()).username}\n"
                    f"⭐ Админы получили бонус за добавление!"
                )
        except Exception as e:
            logger.error(f"Ошибка обработки группы: {e}")

# ========== KEEP-ALIVE СЕРВЕР ==========
try:
    from flask import Flask
    from threading import Thread
    
    flask_app = Flask(__name__)
    
    @flask_app.route('/')
    def home():
        return "StarEarnBot is alive!", 200
    
    @flask_app.route('/ping')
    def ping():
        return "pong", 200
    
    @flask_app.route('/health')
    def health():
        return {"status": "ok", "time": datetime.now().isoformat()}, 200
    
    def run_flask():
        flask_app.run(host='0.0.0.0', port=PORT)
    
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False
    logger.warning("Flask не установлен, Keep-Alive сервер недоступен")

# ========== ЗАПУСК БОТА ==========
async def main():
    """Основная функция"""
    logger.info("=== Запуск StarEarnBot ===")
    
    # Запуск Flask в отдельном потоке
    if HAS_FLASK:
        flask_thread = Thread(target=run_flask, daemon=True)
        flask_thread.start()
        logger.info(f"Flask сервер запущен на порту {PORT}")
    
    # Запуск бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Проверка токена
    if not BOT_TOKEN or BOT_TOKEN == "your_bot_token_here":
        print("❌ Ошибка: Неправильный токен бота!")
        print("📝 Получите токен у @BotFather и настройте в Render")
        sys.exit(1)
    
    # Запуск
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
