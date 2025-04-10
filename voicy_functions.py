import os
from datetime import datetime
from io import BytesIO,FileIO

import subprocess
import logging
import gspread
import openai

from google.oauth2 import service_account
from google.cloud import speech_v1 as speech, storage
from google.oauth2.service_account import Credentials
from googleapiclient.errors import HttpError
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload


logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)

# --- Остальные функции (authenticate, download_file_from_google_drive, etc.) остаются без изменений ---
def authenticate(CREDENTIALS_FILE, SCOPES):
    """Аутентификация и создание сервисных объектов."""
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    drive_service = build('drive', 'v3', credentials=creds)
    sheets_service = build('sheets', 'v4', credentials=creds)
    docs_service  = build('docs', 'v1', credentials=creds)
    speech_client = speech.SpeechClient(credentials=creds)
    storage_client = storage.Client(credentials=creds)
    gc = gspread.service_account(filename=CREDENTIALS_FILE, scopes=SCOPES)
    return drive_service, sheets_service, docs_service, speech_client, storage_client, gc

def download_file_from_google_drive(file_id, destination_path, credentials_path):
    """
    Скачивает файл с Google Drive по его идентификатору.
    Returns True on success, False on failure.
    """
    try:
        credentials = service_account.Credentials.from_service_account_file(credentials_path)
        service = build('drive', 'v3', credentials=credentials)
        request = service.files().get_media(fileId=file_id)
        logger.info(f"Начало скачивания файла {file_id} в {destination_path}")
        # Ensure directory exists
        os.makedirs(os.path.dirname(destination_path), exist_ok=True)
        fh = FileIO(destination_path, 'wb')
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                 logger.info(f"Скачивание {int(status.progress() * 100)}% завершено.")
        logger.info(f"Файл {file_id} успешно скачан в {destination_path}.")
        return True # Успех
    except HttpError as error:
        logger.error(f"Ошибка HttpError при скачивании файла {file_id}: {error}")
        return False # Неудача
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при скачивании файла {file_id}: {e}")
        # Попытка удалить неполный файл, если он был создан
        if os.path.exists(destination_path):
            try:
                os.remove(destination_path)
                logger.info(f"Удален частично скачанный файл: {destination_path}")
            except OSError as remove_error:
                logger.error(f"Не удалось удалить частично скачанный файл {destination_path}: {remove_error}")
        return False # Неудача

# --- ИЗМЕНЕНА: convert_mp4_to_wav (v2 - Стерео) ---
def convert_mp4_to_wav(input_path, output_path):
    """
    Converts an input media file (like MP4) to a stereo WAV audio file using ffmpeg.
    Returns True on success, False on failure.
    """
    if os.path.exists(output_path):
        try:
            os.remove(output_path)
            logger.info(f"Удален существующий файл: {output_path}")
        except OSError as e:
            logger.error(f"Не удалось удалить существующий файл {output_path}: {e}")

    try:
        command = [
            'ffmpeg',
            '-i', input_path,
            # '-ac', '1',             # УБРАНО: Оставляем исходное количество каналов (стерео)
            '-ar', '16000',          # Set sample rate to 16000 Hz
            '-acodec', 'pcm_s16le',  # Use PCM S16LE codec for WAV
            '-vn',                   # Disable video
            '-y',                    # Overwrite output file without asking
            output_path
        ]
        logger.info(f"Запуск ffmpeg для конвертации {input_path} в СТЕРЕО {output_path}")
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=600)

        if result.returncode != 0:
            logger.error(f"ffmpeg завершился с кодом ошибки {result.returncode} для файла {input_path}.")
            logger.error(f"ffmpeg stderr: {result.stderr}")
            logger.error(f"ffmpeg stdout: {result.stdout}")
            return False

        if not os.path.exists(output_path):
            logger.error(f"Конвертация завершилась, но выходной файл {output_path} не найден!")
            logger.error(f"ffmpeg stderr: {result.stderr}")
            logger.error(f"ffmpeg stdout: {result.stdout}")
            return False
        if os.path.getsize(output_path) == 0:
             logger.error(f"Конвертация завершилась, но выходной файл {output_path} имеет нулевой размер!")
             logger.error(f"ffmpeg stderr: {result.stderr}")
             logger.error(f"ffmpeg stdout: {result.stdout}")
             try:
                 os.remove(output_path)
             except OSError:
                 pass
             return False

        logger.info(f"Аудио {input_path} успешно конвертировано в СТЕРЕО {output_path}.")
        return True

    except subprocess.TimeoutExpired:
        logger.error(f"Превышен таймаут ffmpeg при конвертации {input_path}.")
        return False
    except subprocess.CalledProcessError as e:
        logger.error(f"Ошибка CalledProcessError при конвертации аудио с помощью ffmpeg: {e}")
        if e.stderr: logger.error(f"ffmpeg stderr: {e.stderr}")
        if e.stdout: logger.error(f"ffmpeg stdout: {e.stdout}")
        return False
    except FileNotFoundError:
        logger.error("Критическая ошибка: Команда ffmpeg не найдена. Убедитесь, что ffmpeg установлен и добавлен в PATH.")
        return False
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при конвертации {input_path}: {e}")
        return False

# --- ИЗМЕНЕНА: transcribe_audio_file (v4 - Стерео, latest_long) ---
def transcribe_audio_file(CLOUD_STORAGE_BUCKET_NAME, audio_path, credentials_path, min_speakers=2, max_speakers=6):
    """
    Транскрибирует СТЕРЕО аудиофайл (WAV) с использованием Google Cloud Speech-to-Text (модель latest_long),
    распознает разных спикеров (diarization) и возвращает диалог.

    Args:
        CLOUD_STORAGE_BUCKET_NAME (str): Имя бакета Google Cloud Storage.
        audio_path (str): Путь к локальному СТЕРЕО аудиофайлу WAV (16000 Hz рекомендуется).
        credentials_path (str): Путь к файлу учетных данных сервисного аккаунта.
        min_speakers (int): Минимальное ожидаемое количество спикеров.
        max_speakers (int): Максимальное ожидаемое количество спикеров.

    Returns:
        tuple: (dialogue_text, duration_minutes)
               dialogue_text (str): Расшифрованный диалог или сообщение об ошибке/None.
               duration_minutes (float): Длительность в минутах или 0.0 при ошибке.
    """
    duration_minutes = 0.0
    # --- Определение длительности с помощью ffprobe (без изменений) ---
    try:
        if not os.path.exists(audio_path):
            logger.error(f"Файл {audio_path} не найден перед вызовом ffprobe.")
            raise FileNotFoundError(f"Файл {audio_path} не найден")

        command = [
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', audio_path
        ]
        logger.info(f"Запуск ffprobe для определения длительности файла: {audio_path}")
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=60)

        if result.returncode != 0:
            logger.warning(f"ffprobe не смог определить длительность файла {audio_path}. Код возврата: {result.returncode}")
            logger.warning(f"ffprobe stderr: {result.stderr}")
        else:
            try:
                duration_seconds = float(result.stdout.strip())
                duration_minutes = duration_seconds / 60
                logger.info(f"Длительность аудиофайла {audio_path}: {duration_minutes:.2f} минут.")
            except ValueError:
                logger.warning(f"Не удалось преобразовать вывод ffprobe ('{result.stdout.strip()}') в число для файла {audio_path}.")
    except FileNotFoundError as e:
         logger.warning(f"Пропуск определения длительности из-за отсутствия файла: {e}")
    except subprocess.TimeoutExpired:
        logger.warning(f"Превышен таймаут ffprobe для файла {audio_path}. Длительность не определена.")
    except Exception as e:
        logger.warning(f"Ошибка при определении длительности аудиофайла {audio_path} с помощью ffprobe: {e}")

    # --- Транскрипция с распознаванием спикеров (Стерео, latest_long) ---
    credentials = service_account.Credentials.from_service_account_file(credentials_path)
    speech_client = speech.SpeechClient(credentials=credentials)
    storage_client = storage.Client(credentials=credentials)
    bucket = storage_client.bucket(CLOUD_STORAGE_BUCKET_NAME)
    blob_name = os.path.basename(audio_path)
    blob = bucket.blob(blob_name)
    gcs_uri = f"gs://{CLOUD_STORAGE_BUCKET_NAME}/{blob_name}"

    try:
        if not os.path.exists(audio_path):
             logger.error(f"Ошибка: Попытка загрузить несуществующий файл {audio_path} в GCS.")
             return "Ошибка: Исходный аудиофайл не найден для транскрипции.", duration_minutes

        logger.info(f"Загрузка {audio_path} в {gcs_uri}...")
        blob.upload_from_filename(audio_path)
        logger.info(f"Аудиофайл успешно загружен в Cloud Storage: {gcs_uri}")

        audio_content = speech.RecognitionAudio(uri=gcs_uri)

        # --- Конфигурация с включенной диаризацией (v4 - Стерео, latest_long) ---
        diarization_config = speech.SpeakerDiarizationConfig(
            enable_speaker_diarization=True,
            min_speaker_count=min_speakers,
            max_speaker_count=max_speakers,
        )

        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code="ru-RU",
            model='latest_long', # <-- ДОБАВЛЕНО: Используем последнюю модель для длинных аудио
            audio_channel_count=2,
            enable_separate_recognition_per_channel=True,
            enable_automatic_punctuation=True,
            diarization_config=diarization_config,
            enable_word_time_offsets=True
        )
        # -------------------------------------------

        logger.info(f"Запуск асинхронной транскрипции СТЕРЕО (модель latest_long) с диаризацией для {gcs_uri}...")
        operation = speech_client.long_running_recognize(config=config, audio=audio_content)
        logger.info("Ожидание завершения операции транскрипции...")
        response = operation.result(timeout=3600) # Consider adjusting timeout based on audio length

        # --- Обработка результата с диаризацией (без изменений в логике) ---
        if response.results:
            final_result = response.results[-1]
            if final_result.alternatives:
                alternative = final_result.alternatives[0]
                if alternative.words:
                    logger.debug("Начало обработки слов для диаризации...")
                    dialogue = []
                    current_speaker_tag = None
                    current_line = ""
                    word_count_with_tags = 0

                    for i, word_info in enumerate(alternative.words):
                        speaker_tag = getattr(word_info, 'speaker_tag', None)

                        if i < 10 or speaker_tag != current_speaker_tag:
                             logger.debug(f"Слово: '{word_info.word}', Тег спикера: {speaker_tag}")

                        if speaker_tag is not None:
                             word_count_with_tags += 1

                        if speaker_tag != current_speaker_tag:
                            if current_line:
                                dialogue.append(f"Спикер {current_speaker_tag}: {current_line.strip()}")
                            current_speaker_tag = speaker_tag if speaker_tag is not None else "Неизвестный"
                            current_line = word_info.word + " "
                        else:
                            current_line += word_info.word + " "

                    if current_line:
                         tag_to_use = current_speaker_tag if current_speaker_tag is not None else "Неизвестный"
                         dialogue.append(f"Спикер {tag_to_use}: {current_line.strip()}")

                    logger.debug(f"Обработка слов завершена. Слов с тегами: {word_count_with_tags} из {len(alternative.words)}")

                    if dialogue and word_count_with_tags > 0:
                        dialogue_text = "\n".join(dialogue)
                        logger.info(f"Транскрипция с диаризацией для {audio_path} завершена успешно.")
                        return dialogue_text, duration_minutes
                    else:
                         logger.warning(f"Диаризация для {audio_path} не дала результата (нет слов с тегами или диалог пуст), возвращаем общий транскрипт.")
                         return alternative.transcript, duration_minutes

                elif alternative.transcript:
                     logger.warning(f"Диаризация для {audio_path} не дала результата (нет информации по словам), возвращаем общий транскрипт.")
                     return alternative.transcript, duration_minutes

        logger.warning(f"Транскрипция для {audio_path} не дала результатов.")
        return "Не удалось распознать речь.", duration_minutes
        # -------------------------------------------

    except Exception as e:
        logger.error(f"Ошибка во время транскрипции файла {audio_path}: {e}", exc_info=True)
        return f"Ошибка транскрипции: {e}", duration_minutes
    finally:
        # Очистка: удаляем загруженный файл из Cloud Storage
        try:
            if storage_client and blob.exists():
                blob.delete()
                logger.info(f"Файл {gcs_uri} удален из Cloud Storage.")
        except Exception as e:
             logger.warning(f"Не удалось удалить файл {gcs_uri} из Cloud Storage: {e}")


# --- Остальные функции (openai_summarizer, read_mapping_sheet, find_media_files_on_drive, write_to_google_sheet, get_first_column_values, read_google_doc, find_new_media_files) остаются как были ---
# ... (вставьте сюда остальные функции без изменений) ...
def openai_summarizer(openai_api_key, transcribed_text, prompt, model="gpt-4o-2024-08-06"):
    """Генерация текста с помощью OpenAI."""
    openai.api_key = openai_api_key
    try:
        response = openai.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": transcribed_text}
            ]
        )
        model_answer = response.choices[0].message.content
        input_tokens = response.usage.prompt_tokens
        output_tokens = response.usage.completion_tokens
        return  model_answer, input_tokens, output_tokens
    except Exception as e:
        logger.error(f"Ошибка при работе с OpenAI API: {e}")
        # Возвращаем None и токены 0, чтобы обозначить ошибку
        return None, 0, 0

def read_mapping_sheet(gc, spreadsheet_name_or_id, worksheet_name=None):
    """
    Читает таблицу маппинга (email, folder_id, chat_id).
    Эта функция используется для получения списка папок для сканирования.

    Args:
        gc: Авторизованный клиент gspread.
        spreadsheet_name_or_id (str): Имя или ID Google Таблицы с маппингом.
        worksheet_name (str, optional): Имя листа. По умолчанию первый лист.

    Returns:
        list: Список словарей [ {'email': '...', 'folder_id': '...', 'chat_id': '...'}, ... ]
              или пустой список в случае ошибки или отсутствия данных.
    """
    mappings = []
    try:
        logger.info(f"Чтение таблицы маппинга папок: {spreadsheet_name_or_id}")
        # Пытаемся открыть по имени, если не ID
        try:
            spreadsheet = gc.open(spreadsheet_name_or_id)
        except gspread.exceptions.SpreadsheetNotFound:
             # Пытаемся открыть по ID
            spreadsheet = gc.open_by_key(spreadsheet_name_or_id)

        if worksheet_name:
            worksheet = spreadsheet.worksheet(worksheet_name)
        else:
            worksheet = spreadsheet.sheet1 # Используем первый лист по умолчанию

        records = worksheet.get_all_records()
        for record in records:
            if ('folder_id' in record and record['folder_id'] and
                'chat_id' in record and record['chat_id']):
                mappings.append({
                    'email': record.get('email', ''),
                    'folder_id': str(record['folder_id']).strip(),
                    'chat_id': str(record['chat_id']).strip()
                })
            else:
                logger.warning(f"Пропуск строки в таблице маппинга из-за отсутствия folder_id/chat_id: {record}")

        logger.info(f"Найдено {len(mappings)} валидных записей в таблице маппинга папок.")
        return mappings

    except gspread.exceptions.APIError as e:
         logger.error(f"Ошибка API Google Sheets при чтении маппинга папок '{spreadsheet_name_or_id}': {e}")
         return []
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при чтении таблицы маппинга папок '{spreadsheet_name_or_id}': {e}")
        return []

def find_media_files_on_drive(drive_service, folder_id, media_mime_types):
    """
    Находит все медиафайлы в **указанной папке** на Google Диске.
    Используется для поиска файлов ВНУТРИ папок из маппинга.

    Args:
        drive_service: Авторизованный клиент Google Drive API.
        folder_id (str): ID папки Google Drive для поиска.
        media_mime_types (list): Список MIME-типов для поиска.

    Returns:
        list: Список словарей [{'id': ..., 'name': ..., 'mimeType': ...}] или None/пустой список.
    """
    media_files = []
    logger.info(f"Поиск медиафайлов в папке ID: {folder_id}")
    try:
        for mime_type in media_mime_types:
            query = f"'{folder_id}' in parents and mimeType='{mime_type}' and trashed = false"
            page_token = None
            while True:
                try:
                    response = drive_service.files().list(
                        q=query,
                        spaces='drive',
                        fields='nextPageToken, files(id, name, mimeType)',
                        pageToken=page_token
                    ).execute()

                    files = response.get('files', [])
                    if files:
                        media_files.extend([{'id': f['id'], 'name': f['name'], 'mimeType': f['mimeType']} for f in files])
                        logger.info(f"Найдено {len(files)} файлов типа {mime_type} в папке {folder_id} на этой странице.")

                    page_token = response.get('nextPageToken')
                    if not page_token:
                        break

                except HttpError as error:
                     logger.error(f"Ошибка HttpError при запросе файлов типа {mime_type} в папке {folder_id}: {error}")
                     break
                except Exception as e:
                    logger.error(f"Непредвиденная ошибка при запросе файлов типа {mime_type} в папке {folder_id}: {e}")
                    break

        logger.info(f"Всего найдено {len(media_files)} медиафайлов в папке {folder_id}.")
        return media_files

    except Exception as e:
        logger.error(f"Критическая ошибка при поиске медиафайлов в папке {folder_id}: {e}")
        return None

def find_new_media_files(drive_files, spreadsheet_ids):
  """
  Сравнивает id файлов из Google Диска со списком id из Google Таблицы.

  Args:
    drive_files: Список словарей с информацией о файлах Google Диска,
                 где каждый словарь содержит ключи 'id' и 'name'.
    spreadsheet_ids: Список строк, представляющих id файлов из Google Таблицы.

  Returns:
    Список словарей, где каждый словарь содержит 'id' и 'name' файлов,
    которые есть на Google Диске, но отсутствуют в Google Таблице.
  """
  new_files = []
  spreadsheet_ids_set = set(spreadsheet_ids)  # Преобразуем список в множество для быстрого поиска

  for file_info in drive_files:
    if file_info['id'] not in spreadsheet_ids_set:
      new_files.append({'id': file_info['id'], 'name': file_info['name']})

  return new_files


def write_to_google_sheet(gc, spreadsheet_id, meeting_id, meeting_name, transcribation_text,
                          summary, speech_minutes, input_openai, output_openai, source_identifier=None): # Добавлен source_identifier
    """
    Записывает данные в Google Таблицу, используя её ID.
    Предполагается, что это ОСНОВНАЯ таблица для логирования обработанных файлов.
    """
    try:
        # Открываем таблицу по ID
        spreadsheet = gc.open_by_key(spreadsheet_id)
        worksheet = spreadsheet.sheet1
        header = [
            "meeting_id",
            "meeting_name",
            "transcribation_text",
            "summary",
            "speech_minutes",
            "input_openai",
            "output_openai",
            "source_identifier", # Добавлено новое поле
            "date_processed",
        ]

        current_header = worksheet.row_values(1)
        if current_header != header:
             if not current_header:
                 worksheet.update('A1', [header])
             else:
                 logger.warning(f"Заголовок в таблице с ID '{spreadsheet_id}' не совпадает с ожидаемым. Добавляю данные без обновления заголовка.")

        current_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row_data = [
            meeting_id,
            meeting_name,
            transcribation_text if transcribation_text else "N/A", # Защита от None
            summary if summary else "N/A", # Защита от None
            f"{speech_minutes:.2f}" if isinstance(speech_minutes, (int, float)) else str(speech_minutes), # Форматируем минуты
            input_openai,
            output_openai,
            source_identifier if source_identifier else '', # Добавляем идентификатор источника
            current_date
        ]
        worksheet.append_row(row_data, value_input_option='USER_ENTERED')
        logger.info(f"Данные по {meeting_id} успешно записаны в Google Таблицу с ID '{spreadsheet_id}'.")
    except gspread.exceptions.APIError as e:
        logger.error(f"Ошибка API Google Sheets при записи в таблицу с ID '{spreadsheet_id}': {e}")
    except Exception as e:
        logger.error(f"Ошибка при записи в Google Таблицу с ID '{spreadsheet_id}': {e}")

def get_first_column_values(gc, spreadsheet_id, worksheet_name=None):
    """
    Возвращает список значений из первого столбца Google Таблицы, используя ID таблицы.
    Используется для чтения ID уже обработанных файлов из ОСНОВНОЙ таблицы логов.

    Args:
        gc: Объект Google Sheets client.
        spreadsheet_id (str): ID Google Таблицы.
        worksheet_name (str, optional): Название листа. Если не указано, используется первый лист.

    Returns:
        list: Список значений из первого столбца, или None в случае ошибки.
    """
    try:
        # Открываем таблицу по ID
        spreadsheet = gc.open_by_key(spreadsheet_id)
        logger.info(f"Чтение первого столбца из таблицы с ID: {spreadsheet_id}")
        worksheet = spreadsheet.worksheet(worksheet_name) if worksheet_name else spreadsheet.sheet1

        values = worksheet.col_values(1)
        return values
    except gspread.exceptions.APIError as e:
        logger.error(f"Ошибка API Google Sheets при чтении из таблицы с ID {spreadsheet_id}: {e}")
        return None
    except Exception as e:
        # Добавим логирование конкретной ошибки сети, если возможно
        if isinstance(e, ConnectionError) or 'RemoteDisconnected' in str(e):
             logger.error(f"Ошибка сети при чтении из Google Таблицы с ID {spreadsheet_id}: {e}")
        else:
             logger.error(f"Непредвиденная ошибка при чтении из Google Таблицы с ID {spreadsheet_id}: {e}")
        return None

def read_google_doc(docs_service, document_id):
    """
    Читает содержимое Google Doc по его ID и возвращает его как строку.
    Используется для чтения промпта OpenAI из КОНКРЕТНОГО документа.
    """
    try:
        logger.info(f"Чтение Google Doc с ID: {document_id}")
        document = docs_service.documents().get(documentId=document_id).execute()
        content = ""
        # Проверяем наличие 'body' и 'content' перед доступом
        body = document.get('body')
        if body:
            doc_content = body.get('content', [])
            for element in doc_content:
                if 'paragraph' in element:
                    paragraph = element.get('paragraph')
                    if paragraph:
                        elements = paragraph.get('elements', [])
                        for paragraph_element in elements:
                            if 'textRun' in paragraph_element:
                                text_run = paragraph_element.get('textRun')
                                if text_run:
                                    content += text_run.get('content', '')
        if content:
            logger.info(f"Документ {document_id} успешно прочитан.")
            return content
        else:
            logger.warning(f"Документ {document_id} пуст или не содержит текстовых элементов.")
            return "" # Возвращаем пустую строку, а не None
    except HttpError as err:
        logger.error(f"Ошибка HttpError при чтении Google Doc ID {document_id}: {err}")
        return None # Ошибка чтения
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при чтении Google Doc ID {document_id}: {e}")
        return None # Ошибка чтения

