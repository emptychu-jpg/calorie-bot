"""
🍎 Калорій Трекер Бот
Telegram бот для відстеження харчування з аналізом фото їжі через Claude AI

Автор: Створено за допомогою Claude
"""

import os
import json
import asyncio
import anthropic
import sqlite3
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import base64
from io import BytesIO

# ============== НАЛАШТУВАННЯ ==============
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "ТВІЙ_TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "ТВІЙ_ANTHROPIC_API_KEY")

# Ініціалізація Claude клієнта
client = anthropic.Client(api_key=ANTHROPIC_API_KEY)

# ============== БАЗА ДАНИХ ==============
def init_database():
    """Створює базу даних якщо її немає"""
    conn = sqlite3.connect("food_tracker.db")
    cursor = conn.cursor()
    
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
            fiber REAL DEFAULT 0,
            health_notes TEXT,
            photo_description TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            daily_goal INTEGER DEFAULT 2000,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            notifications_enabled BOOLEAN DEFAULT 1
        )
    """)
    
    conn.commit()
    conn.close()

def save_meal(user_id: int, meal_data: dict):
    """Зберігає прийом їжі в базу"""
    conn = sqlite3.connect("food_tracker.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO meals (user_id, food_name, calories, protein, fat, carbs, fiber, health_notes, photo_description)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        meal_data.get("food_name", "Невідома їжа"),
        meal_data.get("calories", 0),
        meal_data.get("protein", 0),
        meal_data.get("fat", 0),
        meal_data.get("carbs", 0),
        meal_data.get("fiber", 0),
        meal_data.get("health_notes", ""),
        meal_data.get("photo_description", "")
    ))
    
    conn.commit()
    conn.close()

def register_user(user_id: int, first_name: str):
    """Реєструє нового користувача"""
    conn = sqlite3.connect("food_tracker.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT OR IGNORE INTO users (user_id, first_name)
        VALUES (?, ?)
    """, (user_id, first_name))
    
    conn.commit()
    conn.close()

def get_stats(user_id: int, days: int = 1) -> dict:
    """Отримує статистику за період"""
    conn = sqlite3.connect("food_tracker.db")
    cursor = conn.cursor()
    
    if days == 1:
        # Сьогодні
        date_filter = "DATE(timestamp) = DATE('now', 'localtime')"
    else:
        # За N днів
        date_filter = f"timestamp >= datetime('now', '-{days} days', 'localtime')"
    
    cursor.execute(f"""
        SELECT 
            COUNT(*) as meal_count,
            COALESCE(SUM(calories), 0) as total_calories,
            COALESCE(SUM(protein), 0) as total_protein,
            COALESCE(SUM(fat), 0) as total_fat,
            COALESCE(SUM(carbs), 0) as total_carbs,
            COALESCE(SUM(fiber), 0) as total_fiber
        FROM meals 
        WHERE user_id = ? AND {date_filter}
    """, (user_id,))
    
    row = cursor.fetchone()
    
    # Отримуємо список їжі
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
        "fiber": round(row[5], 1),
        "meals": meals,
        "days": days
    }

# ============== АНАЛІЗ ФОТО ЧЕРЕЗ CLAUDE ==============
async def analyze_food_photo(photo_bytes: bytes) -> dict:
    """Аналізує фото їжі через Claude Vision"""
    
    base64_image = base64.standard_b64encode(photo_bytes).decode("utf-8")
    
    prompt = """Проаналізуй це фото їжі та дай оцінку українською мовою.

Відповідь ОБОВ'ЯЗКОВО у форматі JSON (без markdown, тільки чистий JSON):
{
    "food_name": "Назва страви або продуктів",
    "calories": число (приблизна кількість калорій),
    "protein": число (грами білка),
    "fat": число (грами жирів),
    "carbs": число (грами вуглеводів),
    "fiber": число (грами клітковини),
    "portion_size": "оцінка розміру порції",
    "health_notes": "короткий коментар про користь/шкоду",
    "photo_description": "що саме зображено на фото"
}

Якщо на фото не їжа, поверни:
{
    "error": "На фото не знайдено їжі"
}

Будь точним у підрахунку, враховуй видимий розмір порції."""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[
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
        )
        
        response_text = message.content[0].text
        
        # Очищаємо від можливих markdown-блоків
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

# ============== КОМАНДИ БОТА ==============
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user = update.effective_user
    register_user(user.id, user.first_name)
    
    welcome_text = f"""
👋 Привіт, {user.first_name}!

Я твій персональний **Калорій Трекер** 🍎

📸 **Як користуватися:**
Просто надсилай мені фото своєї їжі, і я:
• Розпізнаю що це за страва
• Порахую калорії та БЖУ
• Збережу в твою статистику

📊 **Команди:**
/today — статистика за сьогодні
/week — статистика за тиждень  
/month — статистика за місяць
/help — допомога

Надішли перше фото їжі, щоб почати! 📷
"""
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    help_text = """
🍎 **Калорій Трекер — Допомога**

📸 **Як додати прийом їжі:**
Просто сфотографуй свою їжу та надішли мені фото.
Я автоматично розпізнаю страву та порахую калорії.

📊 **Статистика:**
/today — що ти їв сьогодні
/week — статистика за 7 днів
/month — статистика за 30 днів

⚙️ **Поради:**
• Фотографуй їжу зверху для кращого розпізнавання
• Намагайся захопити всю порцію в кадр
• Чим краща якість фото — тим точніший аналіз

❓ Якщо є питання — пиши @твій_username
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /today — статистика за сьогодні"""
    user_id = update.effective_user.id
    stats = get_stats(user_id, days=1)
    
    if stats["meal_count"] == 0:
        await update.message.reply_text(
            "📭 Сьогодні ти ще нічого не їв!\n\nНадішли фото їжі, щоб почати трекінг."
        )
        return
    
    meals_list = "\n".join([
        f"  • {meal[0]} — {meal[1]} ккал" 
        for meal in stats["meals"]
    ])
    
    text = f"""
📊 **Статистика за сьогодні**

🔥 Калорії: **{stats['calories']}** ккал
🥩 Білки: {stats['protein']} г
🧈 Жири: {stats['fat']} г
🍞 Вуглеводи: {stats['carbs']} г
🥬 Клітковина: {stats['fiber']} г

🍽 Прийомів їжі: {stats['meal_count']}

📝 **Що ти їв:**
{meals_list}
"""
    await update.message.reply_text(text, parse_mode="Markdown")

async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /week — статистика за тиждень"""
    user_id = update.effective_user.id
    stats = get_stats(user_id, days=7)
    
    if stats["meal_count"] == 0:
        await update.message.reply_text(
            "📭 За останній тиждень немає даних.\n\nНадішли фото їжі, щоб почати трекінг!"
        )
        return
    
    avg_calories = round(stats["calories"] / 7)
    
    text = f"""
📊 **Статистика за тиждень**

🔥 Всього калорій: **{stats['calories']}** ккал
📈 В середньому на день: **{avg_calories}** ккал

🥩 Білки: {stats['protein']} г
🧈 Жири: {stats['fat']} г  
🍞 Вуглеводи: {stats['carbs']} г
🥬 Клітковина: {stats['fiber']} г

🍽 Прийомів їжі: {stats['meal_count']}
"""
    await update.message.reply_text(text, parse_mode="Markdown")

async def month_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /month — статистика за місяць"""
    user_id = update.effective_user.id
    stats = get_stats(user_id, days=30)
    
    if stats["meal_count"] == 0:
        await update.message.reply_text(
            "📭 За останній місяць немає даних.\n\nНадішли фото їжі, щоб почати трекінг!"
        )
        return
    
    avg_calories = round(stats["calories"] / 30)
    
    text = f"""
📊 **Статистика за місяць**

🔥 Всього калорій: **{stats['calories']}** ккал
📈 В середньому на день: **{avg_calories}** ккал

🥩 Білки: {stats['protein']} г
🧈 Жири: {stats['fat']} г
🍞 Вуглеводи: {stats['carbs']} г
🥬 Клітковина: {stats['fiber']} г

🍽 Прийомів їжі: {stats['meal_count']}
"""
    await update.message.reply_text(text, parse_mode="Markdown")

# ============== ОБРОБКА ФОТО ==============
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє надіслане фото"""
    user = update.effective_user
    register_user(user.id, user.first_name)
    
    # Відправляємо повідомлення про обробку
    processing_msg = await update.message.reply_text("🔍 Аналізую фото...")
    
    try:
        # Отримуємо фото найкращої якості
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        
        # Завантажуємо фото в пам'ять
        photo_bytes = await file.download_as_bytearray()
        
        # Аналізуємо через Claude
        result = await analyze_food_photo(bytes(photo_bytes))
        
        if "error" in result:
            await processing_msg.edit_text(f"❌ {result['error']}")
            return
        
        # Зберігаємо в базу
        save_meal(user.id, result)
        
        # Формуємо відповідь
        response = f"""
✅ **Записано!**

🍽 **{result.get('food_name', 'Страва')}**

🔥 Калорії: **{result.get('calories', 0)}** ккал
🥩 Білки: {result.get('protein', 0)} г
🧈 Жири: {result.get('fat', 0)} г
🍞 Вуглеводи: {result.get('carbs', 0)} г
🥬 Клітковина: {result.get('fiber', 0)} г

📏 Порція: {result.get('portion_size', 'не визначено')}

💡 {result.get('health_notes', '')}

📊 /today — переглянути статистику за сьогодні
"""
        await processing_msg.edit_text(response, parse_mode="Markdown")
        
    except Exception as e:
        await processing_msg.edit_text(f"❌ Помилка обробки: {str(e)}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє текстові повідомлення"""
    await update.message.reply_text(
        "📷 Надішли мені **фото їжі**, і я проаналізую його!\n\n"
        "Або скористайся командами:\n"
        "/today — статистика за сьогодні\n"
        "/help — допомога",
        parse_mode="Markdown"
    )

# ============== ЗАПУСК БОТА ==============
def main():
    """Головна функція запуску бота"""
    # Ініціалізуємо базу даних
    init_database()
    
    # Створюємо застосунок
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Додаємо обробники команд
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("today", today_command))
    app.add_handler(CommandHandler("week", week_command))
    app.add_handler(CommandHandler("month", month_command))
    
    # Українські аліаси команд
    app.add_handler(CommandHandler("сьогодні", today_command))
    app.add_handler(CommandHandler("тиждень", week_command))
    app.add_handler(CommandHandler("місяць", month_command))
    
    # Обробник фото
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    # Обробник тексту
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("🤖 Бот запущено!")
    print("Натисни Ctrl+C для зупинки")
    
    # Запускаємо бота
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
