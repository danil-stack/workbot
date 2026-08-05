import os
import asyncio
import logging
import sqlite3
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# === НАСТРОЙКИ ===
API_TOKEN = '8998313772:AAHVr2HGJg73_c1ji__al0xAFQ_OJup2iLw'
ADMIN_ID = 7623928167  # СЮДА_ВСТАВЬТЕ_ВАШ_TELEGRAM_ID (числом)

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# === РАБОТА С БАЗОЙ ДАННЫХ SQLite ===
def init_db():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    # Таблица пользователей для рассылки
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY
        )
    ''')
    # Таблица карт
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_number TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def add_user(user_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO users (id) VALUES (?)', (user_id,))
    conn.commit()
    conn.close()

def get_users():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM users')
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users

def add_card(card_number):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('INSERT INTO cards (card_number) VALUES (?)', (card_number,))
    conn.commit()
    conn.close()

def get_cards():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, card_number FROM cards')
    cards = cursor.fetchall()
    conn.close()
    return cards

def delete_card(card_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM cards WHERE id = ?', (card_id,))
    conn.commit()
    conn.close()


# === СОСТОЯНИЯ (FSM) ===
class AdminStates(StatesGroup):
    adding_card = State()

class UserStates(StatesGroup):
    sending_receipt = State()


# === КЛАВИАТУРЫ ===
def get_admin_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="Карты 💳")
    builder.button(text="Старт 🟢")
    builder.button(text="Стоп 🔴")
    builder.button(text="Тех.работы 🛠")
    builder.adjust(2, 2)
    return builder.as_markup(resize_keyboard=True)

def get_user_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="Валидные карты 💳")
    builder.button(text="Отправить чек 🧾")
    builder.adjust(1, 1)
    return builder.as_markup(resize_keyboard=True)

def get_cards_inline_keyboard(cards):
    builder = InlineKeyboardBuilder()
    for card_id, card_num in cards:
        builder.button(text=f"❌ Удалить: {card_num}", callback_data=f"del_{card_id}")
    builder.button(text="➕ Добавить карту", callback_data="add_card")
    builder.adjust(1)
    return builder.as_markup()


# === ФУНКЦИЯ РАССЫЛКИ ===
async def broadcast_message(text: str):
    users = get_users()
    sent_count = 0
    for user_id in users:
        try:
            # Не отправляем рассылку самому админу, чтобы не спамить
            if user_id != ADMIN_ID:
                await bot.send_message(chat_id=user_id, text=text)
                sent_count += 1
                await asyncio.sleep(0.05)  # Лимиты Telegram
        except Exception as e:
            logging.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
    return sent_count


# === ОБРАБОТЧИКИ (КОМАНДЫ И ОБЩЕЕ) ===
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    add_user(user_id)
    
    if user_id == ADMIN_ID:
        await message.answer("Добро пожаловать в админ-панель!", reply_markup=get_admin_keyboard())
    else:
        await message.answer("Добро пожаловать в бота!", reply_markup=get_user_keyboard())


# === АДМИН-ПАНЕЛЬ ===
@dp.message(F.text == "Карты 💳")
async def admin_cards(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    cards = get_cards()
    if not cards:
        text = "Список карт пуст."
    else:
        text = "Список карт (нажмите кнопку под сообщением, чтобы удалить):"
    await message.answer(text, reply_markup=get_cards_inline_keyboard(cards))

@dp.callback_query(F.data == "add_card")
async def process_add_card(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminStates.adding_card)
    await callback.message.answer("Отправьте номер или данные новой карты:")
    await callback.answer()

@dp.message(AdminStates.adding_card)
async def save_card(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    card_text = message.text.strip()
    if card_text:
        add_card(card_text)
        await state.clear()
        await message.answer(f"Карта успешно добавлена: {card_text}", reply_markup=get_admin_keyboard())
    else:
        await message.answer("Пожалуйста, введите корректный текст.")

@dp.callback_query(F.data.startswith("del_"))
async def process_delete_card(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    card_id = int(callback.data.split("_")[1])
    delete_card(card_id)
    await callback.answer("Карта удалена")
    
    # Обновляем сообщение со списком
    cards = get_cards()
    if not cards:
        text = "Список карт пуст."
    else:
        text = "Список карт (нажмите кнопку под сообщением, чтобы удалить):"
    await callback.message.edit_text(text, reply_markup=get_cards_inline_keyboard(cards))

# Кнопки рассылки для админа
@dp.message(F.text == "Старт 🟢")
async def admin_start_work(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("Запуск рассылки СТАРТ ВОРК...")
    count = await broadcast_message("СТАРТ ВОРК")
    await message.answer(f"Рассылка завершена. Отправлено пользователям: {count}")

@dp.message(F.text == "Стоп 🔴")
async def admin_stop_work(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("Запуск рассылки СТОП ВОРК...")
    count = await broadcast_message("СТОП ВОРК")
    await message.answer(f"Рассылка завершена. Отправлено пользователям: {count}")

@dp.message(F.text == "Тех.работы 🛠")
async def admin_maintenance(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("Запуск рассылки о тех. работах...")
    count = await broadcast_message("Сейчас ведутся тех. работы.")
    await message.answer(f"Рассылка завершена. Отправлено пользователям: {count}")


# === ПОЛЬЗОВАТЕЛЬСКАЯ ЧАСТЬ ===
@dp.message(F.text == "Валидные карты 💳")
async def user_cards(message: types.Message):
    cards = get_cards()
    if not cards:
        await message.answer("в поиске карт , ожидайте")
    else:
        cards_list = "\n".join([f"• <code>{card[1]}</code>" for card in cards])
        await message.answer(f"Доступные карты:\n\n{cards_list}")

@dp.message(F.text == "Отправить чек 🧾")
async def user_send_receipt_prompt(message: types.Message, state: FSMContext):
    await state.set_state(UserStates.sending_receipt)
    await message.answer("Пожалуйста, просто отправьте скриншот в чат.")

@dp.message(UserStates.sending_receipt)
async def handle_receipt(message: types.Message, state: FSMContext):
    # Проверяем, прислал ли пользователь картинку или документ
    if not (message.photo or message.document):
        await message.answer("Пожалуйста, отправьте именно скриншот (фотографию или файл).")
        return

    user = message.from_user
    
    # Создаем кликабельное имя пользователя или ссылку по ID
    user_mention = f"@{user.username}" if user.username else f'<a href="tg://user?id={user.id}">{user.full_name}</a>'
    caption_text = f"<b>Получен новый чек!</b>\nОтправитель: {user_mention}\nID пользователя: <code>{user.id}</code>"

    try:
        if message.photo:
            # Отправляем фото админу (самый крупный размер из массива photo)
            await bot.send_photo(chat_id=ADMIN_ID, photo=message.photo[-1].file_id, caption=caption_text)
        elif message.document:
            # Если отправлено файлом
            await bot.send_document(chat_id=ADMIN_ID, document=message.document.file_id, caption=caption_text)
        
        await state.clear()
        await message.answer("чек принят , ожидайте оплату")
    except Exception as e:
        logging.error(f"Ошибка при пересылке чека админу: {e}")
        await message.answer("Произошла ошибка при отправке чека. Пожалуйста, попробуйте еще раз.")


# === ВЕБ-СЕРВЕР И ЗАПУСК БОТА ===
async def handle_ping(request):
    return web.Response(text="Bot is alive!")

async def main():
    init_db()
    
    # Поднимаем лёгкий веб-сервер для Render
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Render передает порт через переменную окружения PORT
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    # Запуск бота
    await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен")
