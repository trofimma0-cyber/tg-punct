"""Модуль автоматического переключения клавиатурной раскладки (Punto Switcher).
Преобразует текст, случайно набранный в английской раскладке (QWERTY), в правильный русский (ЙЦУКЕН).
"""
import re

COMMON_EN = {
    "the", "be", "to", "of", "and", "a", "in", "that", "have", "i", "it", "for",
    "not", "on", "with", "he", "as", "you", "do", "at", "this", "but", "his", "by",
    "from", "they", "we", "say", "her", "she", "or", "an", "will", "my", "one",
    "all", "would", "there", "their", "what", "so", "up", "out", "if", "about",
    "who", "get", "which", "go", "me", "when", "make", "can", "like", "time", "no",
    "just", "him", "know", "take", "people", "into", "year", "your", "good", "some",
    "could", "them", "see", "other", "than", "then", "now", "look", "only", "come",
    "its", "over", "think", "also", "back", "after", "use", "two", "how", "our",
    "work", "first", "well", "way", "even", "new", "want", "because", "any", "these",
    "give", "day", "most", "us", "hello", "hi", "hey", "bro", "ok", "yes", "cool",
    "super", "please", "pls", "thx", "thanks", "lol", "wait", "ready", "done", "test",
    "check", "id", "bot", "python", "file", "code", "run", "stop", "link", "chat",
    "admin", "info", "help", "settings", "stats", "true", "false", "none", "def"
}

COMMON_RU = {
    "привет", "как", "дела", "что", "где", "когда", "зачем", "почему", "давай",
    "не", "нет", "да", "могу", "буду", "сейчас", "позже", "скоро", "пошли", "пойдем",
    "гулять", "встреча", "созвон", "спасибо", "пожалуйста", "ладно", "хорошо",
    "понял", "ясно", "ты", "вы", "он", "она", "они", "мы", "меня", "тебя", "его",
    "ее", "их", "нас", "вас", "мне", "тебе", "ему", "ей", "нам", "вам", "мой",
    "твой", "свой", "наш", "ваш", "кто", "это", "этот", "эта", "все", "всё",
    "только", "уже", "еще", "ещё", "тут", "там", "здесь", "куда", "откуда",
    "очень", "много", "мало", "надо", "нужно", "можно", "нельзя", "будет",
    "было", "есть", "хочу", "хочешь", "знаю", "думаю", "смотри", "слушай",
    "пока", "договор", "оплата", "деньги", "карта", "номер", "скинь", "скинуть",
    "отправь", "жду", "жди", "сегодня", "завтра", "вчера", "утро", "день", "вечер",
    "ночь", "час", "минут", "минуту", "минуты", "часов", "часа", "руб", "рублей",
    "время", "место", "город", "работа", "работаю", "дома", "улица", "через",
    "после", "перед", "под", "над", "при", "про", "без", "для", "из", "до", "от",
    "со", "во", "ко", "же", "ли", "бы", "то", "или", "если", "хотя", "чтобы"
}

EN_CHARS = "qwertyuiop[]asdfghjkl;'zxcvbnm,./`QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>?~@#$^&"
RU_CHARS = "йцукенгшщзхъфывапролджэячсмитьбю.ёЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ?Ё\"№;:?"

EN_TO_RU_TABLE = str.maketrans(EN_CHARS, RU_CHARS)


def convert_en_to_ru(text: str) -> str:
    """Конвертирует строку из QWERTY в русскую раскладку, бережно сохраняя время и знаки."""
    # Сохраняем паттерны времени вроде 10:50, чтобы ':' не превратился в 'Ж'
    time_placeholders = []

    def _replace_time(m):
        time_placeholders.append(m.group(0))
        return f"\x01{len(time_placeholders) - 1}\x01"

    temp = re.sub(r"\b\d{1,2}:\d{2}\b", _replace_time, text)

    # Транслитерация
    res = temp.translate(EN_TO_RU_TABLE)

    # Восстанавливаем время
    for i, t_val in enumerate(time_placeholders):
        res = res.replace(f"\x01{i}\x01", t_val)

    return res


def should_convert_layout(text: str) -> bool:
    """Проверяет, набран ли текст по ошибке в неверной раскладке клавиатуры."""
    clean = text.strip()
    if not clean or len(clean) < 2:
        return False

    # Игнорируем команды и ссылки
    if clean.startswith(("/", "!", "@", "http://", "https://")):
        return False

    # Если уже есть русские буквы - не трогаем (текст уже частично или полностью на русском)
    if re.search(r"[а-яА-ЯёЁ]", clean):
        return False

    # Выделяем слова
    words = [w.lower() for w in re.findall(r"[a-zA-Z]+", clean)]
    if not words:
        return False

    # Считаем валидные английские слова
    en_matches = sum(1 for w in words if w in COMMON_EN)

    # Проверяем, что получается на русском
    converted = convert_en_to_ru(clean)
    ru_words = [w.lower() for w in re.findall(r"[а-яА-ЯёЁ]+", converted)]
    ru_matches = sum(1 for w in ru_words if w in COMMON_RU)

    # Если есть хотя бы одно точное русское совпадение и русских больше/равно английских
    if ru_matches > 0 and ru_matches >= en_matches:
        return True

    # Проверка на полное отсутствие английских гласных (типично для ошибочного набора ghbdtn, rfr, ltkf)
    no_vowels = sum(1 for w in words if len(w) >= 3 and not re.search(r"[aeiouy]", w))
    if no_vowels > 0 and en_matches == 0:
        return True

    return False
