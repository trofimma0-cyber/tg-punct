"""
Обёртка над RUPunct — специализированной моделью классификации токенов
для расстановки пунктуации и регистра в русском тексте. Не переписывает
текст заново (в отличие от LLM), а размечает существующие слова, поэтому
быстрее и не меняет смысл фразы.

Поддерживает два режима:
- ONNX (лёгкий, для деплоя с ограниченной RAM) — используется, если
  папка onnx_model/ существует (создаётся скриптом export_onnx.py)
- Обычный transformers (тяжелее, но не требует предварительной конвертации)
  — используется как запасной вариант, если onnx_model/ нет
"""
import os
import re
import threading

_classifier = None
_lock = threading.Lock()

MODEL_NAME = "RUPunct/RUPunct_small"
ONNX_DIR = "onnx_model"

SUFFIX_MAP = {
    "LOWER_O": "", "LOWER_PERIOD": ".", "LOWER_COMMA": ",",
    "LOWER_QUESTION": "?", "LOWER_TIRE": " —", "LOWER_DVOETOCHIE": ":",
    "LOWER_VOSKL": "!", "LOWER_PERIODCOMMA": ";", "LOWER_DEFIS": "-",
    "LOWER_MNOGOTOCHIE": "...", "LOWER_QUESTIONVOSKL": "?!",
    "UPPER_O": "", "UPPER_PERIOD": ".", "UPPER_COMMA": ",",
    "UPPER_QUESTION": "?", "UPPER_TIRE": " —", "UPPER_DVOETOCHIE": ":",
    "UPPER_VOSKL": "!", "UPPER_PERIODCOMMA": ";", "UPPER_DEFIS": "-",
    "UPPER_MNOGOTOCHIE": "...", "UPPER_QUESTIONVOSKL": "?!",
    "UPPER_TOTAL_O": "", "UPPER_TOTAL_PERIOD": ".", "UPPER_TOTAL_COMMA": ",",
    "UPPER_TOTAL_QUESTION": "?", "UPPER_TOTAL_TIRE": " —",
    "UPPER_TOTAL_DVOETOCHIE": ":", "UPPER_TOTAL_VOSKL": "!",
    "UPPER_TOTAL_PERIODCOMMA": ";", "UPPER_TOTAL_DEFIS": "-",
    "UPPER_TOTAL_MNOGOTOCHIE": "...", "UPPER_TOTAL_QUESTIONVOSKL": "?!",
}

COMMON_YO_MAP = {
    "еще": "ещё", "Еще": "Ещё",
    "все": "всё", "Все": "Всё",
    "ее": "её", "Ее": "Её",
    "свое": "своё", "Свое": "Своё",
    "твое": "твоё", "Твое": "Твоё",
    "мое": "моё", "Мое": "Моё",
    "черт": "чёрт", "Черт": "Чёрт",
    "о нем": "о нём", "О нем": "О нём",
    "зачет": "зачёт", "Зачет": "Зачёт",
    "ребенок": "ребёнок", "Ребенок": "Ребёнок",
    "счет": "счёт", "Счет": "Счёт",
    "насчет": "насчёт", "Насчет": "Насчёт",
    "четко": "чётко", "Четко": "Чётко",
    "вдвоем": "вдвоём", "Вдвоем": "Вдвоём",
    "втроем": "втроём", "Втроем": "Втроём",
    "пошел": "пошёл", "Пошел": "Пошёл",
    "нашел": "нашёл", "Нашел": "Нашёл",
    "пришел": "пришёл", "Пришел": "Пришёл",
    "ушел": "ушёл", "Ушел": "Ушёл",
}


_YO_RE = re.compile(r'\b(' + '|'.join(map(re.escape, COMMON_YO_MAP.keys())) + r')\b')
_DIGIT_WORDS = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять", "десять"]
_PROT_PATTERN = re.compile(r"(`[^`]+`|https?://\S+|t\.me/\S+|@\w+|#\w+)")

# Неформальные дефисные частицы в разговорной речи (-то, -ка, -таки)
HYPHEN_RE = re.compile(
    r'\b(сам|как|где|кто|что|че|чё|чо|так|куда|откуда|почему|зачем|когда|он|она|они|мы|вы|ты|я|щас|сейчас|всё|все|да|нет|тот|та|те)\s+(то)\b',
    re.IGNORECASE
)
HYPHEN_KA_RE = re.compile(
    r'\b(давай|глянь|погоди|стой|постой|на|ну|гляди|посмотри|послушай|скажи)\s+(ка)\b',
    re.IGNORECASE
)
HYPHEN_TAKI_RE = re.compile(
    r'\b(все|всё|так|опять|снова)\s+(таки)\b',
    re.IGNORECASE
)

# Разговорные приветствия и обращения (чтобы не было 'Дарова. Лох.')
GREETINGS = [
    'дарова', 'здарова', 'здорово', 'привет', 'приветик', 'хай', 'салам', 'хеллоу', 'ку', 'йоу', 'ало', 'алло',
    'добрый день', 'добрый вечер', 'доброе утро'
]
VOCATIVES = [
    'друг', 'друзья', 'брат', 'бро', 'чувак', 'чел', 'братан', 'братишка', 'кореш', 'родной', 'лох',
    'малой', 'дядя', 'пацаны', 'пацан', 'ребята', 'красотка', 'шеф', 'мужики', 'босс', 'народ'
]
GREETING_VOC_RE = re.compile(
    r'\b(' + '|'.join(GREETINGS) + r')[\.,!\s]+(' + '|'.join(VOCATIVES) + r')\b',
    re.IGNORECASE
)

# Интеллектуальный детектор вопросов для неформального общения
QUESTION_WORDS = {
    'кто', 'что', 'че', 'чё', 'чо', 'кого', 'кому', 'кем', 'ком',
    'где', 'куда', 'откуда', 'когда',
    'как', 'почему', 'зачем', 'отчего',
    'какой', 'какая', 'какое', 'какие', 'какого', 'какому', 'каким', 'каких',
    'чей', 'чья', 'чьё', 'чье', 'чьи',
    'сколько', 'скольких', 'скольким', 'почём', 'почем', 'насколько'
}
QUESTION_VERBS = {
    'пойдешь', 'пойдёшь', 'поедешь', 'хочешь', 'будешь', 'можешь', 'знаешь',
    'помнишь', 'видел', 'слышал', 'думаешь', 'скинешь', 'поможешь', 'подскажешь',
    'успеешь', 'придешь', 'придёшь', 'сможешь', 'пойдете', 'пойдёте', 'поедете',
    'хотите', 'будете', 'можете', 'знаете', 'помните', 'видели', 'слышали',
    'думаете', 'скинете', 'поможете', 'подскажете', 'успеете', 'придете',
    'придёте', 'сможете', 'пойдем', 'пойдём', 'поедем', 'будем', 'погнали'
}
QUESTION_STATES = {
    'свободен', 'свободна', 'свободны', 'занят', 'занята', 'заняты', 'дома',
    'готов', 'готова', 'готовы', 'живой', 'жив', 'спишь'
}
SHORT_QUESTIONS = {
    'правда', 'серьезно', 'серьёзно', 'точно', 'уверен', 'уверена', 'реально',
    'можно', 'куда', 'где', 'когда', 'зачем', 'почему', 'кто', 'что', 'че',
    'чё', 'чо', 'а ты', 'а вы', 'а он', 'а она', 'а мы', 'а они', 'в смысле', 'всмысле'
}

def is_sentence_question(s: str, context_info: dict = None) -> bool:
    s_clean = re.sub(r'[\.,!\?:;]+', '', s).strip().lower()
    if not s_clean:
        return False
    words = s_clean.split()
    if not words:
        return False

    prev_text = context_info.get("prev_text") if context_info else None
    prev_is_q = bool(prev_text and ("?" in prev_text or is_sentence_question(prev_text)))

    # Если собеседник только что задал вопрос, то одиночный ответ (дома, норм) — не вопрос
    if prev_is_q:
        if s_clean in {'дома', 'норм', 'нормально', 'да', 'нет', 'хорошо', 'ладно', 'лан', 'хз', 'не знаю', 'еду', 'сплю', 'работаю', 'занят', 'свободен'}:
            return False

    if s_clean in SHORT_QUESTIONS:
        return True
    if 'ли' in words or 'ль' in words:
        return True
    if re.search(r',\s*(да|нет|правда|верно|так|ок|окей)\b', s, re.IGNORECASE):
        return True

    skip_prefixes = {'слушай', 'скажи', 'глянь', 'прикинь', 'короче', 'кстати', 'привет', 'дарова', 'здарова', 'здорово', 'хай', 'салам', 'ало', 'алло', 'ну', 'а'}
    start_idx = 0
    while start_idx < len(words) and words[start_idx] in skip_prefixes:
        start_idx += 1
    check_words = words[start_idx:] if start_idx < len(words) else words
    if not check_words:
        return False

    if check_words[0] in QUESTION_WORDS:
        return True
    if 'как' in check_words and ('сам' in check_words or 'дела' in check_words or 'ты' in check_words or 'вы' in check_words):
        return True
    if len(check_words) >= 2 and check_words[0] in {'ты', 'вы'} and check_words[1] in QUESTION_WORDS:
        return True
    if check_words[0] in QUESTION_VERBS or check_words[0] in QUESTION_STATES:
        return True
    if len(check_words) >= 2 and check_words[0] in {'ты', 'вы', 'мы'} and (check_words[1] in QUESTION_VERBS or check_words[1] in QUESTION_STATES):
        return True
    return False



def apply_text_transforms(text: str, settings: dict = None) -> str:
    if not text or not settings:
        return text

    # 1. Типографика (тире, кавычки, пробелы)
    if settings.get("typography", 1):
        text = re.sub(r'(?<=\S)\s+-\s+(?=\S)', ' — ', text)
        text = re.sub(r'"([^"\n]+)"', r'«\1»', text)
        text = re.sub(r'[ \t]{2,}', ' ', text)

    # 2. Ёфикация (быстрый однопроходный regex)
    if settings.get("yo", 0):
        text = _YO_RE.sub(lambda m: COMMON_YO_MAP[m.group(0)], text)

    return text


def _process_token(token: str, label: str, settings: dict = None) -> str:
    settings = settings or {}
    allow_caps = bool(settings.get("caps", 1))
    allow_punct = bool(settings.get("punct", 1))

    suffix = SUFFIX_MAP.get(label, "") if allow_punct else ""
    if allow_caps:
        if label.startswith("UPPER_TOTAL_"):
            base = token.upper()
        elif label.startswith("UPPER_"):
            base = token.capitalize()
        else:
            base = token
    else:
        base = token
    return base + suffix


class _PureONNXClassifier:
    """Ультра-лёгкий инференс ONNX без transformers и torch (~180 MB RAM вместо 580 MB)."""
    def __init__(self, model_dir=ONNX_DIR):
        import json
        import onnxruntime as ort
        from tokenizers import Tokenizer

        with open(os.path.join(model_dir, "config.json"), "r", encoding="utf-8") as f:
            cfg = json.load(f)
        self.id2label = {int(k): v for k, v in cfg.get("id2label", {}).items()}
        self.tokenizer = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(os.path.join(model_dir, "model.onnx"), opts)

    def __call__(self, text: str):
        if not text:
            return []
        import numpy as np

        encoded = self.tokenizer.encode(text)
        tokens = encoded.tokens
        ids = encoded.ids
        type_ids = encoded.type_ids
        mask = encoded.attention_mask
        offsets = encoded.offsets

        if not ids:
            return []

        inputs = {
            "input_ids": np.array([ids], dtype=np.int64),
            "attention_mask": np.array([mask], dtype=np.int64),
            "token_type_ids": np.array([type_ids], dtype=np.int64),
        }
        logits = self.session.run(None, inputs)[0][0]
        pred_ids = np.argmax(logits, axis=-1)

        results = []
        current_word_chars = []
        current_label = None

        for token, (start, end), pred_id in zip(tokens, offsets, pred_ids):
            if token in ("[CLS]", "[SEP]", "[PAD]"):
                continue

            if token.startswith("##"):
                current_word_chars.append(token[2:])
            else:
                if current_word_chars and current_label is not None:
                    results.append({
                        "entity_group": current_label,
                        "word": "".join(current_word_chars),
                    })
                current_word_chars = [token]
                current_label = self.id2label.get(pred_id, "LOWER_O")

        if current_word_chars and current_label is not None:
            results.append({
                "entity_group": current_label,
                "word": "".join(current_word_chars),
            })

        return results


def _get_classifier():
    global _classifier
    if _classifier is None:
        with _lock:
            if _classifier is None:
                if os.path.isdir(ONNX_DIR):
                    try:
                        _classifier = _PureONNXClassifier(ONNX_DIR)
                        return _classifier
                    except Exception as e:
                        print(f"[punct] Ошибка инициализации pure ONNX, откат на transformers: {e}")

                from transformers import pipeline, AutoTokenizer

                model = MODEL_NAME
                tokenizer = AutoTokenizer.from_pretrained(
                    MODEL_NAME, strip_accents=False, add_prefix_space=True
                )
                _classifier = pipeline(
                    "ner",
                    model=model,
                    tokenizer=tokenizer,
                    aggregation_strategy="first",
                )
    return _classifier


def warmup():
    """Прогревает модель при старте бота, чтобы первое сообщение пользователя обрабатывалось мгновенно."""
    try:
        clf = _get_classifier()
        if clf:
            clf("прогрев модели")
    except Exception as e:
        print(f"[punct] ошибка прогрева: {e}")


def _fix_single_line(line: str, settings: dict, context_info: dict = None) -> str:
    stripped = line.strip()
    if not stripped:
        return line

    # Быстрый фильтр: если нет русских букв — не тратим ресурсы на нейросеть
    if not re.search(r"[а-яёА-ЯЁ]", stripped):
        res = apply_text_transforms(line, settings)
        if res.endswith(".") and not res.endswith("..."):
            res = res[:-1].rstrip()
        return res

    # Быстрый фильтр: одиночное русское слово («да», «нет», «хорошо», «привет», «правда»)
    words = stripped.split()
    if len(words) == 1 and re.match(r"^[а-яёА-ЯЁ]+$", words[0]):
        w = words[0]
        if settings.get("caps", 1):
            w = w[0].upper() + w[1:]
        if is_sentence_question(words[0], context_info):
            w = w + "?"
        return apply_text_transforms(w, settings)

    # Дефисные частицы в неформальной речи (-то, -ка, -таки)
    stripped = HYPHEN_RE.sub(r'\1-\2', stripped)
    stripped = HYPHEN_KA_RE.sub(r'\1-\2', stripped)
    stripped = HYPHEN_TAKI_RE.sub(r'\1-\2', stripped)

    # Защита спец-элементов (ссылки, юзернеймы, хештеги, моноширинный код)
    placeholders = {}

    def _protect(m):
        idx = len(placeholders)
        word_idx = _DIGIT_WORDS[idx] if idx < len(_DIGIT_WORDS) else f"м{idx}"
        tag = f"спецмаркер{word_idx}"
        placeholders[tag] = m.group(0)
        return tag

    protected = _PROT_PATTERN.sub(_protect, stripped)

    # Очищаем вход для модели от существующей пунктуации, чтобы избежать дубликатов
    cleaned_for_model = re.sub(r"[\.,\?!:;—–\-]+", " ", protected)
    cleaned_for_model = re.sub(r"\s+", " ", cleaned_for_model).strip()

    try:
        classifier = _get_classifier()
        preds = classifier(cleaned_for_model)
        if not preds:
            res = apply_text_transforms(line, settings)
            if res.endswith(".") and not res.endswith("..."):
                res = res[:-1].rstrip()
            return res

        parts = []
        for item in preds:
            word = item["word"].strip()
            if not word:
                continue
            parts.append(_process_token(word, item["entity_group"], settings))

        res = " ".join(parts).strip()
    except Exception as e:
        print(f"[punct] ошибка модели: {e}")
        res = apply_text_transforms(line, settings)
        if res.endswith(".") and not res.endswith("..."):
            res = res[:-1].rstrip()
        return res

    # Восстанавливаем защищённые элементы
    for tag, val in placeholders.items():
        res = re.sub(re.escape(tag), val, res, flags=re.IGNORECASE)

    # Убираем пробелы перед знаками препинания
    res = re.sub(r"\s+([,\.!\?:;])", r"\1", res)
    res = re.sub(r"\?{2,}!", "?!", res)
    res = re.sub(r"!{2,}", "!", res)
    res = re.sub(r"\.{4,}", "...", res)

    # Приветствие + обращение ('Дарова. Лох.' -> 'Дарова, лох')
    res = GREETING_VOC_RE.sub(lambda m: f"{m.group(1).capitalize()}, {m.group(2).lower()}", res)

    # Восстановление дефисов после модели ('сам, то' -> 'сам-то')
    res = re.sub(r'\b(сам|как|где|кто|что|че|чё|чо|так|куда|откуда|почему|зачем|когда|он|она|они|мы|вы|ты|я|щас|сейчас|всё|все|да|нет),?\s+то\b', r'\1-то', res, flags=re.IGNORECASE)
    res = re.sub(r'\b(давай|глянь|погоди|стой|постой|на|ну|гляди|посмотри|послушай|скажи),?\s+ка\b', r'\1-ка', res, flags=re.IGNORECASE)
    res = re.sub(r'\b(все|всё|так|опять|снова),?\s+таки\b', r'\1-таки', res, flags=re.IGNORECASE)

    # Разговорные вводные в начале строки
    res = re.sub(r'^(короче|кстати|в общем|впрочем|прикинь)\s+(?=[а-яёА-ЯЁ])', r'\1, ', res, flags=re.IGNORECASE)

    # Исправляем регистр предложений
    if settings.get("caps", 1):
        if len(res) > 0 and res[0].islower():
            res = res[0].upper() + res[1:]
        res = re.sub(r"([\.!\?]\s+)([а-яё])", lambda m: m.group(1) + m.group(2).upper(), res)

    # Интеллектуальный вопрос: если фраза — вопрос, ставим '?' вместо '.' или '!'
    if is_sentence_question(stripped, context_info):
        if res.endswith((".", "!")):
            res = res[:-1].rstrip() + "?"
        elif not res.endswith(("?", "...", "?!")):
            res = res.rstrip() + "?"

    # Убираем точку в конце сообщения (по запросу: не ставить в конце точку!)
    if res.endswith(".") and not res.endswith("..."):
        res = res[:-1].rstrip()

    return apply_text_transforms(res, settings)


def fix_punctuation(text: str, settings: dict = None, context_info: dict = None) -> str:
    """Возвращает текст с расставленными знаками препинания и регистром.
    Бережно сохраняет переносы строк, ссылки, юзернеймы и разметку.
    Не ставит точку в самом конце сообщения в неформальном общении.
    """
    if not text:
        return text

    settings = settings or {}
    if not settings.get("enabled", 1):
        return text

    # Если знаки препинания выключены пользователем
    if not settings.get("punct", 1):
        res = text
        if settings.get("caps", 1):
            if len(res) > 0 and res[0].islower():
                res = res[0].upper() + res[1:]
            res = re.sub(r"([\.!\?]\s+)([а-яёА-ЯЁ])", lambda m: m.group(1) + m.group(2).upper(), res)
        if res.endswith(".") and not res.endswith("..."):
            res = res[:-1].rstrip()
        return apply_text_transforms(res, settings)

    # Обработка многострочного текста с сохранением абзацев
    if "\n" in text:
        lines = text.split("\n")
        fixed_lines = [_fix_single_line(l, settings, context_info) for l in lines]
        res = "\n".join(fixed_lines)
    else:
        res = _fix_single_line(text, settings, context_info)

    # Финальная гарантия: никакого периода в самом конце сообщения
    if res.endswith(".") and not res.endswith("..."):
        res = res[:-1].rstrip()

    return res
