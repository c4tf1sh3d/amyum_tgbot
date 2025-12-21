import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import json
import os

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Токен вашего бота (замените на свой)
TOKEN = "%8364305489:AAGaNNx1lc77a43Z41QGCXXfLxOi1OeVQy4"

# База знаний с ответами на вопросы
KNOWLEDGE_BASE = {
    "привет": "Привет! Как дела?",
    "как дела": "У меня всё отлично! А у вас?",
    "что ты умеешь": "Я могу отвечать на простые вопросы. Спросите меня о чём-нибудь!",
    "как тебя зовут": "Я простой бот-помощник",
    "спасибо": "Пожалуйста! Обращайтесь ещё!",
    "пока": "До свидания! Было приятно пообщаться!",
    "время": "Я не могу показать точное время, но вы можете включить эту функцию в моём коде!",
    "погода": "Для информации о погоде лучше использовать специализированные сервисы.",
    "помощь": "Я отвечаю на простые вопросы. Просто напишите что-нибудь, и я постараюсь ответить!",
    "кто тебя создал": "Меня создал разработчик на Python с использованием библиотеки python-telegram-bot",
    "что такое python": "Python - это язык программирования высокого уровня, который популярен в веб-разработке, data science и автоматизации.",
    "как создать бота": "Чтобы создать Telegram-бота, нужно:\n1. Получить токен у @BotFather\n2. Написать код на Python\n3. Запустить бота на сервере",
}

# Функция для поиска ответа в базе знаний
def find_answer(question: str) -> str:
    question_lower = question.lower().strip()
    
    # Поиск точного совпадения
    if question_lower in KNOWLEDGE_BASE:
        return KNOWLEDGE_BASE[question_lower]
    
    # Поиск по ключевым словам
    keywords = {
        "привет": ["привет", "здравствуй", "хай", "hello", "hi"],
        "как дела": ["как дела", "как ты", "как настроение"],
        "что ты умеешь": ["умеешь", "можешь", "функции", "возможности"],
        "погода": ["погода", "погоду", "weather"],
        "время": ["время", "час", "time"],
        "спасибо": ["спасибо", "благодарю", "thanks"],
        "пока": ["пока", "до свидания", "прощай", "bye"],
    }
    
    for key, words in keywords.items():
        for word in words:
            if word in question_lower:
                return KNOWLEDGE_BASE.get(key, "Извините, я не знаю ответа на этот вопрос.")
    
    return "Извините, я не понимаю ваш вопрос. Попробуйте спросить что-то другое!"

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_text = f"""
Привет, {user.first_name}! 👋

Я бот, который отвечает на простые вопросы.

Что я умею:
• Отвечать на приветствия
• Отвечать на вопросы о погоде, времени и т.д.
• Объяснять простые понятия

Попробуйте спросить:
- Как дела?
- Что ты умеешь?
- Что такое Python?
- Как тебя зовут?

Или напишите /help для помощи.
    """
    await update.message.reply_text(welcome_text)

# Команда /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
📚 Доступные команды:
/start - Начать диалог
/help - Получить справку
/faq - Часто задаваемые вопросы

💡 Примеры вопросов:
• Привет!
• Как дела?
• Что ты умеешь?
• Что такое Python?
• Как тебя зовут?
• Пока

Просто напишите ваш вопрос, и я постараюсь на него ответить!
    """
    await update.message.reply_text(help_text)

# Команда /faq
async def faq_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    faq_text = "❓ Часто задаваемые вопросы:\n\n"
    for question, answer in list(KNOWLEDGE_BASE.items())[:10]:  # Показываем первые 10 вопросов
        faq_text += f"• {question.capitalize()}\n"
    faq_text += "\nЗадайте любой из этих вопросов, чтобы получить ответ!"
    await update.message.reply_text(faq_text)

# Обработка текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_type = update.message.chat.type
    text = update.message.text
    
    logger.info(f"User ({update.message.chat.id}) in {message_type}: '{text}'")
    
    if message_type == 'group' or message_type == 'supergroup':
        # В группе бот отвечает только если к нему обращаются по имени
        if context.bot.username and text.lower().find(context.bot.username.lower()) != -1:
            text = text.replace(f'@{context.bot.username}', '').strip()
            response = find_answer(text)
            await update.message.reply_text(response)
        return
    
    # В личных сообщениях отвечает всегда
    response = find_answer(text)
    await update.message.reply_text(response)

# Обработка ошибок
async def error(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")
    if update and update.message:
        await update.message.reply_text("Произошла ошибка. Пожалуйста, попробуйте позже.")

# Основная функция
def main():
    # Создаем приложение
    application = Application.builder().token(TOKEN).build()
    
    # Регистрируем команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("faq", faq_command))
    
    # Регистрируем обработчик текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Регистрируем обработчик ошибок
    application.add_error_handler(error)
    
    # Запускаем бота
    print("Бот запущен...")
    application.run_polling(allowed_updates=Update.ALL_UPDATES)

if __name__ == '__main__':
    main()