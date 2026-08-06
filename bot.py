import os
import asyncio
import logging
import sqlite3
import psycopg2  # Подключили драйвер для PostgreSQL
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
ADMIN_ID = 7623928167  # Ваш Telegram ID

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# Считываем переменную БД из окружения. Если ее нет — используем локальный SQLite.
DATABASE_URL = os.getenv("DATABASE_URL")

# === РАБОТА С БАЗАМИ ДАННЫХ (Postgres / SQLite) ===
def get_db_connection():
    if DATABASE_URL:
        # Для облака (Postgres)
        return psycopg2.connect(DATABASE_URL)
    else:
        # Для локального ПК (SQLite)
        return sqlite3.connect('bot_data.db')

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    if DATABASE_URL:
        # Таблицы для Postgres
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id BIGINT PRIMARY KEY,
                crypto_link TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cards (
                id SERIAL PRIMARY KEY,
                card_number TEXT NOT NULL
            )
        ''')
        # Автоматическая миграция (если база на Neon уже существовала без колонки crypto_link)
        try:
            cursor.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS crypto_link TEXT;')
        except Exception:
            conn.rollback()
    else:
        # Таблицы для SQLite
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                crypto_link TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_number TEXT NOT NULL
            )
        ''')
        # Автоматическая миграция для локального SQLite
        try:
            cursor.execute('ALTER TABLE users ADD COLUMN crypto_link TEXT;')
        except Exception:
            pass  # Колонка уже создана
            
    conn.commit()
    conn.close()

def add_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    if DATABASE_URL:
        # Синтаксис ON CONFLICT заменяет sqlite-овский INSERT OR IGNORE
        cursor.execute('INSERT INTO users (id) VALUES (%s) ON CONFLICT (id) DO NOTHING', (user_id,))
    else:
        cursor.execute('INSERT OR IGNORE INTO users (id) VALUES (?)', (user_id,))
    conn.commit()
    conn.close()

def get_user_crypto_link(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    if DATABASE_URL:
        cursor.execute('SELECT crypto_link FROM users WHERE id = %s', (user_id,))
    else:
        cursor.execute('SELECT crypto_link FROM users WHERE id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def update_user_crypto_link(user_id, crypto_link):
    conn = get_db_connection()
    cursor = conn.cursor()
    if DATABASE_URL:
        cursor.execute('UPDATE users SET crypto_link = %s WHERE id = %s', (crypto_link, user_id))
    else:
        cursor.execute('UPDATE users SET crypto_link = ? WHERE id = ?', (crypto_link, user_id))
    conn.commit()
    conn.close()

def get_users():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM users')
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users

def add_card(card_number):
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    cursor.execute(f'INSERT INTO cards (card_number) VALUES ({placeholder})', (card_number,))
    conn.commit()
    conn.close()

def get_cards():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, card_number FROM cards')
    cards = cursor.fetchall()
    conn.close()
    return cards

def delete_card(card_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    cursor.execute(f'DELETE FROM cards WHERE id = {placeholder}', (card_id,))
    conn.commit()
    conn.close()


# === СОСТОЯНИЯ (FSM) ===
class AdminStates(StatesGroup):
    adding_card = State()

class UserStates(StatesGroup):
    waiting_for_crypto_link = State()
    updating_crypto_link = State()
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

# Кнопки под чеком для администратора
def get_receipt_action_keyboard(user_id):
    builder = InlineKeyboardBuilder()
    builder.button(text="У меня 📥", callback_data=f"rcpt_received_{user_id}")
    builder.button(text="Деньги не пришли ❌", callback_data=f"rcpt_missing_{user_id}")
    builder.adjust(2)
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
        # Проверяем, есть ли у пользователя ссылка на Crypto Bot
        crypto_link = get_user_crypto_link(user_id)
        if not crypto_link:
            await state.set_state(UserStates.waiting_for_crypto_link)
            await message.answer(
                "👋 <b>Добро пожаловать!</b>\n\n"
                "Перед началом работы, пожалуйста, отправьте вашу ссылку на счет (чек или адрес) в <b>Crypto Bot (@send)</b>.\n"
                "Это необходимо для того, чтобы администратор мог производить вам мгновенные выплаты."
            )
        else:
            await message.answer(
                f"Добро пожаловать обратно!\n"
                f"Ваш счет для выплат: <code>{crypto_link}</code>\n\n"
                f"Если вам нужно изменить его, используйте команду /wallet",
                reply_markup=get_user_keyboard()
            )

# Изменение реквизитов по команде /wallet
@dp.message(Command("wallet"))
async def cmd_wallet(message: types.Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID:
        return
    await state.set_state(UserStates.updating_crypto_link)
    await message.answer("Пожалуйста, отправьте новую ссылку на ваш счет в Crypto Bot (@send):")


@dp.message(UserStates.waiting_for_crypto_link)
async def process_first_crypto_link(message: types.Message, state: FSMContext):
    link = message.text.strip()
    if not link:
        await message.answer("Пожалуйста, отправьте текстовую ссылку.")
        return
    
    update_user_crypto_link(message.from_user.id, link)
    await state.clear()
    await message.answer(
        "✅ <b>Реквизиты успешно сохранены!</b>\n"
        "Теперь вы можете пользоваться возможностями бота.",
        reply_markup=get_user_keyboard()
    )

@dp.message(UserStates.updating_crypto_link)
async def process_update_crypto_link(message: types.Message, state: FSMContext):
    link = message.text.strip()
    if not link:
        await message.answer("Пожалуйста, отправьте текстовую ссылку.")
        return
    
    update_user_crypto_link(message.from_user.id, link)
    await state.clear()
    await message.answer(
        f"✅ <b>Реквизиты успешно обновлены!</b>\nНовый счет: <code>{link}</code>",
        reply_markup=get_user_keyboard()
    )


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


# === ПОЛУЧЕНИЕ И ОБРАБОТКА CALLBACK ЗАПРОСОВ АДМИНА ПО ЧЕКАМ ===
@dp.callback_query(F.data.startswith("rcpt_received_"))
async def process_rcpt_received(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    user_id = int(callback.data.split("_")[2])
    try:
        # Отправляем оповещение пользователю
        await bot.send_message(chat_id=user_id, text="деньги у меня и ожидайте выплату")
        await callback.answer("Оповещение отправлено пользователю!")
    except Exception as e:
        await callback.answer(f"Не удалось отправить сообщение: {e}", show_alert=True)
        return

    # Обновляем сообщение у админа, удаляя кнопки и фиксируя статус
    old_caption = callback.message.caption or ""
    new_caption = old_caption + "\n\n🟢 <b>Статус:</b> Деньги у меня (оповещено)"
    try:
        await callback.message.edit_caption(caption=new_caption, reply_markup=None)
    except Exception as e:
        logging.error(f"Не удалось изменить подпись: {e}")

@dp.callback_query(F.data.startswith("rcpt_missing_"))
async def process_rcpt_missing(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    user_id = int(callback.data.split("_")[2])
    try:
        # Отправляем оповещение пользователю
        await bot.send_message(chat_id=user_id, text="деньги не пришли и возможно нужно подождать")
        await callback.answer("Оповещение отправлено пользователю!")
    except Exception as e:
        await callback.answer(f"Не удалось отправить сообщение: {e}", show_alert=True)
        return

    # Обновляем сообщение у админа, удаляя кнопки и фиксируя статус
    old_caption = callback.message.caption or ""
    new_caption = old_caption + "\n\n🔴 <b>Статус:</b> Деньги не пришли (оповещено)"
    try:
        await callback.message.edit_caption(caption=new_caption, reply_markup=None)
    except Exception as e:
        logging.error(f"Не удалось изменить подпись: {e}")


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
    # Дополнительная проверка на наличие реквизитов перед отправкой
    crypto_link = get_user_crypto_link(message.from_user.id)
    if not crypto_link:
        await state.set_state(UserStates.waiting_for_crypto_link)
        await message.answer("Сначала отправьте вашу ссылку на Crypto Bot (@send):")
        return

    await state.set_state(UserStates.sending_receipt)
    await message.answer("Пожалуйста, просто отправьте скриншот в чат.")

@dp.message(UserStates.sending_receipt)
async def handle_receipt(message: types.Message, state: FSMContext):
    # Проверяем, прислал ли пользователь картинку или документ
    if not (message.photo or message.document):
        await message.answer("Пожалуйста, отправьте именно скриншот (фотографию или файл).")
        return

    user = message.from_user
    crypto_link = get_user_crypto_link(user.id) or "Не указан"
    
    # Форматируем ссылку на оплату для админа
    link_html = f"<a href='{crypto_link}'>Оплатить в @send</a>" if crypto_link.startswith("http") else f"<code>{crypto_link}</code>"
    
    # Создаем кликабельное имя пользователя или ссылку по ID
    user_mention = f"@{user.username}" if user.username else f'<a href="tg://user?id={user.id}">{user.full_name}</a>'
    
    caption_text = (
        f"<b>Получен новый чек!</b>\n"
        f"Отправитель: {user_mention}\n"
        f"ID пользователя: <code>{user.id}</code>\n\n"
        f"🔗 <b>Реквизиты для оплаты:</b> {link_html}"
    )

    try:
        markup = get_receipt_action_keyboard(user.id)
        if message.photo:
            # Отправляем фото админу вместе с интерактивными кнопками
            await bot.send_photo(chat_id=ADMIN_ID, photo=message.photo[-1].file_id, caption=caption_text, reply_markup=markup)
        elif message.document:
            # Если отправлено файлом
            await bot.send_document(chat_id=ADMIN_ID, document=message.document.file_id, caption=caption_text, reply_markup=markup)
        
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
