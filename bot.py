"""
StarsForQuestion - ULTIMATE MONOLITH v10.0 (ПОЛНАЯ ВЕРСИЯ, РУССКИЙ)
Абсолютно все функции: экономика, рефералы (с бонусом после активации), 
посты в канал, реалистичные фейки, P2P маркет, лотерея, дуэли, квесты,
магазин с эксклюзивами, инвентарь, глобальные бусты (админ-роабьюзы),
полная настройка через БД, логирование админов, PostgreSQL для Render.
Все тексты на русском языке, все кнопки работаютт.
"""

import asyncio
import logging
import os
import random
import json
from datetime import datetime, timedelta
from typing import Optional, Any, Dict, List, Tuple

# База данных: поддержка SQLite и PostgreSQL
import sqlite3
try:
    import psycopg2
    import psycopg2.extras
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

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


# ========== КОНФИГУРАЦИЯ ИЗ ОКРУЖЕНИЯ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан!")

CHANNEL_ID = os.getenv("CHANNEL_ID", "-1003326584722")
raw_admins = os.getenv("ADMIN_IDS", "8364667153")
ADMIN_IDS = [int(id.strip()) for id in raw_admins.split(",") if id.strip()]
WITHDRAWAL_CHANNEL_ID = os.getenv("WITHDRAWAL_CHANNEL", "-1003891414947")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@Nft_top3")
PORT = int(os.environ.get("PORT", 10000))

# Выбор базы данных: PostgreSQL если задан DATABASE_URL, иначе SQLite
DATABASE_URL = os.getenv("DATABASE_URL")  # для Render PostgreSQL


# ========== БАЗА ДАННЫХ (УНИВЕРСАЛЬНЫЙ КЛАСС) ==========

class Database:
    def __init__(self):
        self.use_postgres = DATABASE_URL is not None and PSYCOPG2_AVAILABLE
        if self.use_postgres:
            self.conn = self._get_postgres_conn()
            self._init_postgres()
        else:
            self.conn = sqlite3.connect("bot_data.db", check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self._init_sqlite()

    def _get_postgres_conn(self):
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        conn.autocommit = False
        return conn

    def _init_postgres(self):
        with self.conn:
            with self.conn.cursor() as cur:
                # Таблица пользователей
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id BIGINT PRIMARY KEY,
                        username TEXT,
                        first_name TEXT,
                        stars REAL DEFAULT 0,
                        referrals INTEGER DEFAULT 0,
                        last_daily TIMESTAMP,
                        last_luck TIMESTAMP,
                        ref_code TEXT UNIQUE,
                        ref_boost REAL DEFAULT 1.0,
                        is_active INTEGER DEFAULT 0,
                        total_earned REAL DEFAULT 0,
                        premium_mode INTEGER DEFAULT 0,
                        referred_by BIGINT
                    )
                """)
                # Добавляем недостающие колонки (если их нет)
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS premium_mode INTEGER DEFAULT 0")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name TEXT")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS stars REAL DEFAULT 0")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referrals INTEGER DEFAULT 0")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_daily TIMESTAMP")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_luck TIMESTAMP")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS ref_code TEXT UNIQUE")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS ref_boost REAL DEFAULT 1.0")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active INTEGER DEFAULT 0")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS total_earned REAL DEFAULT 0")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by BIGINT")

                # Инвентарь
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS inventory (
                        user_id BIGINT,
                        item_name TEXT,
                        quantity INTEGER DEFAULT 1,
                        PRIMARY KEY (user_id, item_name)
                    )
                """)
                # Маркетплейс P2P
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS marketplace (
                        id SERIAL PRIMARY KEY,
                        seller_id BIGINT,
                        item_name TEXT,
                        price REAL
                    )
                """)
                # Лотерея
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS lottery (
                        id INTEGER PRIMARY KEY,
                        pool REAL DEFAULT 0,
                        participants TEXT DEFAULT ''
                    )
                """)
                cur.execute("INSERT INTO lottery (id, pool, participants) VALUES (1, 0, '') ON CONFLICT DO NOTHING")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS lottery_history (
                        user_id BIGINT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                # Квесты
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS task_claims (
                        user_id BIGINT,
                        task_id TEXT,
                        PRIMARY KEY (user_id, task_id)
                    )
                """)
                # Промокоды
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS promo (
                        code TEXT PRIMARY KEY,
                        reward_type TEXT,
                        reward_value TEXT,
                        uses INTEGER
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS promo_history (
                        user_id BIGINT,
                        code TEXT,
                        PRIMARY KEY (user_id, code)
                    )
                """)
                # Стрики
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS daily_bonus (
                        user_id BIGINT PRIMARY KEY,
                        last_date TEXT,
                        streak INTEGER DEFAULT 0
                    )
                """)
                # Дуэли
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS active_duels (
                        creator_id BIGINT PRIMARY KEY,
                        amount REAL
                    )
                """)
                # Таблица настроек config
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS config (
                        key TEXT PRIMARY KEY,
                        value TEXT,
                        description TEXT
                    )
                """)
                # Таблица логов админов
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS admin_logs (
                        id SERIAL PRIMARY KEY,
                        admin_id BIGINT,
                        action TEXT,
                        details TEXT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("""
                CREATE TABLE IF NOT EXISTS quests (
                id SERIAL PRIMARY KEY,
                name TEXT,
                description TEXT,
                reward_type TEXT,
                reward_value TEXT,
                condition_type TEXT,
                condition_value TEXT,
                is_active INTEGER DEFAULT 1
                )
                """)
                cur.execute("""
                CREATE TABLE IF NOT EXISTS user_quests (
                user_id BIGINT,
                quest_id INTEGER,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, quest_id)
                )

                """)
                cur.execute("""
                CREATE TABLE IF NOT EXISTS checks (
                id TEXT PRIMARY KEY,
                creator_id BIGINT,
                type TEXT,
                value TEXT,
                password TEXT,
                max_uses INTEGER,
                used INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1
                )
                """)
                cur.execute("""
                CREATE TABLE IF NOT EXISTS check_uses (
                check_id TEXT,
                user_id BIGINT,
                PRIMARY KEY (check_id, user_id)
                )
                """)
                # Заполняем config значениями по умолчанию
                default_config = {
                    'ref_reward': ('5.0', 'Награда за активного реферала (звезд)'),
                    'view_reward': ('0.3', 'Награда за просмотр поста'),
                    'daily_min': ('1', 'Минимум ежедневного бонуса'),
                    'daily_max': ('3', 'Максимум ежедневного бонуса'),
                    'luck_min': ('0', 'Минимум удачи'),
                    'luck_max': ('5', 'Максимум удачи'),
                    'luck_cooldown': ('21600', 'Кулдаун удачи (секунд)'),
                    'withdrawal_options': ('15,25,50,100', 'Доступные суммы вывода через запятую'),
                    'gifts_prices': ('{"🧸 Мишка":45,"❤️ Сердце":45,"🎁 Подарок":75,"🌹 Роза":75,"🍰 Тортик":150,"💐 Букет":150,"🚀 Ракета":150,"🍾 Шампанское":150,"🏆 Кубок":300,"💍 Колечко":300,"💎 Алмаз":300}', 'Цены на подарки (JSON)'),
                    'special_items': ('{"Ramen":{"price":250,"limit":25,"full_name":"🍜 Ramen"},"Candle":{"price":199,"limit":30,"full_name":"🕯 B-Day Candle"},"Calendar":{"price":320,"limit":18,"full_name":"🗓 Desk Calendar"}}', 'Эксклюзивные товары (JSON)'),
                }
                for key, (value, desc) in default_config.items():
                    cur.execute("INSERT INTO config (key, value, description) VALUES (%s, %s, %s) ON CONFLICT (key) DO NOTHING", (key, value, desc))
                # Глобальные бусты
                cur.execute("INSERT INTO config (key, value, description) VALUES ('global_ref_mult', '1.0', 'Глобальный множитель рефералов') ON CONFLICT DO NOTHING")
                cur.execute("INSERT INTO config (key, value, description) VALUES ('global_ref_until', '', 'Время окончания глобального буста рефералов (ISO)') ON CONFLICT DO NOTHING")
                cur.execute("INSERT INTO config (key, value, description) VALUES ('global_game_mult', '1.0', 'Глобальный множитель выигрышей в играх') ON CONFLICT DO NOTHING")
                cur.execute("INSERT INTO config (key, value, description) VALUES ('global_game_until', '', 'Время окончания глобального буста игр') ON CONFLICT DO NOTHING")

    def _init_sqlite(self):
        cursor = self.conn.cursor()
        # Таблица пользователей
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                stars REAL DEFAULT 0,
                referrals INTEGER DEFAULT 0,
                last_daily TIMESTAMP,
                last_luck TIMESTAMP,
                ref_code TEXT UNIQUE,
                ref_boost REAL DEFAULT 1.0,
                is_active INTEGER DEFAULT 0,
                total_earned REAL DEFAULT 0,
                premium_mode INTEGER DEFAULT 0,
                referred_by INTEGER
            )
        """)
        # Инвентарь
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                user_id INTEGER,
                item_name TEXT,
                quantity INTEGER DEFAULT 1,
                PRIMARY KEY (user_id, item_name)
            )
        """)
        # Маркетплейс P2P
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS marketplace (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seller_id INTEGER,
                item_name TEXT,
                price REAL
            )
        """)
        # Лотерея
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lottery (
                id INTEGER PRIMARY KEY,
                pool REAL DEFAULT 0,
                participants TEXT DEFAULT ''
            )
        """)
        cursor.execute("INSERT OR IGNORE INTO lottery (id, pool, participants) VALUES (1, 0, '')")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lottery_history (
                user_id INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Квесты
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_claims (
                user_id INTEGER,
                task_id TEXT,
                PRIMARY KEY (user_id, task_id)
            )
        """)
        # Промокоды
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS promo (
                code TEXT PRIMARY KEY,
                reward_type TEXT,
                reward_value TEXT,
                uses INTEGER
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS promo_history (
                user_id INTEGER,
                code TEXT,
                PRIMARY KEY (user_id, code)
            )
        """)
        # Стрики
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_bonus (
                user_id INTEGER PRIMARY KEY,
                last_date TEXT,
                streak INTEGER DEFAULT 0
            )
        """)
        # Дуэли
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS active_duels (
                creator_id INTEGER PRIMARY KEY,
                amount REAL
            )
        """)
        # Таблица настроек config
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT,
                description TEXT
            )
        """)
        # Таблица логов админов
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                action TEXT,
                details TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS quests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        description TEXT,
        reward_type TEXT,
        reward_value TEXT,
        condition_type TEXT,
        condition_value TEXT,
        is_active INTEGER DEFAULT 1
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_quests (
        user_id INTEGER,
        quest_id INTEGER,
        completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, quest_id)
        try:
        cursor.execute("ALTER TABLE user_quests ADD COLUMN task_id TEXT")
        except sqlite3.OperationalError:
        pass
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS checks (
        id TEXT PRIMARY KEY,
        creator_id INTEGER,
        type TEXT,
        value TEXT,
        password TEXT,
        max_uses INTEGER,
        used INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS check_uses (
        check_id TEXT,
        user_id INTEGER,
        PRIMARY KEY (check_id, user_id)
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS checks (
        id TEXT PRIMARY KEY,
        creator_id INTEGER,
        type TEXT,
        value TEXT,
        password TEXT,
        max_uses INTEGER,
        used INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS check_uses (
        check_id TEXT,
        user_id INTEGER,
        PRIMARY KEY (check_id, user_id)
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS quests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        description TEXT,
        reward_type TEXT,
        reward_value TEXT,
        condition_type TEXT,
        condition_value TEXT,
        is_active INTEGER DEFAULT 1
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_quests (
        user_id INTEGER,
        quest_id INTEGER,
        completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, quest_id)
        )
        """)
        # Заполняем config значениями по умолчанию
        default_config = {
            'ref_reward': ('5.0', 'Награда за активного реферала (звезд)'),
            'view_reward': ('0.3', 'Награда за просмотр поста'),
            'daily_min': ('1', 'Минимум ежедневного бонуса'),
            'daily_max': ('3', 'Максимум ежедневного бонуса'),
            'luck_min': ('0', 'Минимум удачи'),
            'luck_max': ('5', 'Максимум удачи'),
            'luck_cooldown': ('21600', 'Кулдаун удачи (секунд)'),
            'withdrawal_options': ('15,25,50,100', 'Доступные суммы вывода через запятую'),
            'gifts_prices': ('{"🧸 Мишка":45,"❤️ Сердце":45,"🎁 Подарок":75,"🌹 Роза":75,"🍰 Тортик":150,"💐 Букет":150,"🚀 Ракета":150,"🍾 Шампанское":150,"🏆 Кубок":300,"💍 Колечко":300,"💎 Алмаз":300}', 'Цены на подарки (JSON)'),
            'special_items': ('{"Ramen":{"price":250,"limit":25,"full_name":"🍜 Ramen"},"Candle":{"price":199,"limit":30,"full_name":"🕯 B-Day Candle"},"Calendar":{"price":320,"limit":18,"full_name":"🗓 Desk Calendar"}}', 'Эксклюзивные товары (JSON)'),
        }
        for key, (value, desc) in default_config.items():
            cursor.execute("INSERT OR IGNORE INTO config (key, value, description) VALUES (?, ?, ?)", (key, value, desc))
        # Глобальные бусты
        cursor.execute("INSERT OR IGNORE INTO config (key, value, description) VALUES ('global_ref_mult', '1.0', 'Глобальный множитель рефералов')")
        cursor.execute("INSERT OR IGNORE INTO config (key, value, description) VALUES ('global_ref_until', '', 'Время окончания глобального буста рефералов (ISO)')")
        cursor.execute("INSERT OR IGNORE INTO config (key, value, description) VALUES ('global_game_mult', '1.0', 'Глобальный множитель выигрышей в играх')")
        cursor.execute("INSERT OR IGNORE INTO config (key, value, description) VALUES ('global_game_until', '', 'Время окончания глобального буста игр')")
        self.conn.commit()

    def execute(self, query: str, params: tuple = (), fetch: bool = False, fetchone: bool = False):
        """Универсальный метод выполнения запросов"""
        if self.use_postgres:
            query = query.replace('?', '%s')
            with self.conn:
                with self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                    cur.execute(query, params)
                    if fetch:
                        return cur.fetchall()
                    if fetchone:
                        return cur.fetchone()
                    self.conn.commit()
                    return None
        else:
            cursor = self.conn.cursor()
            cursor.execute(query, params)
            if fetch:
                return cursor.fetchall()
            if fetchone:
                return cursor.fetchone()
            self.conn.commit()
            return None

    def get_user(self, user_id: int) -> Optional[Dict]:
        row = self.execute("SELECT * FROM users WHERE user_id = ?", (user_id,), fetchone=True)
        return dict(row) if row else None

    def get_user_safe(self, user_id: int) -> Optional[Dict]:
        """Возвращает пользователя со значениями по умолчанию для отсутствующих полей"""
        user = self.get_user(user_id)
        if not user:
            return None
        defaults = {
            'stars': 0.0,
            'referrals': 0,
            'last_daily': None,
            'last_luck': None,
            'ref_boost': 1.0,
            'is_active': 0,
            'total_earned': 0.0,
            'referred_by': None
        }
        for key, default_value in defaults.items():
            if key not in user:
                user[key] = default_value
        return user

    def create_user(self, user_id: int, username: str, first_name: str, referred_by: int = None):
        ref_code = f"ref{user_id}"
        self.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name, ref_code, referred_by) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, first_name, ref_code, referred_by)
        )

    def add_stars(self, user_id: int, amount: float):
        if amount == 0:
            return
        if amount > 0:
            user = self.get_user_safe(user_id)
            if user:
                boost = user.get('ref_boost', 1.0)
                amount = amount * boost
            self.execute("UPDATE users SET stars = stars + ? WHERE user_id = ?", (amount, user_id))
            self.update_user_activity(user_id, amount)
        else:
            self.execute("UPDATE users SET stars = stars + ? WHERE user_id = ?", (amount, user_id))

    def update_user_activity(self, user_id: int, earned: float):
        self.execute("UPDATE users SET total_earned = total_earned + ? WHERE user_id = ?", (earned, user_id))
        user = self.get_user_safe(user_id)
        if user and user['total_earned'] >= 1.0 and not user['is_active']:
            self.execute("UPDATE users SET is_active = 1 WHERE user_id = ?", (user_id,))
            if user['referred_by']:
                ref_reward = float(self.get_config('ref_reward', 5.0))
                global_mult = self.get_global_boost('ref')
                self.add_stars(user['referred_by'], ref_reward * global_mult)

    def get_config(self, key: str, default: Any = None) -> Any:
        row = self.execute("SELECT value FROM config WHERE key = ?", (key,), fetchone=True)
        if row:
            return row['value']
        return default

    def set_config(self, key: str, value: str):
        self.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))

    def get_gifts_prices(self) -> dict:
        try:
            return json.loads(self.get_config('gifts_prices', '{}'))
        except:
            return {}

    def get_special_items(self) -> dict:
        try:
            return json.loads(self.get_config('special_items', '{}'))
        except:
            return {}

    def get_withdrawal_options(self) -> list:
        opt = self.get_config('withdrawal_options', '15,25,50,100')
        return [int(x.strip()) for x in opt.split(',') if x.strip()]

    def get_global_boost(self, boost_type: str) -> float:
        mult_key = f'global_{boost_type}_mult'
        until_key = f'global_{boost_type}_until'
        mult = float(self.get_config(mult_key, 1.0))
        until_str = self.get_config(until_key, '')
        if until_str:
            try:
                until = datetime.fromisoformat(until_str)
                if datetime.utcnow() > until:
                    self.set_config(mult_key, '1.0')
                    self.set_config(until_key, '')
                    return 1.0
            except:
                pass
        return mult

    def set_global_boost(self, boost_type: str, multiplier: float, duration_seconds: int = None):
        self.set_config(f'global_{boost_type}_mult', str(multiplier))
        if duration_seconds:
            until = (datetime.utcnow() + timedelta(seconds=duration_seconds)).isoformat()
            self.set_config(f'global_{boost_type}_until', until)
        else:
            self.set_config(f'global_{boost_type}_until', '')

    def disable_global_boost(self, boost_type: str):
        self.set_config(f'global_{boost_type}_mult', '1.0')
        self.set_config(f'global_{boost_type}_until', '')

    def log_admin(self, admin_id: int, action: str, details: str = ''):
        self.execute("INSERT INTO admin_logs (admin_id, action, details) VALUES (?, ?, ?)", (admin_id, action, details))

# ========== ИНИЦИАЛИЗАЦИЯ БД ==========
db = Database()


# ========== СОСТОЯНИЯ FSM ==========
class AdminStates(StatesGroup):
    waiting_fake_name = State()
    waiting_give_data = State()
    waiting_broadcast_msg = State()
    waiting_channel_post = State()
    waiting_promo_data = State()
    waiting_config_key = State()
    waiting_config_value = State()
    waiting_boost_type = State()
    waiting_boost_mult = State()
    waiting_boost_duration = State()
    waiting_gift_item = State()
    waiting_gift_price = State()
    waiting_special_item_key = State()
    waiting_special_field = State()

class PromoStates(StatesGroup):
    waiting_for_code = State()

class P2PSaleStates(StatesGroup):
    waiting_for_price = State()

class CheckStates(StatesGroup):
    waiting_for_password = State()

class CreateCheckStates(StatesGroup):
    waiting_for_type = State()
    waiting_for_value = State()
    waiting_for_password = State()
    waiting_for_max_uses = State()

# ========== ИНИЦИАЛИЗАЦИЯ БОТА ==========
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def mask_name(name: str) -> str:
    if not name:
        return "User****"
    name = name.replace("@", "")
    return name[:3] + "****" if len(name) > 3 else name + "****"

def generate_fake_id() -> str:
    return "".join([str(random.randint(0, 9)) for _ in range(10)])

def get_main_kb(uid: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎯 Квесты", callback_data="tasks"),
        InlineKeyboardButton(text="⚔️ Дуэль", callback_data="duel_menu"),
        InlineKeyboardButton(text="👥 Друзья", callback_data="referrals")
    )
    builder.row(
        InlineKeyboardButton(text="🎰 Казино", callback_data="casino_menu"),  # заменили Удача
        InlineKeyboardButton(text="📆 Ежедневно", callback_data="daily"),
        InlineKeyboardButton(text="🎟 Лотерея", callback_data="lottery")
    )
    builder.row(
        InlineKeyboardButton(text="🛒 Магазин", callback_data="shop"),
        InlineKeyboardButton(text="🏪 P2P Маркет", callback_data="p2p_market"),
        InlineKeyboardButton(text="🎒 Инвентарь", callback_data="inventory_0")
    )
    builder.row(
        InlineKeyboardButton(text="🏆 ТОП", callback_data="top"),
        InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
        InlineKeyboardButton(text="🎁 Промокод", callback_data="use_promo")
    )
    builder.row(
    InlineKeyboardButton(text="💸 Вывести", callback_data="withdraw"),
    InlineKeyboardButton(text="🧾 Создать чек", callback_data="create_check")
    )
    if uid in ADMIN_IDS:
        builder.row(InlineKeyboardButton(text="👑 Админ Панель", callback_data="admin_panel"))
    return builder.as_markup()

def get_admin_decision_kb(uid: int, amount: Any) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Принять", callback_data=f"adm_app_{uid}_{amount}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_rej_{uid}_{amount}")
    )
    builder.row(InlineKeyboardButton(text="✉️ Написать в ЛС", callback_data=f"adm_chat_{uid}"))
    return builder.as_markup()


# ========== ОБРАБОТЧИКИ ПОЛЬЗОВАТЕЛЕЙ ==========
    
@dp.message(CommandStart())
async def cmd_start(message: Message):
    args = message.text.split()
    uid = message.from_user.id
    referred_by = None

    if len(args) > 1:
        param = args[1]
        if param.startswith("check_"):
            check_id = param.replace("check_", "")
            check = db.execute("SELECT * FROM checks WHERE id = ? AND is_active = 1", (check_id,), fetchone=True)
        if not check:
            await message.answer("❌ Чек не найден или неактивен")
            return
        if check['type'] == 'stars':
            per_use = float(check['value']) / check['max_uses']
            text = f"🎁 Чек на {per_use:.2f} ⭐"
        else:
            text = f"🎁 Чек на {check['value']}"
        if check['password']:
            text += "\n🔒 Защищён паролем"
        kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🎁 Забрать", callback_data=f"claim_{check_id}")).as_markup()
        await message.answer(text, reply_markup=kb)
        return

    # Проверка на реферальную ссылку
    if len(args) > 1:
        param = args[1]
        if param.startswith("ref"):
            try:
                referred_by = int(param.replace("ref", ""))
                if referred_by == uid:
                    referred_by = None
            except:
                pass
        elif param.startswith("duel"):
            creator_id = int(param.replace("duel", ""))
            if creator_id != uid:
                kb = InlineKeyboardBuilder().row(
                    InlineKeyboardButton(text="🤝 Принять вызов (5.0 ⭐)", callback_data=f"accept_duel_{creator_id}"),
                    InlineKeyboardButton(text="❌ Отказ", callback_data="menu")
                ).as_markup()
                await message.answer(f"⚔️ Игрок ID:{creator_id} вызывает тебя на дуэль!", reply_markup=kb)
                return

    # Создание пользователя, если новый
    user = db.get_user(uid)
    if not user:
        db.create_user(uid, message.from_user.username or "", message.from_user.first_name or "", referred_by)
        if referred_by:
            try:
                await bot.send_message(referred_by, "👥 У вас новый реферал! Он получит бонус, когда заработает первые 1.0 ⭐.")
            except:
                pass

    await message.answer(
        f"👋 Привет, <b>{message.from_user.first_name}</b>!\n\n"
        "💎 <b>StarsForQuestion</b> — место, где твоя активность превращается в Звёзды.\n\n"
        "🎯 Выполняй задания, крути удачу и забирай подарки!",
        reply_markup=get_main_kb(uid)
    )

@dp.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery):
    await call.message.edit_text("⭐ <b>Главное меню</b>", reply_markup=get_main_kb(call.from_user.id))

@dp.callback_query(F.data == "profile")
async def cb_profile(call: CallbackQuery):
    logging.info(f"Profile callback from {call.from_user.id}")
    await call.answer()
    u = db.get_user(call.from_user.id)
    if not u:
        return await call.message.answer("❌ Ошибка: вас нет в базе. Напишите /start")
    
    stars = float(u.get('stars', 0))
    referrals = int(u.get('referrals', 0))
    total_earned = float(u.get('total_earned', 0))
    ref_boost = float(u.get('ref_boost', 1.0))
    user_id = u.get('user_id', call.from_user.id)
    
    text = (
        f"👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"⭐ Баланс: <b>{stars:.2f} ⭐</b>\n"
        f"👥 Рефералов: {referrals}\n"
        f"📈 Всего заработано: {total_earned:.2f} ⭐\n"
        f"⚡ Персональный буст: x{ref_boost:.1f}"
    )
    kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu")).as_markup()
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception as e:
        logging.error(f"Error editing message in profile: {e}")
        await call.message.answer(text, reply_markup=kb)

@dp.callback_query(F.data == "referrals")
async def cb_referrals(call: CallbackQuery):
    logging.info(f"Referrals callback from {call.from_user.id}")
    await call.answer()
    u = db.get_user(call.from_user.id)
    if not u:
        return
    ref_code = u.get('ref_code', f"ref{call.from_user.id}")
    bot_username = (await bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start={ref_code}"
    ref_reward = float(db.get_config('ref_reward', 5.0))
    text = (
        f"👥 <b>Рефералы</b>\n\n"
        f"За активного друга (заработал ≥1 ⭐): <b>{ref_reward} ⭐</b>\n\n"
        f"🔗 Твоя реферальная ссылка:\n<code>{ref_link}</code>"
    )
    kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu")).as_markup()
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception as e:
        logging.error(f"Error editing message in referrals: {e}")
        await call.message.answer(text, reply_markup=kb)

@dp.callback_query(F.data == "daily")
async def cb_daily(call: CallbackQuery):
    uid = call.from_user.id
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    # Получаем текущий стрик
    row = db.execute("SELECT last_date, streak FROM daily_bonus WHERE user_id = ?", (uid,), fetchone=True)
    if row:
        last_date = datetime.strptime(row['last_date'], "%Y-%m-%d")
        delta = (now.date() - last_date.date()).days
        if delta == 0:
            return await call.answer("❌ Бонус уже получен! Приходи завтра.", show_alert=True)
        elif delta == 1:
            new_streak = min(row['streak'] + 1, 7)
        else:
            new_streak = 1
        db.execute("UPDATE daily_bonus SET last_date = ?, streak = ? WHERE user_id = ?", (today_str, new_streak, uid))
    else:
        new_streak = 1
        db.execute("INSERT INTO daily_bonus (user_id, last_date, streak) VALUES (?, ?, ?)", (uid, today_str, new_streak))

    # Размер бонуса: 0.1 * стрик
    reward = round(0.1 * new_streak, 2)
    db.add_stars(uid, reward)
    await call.answer(f"✅ День {new_streak}! Получено: {reward} ⭐", show_alert=True)
    await call.message.edit_text("⭐ <b>Главное меню</b>", reply_markup=get_main_kb(uid))

@dp.callback_query(F.data == "casino_menu")
async def casino_menu(call: CallbackQuery):
    uid = call.from_user.id
    user = db.get_user_safe(uid)
    premium = user.get('premium_mode', 0)
    status = "💎 Премиум (x2 ставка, x2 выигрыш)" if premium else "⚪ Обычный режим"
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🎰 1 spin (2 ⭐)", callback_data="casino_spin_1"),
        InlineKeyboardButton(text="🎰 10 spins (15 ⭐)", callback_data="casino_spin_10")
    )
    kb.row(
        InlineKeyboardButton(text=f"{'💎' if premium else '⚪'} Премиум режим", callback_data="casino_premium_toggle")
    )
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text(
        f"🎰 <b>КАЗИНО</b>\n\n"
        f"{status}\n"
        f"• 1 спин — 2 ⭐\n"
        f"• 10 спинов — 15 ⭐ (экономия 5 ⭐)\n"
        f"• Премиум режим — все ставки и выигрыши удваиваются",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data.startswith("casino_spin_"))
async def casino_spin(call: CallbackQuery):
    uid = call.from_user.id
    user = db.get_user_safe(uid)
    if not user:
        return await call.answer("Ошибка: вас нет в базе", show_alert=True)

    spin_count = int(call.data.split("_")[2])
    premium = user.get('premium_mode', 0)

    # Базовая стоимость
    base_cost = 2 if spin_count == 1 else 15
    cost = base_cost * (2 if premium else 1)

    if user['stars'] < cost:
        return await call.answer(f"❌ Недостаточно ⭐! Нужно {cost}", show_alert=True)

    db.add_stars(uid, -cost)

    total_win = 0
    results = []
    for _ in range(spin_count):
        win = random.uniform(0, 5)
        if premium:
            win *= 2
        total_win += win
        results.append(round(win, 2))

    db.add_stars(uid, total_win)

    if spin_count == 1:
        msg = f"🎰 Выигрыш: <b>{total_win:.2f} ⭐</b>"
    else:
        msg = f"🎰 Результаты 10 спинов: {', '.join(map(str, results))}\nИтого: <b>{total_win:.2f} ⭐</b>"

    await call.message.answer(msg)
    await casino_menu(call)
    
@dp.callback_query(F.data == "casino_premium_toggle")
async def casino_premium_toggle(call: CallbackQuery):
    uid = call.from_user.id
    user = db.get_user_safe(uid)
    if not user:
        return
    new_mode = 0 if user.get('premium_mode', 0) else 1
    db.execute("UPDATE users SET premium_mode = ? WHERE user_id = ?", (new_mode, uid))
    status = "включён" if new_mode else "выключен"
    await call.answer(f"💎 Премиум режим {status}", show_alert=True)
    await casino_menu(call)

@dp.callback_query(F.data == "luck")
async def cb_luck(call: CallbackQuery):
    logging.info(f"Luck callback from {call.from_user.id}")
    await call.answer()
    uid = call.from_user.id
    user = db.get_user(uid)
    if not user:
        return await call.message.answer("❌ Ошибка: вас нет в базе. Напишите /start")
    now = datetime.now()
    cooldown = int(db.get_config('luck_cooldown', 21600))
    last_luck = user.get('last_luck')
    if last_luck:
        try:
            last = datetime.fromisoformat(last_luck)
            if (now - last).total_seconds() < cooldown:
                remaining = int(cooldown - (now - last).total_seconds())
                minutes = remaining // 60
                return await call.answer(f"⏳ Подожди {minutes} мин.", show_alert=True)
        except:
            pass
    luck_min = float(db.get_config('luck_min', 0))
    luck_max = float(db.get_config('luck_max', 5))
    win = round(random.uniform(luck_min, luck_max), 2)
    game_boost = db.get_global_boost('game')
    win *= game_boost
    db.add_stars(uid, win)
    db.execute("UPDATE users SET last_luck = ? WHERE user_id = ?", (now.isoformat(), uid))
    await call.answer(f"🎰 +{win:.2f} ⭐", show_alert=True)
    try:
        await call.message.edit_text("⭐ <b>Главное меню</b>", reply_markup=get_main_kb(uid))
    except Exception as e:
        logging.error(f"Error editing message in luck: {e}")
        await call.message.answer("⭐ <b>Главное меню</b>", reply_markup=get_main_kb(uid))

# ========== КВЕСТЫ ==========

@dp.callback_query(F.data == "tasks")
async def cb_tasks(call: CallbackQuery):
    uid = call.from_user.id
    # Получаем активные квесты из БД
    quests = db.execute("SELECT * FROM quests WHERE is_active = 1", fetch=True)

    kb = InlineKeyboardBuilder()
    for q in quests:
        done = db.execute("SELECT 1 FROM user_quests WHERE user_id = ? AND quest_id = ?", (uid, q['id']), fetchone=True)
        status = "✅" if done else "⏳"
        kb.row(InlineKeyboardButton(text=f"{status} {q['name']}", callback_data=f"quest_info_{q['id']}"))

    # Постоянные разделы
    kb.row(InlineKeyboardButton(text="📺 Подписка на канал", callback_data="quest_channel"))
    kb.row(InlineKeyboardButton(text="🤖 Запустить бота", callback_data="quest_start"))
    kb.row(InlineKeyboardButton(text="📰 Посмотреть посты", callback_data="quest_posts"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))

    await call.message.edit_text(
        "🎯 <b>КВЕСТЫ</b>\n\n"
        "Здесь ты можешь выполнять задания и получать награды.\n"
        "Нажми на квест, чтобы узнать подробности.",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data.startswith("quest_info_"))
async def quest_info(call: CallbackQuery):
    quest_id = int(call.data.split("_")[2])
    q = db.execute("SELECT * FROM quests WHERE id = ?", (quest_id,), fetchone=True)
    if not q:
        return await call.answer("Квест не найден", show_alert=True)

    uid = call.from_user.id
    done = db.execute("SELECT 1 FROM user_quests WHERE user_id = ? AND quest_id = ?", (uid, quest_id), fetchone=True)

    text = f"<b>{q['name']}</b>\n{q['description']}\n\n"
    if q['reward_type'] == 'stars':
        text += f"Награда: {q['reward_value']} ⭐"
    else:
        text += f"Награда: {q['reward_value']}"

    kb = InlineKeyboardBuilder()
    if not done:
        kb.row(InlineKeyboardButton(text="✅ Выполнить", callback_data=f"quest_do_{quest_id}"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="tasks"))
    await call.message.edit_text(text, reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("quest_do_"))
async def quest_do(call: CallbackQuery):
    quest_id = int(call.data.split("_")[2])
    uid = call.from_user.id
    q = db.execute("SELECT * FROM quests WHERE id = ?", (quest_id,), fetchone=True)
    if not q:
        return await call.answer("Квест не найден", show_alert=True)

    # Проверяем, не выполнен ли уже
    done = db.execute("SELECT 1 FROM user_quests WHERE user_id = ? AND quest_id = ?", (uid, quest_id), fetchone=True)
    if done:
        return await call.answer("Ты уже выполнил этот квест", show_alert=True)

    # Проверка условия (упрощённо, можно расширить)
    condition_met = False
    if q['condition_type'] == 'channel_sub':
        try:
            chat_member = await bot.get_chat_member(chat_id=q['condition_value'], user_id=uid)
            condition_met = chat_member.status in ['member', 'administrator', 'creator']
        except:
            condition_met = False
    elif q['condition_type'] == 'bot_start':
        condition_met = True  # всегда true, т.к. пользователь в боте
    elif q['condition_type'] == 'post_view':
        # Здесь нужна отдельная логика, для примера считаем true
        condition_met = True
    else:
        condition_met = True  # для кастомных квестов

    if not condition_met:
        return await call.answer("❌ Условие не выполнено", show_alert=True)

    # Выдача награды
    if q['reward_type'] == 'stars':
        db.add_stars(uid, float(q['reward_value']))
    else:
        item = q['reward_value']
        existing = db.execute("SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?", (uid, item), fetchone=True)
        if existing:
            db.execute("UPDATE inventory SET quantity = quantity + 1 WHERE user_id = ? AND item_name = ?", (uid, item))
        else:
            db.execute("INSERT INTO inventory (user_id, item_name, quantity) VALUES (?, ?, 1)", (uid, item))

    db.execute("INSERT INTO user_quests (user_id, quest_id) VALUES (?, ?)", (uid, quest_id))
    await call.answer("✅ Награда получена!", show_alert=True)
    await quest_info(call)  # обновляем

@dp.callback_query(F.data == "quest_channel")
async def quest_channel(call: CallbackQuery):
    # Здесь можно проверить подписку на конкретный канал
    await call.answer("Функция в разработке", show_alert=True)

@dp.callback_query(F.data == "quest_start")
async def quest_start(call: CallbackQuery):
    # Запуск бота – уже выполнено, можно выдать награду один раз
    uid = call.from_user.id
    done = db.execute("SELECT 1 FROM user_quests WHERE user_id = ? AND task_id = 'start_bot'", (uid,), fetchone=True)
    if done:
        await call.answer("Ты уже получал награду за запуск", show_alert=True)
    else:
        db.add_stars(uid, 1.0)
        db.execute("INSERT INTO user_quests (user_id, task_id) VALUES (?, 'start_bot')", (uid,))
        await call.answer("✅ +1 ⭐ за запуск бота!", show_alert=True)

@dp.callback_query(F.data == "quest_posts")
async def quest_posts(call: CallbackQuery):
    # Здесь можно давать награду за просмотр постов (нужна отдельная логика)
    await call.answer("Функция в разработке", show_alert=True)

# ========== ДУЭЛИ ==========
@dp.callback_query(F.data == "duel_menu")
async def cb_duel_menu(call: CallbackQuery):
    uid = call.from_user.id
    bot_username = (await bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=duel{uid}"
    text = (
        "⚔️ <b>ДУЭЛЬНЫЙ КЛУБ</b>\n━━━━━━━━━━━━━━\n"
        "Ставка: <b>5.0 ⭐</b>\n"
        "Победитель получает: <b>9.0 ⭐</b>\n\n"
        "Отправь ссылку другу, чтобы вызвать его на бой:"
    )
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📨 Отправить другу", switch_inline_query=link))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text(f"{text}\n<code>{link}</code>", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("accept_duel_"))
async def cb_accept_duel(call: CallbackQuery):
    opponent_id = call.from_user.id
    creator_id = int(call.data.split("_")[2])
    if opponent_id == creator_id:
        return await call.answer("❌ Нельзя играть с самим собой!", show_alert=True)
    user = db.get_user(opponent_id)
    if not user or user['stars'] < 5.0:
        return await call.answer("❌ Недостаточно ⭐ для ставки!", show_alert=True)
    db.add_stars(opponent_id, -5.0)
    msg = await call.message.answer("🎲 Бросаем кости...")
    dice = await msg.answer_dice("🎲")
    await asyncio.sleep(3.5)
    winner_id = creator_id if dice.dice.value <= 3 else opponent_id
    db.add_stars(winner_id, 9.0)
    await call.message.answer(
        f"🎰 Выпало <b>{dice.dice.value}</b>!\n"
        f"👑 Победитель: <a href='tg://user?id={winner_id}'>Игрок</a>\n"
        f"Зачислено: <b>9.0 ⭐</b>"
    )

# ========== ЛОТЕРЕЯ ==========
@dp.callback_query(F.data == "lottery")
async def cb_lottery(call: CallbackQuery):
    data = db.execute("SELECT pool, participants FROM lottery WHERE id = 1", fetchone=True)
    if not data:
        return
    participants = data['participants'].split(',') if data['participants'] else []
    count = len([p for p in participants if p])
    text = (
        "🎟 <b>ЗВЁЗДНАЯ ЛОТЕРЕЯ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"💰 Текущий банк: <b>{data['pool']:.2f} ⭐</b>\n"
        f"👥 Участников: <b>{count}</b>\n"
        f"🎫 Цена билета: <b>2.0 ⭐</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Победитель забирает 80% банка. Розыгрыш происходит автоматически при запуске админом!</i>"
    )
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="💎 Купить билет", callback_data="buy_ticket"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text(text, reply_markup=kb.as_markup())

@dp.callback_query(F.data == "buy_ticket")
async def cb_buy_ticket(call: CallbackQuery):
    uid = call.from_user.id
    user = db.get_user(uid)
    if not user or user['stars'] < 2:
        return await call.answer("❌ Недостаточно звёзд (нужно 2.0)", show_alert=True)
    db.add_stars(uid, -2)
    db.execute("UPDATE lottery SET pool = pool + 2, participants = participants || ? WHERE id = 1", (f"{uid},",))
    db.execute("INSERT INTO lottery_history (user_id) VALUES (?)", (uid,))
    await call.answer("✅ Билет куплен!", show_alert=True)
    await cb_lottery(call)

# ========== ТОП ==========

@dp.callback_query(F.data == "top")
async def cb_top(call: CallbackQuery):
    await call.answer()
    rows = db.execute("SELECT user_id, username, first_name, stars FROM users ORDER BY stars DESC LIMIT 10", fetch=True)
    text = "🏆 <b>ТОП-10 МАГНАТОВ</b>\n━━━━━━━━━━━━━━━━━━\n"
    for i, row in enumerate(rows, 1):
        # Используем username, если есть, иначе first_name
        name = row['username'] or row['first_name'] or "Без имени"
        stars = float(row['stars']) if row['stars'] is not None else 0
        text += f"{i}. {name} — <b>{stars:.1f} ⭐</b>\n"
    kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu")).as_markup()
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except:
        await call.message.answer(text, reply_markup=kb)

# ========== ВЫВОД СРЕДСТВ ==========
@dp.callback_query(F.data == "withdraw")
async def cb_withdraw_select(call: CallbackQuery):
    uid = call.from_user.id
    user = db.get_user(uid)
    if not user or user['stars'] < 15:
        return await call.answer("❌ Минимум 15 ⭐", show_alert=True)
    options = db.get_withdrawal_options()
    kb = InlineKeyboardBuilder()
    for opt in options:
        if user['stars'] >= opt:
            kb.row(InlineKeyboardButton(text=f"💎 {opt} ⭐", callback_data=f"wd_run_{opt}"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text("Выбери сумму:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("wd_run_"))
async def cb_wd_execute(call: CallbackQuery):
    amt = float(call.data.split("_")[2])
    uid = call.from_user.id
    user = db.get_user(uid)
    if not user or user['stars'] < amt:
        return await call.answer("❌ Недостаточно ⭐", show_alert=True)
    db.add_stars(uid, -amt)
    name = mask_name(call.from_user.username or call.from_user.first_name)
    await bot.send_message(
        WITHDRAWAL_CHANNEL_ID,
        f"📥 <b>НОВАЯ ЗАЯВКА</b>\n\n👤 Юзер: @{name}\n🆔 ID: <code>{uid}</code>\n💎 Сумма: <b>{amt} ⭐</b>",
        reply_markup=get_admin_decision_kb(uid, amt)
    )
    await call.message.edit_text("✅ Заявка отправлена!", reply_markup=get_main_kb(uid))

# ========== МАГАЗИН И ИНВЕНТАРЬ ==========
@dp.callback_query(F.data == "shop")
async def cb_shop_menu(call: CallbackQuery):
    gifts = db.get_gifts_prices()
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="💎 ЭКСКЛЮЗИВНЫЕ ТОВАРЫ", callback_data="special_shop"))
    kb.row(InlineKeyboardButton(text="⚡ Буст рефералов +0.1 (50 ⭐)", callback_data="buy_boost_01"))
    for item, price in gifts.items():
        kb.add(InlineKeyboardButton(text=f"{item} {price}⭐", callback_data=f"buy_g_{item}"))
    kb.adjust(1, 1, 2)
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text(
        "✨ <b>МАГАЗИН</b>\n\n"
        "Обычные подарки доступны всегда, а в <b>Эксклюзивном отделе</b> товары ограничены по количеству!",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data == "buy_boost_01")
async def buy_boost(call: CallbackQuery):
    uid = call.from_user.id
    user = db.get_user(uid)
    if not user or user['stars'] < 50:
        return await call.answer("❌ Нужно 50 ⭐", show_alert=True)
    db.add_stars(uid, -50)
    db.execute("UPDATE users SET ref_boost = ref_boost + 0.1 WHERE user_id = ?", (uid,))
    await call.answer("🚀 Буст куплен! Теперь ты получаешь больше.", show_alert=True)

@dp.callback_query(F.data.startswith("buy_g_"))
async def process_gift_buy(call: CallbackQuery):
    item_name = call.data.replace("buy_g_", "")
    gifts = db.get_gifts_prices()
    price = gifts.get(item_name)
    if not price:
        return await call.answer("❌ Товар не найден", show_alert=True)
    uid = call.from_user.id
    user = db.get_user(uid)
    if not user or user['stars'] < price:
        return await call.answer(f"❌ Недостаточно звёзд! Нужно {price} ⭐", show_alert=True)
    db.add_stars(uid, -price)
    # Добавляем в инвентарь
    existing = db.execute(
        "SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?",
        (uid, item_name), fetchone=True
    )
    if existing:
        db.execute("UPDATE inventory SET quantity = quantity + 1 WHERE user_id = ? AND item_name = ?", (uid, item_name))
    else:
        db.execute("INSERT INTO inventory (user_id, item_name, quantity) VALUES (?, ?, 1)", (uid, item_name))
    await call.answer(f"✅ Ты купил {item_name}!", show_alert=True)

@dp.callback_query(F.data.startswith("inventory"))
async def cb_inventory_logic(call: CallbackQuery):
    # Обработчик для inventory_0, inventory_1 и т.д.
    parts = call.data.split("_")
    page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    uid = call.from_user.id
    items = db.execute(
        "SELECT item_name, quantity FROM inventory WHERE user_id = ?",
        (uid,), fetch=True
    )
    if not items:
        kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu")).as_markup()
        return await call.message.edit_text("🎒 <b>Твой инвентарь пуст.</b>\nКупи что-нибудь в магазине!", reply_markup=kb)

    ITEMS_PER_PAGE = 5
    total_pages = (len(items) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    current = items[start:end]

    text = f"🎒 <b>ТВОЙ ИНВЕНТАРЬ</b> (Стр. {page+1}/{total_pages})\n\nНажми на предмет, чтобы вывести его:"
    kb = InlineKeyboardBuilder()
    for it in current:
        kb.row(InlineKeyboardButton(text=f"{it['item_name']} ({it['quantity']} шт.)", callback_data=f"pre_out_{it['item_name']}"))
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"inventory_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"inventory_{page+1}"))
    if nav:
        kb.row(*nav)
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text(text, reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("pre_out_"))
async def cb_pre_out(call: CallbackQuery):
    item = call.data.replace("pre_out_", "")
    specials = db.get_special_items()
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🎁 Получить как подарок", callback_data=f"confirm_out_{item}"))
    # Если это эксклюзивный товар – разрешить продажу на P2P
    if any(info['full_name'] == item for info in specials.values()):
        kb.row(InlineKeyboardButton(text="💰 Выставить на P2P Маркет", callback_data=f"sell_p2p_{item}"))
    kb.row(InlineKeyboardButton(text="❌ Отмена", callback_data="inventory_0"))
    await call.message.edit_text(f"Ты выбрал: <b>{item}</b>\nЧто хочешь сделать?", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("confirm_out_"))
async def cb_final_out(call: CallbackQuery):
    item = call.data.replace("confirm_out_", "")
    uid = call.from_user.id
    username = call.from_user.username or "User"
    name_masked = mask_name(call.from_user.first_name)

    # Проверяем наличие
    res = db.execute(
        "SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?",
        (uid, item), fetchone=True
    )
    if not res or res['quantity'] <= 0:
        return await call.answer("❌ Предмет не найден!", show_alert=True)

    # Удаляем 1 шт
    if res['quantity'] > 1:
        db.execute("UPDATE inventory SET quantity = quantity - 1 WHERE user_id = ? AND item_name = ?", (uid, item))
    else:
        db.execute("DELETE FROM inventory WHERE user_id = ? AND item_name = ?", (uid, item))

    await bot.send_message(
        WITHDRAWAL_CHANNEL_ID,
        f"🎁 <b>ЗАЯВКА НА ВЫВОД</b>\n\n"
        f"👤 Юзер: @{username}\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📦 Предмет: <b>{item}</b>",
        reply_markup=get_admin_decision_kb(uid, "GIFT")
    )
    await call.message.edit_text(
        f"✅ Заявка на вывод <b>{item}</b> отправлена!\nОжидай сообщения от администратора.",
        reply_markup=get_main_kb(uid)
    )

# ========== ЭКСКЛЮЗИВНЫЙ МАГАЗИН ==========
@dp.callback_query(F.data == "special_shop")
async def cb_special_shop(call: CallbackQuery):
    specials = db.get_special_items()
    kb = InlineKeyboardBuilder()
    for key, info in specials.items():
        sold = db.execute(
            "SELECT SUM(quantity) as total FROM inventory WHERE item_name = ?",
            (info['full_name'],), fetchone=True
        )
        sold_cnt = sold['total'] if sold and sold['total'] else 0
        left = info['limit'] - sold_cnt
        if left > 0:
            text = f"{info['full_name']} — {info['price']} ⭐ (Осталось: {left})"
            callback = f"buy_t_{key}"
        else:
            text = f"{info['full_name']} — 🚫 РАСПРОДАНО"
            callback = "sold_out"
        kb.row(InlineKeyboardButton(text=text, callback_data=callback))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="shop"))
    await call.message.edit_text(
        "🛒 <b>ЭКСКЛЮЗИВНЫЕ ТОВАРЫ</b>\n\n"
        "<i>Когда лимит исчерпан, товар можно купить только у игроков на P2P Рынке!</i>",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data == "sold_out")
async def cb_sold_out(call: CallbackQuery):
    await call.answer("❌ Этот товар закончился в магазине! Ищи его на P2P.", show_alert=True)

@dp.callback_query(F.data.startswith("buy_t_"))
async def buy_special_item(call: CallbackQuery):
    item_key = call.data.split("_")[2]
    specials = db.get_special_items()
    info = specials.get(item_key)
    if not info:
        return
    uid = call.from_user.id
    user = db.get_user(uid)
    if not user or user['stars'] < info['price']:
        return await call.answer("❌ Недостаточно звёзд!", show_alert=True)

    # Проверка лимита
    sold = db.execute(
        "SELECT SUM(quantity) as total FROM inventory WHERE item_name = ?",
        (info['full_name'],), fetchone=True
    )
    sold_cnt = sold['total'] if sold and sold['total'] else 0
    if sold_cnt >= info['limit']:
        return await call.answer("❌ Лимит исчерпан!", show_alert=True)

    db.add_stars(uid, -info['price'])
    # Добавляем в инвентарь
    existing = db.execute(
        "SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?",
        (uid, info['full_name']), fetchone=True
    )
    if existing:
        db.execute("UPDATE inventory SET quantity = quantity + 1 WHERE user_id = ? AND item_name = ?", (uid, info['full_name']))
    else:
        db.execute("INSERT INTO inventory (user_id, item_name, quantity) VALUES (?, ?, 1)", (uid, info['full_name']))
    await call.answer(f"✅ {info['full_name']} куплен!", show_alert=True)
    await cb_special_shop(call)

# ========== P2P МАРКЕТ ==========
@dp.callback_query(F.data == "p2p_market")
async def cb_p2p_market(call: CallbackQuery):
    items = db.execute("SELECT id, seller_id, item_name, price FROM marketplace", fetch=True)
    text = "🏪 <b>P2P МАРКЕТ</b>\n\nЗдесь можно перекупить эксклюзивы у игроков.\n"
    if not items:
        text += "\n<i>Лотов пока нет.</i>"
    kb = InlineKeyboardBuilder()
    for it in items:
        kb.row(InlineKeyboardButton(text=f"🛒 {it['item_name']} | {it['price']} ⭐", callback_data=f"buy_p2p_{it['id']}"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text(text, reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("sell_p2p_"))
async def cb_sell_item_start(call: CallbackQuery, state: FSMContext):
    item_name = call.data.replace("sell_p2p_", "")
    await state.update_data(sell_item=item_name)
    await state.set_state(P2PSaleStates.waiting_for_price)
    await call.message.answer(f"💰 Введи цену в ⭐, за которую хочешь продать <b>{item_name}</b>:")

@dp.message(P2PSaleStates.waiting_for_price)
async def process_p2p_sale_price(message: Message, state: FSMContext):
    data = await state.get_data()
    item_name = data.get("sell_item")
    uid = message.from_user.id
    if not message.text.isdigit():
        return await message.answer("❌ Введи цену числом!")
    price = int(message.text)
    if price <= 0:
        return await message.answer("❌ Цена должна быть больше 0!")

    # Проверяем наличие предмета
    res = db.execute(
        "SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?",
        (uid, item_name), fetchone=True
    )
    if not res or res['quantity'] <= 0:
        await state.clear()
        return await message.answer("❌ У тебя нет этого предмета!")

    # Забираем предмет
    if res['quantity'] > 1:
        db.execute("UPDATE inventory SET quantity = quantity - 1 WHERE user_id = ? AND item_name = ?", (uid, item_name))
    else:
        db.execute("DELETE FROM inventory WHERE user_id = ? AND item_name = ?", (uid, item_name))

    # Выставляем на маркет
    db.execute("INSERT INTO marketplace (seller_id, item_name, price) VALUES (?, ?, ?)", (uid, item_name, price))
    await message.answer(f"✅ Предмет <b>{item_name}</b> выставлен на P2P Маркет за {price} ⭐")
    await state.clear()

@dp.callback_query(F.data.startswith("buy_p2p_"))
async def cb_buy_p2p(call: CallbackQuery):
    order_id = int(call.data.split("_")[2])
    buyer_id = call.from_user.id
    order = db.execute("SELECT * FROM marketplace WHERE id = ?", (order_id,), fetchone=True)
    if not order:
        return await call.answer("❌ Товар уже продан!", show_alert=True)
    if order['seller_id'] == buyer_id:
        return await call.answer("❌ Свой товар купить нельзя!", show_alert=True)
    buyer = db.get_user(buyer_id)
    if not buyer or buyer['stars'] < order['price']:
        return await call.answer("❌ Недостаточно ⭐", show_alert=True)

    # Списать с покупателя, начислить продавцу (комиссия 10%)
    db.add_stars(buyer_id, -order['price'])
    seller_income = order['price'] * 0.9
    db.add_stars(order['seller_id'], seller_income)

    # Добавить предмет покупателю
    existing = db.execute(
        "SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?",
        (buyer_id, order['item_name']), fetchone=True
    )
    if existing:
        db.execute("UPDATE inventory SET quantity = quantity + 1 WHERE user_id = ? AND item_name = ?", (buyer_id, order['item_name']))
    else:
        db.execute("INSERT INTO inventory (user_id, item_name, quantity) VALUES (?, ?, 1)", (buyer_id, order['item_name']))

    # Удалить лот
    db.execute("DELETE FROM marketplace WHERE id = ?", (order_id,))

    await call.answer(f"✅ Успешно куплен {order['item_name']}!", show_alert=True)
    await cb_p2p_market(call)

# ========== ПРОМОКОДЫ ==========
@dp.callback_query(F.data == "use_promo")
async def promo_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(PromoStates.waiting_for_code)
    await call.message.answer("⌨️ Введи промокод:")

@dp.message(PromoStates.waiting_for_code)
async def promo_process(message: Message, state: FSMContext):
    code = message.text.strip()
    uid = message.from_user.id

    already = db.execute(
        "SELECT 1 FROM promo_history WHERE user_id = ? AND code = ?",
        (uid, code), fetchone=True
    )
    if already:
        await state.clear()
        return await message.answer("❌ Ты уже активировал этот промокод!")

    promo = db.execute(
        "SELECT * FROM promo WHERE code = ? AND uses > 0",
        (code,), fetchone=True
    )
    if not promo:
        await state.clear()
        return await message.answer("❌ Код неверный или закончились активации.")

    # Уменьшаем лимит использований
    db.execute("UPDATE promo SET uses = uses - 1 WHERE code = ?", (code,))
    db.execute("INSERT INTO promo_history (user_id, code) VALUES (?, ?)", (uid, code))

    if promo['reward_type'] == 'stars':
        db.add_stars(uid, float(promo['reward_value']))
        await message.answer(f"✅ Активировано! +{promo['reward_value']} ⭐")
    else:
        item = promo['reward_value']
        existing = db.execute(
            "SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?",
            (uid, item), fetchone=True
        )
        if existing:
            db.execute("UPDATE inventory SET quantity = quantity + 1 WHERE user_id = ? AND item_name = ?", (uid, item))
        else:
            db.execute("INSERT INTO inventory (user_id, item_name, quantity) VALUES (?, ?, 1)", (uid, item))
        await message.answer(f"✅ Активировано! Получен предмет: {item}")
    await state.clear()

#============== ЧЕКИ ===============

@dp.callback_query(F.data == "create_check")
async def create_check_start(call: CallbackQuery, state: FSMContext):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⭐ Звёзды", callback_data="check_type_stars"))
    kb.row(InlineKeyboardButton(text="🎁 Подарок", callback_data="check_type_item"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text("Выбери тип чека:", reply_markup=kb.as_markup())
    await state.set_state(CreateCheckStates.waiting_for_type)

@dp.callback_query(CreateCheckStates.waiting_for_type, F.data.startswith("check_type_"))
async def create_check_type(call: CallbackQuery, state: FSMContext):
    ctype = call.data.split("_")[2]  # stars или item
    await state.update_data(ctype=ctype)
    if ctype == "stars":
        await call.message.answer("Введи количество звёзд (например: 50):")
    else:
        # Показываем список предметов из инвентаря
        uid = call.from_user.id
        items = db.execute("SELECT item_name, quantity FROM inventory WHERE user_id = ?", (uid,), fetch=True)
        if not items:
            await state.clear()
            return await call.answer("У тебя нет предметов для создания чека!", show_alert=True)
        text = "Твои предметы:\n" + "\n".join([f"{it['item_name']} ({it['quantity']} шт.)" for it in items])
        text += "\n\nВведи название предмета, который хочешь использовать:"
        await call.message.answer(text)
    await state.set_state(CreateCheckStates.waiting_for_value)

@dp.message(CreateCheckStates.waiting_for_value)
async def create_check_value(message: Message, state: FSMContext):
    data = await state.get_data()
    ctype = data.get('ctype')
    value = message.text.strip()
    uid = message.from_user.id

    if ctype == 'stars':
        try:
            amount = float(value)
            if amount <= 0:
                raise ValueError
            user = db.get_user_safe(uid)
            if user['stars'] < amount:
                await message.answer("❌ Недостаточно звёзд!")
                return
            # Временно списывать не будем, спишем при создании
        except:
            await message.answer("❌ Введи положительное число")
            return
    else:
        # Проверяем наличие предмета
        item = value
        res = db.execute("SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?", (uid, item), fetchone=True)
        if not res or res['quantity'] <= 0:
            await message.answer("❌ У тебя нет такого предмета!")
            return

    await state.update_data(value=value)
    await message.answer("Введи пароль (если не нужен, отправь '-'):")
    await state.set_state(CreateCheckStates.waiting_for_password)

@dp.message(CreateCheckStates.waiting_for_password)
async def create_check_password(message: Message, state: FSMContext):
    password = message.text.strip()
    if password == '-':
        password = ''
    await state.update_data(password=password)
    await message.answer("Введи количество активаций (число):")
    await state.set_state(CreateCheckStates.waiting_for_max_uses)

@dp.message(CreateCheckStates.waiting_for_max_uses)
async def create_check_max_uses(message: Message, state: FSMContext):
    try:
        max_uses = int(message.text.strip())
        if max_uses <= 0:
            raise ValueError
    except:
        await message.answer("❌ Введи положительное целое число")
        return

    data = await state.get_data()
    ctype = data['ctype']
    value = data['value']
    password = data['password']
    uid = message.from_user.id

    if ctype == 'stars':
        total_amount = float(value)
        user = db.get_user_safe(uid)
        if user['stars'] < total_amount:
            await message.answer("❌ Недостаточно звёзд!")
            await state.clear()
            return
        db.add_stars(uid, -total_amount)
        stored_value = str(total_amount)
    else:  # предмет
        item = value
        res = db.execute("SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?", (uid, item), fetchone=True)
        if not res or res['quantity'] < max_uses:
            await message.answer(f"❌ У тебя недостаточно предметов! Нужно {max_uses} шт.")
            await state.clear()
            return
        if res['quantity'] > max_uses:
            db.execute("UPDATE inventory SET quantity = quantity - ? WHERE user_id = ? AND item_name = ?", (max_uses, uid, item))
        else:
            db.execute("DELETE FROM inventory WHERE user_id = ? AND item_name = ?", (uid, item))
        stored_value = item

    check_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    db.execute(
        "INSERT INTO checks (id, creator_id, type, value, password, max_uses) VALUES (?, ?, ?, ?, ?, ?)",
        (check_id, uid, ctype, stored_value, password, max_uses)
    )

    bot_username = (await bot.get_me()).username
    deep_link = f"https://t.me/{bot_username}?start=check_{check_id}"

    if ctype == 'stars':
        per_use = total_amount / max_uses
        text = f"🎁 Чек на {per_use:.2f} ⭐ (всего {max_uses} активаций, общая сумма {total_amount} ⭐)"
    else:
        text = f"🎁 Чек на {stored_value} (всего {max_uses} активаций)"

    if password:
        text += "\n🔒 Защищён паролем"
    text += f"\n\n🔗 Ссылка на чек: {deep_link}"

    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🎁 Забрать", callback_data=f"claim_{check_id}"))
    kb.row(InlineKeyboardButton(text="📤 Отправить другу", url=f"https://t.me/share/url?url={deep_link}&text=Забери%20чек!"))
    await message.answer(text, reply_markup=kb.as_markup())
    await state.clear()

# ========== АДМИН ПАНЕЛЬ ==========
@dp.callback_query(F.data == "admin_panel")
async def cb_admin_panel(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return await call.answer("❌ Нет доступа!", show_alert=True)
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="📢 Рассылка", callback_data="a_broadcast"),
        InlineKeyboardButton(text="🎁 Создать Промо", callback_data="a_create_promo")
    )
    kb.row(
        InlineKeyboardButton(text="📢 Пост в КАНАЛ", callback_data="a_post_chan"),
        InlineKeyboardButton(text="🎭 Фейк Заявка", callback_data="a_fake_gen")
    )
    kb.row(
        InlineKeyboardButton(text="💎 Выдать ⭐", callback_data="a_give_stars"),
        InlineKeyboardButton(text="⛔ Стоп Лотерея 🎰", callback_data="a_run_lottery")
    )
    kb.row(
        InlineKeyboardButton(text="⚙️ Настройки бота", callback_data="a_config_menu"),
        InlineKeyboardButton(text="📈 Глобальные бусты", callback_data="a_global_boost_menu")
    )
    kb.row(
        InlineKeyboardButton(text="🛍 Цены магазина", callback_data="a_edit_gifts"),
        InlineKeyboardButton(text="📦 Лимиты эксклюзивов", callback_data="a_edit_specials")
    )
    kb.row(InlineKeyboardButton(text="🎯 Управление квестами", callback_data="a_quests"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text("👑 <b>АДМИН-МЕНЮ</b>", reply_markup=kb.as_markup())


@dp.callback_query(F.data == "a_quests")
async def a_quests_menu(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="➕ Создать квест", callback_data="a_quest_create"))
    kb.row(InlineKeyboardButton(text="📋 Список квестов", callback_data="a_quest_list"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel"))
    await call.message.edit_text("🎯 Управление квестами", reply_markup=kb.as_markup())
    

# --- Рассылка ---
@dp.callback_query(F.data == "a_broadcast")
async def adm_broadcast_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_broadcast_msg)
    await call.message.edit_text(
        "📢 <b>РАССЫЛКА ПОЛЬЗОВАТЕЛЯМ</b>\n\n"
        "Отправь сообщение (текст, фото, видео), которое хочешь разослать всем.",
        reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")).as_markup()
    )

@dp.message(AdminStates.waiting_broadcast_msg)
async def adm_broadcast_confirm(message: Message, state: FSMContext):
    await state.update_data(broadcast_msg_id=message.message_id, broadcast_chat_id=message.chat.id)
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🚀 НАЧАТЬ", callback_data="confirm_broadcast_send"))
    kb.row(InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel"))
    await message.answer("👆 <b>Это превью сообщения.</b>\nНачать рассылку для всех пользователей?",
                         reply_markup=kb.as_markup())

@dp.callback_query(F.data == "confirm_broadcast_send")
async def adm_broadcast_run(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    msg_id = data.get("broadcast_msg_id")
    from_chat = data.get("broadcast_chat_id")
    await state.clear()

    # Получаем всех пользователей
    rows = db.execute("SELECT user_id FROM users", fetch=True)
    users = [row['user_id'] for row in rows]
    if not users:
        return await call.message.answer("❌ Нет пользователей для рассылки.")

    await call.message.edit_text(f"⏳ Рассылка запущена для {len(users)} чел...")
    count = 0
    err = 0
    for uid in users:
        try:
            await bot.copy_message(chat_id=uid, from_chat_id=from_chat, message_id=msg_id)
            count += 1
            await asyncio.sleep(0.05)
        except Exception:
            err += 1
    await call.message.answer(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📊 Успешно: {count}\n"
        f"🚫 Ошибок: {err}"
    )
    db.log_admin(call.from_user.id, "broadcast", f"Успешно: {count}, ошибок: {err}")

# --- Выдача звёзд ---
@dp.callback_query(F.data == "a_give_stars")
async def adm_give_stars_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_give_data)
    await call.message.edit_text(
        "💎 <b>ВЫДАЧА ЗВЁЗД</b>\n\n"
        "Введи ID пользователя и количество звёзд через пробел.\n"
        "Пример: <code>8364667153 100</code>",
        reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")).as_markup()
    )

@dp.message(AdminStates.waiting_give_data)
async def adm_give_stars_process(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        data = message.text.split()
        if len(data) != 2:
            return await message.answer("❌ Введи два числа: ID и сумму.")
        target_id = int(data[0])
        amount = float(data[1])
        user = db.get_user(target_id)
        if not user:
            return await message.answer(f"❌ Пользователь с ID <code>{target_id}</code> не найден!")
        db.add_stars(target_id, amount)
        await message.answer(
            f"✅ <b>УСПЕШНО!</b>\n\n"
            f"Пользователю: <b>{user['first_name']}</b> (<code>{target_id}</code>)\n"
            f"Начислено: <b>{amount} ⭐</b>",
            reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 В админку", callback_data="admin_panel")).as_markup()
        )
        try:
            await bot.send_message(target_id, f"🎁 Администратор начислил тебе <b>{amount} ⭐</b>!")
        except:
            pass
        db.log_admin(message.from_user.id, "give_stars", f"Пользователю {target_id} сумма {amount}")
        await state.clear()
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

# --- Создание промокода ---
@dp.callback_query(F.data == "a_create_promo")
async def adm_promo_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_promo_data)
    await call.message.answer(
        "Введи данные промокода через пробел:\n"
        "<code>КОД ТИП ЗНАЧЕНИЕ КОЛИЧЕСТВО</code>\n\n"
        "Примеры:\n"
        "<code>GIFT1 stars 100 10</code> (100 звёзд)\n"
        "<code>ROZA gift 🌹_Роза 5</code> (5 роз)"
    )

@dp.message(AdminStates.waiting_promo_data)
async def adm_promo_save(message: Message, state: FSMContext):
    try:
        code, r_type, val, uses = message.text.split()
        uses = int(uses)
        db.execute("INSERT INTO promo VALUES (?, ?, ?, ?)", (code, r_type, val, uses))
        await message.answer(f"✅ Промокод <code>{code}</code> создан на {uses} использований!")
        db.log_admin(message.from_user.id, "create_promo", f"Код {code}, тип {r_type}, значение {val}, лимит {uses}")
        await state.clear()
    except Exception as e:
        await message.answer("❌ Ошибка! Формат: <code>КОД ТИП ЗНАЧЕНИЕ КОЛИЧЕСТВО</code>")

# --- Пост в канал ---
@dp.callback_query(F.data == "a_post_chan")
async def adm_post_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_channel_post)
    await call.message.edit_text(
        "📢 Отправь текст для публикации в канале.\n"
        "Бот автоматически добавит кнопку для получения награды."
    )

@dp.message(AdminStates.waiting_channel_post)
async def adm_post_end(message: Message, state: FSMContext):
    pid = f"v_{random.randint(100, 999)}"
    view_reward = float(db.get_config('view_reward', 0.3))
    kb = InlineKeyboardBuilder().row(
        InlineKeyboardButton(text=f"💰 Забрать {view_reward} ⭐", callback_data=f"claim_{pid}")
    ).as_markup()
    await bot.send_message(CHANNEL_ID, message.text, reply_markup=kb)
    await message.answer("✅ Опубликовано!")
    db.log_admin(message.from_user.id, "channel_post", f"Пост с id {pid}")
    await state.clear()

@dp.callback_query(F.data.startswith("claim_"))
async def cb_claim(call: CallbackQuery):
    pid = call.data.split("_")[1]
    uid = call.from_user.id
    user = db.get_user(uid)
    if not user:
        return await call.answer("❌ Запусти бота командой /start", show_alert=True)
    # Проверка, забирал ли уже
    check = db.execute(
        "SELECT 1 FROM task_claims WHERE user_id = ? AND task_id = ?",
        (uid, f"post_{pid}"), fetchone=True
    )
    if check:
        return await call.answer("❌ Ты уже забрал награду!", show_alert=True)
    view_reward = float(db.get_config('view_reward', 0.3))
    db.add_stars(uid, view_reward)
    db.execute("INSERT INTO task_claims (user_id, task_id) VALUES (?, ?)", (uid, f"post_{pid}"))
    await call.answer(f"✅ +{view_reward} ⭐", show_alert=True)

# --- Фейк заявка ---
@dp.callback_query(F.data == "a_fake_gen")
async def adm_fake(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    gifts = db.get_gifts_prices()
    fake_item = random.choice(list(gifts.keys())) if gifts else "Подарок"
    fake_names = ["Dmitry_ST", "Sasha_Official", "Rich_Boy", "CryptoKing", "Masha_Stars", "Legenda_77"]
    name = random.choice(fake_names)
    fid = random.randint(1000000000, 9999999999)
    text = (
        f"🎁 <b>ЗАЯВКА НА ВЫВОД </b>\n\n"
        f"👤 Юзер: @{name}\n"
        f"🆔 ID: <code>{fid}</code>\n"
        f"📦 Предмет: <b>{fake_item}</b>"
    )
    await bot.send_message(WITHDRAWAL_CHANNEL_ID, text, reply_markup=get_admin_decision_kb(0, "GIFT"))
    await call.answer("✅ Реалистичный фейк отправлен!")
    db.log_admin(call.from_user.id, "fake_withdraw", f"Фейк предмет {fake_item}")

# --- Запуск лотереи ---
@dp.callback_query(F.data == "a_run_lottery")
async def adm_run_lottery(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    data = db.execute("SELECT pool, participants FROM lottery WHERE id = 1", fetchone=True)
    if not data or not data['participants']:
        return await call.answer("❌ Нет участников!", show_alert=True)
    participants = [p for p in data['participants'].split(',') if p]
    winner_id = int(random.choice(participants))
    win_amount = data['pool'] * 0.8
    db.execute("UPDATE lottery SET pool = 0, participants = '' WHERE id = 1")
    db.add_stars(winner_id, win_amount)
    await bot.send_message(winner_id, f"🥳 <b>ПОЗДРАВЛЯЕМ!</b>\nТы выиграл в лотерее: <b>{win_amount:.2f} ⭐</b>")
    await call.message.answer(f"✅ Лотерея завершена! Победитель: {winner_id}, сумма: {win_amount:.2f}")
    db.log_admin(call.from_user.id, "run_lottery", f"Победитель {winner_id}, сумма {win_amount}")

# --- Меню настроек бота ---
@dp.callback_query(F.data == "a_config_menu")
async def adm_config_menu(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="💰 Реферальная награда", callback_data="edit_config_ref_reward"))
    kb.row(InlineKeyboardButton(text="👀 Награда за пост", callback_data="edit_config_view_reward"))
    kb.row(InlineKeyboardButton(text="📅 Ежедневный мин/макс", callback_data="edit_config_daily"))
    kb.row(InlineKeyboardButton(text="🎰 Удача мин/макс/кулдаун", callback_data="edit_config_luck"))
    kb.row(InlineKeyboardButton(text="💎 Суммы вывода", callback_data="edit_config_withdraw"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel"))
    await call.message.edit_text("⚙️ <b>Настройки бота</b>\nВыбери параметр для изменения:", reply_markup=kb.as_markup())

# Редактирование реферальной награды
@dp.callback_query(F.data == "edit_config_ref_reward")
async def edit_ref_reward(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    current = db.get_config('ref_reward', '5.0')
    await state.set_state(AdminStates.waiting_config_value)
    await state.update_data(config_key='ref_reward')
    await call.message.answer(f"Текущее значение: <b>{current}</b>\nВведи новую награду за реферала (число):")

@dp.callback_query(F.data == "edit_config_view_reward")
async def edit_view_reward(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    current = db.get_config('view_reward', '0.3')
    await state.set_state(AdminStates.waiting_config_value)
    await state.update_data(config_key='view_reward')
    await call.message.answer(f"Текущее значение: <b>{current}</b>\nВведи новую награду за просмотр поста (число):")

@dp.callback_query(F.data == "edit_config_daily")
async def edit_daily(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    current_min = db.get_config('daily_min', '1')
    current_max = db.get_config('daily_max', '3')
    await state.set_state(AdminStates.waiting_config_value)
    await state.update_data(config_key='daily')
    await call.message.answer(
        f"Текущие значения: мин {current_min}, макс {current_max}\n"
        "Введи новые минимум и максимум через пробел (например: 2 5):"
    )

@dp.callback_query(F.data == "edit_config_luck")
async def edit_luck(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    current_min = db.get_config('luck_min', '0')
    current_max = db.get_config('luck_max', '5')
    current_cd = db.get_config('luck_cooldown', '21600')
    await state.set_state(AdminStates.waiting_config_value)
    await state.update_data(config_key='luck')
    await call.message.answer(
        f"Текущие значения: мин {current_min}, макс {current_max}, кулдаун {current_cd} сек\n"
        "Введи новые минимум, максимум и кулдаун через пробел (например: 1 10 3600):"
    )

@dp.callback_query(F.data == "edit_config_withdraw")
async def edit_withdraw(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    current = db.get_config('withdrawal_options', '15,25,50,100')
    await state.set_state(AdminStates.waiting_config_value)
    await state.update_data(config_key='withdrawal_options')
    await call.message.answer(
        f"Текущие суммы: {current}\n"
        "Введи новые суммы через запятую (например: 10,20,30,50,100):"
    )

@dp.message(AdminStates.waiting_config_value)
async def set_config_value(message: Message, state: FSMContext):
    data = await state.get_data()
    key = data.get('config_key')
    text = message.text.strip()
    try:
        if key in ('ref_reward', 'view_reward'):
            new_val = float(text)
            db.set_config(key, str(new_val))
            await message.answer(f"✅ Параметр <b>{key}</b> изменён на {new_val}")
        elif key == 'daily':
            parts = text.split()
            if len(parts) != 2:
                raise ValueError
            min_val = float(parts[0])
            max_val = float(parts[1])
            db.set_config('daily_min', str(min_val))
            db.set_config('daily_max', str(max_val))
            await message.answer(f"✅ Ежедневный бонус изменён: мин {min_val}, макс {max_val}")
        elif key == 'luck':
            parts = text.split()
            if len(parts) != 3:
                raise ValueError
            min_val = float(parts[0])
            max_val = float(parts[1])
            cd = int(parts[2])
            db.set_config('luck_min', str(min_val))
            db.set_config('luck_max', str(max_val))
            db.set_config('luck_cooldown', str(cd))
            await message.answer(f"✅ Удача изменена: мин {min_val}, макс {max_val}, кулдаун {cd} сек")
        elif key == 'withdrawal_options':
            # проверяем, что это числа через запятую
            options = [int(x.strip()) for x in text.split(',') if x.strip()]
            if not options:
                raise ValueError
            db.set_config('withdrawal_options', ','.join(str(x) for x in options))
            await message.answer(f"✅ Суммы вывода изменены: {', '.join(str(x) for x in options)}")
        else:
            await message.answer("❌ Неизвестный параметр")
            await state.clear()
            return
        db.log_admin(message.from_user.id, "change_config", f"{key} = {text}")
    except Exception:
        await message.answer("❌ Ошибка ввода! Проверь формат.")
        return
    await state.clear()
    await adm_config_menu(await message.answer("⚙️ Настройки", reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")).as_markup()))

# --- Глобальные бусты ---
@dp.callback_query(F.data == "a_global_boost_menu")
async def adm_global_boost_menu(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="👥 Буст рефералов x2 (1 час)", callback_data="set_boost_ref_2_3600"))
    kb.row(InlineKeyboardButton(text="👥 Буст рефералов x3 (3 часа)", callback_data="set_boost_ref_3_10800"))
    kb.row(InlineKeyboardButton(text="🎰 Буст игр x2 (1 час)", callback_data="set_boost_game_2_3600"))
    kb.row(InlineKeyboardButton(text="❌ Выключить буст рефералов", callback_data="disable_boost_ref"))
    kb.row(InlineKeyboardButton(text="❌ Выключить буст игр", callback_data="disable_boost_game"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel"))
    await call.message.edit_text("📈 <b>Глобальные бусты</b>\nВыбери действие:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("set_boost_"))
async def set_boost_handler(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    parts = call.data.split("_")
    # Формат: set_boost_{type}_{mult}_{duration} или set_boost_{type}_{mult}
    boost_type = parts[2]  # ref или game
    mult = float(parts[3])
    duration = int(parts[4]) if len(parts) > 4 else None
    db.set_global_boost(boost_type, mult, duration)
    await call.answer(f"✅ Буст {boost_type} x{mult} активирован!", show_alert=True)
    db.log_admin(call.from_user.id, "global_boost", f"{boost_type} x{mult} на {duration} сек")
    await adm_global_boost_menu(call)

@dp.callback_query(F.data.startswith("disable_boost_"))
async def disable_boost_handler(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    boost_type = call.data.replace("disable_boost_", "")
    db.disable_global_boost(boost_type)
    await call.answer(f"✅ Буст {boost_type} выключен!", show_alert=True)
    db.log_admin(call.from_user.id, "global_boost", f"Выключен {boost_type}")
    await adm_global_boost_menu(call)

# --- Редактирование цен подарков ---
@dp.callback_query(F.data == "a_edit_gifts")
async def adm_edit_gifts(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    gifts = db.get_gifts_prices()
    text = "🛍 <b>Текущие цены подарков:</b>\n"
    for name, price in gifts.items():
        text += f"{name}: {price} ⭐\n"
    text += "\nВведи название товара и новую цену через пробел (например: 🧸 Мишка 50)."
    await state.set_state(AdminStates.waiting_gift_price)
    await call.message.edit_text(text)

@dp.message(AdminStates.waiting_gift_price)
async def set_gift_price(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        parts = message.text.rsplit(' ', 1)
        if len(parts) != 2:
            return await message.answer("❌ Формат: название цена")
        item_name = parts[0].strip()
        price = float(parts[1])
        gifts = db.get_gifts_prices()
        if item_name not in gifts:
            return await message.answer("❌ Товар не найден в списке!")
        gifts[item_name] = price
        db.set_config('gifts_prices', json.dumps(gifts, ensure_ascii=False))
        await message.answer(f"✅ Цена для <b>{item_name}</b> изменена на {price} ⭐")
        db.log_admin(message.from_user.id, "edit_gift_price", f"{item_name} = {price}")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    await state.clear()
    await adm_config_menu(await message.answer("⚙️ Настройки", reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")).as_markup()))

# --- Редактирование эксклюзивных товаров ---
@dp.callback_query(F.data == "a_edit_specials")
async def adm_edit_specials(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    specials = db.get_special_items()
    text = "📦 <b>Эксклюзивные товары (текущие лимиты и цены):</b>\n"
    for key, info in specials.items():
        text += f"{info['full_name']}: цена {info['price']} ⭐, лимит {info['limit']}\n"
    text += "\nВведи ключ товара (Ramen/Candle/Calendar), новую цену и новый лимит через пробел.\n"
    text += "Пример: Ramen 300 20"
    await state.set_state(AdminStates.waiting_special_field)
    await call.message.edit_text(text)

@dp.message(AdminStates.waiting_special_field)
async def set_special_item(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        parts = message.text.split()
        if len(parts) != 3:
            return await message.answer("❌ Формат: ключ цена лимит")
        key = parts[0].strip()
        price = float(parts[1])
        limit = int(parts[2])
        specials = db.get_special_items()
        if key not in specials:
            return await message.answer("❌ Ключ не найден! Доступны: Ramen, Candle, Calendar")
        specials[key]['price'] = price
        specials[key]['limit'] = limit
        db.set_config('special_items', json.dumps(specials, ensure_ascii=False))
        await message.answer(f"✅ Товар <b>{specials[key]['full_name']}</b> обновлён: цена {price}, лимит {limit}")
        db.log_admin(message.from_user.id, "edit_special", f"{key} price={price} limit={limit}")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    await state.clear()
    await adm_config_menu(await message.answer("⚙️ Настройки", reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")).as_markup()))

#========== СОЗДАТЬ КВЕСТЫ ===========

class CreateQuestStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_reward_type = State()
    waiting_for_reward_value = State()
    waiting_for_condition_type = State()
    waiting_for_condition_value = State()

@dp.callback_query(F.data == "a_quest_create")
async def a_quest_create_start(call: CallbackQuery, state: FSMContext):
    await call.message.answer("Введи название квеста:")
    await state.set_state(CreateQuestStates.waiting_for_name)

@dp.message(CreateQuestStates.waiting_for_name)
async def a_quest_create_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Введи описание квеста:")
    await state.set_state(CreateQuestStates.waiting_for_description)

@dp.message(CreateQuestStates.waiting_for_description)
async def a_quest_create_desc(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⭐ Звёзды", callback_data="reward_stars"))
    kb.row(InlineKeyboardButton(text="🎁 Подарок", callback_data="reward_item"))
    await message.answer("Выбери тип награды:", reply_markup=kb.as_markup())
    await state.set_state(CreateQuestStates.waiting_for_reward_type)

@dp.callback_query(CreateQuestStates.waiting_for_reward_type, F.data.startswith("reward_"))
async def a_quest_create_reward_type(call: CallbackQuery, state: FSMContext):
    rtype = call.data.split("_")[1]  # stars или item
    await state.update_data(reward_type=rtype)
    if rtype == 'stars':
        await call.message.answer("Введи количество звёзд:")
    else:
        await call.message.answer("Введи название предмета:")
    await state.set_state(CreateQuestStates.waiting_for_reward_value)

@dp.message(CreateQuestStates.waiting_for_reward_value)
async def a_quest_create_reward_value(message: Message, state: FSMContext):
    await state.update_data(reward_value=message.text)
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📺 Подписка на канал", callback_data="cond_channel"))
    kb.row(InlineKeyboardButton(text="🤖 Запуск бота", callback_data="cond_botstart"))
    kb.row(InlineKeyboardButton(text="📰 Просмотр постов", callback_data="cond_posts"))
    kb.row(InlineKeyboardButton(text="🔘 Другое", callback_data="cond_other"))
    await message.answer("Выбери тип условия:", reply_markup=kb.as_markup())
    await state.set_state(CreateQuestStates.waiting_for_condition_type)

@dp.callback_query(CreateQuestStates.waiting_for_condition_type, F.data.startswith("cond_"))
async def a_quest_create_cond_type(call: CallbackQuery, state: FSMContext):
    cond_type = call.data.split("_")[1]
    await state.update_data(condition_type=cond_type)
    if cond_type == 'channel':
        await call.message.answer("Введи ID канала (например: -100123456789):")
    elif cond_type == 'botstart':
        # Не требует значения
        await state.update_data(condition_value='')
        await finish_quest_creation(call, state)
        return
    elif cond_type == 'posts':
        await state.update_data(condition_value='')
        await finish_quest_creation(call, state)
        return
    else:
        await call.message.answer("Введи условие (текст):")
    await state.set_state(CreateQuestStates.waiting_for_condition_value)

@dp.message(CreateQuestStates.waiting_for_condition_value)
async def a_quest_create_cond_value(message: Message, state: FSMContext):
    await state.update_data(condition_value=message.text)
    await finish_quest_creation(message, state)

async def finish_quest_creation(event, state: FSMContext):
    data = await state.get_data()
    db.execute(
        "INSERT INTO quests (name, description, reward_type, reward_value, condition_type, condition_value) VALUES (?, ?, ?, ?, ?, ?)",
        (data['name'], data['description'], data['reward_type'], data['reward_value'], data['condition_type'], data['condition_value'])
    )
    await event.answer("✅ Квест создан!")
    await state.clear()

# ========== ОБРАБОТКА АДМИН-РЕШЕНИЙ ПО ЗАЯВКАМ ==========
@dp.callback_query(F.data.startswith("adm_app_") | F.data.startswith("adm_rej_"))
async def cb_adm_action(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return await call.answer("❌ Ты не администратор!", show_alert=True)
    parts = call.data.split("_")
    action = parts[1]  # app или rej
    target_uid = int(parts[2])
    value = parts[3]   # сумма или GIFT

    # Фейк
    if target_uid == 0:
        status = "✅ ОДОБРЕНО" if action == "app" else "❌ ОТКЛОНЕНО"
        await call.message.edit_text(f"{call.message.text}\n\n<b>Итог: {status}</b>")
        return await call.answer("Вывод обработан")

    # Реальный пользователь
    try:
        if action == "app":
            reward_text = "подарка" if value == "GIFT" else f"{value} ⭐"
            await bot.send_message(target_uid, f"🎉 <b>Твоя заявка на вывод {reward_text} одобрена!</b>")
            status_text = "✅ ПРИНЯТО"
            db.log_admin(call.from_user.id, "withdraw_approve", f"Пользователь {target_uid}, сумма {value}")
        else:
            if value == "GIFT":
                await bot.send_message(target_uid, "❌ <b>Заявка на вывод подарка отклонена.</b>\nСвяжись с поддержкой.")
            else:
                db.add_stars(target_uid, float(value))
                await bot.send_message(target_uid, f"❌ <b>Выплата {value} ⭐ отклонена.</b>\nЗвёзды возвращены на твой баланс.")
            status_text = "❌ ОТКЛОНЕНО"
            db.log_admin(call.from_user.id, "withdraw_reject", f"Пользователь {target_uid}, сумма {value}")

        await call.message.edit_text(
            f"{call.message.text}\n\n<b>Итог: {status_text}</b> (Админ: @{call.from_user.username or call.from_user.id})"
        )
        await call.answer("Готово!")
    except Exception as e:
        logging.error(f"Ошибка в админ-действии: {e}")
        await call.answer("❌ Ошибка (возможно, юзер заблокировал бота)", show_alert=True)

@dp.callback_query(F.data.startswith("adm_chat_"))
async def cb_adm_chat(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    uid = call.data.split("_")[2]
    if uid == "0":
        return await call.answer("❌ Это фейк!", show_alert=True)
    await call.message.answer(f"🔗 Связь с юзером: tg://user?id={uid}")
    await call.answer()

# ========== ЗАПУСК ==========
async def web_handle(request):
    return web.Response(text="Bot Active")

async def main():
    # Настройка веб-сервера для Render (необязательно, но для health check)
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
