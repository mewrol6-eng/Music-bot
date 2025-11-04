# bot.py
import os
import sqlite3
import asyncio
import shlex
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional

from aiogram import Bot, Dispatcher, types, executor
from aiogram.types import InputFile
from mutagen.id3 import ID3, APIC, TIT2, error
from mutagen.mp3 import MP3
from PIL import Image
from dotenv import load_dotenv

load_dotenv()

import config

BOT_TOKEN = os.getenv("BOT_TOKEN") or config.BOT_TOKEN
ADMINS = config.ADMINS
CHANNELS = config.CHANNELS
TMP_DIR = Path(config.TMP_DIR)
TMP_DIR.mkdir(parents=True, exist_ok=True)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

DB_PATH = TMP_DIR / "bot_db.sqlite3"

# ---- simple sqlite helper ----
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        last_file TEXT,
        updated_at TEXT
    )
    """)
    conn.commit()
    conn.close()

def upsert_user(user: types.User, last_file: Optional[str]=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, last_file, updated_at) VALUES (?,?,?,?,?,?)",
                (user.id, user.username, user.first_name, user.last_name, last_file or get_last_file(user.id), datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()

def get_last_file(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT last_file FROM users WHERE user_id=?", (user_id,))
    r = cur.fetchone()
    conn.close()
    return r[0] if r else None

def set_last_file(user_id, path):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_file=?, updated_at=? WHERE user_id=?", (path, datetime.utcnow().isoformat(), user_id))
    conn.commit()
    conn.close()

def all_users():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows]

# ---- util ----
def ensure_subscribed(user_id: int):
    """Проверка подписки на все каналы CHANNELS. Возвращает (ok, text_if_not)"""
    if not CHANNELS:
        return True, None
    for ch in CHANNELS:
        try:
            member = asyncio.get_event_loop().run_until_complete(bot.get_chat_member(ch, user_id))
            status = member.status
            if status in ("left", "kicked"):
                # not subscribed
                return False, ch
        except Exception as e:
            # если канал приватный или бот не админ — допускаем, но предупреждаем
            print(f"Error checking channel {ch}: {e}")
            return False, ch
    return True, None

def parse_time_to_seconds(t: str):
    parts = t.split(":")
    parts = list(map(int, parts[::-1]))
    secs = 0
    mul = 1
    for p in parts:
        secs += p * mul
        mul *= 60
    return secs

async def download_file(file_id: str, dest_path: Path):
    file = await bot.get_file(file_id)
    await file.download(destination_file=str(dest_path))
    return dest_path

def run_ffmpeg_cut(input_path: Path, output_path: Path, start_s: int, duration_s: int):
    # перекодируем mp3 в mp3 часть (чтобы точно работало)
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_s),
        "-t", str(duration_s),
        "-i", str(input_path),
        "-vn",
        "-acodec", "libmp3lame",
        "-b:a", "192k",
        str(output_path)
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg error: {proc.stderr.decode('utf-8')}")
    return output_path

def set_mp3_title(path: Path, new_title: str):
    try:
        audio = ID3(str(path))
    except error:
        audio = ID3()
    audio.delall("TIT2")
    audio.add(TIT2(encoding=3, text=new_title))
    audio.save(str(path))

def set_mp3_cover(path: Path, img_path: Path):
    try:
        audio = ID3(str(path))
    except error:
        audio = ID3()
    with open(img_path, "rb") as f:
        img = f.read()
    audio.delall("APIC")
    audio.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=img))
    audio.save(str(path))

# ---- handlers ----
@dp.message_handler(commands=["start", "help"])
async def cmd_start(message: types.Message):
    upsert_user(message.from_user)
    ok, ch = ensure_subscribed(message.from_user.id)
    if not ok:
        kb = types.InlineKeyboardMarkup()
        for c in CHANNELS:
            kb.add(types.InlineKeyboardButton(text=f"Подписаться на {c}", url=f"https://t.me/{c.replace('@','')}"))
        await message.answer("Чтобы пользоваться ботом, подпишись на каналы ниже:", reply_markup=kb)
        return
    await message.answer(
        "Привет! Пришли мне mp3 (как файл или как аудио). Затем используй команды:\n"
        "/rename НовыйНазвание — переименовать последний загруженный трек\n"
        "/cut start end — обрезать (формат mm:ss или hh:mm:ss), например /cut 0:30 1:20\n"
        "/setcover — отправь фото в ответ на файл (reply) чтобы установить обложку\n"
        "/myfile — получить последний загруженный файл\n        "
    )

@dp.message_handler(content_types=[types.ContentType.AUDIO, types.ContentType.DOCUMENT])
async def handle_audio(message: types.Message):
    # проверка подписки
    ok, ch = ensure_subscribed(message.from_user.id)
    if not ok:
        kb = types.InlineKeyboardMarkup()
        for c in CHANNELS:
            kb.add(types.InlineKeyboardButton(text=f"Подписаться на {c}", url=f"https://t.me/{c.replace('@','')}"))
        await message.answer("Чтобы пользоваться ботом, подпишись на каналы ниже:", reply_markup=kb)
        return

    file_obj = None
    fname = None
    if message.audio:
        file_obj = message.audio
        fname = file_obj.file_name or f"audio_{message.message_id}.mp3"
    elif message.document:
        # принимаем mp3 как document
        file_obj = message.document
        fname = file_obj.file_name or f"audio_{message.message_id}.mp3"
    else:
        await message.answer("Отправь mp3 как файл или аудио.")
        return

    ext = Path(fname).suffix
    if ext.lower() not in (".mp3", ".wav", ".m4a", ".ogg"):
        # допускаем разные форматы, но рекомендуем mp3
        pass

    dest = TMP_DIR / f"{message.from_user.id}_{int(datetime.utcnow().timestamp())}{ext}"
    await download_file(file_obj.file_id, dest)
    # если не mp3 — перекодируем в mp3 для единообразия
    if ext.lower() != ".mp3":
        mp3_out = dest.with_suffix(".mp3")
        cmd = ["ffmpeg","-y","-i", str(dest), "-vn","-acodec","libmp3lame","-b:a","192k", str(mp3_out)]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        dest.unlink(missing_ok=True)
        dest = mp3_out

    # сохранить в БД как последний файл
    set_last_file(message.from_user.id, str(dest))
    upsert_user(message.from_user, last_file=str(dest))
    await message.answer(f"Файл получен и сохранён. Используй /rename, /cut, /setcover в ответ на этот файл или просто указывая команды.")

@dp.message_handler(commands=["myfile"])
async def cmd_myfile(message: types.Message):
    last = get_last_file(message.from_user.id)
    if not last:
        await message.answer("У тебя нет сохранённых файлов.")
        return
    try:
        await message.answer_document(InputFile(last))
    except Exception as e:
        await message.answer(f"Ошибка отправки файла: {e}")

@dp.message_handler(commands=["rename"])
async def cmd_rename(message: types.Message):
    # формат: /rename Новый Заголовок
    args = message.get_args().strip()
    if not args:
        await message.answer("Использование: /rename НовыйНазвание (например: /rename My Song)")
        return
    last = get_last_file(message.from_user.id)
    if not last:
        await message.answer("У тебя нет сохранённых файлов.")
        return
    try:
        set_mp3_title(Path(last), args)
        await message.answer("Название установлено. Отправляю файл...")
        await message.answer_document(InputFile(last))
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

@dp.message_handler(commands=["cut"])
async def cmd_cut(message: types.Message):
    # формат: /cut 0:30 1:20  или /cut 00:01:00 00:02:00
    args = message.get_args().split()
    if len(args) != 2:
        await message.answer("Использование: /cut start end (формат mm:ss или hh:mm:ss). Например: /cut 0:30 1:20")
        return
    last = get_last_file(message.from_user.id)
    if not last:
        await message.answer("У тебя нет сохранённых файлов.")
        return
    try:
        start_s = parse_time_to_seconds(args[0])
        end_s = parse_time_to_seconds(args[1])
        if end_s <= start_s:
            await message.answer("Неправильный диапазон (end <= start).")
            return
        duration = end_s - start_s
        out = Path(last).with_name(Path(last).stem + "_cut.mp3")
        await message.answer("Идёт обрезка, подожди...")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, run_ffmpeg_cut, Path(last), out, start_s, duration)
        set_last_file(message.from_user.id, str(out))
        upsert_user(message.from_user)
        await message.answer_document(InputFile(out))
    except Exception as e:
        await message.answer(f"Ошибка при обрезке: {e}")

@dp.message_handler(commands=["setcover"])
async def cmd_setcover(message: types.Message):
    # Пользователь должен ответить фото в reply на сообщение с файлом (или прислать фото и в тексте написать /setcover)
    if not message.reply_to_message:
        await message.answer("Отправь фото в ответ на сообщение с файлом и в ответе на фото напиши /setcover")
        return
    # нашли фото в reply_to_message
    replied = message.reply_to_message
    photo = None
    # если пользователь присылает фото сейчас и делает reply к сообщению где есть файл
    if replied.photo:
        photo = replied.photo[-1]
    elif message.photo:
        photo = message.photo[-1]
    else:
        await message.answer("В ответном сообщении нет фото.")
        return

    last = get_last_file(message.from_user.id)
    if not last:
        await message.answer("У тебя нет сохранённых файлов.")
        return

    img_path = TMP_DIR / f"cover_{message.from_user.id}_{int(datetime.utcnow().timestamp())}.jpg"
    await download_file(photo.file_id, img_path)
    # привести картинку к jpeg и разумному размеру
    try:
        im = Image.open(img_path)
        im = im.convert("RGB")
        im.thumbnail((1400,1400))
        im.save(img_path, format="JPEG")
    except Exception as e:
        print("Image processing error:", e)

    try:
        set_mp3_cover(Path(last), img_path)
        await message.answer("Обложка установлена. Отправляю файл...")
        await message.answer_document(InputFile(last))
    except Exception as e:
        await message.answer(f"Ошибка установки обложки: {e}")

# Admin broadcast
@dp.message_handler(commands=["broadcast"])
async def cmd_broadcast(message: types.Message):
    if message.from_user.id not in ADMINS:
        await message.answer("Только админы могут использовать эту команду.")
        return
    text = message.get_args().strip()
    if not text and not message.reply_to_message:
        await message.answer("Использование: /broadcast ТЕКСТ  или ответьте на фото/сообщение и напишите /broadcast")
        return

    users = all_users()
    sent = 0
    failed = 0
    await message.answer(f"Начинаю рассылку по {len(users)} пользователям...")
    for uid in users:
        try:
            # если команда была ответом на фото/сообщение, то пересылаем его
            if message.reply_to_message:
                # если reply содержит фото
                if message.reply_to_message.photo:
                    await bot.send_photo(uid, message.reply_to_message.photo[-1].file_id, caption= text or "")
                elif message.reply_to_message.document:
                    await bot.send_document(uid, message.reply_to_message.document.file_id, caption=text or "")
                else:
                    await bot.send_message(uid, text or message.reply_to_message.text or "")
            else:
                await bot.send_message(uid, text)
            sent += 1
            await asyncio.sleep(0.05)  # маленькая пауза чтобы не превышать лимиты
        except Exception as e:
            failed += 1
            print(f"Failed send to {uid}: {e}")
    await message.answer(f"Рассылка завершена. Отправлено: {sent}, ошибок: {failed}")

# misc
@dp.message_handler(commands=["users"])
async def cmd_users(message: types.Message):
    if message.from_user.id not in ADMINS:
        await message.answer("Только админ.")
        return
    ucount = len(all_users())
    await message.answer(f"Пользователей в БД: {ucount}")

if __name__ == "__main__":
    init_db()
    print("Bot starting...")
    executor.start_polling(dp, skip_updates=True)
