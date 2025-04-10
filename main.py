import os
import config as conf
import logging
import asyncio
import voicy_functions as voicy
from telegram import Bot
import time # Для возможной задержки между обработкой папок

logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)

# Инициализация бота и сервисов Google
try:
    bot = Bot(token=conf.TELEGRAM_API_TOKEN)
    drive_service, sheets_service, docs_service, speech_client, storage_client, gc = voicy.authenticate(
        conf.SERVICE_ACCOUNT_FILE, conf.SCOPES)
    logger.info("Аутентификация и инициализация сервисов Google прошла успешно.")
except Exception as auth_error:
    logger.critical(f"Критическая ошибка при аутентификации или инициализации сервисов: {auth_error}")
    # Здесь можно завершить работу скрипта, если аутентификация не удалась
    exit() # Или использовать sys.exit()

media_mime_types = conf.media_mime_types

async def check_and_process_all_mappings():
    """
    Асинхронно проверяет папки Google Drive согласно маппингу, обрабатывает новые файлы
    и отправляет результаты в соответствующие Telegram чаты.
    """
    start_time = time.time()
    logger.info("Начало цикла проверки папок по маппингу...")

    try:
        # 1. Получаем список всех уже обработанных файлов ОДИН РАЗ за цикл
        processed_media_ids = voicy.get_first_column_values(gc, conf.SPREADSHEET_ID)
        if processed_media_ids is None:
            logger.error("Не удалось получить список обработанных ID из основной таблицы. Пропуск цикла.")
            return # Выходим, если не можем получить ID

        # 2. Получаем все маппинги "папка-чат"
        mapping_entries = voicy.read_mapping_sheet(gc, conf.MAPPING_SPREADSHEET_ID) # Используем имя из конфига
        if not mapping_entries:
            logger.warning("Таблица маппинга пуста или не найдена. Нет папок для проверки.")
            return

        print(mapping_entries)

        # 3. Итерируемся по каждому маппингу
        for mapping in mapping_entries:
            current_folder_id = mapping['folder_id']
            current_chat_id = mapping['chat_id']
            current_email = mapping.get('email', 'N/A') # Получаем email для логирования

            logger.info(f"--- Обработка маппинга для папки: {current_folder_id} (Email: {current_email}, ChatID: {current_chat_id}) ---")

            try:
                # 4. Ищем медиафайлы в ТЕКУЩЕЙ папке
                media_in_folder = voicy.find_media_files_on_drive(drive_service, current_folder_id, media_mime_types=media_mime_types)

                if media_in_folder is None:
                    logger.warning(f"Произошла ошибка при поиске файлов в папке {current_folder_id}, переход к следующему маппингу.")
                    continue # Переходим к следующему маппингу

                if not media_in_folder:
                    logger.info(f"В папке {current_folder_id} нет медиафайлов нужных типов.")
                    continue # Переходим к следующему маппингу

                # 5. Находим НОВЫЕ файлы для этой папки (сравниваем с ОБЩИМ списком обработанных)
                new_files_for_folder = voicy.find_new_media_files(media_in_folder, processed_media_ids)

                if not new_files_for_folder:
                    logger.info(f"Нет новых медиафайлов для обработки в папке {current_folder_id}.")
                    continue # Переходим к следующему маппингу

                logger.info(f"Найдено {len(new_files_for_folder)} новых файлов в папке {current_folder_id}. Начинаю обработку...")

                # 6. Обрабатываем КАЖДЫЙ новый файл в этой папке
                for file_info in new_files_for_folder:
                    file_audio_id = file_info['id']
                    file_audio_name = file_info['name']
                    logger.info(f"Обработка файла: {file_audio_name} (ID: {file_audio_id}) из папки {current_folder_id}")

                    # Генерируем уникальные пути для временных файлов, чтобы избежать конфликтов
                    # Можно добавить ID файла или timestamp к имени
                    temp_id = f"{file_audio_id}_{int(time.time())}"
                    downloaded_file_path = os.path.join(conf.TEMP_FOLDER_PATH, f"{temp_id}_downloaded.mp4") # Предполагаем наличие TEMP_FOLDER_PATH в config.py
                    audio_file_path = os.path.join(conf.TEMP_FOLDER_PATH, f"{temp_id}_converted.wav")

                    transcribed_text = None
                    duration_minutes = 0.0
                    model_answer = None
                    input_tokens = 0
                    output_tokens = 0
                    processed_successfully = False

                    try:
                        # --- Шаги обработки файла ---
                        logger.info(f"Скачивание файла {file_audio_name}...")
                        voicy.download_file_from_google_drive(file_audio_id, downloaded_file_path, conf.SERVICE_ACCOUNT_FILE)
                        logger.info(f"Файл {file_audio_name} скачан.")

                        logger.info(f"Конвертация {file_audio_name} в WAV...")
                        voicy.convert_mp4_to_wav(downloaded_file_path, audio_file_path)
                        logger.info(f"Файл {file_audio_name} сконвертирован.")

                        logger.info(f"Транскрипция {file_audio_name}...")
                        # Получаем и текст, и длительность
                        transcribed_text, duration_minutes = voicy.transcribe_audio_file(
                            conf.CLOUD_STORAGE_BUCKET_NAME, audio_file_path, conf.SERVICE_ACCOUNT_FILE
                        )

                        if transcribed_text is None:
                             raise ValueError("Ошибка транскрипции, получено None.") # Генерируем ошибку для блока except
                        logger.info(f"Транскрипция завершена. Длительность: {duration_minutes:.2f} мин.")


                        logger.info(f"Чтение промпта из Google Doc ID: {conf.DOCUMENT_PROMPT_ID}")
                        prompt = voicy.read_google_doc(docs_service, conf.DOCUMENT_PROMPT_ID)
                        if prompt is None:
                            logger.error("Не удалось прочитать документ с промптом. Пропуск саммаризации.")
                            # Можно либо пропустить саммаризацию, либо прервать обработку файла
                            model_answer = "Ошибка: Не удалось загрузить промпт для саммаризации."
                        else:
                            logger.info("Саммаризация текста...")
                            # Указываем модель явно или берем из конфига
                            openai_model = conf.OPENAI_MODEL if hasattr(conf, 'OPENAI_MODEL') else "gpt-3.5-turbo"
                            summary_result = voicy.openai_summarizer(conf.openai_api_key,
                                                                        transcribed_text, prompt, openai_model)
                            if summary_result:
                                model_answer, input_tokens, output_tokens = summary_result
                                logger.info(f"Саммаризация завершена. Токены: In={input_tokens}, Out={output_tokens}")
                            else:
                                logger.error("Ошибка при саммаризации текста.")
                                model_answer = "Ошибка: Не удалось выполнить саммаризацию."


                        logger.info(f"Отправка саммари в Telegram чат ID: {current_chat_id}...")
                        await bot.send_message(chat_id=current_chat_id, text=model_answer)
                        logger.info("Саммари отправлено.")

                        processed_successfully = True

                    except Exception as file_proc_error:
                        logger.error(f"Ошибка при обработке файла {file_audio_name} (ID: {file_audio_id}): {file_proc_error}")
                        # Попытка отправить сообщение об ошибке в чат
                        try:
                            error_message = f"Не удалось обработать файл: {file_audio_name}\nОшибка: {file_proc_error}"
                            await bot.send_message(chat_id=current_chat_id, text=error_message)
                        except Exception as telegram_error:
                             logger.error(f"Не удалось отправить сообщение об ошибке в Telegram чат {current_chat_id}: {telegram_error}")

                    finally:
                        # --- Запись в основную таблицу ВНЕ зависимости от успеха саммаризации, если была транскрипция ---
                        # Записываем, даже если была ошибка, чтобы не обрабатывать повторно
                        # Но записываем только если есть ID файла
                        if file_audio_id:
                             # Используем source_identifier для указания папки
                            source_id = f"Folder: {current_folder_id}"
                            voicy.write_to_google_sheet(
                                gc=gc,
                                spreadsheet_id=conf.SPREADSHEET_ID, # Имя основной таблицы из конфига
                                meeting_id=file_audio_id,
                                meeting_name=file_audio_name,
                                transcribation_text=transcribed_text if transcribed_text else "Ошибка транскрипции",
                                summary=model_answer if model_answer else "Ошибка саммаризации",
                                speech_minutes=duration_minutes,
                                input_openai=input_tokens,
                                output_openai=output_tokens,
                                source_identifier=current_email # Передаем идентификатор сотрудника
                            )
                            # Важно: Добавляем ID в список обработанных *сразу после записи*
                            # чтобы следующий файл в этом же цикле не считался новым, если он был найден ранее
                            if processed_media_ids is not None:
                                processed_media_ids.append(file_audio_id)
                        else:
                            logger.warning("Не удалось записать результат в таблицу: отсутствует file_audio_id.")


                        # --- Очистка временных файлов ---
                        logger.info(f"Очистка временных файлов для {file_audio_id}...")
                        for f_path in [downloaded_file_path, audio_file_path]:
                             if os.path.exists(f_path):
                                 try:
                                     os.remove(f_path)
                                     logger.info(f"Удален временный файл: {f_path}")
                                 except OSError as remove_error:
                                     logger.error(f"Не удалось удалить временный файл {f_path}: {remove_error}")

                    # Небольшая пауза между обработкой файлов, если нужно
                    # await asyncio.sleep(5)

            except Exception as mapping_proc_error:
                logger.error(f"Непредвиденная ошибка при обработке маппинга для папки {current_folder_id}: {mapping_proc_error}")
                # Продолжаем со следующим маппингом

            logger.info(f"--- Завершение обработки маппинга для папки: {current_folder_id} ---")
            # Можно добавить небольшую паузу между проверкой разных папок
            # await asyncio.sleep(10)

    except Exception as e:
        logger.error(f"Критическая ошибка в главном цикле `check_and_process_all_mappings`: {e}", exc_info=True) # Добавляем traceback
    finally:
        end_time = time.time()
        logger.info(f"Цикл проверки завершен за {end_time - start_time:.2f} секунд.")


async def main():
    # Убедимся, что временная папка существует
    if hasattr(conf, 'TEMP_FOLDER_PATH'):
        os.makedirs(conf.TEMP_FOLDER_PATH, exist_ok=True)
    else:
        logger.warning("Переменная TEMP_FOLDER_PATH не задана в config.py. Временные файлы будут создаваться в текущей директории.")
        conf.TEMP_FOLDER_PATH = "." # Используем текущую директорию

    logger.info("Бот запущен и проверяет папки каждые 30 минут...")
    while True:
        await check_and_process_all_mappings() # Вызываем обновленную функцию
        logger.info("Ожидание следующего цикла проверки (10 минут)...")
        await asyncio.sleep(600)  # Проверяем каждые 10 минут!!!
        logger.info("Проснулся после ожидания, начинаю новый цикл...")  # <-- Добавленный лог


if __name__ == '__main__':
    asyncio.run(main())