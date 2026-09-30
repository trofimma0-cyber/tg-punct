# Telegram-бот для автоматической расстановки запятых

Бот подключается через встроенную в Telegram функцию **"Автоматизация чатов"**
и редактирует твои сообщения, расставляя пунктуацию моделью **RUPunct** —
лёгким специализированным классификатором токенов (не LLM).

## Локальный запуск (для тестов)

1. `pip install -r requirements.txt`
2. Создай бота у @BotFather (`/newbot`), включи **Business Mode**
   (`/mybots` → бот → Bot Settings → Business Mode → Turn On)
3. `copy .env.example .env`, впиши `BOT_TOKEN`
4. Если нужен VPN для доступа к Telegram API — впиши `PROXY_URL` в `.env`
5. `python main.py`
6. В Telegram: Настройки → Telegram для бизнеса → Автоматизация чатов →
   вставь `@username` бота → выбери чаты

## Деплой на Render.com (Бесплатно, $0 / мес)

Бот полностью оптимизирован под бесплатный тариф Render Free (512 MB RAM):
инференс переведён на чистый `onnxruntime`, бот занимает всего **~189 MB RAM** и снабжён встроенным keep-alive сервером для порта `$PORT`.

### Шаг 1 — Залей проект на GitHub
1. Создай репозиторий на [GitHub](https://github.com/new).
2. Запушь папку проекта (включая папку `onnx_model/`, `main.py`, `punct.py`, `render.yaml`, `Procfile`, `requirements.txt`).

### Шаг 2 — Создай Web Service на Render
1. Зайди на [Render.com](https://render.com) и нажми **New +** → **Web Service**.
2. Подключи свой GitHub-репозиторий.
3. Заполни базовые поля (или они подтянутся из `render.yaml`):
   * **Name:** `tg-punct-bot`
   * **Region:** `Frankfurt (EU Central)`
   * **Branch:** `main` (или `master`)
   * **Runtime:** `Python 3`
   * **Build Command:** `pip install -r requirements.txt`
   * **Start Command:** `python main.py`
   * **Instance Type:** **Free** (0.1 CPU / 512 MB RAM)
4. В разделе **Environment Variables** (Переменные окружения) добавь:
   * `BOT_TOKEN` — токен от @BotFather.
   * `ADMIN_ID` — твой числовой Telegram ID.
   * `PYTHON_VERSION` — `3.11.9`
5. Нажми **Deploy Web Service**. Сборка займёт около 1 минуты.

### Шаг 3 — Настрой бесплатный авто-пинг (чтобы бот не засыпал)
Render Free засыпает через 15 минут неактивности. В боте уже встроен легковесный эндпоинт `/health`:
1. Скопируй адрес своего сервиса из Render (например: `https://tg-punct-bot-xyz.onrender.com`).
2. Зарегистрируйся на [cron-job.org](https://cron-job.org) или [UptimeRobot](https://uptimerobot.com) (оба бесплатные).
3. Создай проверку (GET-запрос) на адрес:
   `https://tg-punct-bot-xyz.onrender.com/health`
4. Поставь интервал **каждые 10 минут**.
Теперь бот никогда не уснёт и будет работать 24/7 абсолютно бесплатно!

## Настройка исключений

В `.env` или переменных окружения Amvera:
```
EXCLUDE_CHATS=work_chat,some_username
```

## Многопользовательский режим

Бот уже поддерживает работу от нескольких Business-подключений одновременно
(каждое отслеживается по `business_connection_id`, владелец сверяется через
`context.bot_data`). Обработка сообщений идёт через общую очередь
(`asyncio.Lock`) — модель в памяти одна, сообщения обрабатываются одно за
другим быстро (RUPunct — лёгкий классификатор, не LLM), но не параллельно.
При очень большой нагрузке (десятки одновременных сообщений) возможна
недолгая очередь — для типичного личного использования это не заметно.

## Структура проекта

```
tg-punct/
├── main.py            # бот: слушает business_message, редактирует
├── punct.py           # обёртка над RUPunct (ONNX или transformers)
├── export_onnx.py      # (для деплоя) конвертация модели в ONNX
├── download_model.py  # (опционально) заранее скачать transformers-модель
├── requirements.txt
├── amvera.yml          # конфиг деплоя для Amvera
├── .env.example
└── README.md
```
