# -*- coding: utf-8 -*-
"""ВСЕ настройки бота. Меняются только здесь."""

# ---------------- Источники ----------------
# Список YouTube-каналов, с которых берём видео
CHANNELS = [
    "https://www.youtube.com/@Sket4i",
    "https://www.youtube.com/@marmazh",
    "https://www.youtube.com/@22mirby",
    "https://www.youtube.com/@Dobryak",
    "https://www.youtube.com/@ZAKATOON",
    "https://www.youtube.com/@X2DED",
    "https://www.youtube.com/@unoniceguy",
]
MAX_VIDEOS_PER_CHANNEL = 0      # 0 = все новые видео канала (без ограничения по каналу)
VIDEOS_PER_AUTHOR = 10          # сколько видео брать у одного автора за один заход
MAX_VIDEOS_PER_RUN = 0          # 0 = без ограничения; иначе столько успешных видео за запуск
MIN_VIDEO_DURATION = 60         # пропускать видео короче (сек)
MAX_VIDEO_DURATION = 3600       # и длиннее (сек)
MIN_VIDEO_DURATION_TIKTOK = 5   # у TikTok ролики короткие — свой минимум
# Формат yt-dlp. YouTube отдаёт 1080p ещё и через HLS (m3u8) — такие потоки
# у провайдера часто зависают, поэтому берём обычные https-потоки (vp9/opus).
YTDLP_FORMAT = (
    "bv*[height<=1080][protocol^=http][vcodec^=vp9]+"
    "ba[protocol^=http][acodec^=opus]/"
    "bv*[height<=1080][protocol^=http]+ba[protocol^=http]/"
    "bv*[height<=1080]+ba/b[height<=1080]"
)
COOKIES_FILE = None             # "cookies.txt", если YouTube блокирует скачивание
USE_DOH_FALLBACK = True         # обход DNS-блокировки YouTube через Google DoH
PROXY = None                    # "socks5://127.0.0.1:1080", если работаете через VPN/прокси

# TikTok: свои форматы и минимальная длительность (ролики короткие).
TIKTOK_FORMAT = "bv*+ba/b[ext=mp4]/b"

# ---------------- Нарезка ----------------
CLIP_DURATION = 45              # длина клипа (сек)
CUT_SEARCH_WINDOW = 3.0         # где искать паузу вокруг точки реза (±сек)
MIN_LAST_CLIP = 20              # последний кусок короче — выкинуть

# ---------------- Кадр и фон ----------------
# background_mode:
#   width  = видео на всю ширину, сверху/снизу чёрные поля (как в примере)
#   crop   = видео заполняет весь кадр, лишнее по краям обрезается
#   folder = сверху клип, снизу фоновое видео из папки
#   blur   = сверху клип, снизу размытая копия клипа
#   black  = сверху клип, снизу чёрный
BACKGROUND_MODE = "width"
BACKGROUND_DIR = "backgrounds/"  # папка с фоновыми видео
BACKGROUND_VOLUME = 0.0         # громкость фона (0 = выключен)
TOP_RATIO = 0.5                 # доля высоты под клип (только folder/blur/black)

# ---------------- Музыка ----------------
MUSIC_ENABLED = False           # подкладывать музыку под клип
MUSIC_DIR = "music/"            # папка с треками (mp3/m4a/wav/ogg)
MUSIC_VOLUME = 0.12             # громкость музыки (0..1)

# ---------------- Перевод и озвучка ----------------
TRANSLATE = False               # переводить речь на русский и озвучивать
TRANSLATE_TO = "ru"             # язык перевода
VOICE_NAME = "ru-RU-DmitryNeural"  # голос edge-tts
KEEP_ORIGINAL_AUDIO = False     # оставить оригинальный звук под озвучкой

# ---------------- Рекламные вставки ----------------
BANNER_ENABLED = True           # вставлять рекламные ролики из папки
BANNER_DIR = "banners/"         # папка с рекламой
BANNER_EVERY = 45               # клип длиннее 2 минут — вставка каждые N секунд
BANNER_MIDDLE_UNTIL = 120       # клип короче — одна вставка посередине
BANNER_DURATION = 4             # сколько секунд показывать рекламу (0 = целиком)
BANNER_GREEN = True             # убирать зелёный фон у рекламного ролика

# ---------------- Видео ----------------
WIDTH, HEIGHT, FPS = 1080, 1920, 30   # размер кадра и частота кадров
VIDEO_CODEC = "libx264"         # или "h264_nvenc" при наличии NVIDIA
CRF, PRESET = 20, "veryfast"    # качество и скорость кодирования
PARALLEL_JOBS = 2               # сколько клипов рендерить одновременно

# ---------------- Whisper / субтитры ----------------
WHISPER_MODEL = "small"         # tiny/base/small/medium/large-v3
WHISPER_DEVICE = "auto"         # cpu / cuda / auto
WHISPER_COMPUTE_TYPE = "auto"   # auto / int8 / float16 / float32
LANGUAGE = None                 # None = автоопределение языка речи
WORDS_PER_CAPTION = 2           # 1–3 слова на экране за раз
MAX_WORD_GAP = 0.6              # разрыв между словами (сек), после которого начинается новая строка
FONT_NAME = "Montserrat Black"  # имя шрифта внутри TTF
FONT_FILE = "fonts/Montserrat-Black.ttf"  # файл шрифта
FONT_SIZE = 90                  # размер шрифта
OUTLINE = 6                     # толщина чёрной обводки
UPPERCASE = True                # текст КАПСОМ
CAPTION_Y = None                # None = ровно на стыке половин

# ---------------- Google Drive ----------------
DRIVE_ROOT_FOLDER = "TikTok Bot"          # корневая папка на Drive
GOOGLE_CREDENTIALS = "credentials.json"   # OAuth-ключи desktop-приложения
GOOGLE_TOKEN = "token.json"               # сохранённый токен после первого входа
DELETE_LOCAL_AFTER_UPLOAD = True          # удалять исходник и клипы после успешной отправки

# ---------------- Почта (Gmail) ----------------
EMAIL_ENABLED = True            # отправлять письмо после загрузки на Диск
EMAIL_TO = None                 # None = на тот же адрес, что у Google-аккаунта
EMAIL_SUBJECT_PREFIX = "Готово"  # начало темы письма

# ---------------- Повторы ----------------
CLIP_RETRIES = 3                # сколько ДОПОЛНИТЕЛЬНЫХ попыток даётся каждой части
RETRY_PAUSE = 5                 # пауза между попытками (сек)

# ---------------- Служебное ----------------
WORK_DIR = "work/"              # временные файлы
OUTPUT_DIR = "output/"          # готовые клипы
STATE_DB = "state.sqlite"       # база обработанных видео
USE_CPP = True                  # False = только Python-фолбэк поиска пауз

# ---------------- Параметры поиска пауз (менять не обязательно) ----------------
SILENCE_SAMPLE_RATE = 16000     # частота аудио для анализа (Гц)
SILENCE_FRAME_MS = 20           # окно RMS (мс)
SILENCE_PERCENTILE = 15         # перцентиль энергии, считаем его «шумом»
SILENCE_THRESHOLD_FACTOR = 2.5  # порог тишины = шум * этот коэффициент
SILENCE_MEDIAN_FACTOR = 0.25    # ...но не выше доли медианной энергии (защита от записей без пауз)
SILENCE_THRESHOLD_MIN = 0.002   # абсолютный минимум порога тишины (0..1)
MIN_PAUSE = 0.15                # минимальная длина паузы (сек)
