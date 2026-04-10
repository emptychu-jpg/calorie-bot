"""
🍎 Калорій Трекер Бот v2.0
Telegram бот для відстеження харчування з аналізом фото їжі через Claude AI
+ Профіль користувача, цілі, персоналізовані поради
"""

import os
import json
import sqlite3
import base64
import httpx
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

# ============== НАЛАШТУВАННЯ ==============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "ТВІЙ_TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "ТВІЙ_ANTHROPIC_API_KEY")

# Стани для ConversationHandler (створення профілю)
GENDER, AGE, WEIGHT, HEIGHT, ACTIVITY, GOAL = range(6)

# ============== БАЗА ДАНИХ ==============
def init_database():
    conn = sqlite3.connect("food_tracker.db")
    cursor = conn.cursor()
    
    # Таблиця прийомів їжі
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            food_name TEXT NOT NULL,
            calories INTEGER DEFAULT 0,
            protein REAL DEFAULT 0,
            fat REAL DEFAULT 0,
            carbs REAL DEFAULT 0,
            sugar REAL DEFAULT 0,
            fiber REAL DEFAULT 0,
            health_notes TEXT,
            photo_description TEXT
        )
    """)
    
    # Розширена таблиця користувачів
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            gender TEXT,
            age INTEGER,
            weight REAL,
            height REAL,
            activity_level TEXT,
            goal TEXT,
            daily_calories INTEGER,
            daily_protein INTEGER,
            daily_fat INTEGER,
            daily_carbs INTEGER,
            daily_sugar INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            profile_complete BOOLEAN DEFAULT 0
        )
    """)
    
    # Таблиця активностей
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            activity_type TEXT,
            duration_minutes INTEGER,
            steps INTEGER,
            calories_burned INTEGER,
            description TEXT
        )
    """)
    
    conn.commit()
    conn.close()

def get_user_profile(user_id: int) -> dict:
    """Отримує профіль користувача"""
    conn = sqlite3.connect("food_tracker.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT user_id, first_name, gender, age, weight, height, 
               activity_level, goal, daily_calories, daily_protein,
               daily_fat, daily_carbs, daily_sugar, profile_complete
        FROM users WHERE user_id = ?
    """, (user_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            "user_id": row[0],
            "first_name": row[1],
            "gender": row[2],
            "age": row[3],
            "weight": row[4],
            "height": row[5],
            "activity_level": row[6],
            "goal": row[7],
            "daily_calories": row[8],
            "daily_protein": row[9],
            "daily_fat": row[10],
            "daily_carbs": row[11],
            "daily_sugar": row[12],
            "profile_complete": row[13]
        }
    return None

def save_user_profile(user_id: int, profile: dict):
    """Зберігає профіль користувача"""
    conn = sqlite3.connect("food_tracker.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT OR REPLACE INTO users 
        (user_id, first_name, gender, age, weight, height, activity_level, goal,
         daily_calories, daily_protein, daily_fat, daily_carbs, daily_sugar, profile_complete)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        profile.get("first_name"),
        profile.get("gender"),
        profile.get("age"),
        profile.get("weight"),
        profile.get("height"),
        profile.get("activity_level"),
        profile.get("goal"),
        profile.get("daily_calories"),
        profile.get("daily_protein"),
        profile.get("daily_fat"),
        profile.get("daily_carbs"),
        profile.get("daily_sugar"),
        profile.get("profile_complete", 1)
    ))
    
    conn.commit()
    conn.close()

def calculate_daily_goals(gender: str, age: int, weight: float, height: float, 
                          activity_level: str, goal: str) -> dict:
    """Розраховує денну норму КБЖУ за формулою Міффліна-Сан Жеора"""
    
    # Базовий метаболізм (BMR)
    if gender == "чоловік":
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161
    
    # Коефіцієнт активності
    activity_multipliers = {
        "мінімальна": 1.2,      # сидячий спосіб життя
        "низька": 1.375,        # легкі тренування 1-3 рази на тиждень
        "середня": 1.55,        # тренування 3-5 разів на тиждень
        "висока": 1.725,        # інтенсивні тренування 6-7 разів на тиждень
        "дуже висока": 1.9      # фізична робота + тренування
    }
    
    multiplier = activity_multipliers.get(activity_level, 1.55)
    maintenance_calories = bmr * multiplier
    
    # Корекція під ціль
    if goal == "схуднення":
        daily_calories = maintenance_calories - 500  # дефіцит 500 ккал
    elif goal == "набір маси":
        daily_calories = maintenance_calories + 300  # профіцит 300 ккал
    else:  # підтримка
        daily_calories = maintenance_calories
    
    daily_calories = round(daily_calories)
    
    # Розрахунок БЖУ
    if goal == "набір маси":
        protein_ratio, fat_ratio, carbs_ratio = 0.25, 0.25, 0.50
    elif goal == "схуднення":
        protein_ratio, fat_ratio, carbs_ratio = 0.30, 0.30, 0.40
    else:
        protein_ratio, fat_ratio, carbs_ratio = 0.25, 0.30, 0.45
    
    daily_protein = round((daily_calories * protein_ratio) / 4)  # 4 ккал на грам
    daily_fat = round((daily_calories * fat_ratio) / 9)          # 9 ккал на грам
    daily_carbs = round((daily_calories * carbs_ratio) / 4)      # 4 ккал на грам
    daily_sugar = round(daily_calories * 0.05 / 4)               # макс 5% від калорій
    
    return {
        "daily_calories": daily_calories,
        "daily_protein": daily_protein,
        "daily_fat": daily_fat,
        "daily_carbs": daily_carbs,
        "daily_sugar": daily_sugar
    }

def save_meal(user_id: int, meal_data: dict):
    conn = sqlite3.connect("food_tracker.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO meals (user_id, food_name, calories, protein, fat, carbs, sugar, fiber, health_notes, photo_description)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        meal_data.get("food_name", "Невідома їжа"),
        meal_data.get("calories", 0),
        meal_data.get("protein", 0),
        meal_data.get("fat", 0),
        meal_data.get("carbs", 0),
        meal_data.get("sugar", 0),
        meal_data.get("fiber", 0),
        meal_data.get("health_notes", ""),
        meal_data.get("photo_description", "")
    ))
    
    conn.commit()
    conn.close()

def register_user(user_id: int, first_name: str):
    conn = sqlite3.connect("food_tracker.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, first_name) VALUES (?, ?)", (user_id, first_name))
    conn.commit()
    conn.close()

def get_stats(user_id: int, days: int = 1) -> dict:
    conn = sqlite3.connect("food_tracker.db")
    cursor = conn.cursor()
    
    if days == 1:
        date_filter = "DATE(timestamp) = DATE('now', 'localtime')"
    else:
        date_filter = f"timestamp >= datetime('now', '-{days} days', 'localtime')"
    
    cursor.execute(f"""
        SELECT 
            COUNT(*) as meal_count,
            COALESCE(SUM(calories), 0) as total_calories,
            COALESCE(SUM(protein), 0) as total_protein,
            COALESCE(SUM(fat), 0) as total_fat,
            COALESCE(SUM(carbs), 0) as total_carbs,
            COALESCE(SUM(sugar), 0) as total_sugar,
            COALESCE(SUM(fiber), 0) as total_fiber
        FROM meals 
        WHERE user_id = ? AND {date_filter}
    """, (user_id,))
    
    row = cursor.fetchone()
    
    cursor.execute(f"""
        SELECT food_name, calories, timestamp
        FROM meals 
        WHERE user_id = ? AND {date_filter}
        ORDER BY timestamp DESC
        LIMIT 20
    """, (user_id,))
    
    meals = cursor.fetchall()
    conn.close()
    
    return {
        "meal_count": row[0],
        "calories": row[1],
        "protein": round(row[2], 1),
        "fat": round(row[3], 1),
        "carbs": round(row[4], 1),
        "sugar": round(row[5], 1),
        "fiber": round(row[6], 1),
        "meals": meals,
        "days": days
    }

# ============== АНАЛІЗ ФОТО ЧЕРЕЗ CLAUDE API ==============
async def analyze_food_photo(photo_bytes: bytes, user_comment: str = None, user_profile: dict = None) -> dict:
    base64_image = base64.standard_b64encode(photo_bytes).decode("utf-8")
    
    # Базовий промпт
    prompt = """Проаналізуй це фото їжі та дай оцінку українською мовою.

Відповідь ОБОВ'ЯЗКОВО у форматі JSON (без markdown, тільки чистий JSON):
{
    "food_name": "Назва страви або продуктів",
    "calories": число (приблизна кількість калорій),
    "protein": число (грами білка),
    "fat": число (грами жирів),
    "carbs": число (грами вуглеводів),
    "sugar": число (грами цукру),
    "fiber": число (грами клітковини),
    "portion_size": "оцінка розміру порції",
    "health_notes": "короткий коментар про користь/шкоду",
    "photo_description": "що саме зображено на фото",
    "personalized_tip": "персоналізована порада"
}

Якщо на фото не їжа, поверни:
{"error": "На фото не знайдено їжі"}

Будь точним у підрахунку, враховуй видимий розмір порції."""

    # Додаємо персоналізацію якщо є профіль
    if user_profile and user_profile.get("profile_complete"):
        goal_text = {
            "схуднення": "схуднути (потрібен дефіцит калорій, більше білка)",
            "набір маси": "набрати м'язову масу (потрібен профіцит калорій, багато білка)",
            "підтримка": "підтримувати поточну форму"
        }.get(user_profile.get("goal"), "")
        
        prompt += f"""

ПРОФІЛЬ КОРИСТУВАЧА (обов'язково враховуй для персоналізованої поради):
- Стать: {user_profile.get('gender')}
- Вік: {user_profile.get('age')} років
- Вага: {user_profile.get('weight')} кг
- Зріст: {user_profile.get('height')} см
- Рівень активності: {user_profile.get('activity_level')}
- Ціль: {goal_text}
- Денна норма: {user_profile.get('daily_calories')} ккал, {user_profile.get('daily_protein')}г білка

В полі "personalized_tip" дай пораду саме для цієї людини з урахуванням її цілі!
Наприклад: "Для схуднення ця страва підходить, але краще зменшити порцію" або "Чудовий вибір для набору маси — багато білка!"."""

    # Додаємо коментар користувача якщо є
    if user_comment:
        prompt += f"""

Користувач додав уточнення: "{user_comment}"
Врахуй це при аналізі!"""

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "content-type": "application/json",
                    "anthropic-version": "2023-06-01"
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 1024,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/jpeg",
                                        "data": base64_image,
                                    },
                                },
                                {
                                    "type": "text",
                                    "text": prompt
                                }
                            ],
                        }
                    ],
                },
                timeout=60.0
            )
            
            data = response.json()
            response_text = data["content"][0]["text"]
            
            response_text = response_text.strip()
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
            response_text = response_text.strip()
            
            return json.loads(response_text)
            
    except json.JSONDecodeError:
        return {"error": "Не вдалося розпізнати відповідь AI"}
    except Exception as e:
        return {"error": f"Помилка аналізу: {str(e)}"}

# ============== КОМАНДИ ПРОФІЛЮ ==============
async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показує профіль або пропонує створити"""
    user = update.effective_user
    profile = get_user_profile(user.id)
    
    if profile and profile.get("profile_complete"):
        # Показуємо існуючий профіль
        goal_emoji = {"схуднення": "🔥", "набір маси": "💪", "підтримка": "⚖️"}.get(profile["goal"], "")
        
        text = f"""
👤 *Твій профіль*

📊 *Параметри:*
• Стать: {profile['gender']}
• Вік: {profile['age']} років
• Вага: {profile['weight']} кг
• Зріст: {profile['height']} см
• Активність: {profile['activity_level']}

🎯 *Ціль:* {goal_emoji} {profile['goal']}

📈 *Денна норма:*
🔥 Калорії: {profile['daily_calories']} ккал
🥩 Білки: {profile['daily_protein']} г
🧈 Жири: {profile['daily_fat']} г
🍞 Вуглеводи: {profile['daily_carbs']} г
🍬 Цукор: до {profile['daily_sugar']} г

✏️ /editprofile — змінити профіль
"""
        await update.message.reply_text(text, parse_mode="Markdown")
    else:
        # Пропонуємо створити
        await update.message.reply_text(
            "👤 У тебе ще немає профілю!\n\n"
            "Створи його, щоб отримувати персоналізовані поради та відстежувати прогрес до своєї цілі.\n\n"
            "Натисни /newprofile щоб почати 📝"
        )

async def new_profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Початок створення профілю"""
    keyboard = [["Чоловік 👨", "Жінка 👩"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    
    await update.message.reply_text(
        "📝 *Створення профілю*\n\n"
        "Крок 1/6: Обери стать:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    return GENDER

async def profile_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка статі"""
    text = update.message.text.lower()
    if "чоловік" in text:
        context.user_data["gender"] = "чоловік"
    else:
        context.user_data["gender"] = "жінка"
    
    await update.message.reply_text(
        "Крок 2/6: Введи свій вік (число):",
        reply_markup=ReplyKeyboardRemove()
    )
    return AGE

async def profile_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка віку"""
    try:
        age = int(update.message.text)
        if age < 10 or age > 120:
            await update.message.reply_text("❌ Введи реальний вік (10-120 років):")
            return AGE
        context.user_data["age"] = age
    except ValueError:
        await update.message.reply_text("❌ Введи число! Наприклад: 25")
        return AGE
    
    await update.message.reply_text("Крок 3/6: Введи свою вагу в кг (наприклад: 70):")
    return WEIGHT

async def profile_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка ваги"""
    try:
        weight = float(update.message.text.replace(",", "."))
        if weight < 30 or weight > 300:
            await update.message.reply_text("❌ Введи реальну вагу (30-300 кг):")
            return WEIGHT
        context.user_data["weight"] = weight
    except ValueError:
        await update.message.reply_text("❌ Введи число! Наприклад: 70")
        return WEIGHT
    
    await update.message.reply_text("Крок 4/6: Введи свій зріст в см (наприклад: 175):")
    return HEIGHT

async def profile_height(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка зросту"""
    try:
        height = float(update.message.text.replace(",", "."))
        if height < 100 or height > 250:
            await update.message.reply_text("❌ Введи реальний зріст (100-250 см):")
            return HEIGHT
        context.user_data["height"] = height
    except ValueError:
        await update.message.reply_text("❌ Введи число! Наприклад: 175")
        return HEIGHT
    
    keyboard = [
        ["Мінімальна 🪑"],
        ["Низька 🚶"],
        ["Середня 🏃"],
        ["Висока 💪"],
        ["Дуже висока 🔥"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    
    await update.message.reply_text(
        "Крок 5/6: Обери рівень активності:\n\n"
        "🪑 *Мінімальна* — сидяча робота, без тренувань\n"
        "🚶 *Низька* — легкі тренування 1-3 рази на тиждень\n"
        "🏃 *Середня* — тренування 3-5 разів на тиждень\n"
        "💪 *Висока* — інтенсивні тренування 6-7 разів на тиждень\n"
        "🔥 *Дуже висока* — фізична робота + тренування",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    return ACTIVITY

async def profile_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка активності"""
    text = update.message.text.lower()
    
    if "мінімальна" in text:
        activity = "мінімальна"
    elif "низька" in text:
        activity = "низька"
    elif "середня" in text:
        activity = "середня"
    elif "дуже висока" in text:
        activity = "дуже висока"
    elif "висока" in text:
        activity = "висока"
    else:
        activity = "середня"
    
    context.user_data["activity_level"] = activity
    
    keyboard = [
        ["Схуднення 🔥"],
        ["Набір маси 💪"],
        ["Підтримка ваги ⚖️"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    
    await update.message.reply_text(
        "Крок 6/6: Яка твоя ціль?",
        reply_markup=reply_markup
    )
    return GOAL

async def profile_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершення створення профілю"""
    text = update.message.text.lower()
    
    if "схуднення" in text:
        goal = "схуднення"
    elif "набір" in text:
        goal = "набір маси"
    else:
        goal = "підтримка"
    
    context.user_data["goal"] = goal
    
    # Розраховуємо денну норму
    daily_goals = calculate_daily_goals(
        context.user_data["gender"],
        context.user_data["age"],
        context.user_data["weight"],
        context.user_data["height"],
        context.user_data["activity_level"],
        goal
    )
    
    # Зберігаємо профіль
    user = update.effective_user
    profile = {
        "first_name": user.first_name,
        "gender": context.user_data["gender"],
        "age": context.user_data["age"],
        "weight": context.user_data["weight"],
        "height": context.user_data["height"],
        "activity_level": context.user_data["activity_level"],
        "goal": goal,
        "profile_complete": 1,
        **daily_goals
    }
    
    save_user_profile(user.id, profile)
    
    goal_emoji = {"схуднення": "🔥", "набір маси": "💪", "підтримка": "⚖️"}.get(goal, "")
    
    await update.message.reply_text(
        f"✅ *Профіль створено!*\n\n"
        f"🎯 Твоя ціль: {goal_emoji} {goal}\n\n"
        f"📈 *Твоя денна норма:*\n"
        f"🔥 Калорії: *{daily_goals['daily_calories']}* ккал\n"
        f"🥩 Білки: {daily_goals['daily_protein']} г\n"
        f"🧈 Жири: {daily_goals['daily_fat']} г\n"
        f"🍞 Вуглеводи: {daily_goals['daily_carbs']} г\n"
        f"🍬 Цукор: до {daily_goals['daily_sugar']} г\n\n"
        f"Тепер я буду давати персоналізовані поради! 🎯\n\n"
        f"📸 Надішли фото їжі, щоб почати трекінг!",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    
    return ConversationHandler.END

async def profile_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скасування створення профілю"""
    await update.message.reply_text(
        "❌ Створення профілю скасовано.\n"
        "Можеш почати знову: /newprofile",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# ============== ОСНОВНІ КОМАНДИ ==============
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.first_name)
    profile = get_user_profile(user.id)
    
    welcome_text = f"""
👋 Привіт, {user.first_name}!

Я твій персональний *Калорій Трекер* 🍎

📸 *Як користуватися:*
Надсилай мені фото своєї їжі, і я:
• Розпізнаю страву та порахую КБЖУ
• Дам персоналізовані поради
• Покажу прогрес до твоєї цілі

📊 *Команди:*
/today — статистика за сьогодні
/week — статистика за тиждень
/month — статистика за місяць
/profile — твій профіль
/help — допомога
"""
    
    if not profile or not profile.get("profile_complete"):
        welcome_text += "\n\n⚡ *Рекомендую почати з профілю:* /newprofile"
    
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🍎 *Калорій Трекер — Допомога*

📸 *Трекінг їжі:*
Сфотографуй їжу та надішли мені.
Можеш додати підпис: "смажене", "300г", "без цукру"

👤 *Профіль:*
/profile — переглянути профіль
/newprofile — створити/оновити профіль
/editprofile — змінити дані

📊 *Статистика:*
/today — за сьогодні (з прогресом до цілі)
/week — за тиждень
/month — за місяць

📈 *Що я рахую:*
🔥 Калорії
🥩 Білки, 🧈 Жири, 🍞 Вуглеводи
🍬 Цукор, 🥬 Клітковина
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats = get_stats(user_id, days=1)
    profile = get_user_profile(user_id)
    
    if stats["meal_count"] == 0:
        await update.message.reply_text(
            "📭 Сьогодні ти ще нічого не їв!\n\n"
            "Надішли фото їжі, щоб почати трекінг."
        )
        return
    
    meals_list = "\n".join([f"  • {meal[0]} — {meal[1]} ккал" for meal in stats["meals"]])
    
    # Базова статистика
    text = f"""
📊 *Статистика за сьогодні*

🔥 Калорії: *{stats['calories']}* ккал
🥩 Білки: {stats['protein']} г
🧈 Жири: {stats['fat']} г
🍞 Вуглеводи: {stats['carbs']} г
🍬 Цукор: {stats['sugar']} г
🥬 Клітковина: {stats['fiber']} г

🍽 Прийомів їжі: {stats['meal_count']}
"""
    
    # Додаємо прогрес якщо є профіль
    if profile and profile.get("profile_complete"):
        cal_goal = profile["daily_calories"]
        cal_left = cal_goal - stats["calories"]
        cal_percent = min(100, round(stats["calories"] / cal_goal * 100))
        
        # Прогрес-бар
        filled = int(cal_percent / 10)
        progress_bar = "▓" * filled + "░" * (10 - filled)
        
        if cal_left > 0:
            progress_text = f"Залишилось: *{cal_left}* ккал"
            emoji = "🟢" if cal_percent < 80 else "🟡"
        else:
            progress_text = f"Перевищено на: *{abs(cal_left)}* ккал"
            emoji = "🔴"
        
        text += f"""
━━━━━━━━━━━━━━━
🎯 *Прогрес до цілі:*

{emoji} [{progress_bar}] {cal_percent}%
{progress_text}

Ціль: {cal_goal} ккал ({profile['goal']})
"""
    
    text += f"""
━━━━━━━━━━━━━━━
📝 *Що ти їв:*
{meals_list}
"""
    
    await update.message.reply_text(text, parse_mode="Markdown")

async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats = get_stats(user_id, days=7)
    profile = get_user_profile(user_id)
    
    if stats["meal_count"] == 0:
        await update.message.reply_text("📭 За останній тиждень немає даних.")
        return
    
    avg_calories = round(stats["calories"] / 7)
    avg_sugar = round(stats["sugar"] / 7, 1)
    
    text = f"""
📊 *Статистика за тиждень*

🔥 Всього калорій: *{stats['calories']}* ккал
📈 В середньому на день: *{avg_calories}* ккал

🥩 Білки: {stats['protein']} г
🧈 Жири: {stats['fat']} г
🍞 Вуглеводи: {stats['carbs']} г
🍬 Цукор: {stats['sugar']} г (≈{avg_sugar} г/день)
🥬 Клітковина: {stats['fiber']} г

🍽 Прийомів їжі: {stats['meal_count']}
"""
    
    # Порівняння з ціллю
    if profile and profile.get("profile_complete"):
        cal_goal = profile["daily_calories"] * 7
        diff = stats["calories"] - cal_goal
        
        if abs(diff) < cal_goal * 0.05:
            verdict = "✅ Ти тримаєшся в межах норми!"
        elif diff < 0:
            verdict = f"🟢 Ти з'їв на {abs(diff)} ккал менше норми"
        else:
            verdict = f"🟡 Ти перевищив норму на {diff} ккал"
        
        text += f"\n🎯 *Ціль на тиждень:* {cal_goal} ккал\n{verdict}"
    
    await update.message.reply_text(text, parse_mode="Markdown")

async def month_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats = get_stats(user_id, days=30)
    
    if stats["meal_count"] == 0:
        await update.message.reply_text("📭 За останній місяць немає даних.")
        return
    
    avg_calories = round(stats["calories"] / 30)
    avg_sugar = round(stats["sugar"] / 30, 1)
    
    text = f"""
📊 *Статистика за місяць*

🔥 Всього калорій: *{stats['calories']}* ккал
📈 В середньому на день: *{avg_calories}* ккал

🥩 Білки: {stats['protein']} г
🧈 Жири: {stats['fat']} г
🍞 Вуглеводи: {stats['carbs']} г
🍬 Цукор: {stats['sugar']} г (≈{avg_sugar} г/день)
🥬 Клітковина: {stats['fiber']} г

🍽 Прийомів їжі: {stats['meal_count']}
"""
    await update.message.reply_text(text, parse_mode="Markdown")

# ============== ОБРОБКА ФОТО ==============
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.first_name)
    
    user_comment = update.message.caption
    profile = get_user_profile(user.id)
    
    if user_comment:
        processing_msg = await update.message.reply_text(
            f"🔍 Аналізую фото з урахуванням: _{user_comment}_",
            parse_mode="Markdown"
        )
    else:
        processing_msg = await update.message.reply_text("🔍 Аналізую фото...")
    
    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        photo_bytes = await file.download_as_bytearray()
        
        result = await analyze_food_photo(bytes(photo_bytes), user_comment, profile)
        
        if "error" in result:
            await processing_msg.edit_text(f"❌ {result['error']}")
            return
        
        save_meal(user.id, result)
        
        # Формуємо відповідь
        response = f"""
✅ *Записано!*

🍽 *{result.get('food_name', 'Страва')}*

🔥 Калорії: *{result.get('calories', 0)}* ккал
🥩 Білки: {result.get('protein', 0)} г
🧈 Жири: {result.get('fat', 0)} г
🍞 Вуглеводи: {result.get('carbs', 0)} г
🍬 Цукор: {result.get('sugar', 0)} г
🥬 Клітковина: {result.get('fiber', 0)} г

📏 Порція: {result.get('portion_size', 'не визначено')}
"""
        
        # Додаємо персоналізовану пораду
        if result.get('personalized_tip'):
            response += f"\n💡 *Порада для тебе:*\n_{result.get('personalized_tip')}_"
        elif result.get('health_notes'):
            response += f"\n💡 {result.get('health_notes')}"
        
        # Додаємо прогрес за день якщо є профіль
        if profile and profile.get("profile_complete"):
            stats = get_stats(user.id, days=1)
            cal_goal = profile["daily_calories"]
            cal_left = cal_goal - stats["calories"]
            
            if cal_left > 0:
                response += f"\n\n📊 Сьогодні з'їдено: {stats['calories']}/{cal_goal} ккал\n🎯 Залишилось: *{cal_left}* ккал"
            else:
                response += f"\n\n📊 Сьогодні з'їдено: {stats['calories']}/{cal_goal} ккал\n⚠️ Перевищено на: *{abs(cal_left)}* ккал"
        
        if user_comment:
            response += f"\n\n📝 Враховано: _{user_comment}_"
        
        await processing_msg.edit_text(response, parse_mode="Markdown")
        
    except Exception as e:
        await processing_msg.edit_text(f"❌ Помилка обробки: {str(e)}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📷 Надішли мені *фото їжі*, і я проаналізую його!\n\n"
        "💡 Можеш додати підпис для уточнення\n\n"
        "Або скористайся командами:\n"
        "/today — статистика за сьогодні\n"
        "/profile — твій профіль\n"
        "/help — допомога",
        parse_mode="Markdown"
    )

# ============== ЗАПУСК БОТА ==============
def main():
    init_database()
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Обробник створення профілю
    profile_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("newprofile", new_profile_start),
            CommandHandler("editprofile", new_profile_start)
        ],
        states={
            GENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_gender)],
            AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_age)],
            WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_weight)],
            HEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_height)],
            ACTIVITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_activity)],
            GOAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_goal)],
        },
        fallbacks=[CommandHandler("cancel", profile_cancel)],
    )
    
    app.add_handler(profile_conv_handler)
    
    # Основні команди
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("week", week_command))
    app.add_handler(CommandHandler("month", month_command))
    app.add_handler(CommandHandler("profile", profile_command))
    
    # Обробники повідомлень
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("🤖 Бот запущено!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
