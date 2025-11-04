import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMINS = list(map(int, os.getenv("ADMINS","").split(","))) if os.getenv("ADMINS") else []
CHANNELS = os.getenv("CHANNELS", "").split(",") if os.getenv("CHANNELS") else []
TMP_DIR = os.getenv("TMP_DIR", "/tmp/music_bot")
