"""Админ-меню: выбор чата, периода и выгрузка переписки архивом."""
import os
import html
import shutil
import tempfile
import zipfile
import asyncio
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction

import storage
import render

ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)

PERIODS = [
    ("1 день", 1),
    ("3 дня", 3),
    ("7 дней", 7),
    ("10 дней", 10),
    ("30 дней", 30),
    ("Вся переписка ♾", 0),
]

MAX_CHAT_BUTTONS = 90


def _is_admin(update) -> bool:
    u = update.effective_user
    return bool(u) and u.id == ADMIN_ID


def _sanitize(name: str) -> str:
    safe = "".join(ch for ch in (name or "") if ch.isalnum() or ch in " -_@.")
    return safe.strip() or "chat"


def _short(s: str, n: int = 50) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."


def admin_menu():
    conns = storage.get_connected_users()
    started = storage.get_started_users()
    chats = storage.list_chats()

    kb = [
        [InlineKeyboardButton(f"👥 Пользователи ({len(conns)} подкл. / {len(started)} старт)", callback_data="adm:users")],
        [InlineKeyboardButton("⌨️ Ввести ID или @юз чата", callback_data="adm:askchat:0")],
    ]
    if chats:
        kb.append([InlineKeyboardButton(f"💬 Все диалоги ({len(chats)})", callback_data="adm:allchats")])
    kb.append([InlineKeyboardButton("❌ Закрыть", callback_data="adm:close")])

    text = (
        "🗂 <b>Панель администратора</b>\n\n"
        f"• 🔌 Подключивших бота: <b>{len(conns)}</b>\n"
        f"• 🚀 Нажавших старт: <b>{len(started)}</b>\n"
        f"• 💬 Сохранённых диалогов: <b>{len(chats)}</b>\n\n"
        "Выберите раздел:"
    )
    return InlineKeyboardMarkup(kb), text


async def admin_command(update, context):
    if not _is_admin(update):
        return
    kb, text = admin_menu()
    if update.message is None:
        return
    if kb is None:
        await update.message.reply_text(text, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def admin_callback(update, context):
    q = update.callback_query
    if not _is_admin(update):
        await q.answer("Не для тебя.", show_alert=True)
        return

    parts = (q.data or "").split(":")
    if not parts or parts[0] != "adm":
        return

    if q.message is None:
        await q.answer()
        return

    action = parts[1]

    if action == "close":
        try:
            await q.message.delete()
        except Exception:
            pass
        await q.answer()
        return

    if action == "noop":
        await q.answer()
        return

    if action == "menu":
        kb, text = admin_menu()
        if kb is None:
            await q.edit_message_text(text, parse_mode="HTML")
        else:
            await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        await q.answer()
        return

    # Раздел "Пользователи"
    if action == "users":
        if len(parts) > 2 and parts[2] == "starts":
            started = storage.get_started_users()
            if not started:
                text = "🚀 <b>Пока никто не нажимал /start в боте.</b>"
            else:
                text = f"🚀 <b>Пользователи, нажавшие старт ({len(started)}):</b>\n\n"
                for i, u in enumerate(started[:40], 1):
                    u_name = html.escape(u.get("user_name") or f"Пользователь {u['user_id']}")
                    u_user = f" (@{u['username']})" if u.get("username") else ""
                    dt_start = datetime.fromtimestamp(u["last_start"], tz=timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M") if u.get("last_start") else "—"
                    text += f"{i}. <b>{u_name}</b>{u_user}\n   🆔 <code>{u['user_id']}</code> | 🕒 Старт: {dt_start}\n\n"
                if len(started) > 40:
                    text += f"<i>...и ещё {len(started) - 40} пользователей</i>\n"

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Обновить", callback_data="adm:users:starts")],
                [InlineKeyboardButton("« К пользователям", callback_data="adm:users")],
                [InlineKeyboardButton("« В меню админа", callback_data="adm:menu")],
            ])
            await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
            await q.answer()
            return

        if len(parts) > 2 and parts[2] == "conns":
            conns = storage.get_connected_users()
            if not conns:
                text = "🔌 <b>Нет пользователей, подключивших бота в Telegram Business.</b>"
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("« К пользователям", callback_data="adm:users")],
                    [InlineKeyboardButton("« В меню админа", callback_data="adm:menu")],
                ])
            else:
                text = (
                    f"🔌 <b>Пользователи, подключившие бота ({len(conns)}):</b>\n\n"
                    "Нажмите на кнопку с пользователем (<code>@username | ID</code>), "
                    "чтобы открыть список всех его чатов и выгрузить переписку:"
                )
                kb_rows = []
                for u in conns:
                    handle_label = f"@{u['user_username']}" if u.get("user_username") else (_short(u.get("user_name") or "User", 14))
                    btn_label = f"{handle_label} | ID: {u['user_id']}"
                    kb_rows.append([InlineKeyboardButton(btn_label, callback_data=f"adm:userconns:{u['user_id']}")])

                kb_rows.append([InlineKeyboardButton("« К пользователям", callback_data="adm:users")])
                kb_rows.append([InlineKeyboardButton("« В меню админа", callback_data="adm:menu")])
                kb = InlineKeyboardMarkup(kb_rows)

            await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
            await q.answer()
            return

        # Главный экран раздела "Пользователи"
        conns = storage.get_connected_users()
        started = storage.get_started_users()
        text = (
            "👥 <b>Раздел «Пользователи»</b>\n\n"
            "Выберите интересующую подкатегорию:\n\n"
            f"• 🔌 <b>Подключившие бота:</b> {len(conns)} чел. (пользователи Telegram Business с диалогами)\n"
            f"• 🚀 <b>Нажавшие старт:</b> {len(started)} чел. (все, кто когда-либо запускал бота)"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🔌 Подключившие бота ({len(conns)})", callback_data="adm:users:conns")],
            [InlineKeyboardButton(f"🚀 Нажавшие старт ({len(started)})", callback_data="adm:users:starts")],
            [InlineKeyboardButton("« Назад в главное меню", callback_data="adm:menu")],
        ])
        await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        await q.answer()
        return

    # Карточка конкретного подключившего пользователя и список его чатов
    if action == "userconns":
        owner_id = int(parts[2])
        u_info = storage.get_user_info_for_admin(owner_id)
        if not u_info:
            await q.answer("Пользователь не найден.", show_alert=True)
            return

        chats = u_info["chats"]
        u_name = html.escape(u_info["user_name"])
        u_handle = f" (@{u_info['username']})" if u_info.get("username") else ""
        st_icon = "🟢 Подключён" if u_info.get("is_enabled", 1) else "🔴 Отключён"
        dt_str = datetime.fromtimestamp(u_info["date"], tz=timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M") if u_info.get("date") else "—"

        text = (
            f"👤 <b>Пользователь:</b> <b>{u_name}</b>{u_handle}\n"
            f"🆔 <b>ID:</b> <code>{u_info['user_id']}</code>\n"
            f"⚡ <b>Статус:</b> {st_icon}\n"
            f"📅 <b>Дата подключения:</b> {dt_str}\n"
            f"💬 <b>Доступно диалогов:</b> <b>{u_info['chats_count']}</b> | <b>Сообщений:</b> <b>{u_info['msgs_count']}</b>\n\n"
        )

        kb_rows = []
        # Кнопка ручного ввода ID чата или юзернейма
        kb_rows.append([InlineKeyboardButton("⌨️ Ввести ID или @юз чата", callback_data=f"adm:askchat:{owner_id}")])

        if not chats:
            text += "<i>В базе пока нет сообщений из диалогов этого пользователя. Чаты появятся здесь автоматически при активности.</i>"
        else:
            text += "<b>Список всех доступных чатов (нажмите для выгрузки):</b>"
            for ch in chats[:MAX_CHAT_BUTTONS]:
                title_lbl = _short(ch["chat_title"] or str(ch["chat_id"]), 22)
                btn_lbl = f"💬 {title_lbl} ({ch['msg_count']} сообщ.)"
                kb_rows.append([InlineKeyboardButton(btn_lbl, callback_data=f"adm:chat:{ch['chat_id']}:{ch['business_connection_id']}")])

        kb_rows.append([InlineKeyboardButton("« К подключившим бота", callback_data="adm:users:conns")])
        kb_rows.append([InlineKeyboardButton("« В меню админа", callback_data="adm:menu")])
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode="HTML")
        await q.answer()
        return

    # Запрос ввода ID или @юзернейма чата
    if action == "askchat":
        owner_id = int(parts[2]) if len(parts) > 2 else 0
        context.user_data["awaiting_admin_chat_query"] = owner_id or True
        text = (
            "⌨️ <b>Поиск диалога для выгрузки переписки</b>\n\n"
            "Отправьте мне <b>ID чата</b> (например: <code>-100123456789</code> или <code>123456789</code>)\n"
            "либо <b>@username / название диалога</b>:\n\n"
            "<i>Бот найдёт чат и сразу предложит кнопки выбора периода выгрузки.</i>"
        )
        back_cb = f"adm:userconns:{owner_id}" if owner_id else "adm:menu"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("« Отмена", callback_data=back_cb)]
        ])
        await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        await q.answer()
        return

    # Список всех диалогов
    if action == "allchats":
        chats = storage.list_chats()
        if not chats:
            text = "💬 <b>Пока нет сохранённых диалогов.</b>"
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("« Назад в меню", callback_data="adm:menu")]])
        else:
            text = f"💬 <b>Все сохранённые диалоги ({len(chats)}):</b>\n\nВыберите диалог для выгрузки переписки:"
            kb_rows = [
                [InlineKeyboardButton("⌨️ Ввести ID или @юз чата", callback_data="adm:askchat:0")]
            ]
            for ch in chats[:MAX_CHAT_BUTTONS]:
                title = ch["chat_title"] or str(ch["chat_id"])
                label = f"{_short(title, 26)} ({ch['cnt']} сообщ.)"
                cb = f"adm:chat:{ch['chat_id']}:{ch['business_connection_id']}"
                kb_rows.append([InlineKeyboardButton(label, callback_data=cb)])
            kb_rows.append([InlineKeyboardButton("« Назад в меню", callback_data="adm:menu")])
            kb = InlineKeyboardMarkup(kb_rows)
        await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        await q.answer()
        return

    # Совместимость с adm:conns и adm:connchats
    if action == "conns":
        conns = storage.get_connected_users()
        if not conns:
            text = "🔌 <b>Нет зарегистрированных бизнес-подключений.</b>"
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("« Назад в меню", callback_data="adm:menu")]])
        else:
            text = "🔌 <b>Пользователи Telegram Business:</b>\n\n"
            kb_rows = []
            for i, c_item in enumerate(conns, 1):
                name = c_item.get("user_name") or f"Пользователь {c_item['user_id']}"
                handle = f" (@{c_item['user_username']})" if c_item.get("user_username") else ""
                status_icon = "🟢" if c_item.get("is_enabled", 1) else "🔴"
                dt_str = datetime.fromtimestamp(c_item["date"], tz=timezone.utc).astimezone().strftime("%d.%m.%Y") if c_item.get("date") else ""
                text += (
                    f"{i}. {status_icon} <b>{html.escape(name)}</b>{html.escape(handle)}\n"
                    f"   🆔 <code>{c_item['user_id']}</code> | 💬 Диалогов: <b>{c_item.get('chats_count', 0)}</b>\n"
                    f"   📅 Дата: {dt_str}\n\n"
                )
                btn_title = _short(name, 22)
                kb_rows.append([InlineKeyboardButton(f"💬 Диалоги «{btn_title}»", callback_data=f"adm:userconns:{c_item['user_id']}")])
            kb_rows.append([InlineKeyboardButton("« Назад в меню", callback_data="adm:menu")])
            kb = InlineKeyboardMarkup(kb_rows)
        await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        await q.answer()
        return

    if action == "connchats":
        conn_id = parts[2]
        conn_info = storage.get_connection_info(conn_id)
        chats = storage.get_chats_for_connection(conn_id)
        owner_name = conn_info.get("user_name") if conn_info else conn_id
        if not chats:
            text = (
                f"💬 <b>Подключение: {html.escape(owner_name)}</b>\n\n"
                f"В базе бота пока нет зафиксированных сообщений по этому подключению.\n"
                f"Диалоги появятся здесь автоматически при активности."
            )
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("« К подключениям", callback_data="adm:users:conns")]])
        else:
            text = f"💬 <b>Доступные диалоги пользователя {html.escape(owner_name)} ({len(chats)}):</b>\n\n"
            kb_rows = []
            for i, ch in enumerate(chats, 1):
                dt_str = datetime.fromtimestamp(ch["last_date"], tz=timezone.utc).astimezone().strftime("%d.%m %H:%M") if ch.get("last_date") else ""
                text += f"{i}. <b>{html.escape(ch['chat_title'])}</b>\n   ID: <code>{ch['chat_id']}</code> | Сообщений: {ch['msg_count']} | Акт: {dt_str}\n\n"
                kb_rows.append([InlineKeyboardButton(f"📥 Скачать «{_short(ch['chat_title'], 18)}»", callback_data=f"adm:chat:{ch['chat_id']}:{conn_id}")])
            kb_rows.append([InlineKeyboardButton("« К подключениям", callback_data="adm:users:conns")])
            kb = InlineKeyboardMarkup(kb_rows)
        await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        await q.answer()
        return

    if action == "chat":
        chat_id = int(parts[2])
        conn_id = parts[3] if len(parts) > 3 else ""
        owner_id = storage.get_connection_owner(conn_id) if conn_id else 0

        # Получаем данные о чате
        msg_sample = storage.get_messages(conn_id, chat_id, 0, int(datetime.now(timezone.utc).timestamp()) + 1)
        cnt = len(msg_sample)
        title = next((r["chat_title"] for r in msg_sample if r.get("chat_title")), str(chat_id))

        text = (
            f"📥 <b>Выгрузка переписки:</b> «<b>{html.escape(title)}</b>»\n\n"
            f"🆔 <b>ID чата:</b> <code>{chat_id}</code>\n"
            f"💬 <b>Сообщений в базе:</b> {cnt}\n\n"
            f"За какой период выгрузить переписку архивом?"
        )
        kb = [[InlineKeyboardButton(lbl, callback_data=f"adm:run:{chat_id}:{days}:{conn_id}")]
              for lbl, days in PERIODS]
        back_cb = f"adm:userconns:{owner_id}" if owner_id else "adm:menu"
        kb.append([InlineKeyboardButton("⬅️ Назад", callback_data=back_cb)])
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
        await q.answer()
        return

    if action == "run":
        chat_id = int(parts[2])
        days = int(parts[3])
        conn_id = parts[4] if len(parts) > 4 else ""
        period_name = f"{days} дн." if days > 0 else "Вся переписка"
        await q.answer("Готовлю выгрузку…")
        await q.edit_message_text(f"Собираю «{period_name}» — может занять минуту…")
        await _export(context, q.message.chat_id, conn_id, chat_id, days)
        return

    await q.answer()


async def _export(context, target_chat_id, conn_id, chat_id, days):
    bot = context.bot
    until = int(datetime.now(timezone.utc).timestamp()) + 1
    since = until - days * 86400 if days > 0 else 0
    period_label = f"{days} дн." if days > 0 else "Всё время"

    rows = storage.get_messages(conn_id, chat_id, since, until)
    if not rows:
        await bot.send_message(target_chat_id, f"За период «{period_label}» сообщений нет.")
        return

    chat_title = next((r["chat_title"] for r in rows if r["chat_title"]),
                      str(chat_id))

    bubbles, media_jobs = [], []
    for r in rows:
        dt = datetime.fromtimestamp(r["date"], tz=timezone.utc).astimezone()
        time_str = dt.strftime("%d.%m %H:%M")
        name = r["from_user_name"] or ("Я" if r["is_owner"] == 1 else "Собеседник")

        mt = r["media_type"]
        media_label = {
            "voice": "🎤 Голосовое",
            "video_note": "⭕ Видеокружок",
            "photo": "🖼 Фото",
            "video": "🎬 Видео",
            "document": "📎 Файл",
            "audio": "🎵 Аудио",
        }.get(mt)

        bubbles.append(render.Bubble(name, r["text"] or "", time_str,
                                     r["is_owner"] == 1, media_label))

        if r["file_id"] and mt in ("voice", "video_note"):
            ext = "ogg" if mt == "voice" else "mp4"
            fn = f"{dt.strftime('%Y%m%d_%H%M%S')}_{r['message_id']}.{ext}"
            media_jobs.append((fn, r["file_id"], mt))

    await bot.send_chat_action(target_chat_id, ChatAction.UPLOAD_DOCUMENT)

    loop = asyncio.get_running_loop()
    pages = await loop.run_in_executor(
        None, render.render_pages, bubbles, chat_title
    )

    tmp = tempfile.mkdtemp(prefix="export_")
    try:
        for i, img in enumerate(pages, 1):
            img.save(os.path.join(tmp, f"page_{i:03}.png"))

        for fn, file_id, _kind in media_jobs:
            try:
                tg_file = await bot.get_file(file_id)
                await tg_file.download_to_drive(os.path.join(tmp, fn))
            except Exception as e:
                with open(os.path.join(tmp, "errors.txt"), "a", encoding="utf-8") as f:
                    f.write(f"{fn}: {e}\n")

        zip_suffix = f"{days}d" if days > 0 else "all"
        zip_name = f"export_{_sanitize(chat_title)}_{zip_suffix}.zip"
        zip_path = os.path.join(tmp, zip_name)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for nm in sorted(os.listdir(tmp)):
                full = os.path.join(tmp, nm)
                if full == zip_path or not os.path.isfile(full):
                    continue
                z.write(full, nm)

        size_mb = os.path.getsize(zip_path) / 1024 / 1024
        caption = (f"«{chat_title}» • {period_label} • "
                   f"{len(rows)} сообщ. • {len(pages)} стр. • {size_mb:.1f} МБ")

        if size_mb > 49:
            await bot.send_message(
                target_chat_id,
                f"⚠️ Архив {size_mb:.1f} МБ — больше лимита 50 МБ.\n"
                "Отправляю только PNG-страницы."
            )
            for i, img in enumerate(pages, 1):
                with open(os.path.join(tmp, f"page_{i:03}.png"), "rb") as f:
                    await bot.send_photo(target_chat_id, f,
                                         caption=f"стр. {i}/{len(pages)}")
        else:
            with open(zip_path, "rb") as f:
                await bot.send_document(target_chat_id, f,
                                        filename=zip_name, caption=caption)

        if media_jobs:
            await bot.send_message(
                target_chat_id,
                f"⬇️ Отправляю вложения отдельно: {len(media_jobs)} шт."
            )
        for fn, file_id, kind in media_jobs:
            try:
                if kind == "voice":
                    await bot.send_voice(target_chat_id, file_id, caption=fn)
                else:
                    await bot.send_video_note(target_chat_id, file_id)
            except Exception:
                try:
                    await bot.send_document(target_chat_id, file_id, filename=fn)
                except Exception as e:
                    await bot.send_message(target_chat_id, f"⚠️ {fn}: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
