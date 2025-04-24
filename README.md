# Voicy Project: Автоматическая транскрибация и саммаризация встреч
![image](https://github.com/user-attachments/assets/25ef5862-1c93-4439-a804-7deb99eb0cd5)

## Описание

Этот проект представляет собой автоматизированный сервис, который:
1.  Отслеживает появление новых аудио/видео файлов в указанных папках Google Drive.
2.  Скачивает новые файлы и конвертирует их в формат WAV (сохраняя стерео).
3.  Транскрибирует аудио с использованием Google Cloud Speech-to-Text, применяя функцию распознавания спикеров (диаризацию) и используя последнюю доступную модель (`latest_long`).
4.  Генерирует краткое содержание (саммари) транскрипта с помощью OpenAI API (модель настраивается).
5.  Отправляет результат (саммари и статус обработки) в соответствующий чат Telegram.
6.  Записывает подробные логи обработки (включая полный транскрипт, саммари, метаданные) в Google Таблицу.

Проект предназначен для работы в качестве фонового сервиса на виртуальной машине Google Cloud (GCE).

## Возможности

* Мониторинг нескольких папок Google Drive.
* Поддержка различных аудио/видео форматов (через `ffmpeg`).
* Конвертация в стерео WAV.
* Транскрибация речи с использованием Google Cloud Speech-to-Text.
* Распознавание и разделение спикеров (диаризация).
* Использование последней модели Google для распознавания (`latest_long`).
* Саммаризация транскриптов с помощью OpenAI (GPT-4o или другая модель).
* Настраиваемый промпт для OpenAI через Google Документ.
* Отправка результатов в Telegram (личные чаты, группы, каналы).
* Детальное логирование в Google Таблицу.
* Автоматический перезапуск и работа в фоновом режиме (через `systemd`).

## Технологии

* **Язык:** Python 3
* **Основные библиотеки:**
    * `google-api-python-client`, `google-auth-oauthlib`, `google-auth-httplib2`, `oauth2client` (для Google Drive, Docs, Sheets API)
    * `google-cloud-speech` (для транскрибации)
    * `google-cloud-storage` (для временного хранения аудио)
    * `gspread` (для работы с Google Sheets)
    * `openai` (для саммаризации)
    * `python-telegram-bot` (для отправки сообщений)
    * `asyncio` (для асинхронной работы)
* **Внешние сервисы:**
    * Google Cloud Platform (Drive, Docs, Sheets, Speech-to-Text, Storage, Compute Engine)
    * OpenAI API
    * Telegram Bot API
* **Утилиты:** `ffmpeg`, `ffprobe` (для обработки аудио/видео)
* **Окружение:** Linux (рекомендуется Debian/Ubuntu на GCE), `systemd` (для управления сервисом)

## Установка и настройка

### Предварительные требования

* Аккаунт Google Cloud с включенной оплатой (некоторые API платные).
* Установленный `gcloud` SDK на локальном компьютере (или использование Cloud Shell).
* Python 3.10 или выше.
* `pip` и `venv`.
* Установленный `ffmpeg`.
* Аккаунт OpenAI с API ключом.
* Созданный Telegram бот и его API токен.

### Настройка Google Cloud

1.  **Создайте проект** в Google Cloud Console (если еще нет).
2.  **Включите необходимые API:**
    * Google Drive API
    * Google Docs API
    * Google Sheets API
    * Cloud Speech-to-Text API
    * Cloud Storage API
    * Compute Engine API (для VM)
3.  **Создайте Сервисный Аккаунт:**
    * Перейдите в IAM & Admin -> Service Accounts.
    * Создайте новый сервисный аккаунт.
    * Предоставьте ему необходимые роли (как минимум: Editor или более гранулярные роли для доступа к Drive, Sheets, Docs, Storage, Speech-to-Text).
    * Создайте ключ для этого сервисного аккаунта (формат JSON) и скачайте его. Переименуйте файл ключа, например, в `service_account.json`. **Храните этот файл надежно!**
    * Запомните **email** созданного сервисного аккаунта (вида `...@...gserviceaccount.com`). Он понадобится для предоставления доступа к папкам Google Drive.
4.  **Создайте Google Cloud Storage Bucket:**
    * Перейдите в Cloud Storage -> Buckets.
    * Создайте новый бакет (bucket) для временного хранения аудиофайлов перед транскрибацией. Запомните его имя.
5.  **Создайте Google Таблицу для логов:**
    * Создайте новую Google Таблицу.
    * Скопируйте ее **ID** из URL (длинная строка символов между `/d/` и `/edit`).
    * Предоставьте доступ на редактирование этой таблицы вашему **сервисному аккаунту** (укажите его email).
6.  **Создайте Google Таблицу для маппинга:**
    * Создайте еще одну Google Таблицу.
    * На первом листе создайте столбцы с заголовками (в первой строке): `folder_id`, `chat_id`, `email` (опционально).
    * Запомните **имя или ID** этой таблицы.
    * Предоставьте доступ на редактирование этой таблицы вашему **сервисному аккаунту**.
7.  **Создайте Google Документ для промпта:**
    * Создайте новый Google Документ.
    * Напишите в нем системный промпт, который будет использоваться OpenAI для саммаризации транскрипта.
    * Скопируйте **ID** этого документа из URL.
    * Предоставьте доступ на чтение (или редактирование) этого документа вашему **сервисному аккаунту**.
8.  **Создайте VM (GCE):**
    * Создайте инстанс виртуальной машины Linux (рекомендуется Debian или Ubuntu). Пример: `instance-20250407-105636` в `asia-east1`.
    * Убедитесь, что у VM есть доступ к необходимым API Google Cloud (обычно настраивается через 'Access scopes' при создании VM или через привязанный сервисный аккаунт).

### Настройка OpenAI и Telegram

1.  **OpenAI:** Получите ваш API ключ со страницы OpenAI API keys.
2.  **Telegram:**
    * Создайте бота через `@BotFather` в Telegram.
    * Получите **API токен** вашего бота.

### Настройка проекта

1.  **Скопируйте файлы проекта:** Перенесите файлы `main.py`, `voicy_functions.py` и скачанный ключ сервисного аккаунта (`service_account.json`) на вашу VM (например, в папку `~/voicy_project`).
2.  **Создайте `config.py`:** Создайте файл `config.py` в папке проекта на VM со следующим содержимым, подставив ваши значения:

    ```python
    # config.py
    SERVICE_ACCOUNT_FILE = 'service_account.json' # Или полный путь к файлу ключа
    SCOPES = [
        '[https://www.googleapis.com/auth/drive](https://www.googleapis.com/auth/drive)',
        '[https://www.googleapis.com/auth/spreadsheets](https://www.googleapis.com/auth/spreadsheets)',
        '[https://www.googleapis.com/auth/documents](https://www.googleapis.com/auth/documents)',
        '[https://www.googleapis.com/auth/cloud-platform](https://www.googleapis.com/auth/cloud-platform)' # Общий доступ, может потребоваться для Speech/Storage
    ]
    TELEGRAM_API_TOKEN = 'ВАШ_TELEGRAM_BOT_TOKEN'
    openai_api_key = 'ВАШ_OPENAI_API_KEY'
    CLOUD_STORAGE_BUCKET_NAME = 'имя-вашего-gcs-бакета'
    TEMP_FOLDER_PATH = './temp_audio' # Временная папка на VM

    # --- ID ваших Google Документов/Таблиц ---
    SPREADSHEET_ID = 'ID_ВАШЕЙ_ТАБЛИЦЫ_ЛОГОВ'
    PROMPT_DOCUMENT_ID = 'ID_ВАШЕГО_ДОКУМЕНТА_С_ПРОМПТОМ'
    MAPPING_SPREADSHEET_NAME = 'ИМЯ_ИЛИ_ID_ТАБЛИЦЫ_МАППИНГА' # Укажите имя или ID

    # --- Настройки обработки ---
    # Пример MIME-типов для поиска в Google Drive
    media_mime_types = [
        'video/mp4', 'audio/mpeg', 'audio/ogg', 'audio/wav',
        'audio/x-m4a', 'video/quicktime', 'video/x-msvideo',
        'audio/flac', 'audio/aac'
        ]
    OPENAI_MODEL = "gpt-4o" # Модель OpenAI для саммаризации
    ```

3.  **Создайте `requirements.txt`:** Создайте файл `requirements.txt` в папке проекта:

    ```txt
    google-api-python-client
    google-auth-httplib2
    google-auth-oauthlib
    google-cloud-speech
    google-cloud-storage
    gspread
    openai
    python-telegram-bot
    oauth2client
    ```

### Установка на VM (GCE)

1.  **Подключитесь к VM** по SSH.
2.  **Обновите систему:** `sudo apt update && sudo apt upgrade -y`
3.  **Установите зависимости ОС:** `sudo apt install -y python3 python3-pip python3-venv ffmpeg`
4.  **Перейдите в папку проекта:** `cd ~/voicy_project`
5.  **Создайте и активируйте виртуальное окружение:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```
6.  **Установите Python-зависимости:** `pip install -r requirements.txt`
7.  **Настройте права на ключ:** `chmod 600 service_account.json`
8.  **Настройте сервис `systemd`:**
    * Создайте файл `sudo nano /etc/systemd/system/voicybot.service`.
    * Вставьте конфигурацию (заменив `[ВАШ_ПОЛЬЗОВАТЕЛЬ_НА_VM]`):
        ```ini
        [Unit]
        Description=Voicy Telegram Bot Service
        After=network.target

        [Service]
        User=[ВАШ_ПОЛЬЗОВАТЕЛЬ_НА_VM]
        WorkingDirectory=/home/[ВАШ_ПОЛЬЗОВАТЕЛЬ_НА_VM]/voicy_project
        ExecStart=/home/[ВАШ_ПОЛЬЗОВАТЕЛЬ_НА_VM]/voicy_project/venv/bin/python3 /home/[ВАШ_ПОЛЬЗОВАТЕЛЬ_НА_VM]/voicy_project/main.py
        Restart=always
        RestartSec=10
        StandardOutput=journal
        StandardError=journal
        SyslogIdentifier=voicybot

        [Install]
        WantedBy=multi-user.target
        ```
    * Сохраните (`Ctrl+X`, `Y`, `Enter`).
    * Перезагрузите `systemd`: `sudo systemctl daemon-reload`
    * Включите автозапуск: `sudo systemctl enable voicybot.service`
    * Запустите сервис: `sudo systemctl start voicybot.service`
    * Проверьте статус: `sudo systemctl status voicybot.service`

## Использование

1.  **Добавление папки для мониторинга:**
    * Попросите пользователя создать папку в Google Drive и дать доступ на редактирование **email вашего сервисного аккаунта**.
    * Получите ID папки и ID целевого Telegram чата.
    * Добавьте новую строку с `folder_id` и `chat_id` в вашу **таблицу маппинга**.
2.  **Работа сервиса:** Сервис `voicybot`, запущенный через `systemd`, будет автоматически проверять папки из таблицы маппинга каждые 30 минут (интервал настраивается в `main.py`). При обнаружении новых файлов он их обработает и отправит результат в соответствующий Telegram чат.
3.  **Просмотр логов:**
    * Операционные логи сервиса: `journalctl -u voicybot.service -f`
    * Логи обработанных файлов: Google Таблица, ID которой указан в `SPREADSHEET_ID`.
