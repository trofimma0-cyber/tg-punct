"""Модуль работы с Избранными сообщениями (Saved Messages).
Сохраняет важные сообщения при установке реакции ⭐ или ответе ⭐ в переписке.
"""
import html
import logging
import re
from datetime import datetime, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import storage

logger = logging.getLogger(__name__)


def is_star_reaction(reactions) -> bool:
    """Проверяет, содержит ли список реакций звёздочку (⭐ / ⭐️)."""
    if not reactions:
        return False
    for r in reactions:
        # Стандартные эмодзи
        emoji = getattr(r, "emoji", None)
        if emoji and ("⭐" in emoji or "⭐️" in emoji):
            return True
        # Проверка строкового представления на случай кастомных эмодзи
        r_str = str(r)
        if "⭐" in r_str or "⭐️" in r_str:
            return True
    return False


def make_tag(s: str) -> str:
    """Генерирует безопасный Telegram-хештег из строки."""
    clean = re.sub(r"[^\w]", "_", s.strip()).strip("_")
    return f"#{clean}" if clean else ""


async def save_and_forward_starred(bot, user_id: int, chat_id: int, message_id: int, msg_data: dict = None) -> bool:
    """Сохраняет сообщение в БД и отправляет красивую карточку в личку пользователю."""
    if not user_id:
        return False

    # Проверяем, не сохранено ли уже
    if storage.is_message_starred(user_id, chat_id, message_id):
        logger.info("Message %s:%s already starred by %s", chat_id, message_id, user_id)
        return False

    if not msg_data:
        msg_data = storage.get_message_by_chat_and_id(chat_id, message_id)

    if not msg_data:
        logger.warning("Message %s:%s not found in storage for starring", chat_id, message_id)
        # Если нет в базе, всё равно можем отправить краткое уведомление
        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "⭐ <b>Вы добавили реакцию ⭐ к сообщению!</b>\n\n"
                    f"💬 Чат: <code>{chat_id}</code>\n"
                    f"🆔 Сообщение: <code>{message_id}</code>\n\n"
                    "<i>(Текст сообщения не найден в локальной базе бота — возможно, оно было отправлено до подключения).</i>"
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning("Failed to notify user about star: %s", e)
        return False

    chat_title = msg_data.get("chat_title") or str(chat_id)
    is_owner = msg_data.get("is_owner") == 1
    author = msg_data.get("from_user_name") or ("Вы" if is_owner else "Собеседник")
    text_content = (msg_data.get("text") or "").strip()
    media_type = msg_data.get("media_type")
    file_id = msg_data.get("file_id")
    msg_date = msg_data.get("date")

    # Сохраняем в таблицу starred_messages
    star_id = storage.save_starred_message(
        user_id=user_id,
        chat_id=chat_id,
        chat_title=chat_title,
        message_id=message_id,
        author_name=author,
        text=text_content,
        media_type=media_type,
        file_id=file_id,
        date=msg_date or int(datetime.now(timezone.utc).timestamp()),
    )

    # Форматируем дату и хештеги
    dt_str = ""
    if msg_date:
        dt_str = datetime.fromtimestamp(msg_date, tz=timezone.utc).astimezone().strftime("%d.%m.%Y в %H:%M")
    else:
        dt_str = datetime.now().strftime("%d.%m.%Y в %H:%M")

    chat_tag = make_tag(chat_title)
    author_tag = make_tag(author)
    tags = f"#избранное {chat_tag} {author_tag}".strip()

    esc_author = html.escape(author)
    esc_title = html.escape(chat_title)
    esc_text = html.escape(text_content)
    esc_tags = html.escape(tags)

    header = (
        f"⭐ <b>СОХРАНЕНО В ИЗБРАННОЕ</b>\n\n"
        f"👤 <b>От кого:</b> {esc_author}\n"
        f"💬 <b>Чат:</b> {esc_title}\n"
        f"⏰ <b>Дата:</b> {dt_str}\n"
        f"🏷 <i>{esc_tags}</i>\n"
    )

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗑 Удалить", callback_data=f"star:del:{star_id}"),
            InlineKeyboardButton("⭐ Все сохранённые", callback_data="starred_list"),
        ]
    ])

    try:
        if media_type == "video_note" and file_id:
            await bot.send_message(chat_id=user_id, text=header + "\n⭕ <b>Видеокружок:</b>", parse_mode="HTML", reply_markup=kb)
            await bot.send_video_note(chat_id=user_id, video_note=file_id)

        elif media_type == "voice" and file_id:
            cap = (header + "\n🎤 <b>Голосовое сообщение</b>")[:1024]
            await bot.send_voice(chat_id=user_id, voice=file_id, caption=cap, parse_mode="HTML", reply_markup=kb)

        elif media_type == "photo" and file_id:
            cap = header + (f"\n📝 <i>«{esc_text}»</i>" if esc_text else "")
            await bot.send_photo(chat_id=user_id, photo=file_id, caption=cap[:1024], parse_mode="HTML", reply_markup=kb)

        elif media_type == "video" and file_id:
            cap = header + (f"\n📝 <i>«{esc_text}»</i>" if esc_text else "")
            await bot.send_video(chat_id=user_id, video=file_id, caption=cap[:1024], parse_mode="HTML", reply_markup=kb)

        elif media_type == "document" and file_id:
            cap = header + (f"\n📝 <i>«{esc_text}»</i>" if esc_text else "")
            await bot.send_document(chat_id=user_id, document=file_id, caption=cap[:1024], parse_mode="HTML", reply_markup=kb)

        elif media_type == "audio" and file_id:
            cap = header + (f"\n📝 <i>«{esc_text}»</i>" if esc_text else "")
            await bot.send_audio(chat_id=user_id, audio=file_id, caption=cap[:1024], parse_mode="HTML", reply_markup=kb)

        else:
            full_text = header + (f"\n💬 <b>Сообщение:</b>\n<i>«{esc_text}»</i>" if esc_text else "")
            await bot.send_message(chat_id=user_id, text=full_text, parse_mode="HTML", reply_markup=kb)

        logger.info("Successfully sent starred message #%s to user %s", star_id, user_id)
        return True
    except Exception as e:
        logger.exception("Failed to send starred message to user %s: %s", user_id, e)
        return False
