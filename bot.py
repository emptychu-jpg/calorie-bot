"""
🍎 Калорій Трекер Бот v3.0
Telegram бот для відстеження харчування з аналізом фото їжі через Claude AI
+ Профіль, цілі, персоналізовані поради
+ Кнопка видалення, трекінг активності, вечірні звіти
"""

import os
import json
import sqlite3
import base64
import httpx
import re
from datetime import datetime, time
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# ============== НАЛАШТУВАННЯ ==============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "ТВІЙ_TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "ТВІЙ_ANTHROPIC_API_KEY")

# Стани для ConversationHandler
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
            notifications_enabled BOOLEAN DEFAULT 1,
            notification_time TEXT DEFAULT '21:00',
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
            duration_minutes INTEGER DEFAULT 0,
            steps INTEGER DEFAULT 0,
            calories_burned INTEGER DEFAULT 0,
            description TEXT
        )
    """)
    
    conn.commit()
    conn.close()

def get_user_profile(user_id: int) -> dict:
    conn = sqlite3.connect("food_tracker.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT user_id, first_name, gender, age, weight, height, 
               activity_level, goal, daily_calories, daily_protein,
               daily_fat, daily_carbs, daily_sugar, profile_complete,
               notifications_enabled
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
            "profile_complete": row[13],
            "notifications_enabled": row[14]
        }
    return None

def save_user_profile(user_id: int, profile: dict):
    conn = sqlite3.connect("food_tracker.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT OR REPLACE INTO users 
        (user_id, first_name, gender, age, weight, height, activity_level, goal,
         daily_calories, daily_protein, daily_fat, daily_carbs, daily_sugar, 
         profile_complete, notifications_enabled)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        profile.get("profile_complete", 1),
        profile.get("notifications_enabled", 1)
    ))
    
    conn.commit()
    conn.close()

def calculate_daily_goals(gender: str, age: int, weight: float, height: float, 
                          activity_level: str, goal: str) -> dict:
    # Базовий метаболізм (BMR) за Міффліном-Сан Жеором
    if gender == "чоловік":
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161
    
    activity_multipliers = {
        "мінімальна": 1.2,
        "низька": 1.375,
        "середня": 1.55,
        "висока": 1.725,
        "дуже висока": 1.9
    }
    
    multiplier = activity_multipliers.get(activity_level, 1.55)
    maintenance_calories = bmr * multiplier
    
    if goal == "схуднення":
        daily_calories = maintenance_calories - 500
    elif goal == "набір маси":
        daily_calories = maintenance_calories + 300
    else:
        daily_calories = maintenance_calories
    
    daily_calories = round(daily_calories)
    
    if goal == "набір маси":
        protein_ratio, fat_ratio, carbs_ratio = 0.25, 0.25, 0.50
    elif goal == "схуднення":
        protein_ratio, fat_ratio, carbs_ratio = 0.30, 0.30, 0.40
    else:
        protein_ratio, fat_ratio, carbs_ratio = 0.25, 0.30, 0.45
    
    daily_protein = round((daily_calories * protein_ratio) / 4)
    daily_fat = round((daily_calories * fat_ratio) / 9)
    daily_carbs = round((daily_calories * carbs_ratio) / 4)
    daily_sugar = round(daily_calories * 0.05 / 4)
    
    return {
        "daily_calories": daily_calories,
        "daily_protein": daily_protein,
        "daily_fat": daily_fat,
        "daily_carbs": daily_carbs,
        "daily_sugar": daily_sugar
    }

def save_meal(user_id: int, meal_data: dict) -> int:
    """Зберігає їжу і повертає ID запису"""
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
    
    meal_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return meal_id

def delete_meal(meal_id: int, user_id: int) -> bool:
    """Видаляє запис їжі. Повертає True якщо успішно."""
    conn = sqlite3.connect("food_tracker.db")
    cursor = conn.cursor()
    
    # Перевіряємо що запис належить цьому користувачу
    cursor.execute("SELECT id FROM meals WHERE id = ? AND user_id = ?", (meal_id, user_id))
    if not cursor.fetchone():
        conn.close()
        return False
    
    cursor.execute("DELETE FROM meals WHERE id = ? AND user_id = ?", (meal_id, user_id))
    conn.commit()
    conn.close()
    return True

def get_meal_by_id(meal_id: int) -> dict:
    """Отримує запис їжі за ID"""
    conn = sqlite3.connect("food_tracker.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, food_name, calories FROM meals WHERE id = ?
    """, (meal_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {"id": row[0], "food_name": row[1], "calories": row[2]}
    return None

def save_activity(user_id: int, activity_data: dict) -> int:
    """Зберігає активність і повертає ID"""
    conn = sqlite3.connect("food_tracker.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO activities (user_id, activity_type, duration_minutes, steps, calories_burned, description)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        activity_data.get("activity_type", ""),
        activity_data.get("duration_minutes", 0),
        activity_data.get("steps", 0),
        activity_data.get("calories_burned", 0),
        activity_data.get("description", "")
    ))
    
    activity_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return activity_id

def delete_activity(activity_id: int, user_id: int) -> bool:
    """Видаляє запис активності"""
    conn = sqlite3.connect("food_tracker.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM activities WHERE id = ? AND user_id = ?", (activity_id, user_id))
    if not cursor.fetchone():
        conn.close()
        return False
    
    cursor.execute("DELETE FROM activities WHERE id = ? AND user_id = ?", (activity_id, user_id))
    conn.commit()
    conn.close()
    return True

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
    
    # Статистика їжі
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
        SELECT id, food_name, calories, timestamp
        FROM meals 
        WHERE user_id = ? AND {date_filter}
        ORDER BY timestamp DESC
        LIMIT 20
    """, (user_id,))
    
    meals = cursor.fetchall()
    
    # Статистика активностей
    cursor.execute(f"""
        SELECT 
            COALESCE(SUM(calories_burned), 0) as total_burned,
            COALESCE(SUM(steps), 0) as total_steps,
            COALESCE(SUM(duration_minutes), 0) as total_duration
        FROM activities 
        WHERE user_id = ? AND {date_filter}
    """, (user_id,))
    
    activity_row = cursor.fetchone()
    
    cursor.execute(f"""
        SELECT id, activity_type, calories_burned, steps, duration_minutes, description
        FROM activities 
        WHERE user_id = ? AND {date_filter}
        ORDER BY timestamp DESC
    """, (user_id,))
    
    activities = cursor.fetchall()
    
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
        "days": days,
        "calories_burned": activity_row[0],
        "total_steps": activity_row[1],
        "total_activity_minutes": activity_row[2],
        "activities": activities
    }

def get_users_for_notification() -> list:
    """Отримує список користувачів для вечірнього звіту"""
    conn = sqlite3.connect("food_tracker.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT user_id FROM users 
        WHERE notifications_enabled = 1 AND profile_complete = 1
    """)
    
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users

def toggle_notifications(user_id: int, enabled: bool):
    """Вмикає/вимикає сповіщення"""
    conn = sqlite3.connect("food_tracker.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET notifications_enabled = ? WHERE user_id = ?", (enabled, user_id))
    conn.commit()
    conn.close()

# ============== АНАЛІЗ АКТИВНОСТІ ==============
def parse_activity(text: str, user_profile: dict = None) -> dict:
    """Розпізнає активність з тексту і рахує спалені калорії"""
    text_lower = text.lower()
    
    # Вага користувача (для розрахунку калорій)
    weight = 70  # за замовчуванням
    if user_profile and user_profile.get("weight"):
        weight = user_profile["weight"]
    
    result = {
        "activity_type": "",
        "duration_minutes": 0,
        "steps": 0,
        "calories_burned": 0,
        "description": text
    }
    
    # Розпізнавання кроків
    steps_match = re.search(r'(\d+)\s*(?:кроків|кроки|крок|steps|к\.)', text_lower)
    if steps_match:
        steps = int(steps_match.group(1))
        result["steps"] = steps
        result["activity_type"] = "кроки"
        # Приблизно 0.04 ккал на крок для людини 70 кг
        result["calories_burned"] = round(steps * 0.04 * (weight / 70))
        return result
    
    # Розпізнавання часу
    duration = 0
    time_match = re.search(r'(\d+)\s*(?:хв|хвилин|минут|мін|min|m)', text_lower)
    if time_match:
        duration = int(time_match.group(1))
    
    hour_match = re.search(r'(\d+)\s*(?:год|година|години|годин|hour|h)', text_lower)
    if hour_match:
        duration += int(hour_match.group(1)) * 60
    
    result["duration_minutes"] = duration
    
    # MET значення для різних активностей (метаболічний еквівалент)
    activities = {
        "біг": 9.8,
        "пробіж": 9.8,
        "running": 9.8,
        "run": 9.8,
        "бігав": 9.8,
        "бігала": 9.8,
        
        "ходьба": 3.8,
        "прогулянка": 3.8,
        "гуляла": 3.8,
        "гуляв": 3.8,
        "walk": 3.8,
        "walking": 3.8,
        
        "велосипед": 7.5,
        "велик": 7.5,
        "cycling": 7.5,
        "bike": 7.5,
        
        "плавання": 6.0,
        "плавала": 6.0,
        "плавав": 6.0,
        "swim": 6.0,
        "басейн": 6.0,
        
        "тренування": 6.0,
        "тренажерка": 6.0,
        "зал": 6.0,
        "gym": 6.0,
        "workout": 6.0,
        "фітнес": 5.5,
        "fitness": 5.5,
        
        "йога": 3.0,
        "yoga": 3.0,
        "розтяжка": 2.5,
        "stretch": 2.5,
        
        "танці": 5.0,
        "dance": 5.0,
        "танцювала": 5.0,
        "танцював": 5.0,
        
        "футбол": 7.0,
        "баскетбол": 6.5,
        "волейбол": 4.0,
        "теніс": 7.0,
        
        "прибирання": 3.5,
        "домашні справи": 3.0,
        
        "сходи": 8.0,
        "stairs": 8.0,
    }
    
    # Шукаємо активність у тексті
    met_value = 5.0  # за замовчуванням — помірна активність
    for activity, met in activities.items():
        if activity in text_lower:
            result["activity_type"] = activity
            met_value = met
            break
    
    if not result["activity_type"]:
        result["activity_type"] = "активність"
    
    # Розрахунок калорій: MET * вага(кг) * час(год)
    if duration > 0:
        result["calories_burned"] = round(met_value * weight * (duration / 60))
    
    return result

# ============== АНАЛІЗ ФОТО ЧЕРЕЗ CLAUDE API ==============
async def analyze_food_photo(photo_bytes: bytes, user_comment: str = None, user_profile: dict = None) -> dict:
    base64_image = base64.standard_b64encode(photo_bytes).decode("utf-8")
    
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

    if user_profile and user_profile.get("profile_complete"):
        goal_text = {
            "схуднення": "схуднути (потрібен дефіцит калорій, більше білка)",
            "набір маси": "набрати м'язову масу (потрібен профіцит калорій, багато білка)",
            "підтримка": "підтримувати поточну форму"
        }.get(user_profile.get("goal"), "")
        
        prompt += f"""

ПРОФІЛЬ КОРИСТУВАЧА:
- Стать: {user_profile.get('gender')}
- Вік: {user_profile.get('age')} років
- Вага: {user_profile.get('weight')} кг
- Ціль: {goal_text}
- Денна норма: {user_profile.get('daily_calories')} ккал

В полі "personalized_tip" дай пораду саме для цієї людини!"""

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
                                {"type": "text", "text": prompt}
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

# ============== АНАЛІЗ ЇЖІ З ТЕКСТУ ЧЕРЕЗ CLAUDE API ==============
async def analyze_food_text(text: str, user_profile: dict = None) -> dict:
    """Аналізує опис їжі з тексту (без фото) і рахує калорії/БЖУ"""
    
    prompt = f"""Користувач описав що він з'їв українською мовою. Проаналізуй текст і порахуй калорії та БЖУ.

ОПИС КОРИСТУВАЧА: "{text}"

Відповідь ОБОВ'ЯЗКОВО у форматі JSON (без markdown, тільки чистий JSON):
{{
    "food_name": "Назва страви або продуктів",
    "calories": число (приблизна кількість калорій),
    "protein": число (грами білка),
    "fat": число (грами жирів),
    "carbs": число (грами вуглеводів),
    "sugar": число (грами цукру),
    "fiber": число (грами клітковини),
    "portion_size": "оцінка розміру порції",
    "health_notes": "короткий коментар про користь/шкоду",
    "photo_description": "опис того, що з'їв користувач",
    "personalized_tip": "персоналізована порада"
}}

Якщо текст НЕ описує їжу (наприклад, це питання, привітання, активність, випадкове повідомлення), поверни:
{{"not_food": true}}

ВАЖЛИВО:
- Якщо розмір порції не вказано — припускай стандартну порцію для такого продукту
- Якщо вказано "велика"/"маленька" порція — враховуй це
- Якщо вказано вагу/кількість (напр. "200г курки", "2 яйця") — рахуй точно за цим
- Будь реалістичним у підрахунку, не занижуй і не завищуй"""

    if user_profile and user_profile.get("profile_complete"):
        goal_text = {
            "схуднення": "схуднути (потрібен дефіцит калорій, більше білка)",
            "набір маси": "набрати м'язову масу (потрібен профіцит калорій, багато білка)",
            "підтримка": "підтримувати поточну форму"
        }.get(user_profile.get("goal"), "")
        
        prompt += f"""

ПРОФІЛЬ КОРИСТУВАЧА:
- Стать: {user_profile.get('gender')}
- Вік: {user_profile.get('age')} років
- Вага: {user_profile.get('weight')} кг
- Ціль: {goal_text}
- Денна норма: {user_profile.get('daily_calories')} ккал

В полі "personalized_tip" дай пораду саме для цієї людини!"""

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
                            "content": prompt,
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

# ============== CALLBACK HANDLERS (кнопки) ==============
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка натискання inline кнопок"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    # Видалення їжі
    if data.startswith("delete_meal_"):
        meal_id = int(data.replace("delete_meal_", ""))
        meal = get_meal_by_id(meal_id)
        
        if meal and delete_meal(meal_id, user_id):
            await query.edit_message_text(
                f"🗑 *Видалено:* {meal['food_name']} ({meal['calories']} ккал)\n\n"
                f"Запис успішно видалено з статистики.",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text("❌ Не вдалося видалити запис.")
    
    # Видалення активності
    elif data.startswith("delete_activity_"):
        activity_id = int(data.replace("delete_activity_", ""))
        
        if delete_activity(activity_id, user_id):
            await query.edit_message_text(
                "🗑 Активність видалено з статистики.",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text("❌ Не вдалося видалити запис.")

# ============== КОМАНДИ ПРОФІЛЮ ==============
async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    profile = get_user_profile(user.id)
    
    if profile and profile.get("profile_complete"):
        goal_emoji = {"схуднення": "🔥", "набір маси": "💪", "підтримка": "⚖️"}.get(profile["goal"], "")
        notif_status = "✅ Увімкнено" if profile.get("notifications_enabled") else "❌ Вимкнено"
        
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

🔔 *Вечірні звіти:* {notif_status}

✏️ /editprofile — змінити профіль
🔔 /notifications — налаштувати сповіщення
"""
        await update.message.reply_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(
            "👤 У тебе ще немає профілю!\n\n"
            "Натисни /newprofile щоб створити 📝"
        )

async def new_profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["Чоловік 👨", "Жінка 👩"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    
    await update.message.reply_text(
        "📝 *Створення профілю*\n\nКрок 1/6: Обери стать:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    return GENDER

async def profile_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    context.user_data["gender"] = "чоловік" if "чоловік" in text else "жінка"
    
    await update.message.reply_text(
        "Крок 2/6: Введи свій вік:",
        reply_markup=ReplyKeyboardRemove()
    )
    return AGE

async def profile_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        age = int(update.message.text)
        if age < 10 or age > 120:
            await update.message.reply_text("❌ Введи реальний вік (10-120):")
            return AGE
        context.user_data["age"] = age
    except ValueError:
        await update.message.reply_text("❌ Введи число!")
        return AGE
    
    await update.message.reply_text("Крок 3/6: Введи вагу в кг:")
    return WEIGHT

async def profile_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        weight = float(update.message.text.replace(",", "."))
        if weight < 30 or weight > 300:
            await update.message.reply_text("❌ Введи реальну вагу (30-300 кг):")
            return WEIGHT
        context.user_data["weight"] = weight
    except ValueError:
        await update.message.reply_text("❌ Введи число!")
        return WEIGHT
    
    await update.message.reply_text("Крок 4/6: Введи зріст в см:")
    return HEIGHT

async def profile_height(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        height = float(update.message.text.replace(",", "."))
        if height < 100 or height > 250:
            await update.message.reply_text("❌ Введи реальний зріст (100-250 см):")
            return HEIGHT
        context.user_data["height"] = height
    except ValueError:
        await update.message.reply_text("❌ Введи число!")
        return HEIGHT
    
    keyboard = [
        ["Мінімальна 🪑"], ["Низька 🚶"], ["Середня 🏃"], 
        ["Висока 💪"], ["Дуже висока 🔥"]
    ]
    
    await update.message.reply_text(
        "Крок 5/6: Рівень активності:\n\n"
        "🪑 Мінімальна — сидяча робота\n"
        "🚶 Низька — 1-3 тренування/тиждень\n"
        "🏃 Середня — 3-5 тренувань/тиждень\n"
        "💪 Висока — 6-7 тренувань/тиждень\n"
        "🔥 Дуже висока — фізична робота + спорт",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return ACTIVITY

async def profile_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    
    activity_map = {
        "мінімальна": "мінімальна",
        "низька": "низька", 
        "середня": "середня",
        "дуже висока": "дуже висока",
        "висока": "висока"
    }
    
    activity = "середня"
    for key, value in activity_map.items():
        if key in text:
            activity = value
            break
    
    context.user_data["activity_level"] = activity
    
    keyboard = [["Схуднення 🔥"], ["Набір маси 💪"], ["Підтримка ваги ⚖️"]]
    
    await update.message.reply_text(
        "Крок 6/6: Яка твоя ціль?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return GOAL

async def profile_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower()
    
    if "схуднення" in text:
        goal = "схуднення"
    elif "набір" in text:
        goal = "набір маси"
    else:
        goal = "підтримка"
    
    daily_goals = calculate_daily_goals(
        context.user_data["gender"],
        context.user_data["age"],
        context.user_data["weight"],
        context.user_data["height"],
        context.user_data["activity_level"],
        goal
    )
    
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
        "notifications_enabled": 1,
        **daily_goals
    }
    
    save_user_profile(user.id, profile)
    
    goal_emoji = {"схуднення": "🔥", "набір маси": "💪", "підтримка": "⚖️"}.get(goal, "")
    
    await update.message.reply_text(
        f"✅ *Профіль створено!*\n\n"
        f"🎯 Ціль: {goal_emoji} {goal}\n\n"
        f"📈 *Денна норма:*\n"
        f"🔥 Калорії: *{daily_goals['daily_calories']}* ккал\n"
        f"🥩 Білки: {daily_goals['daily_protein']} г\n"
        f"🧈 Жири: {daily_goals['daily_fat']} г\n"
        f"🍞 Вуглеводи: {daily_goals['daily_carbs']} г\n\n"
        f"🔔 Вечірні звіти увімкнено (21:00)\n\n"
        f"📸 Надішли фото їжі, щоб почати!",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    
    return ConversationHandler.END

async def profile_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ Скасовано. /newprofile щоб почати знову.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# ============== СПОВІЩЕННЯ ==============
async def notifications_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Налаштування сповіщень"""
    user_id = update.effective_user.id
    profile = get_user_profile(user_id)
    
    if not profile:
        await update.message.reply_text("Спочатку створи профіль: /newprofile")
        return
    
    keyboard = [
        [InlineKeyboardButton("✅ Увімкнути", callback_data="notif_on"),
         InlineKeyboardButton("❌ Вимкнути", callback_data="notif_off")]
    ]
    
    current = "✅ Увімкнено" if profile.get("notifications_enabled") else "❌ Вимкнено"
    
    await update.message.reply_text(
        f"🔔 *Вечірні звіти*\n\n"
        f"Щодня о 21:00 я надсилатиму підсумок дня.\n\n"
        f"Поточний статус: {current}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def notification_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка кнопок сповіщень"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if query.data == "notif_on":
        toggle_notifications(user_id, True)
        await query.edit_message_text("🔔 Вечірні звіти *увімкнено*!\n\nЩодня о 21:00 отримуватимеш підсумок дня.", parse_mode="Markdown")
    elif query.data == "notif_off":
        toggle_notifications(user_id, False)
        await query.edit_message_text("🔕 Вечірні звіти *вимкнено*.\n\n/notifications щоб увімкнути знову.", parse_mode="Markdown")

# ============== ВЕЧІРНІЙ ЗВІТ ==============
async def send_evening_report(context: ContextTypes.DEFAULT_TYPE):
    """Надсилає вечірній звіт всім користувачам"""
    users = get_users_for_notification()
    
    for user_id in users:
        try:
            stats = get_stats(user_id, days=1)
            profile = get_user_profile(user_id)
            
            if not profile:
                continue
            
            # Формуємо звіт
            cal_goal = profile.get("daily_calories", 2000)
            cal_eaten = stats["calories"]
            cal_burned = stats.get("calories_burned", 0)
            net_calories = cal_eaten - cal_burned
            cal_left = cal_goal - net_calories
            
            # Прогрес
            percent = min(100, round(net_calories / cal_goal * 100)) if cal_goal > 0 else 0
            filled = int(percent / 10)
            bar = "▓" * filled + "░" * (10 - filled)
            
            if stats["meal_count"] == 0:
                text = (
                    "🌙 *Вечірній звіт*\n\n"
                    "📭 Сьогодні ти нічого не записував.\n\n"
                    "Не забувай фотографувати їжу! 📸"
                )
            else:
                if cal_left > 0:
                    verdict = f"✅ Залишилось {cal_left} ккал"
                elif cal_left > -200:
                    verdict = f"⚠️ Трохи перевищено ({abs(cal_left)} ккал)"
                else:
                    verdict = f"🔴 Перевищено на {abs(cal_left)} ккал"
                
                text = f"""
🌙 *Вечірній звіт*

📊 *Сьогодні:*
🍽 Прийомів їжі: {stats['meal_count']}
🔥 Спожито: {cal_eaten} ккал
"""
                
                if cal_burned > 0:
                    text += f"💪 Спалено: {cal_burned} ккал\n"
                    text += f"📊 Нетто: {net_calories} ккал\n"
                
                if stats.get("total_steps", 0) > 0:
                    text += f"👟 Кроків: {stats['total_steps']}\n"
                
                text += f"""
━━━━━━━━━━━━
🎯 *Прогрес:*
[{bar}] {percent}%
{verdict}

Ціль: {cal_goal} ккал ({profile.get('goal', 'підтримка')})

Гарного відпочинку! 😴
"""
            
            await context.bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
            
        except Exception as e:
            print(f"Error sending report to {user_id}: {e}")

# ============== ОСНОВНІ КОМАНДИ ==============
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.first_name)
    profile = get_user_profile(user.id)
    
    welcome_text = f"""
👋 Привіт, {user.first_name}!

Я твій *Калорій Трекер* 🍎

📸 *Їжа (фото):* надсилай фото — я порахую калорії
✍️ *Їжа (текст):* просто напиши що з'їв — наприклад "2 яйця і тост з авокадо"
🏃 *Активність:* пиши текстом — "пробіжка 30 хв" або "8000 кроків"

📊 *Команди:*
/today — статистика за сьогодні
/profile — твій профіль
/notifications — налаштувати звіти
/help — допомога
"""
    
    if not profile or not profile.get("profile_complete"):
        welcome_text += "\n⚡ *Почни з профілю:* /newprofile"
    
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🍎 *Калорій Трекер — Допомога*

📸 *Їжа (фото):*
Надішли фото — я проаналізую.
Можеш додати підпис: "300г", "без цукру"

✍️ *Їжа (текст):*
Просто напиши що з'їв, наприклад:
• "з'їв 2 яйця і тост з авокадо"
• "обід: борщ 300мл і котлета"
• "випила каву з молоком і круасан"
• "200г курки з гречкою"

🏃 *Активність:*
Напиши текстом, наприклад:
• "пробіжка 30 хв"
• "10000 кроків"  
• "тренування в залі 1 година"
• "прогулянка 45 хвилин"

👤 *Профіль:*
/profile — переглянути
/newprofile — створити
/editprofile — змінити

📊 *Статистика:*
/today — сьогодні
/week — тиждень
/month — місяць

🔔 *Сповіщення:*
/notifications — вечірні звіти
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats = get_stats(user_id, days=1)
    profile = get_user_profile(user_id)
    
    if stats["meal_count"] == 0 and not stats.get("activities"):
        await update.message.reply_text(
            "📭 Сьогодні поки пусто!\n\n"
            "📸 Надішли фото їжі\n"
            "🏃 Або напиши про активність"
        )
        return
    
    text = "📊 *Статистика за сьогодні*\n\n"
    
    # Їжа
    if stats["meal_count"] > 0:
        text += f"🍽 *Їжа:*\n"
        text += f"🔥 Спожито: *{stats['calories']}* ккал\n"
        text += f"🥩 Б: {stats['protein']}г 🧈 Ж: {stats['fat']}г 🍞 В: {stats['carbs']}г\n"
        text += f"🍬 Цукор: {stats['sugar']}г 🥬 Клітковина: {stats['fiber']}г\n\n"
    
    # Активність
    if stats.get("calories_burned", 0) > 0 or stats.get("total_steps", 0) > 0:
        text += f"💪 *Активність:*\n"
        if stats.get("total_steps", 0) > 0:
            text += f"👟 Кроків: {stats['total_steps']}\n"
        if stats.get("calories_burned", 0) > 0:
            text += f"🔥 Спалено: {stats['calories_burned']} ккал\n"
        if stats.get("total_activity_minutes", 0) > 0:
            text += f"⏱ Час: {stats['total_activity_minutes']} хв\n"
        text += "\n"
    
    # Прогрес
    if profile and profile.get("profile_complete"):
        cal_goal = profile["daily_calories"]
        cal_eaten = stats["calories"]
        cal_burned = stats.get("calories_burned", 0)
        net_calories = cal_eaten - cal_burned
        cal_left = cal_goal - net_calories
        
        percent = min(100, round(net_calories / cal_goal * 100)) if cal_goal > 0 else 0
        filled = int(percent / 10)
        bar = "▓" * filled + "░" * (10 - filled)
        
        emoji = "🟢" if percent < 80 else ("🟡" if percent < 100 else "🔴")
        
        text += f"━━━━━━━━━━━━\n"
        text += f"🎯 *Прогрес:*\n"
        text += f"{emoji} [{bar}] {percent}%\n"
        
        if cal_left > 0:
            text += f"Залишилось: *{cal_left}* ккал\n"
        else:
            text += f"Перевищено на: *{abs(cal_left)}* ккал\n"
    
    # Список їжі
    if stats["meals"]:
        text += f"\n📝 *Що їв:*\n"
        for meal in stats["meals"][:5]:
            text += f"• {meal[1]} — {meal[2]} ккал\n"
    
    await update.message.reply_text(text, parse_mode="Markdown")

async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats = get_stats(user_id, days=7)
    profile = get_user_profile(user_id)
    
    if stats["meal_count"] == 0:
        await update.message.reply_text("📭 За тиждень немає даних.")
        return
    
    avg_cal = round(stats["calories"] / 7)
    
    text = f"""
📊 *Статистика за тиждень*

🔥 Всього: *{stats['calories']}* ккал
📈 Середнє: *{avg_cal}* ккал/день

🥩 Білки: {stats['protein']} г
🧈 Жири: {stats['fat']} г
🍞 Вуглеводи: {stats['carbs']} г
🍬 Цукор: {stats['sugar']} г

🍽 Прийомів їжі: {stats['meal_count']}
"""
    
    if stats.get("calories_burned", 0) > 0:
        text += f"\n💪 Спалено: {stats['calories_burned']} ккал"
    if stats.get("total_steps", 0) > 0:
        text += f"\n👟 Кроків: {stats['total_steps']}"
    
    if profile and profile.get("profile_complete"):
        goal_week = profile["daily_calories"] * 7
        diff = stats["calories"] - goal_week
        
        if abs(diff) < goal_week * 0.05:
            verdict = "✅ В межах норми!"
        elif diff < 0:
            verdict = f"🟢 На {abs(diff)} ккал менше норми"
        else:
            verdict = f"🟡 На {diff} ккал більше норми"
        
        text += f"\n\n🎯 Ціль: {goal_week} ккал\n{verdict}"
    
    await update.message.reply_text(text, parse_mode="Markdown")

async def month_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats = get_stats(user_id, days=30)
    
    if stats["meal_count"] == 0:
        await update.message.reply_text("📭 За місяць немає даних.")
        return
    
    avg_cal = round(stats["calories"] / 30)
    
    text = f"""
📊 *Статистика за місяць*

🔥 Всього: *{stats['calories']}* ккал
📈 Середнє: *{avg_cal}* ккал/день

🥩 Білки: {stats['protein']} г
🧈 Жири: {stats['fat']} г
🍞 Вуглеводи: {stats['carbs']} г
🍬 Цукор: {stats['sugar']} г

🍽 Прийомів їжі: {stats['meal_count']}
"""
    
    if stats.get("calories_burned", 0) > 0:
        text += f"\n💪 Спалено: {stats['calories_burned']} ккал"
    if stats.get("total_steps", 0) > 0:
        text += f"\n👟 Кроків: {stats['total_steps']}"
    
    await update.message.reply_text(text, parse_mode="Markdown")

# ============== ОБРОБКА ФОТО ==============
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.first_name)
    
    user_comment = update.message.caption
    profile = get_user_profile(user.id)
    
    processing_msg = await update.message.reply_text("🔍 Аналізую фото...")
    
    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        photo_bytes = await file.download_as_bytearray()
        
        result = await analyze_food_photo(bytes(photo_bytes), user_comment, profile)
        
        if "error" in result:
            await processing_msg.edit_text(f"❌ {result['error']}")
            return
        
        meal_id = save_meal(user.id, result)
        
        # Відповідь
        response = f"""
✅ *Записано!*

🍽 *{result.get('food_name', 'Страва')}*

🔥 Калорії: *{result.get('calories', 0)}* ккал
🥩 Б: {result.get('protein', 0)}г 🧈 Ж: {result.get('fat', 0)}г 🍞 В: {result.get('carbs', 0)}г
🍬 Цукор: {result.get('sugar', 0)}г 🥬 Клітковина: {result.get('fiber', 0)}г
"""
        
        if result.get('personalized_tip'):
            response += f"\n💡 _{result.get('personalized_tip')}_"
        
        # Прогрес
        if profile and profile.get("profile_complete"):
            stats = get_stats(user.id, days=1)
            cal_goal = profile["daily_calories"]
            cal_left = cal_goal - stats["calories"]
            
            if cal_left > 0:
                response += f"\n\n📊 {stats['calories']}/{cal_goal} ккал • Залишилось: *{cal_left}*"
            else:
                response += f"\n\n📊 {stats['calories']}/{cal_goal} ккал • ⚠️ +{abs(cal_left)}"
        
        # Кнопка видалення
        keyboard = [[InlineKeyboardButton("🗑 Видалити", callback_data=f"delete_meal_{meal_id}")]]
        
        await processing_msg.edit_text(
            response, 
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        await processing_msg.edit_text(f"❌ Помилка: {str(e)}")

# ============== ОБРОБКА ТЕКСТУ (активність або їжа) ==============
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.first_name)
    
    original_text = update.message.text
    text = original_text.lower()
    
    # Перевіряємо чи це схоже на активність
    activity_keywords = [
        'крок', 'кроків', 'біг', 'пробіж', 'ходьба', 'прогулянка', 'гуля',
        'велосипед', 'велик', 'плавання', 'плавав', 'плавала', 'басейн',
        'тренування', 'тренажер', 'зал', 'gym', 'фітнес', 'йога',
        'танці', 'танцював', 'танцювала', 'футбол', 'баскетбол',
    ]
    
    # Ключові слова які сильно вказують що це їжа (щоб не плутати з активністю)
    food_keywords = [
        "з'їв", "з'їла", "поїв", "поїла", "з'їли", "їв", "їла", "їм", "їсти",
        "випив", "випила", "пив", "пила", "випили",
        "сніданок", "обід", "вечеря", "перекус", "снек",
        "на сніданок", "на обід", "на вечерю",
        "страва", "порція", "тарілка", "шматок", "кусок",
        "грам", "г ", "мл ", "кг ",
        "калорій", "ккал",
        "круасан", "бутерброд", "салат", "суп", "борщ", "піца", "бургер",
        "каша", "яєчня", "омлет", "яйце", "яйця", "курка", "м'ясо",
        "риба", "риби", "рис", "гречка", "макарони", "паста",
        "хліб", "булочка", "печиво", "тортик", "торт", "шоколад",
        "кава", "чай", "молоко", "йогурт", "сир", "сметана",
        "яблуко", "банан", "апельсин", "виноград",
    ]
    
    is_food = any(keyword in text for keyword in food_keywords)
    is_activity_word = any(keyword in text for keyword in activity_keywords)
    
    # Перевірка на час (може бути і в активності і в їжі, тому окремо)
    has_time = bool(re.search(r'\d+\s*(?:хв|хвилин|мін|min|год|година|годин)', text))
    
    # Якщо є слова активності (АЛЕ немає яскравих слів їжі) — обробляємо як активність
    if is_activity_word and not is_food:
        profile = get_user_profile(user.id)
        activity = parse_activity(original_text, profile)
        
        if activity["calories_burned"] > 0 or activity["steps"] > 0:
            activity_id = save_activity(user.id, activity)
            
            response = "💪 *Активність записано!*\n\n"
            
            if activity["steps"] > 0:
                response += f"👟 Кроків: *{activity['steps']}*\n"
            if activity["duration_minutes"] > 0:
                response += f"⏱ Тривалість: {activity['duration_minutes']} хв\n"
            if activity["activity_type"]:
                response += f"🏃 Тип: {activity['activity_type']}\n"
            
            response += f"🔥 Спалено: *{activity['calories_burned']}* ккал"
            
            # Прогрес з урахуванням активності
            if profile and profile.get("profile_complete"):
                stats = get_stats(user.id, days=1)
                cal_goal = profile["daily_calories"]
                net = stats["calories"] - stats.get("calories_burned", 0)
                cal_left = cal_goal - net
                
                response += f"\n\n📊 Нетто за день: {net} ккал"
                if cal_left > 0:
                    response += f" • Залишилось: *{cal_left}*"
            
            keyboard = [[InlineKeyboardButton("🗑 Видалити", callback_data=f"delete_activity_{activity_id}")]]
            
            await update.message.reply_text(
                response,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
    
    # Пробуємо розпізнати як опис їжі через Claude AI
    # (якщо є яскраві слова їжі АБО текст достатньо довгий, щоб бути описом)
    should_try_food = is_food or (len(original_text.split()) >= 2 and not is_activity_word)
    
    if should_try_food:
        profile = get_user_profile(user.id)
        processing_msg = await update.message.reply_text("🔍 Аналізую...")
        
        try:
            result = await analyze_food_text(original_text, profile)
            
            # Claude сказав що це не їжа
            if result.get("not_food"):
                await processing_msg.delete()
                # Падаємо далі — покажемо стандартну підказку
            elif "error" in result:
                await processing_msg.edit_text(f"❌ {result['error']}")
                return
            else:
                # Зберігаємо їжу
                meal_id = save_meal(user.id, result)
                
                response = f"""
✅ *Записано!*

🍽 *{result.get('food_name', 'Страва')}*

🔥 Калорії: *{result.get('calories', 0)}* ккал
🥩 Б: {result.get('protein', 0)}г 🧈 Ж: {result.get('fat', 0)}г 🍞 В: {result.get('carbs', 0)}г
🍬 Цукор: {result.get('sugar', 0)}г 🥬 Клітковина: {result.get('fiber', 0)}г
"""
                
                if result.get('personalized_tip'):
                    response += f"\n💡 _{result.get('personalized_tip')}_"
                
                # Прогрес
                if profile and profile.get("profile_complete"):
                    stats = get_stats(user.id, days=1)
                    cal_goal = profile["daily_calories"]
                    cal_left = cal_goal - stats["calories"]
                    
                    if cal_left > 0:
                        response += f"\n\n📊 {stats['calories']}/{cal_goal} ккал • Залишилось: *{cal_left}*"
                    else:
                        response += f"\n\n📊 {stats['calories']}/{cal_goal} ккал • ⚠️ +{abs(cal_left)}"
                
                # Додаємо підказку що можна уточнити
                response += "\n\n_💬 Якщо цифри неточні — напиши деталі (вага, склад) і я перерахую_"
                
                keyboard = [[InlineKeyboardButton("🗑 Видалити", callback_data=f"delete_meal_{meal_id}")]]
                
                await processing_msg.edit_text(
                    response,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return
        except Exception as e:
            try:
                await processing_msg.edit_text(f"❌ Помилка: {str(e)}")
            except:
                pass
            return
    
    # Якщо нічого не спрацювало — стандартна підказка
    await update.message.reply_text(
        "🤔 Не зрозумів. Ти можеш:\n\n"
        "📷 Надіслати *фото їжі* — я проаналізую\n\n"
        "✍️ Або написати текстом що з'їв:\n"
        "• «з'їв 2 яйця і тост з авокадо»\n"
        "• «обід: борщ 300мл і котлета»\n"
        "• «випила каву з молоком і круасан»\n\n"
        "🏃 Або про активність:\n"
        "• «пробіжка 30 хв»\n"
        "• «10000 кроків»",
        parse_mode="Markdown"
    )

# ============== ЗАПУСК БОТА ==============
def main():
    init_database()
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Профіль
    profile_handler = ConversationHandler(
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
    
    app.add_handler(profile_handler)
    
    # Команди
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("week", week_command))
    app.add_handler(CommandHandler("month", month_command))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("notifications", notifications_command))
    
    # Callback (кнопки)
    app.add_handler(CallbackQueryHandler(notification_toggle_callback, pattern="^notif_"))
    app.add_handler(CallbackQueryHandler(button_callback, pattern="^delete_"))
    
    # Повідомлення
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    # Вечірній звіт о 21:00
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_daily(
            send_evening_report,
            time=time(hour=21, minute=0, second=0),
            name="evening_report"
        )
    
    print("🤖 Бот запущено!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
