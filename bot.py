"""
StarsForQuestion - ULTIMATE MONOLITH v9.0 (ПОВНА ВЕРСІЯ)
Абсолютно всі функції: економіка, реферали (з бонусом після активації), 
пости в канал, реалістичні фейки, P2P маркет, лотерея, дуелі, квести,
магазин з ексклюзивами, інвентар, глобальні бусти (адмін-абʼюзи),
повне налаштування через БД, логування адмінів, PostgreSQL для Render.
"""

import asyncio
import logging
import os
import random
import json
from datetime import datetime, timedelta
from typing import Optional, Any, Dict, List, Tuple

# База даних: підтримка SQLite та PostgreSQL
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


# ========== КОНФІГУРАЦІЯ З ОТОЧЕННЯ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задано!")

CHANNEL_ID = os.getenv("CHANNEL_ID", "-1003326584722")
raw_admins = os.getenv("ADMIN_IDS", "8364667153")
ADMIN_IDS = [int(id.strip()) for id in raw_admins.split(",") if id.strip()]
WITHDRAWAL_CHANNEL_ID = os.getenv("WITHDRAWAL_CHANNEL", "-1003891414947")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@Nft_top3")
PORT = int(os.environ.get("PORT", 10000))

# Вибір бази даних: PostgreSQL якщо задано DATABASE_URL, інакше SQLite
DATABASE_URL = os.getenv("DATABASE_URL")  # для Render PostgreSQL


# ========== БАЗА ДАНИХ (УНІВЕРСАЛЬНИЙ КЛАС) ==========
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
                # Таблиця користувачів
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
                        referred_by BIGINT
                    )
                """)
                # Інвентар
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
                # Квести
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS task_claims (
                        user_id BIGINT,
                        task_id TEXT,
                        PRIMARY KEY (user_id, task_id)
                    )
                """)
                # Промокоди
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
                # Стріки
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS daily_bonus (
                        user_id BIGINT PRIMARY KEY,
                        last_date TEXT,
                        streak INTEGER DEFAULT 0
                    )
                """)
                # Дуелі
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS active_duels (
                        creator_id BIGINT PRIMARY KEY,
                        amount REAL
                    )
                """)
                # Таблиця для збереження налаштувань (config)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS config (
                        key TEXT PRIMARY KEY,
                        value TEXT,
                        description TEXT
                    )
                """)
                # Таблиця логів адмінів
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS admin_logs (
                        id SERIAL PRIMARY KEY,
                        admin_id BIGINT,
                        action TEXT,
                        details TEXT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                # Заповнюємо config значеннями за замовчуванням, якщо порожньо
                default_config = {
                    'ref_reward': ('5.0', 'Нагорода за активного реферала (зірок)'),
                    'view_reward': ('0.3', 'Нагорода за перегляд посту'),
                    'daily_min': ('1', 'Мінімум щоденного бонусу'),
                    'daily_max': ('3', 'Максимум щоденного бонусу'),
                    'luck_min': ('0', 'Мінімум удачі'),
                    'luck_max': ('5', 'Максимум удачі'),
                    'luck_cooldown': ('21600', 'Кулдаун удачі (секунд)'),
                    'withdrawal_options': ('15,25,50,100', 'Доступні суми виведення через кому'),
                    'gifts_prices': ('{"🧸 Мишка":45,"❤️ Сердце":45,"🎁 Подарок":75,"🌹 Роза":75,"🍰 Тортик":150,"💐 Букет":150,"🚀 Ракета":150,"🍾 Шампанское":150,"🏆 Кубок":300,"💍 Колечко":300,"💎 Алмаз":300}', 'Ціни на подарунки (JSON)'),
                    'special_items': ('{"Ramen":{"price":250,"limit":25,"full_name":"🍜 Ramen"},"Candle":{"price":199,"limit":30,"full_name":"🕯 B-Day Candle"},"Calendar":{"price":320,"limit":18,"full_name":"🗓 Desk Calendar"}}', 'Ексклюзивні товари (JSON)'),
                }
                for key, (value, desc) in default_config.items():
                    cur.execute("INSERT INTO config (key, value, description) VALUES (%s, %s, %s) ON CONFLICT (key) DO NOTHING", (key, value, desc))
                # Глобальні бусти (зберігаються в config)
                cur.execute("INSERT INTO config (key, value, description) VALUES ('global_ref_mult', '1.0', 'Глобальний множник рефералів') ON CONFLICT DO NOTHING")
                cur.execute("INSERT INTO config (key, value, description) VALUES ('global_ref_until', '', 'Час закінчення глобального бусту рефералів (ISO)') ON CONFLICT DO NOTHING")
                cur.execute("INSERT INTO config (key, value, description) VALUES ('global_game_mult', '1.0', 'Глобальний множник виграшів в іграх') ON CONFLICT DO NOTHING")
                cur.execute("INSERT INTO config (key, value, description) VALUES ('global_game_until', '', 'Час закінчення глобального бусту ігор') ON CONFLICT DO NOTHING")

    def _init_sqlite(self):
        cursor = self.conn.cursor()
        # Таблиця користувачів
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
                referred_by INTEGER
            )
        """)
        # Інвентар
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
        # Квести
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_claims (
                user_id INTEGER,
                task_id TEXT,
                PRIMARY KEY (user_id, task_id)
            )
        """)
        # Промокоди
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
        # Стріки
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_bonus (
                user_id INTEGER PRIMARY KEY,
                last_date TEXT,
                streak INTEGER DEFAULT 0
            )
        """)
        # Дуелі
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS active_duels (
                creator_id INTEGER PRIMARY KEY,
                amount REAL
            )
        """)
        # Таблиця налаштувань config
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT,
                description TEXT
            )
        """)
        # Таблиця логів адмінів
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                action TEXT,
                details TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Заповнюємо config значеннями за замовчуванням
        default_config = {
            'ref_reward': ('5.0', 'Нагорода за активного реферала (зірок)'),
            'view_reward': ('0.3', 'Нагорода за перегляд посту'),
            'daily_min': ('1', 'Мінімум щоденного бонусу'),
            'daily_max': ('3', 'Максимум щоденного бонусу'),
            'luck_min': ('0', 'Мінімум удачі'),
            'luck_max': ('5', 'Максимум удачі'),
            'luck_cooldown': ('21600', 'Кулдаун удачі (секунд)'),
            'withdrawal_options': ('15,25,50,100', 'Доступні суми виведення через кому'),
            'gifts_prices': ('{"🧸 Мишка":45,"❤️ Сердце":45,"🎁 Подарок":75,"🌹 Роза":75,"🍰 Тортик":150,"💐 Букет":150,"🚀 Ракета":150,"🍾 Шампанское":150,"🏆 Кубок":300,"💍 Колечко":300,"💎 Алмаз":300}', 'Ціни на подарунки (JSON)'),
            'special_items': ('{"Ramen":{"price":250,"limit":25,"full_name":"🍜 Ramen"},"Candle":{"price":199,"limit":30,"full_name":"🕯 B-Day Candle"},"Calendar":{"price":320,"limit":18,"full_name":"🗓 Desk Calendar"}}', 'Ексклюзивні товари (JSON)'),
        }
        for key, (value, desc) in default_config.items():
            cursor.execute("INSERT OR IGNORE INTO config (key, value, description) VALUES (?, ?, ?)", (key, value, desc))
        # Глобальні бусти
        cursor.execute("INSERT OR IGNORE INTO config (key, value, description) VALUES ('global_ref_mult', '1.0', 'Глобальний множник рефералів')")
        cursor.execute("INSERT OR IGNORE INTO config (key, value, description) VALUES ('global_ref_until', '', 'Час закінчення глобального бусту рефералів (ISO)')")
        cursor.execute("INSERT OR IGNORE INTO config (key, value, description) VALUES ('global_game_mult', '1.0', 'Глобальний множник виграшів в іграх')")
        cursor.execute("INSERT OR IGNORE INTO config (key, value, description) VALUES ('global_game_until', '', 'Час закінчення глобального бусту ігор')")
        self.conn.commit()

    def execute(self, query: str, params: tuple = (), fetch: bool = False, fetchone: bool = False):
        """Універсальний метод виконання запитів (працює і з PostgreSQL, і з SQLite)"""
        if self.use_postgres:
            # Замінюємо ? на %s для сумісності
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

    # ========== МЕТОДИ ДЛЯ РОБОТИ З КОРИСТУВАЧАМИ ==========
    def get_user(self, user_id: int) -> Optional[Dict]:
        row = self.execute("SELECT * FROM users WHERE user_id = ?", (user_id,), fetchone=True)
        return dict(row) if row else None

    def create_user(self, user_id: int, username: str, first_name: str, referred_by: int = None):
        ref_code = f"ref{user_id}"
        self.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name, ref_code, referred_by) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, first_name, ref_code, referred_by)
        )

    def add_stars(self, user_id: int, amount: float):
        """Додає зірки, оновлює total_earned та активує реферала при досягненні 1.0"""
        if amount == 0:
            return
        # Якщо додаємо позитивну суму, враховуємо персональний буст
        if amount > 0:
            user = self.get_user(user_id)
            if user:
                boost = user.get('ref_boost', 1.0)
                amount = amount * boost
            # Оновлюємо зірки
            self.execute("UPDATE users SET stars = stars + ? WHERE user_id = ?", (amount, user_id))
            # Оновлюємо total_earned та перевіряємо активацію
            self.update_user_activity(user_id, amount)
        else:
            # Витрата – просто знімаємо зірки
            self.execute("UPDATE users SET stars = stars + ? WHERE user_id = ?", (amount, user_id))

    def update_user_activity(self, user_id: int, earned: float):
        """Оновлює total_earned та перевіряє активацію реферала"""
        self.execute("UPDATE users SET total_earned = total_earned + ? WHERE user_id = ?", (earned, user_id))
        user = self.get_user(user_id)
        if user and user['total_earned'] >= 1.0 and not user['is_active']:
            self.execute("UPDATE users SET is_active = 1 WHERE user_id = ?", (user_id,))
            # Нарахувати бонус рефереру, якщо є
            if user['referred_by']:
                ref_reward = float(self.get_config('ref_reward', 5.0))
                global_mult = self.get_global_boost('ref')
                self.add_stars(user['referred_by'], ref_reward * global_mult)

    # ========== РОБОТА З КОНФІГОМ ==========
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

    # ========== ГЛОБАЛЬНІ БУСТИ ==========
    def get_global_boost(self, boost_type: str) -> float:
        """Повертає множник глобального бусту (якщо активний)"""
        mult_key = f'global_{boost_type}_mult'
        until_key = f'global_{boost_type}_until'
        mult = float(self.get_config(mult_key, 1.0))
        until_str = self.get_config(until_key, '')
        if until_str:
            try:
                until = datetime.fromisoformat(until_str)
                if datetime.utcnow() > until:
                    # Буст прострочений – скидаємо
                    self.set_config(mult_key, '1.0')
                    self.set_config(until_key, '')
                    return 1.0
            except:
                pass
        return mult

    def set_global_boost(self, boost_type: str, multiplier: float, duration_seconds: int = None):
        """Активувати глобальний буст на певний час (якщо duration задано) або назавжди"""
        self.set_config(f'global_{boost_type}_mult', str(multiplier))
        if duration_seconds:
            until = (datetime.utcnow() + timedelta(seconds=duration_seconds)).isoformat()
            self.set_config(f'global_{boost_type}_until', until)
        else:
            self.set_config(f'global_{boost_type}_until', '')

    def disable_global_boost(self, boost_type: str):
        self.set_config(f'global_{boost_type}_mult', '1.0')
        self.set_config(f'global_{boost_type}_until', '')

    # ========== ЛОГИ АДМІНІВ ==========
    def log_admin(self, admin_id: int, action: str, details: str = ''):
        self.execute("INSERT INTO admin_logs (admin_id, action, details) VALUES (?, ?, ?)", (admin_id, action, details))


# ========== ІНІЦІАЛІЗАЦІЯ БД ==========
db = Database()


# ========== СОСТОЯННЯ FSM ==========
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


# ========== ІНІЦІАЛІЗАЦІЯ БОТА ==========
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())


# ========== ДОПОМІЖНІ ФУНКЦІЇ ==========
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
        InlineKeyboardButton(text="🎯 Квести", callback_data="tasks"),
        InlineKeyboardButton(text="⚔️ Дуель", callback_data="duel_menu"),
        InlineKeyboardButton(text="👥 Друзі", callback_data="referrals")
    )
    builder.row(
        InlineKeyboardButton(text="🎰 Удача", callback_data="luck"),
        InlineKeyboardButton(text="📆 Щоденно", callback_data="daily"),
        InlineKeyboardButton(text="🎟 Лотерея", callback_data="lottery")
    )
    builder.row(
        InlineKeyboardButton(text="🛒 Магазин", callback_data="shop"),
        InlineKeyboardButton(text="🏪 P2P Маркет", callback_data="p2p_market"),
        InlineKeyboardButton(text="🎒 Інвентар", callback_data="inventory_0")
    )
    builder.row(
        InlineKeyboardButton(text="🏆 ТОП", callback_data="top"),
        InlineKeyboardButton(text="👤 Профіль", callback_data="profile"),
        InlineKeyboardButton(text="🎁 Промокод", callback_data="use_promo")
    )
    builder.row(
        InlineKeyboardButton(text="💸 Вивести", callback_data="withdraw")
    )
    if uid in ADMIN_IDS:
        builder.row(InlineKeyboardButton(text="👑 Адмін Панель", callback_data="admin_panel"))
    return builder.as_markup()

def get_admin_decision_kb(uid: int, amount: Any) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Прийняти", callback_data=f"adm_app_{uid}_{amount}"),
        InlineKeyboardButton(text="❌ Відхилити", callback_data=f"adm_rej_{uid}_{amount}")
    )
    builder.row(InlineKeyboardButton(text="✉️ Написати в ЛС", callback_data=f"adm_chat_{uid}"))
    return builder.as_markup()


# ========== ОБРОБНИКИ КОРИСТУВАЧІВ ==========
@dp.message(CommandStart())
async def cmd_start(message: Message):
    args = message.text.split()
    uid = message.from_user.id
    referred_by = None

    # Перевірка на реферальне посилання
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
                    InlineKeyboardButton(text="🤝 Прийняти виклик (5.0 ⭐)", callback_data=f"accept_duel_{creator_id}"),
                    InlineKeyboardButton(text="❌ Відмова", callback_data="menu")
                ).as_markup()
                await message.answer(f"⚔️ Гравець ID:{creator_id} викликає тебе на дуель!", reply_markup=kb)
                return

    # Створення користувача, якщо новий
    user = db.get_user(uid)
    if not user:
        db.create_user(uid, message.from_user.username or "", message.from_user.first_name or "", referred_by)
        if referred_by:
            try:
                await bot.send_message(referred_by, "👥 У вас новий реферал! Він отримає бонус, коли заробить перші 1.0 ⭐.")
            except:
                pass

    await message.answer(
        f"👋 Привіт, <b>{message.from_user.first_name}</b>!\n\n"
        "💎 <b>StarsForQuestion</b> — місце, де твоя активність перетворюється на Зірки.\n\n"
        "🎯 Виконуй завдання, крути удачу і забирай подарунки!",
        reply_markup=get_main_kb(uid)
    )

@dp.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery):
    await call.message.edit_text("⭐ <b>Головне меню</b>", reply_markup=get_main_kb(call.from_user.id))

@dp.callback_query(F.data == "profile")
async def cb_profile(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    if not u:
        return await call.answer("❌ Помилка: вас немає в базі. Напишіть /start", show_alert=True)
    text = (
        f"👤 <b>Профіль</b>\n\n"
        f"🆔 ID: <code>{u['user_id']}</code>\n"
        f"⭐ Баланс: <b>{u['stars']:.2f} ⭐</b>\n"
        f"👥 Рефералів: {u['referrals']}\n"
        f"📈 Всього зароблено: {u['total_earned']:.2f} ⭐\n"
        f"⚡ Персональний буст: x{u['ref_boost']:.1f}"
    )
    kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu")).as_markup()
    await call.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data == "referrals")
async def cb_referrals(call: CallbackQuery):
    u = db.get_user(call.from_user.id)
    if not u:
        return
    bot_username = (await bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start={u['ref_code']}"
    ref_reward = float(db.get_config('ref_reward', 5.0))
    text = (
        f"👥 <b>Реферали</b>\n\n"
        f"За активного друга (заробив ≥1 ⭐): <b>{ref_reward} ⭐</b>\n\n"
        f"🔗 Твоя реферальна посилання:\n<code>{ref_link}</code>"
    )
    kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu")).as_markup()
    await call.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data == "daily")
async def cb_daily(call: CallbackQuery):
    uid = call.from_user.id
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    # Отримуємо поточний стрік
    row = db.execute("SELECT last_date, streak FROM daily_bonus WHERE user_id = ?", (uid,), fetchone=True)
    if row:
        last_date = datetime.strptime(row['last_date'], "%Y-%m-%d")
        delta = (now.date() - last_date.date()).days
        if delta == 0:
            return await call.answer("❌ Бонус вже отримано! Приходь завтра.", show_alert=True)
        elif delta == 1:
            new_streak = min(row['streak'] + 1, 7)
        else:
            new_streak = 1
        db.execute("UPDATE daily_bonus SET last_date = ?, streak = ? WHERE user_id = ?", (today_str, new_streak, uid))
    else:
        new_streak = 1
        db.execute("INSERT INTO daily_bonus (user_id, last_date, streak) VALUES (?, ?, ?)", (uid, today_str, new_streak))

    # Розмір бонусу: 0.1 * стрік (наприклад)
    reward = round(0.1 * new_streak, 2)
    db.add_stars(uid, reward)
    await call.answer(f"✅ День {new_streak}! Отримано: {reward} ⭐", show_alert=True)
    await call.message.edit_text("⭐ <b>Головне меню</b>", reply_markup=get_main_kb(uid))

@dp.callback_query(F.data == "luck")
async def cb_luck(call: CallbackQuery):
    uid = call.from_user.id
    user = db.get_user(uid)
    now = datetime.now()
    cooldown = int(db.get_config('luck_cooldown', 21600))
    if user['last_luck']:
        try:
            last = datetime.fromisoformat(user['last_luck'])
            if (now - last).total_seconds() < cooldown:
                remaining = int(cooldown - (now - last).total_seconds())
                minutes = remaining // 60
                return await call.answer(f"⏳ Зачекайте {minutes} хв.", show_alert=True)
        except:
            pass
    luck_min = float(db.get_config('luck_min', 0))
    luck_max = float(db.get_config('luck_max', 5))
    win = round(random.uniform(luck_min, luck_max), 2)
    # Враховуємо глобальний буст ігор
    game_boost = db.get_global_boost('game')
    win *= game_boost
    db.add_stars(uid, win)
    db.execute("UPDATE users SET last_luck = ? WHERE user_id = ?", (now.isoformat(), uid))
    await call.answer(f"🎰 +{win:.2f} ⭐", show_alert=True)
    await call.message.edit_text("⭐ <b>Головне меню</b>", reply_markup=get_main_kb(uid))

# ========== КВЕСТИ ==========
@dp.callback_query(F.data == "tasks")
async def cb_tasks(call: CallbackQuery):
    uid = call.from_user.id
    # Активні реферали (ті, хто заробив ≥1)
    row = db.execute(
        "SELECT COUNT(*) as cnt FROM users WHERE referred_by = ? AND total_earned >= 1.0",
        (uid,), fetchone=True
    )
    active_refs = row['cnt'] if row else 0
    # Кількість куплених лотерейних білетів
    row = db.execute(
        "SELECT COUNT(*) as cnt FROM lottery_history WHERE user_id = ?",
        (uid,), fetchone=True
    )
    tickets_bought = row['cnt'] if row else 0

    kb = InlineKeyboardBuilder()
    status1 = "✅ Готово" if active_refs >= 3 else f"⏳ {active_refs}/3"
    kb.row(InlineKeyboardButton(text=f"📈 Стахановець: {status1}", callback_data="claim_task_1"))
    status2 = "✅ Готово" if tickets_bought >= 5 else f"⏳ {tickets_bought}/5"
    kb.row(InlineKeyboardButton(text=f"🎰 Ловець удачі: {status2}", callback_data="claim_task_2"))
    kb.row(InlineKeyboardButton(text="📸 Надіслати відео-відгук (100 ⭐)", url=f"https://t.me/{SUPPORT_USERNAME.replace('@','')}"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))

    await call.message.edit_text(
        "🎯 <b>ЗАВДАННЯ ТА КВЕСТИ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "💰 Забирай нагороди за активність!\n"
        "Нагороди нараховуються миттєво.",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data.startswith("claim_task_"))
async def claim_task(call: CallbackQuery):
    task_num = call.data.split("_")[2]
    uid = call.from_user.id

    # Перевірка чи вже виконано
    check = db.execute(
        "SELECT 1 FROM task_claims WHERE user_id = ? AND task_id = ?",
        (uid, task_num), fetchone=True
    )
    if check:
        return await call.answer("❌ Ви вже отримали нагороду за цей квест!", show_alert=True)

    if task_num == "1":
        row = db.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE referred_by = ? AND total_earned >= 1.0",
            (uid,), fetchone=True
        )
        active_refs = row['cnt'] if row else 0
        if active_refs < 3:
            return await call.answer("❌ Потрібно 3 активних реферала!", show_alert=True)
        reward = 15.0
    elif task_num == "2":
        row = db.execute(
            "SELECT COUNT(*) as cnt FROM lottery_history WHERE user_id = ?",
            (uid,), fetchone=True
        )
        tickets_bought = row['cnt'] if row else 0
        if tickets_bought < 5:
            return await call.answer("❌ Потрібно купити 5 білетів!", show_alert=True)
        reward = 3.0
    else:
        return

    db.execute("INSERT INTO task_claims (user_id, task_id) VALUES (?, ?)", (uid, task_num))
    db.add_stars(uid, reward)
    await call.answer(f"✅ Нараховано {reward} ⭐!", show_alert=True)
    await cb_tasks(call)

# ========== ДУЕЛІ ==========
@dp.callback_query(F.data == "duel_menu")
async def cb_duel_menu(call: CallbackQuery):
    uid = call.from_user.id
    bot_username = (await bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=duel{uid}"
    text = (
        "⚔️ <b>ДУЕЛЬНИЙ КЛУБ</b>\n━━━━━━━━━━━━━━\n"
        "Ставка: <b>5.0 ⭐</b>\n"
        "Переможець отримує: <b>9.0 ⭐</b>\n\n"
        "Відправ посилання другу, щоб викликати його на бій:"
    )
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📨 Надіслати другу", switch_inline_query=link))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text(f"{text}\n<code>{link}</code>", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("accept_duel_"))
async def cb_accept_duel(call: CallbackQuery):
    opponent_id = call.from_user.id
    creator_id = int(call.data.split("_")[2])
    if opponent_id == creator_id:
        return await call.answer("❌ Не можна грати з самим собою!", show_alert=True)
    user = db.get_user(opponent_id)
    if not user or user['stars'] < 5.0:
        return await call.answer("❌ Недостатньо ⭐ для ставки!", show_alert=True)
    db.add_stars(opponent_id, -5.0)
    msg = await call.message.answer("🎲 Кидаємо кості...")
    dice = await msg.answer_dice("🎲")
    await asyncio.sleep(3.5)
    winner_id = creator_id if dice.dice.value <= 3 else opponent_id
    db.add_stars(winner_id, 9.0)
    await call.message.answer(
        f"🎰 Випало <b>{dice.dice.value}</b>!\n"
        f"👑 Переможець: <a href='tg://user?id={winner_id}'>Гравець</a>\n"
        f"Зараховано: <b>9.0 ⭐</b>"
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
        "🎟 <b>ЗІРКОВА ЛОТЕРЕЯ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"💰 Поточний банк: <b>{data['pool']:.2f} ⭐</b>\n"
        f"👥 Учасників: <b>{count}</b>\n"
        f"🎫 Ціна квитка: <b>2.0 ⭐</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Переможець забирає 80% банку. Розіграш відбувається автоматично при запуску адміном!</i>"
    )
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="💎 Купити квиток", callback_data="buy_ticket"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text(text, reply_markup=kb.as_markup())

@dp.callback_query(F.data == "buy_ticket")
async def cb_buy_ticket(call: CallbackQuery):
    uid = call.from_user.id
    user = db.get_user(uid)
    if not user or user['stars'] < 2:
        return await call.answer("❌ Недостатньо зірок (потрібно 2.0)", show_alert=True)
    db.add_stars(uid, -2)
    db.execute("UPDATE lottery SET pool = pool + 2, participants = participants || ? WHERE id = 1", (f"{uid},",))
    db.execute("INSERT INTO lottery_history (user_id) VALUES (?)", (uid,))
    await call.answer("✅ Квиток куплено!", show_alert=True)
    await cb_lottery(call)

# ========== ТОП ==========
@dp.callback_query(F.data == "top")
async def cb_top(call: CallbackQuery):
    rows = db.execute(
        "SELECT first_name, stars FROM users ORDER BY stars DESC LIMIT 10",
        fetch=True
    )
    text = "🏆 <b>ТОП-10 МАГНАТІВ</b>\n━━━━━━━━━━━━━━━━━━\n"
    for i, row in enumerate(rows, 1):
        name = row['first_name'][:3] + "***" if row['first_name'] else "***"
        stars = float(row['stars'])
        text += f"{i}. {name} — <b>{stars:.1f} ⭐</b>\n"
    kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu")).as_markup()
    await call.message.edit_text(text, reply_markup=kb)

# ========== ВИВЕДЕННЯ ==========
@dp.callback_query(F.data == "withdraw")
async def cb_withdraw_select(call: CallbackQuery):
    uid = call.from_user.id
    user = db.get_user(uid)
    if not user or user['stars'] < 15:
        return await call.answer("❌ Мінімум 15 ⭐", show_alert=True)
    options = db.get_withdrawal_options()
    kb = InlineKeyboardBuilder()
    for opt in options:
        if user['stars'] >= opt:
            kb.row(InlineKeyboardButton(text=f"💎 {opt} ⭐", callback_data=f"wd_run_{opt}"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text("Оберіть суму:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("wd_run_"))
async def cb_wd_execute(call: CallbackQuery):
    amt = float(call.data.split("_")[2])
    uid = call.from_user.id
    user = db.get_user(uid)
    if not user or user['stars'] < amt:
        return await call.answer("❌ Недостатньо ⭐", show_alert=True)
    db.add_stars(uid, -amt)
    name = mask_name(call.from_user.username or call.from_user.first_name)
    await bot.send_message(
        WITHDRAWAL_CHANNEL_ID,
        f"📥 <b>НОВА ЗАЯВКА</b>\n\n👤 Юзер: @{name}\n🆔 ID: <code>{uid}</code>\n💎 Сума: <b>{amt} ⭐</b>",
        reply_markup=get_admin_decision_kb(uid, amt)
    )
    await call.message.edit_text("✅ Заявку відправлено!", reply_markup=get_main_kb(uid))

# ========== МАГАЗИН ТА ІНВЕНТАР ==========
@dp.callback_query(F.data == "shop")
async def cb_shop_menu(call: CallbackQuery):
    gifts = db.get_gifts_prices()
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="💎 ЕКСКЛЮЗИВНІ ТОВАРИ", callback_data="special_shop"))
    kb.row(InlineKeyboardButton(text="⚡ Буст рефералів +0.1 (50 ⭐)", callback_data="buy_boost_01"))
    for item, price in gifts.items():
        kb.add(InlineKeyboardButton(text=f"{item} {price}⭐", callback_data=f"buy_g_{item}"))
    kb.adjust(1, 1, 2)
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text(
        "✨ <b>МАГАЗИН</b>\n\n"
        "Звичайні подарунки доступні завжди, а в <b>Ексклюзивному відділі</b> товари обмежені за кількістю!",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data == "buy_boost_01")
async def buy_boost(call: CallbackQuery):
    uid = call.from_user.id
    user = db.get_user(uid)
    if not user or user['stars'] < 50:
        return await call.answer("❌ Потрібно 50 ⭐", show_alert=True)
    db.add_stars(uid, -50)
    db.execute("UPDATE users SET ref_boost = ref_boost + 0.1 WHERE user_id = ?", (uid,))
    await call.answer("🚀 Буст куплено! Тепер ти отримуєш більше.", show_alert=True)

@dp.callback_query(F.data.startswith("buy_g_"))
async def process_gift_buy(call: CallbackQuery):
    item_name = call.data.replace("buy_g_", "")
    gifts = db.get_gifts_prices()
    price = gifts.get(item_name)
    if not price:
        return await call.answer("❌ Товар не знайдено", show_alert=True)
    uid = call.from_user.id
    user = db.get_user(uid)
    if not user or user['stars'] < price:
        return await call.answer(f"❌ Недостатньо зірок! Потрібно {price} ⭐", show_alert=True)
    db.add_stars(uid, -price)
    # Додаємо в інвентар
    existing = db.execute(
        "SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?",
        (uid, item_name), fetchone=True
    )
    if existing:
        db.execute("UPDATE inventory SET quantity = quantity + 1 WHERE user_id = ? AND item_name = ?", (uid, item_name))
    else:
        db.execute("INSERT INTO inventory (user_id, item_name, quantity) VALUES (?, ?, 1)", (uid, item_name))
    await call.answer(f"✅ Ви купили {item_name}!", show_alert=True)

@dp.callback_query(F.data.startswith("inventory_"))
async def cb_inventory_logic(call: CallbackQuery):
    parts = call.data.split("_")
    page = int(parts[1]) if len(parts) > 1 else 0
    uid = call.from_user.id
    items = db.execute(
        "SELECT item_name, quantity FROM inventory WHERE user_id = ?",
        (uid,), fetch=True
    )
    if not items:
        kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu")).as_markup()
        return await call.message.edit_text("🎒 <b>Твій інвентар порожній.</b>\nКупи щось у магазині!", reply_markup=kb)

    ITEMS_PER_PAGE = 5
    total_pages = (len(items) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    current = items[start:end]

    text = f"🎒 <b>ТВІЙ ІНВЕНТАР</b> (Стор. {page+1}/{total_pages})\n\nНатисни на предмет, щоб вивести його:"
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
    kb.row(InlineKeyboardButton(text="🎁 Отримати як подарунок", callback_data=f"confirm_out_{item}"))
    # Якщо це ексклюзивний товар – дозволити продаж на P2P
    if any(info['full_name'] == item for info in specials.values()):
        kb.row(InlineKeyboardButton(text="💰 Виставити на P2P Маркет", callback_data=f"sell_p2p_{item}"))
    kb.row(InlineKeyboardButton(text="❌ Скасувати", callback_data="inventory_0"))
    await call.message.edit_text(f"Ви обрали: <b>{item}</b>\nЩо бажаєте зробити?", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("confirm_out_"))
async def cb_final_out(call: CallbackQuery):
    item = call.data.replace("confirm_out_", "")
    uid = call.from_user.id
    username = call.from_user.username or "User"
    name_masked = mask_name(call.from_user.first_name)

    # Перевіряємо наявність
    res = db.execute(
        "SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?",
        (uid, item), fetchone=True
    )
    if not res or res['quantity'] <= 0:
        return await call.answer("❌ Предмет не знайдено!", show_alert=True)

    # Видаляємо 1 шт
    if res['quantity'] > 1:
        db.execute("UPDATE inventory SET quantity = quantity - 1 WHERE user_id = ? AND item_name = ?", (uid, item))
    else:
        db.execute("DELETE FROM inventory WHERE user_id = ? AND item_name = ?", (uid, item))

    await bot.send_message(
        WITHDRAWAL_CHANNEL_ID,
        f"🎁 <b>ЗАЯВКА НА ВИВЕДЕННЯ</b>\n\n"
        f"👤 Юзер: @{username}\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📦 Предмет: <b>{item}</b>",
        reply_markup=get_admin_decision_kb(uid, "GIFT")
    )
    await call.message.edit_text(
        f"✅ Заявку на виведення <b>{item}</b> надіслано!\nОчікуйте повідомлення від адміністратора.",
        reply_markup=get_main_kb(uid)
    )

# ========== ЕКСКЛЮЗИВНИЙ МАГАЗИН ==========
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
            text = f"{info['full_name']} — {info['price']} ⭐ (Залишилось: {left})"
            callback = f"buy_t_{key}"
        else:
            text = f"{info['full_name']} — 🚫 РОЗПРОДАНО"
            callback = "sold_out"
        kb.row(InlineKeyboardButton(text=text, callback_data=callback))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="shop"))
    await call.message.edit_text(
        "🛒 <b>ЕКСКЛЮЗИВНІ ТОВАРИ</b>\n\n"
        "<i>Коли ліміт вичерпано, товар можна купити тільки у гравців на P2P Ринку!</i>",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data == "sold_out")
async def cb_sold_out(call: CallbackQuery):
    await call.answer("❌ Цей товар закінчився в магазині! Шукайте його на P2P.", show_alert=True)

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
        return await call.answer("❌ Недостатньо зірок!", show_alert=True)

    # Перевірка ліміту
    sold = db.execute(
        "SELECT SUM(quantity) as total FROM inventory WHERE item_name = ?",
        (info['full_name'],), fetchone=True
    )
    sold_cnt = sold['total'] if sold and sold['total'] else 0
    if sold_cnt >= info['limit']:
        return await call.answer("❌ Ліміт вичерпано!", show_alert=True)

    db.add_stars(uid, -info['price'])
    # Додаємо в інвентар
    existing = db.execute(
        "SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?",
        (uid, info['full_name']), fetchone=True
    )
    if existing:
        db.execute("UPDATE inventory SET quantity = quantity + 1 WHERE user_id = ? AND item_name = ?", (uid, info['full_name']))
    else:
        db.execute("INSERT INTO inventory (user_id, item_name, quantity) VALUES (?, ?, 1)", (uid, info['full_name']))
    await call.answer(f"✅ {info['full_name']} куплено!", show_alert=True)
    await cb_special_shop(call)

# ========== P2P МАРКЕТ ==========
@dp.callback_query(F.data == "p2p_market")
async def cb_p2p_market(call: CallbackQuery):
    items = db.execute("SELECT id, seller_id, item_name, price FROM marketplace", fetch=True)
    text = "🏪 <b>P2P МАРКЕТ</b>\n\nТут можна перекупити ексклюзиви у гравців.\n"
    if not items:
        text += "\n<i>Лотів поки немає.</i>"
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
    await call.message.answer(f"💰 Введіть ціну в ⭐, за яку хочете продати <b>{item_name}</b>:")

@dp.message(P2PSaleStates.waiting_for_price)
async def process_p2p_sale_price(message: Message, state: FSMContext):
    data = await state.get_data()
    item_name = data.get("sell_item")
    uid = message.from_user.id
    if not message.text.isdigit():
        return await message.answer("❌ Введіть ціну числом!")
    price = int(message.text)
    if price <= 0:
        return await message.answer("❌ Ціна повинна бути більше 0!")

    # Перевіряємо наявність предмета
    res = db.execute(
        "SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?",
        (uid, item_name), fetchone=True
    )
    if not res or res['quantity'] <= 0:
        await state.clear()
        return await message.answer("❌ У вас немає цього предмета!")

    # Забираємо предмет
    if res['quantity'] > 1:
        db.execute("UPDATE inventory SET quantity = quantity - 1 WHERE user_id = ? AND item_name = ?", (uid, item_name))
    else:
        db.execute("DELETE FROM inventory WHERE user_id = ? AND item_name = ?", (uid, item_name))

    # Виставляємо на маркет
    db.execute("INSERT INTO marketplace (seller_id, item_name, price) VALUES (?, ?, ?)", (uid, item_name, price))
    await message.answer(f"✅ Предмет <b>{item_name}</b> виставлено на P2P Маркет за {price} ⭐")
    await state.clear()

@dp.callback_query(F.data.startswith("buy_p2p_"))
async def cb_buy_p2p(call: CallbackQuery):
    order_id = int(call.data.split("_")[2])
    buyer_id = call.from_user.id
    order = db.execute("SELECT * FROM marketplace WHERE id = ?", (order_id,), fetchone=True)
    if not order:
        return await call.answer("❌ Товар вже продано!", show_alert=True)
    if order['seller_id'] == buyer_id:
        return await call.answer("❌ Свій товар купити не можна!", show_alert=True)
    buyer = db.get_user(buyer_id)
    if not buyer or buyer['stars'] < order['price']:
        return await call.answer("❌ Недостатньо ⭐", show_alert=True)

    # Списати з покупця, нарахувати продавцю (комісія 10%)
    db.add_stars(buyer_id, -order['price'])
    seller_income = order['price'] * 0.9
    db.add_stars(order['seller_id'], seller_income)

    # Додати предмет покупцю
    existing = db.execute(
        "SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?",
        (buyer_id, order['item_name']), fetchone=True
    )
    if existing:
        db.execute("UPDATE inventory SET quantity = quantity + 1 WHERE user_id = ? AND item_name = ?", (buyer_id, order['item_name']))
    else:
        db.execute("INSERT INTO inventory (user_id, item_name, quantity) VALUES (?, ?, 1)", (buyer_id, order['item_name']))

    # Видалити лот
    db.execute("DELETE FROM marketplace WHERE id = ?", (order_id,))

    await call.answer(f"✅ Успішно куплено {order['item_name']}!", show_alert=True)
    await cb_p2p_market(call)

# ========== ПРОМОКОДИ ==========
@dp.callback_query(F.data == "use_promo")
async def promo_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(PromoStates.waiting_for_code)
    await call.message.answer("⌨️ Введіть промокод:")

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
        return await message.answer("❌ Ви вже активували цей промокод!")

    promo = db.execute(
        "SELECT * FROM promo WHERE code = ? AND uses > 0",
        (code,), fetchone=True
    )
    if not promo:
        await state.clear()
        return await message.answer("❌ Код невірний або закінчилися активації.")

    # Зменшуємо ліміт використань
    db.execute("UPDATE promo SET uses = uses - 1 WHERE code = ?", (code,))
    db.execute("INSERT INTO promo_history (user_id, code) VALUES (?, ?)", (uid, code))

    if promo['reward_type'] == 'stars':
        db.add_stars(uid, float(promo['reward_value']))
        await message.answer(f"✅ Активовано! +{promo['reward_value']} ⭐")
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
        await message.answer(f"✅ Активовано! Отримано предмет: {item}")
    await state.clear()

# ========== АДМІН ПАНЕЛЬ ==========
@dp.callback_query(F.data == "admin_panel")
async def cb_admin_panel(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return await call.answer("❌ Немає доступу!", show_alert=True)
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="📢 Розсилка", callback_data="a_broadcast"),
        InlineKeyboardButton(text="🎁 Створити Промо", callback_data="a_create_promo")
    )
    kb.row(
        InlineKeyboardButton(text="📢 Пост в КАНАЛ", callback_data="a_post_chan"),
        InlineKeyboardButton(text="🎭 Фейк Заявка", callback_data="a_fake_gen")
    )
    kb.row(
        InlineKeyboardButton(text="💎 Видати ⭐", callback_data="a_give_stars"),
        InlineKeyboardButton(text="⛔ Стоп Лотерея 🎰", callback_data="a_run_lottery")
    )
    kb.row(
        InlineKeyboardButton(text="⚙️ Налаштування бота", callback_data="a_config_menu"),
        InlineKeyboardButton(text="📈 Глобальні бусти", callback_data="a_global_boost_menu")
    )
    kb.row(
        InlineKeyboardButton(text="🛍 Ціни магазину", callback_data="a_edit_gifts"),
        InlineKeyboardButton(text="📦 Ліміти ексклюзивів", callback_data="a_edit_specials")
    )
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
    await call.message.edit_text("👑 <b>АДМІН-МЕНЮ</b>", reply_markup=kb.as_markup())

# --- Розсилка ---
@dp.callback_query(F.data == "a_broadcast")
async def adm_broadcast_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_broadcast_msg)
    await call.message.edit_text(
        "📢 <b>РОЗСИЛКА КОРИСТУВАЧАМ</b>\n\n"
        "Надішліть повідомлення (текст, фото, відео), яке хочете розіслати всім.",
        reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="❌ Скасувати", callback_data="admin_panel")).as_markup()
    )

@dp.message(AdminStates.waiting_broadcast_msg)
async def adm_broadcast_confirm(message: Message, state: FSMContext):
    await state.update_data(broadcast_msg_id=message.message_id, broadcast_chat_id=message.chat.id)
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🚀 ПОЧАТИ", callback_data="confirm_broadcast_send"))
    kb.row(InlineKeyboardButton(text="❌ Скасувати", callback_data="admin_panel"))
    await message.answer("👆 <b>Це прев'ю повідомлення.</b>\nПочати розсилку для всіх користувачів?",
                         reply_markup=kb.as_markup())

@dp.callback_query(F.data == "confirm_broadcast_send")
async def adm_broadcast_run(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    msg_id = data.get("broadcast_msg_id")
    from_chat = data.get("broadcast_chat_id")
    await state.clear()

    # Отримуємо всіх користувачів
    rows = db.execute("SELECT user_id FROM users", fetch=True)
    users = [row['user_id'] for row in rows]
    if not users:
        return await call.message.answer("❌ Немає користувачів для розсилки.")

    await call.message.edit_text(f"⏳ Розсилка запущена для {len(users)} чол...")
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
        f"✅ <b>Розсилка завершена!</b>\n\n"
        f"📊 Успішно: {count}\n"
        f"🚫 Помилок: {err}"
    )
    db.log_admin(call.from_user.id, "broadcast", f"Успішно: {count}, помилок: {err}")

# --- Видача зірок ---
@dp.callback_query(F.data == "a_give_stars")
async def adm_give_stars_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_give_data)
    await call.message.edit_text(
        "💎 <b>ВИДАЧА ЗІРОК</b>\n\n"
        "Введіть ID користувача і кількість зірок через пробіл.\n"
        "Приклад: <code>8364667153 100</code>",
        reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="❌ Скасувати", callback_data="admin_panel")).as_markup()
    )

@dp.message(AdminStates.waiting_give_data)
async def adm_give_stars_process(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        data = message.text.split()
        if len(data) != 2:
            return await message.answer("❌ Введіть два числа: ID і суму.")
        target_id = int(data[0])
        amount = float(data[1])
        user = db.get_user(target_id)
        if not user:
            return await message.answer(f"❌ Користувача з ID <code>{target_id}</code> не знайдено!")
        db.add_stars(target_id, amount)
        await message.answer(
            f"✅ <b>УСПІШНО!</b>\n\n"
            f"Користувачу: <b>{user['first_name']}</b> (<code>{target_id}</code>)\n"
            f"Нараховано: <b>{amount} ⭐</b>",
            reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 В адмінку", callback_data="admin_panel")).as_markup()
        )
        try:
            await bot.send_message(target_id, f"🎁 Адміністратор нарахував вам <b>{amount} ⭐</b>!")
        except:
            pass
        db.log_admin(message.from_user.id, "give_stars", f"Користувачу {target_id} сума {amount}")
        await state.clear()
    except Exception as e:
        await message.answer(f"❌ Помилка: {e}")

# --- Створення промокоду ---
@dp.callback_query(F.data == "a_create_promo")
async def adm_promo_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_promo_data)
    await call.message.answer(
        "Введіть дані промокоду через пробіл:\n"
        "<code>КОД ТИП ЗНАЧЕННЯ КІЛЬКІСТЬ</code>\n\n"
        "Приклади:\n"
        "<code>GIFT1 stars 100 10</code> (100 зірок)\n"
        "<code>ROZA gift 🌹_Роза 5</code> (5 троянд)"
    )

@dp.message(AdminStates.waiting_promo_data)
async def adm_promo_save(message: Message, state: FSMContext):
    try:
        code, r_type, val, uses = message.text.split()
        uses = int(uses)
        db.execute("INSERT INTO promo VALUES (?, ?, ?, ?)", (code, r_type, val, uses))
        await message.answer(f"✅ Промокод <code>{code}</code> створено на {uses} використань!")
        db.log_admin(message.from_user.id, "create_promo", f"Код {code}, тип {r_type}, значення {val}, ліміт {uses}")
        await state.clear()
    except Exception as e:
        await message.answer("❌ Помилка! Формат: <code>КОД ТИП ЗНАЧЕННЯ КІЛЬКІСТЬ</code>")

# --- Пост в канал ---
@dp.callback_query(F.data == "a_post_chan")
async def adm_post_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.waiting_channel_post)
    await call.message.edit_text(
        "📢 Надішліть текст для публікації в каналі.\n"
        "Бот автоматично додасть кнопку для отримання нагороди."
    )

@dp.message(AdminStates.waiting_channel_post)
async def adm_post_end(message: Message, state: FSMContext):
    pid = f"v_{random.randint(100, 999)}"
    view_reward = float(db.get_config('view_reward', 0.3))
    kb = InlineKeyboardBuilder().row(
        InlineKeyboardButton(text=f"💰 Забрати {view_reward} ⭐", callback_data=f"claim_{pid}")
    ).as_markup()
    await bot.send_message(CHANNEL_ID, message.text, reply_markup=kb)
    await message.answer("✅ Опубліковано!")
    db.log_admin(message.from_user.id, "channel_post", f"Пост з id {pid}")
    await state.clear()

@dp.callback_query(F.data.startswith("claim_"))
async def cb_claim(call: CallbackQuery):
    pid = call.data.split("_")[1]
    uid = call.from_user.id
    user = db.get_user(uid)
    if not user:
        return await call.answer("❌ Запустіть бота командою /start", show_alert=True)
    # Перевірка чи вже забирав
    check = db.execute(
        "SELECT 1 FROM task_claims WHERE user_id = ? AND task_id = ?",
        (uid, f"post_{pid}"), fetchone=True
    )
    if check:
        return await call.answer("❌ Ви вже забрали нагороду!", show_alert=True)
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
    fake_item = random.choice(list(gifts.keys())) if gifts else "Подарунок"
    fake_names = ["Dmitry_ST", "Sasha_Official", "Rich_Boy", "CryptoKing", "Masha_Stars", "Legenda_77"]
    name = random.choice(fake_names)
    fid = random.randint(1000000000, 9999999999)
    text = (
        f"🎁 <b>ЗАЯВКА НА ВИВЕДЕННЯ </b>\n\n"
        f"👤 Юзер: @{name}\n"
        f"🆔 ID: <code>{fid}</code>\n"
        f"📦 Предмет: <b>{fake_item}</b>"
    )
    await bot.send_message(WITHDRAWAL_CHANNEL_ID, text, reply_markup=get_admin_decision_kb(0, "GIFT"))
    await call.answer("✅ Реалістичний фейк відправлено!")
    db.log_admin(call.from_user.id, "fake_withdraw", f"Фейк предмет {fake_item}")

# --- Запуск лотереї ---
@dp.callback_query(F.data == "a_run_lottery")
async def adm_run_lottery(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    data = db.execute("SELECT pool, participants FROM lottery WHERE id = 1", fetchone=True)
    if not data or not data['participants']:
        return await call.answer("❌ Немає учасників!", show_alert=True)
    participants = [p for p in data['participants'].split(',') if p]
    winner_id = int(random.choice(participants))
    win_amount = data['pool'] * 0.8
    db.execute("UPDATE lottery SET pool = 0, participants = '' WHERE id = 1")
    db.add_stars(winner_id, win_amount)
    await bot.send_message(winner_id, f"🥳 <b>ВІТАЄМО!</b>\nВи виграли в лотереї: <b>{win_amount:.2f} ⭐</b>")
    await call.message.answer(f"✅ Лотерея завершена! Переможець: {winner_id}, сума: {win_amount:.2f}")
    db.log_admin(call.from_user.id, "run_lottery", f"Переможець {winner_id}, сума {win_amount}")

# --- Меню налаштувань бота ---
@dp.callback_query(F.data == "a_config_menu")
async def adm_config_menu(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="💰 Реферальна нагорода", callback_data="edit_config_ref_reward"))
    kb.row(InlineKeyboardButton(text="👀 Нагорода за пост", callback_data="edit_config_view_reward"))
    kb.row(InlineKeyboardButton(text="📅 Щоденний мін/макс", callback_data="edit_config_daily"))
    kb.row(InlineKeyboardButton(text="🎰 Удача мін/макс/кулдаун", callback_data="edit_config_luck"))
    kb.row(InlineKeyboardButton(text="💎 Суми виведення", callback_data="edit_config_withdraw"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel"))
    await call.message.edit_text("⚙️ <b>Налаштування бота</b>\nОберіть параметр для зміни:", reply_markup=kb.as_markup())

# Редагування реферальної нагороди
@dp.callback_query(F.data == "edit_config_ref_reward")
async def edit_ref_reward(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    current = db.get_config('ref_reward', '5.0')
    await state.set_state(AdminStates.waiting_config_value)
    await state.update_data(config_key='ref_reward')
    await call.message.answer(f"Поточне значення: <b>{current}</b>\nВведіть нову нагороду за реферала (число):")

@dp.callback_query(F.data == "edit_config_view_reward")
async def edit_view_reward(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    current = db.get_config('view_reward', '0.3')
    await state.set_state(AdminStates.waiting_config_value)
    await state.update_data(config_key='view_reward')
    await call.message.answer(f"Поточне значення: <b>{current}</b>\nВведіть нову нагороду за перегляд посту (число):")

@dp.callback_query(F.data == "edit_config_daily")
async def edit_daily(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    current_min = db.get_config('daily_min', '1')
    current_max = db.get_config('daily_max', '3')
    await state.set_state(AdminStates.waiting_config_value)
    await state.update_data(config_key='daily')
    await call.message.answer(
        f"Поточні значення: мін {current_min}, макс {current_max}\n"
        "Введіть нові мінімум і максимум через пробіл (наприклад: 2 5):"
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
        f"Поточні значення: мін {current_min}, макс {current_max}, кулдаун {current_cd} сек\n"
        "Введіть нові мінімум, максимум і кулдаун через пробіл (наприклад: 1 10 3600):"
    )

@dp.callback_query(F.data == "edit_config_withdraw")
async def edit_withdraw(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    current = db.get_config('withdrawal_options', '15,25,50,100')
    await state.set_state(AdminStates.waiting_config_value)
    await state.update_data(config_key='withdrawal_options')
    await call.message.answer(
        f"Поточні суми: {current}\n"
        "Введіть нові суми через кому (наприклад: 10,20,30,50,100):"
    )

@dp.message(AdminStates.waiting_config_value)
async def set_config_value(message: Message, state: FSMContext):
    data = await state.get_data()
    key = data.get('config_key')
    text = message.text.strip()
    try:
        if key == 'ref_reward' or key == 'view_reward':
            new_val = float(text)
            db.set_config(key, str(new_val))
            await message.answer(f"✅ Параметр <b>{key}</b> змінено на {new_val}")
        elif key == 'daily':
            parts = text.split()
            if len(parts) != 2:
                raise ValueError
            min_val = float(parts[0])
            max_val = float(parts[1])
            db.set_config('daily_min', str(min_val))
            db.set_config('daily_max', str(max_val))
            await message.answer(f"✅ Щоденний бонус змінено: мін {min_val}, макс {max_val}")
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
            await message.answer(f"✅ Удачу змінено: мін {min_val}, макс {max_val}, кулдаун {cd} сек")
        elif key == 'withdrawal_options':
            # перевіряємо, що це числа через кому
            options = [int(x.strip()) for x in text.split(',') if x.strip()]
            if not options:
                raise ValueError
            db.set_config('withdrawal_options', ','.join(str(x) for x in options))
            await message.answer(f"✅ Суми виведення змінено: {', '.join(str(x) for x in options)}")
        else:
            await message.answer("❌ Невідомий параметр")
            await state.clear()
            return
        db.log_admin(message.from_user.id, "change_config", f"{key} = {text}")
    except Exception:
        await message.answer("❌ Помилка введення! Перевірте формат.")
        return
    await state.clear()
    await adm_config_menu(await message.answer("⚙️ Налаштування", reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")).as_markup()))

# --- Глобальні бусти ---
@dp.callback_query(F.data == "a_global_boost_menu")
async def adm_global_boost_menu(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="👥 Буст рефералів x2 (1 год)", callback_data="set_boost_ref_2_3600"))
    kb.row(InlineKeyboardButton(text="👥 Буст рефералів x3 (3 год)", callback_data="set_boost_ref_3_10800"))
    kb.row(InlineKeyboardButton(text="🎰 Буст ігор x2 (1 год)", callback_data="set_boost_game_2_3600"))
    kb.row(InlineKeyboardButton(text="❌ Вимкнути буст рефералів", callback_data="disable_boost_ref"))
    kb.row(InlineKeyboardButton(text="❌ Вимкнути буст ігор", callback_data="disable_boost_game"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel"))
    await call.message.edit_text("📈 <b>Глобальні бусти</b>\nОберіть дію:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("set_boost_"))
async def set_boost_handler(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    parts = call.data.split("_")
    # Формат: set_boost_{type}_{mult}_{duration} або set_boost_{type}_{mult}
    boost_type = parts[2]  # ref або game
    mult = float(parts[3])
    duration = int(parts[4]) if len(parts) > 4 else None
    db.set_global_boost(boost_type, mult, duration)
    await call.answer(f"✅ Буст {boost_type} x{mult} активовано!", show_alert=True)
    db.log_admin(call.from_user.id, "global_boost", f"{boost_type} x{mult} на {duration} сек")
    await adm_global_boost_menu(call)

@dp.callback_query(F.data.startswith("disable_boost_"))
async def disable_boost_handler(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    boost_type = call.data.replace("disable_boost_", "")
    db.disable_global_boost(boost_type)
    await call.answer(f"✅ Буст {boost_type} вимкнено!", show_alert=True)
    db.log_admin(call.from_user.id, "global_boost", f"Вимкнено {boost_type}")
    await adm_global_boost_menu(call)

# --- Редагування цін подарунків ---
@dp.callback_query(F.data == "a_edit_gifts")
async def adm_edit_gifts(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    gifts = db.get_gifts_prices()
    text = "🛍 <b>Поточні ціни подарунків:</b>\n"
    for name, price in gifts.items():
        text += f"{name}: {price} ⭐\n"
    text += "\nВведіть назву товару та нову ціну через пробіл (наприклад: 🧸 Мишка 50)."
    await state.set_state(AdminStates.waiting_gift_price)
    await call.message.edit_text(text)

@dp.message(AdminStates.waiting_gift_price)
async def set_gift_price(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        parts = message.text.rsplit(' ', 1)
        if len(parts) != 2:
            return await message.answer("❌ Формат: назва ціна")
        item_name = parts[0].strip()
        price = float(parts[1])
        gifts = db.get_gifts_prices()
        if item_name not in gifts:
            return await message.answer("❌ Товар не знайдено в списку!")
        gifts[item_name] = price
        db.set_config('gifts_prices', json.dumps(gifts, ensure_ascii=False))
        await message.answer(f"✅ Ціну для <b>{item_name}</b> змінено на {price} ⭐")
        db.log_admin(message.from_user.id, "edit_gift_price", f"{item_name} = {price}")
    except Exception as e:
        await message.answer(f"❌ Помилка: {e}")
    await state.clear()
    await adm_config_menu(await message.answer("⚙️ Налаштування", reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")).as_markup()))

# --- Редагування ексклюзивних товарів ---
@dp.callback_query(F.data == "a_edit_specials")
async def adm_edit_specials(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    specials = db.get_special_items()
    text = "📦 <b>Ексклюзивні товари (поточні ліміти та ціни):</b>\n"
    for key, info in specials.items():
        text += f"{info['full_name']}: ціна {info['price']} ⭐, ліміт {info['limit']}\n"
    text += "\nВведіть ключ товару (Ramen/Candle/Calendar), нову ціну і новий ліміт через пробіл.\n"
    text += "Приклад: Ramen 300 20"
    await state.set_state(AdminStates.waiting_special_field)
    await call.message.edit_text(text)

@dp.message(AdminStates.waiting_special_field)
async def set_special_item(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        parts = message.text.split()
        if len(parts) != 3:
            return await message.answer("❌ Формат: ключ ціна ліміт")
        key = parts[0].strip()
        price = float(parts[1])
        limit = int(parts[2])
        specials = db.get_special_items()
        if key not in specials:
            return await message.answer("❌ Ключ не знайдено! Доступні: Ramen, Candle, Calendar")
        specials[key]['price'] = price
        specials[key]['limit'] = limit
        db.set_config('special_items', json.dumps(specials, ensure_ascii=False))
        await message.answer(f"✅ Товар <b>{specials[key]['full_name']}</b> оновлено: ціна {price}, ліміт {limit}")
        db.log_admin(message.from_user.id, "edit_special", f"{key} price={price} limit={limit}")
    except Exception as e:
        await message.answer(f"❌ Помилка: {e}")
    await state.clear()
    await adm_config_menu(await message.answer("⚙️ Налаштування", reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")).as_markup()))

# ========== ОБРОБКА АДМІН-РІШЕНЬ ПО ЗАЯВКАХ ==========
@dp.callback_query(F.data.startswith("adm_app_") | F.data.startswith("adm_rej_"))
async def cb_adm_action(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return await call.answer("❌ Ви не адміністратор!", show_alert=True)
    parts = call.data.split("_")
    action = parts[1]  # app або rej
    target_uid = int(parts[2])
    value = parts[3]   # сума або GIFT

    # Фейк
    if target_uid == 0:
        status = "✅ ОДОБРЕНО (ФЕЙК)" if action == "app" else "❌ ВІДХИЛЕНО (ФЕЙК)"
        await call.message.edit_text(f"{call.message.text}\n\n<b>Підсумок: {status}</b>")
        return await call.answer("Фейк-вивід оброблено")

    # Реальний користувач
    try:
        if action == "app":
            reward_text = "подарунка" if value == "GIFT" else f"{value} ⭐"
            await bot.send_message(target_uid, f"🎉 <b>Ваша заявка на виведення {reward_text} схвалена!</b>")
            status_text = "✅ ПРИЙНЯТО"
            db.log_admin(call.from_user.id, "withdraw_approve", f"Користувач {target_uid}, сума {value}")
        else:
            if value == "GIFT":
                await bot.send_message(target_uid, "❌ <b>Заявка на виведення подарунка відхилена.</b>\nЗв'яжіться з підтримкою.")
            else:
                db.add_stars(target_uid, float(value))
                await bot.send_message(target_uid, f"❌ <b>Виплата {value} ⭐ відхилена.</b>\nЗірки повернуто на ваш баланс.")
            status_text = "❌ ВІДХИЛЕНО"
            db.log_admin(call.from_user.id, "withdraw_reject", f"Користувач {target_uid}, сума {value}")

        await call.message.edit_text(
            f"{call.message.text}\n\n<b>Підсумок: {status_text}</b> (Адмін: @{call.from_user.username or call.from_user.id})"
        )
        await call.answer("Готово!")
    except Exception as e:
        logging.error(f"Помилка в адмін-дії: {e}")
        await call.answer("❌ Помилка (можливо, юзер заблокував бота)", show_alert=True)

@dp.callback_query(F.data.startswith("adm_chat_"))
async def cb_adm_chat(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return
    uid = call.data.split("_")[2]
    if uid == "0":
        return await call.answer("❌ Це фейк!", show_alert=True)
    await call.message.answer(f"🔗 Зв'язок з юзером: tg://user?id={uid}")
    await call.answer()

# ========== ЗАПУСК ==========
async def web_handle(request):
    return web.Response(text="Bot Active")

async def main():
    # Налаштування веб-сервера для Render (необов'язково, але для health check)
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
