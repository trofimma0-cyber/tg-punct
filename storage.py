"""Хранилище всех business-сообщений, настроек пользователей и статистики в SQLite."""
import os
import sqlite3
from datetime import datetime, timezone
from threading import Lock

DB_PATH = os.getenv("DB_PATH", "messages.db")
_lock = Lock()


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA cache_size = -32000;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    return conn


def init_db():
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                business_connection_id TEXT,
                chat_id                INTEGER,
                chat_title             TEXT,
                message_id             INTEGER,
                from_user_id           INTEGER,
                from_user_name         TEXT,
                is_owner               INTEGER,
                text                   TEXT,
                media_type             TEXT,
                file_id                TEXT,
                date                   INTEGER,
                UNIQUE(business_connection_id, chat_id, message_id)
            );
            CREATE INDEX IF NOT EXISTS idx_messages_chat_date
                ON messages(business_connection_id, chat_id, date);
            CREATE INDEX IF NOT EXISTS idx_messages_conn_date
                ON messages(business_connection_id, date);
            CREATE INDEX IF NOT EXISTS idx_messages_user_owner
                ON messages(from_user_id, is_owner);

            CREATE TABLE IF NOT EXISTS user_settings (
                user_id     INTEGER PRIMARY KEY,
                enabled     INTEGER DEFAULT 1,
                punct       INTEGER DEFAULT 1,
                caps        INTEGER DEFAULT 1,
                typography  INTEGER DEFAULT 1,
                yo          INTEGER DEFAULT 0,
                stats       INTEGER DEFAULT 1,
                anti_delete INTEGER DEFAULT 1,
                layout_fix      INTEGER DEFAULT 1,
                reminders       INTEGER DEFAULT 1,
                star_save       INTEGER DEFAULT 1,
                view_once_saver INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS stats_edits (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER,
                original_len INTEGER,
                fixed_len    INTEGER,
                commas_added INTEGER,
                dots_added   INTEGER,
                date         INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_stats_user
                ON stats_edits(user_id);

            CREATE TABLE IF NOT EXISTS business_connections (
                connection_id TEXT PRIMARY KEY,
                owner_user_id INTEGER,
                date          INTEGER
            );

            CREATE TABLE IF NOT EXISTS reminders (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id                INTEGER,
                chat_id                INTEGER,
                chat_title             TEXT,
                business_connection_id TEXT,
                title                  TEXT,
                target_time            INTEGER,
                remind_1h_sent         INTEGER DEFAULT 0,
                remind_30m_sent        INTEGER DEFAULT 0,
                status                 TEXT DEFAULT 'active',
                created_at             INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_reminders_status_time
                ON reminders(status, target_time);
            CREATE INDEX IF NOT EXISTS idx_reminders_user_status
                ON reminders(user_id, status);

            CREATE TABLE IF NOT EXISTS starred_messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER,
                chat_id     INTEGER,
                chat_title  TEXT,
                message_id  INTEGER,
                author_name TEXT,
                text        TEXT,
                media_type  TEXT,
                file_id     TEXT,
                date        INTEGER,
                created_at  INTEGER,
                UNIQUE(user_id, chat_id, message_id)
            );
            CREATE INDEX IF NOT EXISTS idx_starred_user
                ON starred_messages(user_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS media_cache (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                business_connection_id TEXT,
                chat_id                INTEGER,
                message_id             INTEGER,
                file_path              TEXT,
                created_at             INTEGER,
                UNIQUE(business_connection_id, chat_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS bot_users (
                user_id     INTEGER PRIMARY KEY,
                user_name   TEXT,
                username    TEXT,
                first_start INTEGER,
                last_start  INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_bot_users_last_start
                ON bot_users(last_start DESC);
            """
        )
        for col in ("anti_delete", "layout_fix", "reminders", "star_save", "view_once_saver"):
            try:
                c.execute(f"ALTER TABLE user_settings ADD COLUMN {col} INTEGER DEFAULT 1")
            except Exception:
                pass

        for col, col_type in (("user_name", "TEXT"), ("user_username", "TEXT"), ("is_enabled", "INTEGER DEFAULT 1")):
            try:
                c.execute(f"ALTER TABLE business_connections ADD COLUMN {col} {col_type}")
            except Exception:
                pass

        try:
            c.execute(
                """
                INSERT OR IGNORE INTO bot_users (user_id, user_name, username, first_start, last_start)
                SELECT owner_user_id, user_name, user_username, date, date
                FROM business_connections
                WHERE owner_user_id IS NOT NULL AND owner_user_id != 0
                """
            )
        except Exception:
            pass
        try:
            c.execute(
                """
                UPDATE user_settings
                SET enabled = 1, punct = 1, caps = 1, typography = 1, layout_fix = 1,
                    anti_delete = 1, view_once_saver = 1, star_save = 1, reminders = 1, stats = 1
                WHERE enabled = 0 AND punct = 0 AND caps = 0 AND typography = 0
                """
            )
        except Exception:
            pass


DEFAULT_SETTINGS = {
    "enabled": 1,
    "punct": 1,
    "caps": 1,
    "typography": 1,
    "yo": 0,
    "stats": 1,
    "anti_delete": 1,
    "layout_fix": 1,
    "reminders": 1,
    "star_save": 1,
    "view_once_saver": 1,
}


def get_user_settings(user_id: int) -> dict:
    if not user_id:
        return dict(DEFAULT_SETTINGS)
    with _conn() as c:
        row = c.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return dict(DEFAULT_SETTINGS)
        res = dict(DEFAULT_SETTINGS)
        for k in DEFAULT_SETTINGS:
            if k in row.keys() and row[k] is not None:
                res[k] = row[k]
        return res


def toggle_user_setting(user_id: int, key: str) -> dict:
    if not user_id or key not in DEFAULT_SETTINGS:
        return get_user_settings(user_id)
    cur = get_user_settings(user_id)
    new_val = 0 if cur.get(key, 0) else 1
    cur[key] = new_val
    with _lock, _conn() as c:
        c.execute(
            """
            INSERT INTO user_settings (user_id, enabled, punct, caps, typography, yo, stats, anti_delete, layout_fix, reminders, star_save, view_once_saver)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                enabled = excluded.enabled,
                punct = excluded.punct,
                caps = excluded.caps,
                typography = excluded.typography,
                yo = excluded.yo,
                stats = excluded.stats,
                anti_delete = excluded.anti_delete,
                layout_fix = excluded.layout_fix,
                reminders = excluded.reminders,
                star_save = excluded.star_save,
                view_once_saver = excluded.view_once_saver
            """,
            (
                user_id,
                cur["enabled"],
                cur["punct"],
                cur["caps"],
                cur["typography"],
                cur["yo"],
                cur["stats"],
                cur["anti_delete"],
                cur["layout_fix"],
                cur["reminders"],
                cur["star_save"],
                cur["view_once_saver"],
            ),
        )
    return cur


def get_message(business_connection_id: str, chat_id: int, message_id: int):
    with _conn() as c:
        row = c.execute(
            """
            SELECT * FROM messages
            WHERE business_connection_id = ? AND chat_id = ? AND message_id = ?
            """,
            (business_connection_id, chat_id, message_id),
        ).fetchone()
        return dict(row) if row else None


def update_message_text(business_connection_id: str, chat_id: int, message_id: int, new_text: str):
    with _lock, _conn() as c:
        c.execute(
            """
            UPDATE messages
            SET text = ?
            WHERE business_connection_id = ? AND chat_id = ? AND message_id = ?
            """,
            (new_text, business_connection_id, chat_id, message_id),
        )


def save_connection(connection_id: str, owner_user_id: int, user_name: str = "", user_username: str = "", is_enabled: int = 1):
    if not connection_id or not owner_user_id:
        return
    with _lock, _conn() as c:
        c.execute(
            """
            INSERT INTO business_connections (connection_id, owner_user_id, user_name, user_username, is_enabled, date)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(connection_id) DO UPDATE SET
                owner_user_id = excluded.owner_user_id,
                user_name = CASE WHEN excluded.user_name != '' THEN excluded.user_name ELSE business_connections.user_name END,
                user_username = CASE WHEN excluded.user_username != '' THEN excluded.user_username ELSE business_connections.user_username END,
                is_enabled = excluded.is_enabled,
                date = excluded.date
            """,
            (connection_id, owner_user_id, user_name, user_username, is_enabled, int(datetime.now(timezone.utc).timestamp())),
        )


def get_connection_owner(connection_id: str):
    if not connection_id:
        return None
    with _conn() as c:
        row = c.execute(
            "SELECT owner_user_id FROM business_connections WHERE connection_id = ?",
            (connection_id,)
        ).fetchone()
        return row["owner_user_id"] if row else None


def get_connection_info(connection_id: str) -> dict | None:
    if not connection_id:
        return None
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM business_connections WHERE connection_id = ?",
            (connection_id,)
        ).fetchone()
        return dict(row) if row else None


def get_all_connections() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            """
            SELECT bc.*,
                   COUNT(DISTINCT m.chat_id) AS chats_count,
                   COUNT(m.id) AS msgs_count
            FROM business_connections bc
            LEFT JOIN messages m ON m.business_connection_id = bc.connection_id
            GROUP BY bc.connection_id
            ORDER BY bc.date DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_chats_for_connection(connection_id: str) -> list[dict]:
    if not connection_id:
        return []
    with _conn() as c:
        rows = c.execute(
            """
            SELECT chat_id,
                   COALESCE(chat_title, CAST(chat_id AS TEXT)) AS chat_title,
                   COUNT(*) AS msg_count,
                   MAX(date) AS last_date
            FROM messages
            WHERE business_connection_id = ?
            GROUP BY chat_id
            ORDER BY last_date DESC
            """,
            (connection_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def is_new_chat_for_connection(business_connection_id: str, chat_id: int) -> bool:
    """Проверяет, было ли уже сохранено хотя бы одно сообщение из этого чата."""
    if not business_connection_id or not chat_id:
        return False
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM messages WHERE business_connection_id = ? AND chat_id = ? LIMIT 1",
            (business_connection_id, chat_id)
        ).fetchone()
        return row is None


def save_bot_user(user_id: int, user_name: str = "", username: str = ""):
    """Сохраняет пользователя, нажавшего /start или подключившего бота."""
    if not user_id:
        return
    now_ts = int(datetime.now(timezone.utc).timestamp())
    with _lock, _conn() as c:
        c.execute(
            """
            INSERT INTO bot_users (user_id, user_name, username, first_start, last_start)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                user_name = CASE WHEN excluded.user_name != '' THEN excluded.user_name ELSE bot_users.user_name END,
                username = CASE WHEN excluded.username != '' THEN excluded.username ELSE bot_users.username END,
                last_start = excluded.last_start
            """,
            (user_id, user_name or "", username or "", now_ts, now_ts),
        )


def get_started_users() -> list[dict]:
    """Список всех пользователей, нажавших /start (с датами запусков)."""
    with _conn() as c:
        rows = c.execute(
            """
            SELECT * FROM bot_users
            ORDER BY last_start DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_connected_users() -> list[dict]:
    """Группирует подключения Telegram Business по владельцам (пользователям)."""
    with _conn() as c:
        rows = c.execute(
            """
            SELECT bc.owner_user_id AS user_id,
                   COALESCE(NULLIF(MAX(bc.user_name), ''), MAX(bu.user_name), '') AS user_name,
                   COALESCE(NULLIF(MAX(bc.user_username), ''), MAX(bu.username), '') AS user_username,
                   MAX(bc.is_enabled) AS is_enabled,
                   MAX(bc.date) AS date,
                   COUNT(DISTINCT m.chat_id) AS chats_count,
                   COUNT(m.id) AS msgs_count,
                   MAX(bc.connection_id) AS connection_id
            FROM business_connections bc
            LEFT JOIN bot_users bu ON bu.user_id = bc.owner_user_id
            LEFT JOIN messages m ON m.business_connection_id = bc.connection_id
            WHERE bc.owner_user_id IS NOT NULL AND bc.owner_user_id != 0
            GROUP BY bc.owner_user_id
            ORDER BY date DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_chats_by_owner(owner_user_id: int) -> list[dict]:
    """Возвращает все уникальные диалоги, к которым владелец предоставил доступ боту."""
    if not owner_user_id:
        return []
    with _conn() as c:
        conn_rows = c.execute(
            "SELECT connection_id FROM business_connections WHERE owner_user_id = ?",
            (owner_user_id,)
        ).fetchall()
        conn_ids = [r["connection_id"] for r in conn_rows]
        if not conn_ids:
            return []
        placeholders = ",".join("?" for _ in conn_ids)
        rows = c.execute(
            f"""
            SELECT m.business_connection_id,
                   m.chat_id,
                   COALESCE(
                       MAX(CASE WHEN m.chat_title IS NOT NULL AND m.chat_title != '' THEN m.chat_title END),
                       CAST(m.chat_id AS TEXT)
                   ) AS chat_title,
                   COUNT(*) AS msg_count,
                   MAX(m.date) AS last_date
            FROM messages m
            WHERE m.business_connection_id IN ({placeholders})
            GROUP BY m.chat_id
            ORDER BY last_date DESC
            """,
            conn_ids
        ).fetchall()
        return [dict(r) for r in rows]


def find_chat_for_export(query: str, owner_user_id: int = None) -> list[dict]:
    """Ищет чаты по ID чата или по части названия / юзернейма."""
    query = (query or "").strip()
    if not query:
        return []

    raw_query = query.lstrip("@")
    is_num = False
    try:
        int(query)
        is_num = True
    except ValueError:
        pass

    with _conn() as c:
        conn_ids = []
        if owner_user_id:
            conn_rows = c.execute(
                "SELECT connection_id FROM business_connections WHERE owner_user_id = ?",
                (owner_user_id,)
            ).fetchall()
            conn_ids = [r["connection_id"] for r in conn_rows]
            if not conn_ids:
                return []

        base_sql = """
            SELECT m.business_connection_id,
                   m.chat_id,
                   COALESCE(
                       MAX(CASE WHEN m.chat_title IS NOT NULL AND m.chat_title != '' THEN m.chat_title END),
                       CAST(m.chat_id AS TEXT)
                   ) AS chat_title,
                   COUNT(*) AS msg_count,
                   MAX(m.date) AS last_date
            FROM messages m
            WHERE
        """
        where_parts = []
        params = []

        if conn_ids:
            ph = ",".join("?" for _ in conn_ids)
            where_parts.append(f"m.business_connection_id IN ({ph})")
            params.extend(conn_ids)

        if is_num:
            where_parts.append("(m.chat_id = ? OR m.chat_title LIKE ?)")
            params.extend([int(query), f"%{query}%"])
        else:
            where_parts.append("(m.chat_title LIKE ? OR m.chat_title LIKE ?)")
            params.extend([f"%{query}%", f"%{raw_query}%"])

        full_sql = base_sql + " " + " AND ".join(where_parts) + """
            GROUP BY m.chat_id
            ORDER BY last_date DESC
            LIMIT 25
        """
        rows = c.execute(full_sql, params).fetchall()
        return [dict(r) for r in rows]


def get_user_info_for_admin(user_id: int) -> dict | None:
    """Полная сводка по пользователю для админ-панели."""
    if not user_id:
        return None
    with _conn() as c:
        bc_row = c.execute(
            """
            SELECT owner_user_id AS user_id, user_name, user_username, is_enabled, date, connection_id
            FROM business_connections
            WHERE owner_user_id = ?
            ORDER BY date DESC LIMIT 1
            """,
            (user_id,)
        ).fetchone()

        bu_row = c.execute(
            "SELECT * FROM bot_users WHERE user_id = ?",
            (user_id,)
        ).fetchone()

        if not bc_row and not bu_row:
            return None

        u_name = (bc_row["user_name"] if bc_row and bc_row["user_name"] else None) or (bu_row["user_name"] if bu_row else None) or f"Пользователь {user_id}"
        u_username = (bc_row["user_username"] if bc_row and bc_row["user_username"] else None) or (bu_row["username"] if bu_row else None) or ""
        is_enabled = bc_row["is_enabled"] if bc_row and bc_row["is_enabled"] is not None else 1
        conn_id = bc_row["connection_id"] if bc_row else ""
        date = bc_row["date"] if bc_row and bc_row["date"] else (bu_row["last_start"] if bu_row else 0)

        chats = get_chats_by_owner(user_id)
        total_msgs = sum(ch.get("msg_count", 0) for ch in chats)

        return {
            "user_id": user_id,
            "user_name": u_name,
            "username": u_username,
            "is_enabled": is_enabled,
            "connection_id": conn_id,
            "date": date,
            "chats_count": len(chats),
            "msgs_count": total_msgs,
            "chats": chats,
        }


def record_edit(user_id: int, original: str, fixed: str):
    if not user_id:
        return
    commas = max(0, fixed.count(",") - original.count(","))
    dots = max(0, fixed.count(".") - original.count("."))
    with _lock, _conn() as c:
        c.execute(
            """
            INSERT INTO stats_edits (user_id, original_len, fixed_len, commas_added, dots_added, date)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, len(original), len(fixed), commas, dots, int(datetime.now(timezone.utc).timestamp())),
        )


def get_user_stats(user_id: int) -> dict:
    with _conn() as c:
        # Статистика правок
        edit_row = c.execute(
            """
            SELECT COUNT(*) AS total_edits,
                   COALESCE(SUM(commas_added), 0) AS total_commas,
                   COALESCE(SUM(dots_added), 0) AS total_dots
            FROM stats_edits
            WHERE user_id = ?
            """,
            (user_id,)
        ).fetchone()

        # Подключения пользователя
        conn_rows = c.execute(
            "SELECT connection_id FROM business_connections WHERE owner_user_id = ?",
            (user_id,)
        ).fetchall()
        conn_ids = [r["connection_id"] for r in conn_rows]

        # Общие сообщения
        if conn_ids:
            placeholders = ",".join("?" for _ in conn_ids)
            msg_stats = c.execute(
                f"""
                SELECT COUNT(*) AS total_msgs,
                       COUNT(DISTINCT chat_id) AS total_chats,
                       SUM(CASE WHEN is_owner = 1 THEN 1 ELSE 0 END) AS my_msgs
                FROM messages
                WHERE business_connection_id IN ({placeholders})
                """,
                conn_ids
            ).fetchone()

            top_chats = c.execute(
                f"""
                SELECT COALESCE(chat_title, CAST(chat_id AS TEXT)) AS title,
                       COUNT(*) AS cnt
                FROM messages
                WHERE business_connection_id IN ({placeholders})
                GROUP BY chat_id
                ORDER BY cnt DESC
                LIMIT 3
                """,
                conn_ids
            ).fetchall()
        else:
            # Если подключение ещё не записано в базу, пробуем найти по from_user_id
            msg_stats = c.execute(
                """
                SELECT COUNT(*) AS total_msgs,
                       COUNT(DISTINCT chat_id) AS total_chats,
                       SUM(CASE WHEN is_owner = 1 THEN 1 ELSE 0 END) AS my_msgs
                FROM messages
                WHERE from_user_id = ? OR is_owner = 1
                """,
                (user_id,)
            ).fetchone()
            top_chats = []

        return {
            "total_edits": edit_row["total_edits"] if edit_row else 0,
            "total_commas": edit_row["total_commas"] if edit_row else 0,
            "total_dots": edit_row["total_dots"] if edit_row else 0,
            "total_msgs": msg_stats["total_msgs"] if msg_stats else 0,
            "total_chats": msg_stats["total_chats"] if msg_stats else 0,
            "my_msgs": msg_stats["my_msgs"] if msg_stats else 0,
            "top_chats": [dict(r) for r in top_chats],
        }


def search_messages(user_id: int, query: str, limit: int = 6):
    query = query.strip()
    if not query:
        return []

    with _conn() as c:
        conn_rows = c.execute(
            "SELECT connection_id FROM business_connections WHERE owner_user_id = ?",
            (user_id,)
        ).fetchall()
        conn_ids = [r["connection_id"] for r in conn_rows]

        if conn_ids:
            placeholders = ",".join("?" for _ in conn_ids)
            params = [f"%{query}%"] + conn_ids + [limit]
            sql = f"""
                SELECT message_id, chat_id, chat_title, from_user_name, is_owner, text, date
                FROM messages
                WHERE text LIKE ? AND business_connection_id IN ({placeholders})
                ORDER BY date DESC
                LIMIT ?
            """
        else:
            params = [f"%{query}%", limit]
            sql = """
                SELECT message_id, chat_id, chat_title, from_user_name, is_owner, text, date
                FROM messages
                WHERE text LIKE ?
                ORDER BY date DESC
                LIMIT ?
            """

        rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def save_message(*, business_connection_id, chat_id, chat_title,
                 message_id, from_user_id, from_user_name, is_owner,
                 text=None, media_type=None, file_id=None, date=None):
    with _lock, _conn() as c:
        c.execute(
            """
            INSERT OR IGNORE INTO messages
              (business_connection_id, chat_id, chat_title, message_id,
               from_user_id, from_user_name, is_owner, text,
               media_type, file_id, date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (business_connection_id, chat_id, chat_title, message_id,
             from_user_id, from_user_name,
             -1 if is_owner is None else int(bool(is_owner)),
             text, media_type, file_id,
             date or int(datetime.now(timezone.utc).timestamp())),
        )


def list_chats():
    """Список чатов: последнее непустое имя + последняя дата + кол-во."""
    with _conn() as c:
        rows = c.execute(
            """
            SELECT business_connection_id,
                   chat_id,
                   MAX(date) AS last_date,
                   COUNT(*)  AS cnt
            FROM messages
            GROUP BY business_connection_id, chat_id
            ORDER BY last_date DESC
            """
        ).fetchall()

        result = []
        for r in rows:
            title_row = c.execute(
                """
                SELECT COALESCE(chat_title, CAST(chat_id AS TEXT)) AS t
                FROM messages
                WHERE business_connection_id = ? AND chat_id = ?
                  AND chat_title IS NOT NULL AND chat_title != ''
                ORDER BY date DESC, id DESC
                LIMIT 1
                """,
                (r["business_connection_id"], r["chat_id"]),
            ).fetchone()

            title = title_row["t"] if title_row else str(r["chat_id"])
            result.append({
                "business_connection_id": r["business_connection_id"],
                "chat_id": r["chat_id"],
                "chat_title": title,
                "last_date": r["last_date"],
                "cnt": r["cnt"],
            })
        return result


def get_messages(business_connection_id, chat_id, since_ts, until_ts=None):
    until_ts = until_ts or int(datetime.now(timezone.utc).timestamp()) + 1
    with _conn() as c:
        rows = c.execute(
            """
            SELECT * FROM messages
            WHERE business_connection_id = ? AND chat_id = ?
              AND date >= ? AND date < ?
            ORDER BY date ASC, message_id ASC
            """,
            (business_connection_id, chat_id, since_ts, until_ts),
        ).fetchall()
        return [dict(r) for r in rows]


def create_reminder(*, user_id: int, chat_id: int, chat_title: str,
                    business_connection_id: str, title: str, target_time: int) -> int:
    with _lock, _conn() as c:
        cur = c.execute(
            """
            INSERT INTO reminders
                (user_id, chat_id, chat_title, business_connection_id, title, target_time, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
            """,
            (user_id, chat_id, chat_title, business_connection_id, title, target_time,
             int(datetime.now(timezone.utc).timestamp())),
        )
        return cur.lastrowid


def get_reminder(reminder_id: int):
    with _conn() as c:
        row = c.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        return dict(row) if row else None


def get_active_reminder_for_chat(chat_id: int, user_id: int = None):
    with _conn() as c:
        if user_id:
            row = c.execute(
                """
                SELECT * FROM reminders
                WHERE chat_id = ? AND user_id = ? AND status = 'active'
                ORDER BY target_time ASC LIMIT 1
                """,
                (chat_id, user_id),
            ).fetchone()
        else:
            row = c.execute(
                """
                SELECT * FROM reminders
                WHERE chat_id = ? AND status = 'active'
                ORDER BY target_time ASC LIMIT 1
                """,
                (chat_id,),
            ).fetchone()
        return dict(row) if row else None


def cancel_reminder(reminder_id: int) -> dict | None:
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        if not row:
            return None
        c.execute("UPDATE reminders SET status = 'cancelled' WHERE id = ?", (reminder_id,))
        return dict(row)


def cancel_active_reminder_for_chat(chat_id: int, user_id: int = None) -> dict | None:
    with _lock, _conn() as c:
        if user_id:
            row = c.execute(
                """
                SELECT * FROM reminders
                WHERE chat_id = ? AND user_id = ? AND status = 'active'
                ORDER BY target_time ASC LIMIT 1
                """,
                (chat_id, user_id),
            ).fetchone()
        else:
            row = c.execute(
                """
                SELECT * FROM reminders
                WHERE chat_id = ? AND status = 'active'
                ORDER BY target_time ASC LIMIT 1
                """,
                (chat_id,),
            ).fetchone()

        if row:
            c.execute("UPDATE reminders SET status = 'cancelled' WHERE id = ?", (row["id"],))
            return dict(row)
        return None


def get_pending_reminders():
    with _conn() as c:
        rows = c.execute(
            """
            SELECT * FROM reminders
            WHERE status = 'active'
            ORDER BY target_time ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def mark_reminder_notified(reminder_id: int, field: str):
    if field not in ("remind_1h_sent", "remind_30m_sent"):
        return
    with _lock, _conn() as c:
        c.execute(f"UPDATE reminders SET {field} = 1 WHERE id = ?", (reminder_id,))


def mark_reminder_completed(reminder_id: int):
    with _lock, _conn() as c:
        c.execute("UPDATE reminders SET status = 'completed' WHERE id = ?", (reminder_id,))


def get_user_reminders(user_id: int, limit: int = 10):
    with _conn() as c:
        rows = c.execute(
            """
            SELECT * FROM reminders
            WHERE user_id = ? AND status = 'active'
            ORDER BY target_time ASC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_message_by_chat_and_id(chat_id: int, message_id: int):
    with _conn() as c:
        row = c.execute(
            """
            SELECT * FROM messages
            WHERE chat_id = ? AND message_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (chat_id, message_id),
        ).fetchone()
        return dict(row) if row else None


def save_starred_message(*, user_id: int, chat_id: int, chat_title: str,
                         message_id: int, author_name: str, text: str,
                         media_type: str = None, file_id: str = None, date: int = None) -> int:
    with _lock, _conn() as c:
        cur = c.execute(
            """
            INSERT OR REPLACE INTO starred_messages
                (user_id, chat_id, chat_title, message_id, author_name, text, media_type, file_id, date, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                chat_id,
                chat_title,
                message_id,
                author_name,
                text,
                media_type,
                file_id,
                date or int(datetime.now(timezone.utc).timestamp()),
                int(datetime.now(timezone.utc).timestamp()),
            ),
        )
        return cur.lastrowid


def is_message_starred(user_id: int, chat_id: int, message_id: int) -> bool:
    with _conn() as c:
        row = c.execute(
            """
            SELECT id FROM starred_messages
            WHERE user_id = ? AND chat_id = ? AND message_id = ?
            LIMIT 1
            """,
            (user_id, chat_id, message_id),
        ).fetchone()
        return bool(row)


def get_starred_messages(user_id: int, limit: int = 10, offset: int = 0) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            """
            SELECT * FROM starred_messages
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_starred_message(star_id: int, user_id: int) -> bool:
    with _lock, _conn() as c:
        cur = c.execute(
            "DELETE FROM starred_messages WHERE id = ? AND user_id = ?",
            (star_id, user_id),
        )
        return cur.rowcount > 0


def save_cached_media_path(conn_id: str, chat_id: int, message_id: int, file_path: str):
    with _lock, _conn() as c:
        c.execute(
            """
            INSERT OR REPLACE INTO media_cache
                (business_connection_id, chat_id, message_id, file_path, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (conn_id, chat_id, message_id, file_path, int(datetime.now(timezone.utc).timestamp())),
        )


def get_cached_media_path(conn_id: str, chat_id: int, message_id: int) -> str | None:
    with _conn() as c:
        row = c.execute(
            """
            SELECT file_path FROM media_cache
            WHERE business_connection_id = ? AND chat_id = ? AND message_id = ?
            LIMIT 1
            """,
            (conn_id, chat_id, message_id),
        ).fetchone()
        return row["file_path"] if row else None


