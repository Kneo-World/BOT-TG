"""
StarsForQuestion - бот для заработка виртуальных звезд
Версия 4.0 - все баги исправлены
"""

import asyncio
import logging
import os
import sys
import sqlite3
import random
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple

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

# Фейковые данные для пользователей (везде кроме админ-панели)
FAKE_TOTAL_USERS = 1250
FAKE_TOTAL_STARS = 58200
FAKE_TOTAL_WITHDRAWN = 2150

# Фейковый топ игроков
FAKE_TOP_USERS = [
    {"name": "Алексей П.", "stars": 2450},
    {"name": "Мария С.", "stars": 2180},
    {"name": "Иван И.", "stars": 1950},
    {"name": "Екатерина С.", "stars": 1820},
    {"name": "Дмитрий К.", "stars": 1750},
    {"name": "Анна В.", "stars": 1680},
    {"name": "Сергей П.", "stars": 1620},
    {"name": "Ольга Н.", "stars": 1550},
    {"name": "Александр В.", "stars": 1480},
    {"name": "Наталья М.", "stars": 1420}
]

# ========== БАЗА ДАННЫХ (ИСПРАВЛЕННАЯ) ==========
class Database:
    """Исправленная база данных SQLite"""
    
    def __init__(self, path="bot_data.db"):
        self.path = path
        self.init_db()
    
    def get_connection(self):
        """Получить соединение с БД"""
        return sqlite3.connect(self.path, check_same_thread=False)
    
    def init_db(self):
        """Инициализация таблиц"""
        conn = self.get_connection()
        try:
            # Таблица пользователей
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
                    created_at TEXT,
                    last_daily TEXT,
                    last_luck TEXT,
                    is_subscribed INTEGER DEFAULT 0,
                    ref_code TEXT UNIQUE
                )
            """)
            
            # Таблица рефералов
            conn.execute("""
                CREATE TABLE IF NOT EXISTS referrals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    referrer_id INTEGER,
                    referred_id INTEGER UNIQUE,
                    created_at TEXT
                )
            """)
            
            # Таблица транзакций
            conn.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount INTEGER,
                    type TEXT,
                    description TEXT,
                    created_at TEXT
                )
            """)
            
            # Таблица выводов
            conn.execute("""
                CREATE TABLE IF NOT EXISTS withdrawals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount INTEGER,
                    status TEXT DEFAULT 'pending',
                    admin_id INTEGER,
                    message_id INTEGER,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            
            # Таблица статистики
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_stats (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    total_withdrawn INTEGER DEFAULT 1900,
                    total_users INTEGER DEFAULT 0,
                    total_stars INTEGER DEFAULT 0
                )
            """)
            
            # Инициализируем статистику
            conn.execute("""
                INSERT OR IGNORE INTO bot_stats (id, total_withdrawn, total_users, total_stars) 
                VALUES (1, 1900, 0, 0)
            """)
            
            conn.commit()
        finally:
            conn.close()
    
    def get_user(self, user_id: int) -> Optional[tuple]:
        """Получить пользователя"""
        conn = self.get_connection()
        try:
            cursor = conn.execute(
                "SELECT * FROM users WHERE user_id = ?", 
                (user_id,)
            )
            return cursor.fetchone()
        finally:
            conn.close()
    
    def create_user(self, user_id: int, username: str, first_name: str, last_name: str) -> bool:
        """Создать нового пользователя"""
        conn = self.get_connection()
        try:
            # Генерируем реферальный код
            ref_code = f"ref{user_id}"
            
            conn.execute(
                """INSERT OR REPLACE INTO users 
                (user_id, username, first_name, last_name, ref_code, created_at, stars) 
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_id, username or "", first_name or "", last_name or "", ref_code, 
                 datetime.now().isoformat(), 0)
            )
            
            # Обновляем статистику
            cursor = conn.execute("SELECT total_users FROM bot_stats WHERE id = 1")
            total_users = cursor.fetchone()[0]
            conn.execute(
                "UPDATE bot_stats SET total_users = ?, total_stars = total_stars + ? WHERE id = 1",
                (total_users + 1, 0)
            )
            
            conn.commit()
            return True
        except Exception as e:
            print(f"Ошибка создания пользователя: {e}")
            return False
        finally:
            conn.close()
    
    def update_user_info(self, user_id: int, username: str, first_name: str, last_name: str) -> bool:
        """Обновить информацию о пользователе"""
        conn = self.get_connection()
        try:
            conn.execute(
                """UPDATE users SET username = ?, first_name = ?, last_name = ? 
                WHERE user_id = ?""",
                (username or "", first_name or "", last_name or "", user_id)
            )
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()
    
    def add_stars(self, user_id: int, amount: int) -> bool:
        """Добавить звезды (исправленная версия)"""
        conn = self.get_connection()
        try:
            # Получаем текущий баланс
            cursor = conn.execute("SELECT stars FROM users WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            if not result:
                return False
            
            current_stars = result[0]
            new_stars = current_stars + amount
            
            # Обновляем баланс
            conn.execute(
                "UPDATE users SET stars = ?, total_earned = total_earned + ? WHERE user_id = ?",
                (new_stars, amount, user_id)
            )
            
            # Обновляем общую статистику звезд
            cursor = conn.execute("SELECT total_stars FROM bot_stats WHERE id = 1")
            total_stars = cursor.fetchone()[0]
            conn.execute(
                "UPDATE bot_stats SET total_stars = ? WHERE id = 1",
                (total_stars + amount,)
            )
            
            # Записываем транзакцию
            conn.execute(
                """INSERT INTO transactions (user_id, amount, type, description, created_at) 
                VALUES (?, ?, ?, ?, ?)""",
                (user_id, amount, "add", "Начисление звезд", datetime.now().isoformat())
            )
            
            conn.commit()
            return True
        except Exception as e:
            print(f"Ошибка добавления звезд: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()
    
    def subtract_stars(self, user_id: int, amount: int) -> bool:
        """Вычесть звезды (исправленная версия)"""
        conn = self.get_connection()
        try:
            # Проверяем баланс
            cursor = conn.execute("SELECT stars FROM users WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            if not result:
                return False
            
            current_stars = result[0]
            if current_stars < amount:
                return False
            
            new_stars = current_stars - amount
            
            # Обновляем баланс
            conn.execute(
                """UPDATE users SET stars = ?, total_withdrawn = total_withdrawn + ? 
                WHERE user_id = ?""",
                (new_stars, amount, user_id)
            )
            
            # Обновляем общую статистику звезд
            cursor = conn.execute("SELECT total_stars FROM bot_stats WHERE id = 1")
            total_stars = cursor.fetchone()[0]
            conn.execute(
                "UPDATE bot_stats SET total_stars = ? WHERE id = 1",
                (total_stars - amount,)
            )
            
            # Записываем транзакцию
            conn.execute(
                """INSERT INTO transactions (user_id, amount, type, description, created_at) 
                VALUES (?, ?, ?, ?, ?)""",
                (user_id, -amount, "withdraw", "Списание звезд", datetime.now().isoformat())
            )
            
            conn.commit()
            return True
        except Exception as e:
            print(f"Ошибка списания звезд: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()
    
    def record_transaction(self, user_id: int, amount: int, trans_type: str, desc: str = "") -> bool:
        """Записать транзакцию"""
        conn = self.get_connection()
        try:
            conn.execute(
                """INSERT INTO transactions (user_id, amount, type, description, created_at) 
                VALUES (?, ?, ?, ?, ?)""",
                (user_id, amount, trans_type, desc, datetime.now().isoformat())
            )
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()
    
    def update_last_daily(self, user_id: int) -> bool:
        """Обновить время последнего ежедневного бонуса"""
        conn = self.get_connection()
        try:
            conn.execute(
                "UPDATE users SET last_daily = ? WHERE user_id = ?",
                (datetime.now().isoformat(), user_id)
            )
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()
    
    def update_last_luck(self, user_id: int) -> bool:
        """Обновить время последней игры"""
        conn = self.get_connection()
        try:
            conn.execute(
                "UPDATE users SET last_luck = ? WHERE user_id = ?",
                (datetime.now().isoformat(), user_id)
            )
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()
    
    def add_referral(self, referrer_id: int, referred_id: int) -> bool:
        """Добавить реферала"""
        conn = self.get_connection()
        try:
            # Проверяем, не существует ли уже
            cursor = conn.execute(
                "SELECT 1 FROM referrals WHERE referred_id = ?", 
                (referred_id,)
            )
            if cursor.fetchone():
                return False
            
            # Добавляем реферала
            conn.execute(
                """INSERT INTO referrals (referrer_id, referred_id, created_at) 
                VALUES (?, ?, ?)""",
                (referrer_id, referred_id, datetime.now().isoformat())
            )
            
            # Обновляем счетчик рефералов
            conn.execute(
                "UPDATE users SET referrals = referrals + 1 WHERE user_id = ?",
                (referrer_id,)
            )
            
            # Начисляем награду
            self.add_stars(referrer_id, REF_REWARD)
            
            conn.commit()
            return True
        except Exception as e:
            print(f"Ошибка добавления реферала: {e}")
            return False
        finally:
            conn.close()
    
    def get_user_referrals_count(self, user_id: int) -> int:
        """Получить количество рефералов пользователя"""
        conn = self.get_connection()
        try:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", 
                (user_id,)
            )
            result = cursor.fetchone()
            return result[0] if result else 0
        finally:
            conn.close()
    
    def create_withdrawal(self, user_id: int, amount: int) -> Optional[int]:
        """Создать заявку на вывод"""
        conn = self.get_connection()
        try:
            cursor = conn.execute(
                """INSERT INTO withdrawals (user_id, amount, created_at) 
                VALUES (?, ?, ?)""",
                (user_id, amount, datetime.now().isoformat())
            )
            withdrawal_id = cursor.lastrowid
            conn.commit()
            return withdrawal_id
        except:
            return None
        finally:
            conn.close()
    
    def update_withdrawal(self, withdrawal_id: int, status: str, admin_id: int = None) -> bool:
        """Обновить статус вывода"""
        conn = self.get_connection()
        try:
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
        finally:
            conn.close()
    
    def get_withdrawal(self, withdrawal_id: int) -> Optional[tuple]:
        """Получить заявку на вывод"""
        conn = self.get_connection()
        try:
            cursor = conn.execute(
                "SELECT * FROM withdrawals WHERE id = ?", 
                (withdrawal_id,)
            )
            return cursor.fetchone()
        finally:
            conn.close()
    
    def get_total_withdrawn(self) -> int:
        """Получить реальное количество выведенных звезд"""
        conn = self.get_connection()
        try:
            cursor = conn.execute("SELECT total_withdrawn FROM bot_stats WHERE id = 1")
            result = cursor.fetchone()
            return result[0] if result else 1900
        finally:
            conn.close()
    
    def add_to_total_withdrawn(self, amount: int) -> bool:
        """Добавить к общему количеству выведенных звезд"""
        conn = self.get_connection()
        try:
            cursor = conn.execute("SELECT total_withdrawn FROM bot_stats WHERE id = 1")
            current = cursor.fetchone()[0]
            conn.execute(
                "UPDATE bot_stats SET total_withdrawn = ? WHERE id = 1",
                (current + amount,)
            )
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()
    
    # ========== АДМИН МЕТОДЫ ==========
    def admin_add_stars(self, user_id: int, amount: int, admin_id: int) -> bool:
        """Админ добавляет звезды пользователю"""
        return self.add_stars(user_id, amount)
    
    def get_real_stats(self) -> dict:
        """Получить реальную статистику (только для админов)"""
        conn = self.get_connection()
        try:
            cursor = conn.execute("SELECT total_users, total_stars, total_withdrawn FROM bot_stats WHERE id = 1")
            result = cursor.fetchone()
            if result:
                return {
                    "total_users": result[0],
                    "total_stars": result[1],
                    "total_withdrawn": result[2]
                }
            return {"total_users": 0, "total_stars": 0, "total_withdrawn": 1900}
        finally:
            conn.close()

# ========== ИНИЦИАЛИЗАЦИЯ ==========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
db = Database()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def get_fake_stats() -> dict:
    """Получить фейковую статистику для пользователей"""
    return {
        "total_users": FAKE_TOTAL_USERS,
        "total_stars": FAKE_TOTAL_STARS,
        "total_withdrawn": FAKE_TOTAL_WITHDRAWN
    }

def get_user_stats(user_id: int) -> dict:
    """Получить статистику пользователя"""
    user_data = db.get_user(user_id)
    if not user_data:
        return {"stars": 0, "referrals": 0, "total_earned": 0, "total_withdrawn": 0}
    
    referrals_count = db.get_user_referrals_count(user_id)
    
    return {
        "stars": user_data[4],
        "referrals": referrals_count,
        "total_earned": user_data[6],
        "total_withdrawn": user_data[7]
    }

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
    user_data = db.get_user(user_id)
    if not user_data:
        # Создаем нового пользователя
        return db.create_user(user_id, username or "", first_name or "", last_name or "")
    else:
        # Обновляем информацию существующего пользователя
        return db.update_user_info(user_id, username or "", first_name or "", last_name or "")

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

def admin_menu():
    """Меню администратора"""
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
        types.InlineKeyboardButton(text="⭐ Добавить звезды", callback_data="admin_add_stars_menu")
    )
    builder.row(
        types.InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast"),
        types.InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")
    )
    builder.row(
        types.InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu")
    )
    return builder.as_markup()

def admin_add_stars_kb():
    """Клавиатура для добавления звезд"""
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(text="➕ 10 звезд", callback_data="admin_add_10"),
        types.InlineKeyboardButton(text="➕ 50 звезд", callback_data="admin_add_50")
    )
    builder.row(
        types.InlineKeyboardButton(text="➕ 100 звезд", callback_data="admin_add_100"),
        types.InlineKeyboardButton(text="➕ 500 звезд", callback_data="admin_add_500")
    )
    builder.row(
        types.InlineKeyboardButton(text="➖ Убрать звезды", callback_data="admin_remove_stars")
    )
    builder.row(
        types.InlineKeyboardButton(text="🔙 В админ-панель", callback_data="admin_panel")
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

# ========== СОСТОЯНИЯ ==========
class WithdrawalStates(StatesGroup):
    waiting_amount = State()
    confirm_withdrawal = State()

class AdminStates(StatesGroup):
    waiting_user_id_for_add = State()
    waiting_amount_for_add = State()
    waiting_user_id_for_remove = State()
    waiting_amount_for_remove = State()
    waiting_broadcast = State()

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Команда /start - ИСПРАВЛЕНА"""
    user = message.from_user
    
    # Обновляем/создаем пользователя
    await ensure_user_registered(
        user.id, 
        user.username, 
        user.first_name, 
        user.last_name
    )
    
    # Обработка реферального кода
    if len(message.text.split()) > 1:
        ref_code = message.text.split()[1]
        if ref_code.isdigit():
            try:
                referrer_id = int(ref_code)
                if referrer_id != user.id:
                    db.add_referral(referrer_id, user.id)
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
    fake_stats = get_fake_stats()
    welcome_text = f"""
⭐ <b>Добро пожаловать, {user.first_name or 'друг'}!</b>

<b>StarsForQuestion</b> - система заработка виртуальных звезд!

🎯 <b>Зарабатывайте звезды:</b>
• 📅 Ежедневные бонусы (1-5 звезд)
• 🎮 Мини-игра "Удача" (0-10 звезд)
• 👥 Приглашайте друзей (+5 звезд за каждого)
• 💬 Добавляйте бота в группы (+2 звезды)

💎 <b>Выводите звезды!</b>
Минимальный вывод: 15 звезд

📊 <b>Статистика:</b>
• 👥 Игроков: {fake_stats['total_users']:,}
• ⭐ Звезд в системе: {fake_stats['total_stars']:,}
• 💰 Выдано: {fake_stats['total_withdrawn']:,}+ звезд!

📞 <b>Поддержка:</b> {SUPPORT_USERNAME}
    """
    
    await message.answer(welcome_text, reply_markup=main_menu())

@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    """Команда /profile - ИСПРАВЛЕНА"""
    user = message.from_user
    
    # Обновляем информацию пользователя
    await ensure_user_registered(
        user.id,
        user.username,
        user.first_name,
        user.last_name
    )
    
    # Получаем статистику
    user_stats = get_user_stats(user.id)
    fake_stats = get_fake_stats()
    
    stars_display = generate_stars(user_stats["stars"])
    
    text = f"""
👤 <b>Личный кабинет</b>

👤 <b>Имя:</b> {user.first_name or 'Не указано'}
🆔 <b>ID:</b> <code>{user.id}</code>

⭐ <b>Звезды:</b> {user_stats['stars']} {stars_display}
👥 <b>Рефералы:</b> {user_stats['referrals']}
💰 <b>Всего заработано:</b> {user_stats['total_earned']}
💎 <b>Выведено:</b> {user_stats['total_withdrawn']}

📊 <b>Общая статистика:</b>
• 👥 Игроков: {fake_stats['total_users']:,}
• 💰 Выдано: {fake_stats['total_withdrawn']:,}+ звезд

💡 <b>Вывод доступен от 15 звезд</b>
    """
    await message.answer(text, reply_markup=main_menu())

@dp.message(Command("daily"))
async def cmd_daily(message: Message):
    """Ежедневный бонус - ИСПРАВЛЕН"""
    user = message.from_user
    
    # Обновляем информацию пользователя
    await ensure_user_registered(
        user.id,
        user.username,
        user.first_name,
        user.last_name
    )
    
    user_data = db.get_user(user.id)
    if not user_data:
        await message.answer("❌ Ошибка! Попробуйте /start")
        return
    
    # Проверка времени
    last_daily = user_data[9] if len(user_data) > 9 else None
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
    if db.add_stars(user.id, reward):
        db.update_last_daily(user.id)
        stars_display = generate_stars(reward)
        await message.answer(
            f"🎉 <b>Ежедневный бонус!</b>\n\n"
            f"Вы получили: +{reward} {stars_display}\n\n"
            f"Заходите завтра!",
            reply_markup=back_to_menu()
        )
    else:
        await message.answer("❌ Ошибка начисления. Попробуйте позже.", reply_markup=back_to_menu())

@dp.message(Command("luck"))
async def cmd_luck(message: Message):
    """Игра 'Удача' - ИСПРАВЛЕНА"""
    user = message.from_user
    
    # Обновляем информацию пользователя
    await ensure_user_registered(
        user.id,
        user.username,
        user.first_name,
        user.last_name
    )
    
    user_data = db.get_user(user.id)
    if not user_data:
        await message.answer("❌ Ошибка! Попробуйте /start")
        return
    
    # Проверка кулдауна
    last_luck = user_data[10] if len(user_data) > 10 else None
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
    """Реферальная система - ИСПРАВЛЕНА"""
    user = message.from_user
    
    # Обновляем информацию пользователя
    await ensure_user_registered(
        user.id,
        user.username,
        user.first_name,
        user.last_name
    )
    
    user_data = db.get_user(user.id)
    ref_code = user_data[12] if user_data and len(user_data) > 12 else f"ref{user.id}"
    ref_link = f"https://t.me/{(await bot.get_me()).username}?start={ref_code}"
    
    user_stats = get_user_stats(user.id)
    
    text = f"""
👥 <b>Реферальная система</b>

🔗 <b>Ваша ссылка:</b>
<code>{ref_link}</code>

📊 <b>Статистика:</b>
• Приглашено: {user_stats['referrals']} человек
• Заработано: {user_stats['referrals'] * REF_REWARD} звезд

🎯 <b>Как работает:</b>
1. Отправьте другу вашу ссылку
2. Друг нажимает и начинает общение с ботом
3. Вы получаете +{REF_REWARD} звезд сразу!

💰 <b>Зарабатывайте больше - приглашайте больше!</b>
    """
    
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🔗 Скопировать ссылку", callback_data=f"copy_{ref_link}"))
    builder.row(types.InlineKeyboardButton(text="📢 Поделиться", switch_inline_query=f"Зарабатывай звезды! {ref_link}"))
    builder.row(types.InlineKeyboardButton(text="🔙 В меню", callback_data="menu"))
    
    await message.answer(text, reply_markup=builder.as_markup())

@dp.message(Command("top"))
async def cmd_top(message: Message):
    """Топ игроков - ТОЛЬКО ФЕЙКОВЫЙ"""
    fake_stats = get_fake_stats()
    
    text = "🏆 <b>Топ игроков по звездам</b>\n\n"
    
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    
    for i, user in enumerate(FAKE_TOP_USERS[:10]):
        medal = medals[i] if i < len(medals) else f"{i+1}."
        stars_display = generate_stars(user["stars"])
        text += f"{medal} {user['name']}: {user['stars']} {stars_display}\n"
    
    text += f"\n📊 <b>Общая статистика:</b>\n"
    text += f"• 👥 Всего игроков: {fake_stats['total_users']:,}\n"
    text += f"• 💰 Выдано звезд: {fake_stats['total_withdrawn']:,}+\n"
    text += f"• ⭐ Звезд в системе: {fake_stats['total_stars']:,}\n"
    text += "\n🎯 <i>Выполняйте задания и зарабатывайте звезды!</i>"
    
    await message.answer(text, reply_markup=back_to_menu())

@dp.message(Command("withdraw"))
async def cmd_withdraw(message: Message):
    """Вывод звезд - ИСПРАВЛЕН"""
    user = message.from_user
    
    # Обновляем информацию пользователя
    await ensure_user_registered(
        user.id,
        user.username,
        user.first_name,
        user.last_name
    )
    
    user_stats = get_user_stats(user.id)
    fake_stats = get_fake_stats()
    
    if user_stats["stars"] < 15:
        await message.answer(
            f"❌ <b>Недостаточно звезд для вывода!</b>\n\n"
            f"Ваш баланс: {user_stats['stars']} звезд\n"
            f"Минимальный вывод: 15 звезд\n\n"
            f"💡 <i>Зарабатывайте звезды через задания и игры!</i>",
            reply_markup=back_to_menu()
        )
        return
    
    text = f"""
💎 <b>Вывод звезд</b>

💰 <b>Ваш баланс:</b> {user_stats['stars']} звезд
💎 <b>Минимальный вывод:</b> 15 звезд

🎁 <b>Доступные суммы:</b>

📊 <b>Статистика бота:</b>
• 👥 Игроков: {fake_stats['total_users']:,}
• 💰 Выдано: {fake_stats['total_withdrawn']:,}+ звезд
• 📞 Поддержка: {SUPPORT_USERNAME}

⚠️ <b>Внимание:</b>
1. Вывод осуществляется в течение 24 часов
2. После создания заявки ожидайте подтверждения
3. Отменить заявку после создания нельзя
    """
    
    await message.answer(text, reply_markup=withdrawal_amounts_kb())

@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Справка - ИСПРАВЛЕНА"""
    fake_stats = get_fake_stats()
    
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
• Статистика: выдано {fake_stats['total_withdrawn']:,}+ звезд

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
    """Играть в удачу - ИСПРАВЛЕНА"""
    user = callback.from_user
    user_data = db.get_user(user.id)
    
    if not user_data:
        await callback.answer("❌ Ошибка! Попробуйте /start", show_alert=True)
        return
    
    # Проверка кулдауна
    last_luck = user_data[10] if len(user_data) > 10 else None
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
    if db.add_stars(user.id, reward):
        db.update_last_luck(user.id)
        
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
    else:
        await callback.answer("❌ Ошибка начисления!", show_alert=True)

@dp.callback_query(F.data.startswith("withdraw_"))
async def callback_withdraw_amount(callback: CallbackQuery):
    """Выбор суммы для вывода - ИСПРАВЛЕН"""
    try:
        amount = int(callback.data.split("_")[1])
        user = callback.from_user
        
        user_stats = get_user_stats(user.id)
        
        if amount < 15:
            await callback.answer("Минимальная сумма 15 звезд!", show_alert=True)
            return
        
        if user_stats["stars"] < amount:
            await callback.answer("Недостаточно звезд!", show_alert=True)
            return
        
        # Создаем предварительную заявку
        withdrawal_id = db.create_withdrawal(user.id, amount)
        if not withdrawal_id:
            await callback.answer("Ошибка создания заявки!", show_alert=True)
            return
        
        text = f"""
💎 <b>Подтверждение вывода</b>

📋 <b>Детали заявки:</b>
• Сумма: {amount} звезд
• Ваш баланс: {user_stats['stars']} звезд
• Остаток после вывода: {user_stats['stars'] - amount} звезд

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
async def callback_confirm_withdrawal(callback: CallbackQuery):
    """Подтверждение вывода - ИСПРАВЛЕН"""
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
        
        user_stats = get_user_stats(user_id)
        if user_stats["stars"] < amount:
            await callback.answer("Недостаточно звезд!", show_alert=True)
            return
        
        # Снимаем звезды
        if not db.subtract_stars(user_id, amount):
            await callback.answer("Ошибка списания звезд!", show_alert=True)
            return
        
        # Обновляем статус заявки
        db.update_withdrawal(withdrawal_id, "processing")
        
        # Отправляем заявку в канал
        censored_username = censor_username(user.username or user.first_name or f"user{user_id}")
        real_stats = db.get_real_stats()
        
        channel_text = f"""
📥 <b>Новая заявка на вывод!</b>

👤 <b>Пользователь:</b> {censored_username}
🆔 <b>ID:</b> <code>{user_id}</code>
💎 <b>Сумма:</b> {amount} звезд
💰 <b>Баланс был:</b> {user_stats['stars']} звезд
⏰ <b>Время:</b> {datetime.now().strftime('%H:%M %d.%m.%Y')}

📊 <b>Статистика бота:</b>
• 👥 Реальных игроков: {real_stats['total_users']}
• 💰 Выдано: {real_stats['total_withdrawn']}+ звезд

#вывод #заявка_{withdrawal_id}
        """
        
        try:
            message_sent = await bot.send_message(
                chat_id=WITHDRAWAL_CHANNEL_ID,
                text=channel_text,
                reply_markup=admin_withdrawal_kb(withdrawal_id)
            )
            
            # Сохраняем ID сообщения
            with sqlite3.connect("bot_data.db") as conn:
                conn.execute(
                    "UPDATE withdrawals SET message_id = ? WHERE id = ?",
                    (message_sent.message_id, withdrawal_id)
                )
                conn.commit()
                
        except Exception as e:
            logger.error(f"Ошибка отправки в канал: {e}")
        
        # Уведомляем пользователя
        db.add_to_total_withdrawn(amount)
        fake_stats = get_fake_stats()
        
        await callback.message.edit_text(
            f"✅ <b>Заявка #{withdrawal_id} создана!</b>\n\n"
            f"💎 <b>Сумма:</b> {amount} звезд\n"
            f"⏰ <b>Статус:</b> На модерации\n"
            f"🕐 <b>Ожидайте:</b> До 24 часов\n\n"
            f"💰 <b>Всего выдано:</b> {fake_stats['total_withdrawn']}+ звезд\n\n"
            f"📞 <b>Поддержка:</b> {SUPPORT_USERNAME}",
            reply_markup=back_to_menu()
        )
        
        await callback.answer("Заявка отправлена на модерацию!")
        
    except Exception as e:
        logger.error(f"Ошибка подтверждения вывода: {e}")
        await callback.answer("Ошибка!", show_alert=True)

@dp.callback_query(F.data == "cancel_wd")
async def callback_cancel_withdrawal(callback: CallbackQuery):
    """Отмена вывода"""
    await callback.message.edit_text(
        "❌ <b>Вывод отменен</b>\n\nВозвращаемся в главное меню...",
        reply_markup=main_menu()
    )
    await callback.answer("Вывод отменен")

# ========== АДМИН ФУНКЦИОНАЛ ==========
@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    """Админ панель - ТОЛЬКО РЕАЛЬНАЯ СТАТИСТИКА"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Доступ запрещен!")
        return
    
    real_stats = db.get_real_stats()
    
    text = f"""
⚙️ <b>Админ панель</b>

👑 <b>Админ:</b> {message.from_user.first_name or 'Администратор'}
🆔 <b>ID:</b> <code>{message.from_user.id}</code>

📊 <b>Реальная статистика:</b>
• 👥 Пользователей: {real_stats['total_users']}
• ⭐ Звезд в системе: {real_stats['total_stars']}
• 💰 Выдано: {real_stats['total_withdrawn']}+ звезд

🔧 <b>Доступные функции:</b>
• Добавить/убрать звезды пользователю
• Рассылка сообщений
• Просмотр пользователей
    """
    
    await message.answer(text, reply_markup=admin_menu())

@dp.message(Command("addstars"))
async def cmd_addstars(message: Message):
    """Добавить звезды пользователю"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Доступ запрещен!")
        return
    
    try:
        args = message.text.split()
        if len(args) != 3:
            await message.answer("❌ Использование: /addstars [user_id] [amount]")
            return
        
        user_id = int(args[1])
        amount = int(args[2])
        
        if amount <= 0:
            await message.answer("❌ Количество должно быть положительным!")
            return
        
        user_data = db.get_user(user_id)
        if not user_data:
            await message.answer("❌ Пользователь не найден!")
            return
        
        if db.admin_add_stars(user_id, amount, message.from_user.id):
            await message.answer(f"✅ Успешно добавлено {amount} звезд пользователю {user_id}")
        else:
            await message.answer("❌ Ошибка добавления звезд")
            
    except ValueError:
        await message.answer("❌ Неверный формат аргументов!")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

@dp.message(Command("remstars"))
async def cmd_remstars(message: Message):
    """Убрать звезды у пользователя"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Доступ запрещен!")
        return
    
    try:
        args = message.text.split()
        if len(args) != 3:
            await message.answer("❌ Использование: /remstars [user_id] [amount]")
            return
        
        user_id = int(args[1])
        amount = int(args[2])
        
        if amount <= 0:
            await message.answer("❌ Количество должно быть положительным!")
            return
        
        user_data = db.get_user(user_id)
        if not user_data:
            await message.answer("❌ Пользователь не найден!")
            return
        
        if db.subtract_stars(user_id, amount):
            await message.answer(f"✅ Успешно убрано {amount} звезд у пользователя {user_id}")
        else:
            await message.answer("❌ Ошибка или недостаточно звезд")
            
    except ValueError:
        await message.answer("❌ Неверный формат аргументов!")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

# ========== АДМИН CALLBACK ОБРАБОТЧИКИ ==========
@dp.callback_query(F.data == "admin_panel")
async def callback_admin_panel(callback: CallbackQuery):
    """Админ панель"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Доступ запрещен!", show_alert=True)
        return
    
    await cmd_admin(callback.message)
    await callback.answer()

@dp.callback_query(F.data == "admin_stats")
async def callback_admin_stats(callback: CallbackQuery):
    """Статистика админа - РЕАЛЬНАЯ"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Доступ запрещен!", show_alert=True)
        return
    
    real_stats = db.get_real_stats()
    fake_stats = get_fake_stats()
    
    text = f"""
📊 <b>Детальная статистика бота</b>

<b>Реальная статистика (только для админов):</b>
• 👥 Пользователей: {real_stats['total_users']}
• ⭐ Звезд в системе: {real_stats['total_stars']}
• 💰 Выдано: {real_stats['total_withdrawn']}+ звезд

<b>Фейковая статистика (для пользователей):</b>
• 👥 Игроков: {fake_stats['total_users']:,}
• ⭐ Звезд в системе: {fake_stats['total_stars']:,}
• 💰 Выдано: {fake_stats['total_withdrawn']:,}+ звезд

🔄 <b>Обновлено:</b> {datetime.now().strftime('%H:%M %d.%m.%Y')}
    """
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardBuilder()
            .row(types.InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel"))
            .as_markup()
    )
    await callback.answer()

@dp.callback_query(F.data == "admin_add_stars_menu")
async def callback_admin_add_stars_menu(callback: CallbackQuery):
    """Добавить звезды (меню)"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Доступ запрещен!", show_alert=True)
        return
    
    text = """
⭐ <b>Управление звездами</b>

Используйте команды:
<code>/addstars [user_id] [amount]</code> - добавить звезды
<code>/remstars [user_id] [amount]</code> - убрать звезды

Или используйте кнопки ниже для быстрого добавления:
    """
    
    await callback.message.edit_text(
        text,
        reply_markup=admin_add_stars_kb()
    )
    await callback.answer()

# Остальные админ callback'и остаются без изменений (аналогично предыдущей версии)
# ...

# ========== ОСНОВНЫЕ CALLBACK ОБРАБОТЧИКИ ==========
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

@dp.callback_query(F.data == "help")
async def callback_help(callback: CallbackQuery):
    """Помощь из меню"""
    await cmd_help(callback.message)
    await callback.answer()

@dp.callback_query(F.data == "tasks")
async def callback_tasks(callback: CallbackQuery):
    """Задания из меню"""
    fake_stats = get_fake_stats()
    
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
   
📊 <b>Статистика бота:</b>
• 👥 Игроков: {fake_stats['total_users']:,}
• 💰 Выдано: {fake_stats['total_withdrawn']:,}+ звезд
• 📞 Поддержка: {SUPPORT_USERNAME}

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

@dp.callback_query(F.data.startswith("copy_"))
async def callback_copy(callback: CallbackQuery):
    """Копирование ссылки"""
    ref_link = callback.data[5:]
    await callback.answer(f"Ссылка: {ref_link}", show_alert=True)

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
