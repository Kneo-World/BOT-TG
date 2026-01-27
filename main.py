"""
StarEarnBot - бот для заработка виртуальных звезд
Версия 2.0 с системой вывода
"""

import asyncio
import logging
import os
import sys
import sqlite3
import random
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from contextlib import contextmanager

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, 
    InlineKeyboardButton, ChatMemberUpdated, ReplyKeyboardRemove
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ Ошибка: BOT_TOKEN не найден!")
    print("📝 Добавьте BOT_TOKEN в настройках Render")
    sys.exit(1)

# Настройки
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "nft0top")
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1001234567890")
ADMIN_IDS = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "").split(",") if id.strip()]
WITHDRAWAL_CHANNEL_ID = os.getenv("WITHDRAWAL_CHANNEL_ID", "-1001234567890")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@Nft_top3")
PORT = int(os.getenv("PORT", 10000))

# Экономика
DAILY_MIN = 1
DAILY_MAX = 5
LUCK_MIN = 0
LUCK_MAX = 10
LUCK_COOLDOWN = 4 * 60 * 60  # 4 часа
REF_REWARD = 5  # Награда за реферала
GROUP_REWARD = 2  # Награда за добавление в группу
WITHDRAWAL_OPTIONS = [15, 25, 50, 100]  # Опции вывода

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
                    total_withdrawn INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_daily TIMESTAMP,
                    last_luck TIMESTAMP,
                    is_subscribed BOOLEAN DEFAULT 0,
                    ref_code TEXT UNIQUE
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
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS withdrawals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount INTEGER,
                    status TEXT DEFAULT 'pending',
                    admin_id INTEGER,
                    message_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    total_withdrawn INTEGER DEFAULT 0,
                    total_users INTEGER DEFAULT 0,
                    total_stars INTEGER DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Создаем начальную статистику
            conn.execute("INSERT OR IGNORE INTO bot_stats (id, total_withdrawn) VALUES (1, 1900)")
            conn.commit()
    
    def get_user(self, user_id: int) -> Optional[tuple]:
        """Получить пользователя"""
        with sqlite3.connect(self.path) as conn:
            cursor = conn.execute(
                "SELECT * FROM users WHERE user_id = ?", 
                (user_id,)
            )
            return cursor.fetchone()
    
    def create_user(self, user_id: int, username: str, first_name: str, last_name: str) -> bool:
        """Создать нового пользователя"""
        try:
            with sqlite3.connect(self.path) as conn:
                # Генерируем реферальный код
                ref_code = f"ref{user_id % 10000:04d}"
                
                conn.execute(
                    """INSERT OR IGNORE INTO users 
                    (user_id, username, first_name, last_name, ref_code) 
                    VALUES (?, ?, ?, ?, ?)""",
                    (user_id, username, first_name, last_name, ref_code)
                )
                conn.commit()
                return True
        except:
            return False
    
    def add_stars(self, user_id: int, amount: int) -> bool:
        """Добавить звезды"""
        try:
            with sqlite3.connect(self.path) as conn:
                conn.execute(
                    "UPDATE users SET stars = stars + ?, total_earned = total_earned + ? WHERE user_id = ?",
                    (amount, amount, user_id)
                )
                conn.commit()
                return True
        except:
            return False
    
    def subtract_stars(self, user_id: int, amount: int) -> bool:
        """Вычесть звезды"""
        try:
            with sqlite3.connect(self.path) as conn:
                # Проверяем баланс
                cursor = conn.execute(
                    "SELECT stars FROM users WHERE user_id = ?", 
                    (user_id,)
                )
                user = cursor.fetchone()
                
                if user and user[0] >= amount:
                    conn.execute(
                        "UPDATE users SET stars = stars - ?, total_withdrawn = total_withdrawn + ? WHERE user_id = ?",
                        (amount, amount, user_id)
                    )
                    conn.commit()
                    return True
                return False
        except:
            return False
    
    def record_transaction(self, user_id: int, amount: int, trans_type: str, desc: str = "") -> bool:
        """Записать транзакцию"""
        try:
            with sqlite3.connect(self.path) as conn:
                conn.execute(
                    """INSERT INTO transactions 
                    (user_id, amount, type, description) 
                    VALUES (?, ?, ?, ?)""",
                    (user_id, amount, trans_type, desc)
                )
                conn.commit()
                return True
        except:
            return False
    
    def update_last_daily(self, user_id: int) -> bool:
        """Обновить время последнего ежедневного бонуса"""
        try:
            with sqlite3.connect(self.path) as conn:
                conn.execute(
                    "UPDATE users SET last_daily = ? WHERE user_id = ?",
                    (datetime.now().isoformat(), user_id)
                )
                conn.commit()
                return True
        except:
            return False
    
    def update_last_luck(self, user_id: int) -> bool:
        """Обновить время последней игры"""
        try:
            with sqlite3.connect(self.path) as conn:
                conn.execute(
                    "UPDATE users SET last_luck = ? WHERE user_id = ?",
                    (datetime.now().isoformat(), user_id)
                )
                conn.commit()
                return True
        except:
            return False
    
    def add_referral(self, referrer_id: int, referred_id: int) -> bool:
        """Добавить реферала"""
        try:
            with sqlite3.connect(self.path) as conn:
                # Проверяем, не регистрировался ли уже по этой ссылке
                cursor = conn.execute(
                    "SELECT 1 FROM referrals WHERE referred_id = ?", 
                    (referred_id,)
                )
                if cursor.fetchone():
                    return False
                
                # Добавляем реферала
                conn.execute(
                    """INSERT INTO referrals (referrer_id, referred_id) 
                    VALUES (?, ?)""",
                    (referrer_id, referred_id)
                )
                
                # Увеличиваем счетчик рефералов
                conn.execute(
                    "UPDATE users SET referrals = referrals + 1 WHERE user_id = ?",
                    (referrer_id,)
                )
                
                conn.commit()
                return True
        except:
            return False
    
    def get_top_users(self, limit=10) -> List[tuple]:
        """Получить топ пользователей"""
        with sqlite3.connect(self.path) as conn:
            cursor = conn.execute(
                "SELECT user_id, username, first_name, stars FROM users ORDER BY stars DESC LIMIT ?",
                (limit,)
            )
            return cursor.fetchall()
    
    def create_withdrawal(self, user_id: int, amount: int) -> Optional[int]:
        """Создать заявку на вывод"""
        try:
            with sqlite3.connect(self.path) as conn:
                cursor = conn.execute(
                    """INSERT INTO withdrawals (user_id, amount) 
                    VALUES (?, ?) RETURNING id""",
                    (user_id, amount)
                )
                withdrawal_id = cursor.fetchone()[0]
                conn.commit()
                return withdrawal_id
        except:
            return None
    
    def update_withdrawal(self, withdrawal_id: int, status: str, admin_id: int = None) -> bool:
        """Обновить статус вывода"""
        try:
            with sqlite3.connect(self.path) as conn:
                conn.execute(
                    """UPDATE withdrawals 
                    SET status = ?, admin_id = ?, updated_at = ? 
                    WHERE id = ?""",
                    (status, admin_id, datetime.now().isoformat(), withdrawal_id)
                )
                conn.commit()
                return True
        except:
            return False
    
    def get_withdrawal(self, withdrawal_id: int) -> Optional[tuple]:
        """Получить заявку на вывод"""
        with sqlite3.connect(self.path) as conn:
            cursor = conn.execute(
                "SELECT * FROM withdrawals WHERE id = ?", 
                (withdrawal_id,)
            )
            return cursor.fetchone()
    
    def get_total_withdrawn(self) -> int:
        """Получить общее количество выведенных звезд"""
        with sqlite3.connect(self.path) as conn:
            cursor = conn.execute("SELECT total_withdrawn FROM bot_stats WHERE id = 1")
            result = cursor.fetchone()
            return result[0] if result else 1900
    
    def add_to_total_withdrawn(self, amount: int) -> bool:
        """Добавить к общему количеству выведенных звезд"""
        try:
            with sqlite3.connect(self.path) as conn:
                conn.execute(
                    "UPDATE bot_stats SET total_withdrawn = total_withdrawn + ? WHERE id = 1",
                    (amount,)
                )
                conn.commit()
                return True
        except:
            return False

# ========== ИНИЦИАЛИЗАЦИЯ ==========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
db = Database()

# ========== СОСТОЯНИЯ ==========
class WithdrawalStates(StatesGroup):
    waiting_amount = State()
    confirm_withdrawal = State()

# ========== КЛАВИАТУРЫ ==========
def main_menu():
    """Главное меню"""
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
        types.InlineKeyboardButton(text="🎯 Задания", callback_data="tasks")
    )
    builder.row(
        types.InlineKeyboardButton(text="🎮 Удача", callback_data="luck"),
        types.InlineKeyboardButton(text="👥 Рефералы", callback_data="referrals")
    )
    builder.row(
        types.InlineKeyboardButton(text="🏆 Топ", callback_data="top"),
        types.InlineKeyboardButton(text="📅 Ежедневный", callback_data="daily")
    )
    builder.row(
        types.InlineKeyboardButton(text="💎 Вывод", callback_data="withdraw"),
        types.InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")
    )
    return builder.as_markup()

def subscription_kb():
    """Клавиатура для проверки подписки"""
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(text="📢 Подписаться", url=f"https://t.me/{CHANNEL_USERNAME}")
    )
    builder.row(
        types.InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub")
    )
    return builder.as_markup()

def back_to_menu():
    """Кнопка назад"""
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🔙 В меню", callback_data="menu"))
    return builder.as_markup()

def withdrawal_amounts_kb():
    """Клавиатура выбора суммы вывода"""
    builder = InlineKeyboardBuilder()
    for amount in WITHDRAWAL_OPTIONS:
        builder.row(
            types.InlineKeyboardButton(
                text=f"💎 {amount} звезд", 
                callback_data=f"withdraw_{amount}"
            )
        )
    builder.row(types.InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    return builder.as_markup()

def withdrawal_confirm_kb(withdrawal_id: int):
    """Клавиатура подтверждения вывода"""
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_wd_{withdrawal_id}"),
        types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_wd")
    )
    return builder.as_markup()

def admin_withdrawal_kb(withdrawal_id: int):
    """Клавиатура для админа по заявке"""
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(text="✅ Принять", callback_data=f"admin_accept_{withdrawal_id}"),
        types.InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin_reject_{withdrawal_id}")
    )
    return builder.as_markup()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
async def check_subscription(user_id: int) -> bool:
    """Проверить подписку на канал"""
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Ошибка проверки подписки: {e}")
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

def censor_username(username: str) -> str:
    """Скрыть часть username звездочками"""
    if not username:
        return "Неизвестный"
    
    if username.startswith('@'):
        username = username[1:]
    
    if len(username) <= 4:
        return f"@{username[:2]}**"
    
    visible = username[:4]
    return f"@{visible}****"

async def ensure_user_registered(user_id: int, username: str = None, 
                                first_name: str = None, last_name: str = None) -> bool:
    """Убедиться что пользователь зарегистрирован"""
    user = db.get_user(user_id)
    if not user:
        return db.create_user(user_id, username or "", first_name or "", last_name or "")
    return True

# ========== ОБРАБОТЧИКИ КОМАНД ==========
@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Команда /start"""
    user = message.from_user
    
    # Регистрация пользователя
    await ensure_user_registered(
        user.id, 
        user.username, 
        user.first_name, 
        user.last_name
    )
    
    # Обработка реферального кода
    if len(message.text.split()) > 1:
        ref_code = message.text.split()[1]
        try:
            # Ищем пользователя с таким реферальным кодом
            with sqlite3.connect("bot_data.db") as conn:
                cursor = conn.execute(
                    "SELECT user_id FROM users WHERE ref_code = ?", 
                    (ref_code,)
                )
                result = cursor.fetchone()
                if result and result[0] != user.id:
                    referrer_id = result[0]
                    # Добавляем реферала
                    if db.add_referral(referrer_id, user.id):
                        # Начисляем награду рефереру
                        db.add_stars(referrer_id, REF_REWARD)
                        db.record_transaction(
                            referrer_id, REF_REWARD, "referral", 
                            f"Реферал: {user.id}"
                        )
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
    welcome_text = f"""
⭐ <b>Добро пожаловать, {user.first_name}!</b>

<b>StarsForQuestion</b> - система заработка виртуальных звезд!

🎯 <b>Зарабатывайте звезды:</b>
• 📅 Ежедневные бонусы (1-5 звезд)
• 🎮 Мини-игра "Удача" (0-10 звезд)
• 👥 Приглашайте друзей (+5 звезд за каждого)
• 💬 Добавляйте бота в группы (+2 звезды)

💎 <b>Выводите звезды!</b>
Минимальный вывод: 15 звезд

🏆 <b>Соревнуйтесь с другими в топе!</b>

💰 <b>Уже выдали: {db.get_total_withdrawn()}+ звезд!</b>
    """
    
    await message.answer(welcome_text, reply_markup=main_menu())

@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    """Команда /profile"""
    if not await ensure_user_registered(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    ):
        await message.answer("Ошибка регистрации!")
        return
    
    user_data = db.get_user(message.from_user.id)
    if not user_data:
        await message.answer("Сначала используйте /start")
        return
    
    stars_display = generate_stars(user_data[4])
    total_withdrawn = db.get_total_withdrawn()
    
    text = f"""
👤 <b>Личный кабинет</b>

🆔 ID: <code>{user_data[0]}</code>
📛 Имя: {user_data[2] or 'Не указано'}

⭐ Звезды: {user_data[4]} {stars_display}
👥 Рефералы: {user_data[5]}
💰 Всего заработано: {user_data[6]}
💎 Выведено: {user_data[7]}

📅 Регистрация: {user_data[8][:10] if user_data[8] else 'Нет данных'}

💰 <b>Всего ботом выдано: {total_withdrawn}+ звезд!</b>

💡 <b>Вывод доступен от 15 звезд</b>
    """
    await message.answer(text, reply_markup=main_menu())

@dp.message(Command("daily"))
async def cmd_daily(message: Message):
    """Ежедневный бонус"""
    user_id = message.from_user.id
    if not await ensure_user_registered(
        user_id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    ):
        await message.answer("Ошибка регистрации!")
        return
    
    user_data = db.get_user(user_id)
    
    if not user_data:
        await message.answer("Сначала используйте /start")
        return
    
    # Проверка времени
    last_daily = user_data[9]
    if last_daily:
        try:
            last_time = datetime.fromisoformat(last_daily)
            if (datetime.now() - last_time).days < 1:
                next_time = last_time + timedelta(days=1)
                wait = next_time - datetime.now()
                await message.answer(
                    f"⏳ Вы уже получали бонус сегодня!\n"
                    f"Следующий через: {format_time(wait.seconds)}",
                    reply_markup=back_to_menu()
                )
                return
        except:
            pass
    
    # Начисление бонуса
    reward = random.randint(DAILY_MIN, DAILY_MAX)
    db.add_stars(user_id, reward)
    db.record_transaction(user_id, reward, "daily", "Ежедневный бонус")
    db.update_last_daily(user_id)
    
    stars_display = generate_stars(reward)
    await message.answer(
        f"🎉 <b>Ежедневный бонус!</b>\n\n"
        f"Вы получили: +{reward} {stars_display}\n\n"
        f"Заходите завтра!",
        reply_markup=back_to_menu()
    )

@dp.message(Command("luck"))
async def cmd_luck(message: Message):
    """Игра 'Удача'"""
    user_id = message.from_user.id
    if not await ensure_user_registered(
        user_id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    ):
        await message.answer("Ошибка регистрации!")
        return
    
    user_data = db.get_user(user_id)
    
    if not user_data:
        await message.answer("Сначала используйте /start")
        return
    
    # Проверка кулдауна
    last_luck = user_data[10]
    if last_luck:
        try:
            last_time = datetime.fromisoformat(last_luck)
            seconds_passed = (datetime.now() - last_time).total_seconds()
            if seconds_passed < LUCK_COOLDOWN:
                wait = LUCK_COOLDOWN - seconds_passed
                await message.answer(
                    f"⏳ Игра доступна раз в 4 часа!\n"
                    f"Следующая игра через: {format_time(wait)}",
                    reply_markup=back_to_menu()
                )
                return
        except:
            pass
    
    # Игра
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🎰 Испытать удачу!", callback_data="play_luck"))
    builder.row(types.InlineKeyboardButton(text="🔙 В меню", callback_data="menu"))
    
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
    if not await ensure_user_registered(
        user_id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    ):
        await message.answer("Ошибка регистрации!")
        return
    
    user_data = db.get_user(user_id)
    ref_code = user_data[12] if user_data and len(user_data) > 12 else f"ref{user_id}"
    ref_link = f"https://t.me/{(await bot.get_me()).username}?start={ref_code}"
    
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
    builder.row(types.InlineKeyboardButton(text="🔗 Копировать ссылку", callback_data=f"copy_{ref_link}"))
    builder.row(types.InlineKeyboardButton(text="📢 Поделиться", switch_inline_query=f"Зарабатывай звезды со мной! {ref_link}"))
    builder.row(types.InlineKeyboardButton(text="🔙 В меню", callback_data="menu"))
    
    await message.answer(text, reply_markup=builder.as_markup())

@dp.message(Command("top"))
async def cmd_top(message: Message):
    """Топ игроков"""
    top_users = db.get_top_users(10)
    
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
    
    text += f"\n💰 <b>Всего выдано: {db.get_total_withdrawn()}+ звезд!</b>\n"
    text += "\n🎯 <i>Выполняйте задания и поднимайтесь в топе!</i>"
    
    await message.answer(text, reply_markup=back_to_menu())

@dp.message(Command("withdraw"))
async def cmd_withdraw(message: Message):
    """Вывод звезд"""
    user_id = message.from_user.id
    if not await ensure_user_registered(
        user_id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    ):
        await message.answer("Ошибка регистрации!")
        return
    
    user_data = db.get_user(user_id)
    if not user_data:
        await message.answer("Сначала используйте /start")
        return
    
    balance = user_data[4]
    total_withdrawn = db.get_total_withdrawn()
    
    if balance < 15:
        await message.answer(
            f"❌ <b>Недостаточно звезд для вывода!</b>\n\n"
            f"Ваш баланс: {balance} звезд\n"
            f"Минимальный вывод: 15 звезд\n\n"
            f"💡 <i>Зарабатывайте звезды через задания и игры!</i>",
            reply_markup=back_to_menu()
        )
        return
    
    text = f"""
💎 <b>Вывод звезд</b>

💰 <b>Ваш баланс:</b> {balance} звезд
💎 <b>Минимальный вывод:</b> 15 звезд

🎁 <b>Доступные суммы:</b>

📊 <b>Статистика бота:</b>
• Всего выдано: {total_withdrawn}+ звезд
• Разработчик: {SUPPORT_USERNAME}

⚠️ <b>Внимание:</b>
1. Вывод осуществляется в течение 24 часов
2. После создания заявки ожидайте подтверждения
3. Отменить заявку после создания нельзя
    """
    
    await message.answer(text, reply_markup=withdrawal_amounts_kb())

@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Справка"""
    total_withdrawn = db.get_total_withdrawn()
    
    text = f"""
ℹ️ <b>Помощь по StarsForQuestion</b>

<b>Основные команды:</b>
/start - Начать работу с ботом
/profile - Ваш профиль и баланс
/daily - Ежедневный бонус
/luck - Мини-игра "Удача"
/referral - Реферальная система
/top - Топ игроков
/withdraw - Вывод звезд
/help - Эта справка

<b>Как зарабатывать звезды:</b>
1. 📅 Забирайте ежедневный бонус (1-5 звезд)
2. 🎮 Играйте в "Удачу" раз в 4 часа (0-10 звезд)
3. 👥 Приглашайте друзей по реферальной ссылке (+5 звезд)
4. 💬 Добавляйте бота в группы от 10 человек (+2 звезды)

<b>Вывод звезд:</b>
• Минимальная сумма: 15 звезд
• Заявки обрабатываются в течение 24 часов
• Статистика: выдано {total_withdrawn}+ звезд

<b>Важно:</b>
• Для доступа к боту нужно подписаться на канал
• Звезды виртуальные
• Вывод осуществляется администратором

<b>Поддержка:</b>
Разработчик и поддержка: {SUPPORT_USERNAME}
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
    user_data = db.get_user(user_id)
    
    if not user_data:
        await callback.answer("Сначала используйте /start!", show_alert=True)
        return
    
    # Проверка кулдауна
    last_luck = user_data[10]
    if last_luck:
        try:
            last_time = datetime.fromisoformat(last_luck)
            if (datetime.now() - last_time).total_seconds() < LUCK_COOLDOWN:
                await callback.answer("Игра доступна раз в 4 часа!", show_alert=True)
                return
        except:
            pass
    
    # Генерация выигрыша
    reward = random.randint(LUCK_MIN, LUCK_MAX)
    
    # Начисление
    db.add_stars(user_id, reward)
    db.record_transaction(user_id, reward, "luck", "Мини-игра 'Удача'")
    db.update_last_luck(user_id)
    
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

@dp.callback_query(F.data.startswith("withdraw_"))
async def callback_withdraw_amount(callback: CallbackQuery, state: FSMContext):
    """Выбор суммы для вывода"""
    try:
        amount = int(callback.data.split("_")[1])
        user_id = callback.from_user.id
        
        user_data = db.get_user(user_id)
        if not user_data:
            await callback.answer("Сначала используйте /start!", show_alert=True)
            return
        
        balance = user_data[4]
        
        if amount < 15:
            await callback.answer("Минимальная сумма 15 звезд!", show_alert=True)
            return
        
        if balance < amount:
            await callback.answer("Недостаточно звезд!", show_alert=True)
            return
        
        # Создаем предварительную заявку
        withdrawal_id = db.create_withdrawal(user_id, amount)
        if not withdrawal_id:
            await callback.answer("Ошибка создания заявки!", show_alert=True)
            return
        
        await state.update_data(withdrawal_id=withdrawal_id, amount=amount)
        
        text = f"""
💎 <b>Подтверждение вывода</b>

📋 <b>Детали заявки:</b>
• Сумма: {amount} звезд
• Баланс: {balance} звезд
• Остаток после вывода: {balance - amount} звезд

⚠️ <b>Внимание:</b>
После подтверждения заявка будет отправлена на модерацию.
Отменить заявку будет невозможно.

✅ <b>Подтверждаете вывод {amount} звезд?</b>
        """
        
        await callback.message.edit_text(
            text,
            reply_markup=withdrawal_confirm_kb(withdrawal_id)
        )
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка вывода: {e}")
        await callback.answer("Ошибка!", show_alert=True)

@dp.callback_query(F.data.startswith("confirm_wd_"))
async def callback_confirm_withdrawal(callback: CallbackQuery, state: FSMContext):
    """Подтверждение вывода"""
    try:
        withdrawal_id = int(callback.data.split("_")[2])
        user = callback.from_user
        
        withdrawal_data = db.get_withdrawal(withdrawal_id)
        if not withdrawal_data:
            await callback.answer("Заявка не найдена!", show_alert=True)
            return
        
        user_id, amount, status = withdrawal_data[1], withdrawal_data[2], withdrawal_data[3]
        
        if status != "pending":
            await callback.answer("Заявка уже обработана!", show_alert=True)
            return
        
        if user.id != user_id:
            await callback.answer("Это не ваша заявка!", show_alert=True)
            return
        
        user_data = db.get_user(user_id)
        if not user_data:
            await callback.answer("Пользователь не найден!", show_alert=True)
            return
        
        balance = user_data[4]
        if balance < amount:
            await callback.answer("Недостаточно звезд!", show_alert=True)
            return
        
        # Снимаем звезды
        if not db.subtract_stars(user_id, amount):
            await callback.answer("Ошибка списания звезд!", show_alert=True)
            return
        
        # Обновляем статус заявки
        db.update_withdrawal(withdrawal_id, "processing")
        
        # Записываем транзакцию
        db.record_transaction(user_id, -amount, "withdrawal", f"Вывод {amount} звезд")
        
        # Отправляем заявку в канал
        censored_username = censor_username(user.username or user.first_name)
        
        channel_text = f"""
📥 <b>Новая заявка на вывод!</b>

👤 <b>Пользователь:</b> {censored_username}
🆔 <b>ID:</b> <code>{user_id}</code>
💎 <b>Сумма:</b> {amount} звезд
💰 <b>Баланс был:</b> {balance} звезд
⏰ <b>Время:</b> {datetime.now().strftime('%H:%M %d.%m.%Y')}

#вывод #заявка_{withdrawal_id}
        """
        
        try:
            message = await bot.send_message(
                chat_id=WITHDRAWAL_CHANNEL_ID,
                text=channel_text,
                reply_markup=admin_withdrawal_kb(withdrawal_id)
            )
            
            # Сохраняем ID сообщения
            with sqlite3.connect("bot_data.db") as conn:
                conn.execute(
                    "UPDATE withdrawals SET message_id = ? WHERE id = ?",
                    (message.message_id, withdrawal_id)
                )
                conn.commit()
                
        except Exception as e:
            logger.error(f"Ошибка отправки в канал: {e}")
        
        # Уведомляем пользователя
        total_withdrawn = db.get_total_withdrawn() + amount
        db.add_to_total_withdrawn(amount)
        
        await callback.message.edit_text(
            f"✅ <b>Заявка #{withdrawal_id} создана!</b>\n\n"
            f"💎 <b>Сумма:</b> {amount} звезд\n"
            f"⏰ <b>Статус:</b> На модерации\n"
            f"🕐 <b>Ожидайте:</b> До 24 часов\n\n"
            f"💰 <b>Всего выдано:</b> {total_withdrawn}+ звезд\n\n"
            f"📞 <b>Поддержка:</b> {SUPPORT_USERNAME}",
            reply_markup=back_to_menu()
        )
        
        await callback.answer("Заявка отправлена на модерацию!")
        
    except Exception as e:
        logger.error(f"Ошибка подтверждения вывода: {e}")
        await callback.answer("Ошибка!", show_alert=True)

@dp.callback_query(F.data == "cancel_wd")
async def callback_cancel_withdrawal(callback: CallbackQuery, state: FSMContext):
    """Отмена вывода"""
    await state.clear()
    await callback.message.edit_text(
        "❌ <b>Вывод отменен</b>\n\nВозвращаемся в главное меню...",
        reply_markup=main_menu()
    )
    await callback.answer("Вывод отменен")

@dp.callback_query(F.data.startswith("admin_accept_"))
async def callback_admin_accept(callback: CallbackQuery):
    """Админ принимает заявку"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Доступ запрещен!", show_alert=True)
        return
    
    try:
        withdrawal_id = int(callback.data.split("_")[2])
        withdrawal_data = db.get_withdrawal(withdrawal_id)
        
        if not withdrawal_data:
            await callback.answer("Заявка не найдена!", show_alert=True)
            return
        
        if withdrawal_data[3] != "processing":
            await callback.answer("Заявка уже обработана!", show_alert=True)
            return
        
        user_id, amount = withdrawal_data[1], withdrawal_data[2]
        
        # Обновляем статус
        db.update_withdrawal(withdrawal_id, "completed", callback.from_user.id)
        
        # Получаем информацию о пользователе
        user_data = db.get_user(user_id)
        username = user_data[1] if user_data else None
        
        # Обновляем сообщение в канале
        censored_username = censor_username(username or f"user{user_id}")
        
        completed_text = f"""
✅ <b>Заявка #{withdrawal_id} ВЫПОЛНЕНА!</b>

👤 <b>Пользователь:</b> {censored_username}
💎 <b>Сумма:</b> {amount} звезд
👑 <b>Исполнитель:</b> @{callback.from_user.username or 'admin'}
⏰ <b>Время выполнения:</b> {datetime.now().strftime('%H:%M %d.%m.%Y')}

💰 <b>Всего выдано:</b> {db.get_total_withdrawn()}+ звезд!

🎁 <b>Подарок отправлен!</b> 🎁
        """
        
        try:
            # Пытаемся отредактировать сообщение
            if withdrawal_data[5]:  # message_id
                await bot.edit_message_text(
                    chat_id=WITHDRAWAL_CHANNEL_ID,
                    message_id=withdrawal_data[5],
                    text=completed_text
                )
        except:
            pass
        
        # Уведомляем пользователя
        try:
            await bot.send_message(
                user_id,
                f"🎉 <b>Ваша заявка #{withdrawal_id} выполнена!</b>\n\n"
                f"💎 <b>Сумма:</b> {amount} звезд\n"
                f"👑 <b>Исполнитель:</b> @{callback.from_user.username or 'admin'}\n"
                f"⏰ <b>Время:</b> {datetime.now().strftime('%H:%M %d.%m.%Y')}\n\n"
                f"💰 <b>Всего ботом выдано:</b> {db.get_total_withdrawn()}+ звезд!\n\n"
                f"🎁 <b>Спасибо за использование бота!</b>"
            )
        except:
            pass
        
        await callback.answer(f"Заявка #{withdrawal_id} принята!")
        
    except Exception as e:
        logger.error(f"Ошибка принятия заявки: {e}")
        await callback.answer("Ошибка!", show_alert=True)

@dp.callback_query(F.data.startswith("admin_reject_"))
async def callback_admin_reject(callback: CallbackQuery):
    """Админ отклоняет заявку"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Доступ запрещен!", show_alert=True)
        return
    
    try:
        withdrawal_id = int(callback.data.split("_")[2])
        withdrawal_data = db.get_withdrawal(withdrawal_id)
        
        if not withdrawal_data:
            await callback.answer("Заявка не найдена!", show_alert=True)
            return
        
        if withdrawal_data[3] != "processing":
            await callback.answer("Заявка уже обработана!", show_alert=True)
            return
        
        user_id, amount = withdrawal_data[1], withdrawal_data[2]
        
        # Возвращаем звезды пользователю
        db.add_stars(user_id, amount)
        db.record_transaction(user_id, amount, "refund", f"Возврат по заявке #{withdrawal_id}")
        
        # Обновляем статус
        db.update_withdrawal(withdrawal_id, "rejected", callback.from_user.id)
        
        # Обновляем сообщение в канале
        rejected_text = f"""
❌ <b>Заявка #{withdrawal_id} ОТКЛОНЕНА!</b>

💎 <b>Сумма:</b> {amount} звезд
👑 <b>Исполнитель:</b> @{callback.from_user.username or 'admin'}
⏰ <b>Время:</b> {datetime.now().strftime('%H:%M %d.%m.%Y')}

💰 <b>Всего выдано:</b> {db.get_total_withdrawn()}+ звезд!

⚠️ <b>Заявка отклонена, звезды возвращены пользователю.</b>
        """
        
        try:
            if withdrawal_data[5]:  # message_id
                await bot.edit_message_text(
                    chat_id=WITHDRAWAL_CHANNEL_ID,
                    message_id=withdrawal_data[5],
                    text=rejected_text
                )
        except:
            pass
        
        # Уведомляем пользователя
        try:
            await bot.send_message(
                user_id,
                f"❌ <b>Ваша заявка #{withdrawal_id} отклонена!</b>\n\n"
                f"💎 <b>Сумма:</b> {amount} звезд\n"
                f"👑 <b>Исполнитель:</b> @{callback.from_user.username or 'admin'}\n"
                f"⏰ <b>Время:</b> {datetime.now().strftime('%H:%M %d.%m.%Y')}\n\n"
                f"💰 <b>Звезды возвращены на ваш счет.</b>\n"
                f"💡 <b>Причина:</b> Нарушение правил или ошибка в заявке\n\n"
                f"📞 <b>По вопросам:</b> {SUPPORT_USERNAME}"
            )
        except:
            pass
        
        await callback.answer(f"Заявка #{withdrawal_id} отклонена!")
        
    except Exception as e:
        logger.error(f"Ошибка отклонения заявки: {e}")
        await callback.answer("Ошибка!", show_alert=True)

@dp.callback_query(F.data == "menu")
async def callback_menu(callback: CallbackQuery):
    """Возврат в меню"""
    await callback.message.edit_text(
        "⭐ <b>Главное меню StarsForQuestion</b>\n\nВыберите действие:",
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

@dp.callback_query(F.data == "withdraw")
async def callback_withdraw(callback: CallbackQuery):
    """Вывод из меню"""
    await cmd_withdraw(callback.message)
    await callback.answer()

@dp.callback_query(F.data == "tasks")
async def callback_tasks(callback: CallbackQuery):
    """Задания из меню"""
    total_withdrawn = db.get_total_withdrawn()
    
    text = f"""
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
   
💰 <b>Статистика бота:</b>
• Выдано звезд: {total_withdrawn}+
• Разработчик: {SUPPORT_USERNAME}

⭐ <b>Выполняйте задания и зарабатывайте!</b>
    """
    
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="👥 Рефералы", callback_data="referrals"))
    builder.row(types.InlineKeyboardButton(text="🎮 Удача", callback_data="luck"))
    builder.row(types.InlineKeyboardButton(text="📅 Ежедневный", callback_data="daily"))
    builder.row(types.InlineKeyboardButton(text="💎 Вывод", callback_data="withdraw"))
    builder.row(types.InlineKeyboardButton(text="🔙 В меню", callback_data="menu"))
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data == "help")
async def callback_help(callback: CallbackQuery):
    """Помощь из меню"""
    await cmd_help(callback.message)
    await callback.answer()

@dp.callback_query(F.data.startswith("copy_"))
async def callback_copy(callback: CallbackQuery):
    """Копирование ссылки"""
    import pyperclip
    try:
        ref_link = callback.data[5:]
        pyperclip.copy(ref_link)
        await callback.answer("Ссылка скопирована в буфер обмена!", show_alert=True)
    except:
        await callback.answer(f"Ссылка: {ref_link}", show_alert=True)

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
                        # Награждаем только если пользователь зарегистрирован
                        user_data = db.get_user(user_id)
                        if user_data:
                            # Проверяем, не получал ли уже награду за эту группу
                            with sqlite3.connect("bot_data.db") as conn:
                                cursor = conn.execute(
                                    """SELECT 1 FROM transactions 
                                    WHERE user_id = ? AND description LIKE ?""",
                                    (user_id, f"%группу {chat_id}%")
                                )
                                if not cursor.fetchone():
                                    db.add_stars(user_id, GROUP_REWARD)
                                    db.record_transaction(
                                        user_id, GROUP_REWARD, "group", 
                                        f"Добавление в группу {chat_id}"
                                    )
                                    
                                    # Уведомление
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
                    f"Я <b>StarsForQuestion</b> - бот для заработка звезд!\n\n"
                    f"Напишите мне в ЛС: @{(await bot.get_me()).username}\n"
                    f"⭐ Админы получили бонус за добавление!\n"
                    f"💰 Уже выдано: {db.get_total_withdrawn()}+ звезд!"
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
        return "StarsForQuestion Bot is alive!", 200
    
    @flask_app.route('/ping')
    def ping():
        return "pong", 200
    
    @flask_app.route('/health')
    def health():
        return {
            "status": "ok", 
            "time": datetime.now().isoformat(),
            "total_withdrawn": db.get_total_withdrawn()
        }, 200
    
    def run_flask():
        flask_app.run(host='0.0.0.0', port=PORT)
    
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False
    logger.warning("Flask не установлен, Keep-Alive сервер недоступен")

# ========== ЗАПУСК БОТА ==========
async def main():
    """Основная функция"""
    logger.info("=== Запуск StarsForQuestion Bot ===")
    
    # Проверяем подключение к боту
    try:
        bot_info = await bot.get_me()
        logger.info(f"Бот запущен: @{bot_info.username}")
    except Exception as e:
        logger.error(f"Ошибка подключения к боту: {e}")
        return
    
    # Запуск Flask в отдельном потоке
    if HAS_FLASK:
        flask_thread = Thread(target=run_flask, daemon=True)
        flask_thread.start()
        logger.info(f"Flask сервер запущен на порту {PORT}")
    
    # Запуск бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Проверка токена
    if not BOT_TOKEN or "your_bot_token" in BOT_TOKEN:
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
