# -*- coding: utf-8 -*-
"""Сохранение хода обработки видео.

Если программу закрыли посреди скачивания, нарезки, монтажа или загрузки,
при следующем запуске бот читает work/progress/<video_id>.json и продолжает
с того места, где остановился: готовое заново не качает, не режет, не грузит
и не отправляет письмо дважды.
"""

import json
import logging
import os
from pathlib import Path

from . import config as cfg

log = logging.getLogger(__name__)

VERSION = 1


def _folder() -> Path:
    path = Path(cfg.WORK_DIR) / "progress"
    path.mkdir(parents=True, exist_ok=True)
    return path


def path_for(video_id: str) -> Path:
    return _folder() / f"{video_id}.json"


def load(video_id: str) -> dict | None:
    path = path_for(video_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.debug("Не удалось прочитать прогресс %s: %s", path.name, e)
        return None
    if data.get("version") != VERSION:
        log.debug("Прогресс %s старой версии — начну заново", path.name)
        return None
    return data


def save(data: dict) -> None:
    """Пишем через временный файл: внезапное закрытие не испортит прогресс."""
    video_id = data.get("video", {}).get("video_id")
    if not video_id:
        raise ValueError("в прогрессе нет video_id")
    path = path_for(video_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def delete(video_id: str) -> None:
    path_for(video_id).unlink(missing_ok=True)


def pending() -> list[dict]:
    """Незавершённые видео с прошлых запусков."""
    result = []
    for path in _folder().glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("version") != VERSION or data.get("finished"):
            continue
        result.append(data)
    return result


def purge_processed(con) -> int:
    """Убирает прогресс видео, которые уже отмечены обработанными в базе."""
    removed = 0
    for path in _folder().glob("*.json"):
        video_id = path.stem
        row = con.execute("SELECT 1 FROM videos WHERE video_id = ?", (video_id,)).fetchone()
        if row is not None:
            path.unlink(missing_ok=True)
            removed += 1
    return removed
