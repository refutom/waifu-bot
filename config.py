import os

# Bot Token от @BotFather
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8618366166:AAEsBB2wqZHh9c7tz22RXLgYjMr6uzUG_HI")

# ID канала (обязательно с @ или -100...)
CHANNEL_ID = os.environ.get("CHANNEL_ID", "-1003864317109")

# API_ID и API_HASH с my.telegram.org (для чтения канала)
API_ID = os.environ.get("API_ID", "123456")
API_HASH = os.environ.get("API_HASH", "твой_api_hash")

# Файл для хранения кэша
CACHE_FILE = "database.json"

# Как часто обновлять кэш (в секундах)
CACHE_UPDATE_INTERVAL = 3600  # раз в час
