"""
Необязательный шаг: заранее скачивает модель RUPunct_small, чтобы первый
реальный запуск бота не тратил время на загрузку. Модель скачивается
автоматически и при первом запуске main.py, так что этот скрипт можно
пропустить — но с ним первое сообщение боту обработается без задержки.
"""
from transformers import pipeline, AutoTokenizer

MODEL_NAME = "RUPunct/RUPunct_small"


def main():
    print(f"Скачиваю модель {MODEL_NAME}...")
    tokenizer = AutoTokenizer.from_pretrained(
        MODEL_NAME, strip_accents=False, add_prefix_space=True
    )
    pipeline("ner", model=MODEL_NAME, tokenizer=tokenizer, aggregation_strategy="first")
    print("Готово: модель скачана и закэширована.")


if __name__ == "__main__":
    main()
