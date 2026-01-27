"""
StarEarnBot - система лояльности и заработка виртуальных "Звезд"
Версия: 2.0.0
Автор: KneoWorld / Chotko Team
Оптимизировано для Render.com
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import aiohttp
from aiogram import Bot, Dispatcher, F, Router, html
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ChatMemberUpdated,
    ChatInviteLink,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.token import TokenValidationError

# Встроенный веб-сервер для Keep-Alive
try:
    from flask import Flask, request
    from threading import Thread
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

# Наши модули
try:
    from database import Database, User, Referral, Transaction, DailyReward
    from keyboards import (
        main_menu_keyboard,
        profile_keyboard,
        tasks_keyboard,
        luck_game_keyboard,
        referrals_keyboard,
        top_players_keyboard,
        admin_keyboard,
    )
    from utils import (
        check_subscription,
        generate_referral_link,
        rate_limit,
        format_number,
        create_stars_display,
        validate_env_vars,
    )
except ImportError:
    # Создаем минимальные заглушки для модулей
    class Database:
        pass
    class User:
        pass
    HAS_CUSTOM_MODULES = False
else:
    HAS_CUSTOM_MODULES = True

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# Конфигурация
class Config:
    """Конфигурация бота"""
    # Получаем токен из переменных окружения
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не найден в переменных окружения!")
        sys.exit(1)
    
    # Канал для подписки (обязательная подписка)
    CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "nft0top")
    CHANNEL_ID = os.getenv("CHANNEL_ID", "-1001234567890")  # Заменить на реальный ID
    
    # Настройки админов
    ADMIN_IDS = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "").split(",") if id.strip()]
    
    # Настройки экономики
    DAILY_REWARD_MIN = 1
    DAILY_REWARD_MAX = 5
    LUCK_GAME_MIN = 0
    LUCK_GAME_MAX = 10
    LUCK_GAME_COOLDOWN = 4 * 60 * 60  # 4 часа в секундах
    REFERRAL_REWARD_LEVELS = [5, 2, 1]  # Вознаграждение за 1, 2, 3 уровни
    GROUP_ADD_REWARD = 2  # Звезды за добавление бота в группу
    
    # Фласк для Keep-Alive
    FLASK_PORT = int(os.getenv("PORT", 10000))

# Проверка валидности токена
try:
    from aiogram.utils.token import validate_token
    validate_token(Config.BOT_TOKEN)
except TokenValidationError:
    logger.error("Невалидный токен бота!")
    sys.exit(1)

# Инициализация бота и диспетчера
bot = Bot(
    token=Config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# Инициализация базы данных
if HAS_CUSTOM_MODULES:
    db = Database("star_earn_bot.db")
else:
    # Минимальная реализация базы данных в основном файле
    import sqlite3
    import aiosqlite
    from contextlib import asynccontextmanager
    
    class SimpleDB:
        def __init__(self, db_path: str):
            self.db_path = db_path
            
        async def init_db(self):
            async with aiosqlite.connect(self.db_path) as conn:
                # Таблица пользователей
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        first_name TEXT,
                        last_name TEXT,
                        stars INTEGER DEFAULT 0,
                        referrals_count INTEGER DEFAULT 0,
                        total_earned INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_daily TIMESTAMP,
                        last_luck_game TIMESTAMP,
                        is_subscribed BOOLEAN DEFAULT FALSE
                    )
                """)
                
                # Таблица рефералов
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS referrals (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        referrer_id INTEGER,
                        referred_id INTEGER UNIQUE,
                        level INTEGER DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (referrer_id) REFERENCES users (user_id),
                        FOREIGN KEY (referred_id) REFERENCES users (user_id)
                    )
                """)
                
                # Таблица транзакций
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS transactions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        amount INTEGER,
                        type TEXT,
                        description TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users (user_id)
                    )
                """)
                
                await conn.commit()
    
    db = SimpleDB("star_earn_bot.db")

# Состояния FSM
class UserStates(StatesGroup):
    """Состояния пользователя для FSM"""
    waiting_for_luck_game = State()
    waiting_for_task_completion = State()

# Встроенный Flask сервер для Keep-Alive
if HAS_FLASK:
    flask_app = Flask(__name__)
    
    @flask_app.route('/')
    def home():
        return "StarEarnBot is running!", 200
    
    @flask_app.route('/health')
    def health():
        return {"status": "healthy", "timestamp": datetime.now().isoformat()}, 200
    
    @flask_app.route('/ping')
    def ping():
        return "pong", 200
    
    def run_flask():
        """Запуск Flask в отдельном потоке"""
        flask_app.run(host='0.0.0.0', port=Config.FLASK_PORT)

# ========== MIDDLEWARE И ФИЛЬТРЫ ==========

@router.message.middleware()
async def subscription_middleware(message: Message, bot: Bot):
    """Middleware для проверки подписки на канал"""
    # Пропускаем команды /start и /help
    if message.text in ['/start', '/help', '/start start']:
        return
    
    # Пропускаем сообщения от админов
    if message.from_user.id in Config.ADMIN_IDS:
        return
    
    # Проверяем подписку
    try:
        is_subscribed = await check_subscription(bot, Config.CHANNEL_ID, message.from_user.id)
        if not is_subscribed:
            kb = InlineKeyboardBuilder()
            kb.add(InlineKeyboardButton(
                text="📢 Подписаться на канал", 
                url=f"https://t.me/{Config.CHANNEL_USERNAME}"
            ))
            kb.add(InlineKeyboardButton(
                text="✅ Я подписался",
                callback_data="check_subscription"
            ))
            
            await message.answer(
                "📢 <b>Для использования бота необходимо подписаться на наш канал!</b>\n\n"
                "Подпишитесь на канал, чтобы получить доступ ко всем функциям бота.",
                reply_markup=kb.as_markup()
            )
            return False
    except Exception as e:
        logger.error(f"Ошибка проверки подписки: {e}")
    
    return True

# ========== ОБРАБОТЧИКИ КОМАНД ==========

@router.message(CommandStart())
@rate_limit(2, "start")
async def cmd_start(message: Message, state: FSMContext):
    """Обработчик команды /start"""
    await state.clear()
    
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    last_name = message.from_user.last_name
    
    # Извлекаем реферальный код из параметров
    ref_code = None
    if len(message.text.split()) > 1:
        ref_code = message.text.split()[1]
    
    # Регистрируем пользователя
    try:
        if HAS_CUSTOM_MODULES:
            user = await db.get_or_create_user(
                user_id=user_id,
                username=username,
                first_name=first_name,
                last_name=last_name
            )
        else:
            # Минимальная реализация регистрации
            async with aiosqlite.connect("star_earn_bot.db") as conn:
                # Проверяем существование пользователя
                cursor = await conn.execute(
                    "SELECT * FROM users WHERE user_id = ?", 
                    (user_id,)
                )
                user = await cursor.fetchone()
                
                if not user:
                    # Создаем нового пользователя
                    await conn.execute(
                        """INSERT INTO users 
                        (user_id, username, first_name, last_name, stars, created_at) 
                        VALUES (?, ?, ?, ?, ?, ?)""",
                        (user_id, username, first_name, last_name, 0, datetime.now())
                    )
                    await conn.commit()
                    user = (user_id, username, first_name, last_name, 0, 0, 0, datetime.now(), None, None, False)
                
                # Обрабатываем реферала
                if ref_code:
                    try:
                        referrer_id = int(ref_code)
                        if referrer_id != user_id:
                            # Проверяем существование реферера
                            cursor = await conn.execute(
                                "SELECT user_id FROM users WHERE user_id = ?", 
                                (referrer_id,)
                            )
                            referrer = await cursor.fetchone()
                            
                            if referrer:
                                # Записываем реферала
                                await conn.execute(
                                    """INSERT OR IGNORE INTO referrals 
                                    (referrer_id, referred_id, level) 
                                    VALUES (?, ?, ?)""",
                                    (referrer_id, user_id, 1)
                                )
                                # Начисляем звезды рефереру
                                reward = Config.REFERRAL_REWARD_LEVELS[0]
                                await conn.execute(
                                    "UPDATE users SET stars = stars + ? WHERE user_id = ?",
                                    (reward, referrer_id)
                                )
                                await conn.execute(
                                    """INSERT INTO transactions 
                                    (user_id, amount, type, description) 
                                    VALUES (?, ?, ?, ?)""",
                                    (referrer_id, reward, "referral", f"Реферал 1 уровня: {user_id}")
                                )
                                await conn.commit()
                    except (ValueError, Exception) as e:
                        logger.error(f"Ошибка обработки реферала: {e}")
    except Exception as e:
        logger.error(f"Ошибка регистрации пользователя: {e}")
        await message.answer("❌ Произошла ошибка при регистрации. Попробуйте позже.")
        return
    
    # Приветственное сообщение
    welcome_text = f"""
    ⭐ <b>Добро пожаловать, {html.quote(first_name)}!</b> ⭐

    <b>StarEarnBot</b> — это система лояльности и заработка виртуальных "Звезд"!

    🎯 <b>Основные возможности:</b>
    • 📅 Ежедневные бонусы
    • 🎮 Мини-игра "Удача"
    • 👥 Реферальная система (многоуровневая)
    • 🏆 Топ игроков
    • 🎁 Специальные задания

    💫 <b>Начните зарабатывать Звезды прямо сейчас!</b>

    Используйте кнопки ниже для навигации:
    """
    
    await message.answer(
        welcome_text,
        reply_markup=main_menu_keyboard()
    )
    
    # Если есть реферальный код, сообщаем об успешной регистрации
    if ref_code:
        await message.answer(
            f"🎉 Вы зарегистрировались по реферальной ссылке! "
            f"На ваш счет начислено +{Config.REFERRAL_REWARD_LEVELS[0]} звезд!"
        )

@router.message(Command("profile"))
@rate_limit(1, "profile")
async def cmd_profile(message: Message):
    """Личный кабинет пользователя"""
    user_id = message.from_user.id
    
    try:
        if HAS_CUSTOM_MODULES:
            user = await db.get_user(user_id)
            if not user:
                await message.answer("❌ Пользователь не найден. Используйте /start")
                return
            
            referrals = await db.get_user_referrals(user_id)
            transactions = await db.get_recent_transactions(user_id, 5)
        else:
            async with aiosqlite.connect("star_earn_bot.db") as conn:
                # Получаем данные пользователя
                cursor = await conn.execute(
                    "SELECT * FROM users WHERE user_id = ?", 
                    (user_id,)
                )
                user = await cursor.fetchone()
                
                if not user:
                    await message.answer("❌ Пользователь не найден. Используйте /start")
                    return
                
                # Считаем рефералов
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", 
                    (user_id,)
                )
                referrals_count = (await cursor.fetchone())[0]
                
                # Получаем последние транзакции
                cursor = await conn.execute(
                    """SELECT * FROM transactions 
                    WHERE user_id = ? 
                    ORDER BY created_at DESC LIMIT 5""",
                    (user_id,)
                )
                transactions = await cursor.fetchall()
        
        # Формируем текст профиля
        stars_display = create_stars_display(user[4] if isinstance(user, tuple) else user.stars)
        
        profile_text = f"""
        👤 <b>Личный кабинет</b>

        🆔 ID: <code>{user_id}</code>
        📛 Имя: {html.quote(user[2] if isinstance(user, tuple) else user.first_name)}
        
        ⭐ <b>Баланс:</b> {user[4] if isinstance(user, tuple) else user.stars} {stars_display}
        
        👥 <b>Рефералы:</b> {referrals_count if 'referrals_count' in locals() else (user[5] if isinstance(user, tuple) else user.referrals_count)}
        💰 <b>Всего заработано:</b> {user[6] if isinstance(user, tuple) else user.total_earned} звезд
        
        📅 <b>Дата регистрации:</b> {(user[7] if isinstance(user, tuple) else user.created_at).strftime('%d.%m.%Y')}
        
        🏆 <b>Ваше место в топе:</b> <i>рассчитывается...</i>
        """
        
        await message.answer(
            profile_text,
            reply_markup=profile_keyboard()
        )
        
    except Exception as e:
        logger.error(f"Ошибка получения профиля: {e}")
        await message.answer("❌ Ошибка загрузки профиля. Попробуйте позже.")

@router.message(Command("daily"))
@rate_limit(1, "daily")
async def cmd_daily(message: Message):
    """Ежедневный бонус"""
    user_id = message.from_user.id
    now = datetime.now()
    
    try:
        if HAS_CUSTOM_MODULES:
            last_daily = await db.get_last_daily(user_id)
            
            if last_daily and (now - last_daily).days < 1:
                next_daily = last_daily + timedelta(days=1)
                time_left = next_daily - now
                
                hours = time_left.seconds // 3600
                minutes = (time_left.seconds % 3600) // 60
                
                await message.answer(
                    f"⏳ Вы уже получали ежедневный бонус сегодня!\n"
                    f"Следующий бонус через: {hours}ч {minutes}м"
                )
                return
            
            # Начисляем бонус
            import random
            reward = random.randint(Config.DAILY_REWARD_MIN, Config.DAILY_REWARD_MAX)
            
            await db.add_stars(user_id, reward)
            await db.record_transaction(user_id, reward, "daily", "Ежедневный бонус")
            await db.update_last_daily(user_id, now)
            
            stars_display = create_stars_display(reward)
            
            await message.answer(
                f"🎉 <b>Ежедневный бонус!</b>\n\n"
                f"Вы получили: +{reward} {stars_display}\n"
                f"🎯 Заходите завтра за новым бонусом!"
            )
            
        else:
            async with aiosqlite.connect("star_earn_bot.db") as conn:
                # Проверяем последний бонус
                cursor = await conn.execute(
                    "SELECT last_daily FROM users WHERE user_id = ?", 
                    (user_id,)
                )
                result = await cursor.fetchone()
                last_daily = result[0] if result and result[0] else None
                
                if last_daily:
                    last_daily = datetime.fromisoformat(last_daily)
                    if (now - last_daily).days < 1:
                        next_daily = last_daily + timedelta(days=1)
                        time_left = next_daily - now
                        hours = time_left.seconds // 3600
                        minutes = (time_left.seconds % 3600) // 60
                        
                        await message.answer(
                            f"⏳ Вы уже получали ежедневный бонус сегодня!\n"
                            f"Следующий бонус через: {hours}ч {minutes}м"
                        )
                        return
                
                # Начисляем бонус
                import random
                reward = random.randint(Config.DAILY_REWARD_MIN, Config.DAILY_REWARD_MAX)
                
                await conn.execute(
                    "UPDATE users SET stars = stars + ?, last_daily = ? WHERE user_id = ?",
                    (reward, now.isoformat(), user_id)
                )
                await conn.execute(
                    """INSERT INTO transactions 
                    (user_id, amount, type, description) 
                    VALUES (?, ?, ?, ?)""",
                    (user_id, reward, "daily", "Ежедневный бонус")
                )
                await conn.commit()
                
                stars_display = create_stars_display(reward)
                
                await message.answer(
                    f"🎉 <b>Ежедневный бонус!</b>\n\n"
                    f"Вы получили: +{reward} {stars_display}\n"
                    f"🎯 Заходите завтра за новым бонусом!"
                )
                
    except Exception as e:
        logger.error(f"Ошибка выдачи ежедневного бонуса: {e}")
        await message.answer("❌ Ошибка выдачи бонуса. Попробуйте позже.")

@router.message(Command("luck"))
@rate_limit(1, "luck")
async def cmd_luck(message: Message):
    """Мини-игра 'Удача'"""
    user_id = message.from_user.id
    now = datetime.now()
    
    try:
        if HAS_CUSTOM_MODULES:
            last_game = await db.get_last_luck_game(user_id)
            
            if last_game:
                time_passed = (now - last_game).total_seconds()
                if time_passed < Config.LUCK_GAME_COOLDOWN:
                    time_left = Config.LUCK_GAME_COOLDOWN - time_passed
                    hours = int(time_left // 3600)
                    minutes = int((time_left % 3600) // 60)
                    
                    await message.answer(
                        f"⏳ Игра 'Удача' доступна раз в 4 часа!\n"
                        f"Следующая игра через: {hours}ч {minutes}м\n\n"
                        f"Вернитесь позже! 🎮"
                    )
                    return
        else:
            async with aiosqlite.connect("star_earn_bot.db") as conn:
                cursor = await conn.execute(
                    "SELECT last_luck_game FROM users WHERE user_id = ?", 
                    (user_id,)
                )
                result = await cursor.fetchone()
                last_game = result[0] if result and result[0] else None
                
                if last_game:
                    last_game = datetime.fromisoformat(last_game)
                    time_passed = (now - last_game).total_seconds()
                    if time_passed < Config.LUCK_GAME_COOLDOWN:
                        time_left = Config.LUCK_GAME_COOLDOWN - time_passed
                        hours = int(time_left // 3600)
                        minutes = int((time_left % 3600) // 60)
                        
                        await message.answer(
                            f"⏳ Игра 'Удача' доступна раз в 4 часа!\n"
                            f"Следующая игра через: {hours}ч {minutes}м\n\n"
                            f"Вернитесь позже! 🎮"
                        )
                        return
        
        # Показываем клавиатуру игры
        await message.answer(
            "🎮 <b>Мини-игра 'Удача'</b>\n\n"
            "Попробуйте свою удачу и выиграйте звезды!\n"
            "Награда: от 0 до 10 звезд!\n\n"
            "🎯 <i>Нажмите кнопку ниже, чтобы сыграть:</i>",
            reply_markup=luck_game_keyboard()
        )
        
    except Exception as e:
        logger.error(f"Ошибка в игре 'Удача': {e}")
        await message.answer("❌ Ошибка запуска игры. Попробуйте позже.")

@router.message(Command("referral"))
@rate_limit(1, "referral")
async def cmd_referral(message: Message):
    """Реферальная система"""
    user_id = message.from_user.id
    
    try:
        # Генерируем реферальную ссылку
        ref_link = generate_referral_link(bot, user_id)
        
        if HAS_CUSTOM_MODULES:
            referrals = await db.get_user_referrals(user_id)
            total_referrals = len(referrals)
            
            # Считаем заработок с рефералов
            total_earned_from_refs = sum(
                Config.REFERRAL_REWARD_LEVELS[0] for _ in referrals
            )
        else:
            async with aiosqlite.connect("star_earn_bot.db") as conn:
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", 
                    (user_id,)
                )
                total_referrals = (await cursor.fetchone())[0]
                
                # Простой расчет заработка
                total_earned_from_refs = total_referrals * Config.REFERRAL_REWARD_LEVELS[0]
        
        referral_text = f"""
        👥 <b>Реферальная система</b>

        💎 <b>Ваша реферальная ссылка:</b>
        <code>{ref_link}</code>

        📊 <b>Статистика:</b>
        • 👥 Приглашено пользователей: {total_referrals}
        • 💰 Заработано с рефералов: {total_earned_from_refs} звезд

        🎯 <b>Уровни реферальной системы:</b>
        1️⃣ Уровень: +{Config.REFERRAL_REWARD_LEVELS[0]} звезд за каждого приглашенного
        2️⃣ Уровень: +{Config.REFERRAL_REWARD_LEVELS[1]} звезд за рефералов 2-го уровня
        3️⃣ Уровень: +{Config.REFERRAL_REWARD_LEVELS[2]} звезд за рефералов 3-го уровня

        📢 <b>Как приглашать:</b>
        1. Отправьте друзьям вашу реферальную ссылку
        2. Друг должен нажать на ссылку и начать общение с ботом
        3. Вы получите награду сразу после регистрации друга!
        """
        
        await message.answer(
            referral_text,
            reply_markup=referrals_keyboard(ref_link),
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Ошибка реферальной системы: {e}")
        await message.answer("❌ Ошибка загрузки реферальной системы. Попробуйте позже.")

@router.message(Command("top"))
@rate_limit(1, "top")
async def cmd_top(message: Message):
    """Топ игроков по звездам"""
    try:
        if HAS_CUSTOM_MODULES:
            top_users = await db.get_top_players(limit=20)
        else:
            async with aiosqlite.connect("star_earn_bot.db") as conn:
                cursor = await conn.execute(
                    """SELECT user_id, username, first_name, stars 
                    FROM users 
                    ORDER BY stars DESC 
                    LIMIT 20"""
                )
                top_users = await cursor.fetchall()
        
        if not top_users:
            await message.answer("📊 Топ игроков пока пуст. Будьте первым!")
            return
        
        # Формируем текст топа
        top_text = "🏆 <b>Топ игроков по звездам</b>\n\n"
        
        for i, user in enumerate(top_users, 1):
            if isinstance(user, tuple):
                user_id, username, first_name, stars = user
            else:
                user_id, username, first_name, stars = user.user_id, user.username, user.first_name, user.stars
            
            medal = ""
            if i == 1:
                medal = "🥇"
            elif i == 2:
                medal = "🥈"
            elif i == 3:
                medal = "🥉"
            else:
                medal = f"{i}."
            
            name = username or first_name or f"User{user_id}"
            stars_display = create_stars_display(stars)
            
            top_text += f"{medal} {html.quote(name)}: {stars} {stars_display}\n"
        
        top_text += "\n🎯 <i>Зарабатывайте звезды и поднимайтесь в топе!</i>"
        
        await message.answer(
            top_text,
            reply_markup=top_players_keyboard()
        )
        
    except Exception as e:
        logger.error(f"Ошибка загрузки топа: {e}")
        await message.answer("❌ Ошибка загрузки топа игроков. Попробуйте позже.")

@router.message(Command("tasks"))
@rate_limit(1, "tasks")
async def cmd_tasks(message: Message):
    """Задания для заработка звезд"""
    tasks_text = """
    🎯 <b>Доступные задания</b>

    1. 📢 <b>Подписка на канал</b>
       • Подпишитесь на наш канал
       • Награда: Проверка подписки для доступа к боту
       
    2. 👥 <b>Пригласите друга</b>
       • Используйте реферальную ссылку
       • Награда: +5 звезд за каждого друга
       
    3. 🎮 <b>Сыграйте в "Удачу"</b>
       • Играйте раз в 4 часа
       • Награда: от 0 до 10 звезд
       
    4. 📅 <b>Ежедневный бонус</b>
       • Заходите каждый день
       • Награда: от 1 до 5 звезд
       
    5. 💬 <b>Добавьте бота в группу</b>
       • Добавьте бота в группу от 10 человек
       • Награда: +2 звезды (однократно)
       
    ⭐ <b>Выполняйте задания и зарабатывайте звезды!</b>
    """
    
    await message.answer(
        tasks_text,
        reply_markup=tasks_keyboard()
    )

# ========== ОБРАБОТЧИКИ CALLBACK-ЗАПРОСОВ ==========

@router.callback_query(F.data == "check_subscription")
async def callback_check_subscription(callback: CallbackQuery):
    """Проверка подписки после нажатия кнопки"""
    user_id = callback.from_user.id
    
    try:
        is_subscribed = await check_subscription(bot, Config.CHANNEL_ID, user_id)
        
        if is_subscribed:
            if HAS_CUSTOM_MODULES:
                await db.update_subscription_status(user_id, True)
            
            await callback.message.edit_text(
                "✅ <b>Отлично! Вы подписаны на канал!</b>\n\n"
                "Теперь вам доступны все функции бота. Используйте меню ниже:",
                reply_markup=main_menu_keyboard()
            )
            await callback.answer("Подписка подтверждена!")
        else:
            await callback.answer(
                "❌ Вы еще не подписались на канал!",
                show_alert=True
            )
    except Exception as e:
        logger.error(f"Ошибка проверки подписки: {e}")
        await callback.answer("Ошибка проверки подписки", show_alert=True)

@router.callback_query(F.data == "play_luck_game")
async def callback_play_luck_game(callback: CallbackQuery):
    """Обработчик игры 'Удача'"""
    user_id = callback.from_user.id
    now = datetime.now()
    
    try:
        # Проверяем кулдаун
        if HAS_CUSTOM_MODULES:
            last_game = await db.get_last_luck_game(user_id)
        else:
            async with aiosqlite.connect("star_earn_bot.db") as conn:
                cursor = await conn.execute(
                    "SELECT last_luck_game FROM users WHERE user_id = ?", 
                    (user_id,)
                )
                result = await cursor.fetchone()
                last_game = result[0] if result and result[0] else None
                if last_game:
                    last_game = datetime.fromisoformat(last_game)
        
        if last_game and (now - last_game).total_seconds() < Config.LUCK_GAME_COOLDOWN:
            await callback.answer("Игра доступна раз в 4 часа!", show_alert=True)
            return
        
        # Генерируем случайный выигрыш
        import random
        reward = random.randint(Config.LUCK_GAME_MIN, Config.LUCK_GAME_MAX)
        
        # Обновляем данные
        if HAS_CUSTOM_MODULES:
            await db.add_stars(user_id, reward)
            await db.record_transaction(user_id, reward, "luck_game", "Мини-игра 'Удача'")
            await db.update_last_luck_game(user_id, now)
        else:
            async with aiosqlite.connect("star_earn_bot.db") as conn:
                await conn.execute(
                    "UPDATE users SET stars = stars + ?, last_luck_game = ? WHERE user_id = ?",
                    (reward, now.isoformat(), user_id)
                )
                await conn.execute(
                    """INSERT INTO transactions 
                    (user_id, amount, type, description) 
                    VALUES (?, ?, ?, ?)""",
                    (user_id, reward, "luck_game", "Мини-игра 'Удача'")
                )
                await conn.commit()
        
        # Результат игры
        stars_display = create_stars_display(reward)
        
        if reward == 0:
            result_text = "😔 К сожалению, вы не выиграли звезд в этот раз."
        elif reward <= 3:
            result_text = f"🎉 Неплохо! Вы выиграли {reward} {stars_display}"
        elif reward <= 7:
            result_text = f"🎊 Отлично! Вы выиграли {reward} {stars_display}"
        else:
            result_text = f"🔥 ВАУ! ДЖЕКПОТ! Вы выиграли {reward} {stars_display}"
        
        result_text += f"\n\n🎮 Следующая игра через 4 часа!"
        
        await callback.message.edit_text(
            result_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 В меню", callback_data="main_menu")]
            ])
        )
        await callback.answer(f"Вы выиграли {reward} звезд!")
        
    except Exception as e:
        logger.error(f"Ошибка в игре 'Удача': {e}")
        await callback.answer("Ошибка в игре", show_alert=True)

@router.callback_query(F.data == "main_menu")
async def callback_main_menu(callback: CallbackQuery):
    """Возврат в главное меню"""
    await callback.message.edit_text(
        "⭐ <b>Главное меню StarEarnBot</b>\n\n"
        "Выберите действие:",
        reply_markup=main_menu_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "show_profile")
async def callback_show_profile(callback: CallbackQuery):
    """Показать профиль из callback"""
    await cmd_profile(callback.message)
    await callback.answer()

@router.callback_query(F.data == "show_tasks")
async def callback_show_tasks(callback: CallbackQuery):
    """Показать задания из callback"""
    await cmd_tasks(callback.message)
    await callback.answer()

@router.callback_query(F.data == "show_referrals")
async def callback_show_referrals(callback: CallbackQuery):
    """Показать реферальную систему из callback"""
    await cmd_referral(callback.message)
    await callback.answer()

@router.callback_query(F.data == "show_top")
async def callback_show_top(callback: CallbackQuery):
    """Показать топ игроков из callback"""
    await cmd_top(callback.message)
    await callback.answer()

# ========== ОБРАБОТЧИК ГРУППОВЫХ СООБЩЕНИЙ ==========

@router.chat_member()
async def chat_member_update(update: ChatMemberUpdated):
    """Обработчик добавления бота в группу"""
    if update.new_chat_member.status == "member":
        # Бота добавили в группу
        chat_id = update.chat.id
        
        try:
            # Получаем количество участников
            chat = await bot.get_chat(chat_id)
            member_count = await bot.get_chat_member_count(chat_id)
            
            # Проверяем, что в группе >= 10 участников
            if member_count >= 10:
                # Ищем, кто добавил бота (администратор)
                # В реальности нужно отслеживать, кто был инициатором добавления
                # Здесь упрощенная логика - награждаем всех админов
                
                admins = await bot.get_chat_administrators(chat_id)
                for admin in admins:
                    user_id = admin.user.id
                    
                    # Проверяем, не бот ли это
                    if not admin.user.is_bot:
                        # Награждаем пользователя
                        if HAS_CUSTOM_MODULES:
                            # Проверяем, не получал ли уже награду
                            transactions = await db.get_user_transactions(user_id)
                            has_group_reward = any(
                                t.type == "group_add" and "чат" in t.description.lower() 
                                for t in transactions
                            )
                            
                            if not has_group_reward:
                                await db.add_stars(user_id, Config.GROUP_ADD_REWARD)
                                await db.record_transaction(
                                    user_id, 
                                    Config.GROUP_ADD_REWARD, 
                                    "group_add", 
                                    f"Добавление бота в чат: {chat.title}"
                                )
                                
                                # Уведомляем пользователя
                                try:
                                    await bot.send_message(
                                        user_id,
                                        f"🎉 <b>Бонус за добавление бота в группу!</b>\n\n"
                                        f"Вы добавили бота в группу <b>{html.quote(chat.title)}</b>\n"
                                        f"На ваш счет начислено +{Config.GROUP_ADD_REWARD} звезд!\n\n"
                                        f"⭐ Спасибо, что используете StarEarnBot!"
                                    )
                                except:
                                    pass  # Не можем отправить сообщение
                        else:
                            # Упрощенная логика для демо
                            async with aiosqlite.connect("star_earn_bot.db") as conn:
                                # Проверяем существование пользователя
                                cursor = await conn.execute(
                                    "SELECT user_id FROM users WHERE user_id = ?", 
                                    (user_id,)
                                )
                                user_exists = await cursor.fetchone()
                                
                                if user_exists:
                                    # Награждаем
                                    await conn.execute(
                                        "UPDATE users SET stars = stars + ? WHERE user_id = ?",
                                        (Config.GROUP_ADD_REWARD, user_id)
                                    )
                                    await conn.execute(
                                        """INSERT INTO transactions 
                                        (user_id, amount, type, description) 
                                        VALUES (?, ?, ?, ?)""",
                                        (user_id, Config.GROUP_ADD_REWARD, "group_add", 
                                         f"Добавление бота в чат: {chat.title}")
                                    )
                                    await conn.commit()
            
            # Отправляем приветствие в группу
            greeting = f"""
            👋 <b>Приветствую участников {html.quote(chat.title)}!</b>

            Я <b>StarEarnBot</b> - бот для заработка виртуальных "Звезд"!

            🎯 <b>Что я умею:</b>
            • Начислять ежедневные бонусы
            • Проводить мини-игры на удачу
            • Вести учет рефералов
            • Показывать топ игроков

            💫 <b>Для личного использования:</b>
            Напишите мне в личные сообщения: @{bot._me.username}

            ⭐ <b>Администраторы группы получили бонус за добавление бота!</b>
            """
            
            await bot.send_message(
                chat_id,
                greeting,
                parse_mode=ParseMode.HTML
            )
            
        except Exception as e:
            logger.error(f"Ошибка обработки добавления в группу: {e}")

# ========== АДМИН ПАНЕЛЬ ==========

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    """Админ панель"""
    user_id = message.from_user.id
    
    if user_id not in Config.ADMIN_IDS:
        await message.answer("❌ Доступ запрещен!")
        return
    
    admin_text = f"""
    ⚙️ <b>Админ панель</b>
    
    👑 Админ: {html.quote(message.from_user.first_name)}
    🆔 ID: <code>{user_id}</code>
    
    📊 <b>Статистика бота:</b>
    • 🧮 Всего пользователей: <i>загрузка...</i>
    • ⭐ Всего звезд в системе: <i>загрузка...</i>
    • 👥 Активных сегодня: <i>загрузка...</i>
    
    🔧 <b>Доступные команды:</b>
    /stats - Подробная статистика
    /broadcast - Рассылка сообщений
    /addstars - Добавить звезды пользователю
    """
    
    await message.answer(
        admin_text,
        reply_markup=admin_keyboard()
    )

# ========== ЗАПУСК БОТА ==========

async def on_startup():
    """Действия при запуске бота"""
    logger.info("=== StarEarnBot запускается ===")
    
    # Инициализация базы данных
    if HAS_CUSTOM_MODULES:
        await db.init_db()
    else:
        await db.init_db() if hasattr(db, 'init_db') else None
    
    # Запуск Flask в отдельном потоке для Keep-Alive
    if HAS_FLASK:
        flask_thread = Thread(target=run_flask, daemon=True)
        flask_thread.start()
        logger.info(f"Flask сервер запущен на порту {Config.FLASK_PORT}")
    
    logger.info("Бот успешно запущен!")

async def on_shutdown():
    """Действия при выключении бота"""
    logger.info("=== StarEarnBot выключается ===")
    
    # Закрытие соединения с БД
    if HAS_CUSTOM_MODULES and hasattr(db, 'close'):
        await db.close()
    
    logger.info("Бот успешно выключен.")

async def main():
    """Основная функция запуска бота"""
    # Регистрируем обработчики старта/остановки
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Проверяем наличие необходимых переменных окружения
    if not Config.BOT_TOKEN:
        logger.error("BOT_TOKEN не установлен!")
        sys.exit(1)
    
    # Запускаем бота
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        sys.exit(1)
