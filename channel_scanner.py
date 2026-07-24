import json
import asyncio
import logging
from datetime import datetime
from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
from config import API_ID, API_HASH, CHANNEL_ID, CACHE_FILE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ChannelScanner:
    def __init__(self):
        self.client = TelegramClient('scanner_session', API_ID, API_HASH)
        self.art_messages = []
        
    def load_cache(self):
        """Загружает кэш из файла"""
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.art_messages = data.get('messages', [])
                logger.info(f"Загружено {len(self.art_messages)} артов из кэша")
                return True
        except FileNotFoundError:
            logger.info("Кэш не найден, создаём новый")
            return False
        except Exception as e:
            logger.error(f"Ошибка загрузки кэша: {e}")
            return False
    
    def save_cache(self):
        """Сохраняет кэш в файл"""
        try:
            data = {
                'messages': self.art_messages,
                'updated': datetime.now().isoformat(),
                'count': len(self.art_messages)
            }
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"Кэш сохранён: {len(self.art_messages)} артов")
        except Exception as e:
            logger.error(f"Ошибка сохранения кэша: {e}")
    
    async def scan_channel(self):
        """Сканирует канал и находит все сообщения с артами"""
        logger.info(f"Начинаю сканирование канала {CHANNEL_ID}...")
        
        try:
            await self.client.start()
            
            # Получаем информацию о канале
            entity = await self.client.get_entity(CHANNEL_ID)
            logger.info(f"Канал найден: {entity.title}")
            
            # Собираем все сообщения с фото/артами
            art_messages = []
            
            async for message in self.client.iter_messages(entity):
                # Проверяем, есть ли в сообщении фото или документ (картинка)
                if message.media:
                    if isinstance(message.media, (MessageMediaPhoto, MessageMediaDocument)):
                        # Проверяем, что это именно изображение
                        if hasattr(message.media, 'document'):
                            if message.media.document and \
                               message.media.document.mime_type.startswith('image/'):
                                art_messages.append(message.id)
                        elif isinstance(message.media, MessageMediaPhoto):
                            art_messages.append(message.id)
                
                # Ограничение: сканируем последние 1000 сообщений
                if len(art_messages) >= 1000:
                    break
            
            # Обновляем кэш
            if art_messages:
                self.art_messages = art_messages
                self.save_cache()
                logger.info(f"✅ Найдено {len(art_messages)} артов!")
            else:
                logger.warning("⚠️ Арты не найдены!")
                
        except Exception as e:
            logger.error(f"Ошибка сканирования: {e}")
            raise
        finally:
            await self.client.disconnect()
    
    def get_all_arts(self):
        """Возвращает список ID сообщений с артами"""
        if not self.art_messages:
            self.load_cache()
        return self.art_messages
    
    async def start_auto_update(self, interval=3600):
        """Автоматически обновляет кэш каждые interval секунд"""
        logger.info(f"Запуск авто-обновления каждые {interval} секунд")
        
        while True:
            try:
                await self.scan_channel()
                await asyncio.sleep(interval)
            except Exception as e:
                logger.error(f"Ошибка в авто-обновлении: {e}")
                await asyncio.sleep(60)  # ждём минуту перед повтором


# Функция для быстрого сканирования
async def quick_scan():
    """Быстрое сканирование канала (для запуска из командной строки)"""
    scanner = ChannelScanner()
    await scanner.scan_channel()
    print(f"\n✅ Готово! Найдено {len(scanner.art_messages)} артов")
    print(f"Кэш сохранён в {CACHE_FILE}")


if __name__ == "__main__":
    asyncio.run(quick_scan())
