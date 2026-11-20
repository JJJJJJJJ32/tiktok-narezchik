# -*- coding: utf-8 -*-
"""Настройки, которые меняются из меню. Хранятся в settings.json.

config.py остаётся «заводскими» значениями: при запуске значения из
settings.json аккуратно переносятся поверх, поэтому меню ничего не ломает.

У каждого бота свой профиль Google: файл токена, корневая папка на Диске и
адрес почты. Поэтому один запуск может работать сразу за несколько ботов —
каждый грузит клипы на свой Диск и пишет письмо на свою почту.
"""

import json
import logging
import re
from pathlib import Path

from . import config as cfg

log = logging.getLogger(__name__)

FILE = Path("settings.json")

# Ключи профиля, которые можно менять в меню «GOOGLE: ПОЧТА И ДИСК».
BOT_KEYS = ("name", "channels", "enabled", "credentials", "token",
            "drive_root", "email_to")


def _slug(name: str) -> str:
    clean = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", "-", name.strip()).strip("-").lower()
    return clean or "bot"


def default_token(index: int) -> str:
    """Первый бот остаётся на старом token.json, остальным — свой файл."""
    return "token.json" if index == 0 else f"token-{index + 1}.json"


def default_drive_root(index: int) -> str:
    return cfg.DRIVE_ROOT_FOLDER if index == 0 else f"{cfg.DRIVE_ROOT_FOLDER} {index + 1}"


def _bot(raw: dict, index: int) -> dict:
    return {
        "name": str(raw.get("name") or f"Бот {index + 1}"),
        "channels": [str(c) for c in raw.get("channels") or []],
        "enabled": bool(raw.get("enabled", True)),
        "credentials": str(raw.get("credentials") or cfg.GOOGLE_CREDENTIALS),
        "token": str(raw.get("token") or default_token(index)),
        "drive_root": str(raw.get("drive_root") or default_drive_root(index)),
        "email_to": raw.get("email_to") or None,
    }


def defaults() -> dict:
    return {
        "bots": [_bot({"name": "Бот 1", "channels": list(cfg.CHANNELS)}, 0)],
        "background_mode": cfg.BACKGROUND_MODE,
        "backgrounds_dir": cfg.BACKGROUND_DIR,
        "top_ratio": cfg.TOP_RATIO,
        "font_size": cfg.FONT_SIZE,
        "clip_duration": cfg.CLIP_DURATION,
        "delete_after_upload": cfg.DELETE_LOCAL_AFTER_UPLOAD,
        "email_enabled": cfg.EMAIL_ENABLED,
        "email_to": cfg.EMAIL_TO,
        "clip_retries": cfg.CLIP_RETRIES,
        "videos_per_author": cfg.VIDEOS_PER_AUTHOR,
        "max_videos_per_run": cfg.MAX_VIDEOS_PER_RUN,
        "music_enabled": cfg.MUSIC_ENABLED,
        "music_dir": cfg.MUSIC_DIR,
        "music_volume": cfg.MUSIC_VOLUME,
        "translate": cfg.TRANSLATE,
        "translate_to": cfg.TRANSLATE_TO,
        "voice_name": cfg.VOICE_NAME,
        "keep_original_audio": cfg.KEEP_ORIGINAL_AUDIO,
        "banner_enabled": cfg.BANNER_ENABLED,
        "banner_dir": cfg.BANNER_DIR,
        "banner_duration": cfg.BANNER_DURATION,
    }


def load() -> dict:
    data = defaults()
    if FILE.exists():
        try:
            saved = json.loads(FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                for key, value in saved.items():
                    if key == "bots" and isinstance(value, list) and value:
                        bots = [_bot(bot, i) for i, bot in enumerate(value)
                                if isinstance(bot, dict)]
                        if bots:
                            data["bots"] = bots
                    elif key in data:
                        data[key] = value
        except Exception as e:
            log.warning("Не смог прочитать settings.json (%s), беру настройки по умолчанию", e)
    return data


def save(data: dict) -> None:
    FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def apply(data: dict, bot: dict | None = None) -> None:
    """Переносит настройки из меню в config, чтобы ими пользовался весь код.

    bot задан — дополнительно подставляем профиль этого бота (Диск и почта).
    """
    cfg.BACKGROUND_MODE = str(data.get("background_mode", cfg.BACKGROUND_MODE))
    cfg.BACKGROUND_DIR = str(data.get("backgrounds_dir", cfg.BACKGROUND_DIR))
    cfg.TOP_RATIO = float(data.get("top_ratio", cfg.TOP_RATIO))
    cfg.FONT_SIZE = int(data.get("font_size", cfg.FONT_SIZE))
    cfg.CLIP_DURATION = float(data.get("clip_duration", cfg.CLIP_DURATION))
    cfg.DELETE_LOCAL_AFTER_UPLOAD = bool(
        data.get("delete_after_upload", cfg.DELETE_LOCAL_AFTER_UPLOAD))
    cfg.EMAIL_ENABLED = bool(data.get("email_enabled", cfg.EMAIL_ENABLED))
    cfg.CLIP_RETRIES = max(0, int(data.get("clip_retries", cfg.CLIP_RETRIES)))
    cfg.VIDEOS_PER_AUTHOR = max(1, int(data.get("videos_per_author",
                                                cfg.VIDEOS_PER_AUTHOR)))
    cfg.MAX_VIDEOS_PER_RUN = max(0, int(data.get("max_videos_per_run",
                                                  cfg.MAX_VIDEOS_PER_RUN)))
    cfg.MUSIC_ENABLED = bool(data.get("music_enabled", cfg.MUSIC_ENABLED))
    cfg.MUSIC_DIR = str(data.get("music_dir", cfg.MUSIC_DIR))
    cfg.MUSIC_VOLUME = float(data.get("music_volume", cfg.MUSIC_VOLUME))
    cfg.TRANSLATE = bool(data.get("translate", cfg.TRANSLATE))
    cfg.TRANSLATE_TO = str(data.get("translate_to", cfg.TRANSLATE_TO))
    cfg.VOICE_NAME = str(data.get("voice_name", cfg.VOICE_NAME))
    cfg.KEEP_ORIGINAL_AUDIO = bool(data.get("keep_original_audio",
                                            cfg.KEEP_ORIGINAL_AUDIO))
    cfg.BANNER_ENABLED = bool(data.get("banner_enabled", cfg.BANNER_ENABLED))
    cfg.BANNER_DIR = str(data.get("banner_dir", cfg.BANNER_DIR))
    cfg.BANNER_DURATION = max(0, int(data.get("banner_duration",
                                              cfg.BANNER_DURATION)))

    if bot is not None:
        apply_bot(bot, data)
        return

    cfg.EMAIL_TO = data.get("email_to") or None


def apply_bot(bot: dict, data: dict | None = None) -> None:
    """Подставляет профиль бота: свой токен, папка на Диске и почта."""
    cfg.GOOGLE_CREDENTIALS = bot.get("credentials") or cfg.GOOGLE_CREDENTIALS
    cfg.GOOGLE_TOKEN = bot.get("token") or cfg.GOOGLE_TOKEN
    cfg.DRIVE_ROOT_FOLDER = bot.get("drive_root") or cfg.DRIVE_ROOT_FOLDER
    # У бота нет своего адреса — берём общий или адрес его Google-аккаунта.
    fallback = (data or {}).get("email_to")
    cfg.EMAIL_TO = bot.get("email_to") or fallback or None


def enabled_bots(data: dict) -> list[dict]:
    """Боты, которых нужно обрабатывать в этом запуске."""
    return [bot for bot in data.get("bots", []) if bot.get("enabled", True)]


def active_bot(data: dict) -> dict | None:
    bots = data.get("bots") or []
    for bot in bots:
        if bot.get("enabled", True):
            return bot
    return bots[0] if bots else None


def enabled_channels(data: dict) -> list[str]:
    """Все каналы всех включённых ботов (без повторов, порядок сохраняем)."""
    channels: list[str] = []
    for bot in enabled_bots(data):
        channels.extend(bot.get("channels") or [])
    seen, result = set(), []
    for channel in channels:
        if channel not in seen:
            seen.add(channel)
            result.append(channel)
    return result


def new_bot(data: dict, name: str) -> dict:
    """Профиль для только что созданного бота."""
    index = len(data["bots"])
    bot = _bot({"name": name}, index)
    data["bots"].append(bot)
    return bot


def unique_slug(data: dict, name: str) -> str:
    used = {_slug(bot.get("name", "")) for bot in data.get("bots", [])}
    base = _slug(name)
    slug, n = base, 1
    while slug in used:
        n += 1
        slug = f"{base}-{n}"
    return slug
