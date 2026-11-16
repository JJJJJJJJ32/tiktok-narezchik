# -*- coding: utf-8 -*-
"""Перевод фраз и озвучка (edge-tts), привязанная к таймкодам субтитров.

Перевод идёт через открытый endpoint Google Translate (без ключа и без
дополнительных библиотек). Если перевести не удалось — остаётся оригинал:
видео всё равно соберётся, просто без перевода.
"""

import asyncio
import json
import logging
import subprocess
import time
import urllib.parse
import urllib.request
import wave
from pathlib import Path

import numpy as np

import config as cfg

log = logging.getLogger(__name__)

SAMPLE_RATE = 44100
_MAX_CHARS = 4500           # ограничение одного запроса перевода
_failures = 0               # подряд неудачных переводов
_disabled = False           # после серии сбоев перевод выключаем до конца запуска


def _translate_once(text: str, target: str) -> str:
    url = ("https://translate.googleapis.com/translate_a/single?client=gtx"
           f"&sl=auto&tl={urllib.parse.quote(target)}&dt=t"
           f"&q={urllib.parse.quote(text)}")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    return "".join(part[0] for part in data[0] if part and part[0])


def translate_text(text: str, target: str | None = None) -> str:
    """Перевод строки. Пустая строка — не получилось."""
    global _failures, _disabled
    target = target or cfg.TRANSLATE_TO
    text = (text or "").strip()
    if not text or _disabled:
        return ""

    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS]

    for attempt in range(1, 4):
        try:
            result = _translate_once(text, target).strip()
            if result:
                _failures = 0
                return result
        except Exception as e:
            log.debug("Перевод, попытка %d: %s", attempt, e)
            time.sleep(attempt * 1.5)
    _failures += 1
    if _failures >= 5:
        _disabled = True
        log.warning("Перевод не отвечает — до конца запуска оставляю оригинальный текст")
    return ""


def translate_captions(captions: list[dict]) -> list[dict]:
    """Переводит фразы. В 'speech' — текст для озвучки, в 'text' — для экрана."""
    result = []
    for caption in captions:
        source = caption["text"].strip()
        spoken = translate_text(source)
        if not spoken:
            spoken = source           # не смогли перевести — оставляем как есть
        text = spoken.upper() if cfg.UPPERCASE else spoken
        result.append({**caption, "text": text.replace("{", "(").replace("}", ")"),
                       "speech": spoken})
    return result


# ---------------------------------------------------------------- озвучка

def _synthesize(text: str, path: Path, voice: str | None = None) -> bool:
    """edge-tts → mp3. Возвращает False, если синтез не удался."""
    voice = voice or cfg.VOICE_NAME
    try:
        import edge_tts
    except Exception as e:  # нет библиотеки
        log.warning("Озвучка недоступна: %s", e)
        return False

    async def run() -> None:
        await edge_tts.Communicate(text, voice).save(str(path))

    for attempt in range(1, 4):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            asyncio.run(run())
            if path.exists() and path.stat().st_size > 500:
                return True
        except Exception as e:
            log.debug("Озвучка, попытка %d: %s", attempt, e)
            time.sleep(attempt)
    return False


def _read_pcm(path: Path) -> np.ndarray:
    """mp3/wav → моно float32 44100 Гц."""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le",
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-"],
        capture_output=True,
    )
    if out.returncode != 0 or not out.stdout:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(out.stdout, dtype=np.float32).copy()


def _speed(pcm: np.ndarray, factor: float) -> np.ndarray:
    """Ускоряет речевой фрагмент через ffmpeg (atempo), чтобы влез в таймкод."""
    if factor <= 1.01 or len(pcm) == 0:
        return pcm
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(SAMPLE_RATE),
         "-ac", "1", "-i", "-", "-filter:a", f"atempo={min(2.0, factor):.3f}",
         "-f", "f32le", "-"],
        input=pcm.tobytes(), capture_output=True,
    )
    if out.returncode != 0 or not out.stdout:
        return pcm
    return np.frombuffer(out.stdout, dtype=np.float32).copy()


def _fade(pcm: np.ndarray, edge: int = 220) -> np.ndarray:
    """Плавные края — без щелчков на стыках."""
    if len(pcm) <= edge * 2:
        return pcm
    pcm = pcm.copy()
    pcm[:edge] *= np.linspace(0.0, 1.0, edge, dtype=np.float32)
    pcm[-edge:] *= np.linspace(1.0, 0.0, edge, dtype=np.float32)
    return pcm


def build_track(captions: list[dict], total: float, work_dir: Path) -> Path | None:
    """Собирает дорожку озвучки длиной ровно total секунд.

    Каждая фраза синтезируется отдельно и ставится на своё место. Если не
    влезает до следующей фразы — слегка ускоряется. None — озвучки нет.
    """
    spoken = [c for c in captions if (c.get("speech") or c.get("text") or "").strip()]
    if not spoken:
        return None

    voice_dir = Path(work_dir) / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    buffer = np.zeros(int(total * SAMPLE_RATE), dtype=np.float32)
    made = 0

    for index, caption in enumerate(captions):
        text = (caption.get("speech") or caption.get("text") or "").strip()
        if not text:
            continue
        start = max(0.0, float(caption["start"]))
        if start >= total:
            continue

        # Сколько времени есть до следующей фразы (или до конца клипа).
        limit = total - start
        for nxt in captions[index + 1:]:
            if (nxt.get("speech") or nxt.get("text") or "").strip():
                limit = min(limit, max(0.4, float(nxt["start"]) - start))
                break
        limit = max(0.4, limit)

        mp3 = voice_dir / f"{index:02d}.mp3"
        if not mp3.exists() and not _synthesize(text, mp3):
            continue
        pcm = _read_pcm(mp3)
        if len(pcm) == 0:
            continue

        seconds = len(pcm) / SAMPLE_RATE
        if seconds > limit:
            pcm = _speed(pcm, seconds / limit)
            seconds = len(pcm) / SAMPLE_RATE
            if seconds > limit:
                pcm = pcm[:int(limit * SAMPLE_RATE)]
                seconds = len(pcm) / SAMPLE_RATE

        pcm = _fade(pcm)
        offset = int(start * SAMPLE_RATE)
        end = min(len(buffer), offset + len(pcm))
        if end <= offset:
            break
        buffer[offset:end] += pcm[:end - offset]
        made += 1

    if made == 0:
        return None

    peak = float(np.max(np.abs(buffer))) if len(buffer) else 0.0
    if peak > 0.9:
        buffer *= 0.9 / peak

    out_path = Path(work_dir) / "voice.wav"
    samples = (np.clip(buffer, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(samples.tobytes())
    return out_path
