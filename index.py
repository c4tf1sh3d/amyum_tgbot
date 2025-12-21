import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Токен вашего бота (получите у @BotFather)
TOKEN = "8364305489:AAGaNNx1lc77a43Z41QGCXXfLxOi1OeVQy4"

# Включаем логирование для отладки
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Простые ответы на частые вопросы
ANSWERS = {
    "привет": "Привет! Как дела?",
    "как дела": "Хорошо, спасибо! А у вас?",
    "что ты умеешь": "Я отвечаю на простые вопросы. Попробуйте спросить 'привет' или 'как дела'!",
    "пока": "До свидания!",
    "спасибо": "Пожалуйста!",
    "помощь": "Напишите /help для списка команд"
}

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Привет! Я простой бот. Напишите мне что-нибудь!")

# Команда /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
📝 Доступные команды:
/start - начать общение
/help - эта справка

💬 Просто напишите:
• Привет
• Как дела
• Что ты умеешь
• Пока
    """
    await update.message.reply_text(help_text)

# Ответы на обычные сообщения
async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.lower().strip()
    
    # Ищем ответ в нашей базе
    for question, answer in ANSWERS.items():
        if question in user_text:
            await update.message.reply_text(answer)
            return
    
    # Если не нашли подходящий ответ
    await update.message.reply_text("Я пока не знаю, как на это ответить 😊")

# Основная функция
def main():
    print("🚀 Бот запускается...")
    
    # Создаём приложение
    app = Application.builder().token(TOKEN).build()
    
    # Добавляем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reply))
    
    # Запускаем бота
    print("✅ Бот запущен! Нажмите Ctrl+C для остановки")
    app.run_polling()

if __name__ == "__main__":
    main()