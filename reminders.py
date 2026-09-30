"""Модуль распознавания встреч, договорённостей и их отмен в сообщениях.
"""
import re
from datetime import datetime, timedelta, timezone

CANCEL_PATTERNS = [
    r"\bне могу\b",
    r"\bне смогу\b",
    r"\bне получается\b",
    r"\bне выйдет\b",
    r"\bне пойду\b",
    r"\bне согласен\b",
    r"\bне согласна\b",
    r"\bотмена\b",
    r"\bотменяется\b",
    r"\bдавай не\b",
    r"\bя пас\b",
    r"\bне успею\b",
    r"\bне буду\b",
    r"\bне поеду\b",
    r"\bперенесем\b",
    r"\bдавай перенесем\b",
]

TIME_REGEX = re.compile(
    r"(?:(?:в|к|до)\s*)?([0-1]?[0-9]|2[0-3])[:.]([0-5][0-9])\b"
)

MEETING_TRIGGERS = [
    "гулять", "встреча", "встретимся", "созвон", "созвонимся",
    "пойдем", "пошли", "поедем", "кино", "кофе", "обед", "ужин",
    "тренировка", "зал", "давай", "договор", "дело"
]


def is_cancellation(text: str) -> bool:
    """Проверяет, содержит ли сообщение отказ, несогласие или отмену встречи."""
    if not text:
        return False
    t = text.lower()
    for p in CANCEL_PATTERNS:
        if re.search(p, t):
            return True
    return False


def extract_meeting(text: str, now_dt: datetime = None) -> dict | None:
    """Извлекает информацию о встрече: время, название/действие и целевую метку времени."""
    if not text:
        return None
    t = text.lower().strip()

    # Ищем время вида 10:50 или 10.50
    m = TIME_REGEX.search(t)
    if not m:
        return None

    hour = int(m.group(1))
    minute = int(m.group(2))

    # Должен быть контекст встречи, предложения или слово "в HH:MM"
    has_trigger = any(tr in t for tr in MEETING_TRIGGERS)
    if not has_trigger and not re.search(r"\bв\s+\d{1,2}[:.]\d{2}\b", t):
        return None

    now_dt = now_dt or datetime.now()
    is_tomorrow = "завтра" in t

    target = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if is_tomorrow:
        target += timedelta(days=1)
    elif target <= now_dt:
        # Если указанное время на сегодня уже прошло — относим на завтра
        target += timedelta(days=1)

    # Определяем тему/действие встречи
    cleaned_topic = t
    cleaned_topic = re.sub(TIME_REGEX, "", cleaned_topic)
    cleaned_topic = re.sub(
        r"\b(давай|завтра|сегодня|в|к|до|по|на|и|ну|мы|же|ты|я|мне|бы|ли|то)\b",
        "",
        cleaned_topic
    ).strip()
    cleaned_topic = re.sub(r"[^\w\s-]", "", cleaned_topic)
    cleaned_topic = re.sub(r"\s+", " ", cleaned_topic).strip()

    title = cleaned_topic if cleaned_topic else "Встреча"

    return {
        "title": title,
        "hour": hour,
        "minute": minute,
        "target_dt": target,
        "target_ts": int(target.timestamp()),
        "is_tomorrow": is_tomorrow or (target.date() > now_dt.date()),
    }
