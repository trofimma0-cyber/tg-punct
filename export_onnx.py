"""
Одноразовый скрипт: конвертирует модель RUPunct_small в формат ONNX и
сохраняет токенизатор рядом. Результат — папка onnx_model/, которую
дальше использует punct.py через onnxruntime вместо полного torch.

Зачем: onnxruntime + сконвертированная модель весят и работают заметно
легче, чем torch + transformers с оригинальными весами — важно для
хостинга с ограниченной RAM (например, бесплатные тарифы).

Запусти один раз локально (там, где есть интернет и место на диск):
    pip install optimum[onnxruntime] transformers torch
    python export_onnx.py

Дальше просто скопируй папку onnx_model/ вместе с проектом при деплое.
"""
from optimum.onnxruntime import ORTModelForTokenClassification
from transformers import AutoTokenizer

MODEL_NAME = "RUPunct/RUPunct_small"
OUT_DIR = "onnx_model"


def main():
    print(f"Конвертирую {MODEL_NAME} в ONNX...")
    model = ORTModelForTokenClassification.from_pretrained(MODEL_NAME, export=True)
    tokenizer = AutoTokenizer.from_pretrained(
        MODEL_NAME, strip_accents=False, add_prefix_space=True
    )

    model.save_pretrained(OUT_DIR)
    tokenizer.save_pretrained(OUT_DIR)
    print(f"Готово: модель сохранена в {OUT_DIR}/")


if __name__ == "__main__":
    main()
