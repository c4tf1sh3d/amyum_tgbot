import telebot;
bot = telebot.TeleBot('%8364305489:AAGaNNx1lc77a43Z41QGCXXfLxOi1OeVQy4');

@bot.message_handler(content_types=['text'])
def get_text_messages(message):
