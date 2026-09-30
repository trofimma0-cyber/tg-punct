"""Модуль перехвата и сохранения сгорающих / одноразовых медиа (View-Once Saver).
Мгновенно кэширует входящие фото, видео, кружки и войсы на диск при получении,
чтобы спасти их, когда они сгорят по таймеру или будут удалены собеседником.
"""
import os
import time
import logging
from datetime import datetime, timezone
import storage

logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cached_media")
os.makedirs(CACHE_DIR, exist_ok=True)


def _get_cache_path(conn_id: str, chat_id: int, message_id: int, ext: str = "bin") -> str:
    safe_conn = (conn_id or "default").replace(":", "_").replace("/", "_")
    filename = f"{safe_conn}_{chat_id}_{message_id}.{ext}"
    return os.path.join(CACHE_DIR, filename)


async def pre_cache_incoming_media(bot, conn_id: str, chat_id: int, message_id: int, file_id: str, media_type: str):
    """Фоновое скачивание входящего медиафайла до того, как сработает таймер сгорания."""
    if not file_id or not media_type:
        return

    ext_map = {
        "photo": "jpg",
        "video": "mp4",
        "video_note": "mp4",
        "voice": "ogg",
        "audio": "mp3",
        "document": "bin",
    }
    ext = ext_map.get(media_type, "bin")
    file_path = _get_cache_path(conn_id, chat_id, message_id, ext)

    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        return

    try:
        tg_file = await bot.get_file(file_id)
        buf = await tg_file.download_as_bytearray()
        with open(file_path, "wb") as f:
            f.write(buf)
        logger.info("Pre-cached %s (%d bytes) for %s:%s", media_type, len(buf), chat_id, message_id)
        storage.save_cached_media_path(conn_id, chat_id, message_id, file_path)
    except Exception as e:
        logger.warning("Could not pre-cache %s file %s: %s", media_type, file_id, e)


def get_cached_media_bytes(conn_id: str, chat_id: int, message_id: int) -> tuple[bytes | None, str | None]:
    """Возвращает байты и расширение кэшированного файла, если он есть на диске."""
    path = storage.get_cached_media_path(conn_id, chat_id, message_id)
    if path and os.path.exists(path):
        try:
            with open(path, "rb") as f:
                data = f.read()
            ext = path.rsplit(".", 1)[-1].lower() if "." in path else "bin"
            return data, ext
        except Exception as e:
            logger.warning("Error reading cached media %s: %s", path, e)

    # Пробуем найти по маске
    safe_conn = (conn_id or "default").replace(":", "_").replace("/", "_")
    prefix = f"{safe_conn}_{chat_id}_{message_id}."
    for fname in os.listdir(CACHE_DIR):
        if fname.startswith(prefix):
            full_p = os.path.join(CACHE_DIR, fname)
            try:
                with open(full_p, "rb") as f:
                    data = f.read()
                ext = fname.rsplit(".", 1)[-1].lower()
                return data, ext
            except Exception:
                pass

    return None, None


def is_likely_view_once(lifetime_seconds: int) -> bool:
    """Определяет, похоже ли удаление на сгорающее/одноразовое медиа (обычно удаляется до 5-10 минут)."""
    return lifetime_seconds <= 600


def cleanup_cache(max_age_seconds: int = 86400 * 3, max_total_bytes: int = 300 * 1024 * 1024):
    """Очищает устаревшие кэшированные медиафайлы для экономии места на диске."""
    now = time.time()
    try:
        files = []
        total_size = 0
        for fname in os.listdir(CACHE_DIR):
            fpath = os.path.join(CACHE_DIR, fname)
            if os.path.isfile(fpath):
                try:
                    stat = os.stat(fpath)
                    if now - stat.st_mtime > max_age_seconds:
                        os.remove(fpath)
                    else:
                        files.append((stat.st_mtime, stat.st_size, fpath))
                        total_size += stat.st_size
                except Exception:
                    pass

        if total_size > max_total_bytes:
            files.sort()
            for _, size, fpath in files:
                try:
                    os.remove(fpath)
                    total_size -= size
                    if total_size <= max_total_bytes * 0.7:
                        break
                except Exception:
                    pass
    except Exception as e:
        logger.warning("Ошибка при очистке кэша медиа: %s", e)
