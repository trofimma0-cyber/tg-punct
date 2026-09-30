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


def _fix_single_line(line: str, settings: dict) -> str:
    stripped = line.strip()
    if not stripped:
        return line

    # Быстрый фильтр: если нет русских букв — не тратим ресурсы на нейросеть
    if not re.search(r"[а-яёА-ЯЁ]", stripped):
        return apply_text_transforms(line, settings)

    # Быстрый фильтр: одиночное русское слово («да», «нет», «хорошо», «привет»)
    words = stripped.split()
    if len(words) == 1 and re.match(r"^[а-яёА-ЯЁ]+$", words[0]):
        w = words[0]
        if settings.get("caps", 1):
            w = w[0].upper() + w[1:]
        return apply_text_transforms(w, settings)

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
            return apply_text_transforms(line, settings)

        parts = []
        for item in preds:
            word = item["word"].strip()
            if not word:
                continue
            parts.append(_process_token(word, item["entity_group"], settings))

        res = " ".join(parts).strip()
    except Exception as e:
        print(f"[punct] ошибка модели: {e}")
        return apply_text_transforms(line, settings)

    # Восстанавливаем защищённые элементы
    for tag, val in placeholders.items():
        res = re.sub(re.escape(tag), val, res, flags=re.IGNORECASE)

    # Убираем пробелы перед знаками препинания
    res = re.sub(r"\s+([,\.!\?:;])", r"\1", res)
    res = re.sub(r"\?{2,}!", "?!", res)
    res = re.sub(r"!{2,}", "!", res)
    res = re.sub(r"\.{4,}", "...", res)

    # Исправляем регистр предложений
    if settings.get("caps", 1):
        if len(res) > 0 and res[0].islower():
            res = res[0].upper() + res[1:]
        res = re.sub(r"([\.!\?]\s+)([а-яё])", lambda m: m.group(1) + m.group(2).upper(), res)

    return apply_text_transforms(res, settings)


def fix_punctuation(text: str, settings: dict = None) -> str:
    """Возвращает текст с расставленными знаками препинания и регистром.
    Бережно сохраняет переносы строк, ссылки, юзернеймы и разметку.
    """
    if not text:
        return text

    settings = settings or {}
    if not settings.get("enabled", 1):
        return text

    # Если знаки препинания выключены пользователем, не вызываем нейросеть
    # и не трогаем запятые/точки, сохраняя авторский текст
    if not settings.get("punct", 1):
        res = text
        if settings.get("caps", 1):
            if len(res) > 0 and res[0].islower():
                res = res[0].upper() + res[1:]
            res = re.sub(r"([\.!\?]\s+)([а-яёА-ЯЁ])", lambda m: m.group(1) + m.group(2).upper(), res)
        return apply_text_transforms(res, settings)

    # Обработка многострочного текста с сохранением абзацев
    if "\n" in text:
        lines = text.split("\n")
        fixed_lines = [_fix_single_line(l, settings) for l in lines]
        return "\n".join(fixed_lines)

    return _fix_single_line(text, settings)
