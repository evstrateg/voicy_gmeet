# Конфигурация
TELEGRAM_API_TOKEN = ""
# google
TEMP_FOLDER_PATH = ""
SERVICE_ACCOUNT_FILE = ""
SCOPES = [
        'https://www.googleapis.com/auth/drive',
        'https://www.googleapis.com/auth/documents.readonly',
        'https://www.googleapis.com/auth/spreadsheets'
        ]
CLOUD_STORAGE_BUCKET_NAME = ""
DRIVE_FOLDER_ID = "" #main_folder
DOCUMENT_PROMPT_ID = "" #prompt
SPREADSHEET_ID = ""
MEETING_ID_COLUMN = ""
MAPPING_SPREADSHEET_ID = ""
# AI
openai_api_key = ""
OPENAI_MODEL = "gpt-4o-mini-2024-07-18"
# converter
media_mime_types = [
    'video/mp4',
    'application/vnd.google-apps.video'  # Для видео, загруженных в Google Диск
]
downloaded_file_path = 'downloaded_video.mp4'
audio_file_path = 'extracted_audio.wav'