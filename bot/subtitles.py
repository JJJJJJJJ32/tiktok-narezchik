# -*- coding: utf-8 -*-
"""faster-whisper → субтитры .ass (КАПС, жирный, по центру стыка половин)."""

import logging
import wave
from pathlib import Path

import numpy as np

from . import config as cfg

log = logging.getLogger(__name__)

_model = None


def _device() -> str:
    if cfg.WHISPER_DEVICE != "auto":
        return cfg.WHISPER_DEVICE
    try:
        import ctranslate2
        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:
        return "cpu"


def get_model():
    """Модель грузится один раз на процесс."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        device = _device()
        compute = cfg.WHISPER_COMPUTE_TYPE
        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"
        log.info("   %s готовлю распознавание речи (%s, %s) — один раз за запуск",
                 "•", cfg.WHISPER_MODEL, device)
        _model = WhisperModel(cfg.WHISPER_MODEL, device=device, compute_type=compute)
    return _model


def _load_wav(wav_path: Path) -> np.ndarray:
    """Читаем WAV сами и отдаём Whisper массив — без зависимости от PyAV."""
    with wave.open(str(wav_path)) as w:
        channels = w.getnchannels()
        frames = w.readframes(w.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio


def transcribe(wav_path: Path) -> list[dict]:
    """Слова сегмента: [{'word', 'start', 'end'}] с таймкодами от начала сегмента."""
    return transcribe_detail(wav_path)[0]


def transcribe_detail(wav_path: Path) -> tuple[list[dict], str]:
    """То же плюс распознанный язык речи (например, 'ko')."""
    segments, info = get_model().transcribe(
        _load_wav(wav_path),
        language=cfg.LANGUAGE,
        word_timestamps=True,
        vad_filter=False,
    )
    words = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append({"word": text, "start": w.start, "end": w.end})
    return words, (getattr(info, "language", "") or "")


def group_words(words: list[dict]) -> list[dict]:
    """1–3 слова на строку; новая строка при паузе или по лимиту слов."""
    lines, current = [], []
    for w in words:
        if current:
            gap = w["start"] - current[-1]["end"]
            if len(current) >= cfg.WORDS_PER_CAPTION or gap > cfg.MAX_WORD_GAP:
                lines.append(current)
                current = []
        current.append(w)
    if current:
        lines.append(current)

    captions = []
    for line in lines:
        text = " ".join(w["word"] for w in line)
        if cfg.UPPERCASE:
            text = text.upper()
        captions.append({
            "start": line[0]["start"],
            "end": max(line[-1]["end"], line[0]["start"] + 0.3),
            "text": text.replace("{", "(").replace("}", ")"),
        })
    return captions


def group_phrases(words: list[dict], max_words: int | None = None,
                  max_gap: float = 0.45, max_duration: float = 5.0) -> list[dict]:
    """Фразы по 3–8 слов: так их можно перевести и озвучить целиком."""
    max_words = max_words or max(4, cfg.WORDS_PER_CAPTION * 4)
    lines, current = [], []
    for w in words:
        if current:
            gap = w["start"] - current[-1]["end"]
            length = w["end"] - current[0]["start"]
            if gap > max_gap or len(current) >= max_words or length > max_duration:
                lines.append(current)
                current = []
        current.append(w)
    if current:
        lines.append(current)

    captions = []
    for line in lines:
        text = " ".join(w["word"] for w in line)
        captions.append({
            "start": line[0]["start"],
            "end": max(line[-1]["end"], line[0]["start"] + 0.3),
            "text": text.replace("{", "(").replace("}", ")"),
        })
    return captions


def _timestamp(seconds: float) -> str:
    cs = int(round(max(0.0, seconds) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def write_ass(captions: list[dict], ass_path: Path, y: int | None = None) -> Path:
    if y is None:
        y = cfg.CAPTION_Y if cfg.CAPTION_Y is not None else cfg.HEIGHT // 2
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {cfg.WIDTH}
PlayResY: {cfg.HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{cfg.FONT_NAME},{cfg.FONT_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,{cfg.OUTLINE},0,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for c in captions:
        lines.append(
            f"Dialogue: 0,{_timestamp(c['start'])},{_timestamp(c['end'])},Default,,0,0,0,,"
            f"{{\\an5\\pos({cfg.WIDTH // 2},{y})}}{c['text']}\n"
        )
    ass_path.write_text("".join(lines), encoding="utf-8")
    return ass_path
