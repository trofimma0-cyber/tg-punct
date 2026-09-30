"""
Бот подключается через меню Telegram "Автоматизация чатов" (Settings ->
Telegram Business / Автоматизация -> вставляешь @username этого бота).
После подключения бот получает business_message апдейты — то есть видит
ТВОИ сообщения (и сообщения собеседника) в чатах, к которым ты дал доступ,
и может редактировать твои сообщения через business_connection_id.

Запуск:
    1. pip install -r requirements.txt
    2. python download_model.py
    3. cp .env.example .env   # вписать свой BOT_TOKEN от @BotFather
    4. python main.py
    5. В Telegram: Настройки -> Telegram Business -> Автоматизация чатов
       -> вставить @username своего бота -> выбрать доступные чаты
"""
import os
import html
import asyncio
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    TypeHandler,
    MessageReactionHandler,
    ContextTypes,
    filters,
)

from punct import fix_punctuation
import storage
import admin
import layout
import reminders
import websearch
import starred
import view_once

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PROXY_URL = os.getenv("PROXY_URL", "").strip()
EXCLUDE_CHATS = {
    c.strip().lstrip("@").lower()
    for c in os.getenv("EXCLUDE_CHATS", "").split(",")
    if c.strip()
}

if not BOT_TOKEN or BOT_TOKEN.startswith("123456789"):
    raise SystemExit(
        "Не задан BOT_TOKEN. Получи его у @BotFather (/newbot) "
        "и пропиши в файле .env (см. .env.example)."
    )

_processing_ids = set()
_model_lock = asyncio.Lock()


def _chat_excluded(chat) -> bool:
    username = getattr(chat, "username", None)
    return bool(username) and username.lower() in EXCLUDE_CHATS


async def on_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.business_message
    if msg is None:
        return

    owner_id = context.bot_data.get(f"owner:{msg.business_connection_id}")
    if owner_id is None:
        owner_id = storage.get_connection_owner(msg.business_connection_id)
        if owner_id is not None:
            context.bot_data[f"owner:{msg.business_connection_id}"] = owner_id

    from_user = msg.from_user
    if owner_id is None:
        is_owner = None
    else:
        is_owner = bool(from_user and from_user.id == owner_id)

    # --- определяем media ---
    media_type = file_id = None
    if msg.voice:
        media_type, file_id = "voice", msg.voice.file_id
    elif msg.video_note:
        media_type, file_id = "video_note", msg.video_note.file_id
    elif msg.photo:
        media_type, file_id = "photo", msg.photo[-1].file_id
    elif msg.video:
        media_type, file_id = "video", msg.video.file_id
    elif msg.document:
        media_type, file_id = "document", msg.document.file_id
    elif msg.audio:
        media_type, file_id = "audio", msg.audio.file_id

    from_name = None
    if from_user:
        from_name = (from_user.full_name
                     or (from_user.username and "@" + from_user.username)
                     or str(from_user.id))

    # --- имя чата ---
    chat_title = None
    if msg.chat.type in ("group", "supergroup", "channel"):
        chat_title = msg.chat.title or (msg.chat.username and "@" + msg.chat.username)
    if not chat_title:
        if is_owner is not True:
            chat_title = from_name
    if not chat_title:
        chat_title = str(msg.chat_id)

    is_new_chat = storage.is_new_chat_for_connection(msg.business_connection_id, msg.chat_id)

    storage.save_message(
        business_connection_id=msg.business_connection_id,
        chat_id=msg.chat_id,
        chat_title=chat_title,
        message_id=msg.message_id,
        from_user_id=from_user.id if from_user else None,
        from_user_name=from_name,
        is_owner=is_owner,
        text=msg.text or msg.caption,
        media_type=media_type,
        file_id=file_id,
        date=int(msg.date.timestamp()) if msg.date else None,
    )

    if is_new_chat and admin.ADMIN_ID:
        conn_info = storage.get_connection_info(msg.business_connection_id)
        owner_name = conn_info.get("user_name") if conn_info else f"ID {owner_id}"
        owner_handle = f" (@{conn_info.get('user_username')})" if conn_info and conn_info.get("user_username") else ""
        owner_label = f"{owner_name}{owner_handle}".strip()
        now_str = datetime.now(timezone.utc).astimezone().strftime("%d.%m.%Y в %H:%M:%S")
        admin_notice_chat = (
            f"💬 <b>Новый доступный диалог в Telegram Business!</b>\n\n"
            f"👤 <b>Кто подключил:</b> {html.escape(owner_label)}\n"
            f"💬 <b>Чат:</b> <b>{html.escape(chat_title)}</b>\n"
            f"🆔 <b>ID чата:</b> <code>{msg.chat_id}</code>\n"
            f"⏰ <b>Время:</b> {now_str}"
        )
        kb_chat_admin = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Выгрузить переписку этого чата", callback_data=f"adm:chat:{msg.chat_id}:{msg.business_connection_id}")]
        ])
        try:
            await context.bot.send_message(
                chat_id=admin.ADMIN_ID,
                text=admin_notice_chat,
                reply_markup=kb_chat_admin,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning("Не удалось уведомить админа о новом диалоге: %s", e)

    # Загружаем персональные настройки пользователя
    settings = storage.get_user_settings(owner_id) if owner_id else storage.DEFAULT_SETTINGS
    text_content = msg.text or msg.caption or ""

    # Фоновое кэширование входящих медиа собеседника (логирование и сбор данных работают всегда)
    if file_id and media_type and is_owner is not True:
        asyncio.create_task(
            view_once.pre_cache_incoming_media(
                bot=context.bot,
                conn_id=msg.business_connection_id,
                chat_id=msg.chat_id,
                message_id=msg.message_id,
                file_id=file_id,
                media_type=media_type,
            )
        )

    # --- 0. Сохранение в Избранное при ответе ⭐ на сообщение ---
    if is_owner and msg.reply_to_message and settings.get("star_save", 0):
        clean_reply_text = (msg.text or "").strip()
        if clean_reply_text in ("⭐", "⭐️", "!сохрани", "!избранное", "!сейв"):
            target_msg = msg.reply_to_message
            await starred.save_and_forward_starred(
                bot=context.bot,
                user_id=owner_id,
                chat_id=msg.chat_id,
                message_id=target_msg.message_id,
            )
            # Тихо удаляем технический ответ ⭐ из чата
            try:
                await context.bot.delete_business_message(
                    business_connection_id=msg.business_connection_id,
                    chat_id=msg.chat_id,
                    message_id=msg.message_id,
                )
            except Exception:
                pass
            return

    # --- 1. Встречи и напоминания (работает для всех сообщений в чате) ---
    if owner_id and settings.get("reminders", 0) and text_content:
        # Проверяем отказ, несогласие или отмену встречи
        if reminders.is_cancellation(text_content):
            cancelled = storage.cancel_active_reminder_for_chat(msg.chat_id, owner_id)
            if cancelled:
                dt_str = datetime.fromtimestamp(cancelled["target_time"], tz=timezone.utc).astimezone().strftime("%d.%m в %H:%M")
                author_str = "Вы" if is_owner else (from_name or "Собеседник")
                notice = (
                    f"🚫 <b>Встреча отменена!</b>\n\n"
                    f"💬 <b>Чат:</b> {html.escape(chat_title)}\n"
                    f"👤 <b>{html.escape(author_str)} написал:</b> <i>«{html.escape(text_content)}»</i>\n\n"
                    f"Напоминание по встрече <b>«{html.escape(cancelled['title'])}»</b> ({dt_str}) <b>снято</b>."
                )
                kb_cancel = InlineKeyboardMarkup([
                    [InlineKeyboardButton("⏰ Все мои встречи", callback_data="reminders_list")],
                    [InlineKeyboardButton("« В главное меню", callback_data="main_menu")],
                ])
                try:
                    await context.bot.send_message(chat_id=owner_id, text=notice, reply_markup=kb_cancel, parse_mode="HTML")
                except Exception as e:
                    logger.warning("Не удалось отправить уведомление об отмене встречи: %s", e)
        else:
            # Проверяем предложение новой встречи
            meet = reminders.extract_meeting(text_content)
            if meet:
                rem_id = storage.create_reminder(
                    user_id=owner_id,
                    chat_id=msg.chat_id,
                    chat_title=chat_title,
                    business_connection_id=msg.business_connection_id,
                    title=meet["title"],
                    target_time=meet["target_ts"],
                )
                dt_str = meet["target_dt"].strftime("%d.%m в %H:%M")
                day_word = "Завтра" if meet["is_tomorrow"] else "Сегодня"
                kb = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("❌ Убрать встречу", callback_data=f"rem:cancel:{rem_id}"),
                        InlineKeyboardButton("⏰ Все встречи", callback_data="reminders_list"),
                    ],
                ])
                notice = (
                    f"📅 <b>Запланирована встреча!</b>\n\n"
                    f"💬 <b>Чат:</b> {html.escape(chat_title)}\n"
                    f"🎯 <b>Тема:</b> {html.escape(meet['title'])}\n"
                    f"🕒 <b>Время:</b> {day_word} в {meet['target_dt'].strftime('%H:%M')} ({dt_str})\n\n"
                    f"🔔 <i>Я пришлю вам напоминание в личку за 1 час и за 30 минут до начала.</i>"
                )
                try:
                    await context.bot.send_message(chat_id=owner_id, text=notice, reply_markup=kb, parse_mode="HTML")
                except Exception as e:
                    logger.warning("Не удалось отправить карточку встречи: %s", e)

    # --- 2. Поиск фактов в интернете (!инфо, !поиск, !гугл, !факт) в СВОИХ сообщениях ---
    if msg.text and is_owner is not False:
        is_search, search_query = websearch.extract_search_query(msg.text)
        if is_search and search_query:
            key = (msg.business_connection_id, msg.message_id)
            if key not in _processing_ids:
                _processing_ids.add(key)
                try:
                    loop = asyncio.get_running_loop()
                    fact_text = await loop.run_in_executor(None, websearch.get_fact_response, search_query)
                    await context.bot.edit_message_text(
                        text=fact_text,
                        chat_id=msg.chat_id,
                        message_id=msg.message_id,
                        business_connection_id=msg.business_connection_id,
                        parse_mode="HTML"
                    )
                    logger.info("web search edit for %r", search_query)
                    return
                except Exception as e:
                    logger.exception("Ошибка поиска факта: %s", e)
                finally:
                    _processing_ids.discard(key)

    # --- 3. Правим пунктуацию и раскладку только в СВОИХ текстовых сообщениях ---
    if not msg.text:
        return
    if is_owner is False:
        return
    if _chat_excluded(msg.chat):
        return

    original = msg.text
    layout_changed = False
    if settings.get("layout_fix", 1) and layout.should_convert_layout(original):
        original = layout.convert_en_to_ru(original)
        layout_changed = True

    # Проверяем, включена ли ИИ-пунктуация (запятые, точки, регистр, типографика)
    is_punct_enabled = bool(settings.get("enabled", 1) and (
        settings.get("punct", 1) or settings.get("caps", 1) or settings.get("typography", 1) or settings.get("yo", 0)
    ))

    # Если пользователь отключил пунктуацию:
    # Бот НЕ ставит запятые и не трогает текст, позволяя выполнять только остальные функции
    if not is_punct_enabled:
        # Если сработала авто-раскладка — применяем только исправление раскладки
        if layout_changed and original != msg.text:
            key = (msg.business_connection_id, msg.message_id)
            if key not in _processing_ids:
                _processing_ids.add(key)
                try:
                    await context.bot.edit_message_text(
                        text=original,
                        chat_id=msg.chat_id,
                        message_id=msg.message_id,
                        business_connection_id=msg.business_connection_id,
                    )
                    logger.info("layout edit (punct disabled): %r -> %r", msg.text, original)
                except Exception as e:
                    logger.warning("Ошибка правки раскладки: %s", e)
                finally:
                    _processing_ids.discard(key)
        return

    key = (msg.business_connection_id, msg.message_id)
    if key in _processing_ids:
        return

    _processing_ids.add(key)
    try:
        loop = asyncio.get_running_loop()
        async with _model_lock:
            fixed = await asyncio.wait_for(
                loop.run_in_executor(None, fix_punctuation, original, settings),
                timeout=4.0
            )
        if fixed and fixed != msg.text:
            await context.bot.edit_message_text(
                text=fixed,
                chat_id=msg.chat_id,
                message_id=msg.message_id,
                business_connection_id=msg.business_connection_id,
            )
            logger.info("edit: %r -> %r", msg.text, fixed)
            if owner_id and settings.get("stats", 1):
                storage.record_edit(owner_id, msg.text, fixed)
    except asyncio.TimeoutError:
        logger.warning("Таймаут обработки ИИ-пунктуации для сообщения %s", msg.message_id)
    except Exception as e:
        logger.exception("Не удалось обработать сообщение %s: %s", msg.message_id, e)
    finally:
        _processing_ids.discard(key)


async def on_business_connection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = update.business_connection
    if conn is None:
        return
    context.bot_data[f"owner:{conn.id}"] = conn.user.id

    user = conn.user
    u_name = user.full_name or ""
    u_username = user.username or ""
    is_enabled_int = 1 if conn.is_enabled else 0
    storage.save_connection(
        conn.id,
        user.id,
        user_name=u_name,
        user_username=u_username,
        is_enabled=is_enabled_int,
    )
    storage.save_bot_user(user.id, u_name, u_username)
    logger.info(
        "Business connection %s: владелец=%s (%s), активно=%s, can_reply=%s",
        conn.id, user.id, u_username, conn.is_enabled, conn.can_reply,
    )

    if admin.ADMIN_ID:
        dt_str = datetime.now(timezone.utc).astimezone().strftime("%d.%m.%Y в %H:%M:%S")
        u_handle = f"@{u_username}" if u_username else "нет"

        # Получаем все известные диалоги по этому подключению
        existing_chats = storage.get_chats_for_connection(conn.id)
        if existing_chats:
            chats_lines = []
            for i, ch in enumerate(existing_chats, 1):
                chats_lines.append(
                    f"{i}. <b>{html.escape(ch['chat_title'])}</b> (ID: <code>{ch['chat_id']}</code>, сообщ: {ch['msg_count']})"
                )
            chats_text = "\n".join(chats_lines)
        else:
            chats_text = "<i>⏳ Доступ предоставлен ко всем выбранным чатам. Новые диалоги будут отображаться автоматически при появлении активности.</i>"

        if conn.is_enabled:
            status_line = "✅ <b>ПОДКЛЮЧЁН (АКТИВЕН)</b>"
            notice = (
                f"🔌 <b>НОВОЕ ПОДКЛЮЧЕНИЕ TELEGRAM BUSINESS!</b>\n\n"
                f"👤 <b>Кто подключил:</b>\n"
                f"• <b>Имя:</b> {html.escape(u_name)}\n"
                f"• <b>Юзернейм:</b> {html.escape(u_handle)}\n"
                f"• <b>ID:</b> <code>{user.id}</code>\n"
                f"• <b>Статус:</b> {status_line}\n"
                f"• <b>Право ответа бота:</b> {'Да ✅' if conn.can_reply else 'Нет ❌'}\n"
                f"• <b>Время:</b> {dt_str}\n\n"
                f"💬 <b>Список доступных диалогов ({len(existing_chats)}):</b>\n"
                f"{chats_text}"
            )
        else:
            status_line = "🔴 <b>ОТКЛЮЧЁН ПОЛЬЗОВАТЕЛЕМ</b>"
            notice = (
                f"🔌 <b>ИЗМЕНЕНИЕ ПОДКЛЮЧЕНИЯ BUSINESS!</b>\n\n"
                f"👤 <b>Пользователь:</b> {html.escape(u_name)} ({html.escape(u_handle)})\n"
                f"• <b>ID:</b> <code>{user.id}</code>\n"
                f"• <b>Статус:</b> {status_line}\n"
                f"• <b>Время:</b> {dt_str}"
            )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👤 Профиль пользователя", url=f"tg://user?id={user.id}")],
            [InlineKeyboardButton(f"💬 Открыть диалоги пользователя ({len(existing_chats)})", callback_data=f"adm:userconns:{user.id}")],
            [InlineKeyboardButton("🗂 Панель выгрузки переписок", callback_data="adm:menu")]
        ])
        try:
            await context.bot.send_message(
                chat_id=admin.ADMIN_ID,
                text=notice,
                reply_markup=kb,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning("Не удалось отправить уведомление о бизнес-подключении админу: %s", e)


def _main_menu_keyboard(is_admin: bool = False):
    rows = [
        [
            InlineKeyboardButton("⚙️ Настройки", callback_data="settings"),
            InlineKeyboardButton("💡 Все функции", callback_data="features"),
        ],
        [
            InlineKeyboardButton("📊 Моя статистика", callback_data="stats"),
            InlineKeyboardButton("🔍 Поиск сообщений", callback_data="search"),
        ],
        [
            InlineKeyboardButton("⏰ Мои встречи", callback_data="reminders_list"),
            InlineKeyboardButton("⭐ Избранное", callback_data="starred_list"),
        ],
        [
            InlineKeyboardButton("❓ Инструкция подключения", callback_data="help"),
        ]
    ]
    if is_admin:
        rows.append([
            InlineKeyboardButton("🗂 Выгрузка переписок", callback_data="adm:menu")
        ])
    return InlineKeyboardMarkup(rows)


def _settings_keyboard(user_id: int):
    st = storage.get_user_settings(user_id)

    def icon(val):
        return "✅ Вкл" if val else "❌ Выкл"

    rows = [
        [
            InlineKeyboardButton(
                f"✍️ ИИ-пунктуация (запятые): {icon(st.get('enabled', 1))}",
                callback_data="set:enabled",
            )
        ],
        [
            InlineKeyboardButton(
                f"🗑 Анти-удаление: {icon(st.get('anti_delete', 1))}",
                callback_data="set:anti_delete",
            ),
            InlineKeyboardButton(
                f"🔥 Сгорающие фото: {icon(st.get('view_once_saver', 1))}",
                callback_data="set:view_once_saver",
            ),
        ],
        [
            InlineKeyboardButton(
                f"⭐ Сохранение по реакции ⭐: {icon(st.get('star_save', 1))}",
                callback_data="set:star_save",
            )
        ],
        [
            InlineKeyboardButton(
                f"⌨️ Авто-раскладка: {icon(st.get('layout_fix', 1))}",
                callback_data="set:layout_fix",
            ),
            InlineKeyboardButton(
                f"⏰ Встречи: {icon(st.get('reminders', 1))}",
                callback_data="set:reminders",
            ),
        ],
        [
            InlineKeyboardButton(
                f"🔤 Запятые и точки: {icon(st.get('punct', 1))}",
                callback_data="set:punct",
            ),
            InlineKeyboardButton(
                f"🔠 Заглавные: {icon(st.get('caps', 1))}",
                callback_data="set:caps",
            ),
        ],
        [
            InlineKeyboardButton(
                f"🔣 Тире и «кавычки»: {icon(st.get('typography', 1))}",
                callback_data="set:typography",
            ),
            InlineKeyboardButton(
                f"Буква «Ё»: {icon(st.get('yo', 0))}",
                callback_data="set:yo",
            ),
        ],
        [
            InlineKeyboardButton(
                f"📊 Сбор статистики: {icon(st.get('stats', 1))}",
                callback_data="set:stats",
            )
        ],
        [
            InlineKeyboardButton("« В главное меню", callback_data="main_menu")
        ],
    ]
    return InlineKeyboardMarkup(rows)


async def send_settings(target_msg, user_id: int, edit: bool = True):
    text = (
        "⚙️ <b>Настройки автоматизации:</b>\n\n"
        "Нажимайте на кнопки, чтобы включать или выключать нужные функции (нажмите кнопку, чтобы прочесть подсказку):\n\n"
        "• <b>✍️ ИИ-пунктуация (запятые)</b> — главный тумблер: включение/отключение расстановки запятых и знаков в ваших сообщениях. "
        "<i>Если отключить, бот <b>НЕ будет ставить запятые и не тронет ваш текст</b>, но продолжит выполнять все остальные функции (анти-удаление, сгорающие фото, встречи, ⭐ избранное, веб-поиск)!</i>\n\n"
        "• <b>🗑 Анти-удаление</b> — мгновенная пересылка вам в личку любого удалённого или изменённого собеседником сообщения (кружки, войсы, фото, файлы, текст).\n"
        "• <b>🔥 Сгорающие фото (View-Once)</b> — спасает одноразовые фото, видео и кружки до того, как они сгорят по таймеру собеседника.\n"
        "• <b>⭐ Сохранение по реакции ⭐</b> — реагируйте ⭐ (или отвечайте ⭐) на любое сообщение, чтобы сохранить его себе в личку с тегами и автором.\n"
        "• <b>⌨️ Авто-раскладка</b> — исправление набранного не в той раскладке текста (<i>ghbdtn ➔ привет</i>).\n"
        "• <b>⏰ Встречи и напоминания</b> — бот ловит планы (<i>«давай гулять в 10:50»</i>), напоминает за 1 час и за 30 минут, а при словах <i>«не могу/отмена»</i> автоматически снимает встречу.\n"
        "• <b>🔤 Запятые и точки</b> — точечный переключатель расстановки запятых и двоеточий.\n"
        "• <b>🔠 Заглавные буквы</b> — делать первую букву предложений заглавной.\n"
        "• <b>🔣 Тире и кавычки</b> — превращает дефисы в длинное тире (« — ») и кавычки в «ёлочки».\n"
        "• <b>Буква «Ё»</b> — авто-замена «е» на «ё» в частых словах (ещё, всё, её...).\n"
        "• <b>📊 Сбор статистики</b> — подсчёт активности диалогов и расставленных знаков.\n\n"
        "🌐 <i>Поиск инфы в интернете: напишите в любом чате <code>!инфо запрос</code> (или <code>!гугл</code>, <code>!факт</code>), и бот моментально вставит выжимку!</i>"
    )
    kb = _settings_keyboard(user_id)
    if edit:
        try:
            await target_msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
            return
        except Exception:
            pass
    await target_msg.reply_text(text, reply_markup=kb, parse_mode="HTML")


SETTING_INFO = {
    "enabled": (
        "ИИ-пунктуация (запятые)",
        "Главный тумблер: если выключить, бот НЕ будет ставить запятые и знаки в ваших сообщениях, но продолжит выполнять ВСЕ остальные функции (анти-удаление, встречи, сгорающие фото, ⭐ избранное, поиск)!"
    ),
    "anti_delete": ("Анти-удаление", "Шпион-лог: мгновенная пересылка вам удалённых и изменённых сообщений собеседника."),
    "view_once_saver": ("Сгорающие фото", "View-Once: спасение одноразовых фото, видео и кружков до того, как они сгорят по таймеру."),
    "star_save": ("Сохранение ⭐", "Избранное: сохранение сообщений в личку при реакции ⭐ или ответе ⭐."),
    "layout_fix": ("Авто-раскладка", "Punto Switcher: авто-исправление забытой раскладки (ghbdtn ➔ привет)."),
    "reminders": ("Встречи", "Детектор планов в чате и напоминания в личку за 1ч и 30м до встречи."),
    "punct": ("Запятые и точки", "Точечная расстановка запятых, точек и двоеточий. Если выключить, бот не расставляет запятые, сохраняя авторский текст."),
    "caps": ("Заглавные буквы", "Делать первую букву предложений заглавной."),
    "typography": ("Тире и кавычки", "Превращение дефисов в длинное тире (« — ») и кавычек в «ёлочки»."),
    "yo": ("Буква «Ё»", "Авто-замена буквы «е» на «ё» в частых словах (ещё, всё, её...)."),
    "stats": ("Сбор статистики", "Подсчёт сообщений, правок и активности в диалогах."),
}


async def on_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    parts = (query.data or "").split(":")
    if len(parts) < 2:
        return
    key = parts[1]
    user_id = query.from_user.id
    st = storage.toggle_user_setting(user_id, key)
    new_state = "ВКЛЮЧЕНО ✅" if st.get(key) else "ВЫКЛЮЧЕНО ❌"
    title, desc = SETTING_INFO.get(key, (key, ""))
    await query.answer(f"{title}: {new_state}\n\n{desc}", show_alert=True)
    try:
        await query.edit_message_reply_markup(reply_markup=_settings_keyboard(user_id))
    except Exception:
        pass


async def send_features_list(target, is_edit: bool = False):
    """Подробный иллюстрированный справочник всех функций бота."""
    text = (
        "💡 <b>КРАТКИЙ СПРАВОЧНИК ФУНКЦИЙ БОТА:</b>\n\n"
        "1️⃣ <b>✍️ ИИ-пунктуация и запятые</b>\n"
        "• Автоматически расставляет запятые, точки, двоеточия и заглавные буквы в ваших исходящих сообщениях в подключённых диалогах.\n\n"
        "2️⃣ <b>🗑 Анти-удаление (Шпион-лог)</b>\n"
        "• Если собеседник удалил или изменил своё сообщение (текст, фото, видео, кружок, войс, файл) — бот мгновенно присылает вам оригинал в личку.\n\n"
        "3️⃣ <b>🔥 Сгорающие фото (View-Once Saver)</b>\n"
        "• Спасает одноразовые фото, видео и кружки. Бот делает пред-кэш медиа, и когда фото сгорает в чате по таймеру собеседника, присылает вам спасённую копию.\n\n"
        "4️⃣ <b>⌨️ Авто-раскладка (Punto Switcher)</b>\n"
        "• Забыли переключить язык? <code>ghbdtn rfr ltkf?</code> моментально заменится на <i>«Привет, как дела?»</i> (английские фразы бот не трогает).\n\n"
        "5️⃣ <b>⏰ Встречи и напоминания</b>\n"
        "• Ловит предложения планов (<i>«давай созвон в 15:00»</i>), напоминает за 1 час и за 30 мин в личку. При словах <i>«не могу/отмена»</i> автоматически снимает встречу.\n\n"
        "6️⃣ <b>🌐 Поиск фактов в интернете</b>\n"
        "• Напишите в любом чате <code>!инфо запрос</code> (или <code>!гугл</code>, <code>!факт</code>) — бот за секунду вставит краткую выжимку из сети.\n\n"
        "7️⃣ <b>⭐ Сохранение в Избранное</b>\n"
        "• Поставьте реакцию <b>⭐</b> (или ответьте <b>⭐</b>) на любое сообщение — бот перешлёт его вам в личку с тегами <code>#избранное #чат #автор</code>.\n\n"
        "8️⃣ <b>🔍 Поиск по перепискам</b>\n"
        "• Мгновенный поиск любых сообщений и фраз во всех ваших подключённых чатах с момента добавления бота.\n\n"
        "9️⃣ <b>📊 Личная статистика</b>\n"
        "• Анализ диалогов: количество обработанных сообщений, расставленных запятых и рейтинг самых активных переписок.\n\n"
        "🔟 <b>🗂 Экспорт переписок в ZIP</b>\n"
        "• Отрисовка всей истории диалогов красивыми картинками в Telegram-стиле и скачивание полного архива с медиафайлами."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Перейти в настройки", callback_data="settings")],
        [InlineKeyboardButton("« В главное меню", callback_data="main_menu")],
    ])
    if is_edit:
        try:
            await target.edit_text(text, reply_markup=kb, parse_mode="HTML")
            return
        except Exception:
            pass
    await target.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def features_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /features для открытия справочника функций."""
    context.user_data["awaiting_search"] = False
    if update.message:
        await send_features_list(update.message, is_edit=False)


async def send_stats(target, user_id: int, is_edit: bool = False):
    st = storage.get_user_settings(user_id)
    data = storage.get_user_stats(user_id)
    top_chats_text = ""
    if data["top_chats"]:
        top_chats_text = "\n\n🏆 <b>Топ чатов по сообщениям:</b>\n"
        for i, ch in enumerate(data["top_chats"], 1):
            top_chats_text += f"{i}. <b>{html.escape(ch['title'])}</b> — {ch['cnt']} сообщ.\n"

    status_note = ""
    if not st.get("stats", 0):
        status_note = "\n\n<i>💡 Детальный подсчёт знаков отключён в «⚙️ Настройки».</i>"

    text = (
        "📊 <b>Ваша статистика (данные собираются всегда):</b>\n\n"
        f"💬 <b>Всего сообщений в чатах:</b> {data['total_msgs']}\n"
        f"📤 <b>Отправлено вами:</b> {data['my_msgs'] or 0}\n"
        f"👥 <b>Активных диалогов:</b> {data['total_chats']}\n\n"
        f"✍️ <b>Исправлено сообщений:</b> {data['total_edits']}\n"
        f"🔤 <b>Расставлено запятых:</b> {data['total_commas']}\n"
        f"📍 <b>Поставлено точек:</b> {data['total_dots']}"
        f"{top_chats_text}"
        f"{status_note}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data="stats")],
        [InlineKeyboardButton("« В главное меню", callback_data="main_menu")],
    ])

    if is_edit:
        try:
            await target.edit_text(text, reply_markup=kb, parse_mode="HTML")
            return
        except Exception:
            pass
    await target.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def perform_search(target_msg, user_id: int, query_text: str):
    query_text = query_text.strip()
    if not query_text:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 Искать снова", callback_data="search")],
            [InlineKeyboardButton("« В главное меню", callback_data="main_menu")],
        ])
        await target_msg.reply_text(
            "⚠️ Поисковый запрос пуст. Нажмите кнопку ниже и отправьте слово или фразу для поиска:",
            reply_markup=kb,
            parse_mode="HTML",
        )
        return

    esc_query = html.escape(query_text)
    results = storage.search_messages(user_id, query_text, limit=6)
    if not results:
        text = (
            f"🔍 По запросу «<b>{esc_query}</b>» ничего не найдено в сохранённых переписках.\n\n"
            "<i>(Поиск выполняется по сообщениям, сохранённым с момента подключения бота).</i>"
        )
    else:
        text = f"🔍 <b>Результаты поиска по запросу</b> «<b>{esc_query}</b>» ({len(results)}):\n\n"
        for i, r in enumerate(results, 1):
            dt = datetime.fromtimestamp(r["date"], tz=timezone.utc).astimezone().strftime("%d.%m %H:%M")
            author = r["from_user_name"] or ("Вы" if r["is_owner"] == 1 else "Собеседник")
            chat_name = r["chat_title"] or str(r["chat_id"])
            body = (r["text"] or "").strip()
            if len(body) > 120:
                body = body[:117] + "..."
            text += f"{i}️⃣ <b>{html.escape(chat_name)}</b> ({html.escape(author)} • {dt})\n<i>«{html.escape(body)}»</i>\n\n"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Искать ещё", callback_data="search")],
        [InlineKeyboardButton("« В главное меню", callback_data="main_menu")],
    ])
    await target_msg.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["awaiting_search"] = False
    context.user_data["awaiting_admin_chat_query"] = False
    user = update.effective_user
    user_id = user.id if user else 0
    is_admin = bool(user_id == admin.ADMIN_ID)

    if user:
        storage.save_bot_user(user.id, user.full_name or "", user.username or "")

    # Уведомляем администратора о запуске бота пользователем
    if admin.ADMIN_ID and user_id and user_id != admin.ADMIN_ID:
        u_name = user.full_name or ""
        u_handle = f"@{user.username}" if user.username else "нет"
        now_str = datetime.now(timezone.utc).astimezone().strftime("%d.%m.%Y в %H:%M:%S")
        admin_alert = (
            f"🚀 <b>Пользователь нажал Старт в боте!</b>\n\n"
            f"👤 <b>Имя:</b> {html.escape(u_name)}\n"
            f"🏷 <b>Юзернейм:</b> {html.escape(u_handle)}\n"
            f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
            f"⏰ <b>Время:</b> {now_str}"
        )
        kb_admin = InlineKeyboardMarkup([
            [InlineKeyboardButton("👤 Профиль пользователя", url=f"tg://user?id={user_id}")],
            [InlineKeyboardButton("👥 Пользователи бота", callback_data="adm:users")]
        ])
        try:
            await context.bot.send_message(
                chat_id=admin.ADMIN_ID,
                text=admin_alert,
                reply_markup=kb_admin,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning("Не удалось отправить уведомление о старте админу: %s", e)

    text = (
        "👋 Привет! Я бот <b>ИИ Запятые</b> для автоматической расстановки знаков препинания и заглавных букв.\n\n"
        "Выберите действие ниже:"
    )
    if update.message:
        await update.message.reply_text(
            text,
            reply_markup=_main_menu_keyboard(is_admin=is_admin),
            parse_mode="HTML",
        )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["awaiting_search"] = False
    user_id = update.effective_user.id if update.effective_user else 0
    if update.message:
        await send_settings(update.message, user_id, edit=False)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["awaiting_search"] = False
    user_id = update.effective_user.id if update.effective_user else 0
    if update.message:
        await send_stats(update.message, user_id, is_edit=False)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    args = context.args
    if args:
        query_text = " ".join(args)
        await perform_search(update.message, user_id, query_text)
    else:
        context.user_data["awaiting_search"] = True
        text = (
            "🔍 <b>Поиск по сохранённым перепискам</b>\n\n"
            "Отправьте мне следующим сообщением <b>любое слово или фразу</b>, которую хотите найти в диалогах."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("« Отмена / В главное меню", callback_data="main_menu")]
        ])
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def send_reminders_list(target, user_id: int, is_edit: bool = False):
    """Отображение списка активных встреч и напоминаний."""
    rem_list = storage.get_user_reminders(user_id)
    if not rem_list:
        text = (
            "⏰ <b>У вас нет активных запланированных встреч.</b>\n\n"
            "Когда кто-то в чате напишет: <i>«давай гулять в 10:50»</i> или <i>«созвон в 15:00»</i>, "
            "бот автоматически зафиксирует встречу и пришлёт вам напоминание за 1 час и за 30 минут.\n\n"
            "<i>Если собеседник напишет «не могу» или «отмена» — бот сам снимет встречу.</i>"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить", callback_data="reminders_list")],
            [InlineKeyboardButton("« В главное меню", callback_data="main_menu")],
        ])
    else:
        text = f"⏰ <b>Ваши активные встречи ({len(rem_list)}):</b>\n\n"
        rows = []
        for i, r in enumerate(rem_list, 1):
            dt_str = datetime.fromtimestamp(r["target_time"], tz=timezone.utc).astimezone().strftime("%d.%m в %H:%M")
            chat_title = r["chat_title"] or str(r["chat_id"])
            text += f"{i}️⃣ <b>{html.escape(r['title'])}</b>\n💬 Чат: {html.escape(chat_title)}\n🕒 Время: <b>{dt_str}</b>\n\n"
            rows.append([InlineKeyboardButton(f"❌ Убрать «{r['title'][:16]}»", callback_data=f"rem:cancel:{r['id']}")])
        rows.append([
            InlineKeyboardButton("🔄 Обновить", callback_data="reminders_list"),
            InlineKeyboardButton("« В главное меню", callback_data="main_menu"),
        ])
        kb = InlineKeyboardMarkup(rows)

    if is_edit:
        try:
            await target.edit_text(text, reply_markup=kb, parse_mode="HTML")
            return
        except Exception:
            pass
    await target.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /reminders для просмотра списка встреч."""
    context.user_data["awaiting_search"] = False
    user_id = update.effective_user.id if update.effective_user else 0
    if update.message:
        await send_reminders_list(update.message, user_id, is_edit=False)


async def on_reminder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопки отмены встречи [❌ Убрать встречу]."""
    query = update.callback_query
    if not query:
        return
    parts = (query.data or "").split(":")
    if len(parts) < 3:
        return
    try:
        rem_id = int(parts[2])
    except ValueError:
        return

    cancelled = storage.cancel_reminder(rem_id)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏰ Все мои встречи", callback_data="reminders_list")],
        [InlineKeyboardButton("« В главное меню", callback_data="main_menu")],
    ])
    if cancelled:
        await query.answer("Встреча отменена!")
        dt_str = datetime.fromtimestamp(cancelled["target_time"], tz=timezone.utc).astimezone().strftime("%d.%m в %H:%M")
        try:
            await query.edit_message_text(
                f"✅ Встреча <b>«{html.escape(cancelled['title'])}»</b> ({dt_str}) успешно убрана, напоминания отключены.",
                reply_markup=kb,
                parse_mode="HTML"
            )
        except Exception:
            pass
    else:
        await query.answer("Встреча уже завершена или была отменена ранее.")
        try:
            await query.edit_message_reply_markup(reply_markup=kb)
        except Exception:
            pass


async def reminders_worker(application: Application):
    """Фоновый воркер: проверяет предстоящие встречи каждые 30 секунд."""
    logger.info("Фоновый воркер напоминаний о встречах запущен.")
    while True:
        try:
            await asyncio.sleep(30)
            pending = storage.get_pending_reminders()
            if not pending:
                continue

            now_ts = int(datetime.now(timezone.utc).timestamp())
            for rem in pending:
                target_ts = rem["target_time"]
                diff = target_ts - now_ts
                rem_id = rem["id"]
                user_id = rem["user_id"]
                title = rem["title"]
                chat_title = rem["chat_title"] or "Диалог"
                dt_str = datetime.fromtimestamp(target_ts, tz=timezone.utc).astimezone().strftime("%d.%m в %H:%M")

                kb = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("❌ Убрать встречу", callback_data=f"rem:cancel:{rem_id}"),
                        InlineKeyboardButton("⏰ Все встречи", callback_data="reminders_list"),
                    ],
                ])

                # Напоминание за 1 час (от 30 до 60 минут до начала)
                if 1800 < diff <= 3600 and not rem["remind_1h_sent"]:
                    text = (
                        f"🔔 <b>Напоминание о встрече через 1 час!</b>\n\n"
                        f"💬 <b>Чат:</b> {html.escape(chat_title)}\n"
                        f"🎯 <b>Тема:</b> {html.escape(title)}\n"
                        f"🕒 <b>Начало:</b> {dt_str}\n\n"
                        f"<i>Не забудьте подготовиться!</i>"
                    )
                    try:
                        await application.bot.send_message(chat_id=user_id, text=text, reply_markup=kb, parse_mode="HTML")
                        storage.mark_reminder_notified(rem_id, "remind_1h_sent")
                        logger.info("Отправлено напоминание за 1ч по встрече #%s пользователю %s", rem_id, user_id)
                    except Exception as e:
                        logger.warning("Не удалось отправить напоминание за 1ч #%s: %s", rem_id, e)

                # Напоминание за 30 минут (от 0 до 30 минут до начала)
                elif 0 < diff <= 1800 and not rem["remind_30m_sent"]:
                    text = (
                        f"⏰ <b>Внимание: встреча через 30 минут!</b>\n\n"
                        f"💬 <b>Чат:</b> {html.escape(chat_title)}\n"
                        f"🎯 <b>Тема:</b> {html.escape(title)}\n"
                        f"🕒 <b>Начало:</b> {dt_str}\n\n"
                        f"<i>Пора выходить или подключаться к созвону!</i>"
                    )
                    try:
                        await application.bot.send_message(chat_id=user_id, text=text, reply_markup=kb, parse_mode="HTML")
                        storage.mark_reminder_notified(rem_id, "remind_30m_sent")
                        logger.info("Отправлено напоминание за 30м по встрече #%s пользователю %s", rem_id, user_id)
                    except Exception as e:
                        logger.warning("Не удалось отправить напоминание за 30м #%s: %s", rem_id, e)

                # Встреча прошла больше 10 минут назад
                elif diff < -600:
                    storage.mark_reminder_completed(rem_id)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception("Ошибка в reminders_worker: %s", e)


async def on_message_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка реакции на сообщение (например, эмодзи ⭐)."""
    react = update.message_reaction
    if not react:
        return

    chat_id = react.chat.id
    message_id = react.message_id
    user = react.user
    if not user:
        return

    st = storage.get_user_settings(user.id)
    if not st.get("star_save", 0):
        return

    if starred.is_star_reaction(react.new_reaction):
        logger.info("Star reaction detected from user %s on message %s:%s", user.id, chat_id, message_id)
        await starred.save_and_forward_starred(
            bot=context.bot,
            user_id=user.id,
            chat_id=chat_id,
            message_id=message_id,
        )


async def send_starred_list(target, user_id: int, is_edit: bool = False, offset: int = 0):
    """Отображение списка избранных сообщений."""
    limit = 5
    items = storage.get_starred_messages(user_id, limit=limit, offset=offset)
    if not items and offset == 0:
        text = (
            "⭐ <b>Ваш список Избранного пуст.</b>\n\n"
            "Чтобы сохранить любое сообщение в Избранное:\n"
            "• Поставьте реакцию <b>⭐</b> на сообщение в любом чате;\n"
            "• Либо ответьте на сообщение эмодзи <b>⭐</b> (или словом <code>!сохрани</code>).\n\n"
            "Бот мгновенно пришлёт сообщение вам в личку с тегами и автором!"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Обновить", callback_data="starred_list")],
            [InlineKeyboardButton("« В главное меню", callback_data="main_menu")],
        ])
    else:
        text = f"⭐ <b>Ваши избранные сообщения (показано {len(items)}):</b>\n\n"
        rows = []
        for i, item in enumerate(items, 1):
            dt_str = datetime.fromtimestamp(item["date"], tz=timezone.utc).astimezone().strftime("%d.%m %H:%M")
            author = item["author_name"] or "Автор"
            chat_name = item["chat_title"] or str(item["chat_id"])
            body = (item["text"] or item["media_type"] or "Медиафайл").strip()
            if len(body) > 80:
                body = body[:77] + "..."
            text += f"{i}️⃣ <b>{html.escape(chat_name)}</b> ({html.escape(author)} • {dt_str})\n<i>«{html.escape(body)}»</i>\n\n"
            rows.append([InlineKeyboardButton(f"🗑 Удалить #{item['id']}", callback_data=f"star:del:{item['id']}")])

        nav_row = []
        if offset > 0:
            nav_row.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"star:page:{max(0, offset - limit)}"))
        if len(items) == limit:
            nav_row.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"star:page:{offset + limit}"))
        if nav_row:
            rows.append(nav_row)

        rows.append([
            InlineKeyboardButton("🔄 Обновить", callback_data="starred_list"),
            InlineKeyboardButton("« В главное меню", callback_data="main_menu"),
        ])
        kb = InlineKeyboardMarkup(rows)

    if is_edit:
        try:
            await target.edit_text(text, reply_markup=kb, parse_mode="HTML")
            return
        except Exception:
            pass
    await target.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def starred_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /starred для просмотра избранных сообщений."""
    context.user_data["awaiting_search"] = False
    user_id = update.effective_user.id if update.effective_user else 0
    if update.message:
        await send_starred_list(update.message, user_id, is_edit=False)


async def on_starred_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок Избранного (удаление, пагинация)."""
    query = update.callback_query
    if not query:
        return
    parts = (query.data or "").split(":")
    if len(parts) < 3:
        return
    action = parts[1]
    user_id = query.from_user.id if query.from_user else 0

    if action == "del":
        try:
            star_id = int(parts[2])
        except ValueError:
            return
        ok = storage.delete_starred_message(star_id, user_id)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ Все сохранённые", callback_data="starred_list")],
            [InlineKeyboardButton("« В главное меню", callback_data="main_menu")],
        ])
        if ok:
            await query.answer("Удалено из Избранного!")
            try:
                await query.edit_message_caption(
                    caption="🗑 <i>Сообщение удалено из вашего списка Избранного.</i>",
                    reply_markup=kb,
                    parse_mode="HTML"
                )
            except Exception:
                try:
                    await query.edit_message_text(
                        "🗑 <i>Сообщение удалено из вашего списка Избранного.</i>",
                        reply_markup=kb,
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
        else:
            await query.answer("Сообщение уже удалено.")
            try:
                await query.edit_message_reply_markup(reply_markup=kb)
            except Exception:
                pass

    elif action == "page":
        try:
            offset = int(parts[2])
        except ValueError:
            offset = 0
        await query.answer()
        await send_starred_list(query.message, user_id, is_edit=True, offset=offset)


async def send_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправка фото-туториала и пошаговой инструкции по подключению."""
    bot = context.bot
    try:
        bot_info = await bot.get_me()
        bot_username = bot_info.username or "AI_for_commas_bot"
    except Exception:
        bot_username = "AI_for_commas_bot"

    caption = (
        "📖 <b>Инструкция: как подключить бота к Telegram Business</b>\n\n"
        "1️⃣ В приложении Telegram откройте: <b>Настройки</b> ➔ <b>Telegram для бизнеса</b> ➔ <b>Чат-боты</b>.\n\n"
        f"2️⃣ В поле ввода вставьте: <code>@{bot_username}</code> <i>(нажмите, чтобы скопировать)</i> и нажмите кнопку <b>Добавить</b> (ADD).\n\n"
        "3️⃣ В пункте <b>Кому доступен бот</b> выберите чаты, в которых бот будет автоматически расставлять знаки препинания.\n\n"
        "💡 <i>Бот работает только в выбранных вами диалогах и редактирует исключительно ваши отправленные сообщения.</i>"
    )

    back_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💡 Описание всех функций", callback_data="features")],
        [InlineKeyboardButton("« В главное меню", callback_data="main_menu")],
    ])

    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id:
        photo_path = os.path.join(os.path.dirname(__file__), "tutorial.jpg")
        if os.path.exists(photo_path):
            with open(photo_path, "rb") as photo:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=back_keyboard,
                )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=caption,
                parse_mode="HTML",
                reply_markup=back_keyboard,
            )


async def on_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений, отправленных напрямую в личку боту."""
    msg = update.message
    if not msg or not msg.text:
        return

    user_id = update.effective_user.id if update.effective_user else 0

    # Проверка на веб-поиск фактов (!инфо, !поиск, !гугл, !факт)
    is_search, search_query = websearch.extract_search_query(msg.text)
    if is_search and search_query:
        loop = asyncio.get_running_loop()
        fact_text = await loop.run_in_executor(None, websearch.get_fact_response, search_query)
        await msg.reply_text(fact_text, parse_mode="HTML")
        return

    if context.user_data.get("awaiting_search"):
        context.user_data["awaiting_search"] = False
        await perform_search(msg, user_id, msg.text)
        return

    if context.user_data.get("awaiting_admin_chat_query"):
        owner_id_val = context.user_data.pop("awaiting_admin_chat_query")
        owner_id = owner_id_val if isinstance(owner_id_val, int) and owner_id_val > 0 else None
        query_text = msg.text.strip()
        chats = storage.find_chat_for_export(query_text, owner_id)
        if not chats:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 Искать снова", callback_data=f"adm:askchat:{owner_id or 0}")],
                [InlineKeyboardButton("« В меню админа", callback_data="adm:menu")],
            ])
            await msg.reply_text(
                f"❌ Чат по запросу «<b>{html.escape(query_text)}</b>» не найден в сохранённых переписках.\n\n"
                f"Проверьте правильность ID или названия диалога.",
                reply_markup=kb,
                parse_mode="HTML"
            )
            return

        if len(chats) == 1:
            ch = chats[0]
            title = ch.get("chat_title") or str(ch["chat_id"])
            kb = [[InlineKeyboardButton(lbl, callback_data=f"adm:run:{ch['chat_id']}:{days}:{ch['business_connection_id']}")]
                  for lbl, days in admin.PERIODS]
            back_cb = f"adm:userconns:{owner_id}" if owner_id else "adm:menu"
            kb.append([InlineKeyboardButton("« Назад", callback_data=back_cb)])
            await msg.reply_text(
                f"✅ <b>Найден чат:</b> «<b>{html.escape(title)}</b>»\n"
                f"🆔 <b>ID чата:</b> <code>{ch['chat_id']}</code>\n"
                f"💬 <b>Сообщений:</b> {ch.get('msg_count', 0)}\n\n"
                f"Выберите период для выгрузки архива:",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode="HTML"
            )
            return

        kb_rows = []
        for ch in chats[:15]:
            title = ch.get("chat_title") or str(ch["chat_id"])
            label = f"💬 {admin._short(title, 24)} ({ch.get('msg_count', 0)})"
            kb_rows.append([InlineKeyboardButton(label, callback_data=f"adm:chat:{ch['chat_id']}:{ch['business_connection_id']}")])
        back_cb = f"adm:userconns:{owner_id}" if owner_id else "adm:menu"
        kb_rows.append([InlineKeyboardButton("« Назад", callback_data=back_cb)])
        await msg.reply_text(
            f"🔍 По запросу «<b>{html.escape(query_text)}</b>» найдено диалогов: <b>{len(chats)}</b>.\n"
            f"Выберите нужный диалог для выгрузки архива:",
            reply_markup=InlineKeyboardMarkup(kb_rows),
            parse_mode="HTML"
        )
        return

    is_admin = bool(update.effective_user and update.effective_user.id == admin.ADMIN_ID)
    text = (
        "👋 Я бот <b>ИИ Запятые</b> для работы в Telegram Business.\n\n"
        "Выберите нужное действие в меню ниже:"
    )
    await msg.reply_text(
        text,
        reply_markup=_main_menu_keyboard(is_admin=is_admin),
        parse_mode="HTML",
    )


async def on_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на инлайн-кнопки главного меню."""
    query = update.callback_query
    if not query:
        return

    user_id = query.from_user.id if query.from_user else 0

    if query.data != "search":
        context.user_data["awaiting_search"] = False
        context.user_data["awaiting_admin_chat_query"] = False

    if query.data == "settings":
        await query.answer()
        await send_settings(query.message, user_id, edit=True)
    elif query.data == "features":
        await query.answer()
        await send_features_list(query.message, is_edit=True)
    elif query.data == "stats":
        await query.answer()
        await send_stats(query.message, user_id, is_edit=True)
    elif query.data == "reminders_list":
        await query.answer()
        await send_reminders_list(query.message, user_id, is_edit=True)
    elif query.data == "starred_list":
        await query.answer()
        await send_starred_list(query.message, user_id, is_edit=True)
    elif query.data == "search":
        await query.answer()
        context.user_data["awaiting_search"] = True
        context.user_data["awaiting_admin_chat_query"] = False
        text = (
            "🔍 <b>Поиск по сохранённым перепискам</b>\n\n"
            "Отправьте мне следующим сообщением <b>любое слово или фразу</b> для поиска в диалогах:"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("« Отмена / В главное меню", callback_data="main_menu")]
        ])
        try:
            await query.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await query.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
    elif query.data == "help":
        await query.answer()
        await send_help(update, context)
    elif query.data == "main_menu":
        await query.answer()
        text = (
            "👋 Привет! Я бот <b>ИИ Запятые</b> для автоматической расстановки знаков препинания и заглавных букв.\n\n"
            "Выберите действие ниже:"
        )
        if query.message:
            is_admin = bool(query.from_user and query.from_user.id == admin.ADMIN_ID)
            try:
                await query.message.edit_text(
                    text,
                    reply_markup=_main_menu_keyboard(is_admin=is_admin),
                    parse_mode="HTML",
                )
            except Exception:
                await query.message.reply_text(
                    text,
                    reply_markup=_main_menu_keyboard(is_admin=is_admin),
                    parse_mode="HTML",
                )
    else:
        await query.answer()


async def on_business_messages_deleted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del_obj = update.deleted_business_messages
    if not del_obj:
        return

    conn_id = del_obj.business_connection_id
    chat_id = del_obj.chat.id
    chat_name = del_obj.chat.title or getattr(del_obj.chat, "effective_name", None) or str(chat_id)

    owner_id = context.bot_data.get(f"owner:{conn_id}") or storage.get_connection_owner(conn_id)
    if not owner_id:
        owner_id = admin.ADMIN_ID
    if not owner_id:
        return

    st = storage.get_user_settings(owner_id)
    if not st.get("anti_delete", 0):
        return

    for msg_id in del_obj.message_ids:
        msg_data = storage.get_message(conn_id, chat_id, msg_id)
        if not msg_data:
            continue

        is_owner = msg_data.get("is_owner") == 1
        author = msg_data.get("from_user_name") or ("Вы" if is_owner else "Собеседник")
        title = msg_data.get("chat_title") or chat_name
        dt_str = ""
        if msg_data.get("date"):
            dt_str = datetime.fromtimestamp(msg_data["date"], tz=timezone.utc).astimezone().strftime("%d.%m %H:%M")

        is_owner = msg_data.get("is_owner") == 1
        author = msg_data.get("from_user_name") or ("Вы" if is_owner else "Собеседник")
        title = msg_data.get("chat_title") or chat_name
        dt_str = ""
        msg_date = msg_data.get("date") or 0
        if msg_date:
            dt_str = datetime.fromtimestamp(msg_date, tz=timezone.utc).astimezone().strftime("%d.%m %H:%M")

        now_ts = int(datetime.now(timezone.utc).timestamp())
        lifetime = max(0, now_ts - msg_date) if msg_date else 9999
        media_type = msg_data.get("media_type")
        file_id = msg_data.get("file_id")
        text_content = (msg_data.get("text") or "").strip()

        # Проверяем наличие пред-загруженного медиафайла в локальном кэше View-Once
        cached_bytes, cached_ext = view_once.get_cached_media_bytes(conn_id, chat_id, msg_id)
        is_view_once = (not is_owner) and view_once.is_likely_view_once(lifetime) and (media_type in ("photo", "video", "video_note", "voice"))

        if is_view_once:
            status_line = (
                f"🔥 <b>ПЕРЕХВАЧЕНО СГОРАЮЩЕЕ МЕДИА (VIEW-ONCE)!</b>\n"
                f"⏳ <i>Самоуничтожилось или удалено через {lifetime} сек.</i>"
            )
        elif not is_owner:
            status_line = "🗑 <b>УДАЛЕНО СООБЩЕНИЕ СОБЕСЕДНИКА!</b>"
        else:
            status_line = "🗑 <b>Удалено сообщение:</b>"

        esc_author = html.escape(author)
        esc_title = html.escape(title)
        esc_text = html.escape(text_content)

        header = (
            f"{status_line}\n"
            f"👤 <b>От кого:</b> {esc_author}\n"
            f"💬 <b>Чат:</b> {esc_title}\n"
            f"⏰ <b>Отправлено:</b> {dt_str}\n"
        )

        kb_notice = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚙️ Настройки бота", callback_data="settings")],
        ])

        try:
            if media_type == "video_note":
                # Видеокружок
                await context.bot.send_message(chat_id=owner_id, text=header + "\n⭕ <b>Спасённый видеокружок:</b>", parse_mode="HTML", reply_markup=kb_notice)
                if cached_bytes:
                    await context.bot.send_video_note(chat_id=owner_id, video_note=bytes(cached_bytes))
                elif file_id:
                    try:
                        await context.bot.send_video_note(chat_id=owner_id, video_note=file_id)
                    except Exception:
                        f = await context.bot.get_file(file_id)
                        buf = await f.download_as_bytearray()
                        await context.bot.send_video_note(chat_id=owner_id, video_note=bytes(buf))

            elif media_type == "voice":
                # Голосовое сообщение
                cap = (header + "\n🎤 <b>Спасённое голосовое:</b>")[:1024]
                if cached_bytes:
                    await context.bot.send_voice(chat_id=owner_id, voice=bytes(cached_bytes), caption=cap, parse_mode="HTML", reply_markup=kb_notice)
                elif file_id:
                    try:
                        await context.bot.send_voice(chat_id=owner_id, voice=file_id, caption=cap, parse_mode="HTML", reply_markup=kb_notice)
                    except Exception:
                        f = await context.bot.get_file(file_id)
                        buf = await f.download_as_bytearray()
                        await context.bot.send_voice(chat_id=owner_id, voice=bytes(buf), caption=cap, parse_mode="HTML", reply_markup=kb_notice)

            elif media_type == "photo":
                # Фото
                cap = header + (f"\n📝 <i>«{esc_text}»</i>" if esc_text else "")
                if cached_bytes:
                    await context.bot.send_photo(chat_id=owner_id, photo=bytes(cached_bytes), caption=cap[:1024], parse_mode="HTML", reply_markup=kb_notice)
                elif file_id:
                    try:
                        await context.bot.send_photo(chat_id=owner_id, photo=file_id, caption=cap[:1024], parse_mode="HTML", reply_markup=kb_notice)
                    except Exception:
                        f = await context.bot.get_file(file_id)
                        buf = await f.download_as_bytearray()
                        await context.bot.send_photo(chat_id=owner_id, photo=bytes(buf), caption=cap[:1024], parse_mode="HTML", reply_markup=kb_notice)

            elif media_type == "video":
                # Видео
                cap = header + (f"\n📝 <i>«{esc_text}»</i>" if esc_text else "")
                if cached_bytes:
                    await context.bot.send_video(chat_id=owner_id, video=bytes(cached_bytes), caption=cap[:1024], parse_mode="HTML", reply_markup=kb_notice)
                elif file_id:
                    try:
                        await context.bot.send_video(chat_id=owner_id, video=file_id, caption=cap[:1024], parse_mode="HTML", reply_markup=kb_notice)
                    except Exception:
                        f = await context.bot.get_file(file_id)
                        buf = await f.download_as_bytearray()
                        await context.bot.send_video(chat_id=owner_id, video=bytes(buf), caption=cap[:1024], parse_mode="HTML", reply_markup=kb_notice)

            elif media_type == "document":
                # Файл / документ
                cap = header + (f"\n📝 <i>«{esc_text}»</i>" if esc_text else "")
                if cached_bytes:
                    await context.bot.send_document(chat_id=owner_id, document=bytes(cached_bytes), caption=cap[:1024], parse_mode="HTML", reply_markup=kb_notice)
                elif file_id:
                    try:
                        await context.bot.send_document(chat_id=owner_id, document=file_id, caption=cap[:1024], parse_mode="HTML", reply_markup=kb_notice)
                    except Exception:
                        f = await context.bot.get_file(file_id)
                        buf = await f.download_as_bytearray()
                        await context.bot.send_document(chat_id=owner_id, document=bytes(buf), caption=cap[:1024], parse_mode="HTML", reply_markup=kb_notice)

            elif media_type == "audio" and file_id:
                # Аудиозапись
                cap = header + (f"\n📝 <i>«{esc_text}»</i>" if esc_text else "")
                try:
                    await context.bot.send_audio(chat_id=owner_id, audio=file_id, caption=cap[:1024], parse_mode="HTML", reply_markup=kb_notice)
                except Exception:
                    f = await context.bot.get_file(file_id)
                    buf = await f.download_as_bytearray()
                    await context.bot.send_audio(chat_id=owner_id, audio=bytes(buf), caption=cap[:1024], parse_mode="HTML", reply_markup=kb_notice)

            else:
                # Обычный текст
                full_text = header + f"\n💬 <b>Текст:</b>\n<i>«{esc_text}»</i>"
                await context.bot.send_message(chat_id=owner_id, text=full_text, reply_markup=kb_notice, parse_mode="HTML")

        except Exception as e:
            logger.exception("Ошибка при отправке удалённого сообщения владельцу %s: %s", owner_id, e)


async def on_business_message_edited(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.edited_business_message
    if msg is None:
        return

    conn_id = msg.business_connection_id
    owner_id = context.bot_data.get(f"owner:{conn_id}") or storage.get_connection_owner(conn_id)
    if not owner_id:
        owner_id = admin.ADMIN_ID
    if not owner_id:
        return

    old_data = storage.get_message(conn_id, msg.chat_id, msg.message_id)
    new_text = msg.text or msg.caption or ""
    old_text = (old_data.get("text") or "") if old_data else ""

    # Всегда логируем обновлённый текст в базу данных (сбор данных работает непрерывно)
    storage.update_message_text(conn_id, msg.chat_id, msg.message_id, new_text)

    st = storage.get_user_settings(owner_id)
    if not st.get("anti_delete", 0):
        return

    from_user = msg.from_user
    is_owner = bool(from_user and from_user.id == owner_id)
    if not is_owner and old_text and new_text and old_text != new_text:
        author = from_user.full_name or ("@" + from_user.username if from_user.username else str(from_user.id))
        chat_title = msg.chat.title or getattr(msg.chat, "effective_name", None) or str(msg.chat_id)
        esc_author = html.escape(author)
        esc_chat_title = html.escape(chat_title)
        esc_old = html.escape(old_text)
        esc_new = html.escape(new_text)
        notice = (
            f"✏️ <b>СОБЕСЕДНИК ИЗМЕНИЛ СООБЩЕНИЕ!</b>\n"
            f"👤 <b>От кого:</b> {esc_author}\n"
            f"💬 <b>Чат:</b> {esc_chat_title}\n\n"
            f"❌ <b>Было:</b>\n<i>«{esc_old}»</i>\n\n"
            f"✅ <b>Стало:</b>\n<i>«{esc_new}»</i>"
        )
        kb_edit = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚙️ Настройки бота", callback_data="settings")],
        ])
        try:
            await context.bot.send_message(chat_id=owner_id, text=notice, reply_markup=kb_edit, parse_mode="HTML")
        except Exception as e:
            logger.warning("Не удалось уведомить об изменении сообщения: %s", e)


async def _dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.business_connection is not None:
        await on_business_connection(update, context)
    if update.business_message is not None:
        await on_business_message(update, context)
    if update.deleted_business_messages is not None:
        await on_business_messages_deleted(update, context)
    if update.edited_business_message is not None:
        await on_business_message_edited(update, context)
    if update.message_reaction is not None:
        await on_message_reaction(update, context)


def main():
    storage.init_db()

    async def start_web_server():
        port_str = os.getenv("PORT")
        if not port_str:
            return
        try:
            from aiohttp import web

            async def handle_ping(request):
                return web.json_response({
                    "status": "ok",
                    "bot": "tg-punct-bot",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

            web_app = web.Application()
            web_app.router.add_get("/", handle_ping)
            web_app.router.add_get("/health", handle_ping)

            runner = web.AppRunner(web_app)
            await runner.setup()
            site = web.TCPSite(runner, "0.0.0.0", int(port_str))
            await site.start()
            logger.info("Render keep-alive web-сервер успешно запущен на порту %s", port_str)
        except Exception as e:
            logger.warning("Не удалось запустить keep-alive web-сервер: %s", e)

    async def post_init(application: Application):
        asyncio.create_task(start_web_server())
        asyncio.create_task(reminders_worker(application))
        loop = asyncio.get_running_loop()
        # Фоновый прогрев ИИ-модели для мгновенного первого отклика
        loop.run_in_executor(None, punct.warmup)
        # Очистка устаревших медиафайлов
        loop.run_in_executor(None, view_once.cleanup_cache)

    builder = Application.builder().token(BOT_TOKEN).post_init(post_init)
    if PROXY_URL:
        builder = builder.proxy(PROXY_URL).get_updates_proxy(PROXY_URL)
        logger.info("Использую прокси: %s", PROXY_URL)
    app = builder.build()

    # Команды
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("reminders", reminders_command))
    app.add_handler(CommandHandler("starred", starred_command))
    app.add_handler(CommandHandler("favorites", starred_command))
    app.add_handler(CommandHandler("features", features_command))
    app.add_handler(CommandHandler("functions", features_command))
    app.add_handler(CommandHandler("help", send_help))
    app.add_handler(CommandHandler("admin", admin.admin_command))

    # Колбэки: сначала специфичные, затем общее меню
    app.add_handler(CallbackQueryHandler(admin.admin_callback, pattern=r"^adm:"))
    app.add_handler(CallbackQueryHandler(on_settings_callback, pattern=r"^set:"))
    app.add_handler(CallbackQueryHandler(on_reminder_callback, pattern=r"^rem:cancel:"))
    app.add_handler(CallbackQueryHandler(on_starred_callback, pattern=r"^star:"))
    app.add_handler(CallbackQueryHandler(on_callback_query))

    # Обработчик реакций ⭐ на сообщения
    app.add_handler(MessageReactionHandler(on_message_reaction))

    # Личные сообщения боту (для интерактивного поиска)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, on_private_message))

    # business_connection и business_message
    app.add_handler(TypeHandler(Update, _dispatch))

    logger.info("Бот запущен. Подключи его через Настройки -> Telegram Business -> "
                "Автоматизация чатов в приложении Telegram.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    main()
