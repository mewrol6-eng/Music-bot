import os
import shutil
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from mutagen.mp3 import MP3
from PIL import Image
from config import BOT_TOKEN, ADMINS, CHANNELS, TMP_DIR

# --- Инициализация ---
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

if not os.path.exists(TMP_DIR):
    os.makedirs(TMP_DIR)

# --- Состояния для FSM ---
class AudioStates(StatesGroup):
    rename = State()
    cut = State()
    setcover = State()

# --- Кнопки ---
def sub_buttons():
    kb = InlineKeyboardMarkup(row_width=2)
    for ch in CHANNELS:
        kb.add(
            InlineKeyboardButton(
                text=f"Подписаться на {ch}",
                url=f"https://t.me/{ch.replace('@','')}"
            )
        )
    return kb

def audio_buttons():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("Переименовать", callback_data="rename"),
        InlineKeyboardButton("Обрезать", callback_data="cut"),
        InlineKeyboardButton("Сменить обложку", callback_data="setcover")
    )
    return kb

# --- Проверка подписки (пример, можно подключить реальный API) ---
def is_subscribed(user_id):
    # Пока заглушка: возвращаем True
    return True

# --- /start ---
@dp.message_handler(commands=["start", "help"])
async def cmd_start(message: types.Message):
    if not is_subscribed(message.from_user.id):
        await message.answer(
            "Чтобы пользоваться ботом, подпишись на каналы ниже:",
            reply_markup=sub_buttons()
        )
    else:
        await message.answer(
            "Выбери действие с треком:", 
            reply_markup=audio_buttons()
        )

# --- Обработка кнопок ---
@dp.callback_query_handler(lambda c: c.data)
async def process_callback(callback_query: types.CallbackQuery, state: FSMContext):
    action = callback_query.data
    user_id = callback_query.from_user.id

    if action == "rename":
        await bot.send_message(user_id, "Отправь новое название для трека")
        await AudioStates.rename.set()
    elif action == "cut":
        await bot.send_message(user_id, "Отправь время обрезки в формате 0:30 1:20")
        await AudioStates.cut.set()
    elif action == "setcover":
        await bot.send_message(user_id, "Ответь на трек фото для обложки")
        await AudioStates.setcover.set()
    await callback_query.answer()

# --- Получение аудио ---
@dp.message_handler(content_types=types.ContentType.AUDIO)
async def handle_audio(message: types.Message, state: FSMContext):
    file_id = message.audio.file_id
    file_info = await bot.get_file(file_id)
    file_path = os.path.join(TMP_DIR, f"{message.from_user.id}_{message.audio.file_name}")
    await message.audio.download(destination_file=file_path)
    await state.update_data(last_file=file_path)
    await message.reply("Файл получен! Выбирай действие через кнопки.")

# --- Обработка переименования ---
@dp.message_handler(state=AudioStates.rename)
async def rename_audio(message: types.Message, state: FSMContext):
    data = await state.get_data()
    file_path = data.get("last_file")
    if file_path and os.path.exists(file_path):
        ext = os.path.splitext(file_path)[1]
        new_path = os.path.join(TMP_DIR, f"{message.text}{ext}")
        os.rename(file_path, new_path)
        await state.update_data(last_file=new_path)
        await message.reply(f"Файл переименован в {message.text}")
    await state.finish()

# --- Обработка обрезки ---
@dp.message_handler(state=AudioStates.cut)
async def cut_audio(message: types.Message, state: FSMContext):
    # Пример: 0:30 1:20
    data = await state.get_data()
    file_path = data.get("last_file")
    if not file_path or not os.path.exists(file_path):
        await message.reply("Файл не найден.")
        await state.finish()
        return
    try:
        start, end = message.text.split()
        # Здесь можно интегрировать ffmpeg для реальной обрезки
        await message.reply(f"Обрезка с {start} до {end} (реальная обрезка пока не реализована)")
    except:
        await message.reply("Неверный формат. Пример: 0:30 1:20")
    await state.finish()

# --- Обработка смены обложки ---
@dp.message_handler(content_types=types.ContentType.PHOTO, state=AudioStates.setcover)
async def set_cover(message: types.Message, state: FSMContext):
    data = await state.get_data()
    file_path = data.get("last_file")
    if not file_path or not os.path.exists(file_path):
        await message.reply("Файл не найден.")
        await state.finish()
        return
    photo = message.photo[-1]
    photo_path = os.path.join(TMP_DIR, f"{message.from_user.id}_cover.jpg")
    await photo.download(photo_path)
    await message.reply("Обложка установлена (реальная интеграция с MP3 пока заглушка)")
    await state.finish()

# --- Запуск ---
if __name__ == "__main__":
    from aiogram import executor
    executor.start_polling(dp, skip_updates=True)
