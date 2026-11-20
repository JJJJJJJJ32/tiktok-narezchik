# -*- coding: utf-8 -*-
"""Точка входа: меню, обработка видео, итоговая сводка."""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

from bot import composer
from bot import config as cfg
from bot import cutter
from bot import downloader
from bot import progress
from bot import settings
from bot import subtitles
from bot import ui
from bot import uploader
from bot import voiceover

log = logging.getLogger("bot")


# ---------------------------------------------------------------- логирование

def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    class DropNoisy(logging.Filter):
        prefixes = ("httpx", "httpcore", "urllib3", "huggingface_hub",
                    "filelock", "faster_whisper", "googleapiclient")

        def filter(self, record):
            name = record.name
            if any(name == p or name.startswith(p + ".") for p in self.prefixes):
                return record.levelno >= logging.WARNING
            if record.levelno < logging.WARNING and str(record.msg).startswith("HTTP Request:"):
                return False
            return True

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(message)s"))
    console.addFilter(DropNoisy())
    root.addHandler(console)

    file_handler = logging.FileHandler("bot.log", encoding="utf-8")
    file_handler.setFormatter(ui.FileFormatter("%(asctime)s %(levelname)s %(message)s"))
    file_handler.addFilter(DropNoisy())
    root.addHandler(file_handler)

    # Чужие библиотеки пишут служебный мусор — в консоли он не нужен.
    for noisy in ("faster_whisper", "urllib3", "googleapiclient", "httpx",
                  "filelock", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _short(error: Exception | str, limit: int = 180) -> str:
    text = " ".join(str(error).split())
    while text.upper().startswith("ERROR:"):
        text = text[len("ERROR:"):].strip()
    return text[:limit] + ("…" if len(text) > limit else "")


# ---------------------------------------------------------------- проверка файлов

def _media_info(path: Path) -> tuple[float, set[str]]:
    """Длительность и типы дорожек. Пустой результат — файл битый."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        return 0.0, set()
    data = json.loads(result.stdout or "{}")
    duration = float((data.get("format") or {}).get("duration") or 0)
    kinds = {s.get("codec_type") for s in data.get("streams") or []}
    return duration, kinds


def _media_ok(path: Path, need_audio: bool = True,
              min_duration: float = 1.0) -> bool:
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return False
        duration, kinds = _media_info(path)
    except Exception:
        return False
    if duration < min_duration or "video" not in kinds:
        return False
    return not need_audio or "audio" in kinds


def _raw_candidates(video_id: str) -> list[Path]:
    """Только финальные файлы вида <id>.mp4, без .part и .f137.mp4."""
    raw_dir = Path(cfg.WORK_DIR) / "raw"
    if not raw_dir.is_dir():
        return []
    return [p for p in raw_dir.iterdir()
            if p.is_file() and p.stem == video_id
            and p.suffix.lower() in composer.VIDEO_EXT]


def _clean_corrupt_raw(video_id: str) -> None:
    for path in _raw_candidates(video_id):
        log.info("Удаляю битый исходник %s — скачаю заново", path.name)
        path.unlink(missing_ok=True)


def _video_from_entry(entry: dict, path: Path) -> dict:
    """Метаданные для уже скачанного файла, если прогресса ещё нет."""
    video_id = entry["id"]
    channel = (entry.get("channel") or entry.get("uploader")
               or entry.get("playlist_title") or "channel")
    try:
        duration = composer.probe_duration(path)
    except Exception:
        duration = float(entry.get("duration") or 0)
    return {
        "video_id": video_id,
        "title": entry.get("title") or video_id,
        "channel": channel,
        "author": entry.get("uploader") or channel,
        "url": (entry.get("webpage_url") or entry.get("url")
                or f"https://www.youtube.com/watch?v={video_id}"),
        "duration": duration,
        "path": path,
    }


def _resume_video(entry: dict, saved: dict | None) -> dict | None:
    """Ищет уже скачанный исходник, чтобы не качать видео заново."""
    video_id = entry["id"]
    expected = float(entry.get("duration") or 0)
    candidates: list[Path] = []
    raw_path = (saved or {}).get("raw_path")
    if raw_path:
        candidates.append(Path(raw_path))
    candidates.extend(_raw_candidates(video_id))

    seen = set()
    for path in candidates:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        if not path.exists():
            continue
        if not _media_ok(path):
            _clean_corrupt_raw(video_id)
            continue
        duration, _ = _media_info(path)
        if expected and duration < expected * 0.9:
            log.info("Исходник %s короче ожидаемого (%.0f из %.0f с) — перекачаю",
                     path.name, duration, expected)
            _clean_corrupt_raw(video_id)
            continue
        meta = (saved or {}).get("video") or downloader.read_meta(video_id)
        if meta:
            video = dict(meta)
            video["video_id"] = video_id
        else:
            video = _video_from_entry(entry, path)
        video["path"] = path
        video.setdefault("duration", duration)
        return video
    return None


def _is_done(saved: dict | None) -> bool:
    """Всё загружено и письмо отправлено — осталось только отметить в базе."""
    if not saved:
        return False
    if saved.get("finished"):
        return bool(saved.get("uploaded"))
    total = len(saved.get("segments") or [])
    uploaded = len(saved.get("uploaded") or [])
    failed = len(saved.get("failed") or [])
    if not total or not uploaded:
        return False
    if uploaded < total - failed:
        return False
    return bool(saved.get("mail_sent")) or not cfg.EMAIL_ENABLED


# ---------------------------------------------------------------- части с прогрессом

def _part_dir(video_id: str, index: int) -> Path:
    return Path(cfg.WORK_DIR) / "clips" / video_id / f"part{index:02d}"


def _clip_path(video: dict, index: int, total: int) -> Path:
    return (Path(cfg.OUTPUT_DIR) / video["video_id"]
            / uploader.clip_filename(video["channel"], video["title"], index, total))


def _part_usable(part: dict, ass_path: Path | None = None) -> bool:
    """Сохранённой подготовки хватает, чтобы смонтировать часть заново."""
    if not part or part.get("start") is None or part.get("end") is None:
        return False
    if ass_path is not None and not Path(ass_path).exists():
        return False
    voice = part.get("voice_path")
    if voice and not Path(voice).exists():
        return False
    backgrounds = part.get("backgrounds") or []
    return all(Path(frag.get("path", "")).exists() for frag in backgrounds)


def _render_settings() -> dict:
    """Настройки, которые нужны монтажу внутри дочернего процесса.

    На Windows пул процессов запускает новые интерпретаторы: изменения cfg,
    сделанные из меню, туда не попадают. Поэтому передаём их вместе с заданием.
    """
    names = (
        "BACKGROUND_MODE", "BACKGROUND_VOLUME", "TOP_RATIO",
        "MUSIC_VOLUME", "KEEP_ORIGINAL_AUDIO",
        "BANNER_ENABLED", "BANNER_DIR", "BANNER_MINUTE", "BANNER_MIN_TAIL",
        "BANNER_DURATION", "BANNER_MAX",
    )
    return {name: getattr(cfg, name) for name in names}


def _free_ram_gb() -> float | None:
    """Сколько свободной оперативной памяти, ГБ. None — узнать не вышло."""
    try:
        import ctypes

        class _Status(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("load", ctypes.c_ulong),
                ("total_phys", ctypes.c_ulonglong),
                ("avail_phys", ctypes.c_ulonglong),
                ("total_page", ctypes.c_ulonglong),
                ("avail_page", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("avail_virtual", ctypes.c_ulonglong),
                ("avail_extended", ctypes.c_ulonglong),
            ]

        status = _Status()
        status.length = ctypes.sizeof(_Status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return status.avail_phys / (1024 ** 3)
    except Exception:
        return None


def _render_workers() -> int:
    """Сколько частей монтировать одновременно — с оглядкой на память.

    Каждому ffmpeg на кадре 1080x1920 нужно около 2 ГБ. Если памяти меньше,
    система убивает воркер и пул процессов рассыпается (BrokenProcessPool
    в логе), после чего монтаж всё равно идёт по очереди — лучше сразу так.
    """
    workers = max(1, int(getattr(cfg, "PARALLEL_JOBS", 1)))
    free = _free_ram_gb()
    if free is None:
        return workers
    per_job, reserve = 2.0, 1.0
    fits = max(1, int((free - reserve) // per_job))
    return max(1, min(workers, fits))


def _part_data(video: dict, index: int, total: int, part: dict) -> dict:
    start, end = float(part["start"]), float(part["end"])
    backgrounds = part.get("backgrounds") or []
    out_path = _clip_path(video, index, total)
    voice = part.get("voice_path")
    music = part.get("music_path")
    job = {
        "clip_path": str(video["path"]),
        "start": start,
        "end": end,
        "backgrounds": backgrounds,
        "ass_path": str(_part_dir(video["video_id"], index) / "sub.ass"),
        "out_path": str(out_path),
        "voice_path": voice if voice and Path(voice).exists() else None,
        "music_path": music if music and Path(music).exists() else None,
        "settings": _render_settings(),
    }
    return {
        "index": index,
        "start": start,
        "end": end,
        "job": job,
        "out_path": out_path,
        "backgrounds": backgrounds,
        "words": int(part.get("words") or 0),
        "voice_path": job["voice_path"],
        "music_path": job["music_path"],
        "description": part.get("description")
        or uploader.build_description(video, index, total, start, end, backgrounds),
    }


def _render_one(data: dict, tries: int, failed: list[dict]) -> bool:
    """Монтаж одной части по очереди: страховка, если пул процессов сорвался."""
    for attempt in range(1, tries + 1):
        try:
            composer.render_clip(data["job"])
        except Exception as e:
            if attempt < tries:
                ui.block(ui.WARN, f"часть {data['index']:02d}: попытка {attempt} "
                                  f"не удалась — {_short(e)}, повторяю")
                time.sleep(cfg.RETRY_PAUSE)
                continue
            failed.append({"index": data["index"], "error": _short(e)})
            Path(data["out_path"]).unlink(missing_ok=True)
            ui.block(ui.FAIL, f"часть {data['index']:02d}: не смонтировалась "
                              f"после {attempt} попыток")
            return False
        data["done"] = True
        ui.block(ui.OK, f"часть {data['index']:02d}: смонтирована")
        return True
    return False


def _part_record(data: dict) -> dict:
    """То, что нужно сохранить в прогресс после подготовки части."""
    return {
        "start": data["start"],
        "end": data["end"],
        "words": data["words"],
        "backgrounds": data["backgrounds"],
        "voice_path": data.get("voice_path"),
        "music_path": data.get("music_path"),
        "description": data["description"],
    }


# ---------------------------------------------------------------- одна часть

def extract_wav(video_path: Path, start: float, end: float, wav_path: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
         "-i", str(video_path), "-vn", "-ac", "1", "-ar", str(cfg.SILENCE_SAMPLE_RATE),
         str(wav_path)],
        check=True, capture_output=True,
    )
    return wav_path


def _prepare_part(video: dict, index: int, total: int, start: float, end: float,
                  video_dir: Path) -> dict:
    part_dir = Path(cfg.WORK_DIR) / "clips" / video["video_id"] / f"part{index:02d}"
    part_dir.mkdir(parents=True, exist_ok=True)

    length = end - start
    wav = extract_wav(video["path"], start, end, part_dir / "audio.wav")
    words, language = subtitles.transcribe_detail(wav)

    voice_path = None
    if cfg.TRANSLATE and language and not language.startswith(cfg.TRANSLATE_TO):
        captions = voiceover.translate_captions(subtitles.group_phrases(words))
        voice_path = voiceover.build_track(captions, length, part_dir)
        if voice_path is None:
            ui.block(ui.WARN, f"часть {index:02d}: озвучка не получилась — "
                              f"оставлю оригинальный звук")
    else:
        captions = subtitles.group_words(words)

    y = composer.caption_y(composer.probe_size(video["path"]))
    ass_path = subtitles.write_ass(captions, part_dir / "sub.ass", y)
    backgrounds = composer.pick_backgrounds(length)
    music = composer.pick_music()

    out_path = video_dir / uploader.clip_filename(
        video["channel"], video["title"], index, total)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    job = {
        "clip_path": str(video["path"]),
        "start": start,
        "end": end,
        "backgrounds": backgrounds,
        "ass_path": str(ass_path),
        "out_path": str(out_path),
        "voice_path": str(voice_path) if voice_path else None,
        "music_path": str(music) if music else None,
        "settings": _render_settings(),
    }
    return {
        "index": index,
        "start": start,
        "end": end,
        "job": job,
        "out_path": out_path,
        "backgrounds": backgrounds,
        "words": len(captions),
        "voice_path": str(voice_path) if voice_path else None,
        "music_path": str(music) if music else None,
        "description": uploader.build_description(video, index, total, start, end, backgrounds),
    }


# ---------------------------------------------------------------- одно видео

def process_video(video: dict, con, drive: uploader.DriveUploader,
                  mailer: uploader.Mailer, dry_run: bool) -> dict:
    """Скачано → нарезка → монтаж → Диск → письмо → уборка. Возвращает статистику.

    Ход сохраняется в work/progress/<video_id>.json: если программу закрыли,
    следующий запуск продолжит с того же места.
    """
    video["path"] = Path(video["path"])
    saved = None if dry_run else progress.load(video["video_id"])
    if saved and saved.get("video"):
        # Название и канал берём из прогресса: в списке канала они могут быть короче.
        for key in ("title", "channel", "author", "url", "duration"):
            if saved["video"].get(key):
                video[key] = saved["video"][key]
    if not video.get("duration") and video["path"].exists():
        try:
            video["duration"] = composer.probe_duration(video["path"])
        except Exception:
            video["duration"] = 0

    ui.open_block(f"«{video['title']}»")
    if video["path"].exists():
        try:
            size = video["path"].stat().st_size
            ui.block(ui.OK, f"скачано: {ui.fmt_size(size)}, "
                            f"{ui.fmt_duration(video.get('duration') or 0)}")
        except OSError:
            pass
    elif saved:
        ui.block(ui.SKIP, "исходник уже удалён — работаю по сохранённому прогрессу")

    if saved and _is_done(saved):
        ui.block(ui.OK, "всё уже загружено и письмо отправлено в прошлый раз")
        if not dry_run:
            downloader.mark_processed(con, video["video_id"], video["channel"], video["title"])
            if cfg.DELETE_LOCAL_AFTER_UPLOAD:
                shutil.rmtree(Path(cfg.OUTPUT_DIR) / video["video_id"], ignore_errors=True)
                shutil.rmtree(Path(cfg.WORK_DIR) / "clips" / video["video_id"],
                              ignore_errors=True)
                video["path"].unlink(missing_ok=True)
                downloader.delete_meta(video["video_id"])
            progress.delete(video["video_id"])
        uploaded = saved.get("uploaded") or []
        ui.close_block(f"{ui.OK} готово: {ui.clips(len(uploaded))}")
        return {"made": len(uploaded), "uploaded": uploaded,
                "failed": len(saved.get("failed") or []),
                "sent": bool(saved.get("mail_sent"))}

    if saved and saved.get("segments"):
        ui.block(ui.SKIP, "продолжаю с места остановки: готовое заново не делаю")
        segments = [(float(pair[0]), float(pair[1])) for pair in saved["segments"]]
    else:
        if not video["path"].exists():
            raise RuntimeError("исходник пропал, а прогресса нет — скачаю заново "
                               "в следующий запуск")
        try:
            pcm = cutter.extract_pcm(video["path"])
            result = cutter.find_cuts(pcm)
            segments = cutter.make_segments(result)
        except Exception as e:
            raise RuntimeError(f"не удалось найти паузы: {_short(e)}") from e

    if not segments:
        ui.block(ui.WARN, "видео слишком короткое — делить нечего")
        ui.close_block(f"{ui.SKIP} клипов нет")
        if not dry_run:
            progress.delete(video["video_id"])
        video["path"].unlink(missing_ok=True)
        downloader.delete_meta(video["video_id"])
        if not dry_run:
            downloader.mark_processed(con, video["video_id"], video["channel"], video["title"])
        return {"made": 0, "uploaded": [], "failed": 0, "sent": False}

    total = len(segments)
    ui.block(ui.OK, f"найдено пауз: {total} — будет {ui.clips(total)}")

    state = {
        "version": progress.VERSION,
        "video": {key: video.get(key) for key in
                  ("video_id", "title", "channel", "author", "url", "duration")},
        "raw_path": str(video["path"]),
        "segments": [[round(start, 3), round(end, 3)] for start, end in segments],
        "parts": (saved or {}).get("parts") or {},
        "failed": [],
        "uploaded": (saved or {}).get("uploaded") or [],
        "mail_sent": bool((saved or {}).get("mail_sent")),
        "finished": False,
    }

    def save_state() -> None:
        if not dry_run:
            progress.save(state)

    save_state()

    video_dir = Path(cfg.OUTPUT_DIR) / video["video_id"]
    tries = 1 + max(0, cfg.CLIP_RETRIES)

    prepared, failed = [], []
    resumed = 0
    for index, (start, end) in enumerate(segments, start=1):
        label = f"часть {index:02d}/{total:02d}"
        out_path = _clip_path(video, index, total)
        part = state["parts"].get(str(index))

        # 1. Клип уже смонтирован и цел — ничего не переделываем.
        if part and out_path.exists() and _media_ok(
                out_path, min_duration=(end - start) * 0.5):
            data = _part_data(video, index, total, part)
            data["done"] = True
            prepared.append(data)
            resumed += 1
            continue

        # 2. Подготовка (распознавание, фон) сохранилась — только перемонтируем.
        #    Нужны и субтитры: без них ffmpeg упадёт на ass=filename=sub.ass.
        if part and _part_usable(part, _part_dir(video["video_id"], index) / "sub.ass"):
            data = _part_data(video, index, total, part)
            prepared.append(data)
            if out_path.exists():
                out_path.unlink(missing_ok=True)
            ui.block(ui.SKIP, f"{label}: распознавание уже готово — только смонтирую")
            continue

        # 3. Готовим часть с нуля.
        last_error: Exception | None = None
        for attempt in range(1, tries + 1):
            try:
                data = _prepare_part(video, index, total, start, end, video_dir)
            except Exception as e:
                last_error = e
                if attempt < tries:
                    ui.block(ui.WARN, f"{label}: попытка {attempt} не удалась — "
                                      f"{_short(e)}, повторяю")
                    time.sleep(cfg.RETRY_PAUSE)
                continue
            prepared.append(data)
            state["parts"][str(index)] = _part_record(data)
            save_state()
            mode = getattr(cfg, "BACKGROUND_MODE", "folder")
            background_label = (
                f"{len(data['backgrounds'])} фрагм." if mode == "folder"
                else {"blur": "размытое видео", "black": "чёрный"}.get(mode, mode)
            )
            ui.block(ui.OK, f"{label}: {start:.1f}–{end:.1f} с, "
                            f"слов {data['words']}, фон {background_label}")
            break
        else:
            failed.append({"index": index, "error": _short(last_error)})
            ui.block(ui.FAIL, f"{label}: не подготовилась после {tries} попыток — "
                              f"{_short(last_error)}")

    if resumed:
        ui.block(ui.SKIP, f"готовых частей с прошлого раза: {resumed} из {total}")

    # Монтаж — параллельно; каждая часть получает свои дополнительные попытки.
    pending = [data for data in prepared if not data.get("done")]
    if pending:
        ui.info(f"монтирую {len(pending)} из {total}…")
        workers = _render_workers()
        if workers < max(1, int(cfg.PARALLEL_JOBS)):
            ui.note(f"свободной памяти мало — монтирую в {workers} поток(а)")
        try:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = {}
                for data in pending:
                    data["render_tries"] = 1
                    futures[pool.submit(composer.render_clip, data["job"])] = data

                while futures:
                    done, _ = wait(list(futures), return_when=FIRST_COMPLETED)
                    for future in done:
                        data = futures.pop(future)
                        try:
                            future.result()
                        except BrokenProcessPool:
                            # Пул умер целиком: повторять в него бессмысленно,
                            # уходим в последовательный монтаж ниже.
                            raise
                        except Exception as e:
                            if data["render_tries"] < tries:
                                ui.block(ui.WARN, f"часть {data['index']:02d}: попытка "
                                                  f"{data['render_tries']} не удалась — "
                                                  f"{_short(e)}, повторяю")
                                data["render_tries"] += 1
                                futures[pool.submit(composer.render_clip, data["job"])] = data
                            else:
                                failed.append({"index": data["index"], "error": _short(e)})
                                Path(data["out_path"]).unlink(missing_ok=True)
                                ui.block(ui.FAIL, f"часть {data['index']:02d}: не смонтировалась "
                                                  f"после {data['render_tries']} попыток")
                        else:
                            data["done"] = True
                            ui.block(ui.OK, f"часть {data['index']:02d}: смонтирована")
        except BrokenProcessPool:
            ui.block(ui.WARN, "параллельный монтаж сорвался — доделаю по очереди")
            time.sleep(2)
            for data in pending:
                if data.get("done"):
                    continue
                out_path = Path(data["out_path"])
                if out_path.exists() and _media_ok(
                        out_path, min_duration=(data["end"] - data["start"]) * 0.5):
                    data["done"] = True
                    continue
                data["render_tries"] = 1
                _render_one(data, tries, failed)

    clips = []
    for data in prepared:
        path = Path(data["out_path"])
        if data.get("done") and _media_ok(path, min_duration=(data["end"] - data["start"]) * 0.5):
            clips.append({
                "path": path,
                "name": path.name,
                "description": data["description"],
                "index": data["index"],
                "start": data["start"],
                "end": data["end"],
            })
        elif not any(item["index"] == data["index"] for item in failed):
            failed.append({"index": data["index"], "error": "часть не смонтировалась"})
    clips.sort(key=lambda clip: clip["index"])

    state["failed"] = failed
    save_state()

    ui.block(ui.OK if not failed else ui.WARN,
             f"смонтировано {len(clips)} из {total}"
             + (f", пропущено {len(failed)}" if failed else ""))
    if not clips:
        close = ui.FAIL
        ui.close_block(f"{close} ни одна часть не смонтировалась")
        raise RuntimeError("ни одна часть не смонтировалась")

    preview = Path(cfg.OUTPUT_DIR) / f"{video['video_id']}_preview.png"
    try:
        composer.make_preview(clips[0]["path"], preview)
        ui.block(ui.OK, f"превью для проверки: {preview.name}")
    except Exception as e:
        ui.block(ui.WARN, f"превью не сделалось: {_short(e)}")

    existing = state.get("uploaded") or []
    if existing:
        ui.block(ui.SKIP, f"уже на Диске с прошлого раза: {ui.clips(len(existing))} — "
                          f"повторно не гружу")

    def on_uploaded(record: dict) -> None:
        state["uploaded"].append(record)
        save_state()

    uploaded = drive.upload_video(video, clips, missing=failed, existing=existing,
                                  on_uploaded=on_uploaded)
    if dry_run:
        ui.block(ui.SKIP, f"проверочный режим: на Диск не загружено — {ui.clips(len(uploaded))}")
    else:
        ui.block(ui.OK, f"на Google Диск загружено: {ui.clips(len(uploaded))}")
        if video.get("drive_folder_link"):
            ui.block(ui.OK, f"папка на Диске: {video['drive_folder_link']}")

    sent = False
    mailed_ok = True
    if uploaded and cfg.EMAIL_ENABLED and mailer is not None:
        if state["mail_sent"] and not dry_run:
            sent = True
            ui.block(ui.SKIP, "письмо уже отправлялось в прошлый раз — повторно не шлю")
        else:
            try:
                mailer.send_video_report(video, uploaded, failed)
                if dry_run:
                    ui.block(ui.SKIP, "проверочный режим: письмо не отправлялось")
                else:
                    sent = True
                    state["mail_sent"] = True
                    save_state()
                    ui.block(ui.OK, f"письмо отправлено: {mailer.recipient()}")
            except Exception as e:
                mailed_ok = False
                ui.block(ui.FAIL, f"письмо не ушло: {_short(e)}")
                ui.note("Клипы оставил на компьютере, чтобы ничего не потерялось.")

    cleaned = False
    if (cfg.DELETE_LOCAL_AFTER_UPLOAD and not dry_run and uploaded and mailed_ok
            and (sent or not cfg.EMAIL_ENABLED)):
        for clip in clips:
            Path(clip["path"]).unlink(missing_ok=True)
        shutil.rmtree(video_dir, ignore_errors=True)
        shutil.rmtree(Path(cfg.WORK_DIR) / "clips" / video["video_id"], ignore_errors=True)
        video["path"].unlink(missing_ok=True)
        downloader.delete_meta(video["video_id"])
        cleaned = True
        ui.block(ui.OK, "исходник и клипы удалены с компьютера")

    if not dry_run and uploaded:
        state["finished"] = True
        save_state()
        downloader.mark_processed(con, video["video_id"], video["channel"], video["title"])
        progress.delete(video["video_id"])

    tail = f"{ui.OK} готово: {ui.clips(len(uploaded))}"
    if failed:
        tail += f", пропущено {ui.clips(len(failed))}"
    if not cleaned and not dry_run:
        tail += f" {ui.DOT} файлы оставлены"
    ui.close_block(tail)

    return {
        "made": len(clips),
        "uploaded": uploaded,
        "failed": len(failed),
        "sent": sent,
    }


# ---------------------------------------------------------------- весь запуск

def _pretty_channel(url: str) -> str:
    name = url.rstrip("/").split("/")[-1]
    return name if name.startswith("@") else url


def _profiles(data: dict, channels: list[str] | None) -> list[dict]:
    """Кого обрабатываем: явные каналы из командной строки или всех ботов."""
    if channels:
        return [{"name": None, "bot": None, "channels": list(channels)}]
    result = []
    for bot in settings.enabled_bots(data):
        if bot.get("channels"):
            result.append({"name": bot["name"], "bot": bot,
                           "channels": list(bot["channels"])})
    return result


def _run_profile(profile: dict, con, dry_run: bool, stats: dict,
                 errors: list[str]) -> bool:
    """Один бот: свой Google-Диск, своя почта, свои каналы.

    True — пора остановить весь запуск (лимит видео или серия неудач).
    """
    data = stats["data"]
    bot = profile["bot"]
    if bot is not None:
        settings.apply(data, bot)
        ui.blank()
        ui.banner(f"Бот: {profile['name']}",
                  f"каналов: {len(profile['channels'])} · Диск: {cfg.DRIVE_ROOT_FOLDER} · "
                  f"почта: {bot.get('email_to') or 'свой адрес'}")
    else:
        settings.apply(data)

    # У нового бота ещё нет своего входа в Google — не роняем из-за него
    # весь запуск, просто честно пропускаем и говорим, что сделать.
    if bot is not None and not dry_run and not Path(cfg.GOOGLE_TOKEN).exists():
        ui.warn(f"Пропускаю «{profile['name']}»: нет входа в Google "
                f"(файла {cfg.GOOGLE_TOKEN} нет)")
        ui.note("Меню → БОТЫ → этот бот → GOOGLE: ПОЧТА И ДИСК → ВОЙТИ В GOOGLE")
        return False

    try:
        drive = uploader.DriveUploader(dry_run=dry_run)
        # Токен мог истечь уже после прошлого запуска — проверяем до скачивания,
        # иначе бот потратит час на монтаж и упадёт на загрузке.
        drive.check()
    except uploader.GoogleAuthError as e:
        ui.fail(f"Google не пускает «{profile['name'] or 'без имени'}»: {e}")
        ui.note(f"Войдите заново: {uploader.RELOGIN_HINT}")
        ui.note("В режиме Testing Google refresh-токен живёт 7 дней — переведите "
                "приложение в Production (README, раздел «Google Drive»).")
        errors.append(f"{profile['name'] or 'Google'}: {e}")
        return False
    except Exception as e:
        text = _short(e)
        ui.fail(f"Google Диск недоступен: {text}")
        if any(key in text.lower() for key in ("find the server", "getaddrinfo",
                                               "name or service", "nodename")):
            ui.note("Похоже, пропал интернет или DNS. Проверьте подключение и VPN.")
        else:
            ui.note(f"Проверьте интернет и файлы {cfg.GOOGLE_CREDENTIALS} / "
                    f"{cfg.GOOGLE_TOKEN}.")
        errors.append(f"{profile['name'] or 'Google'}: {text}")
        return False

    mailer = uploader.Mailer(dry_run=dry_run, drive_service=drive.service)
    if cfg.EMAIL_ENABLED and not dry_run:
        try:
            ui.info(f"Почта: {mailer.recipient()}")
        except Exception as e:
            ui.fail(f"Почта недоступна: {_short(e)}")
            if isinstance(e, uploader.GoogleAuthError):
                ui.note(f"Войдите заново: {uploader.RELOGIN_HINT}")
            ui.note("Клипы загрузятся на Диск, но письма не будет — файлы оставлю "
                    "на компьютере.")
            mailer = None

    targets = profile["channels"]
    budget = cfg.MAX_VIDEOS_PER_RUN
    max_attempts = budget * 2 if budget else 0
    per_author = max(1, cfg.VIDEOS_PER_AUTHOR)

    total_channels = len(targets)
    queues: dict[str, list[dict] | None] = {channel: None for channel in targets}
    finished: set[str] = set()
    round_no = 0

    # Идём по кругу: берём партию видео у одного автора, потом у следующего,
    # и так пока у авторов не закончатся новые видео.
    while True:
        round_no += 1
        worked = False

        for number, channel_url in enumerate(targets, start=1):
            if channel_url in finished:
                continue
            if budget and stats["videos_ok"] >= budget:
                ui.warn(f"Лимит {budget} видео на запуск исчерпан — остальные каналы "
                        f"подхватим в следующий раз")
                return True
            if max_attempts and stats["videos_started"] >= max_attempts:
                ui.warn(f"Слишком много неудачных скачиваний "
                        f"({stats['videos_started']}) — остановлюсь, "
                        f"остальное попробую в следующий раз")
                return True

            label = f"Канал {_pretty_channel(channel_url)}"
            if round_no > 1:
                label = f"Круг {round_no} · {label}"
            ui.step(number, total_channels, label)

            queue = queues[channel_url]
            if queue is None:
                try:
                    entries = downloader.list_new_videos(channel_url, con, limit=None)
                except Exception as e:
                    ui.fail(f"не удалось получить список видео: {_short(e)}")
                    errors.append(f"{channel_url}: {_short(e)}")
                    finished.add(channel_url)
                    continue
                queue = queues[channel_url] = entries
                if not entries:
                    ui.ok("новых видео нет")
                    finished.add(channel_url)
                    continue
                ui.ok(f"новых видео: {len(entries)}")

            batch = queue[:per_author]
            if not batch:
                finished.add(channel_url)
                continue
            del queue[:len(batch)]
            worked = True
            ui.info(f"взял {len(batch)} видео — у автора осталось {len(queue)}")

            for index, entry in enumerate(batch, start=1):
                if budget and stats["videos_ok"] >= budget:
                    ui.warn("Лимит видео на запуск исчерпан")
                    return True
                if max_attempts and stats["videos_started"] >= max_attempts:
                    ui.warn("Слишком много неудачных скачиваний — остальное в следующий раз")
                    return True

                stats["videos_started"] += 1
                saved = None if dry_run else progress.load(entry["id"])
                if saved and (saved.get("video") or {}).get("title"):
                    title = saved["video"]["title"]
                else:
                    title = entry.get("title") or entry["id"]
                ui.blank()
                ui.line(ui.bold(f"Видео {index} из {len(batch)}: «{title}»"))

                if saved and _is_done(saved):
                    ui.skip("уже обработано в прошлый раз — только отмечаю в базе")
                    if not dry_run:
                        downloader.mark_processed(
                            con, entry["id"],
                            saved["video"].get("channel", entry.get("channel", "")),
                            title)
                        progress.delete(entry["id"])
                    done_clips = len(saved.get("uploaded") or [])
                    stats["videos_ok"] += 1
                    stats["clips_made"] += done_clips
                    stats["clips_uploaded"] += done_clips
                    stats["skipped_parts"] += len(saved.get("failed") or [])
                    stats["mails"] += 1 if saved.get("mail_sent") else 0
                    continue

                video = _resume_video(entry, saved)
                if video is not None:
                    ui.skip("уже скачано с прошлого раза — заново не качаю")
                    if saved:
                        ui.info("продолжаю с места остановки")
                else:
                    ui.info("скачиваю…")
                    try:
                        video = downloader.download_video(entry)
                    except Exception as e:
                        ui.fail(f"не скачалось: {_short(e)}")
                        errors.append(f"{title}: {_short(e)}")
                        continue

                if video is None:
                    ui.skip("пропущено: длительность вне разрешённого диапазона")
                    if not dry_run:
                        downloader.mark_processed(
                            con, entry["id"], entry.get("channel", ""), title)
                    continue

                try:
                    result = process_video(video, con, drive, mailer, dry_run)
                except uploader.GoogleAuthError as e:
                    ui.fail(f"Google: {_short(e)}")
                    ui.note(f"Войдите заново: {uploader.RELOGIN_HINT}")
                    errors.append(f"{title}: {_short(e)}")
                    ui.note(f"Остальные видео «{profile['name'] or 'бота'}» пропускаю — "
                            "сначала войдите в Google.")
                    return False
                except Exception as e:
                    log.exception("Ошибка на видео %s", entry.get("id"))
                    ui.fail(f"ошибка обработки: {_short(e)}")
                    errors.append(f"{title}: {_short(e)}")
                    continue

                stats["videos_ok"] += 1
                stats["clips_made"] += result["made"]
                stats["clips_uploaded"] += len(result["uploaded"])
                stats["skipped_parts"] += result["failed"]
                stats["mails"] += 1 if result["sent"] else 0

        if not worked:
            return False


def run_all(channels: list[str] | None = None, dry_run: bool = False) -> int:
    started = time.time()
    os.chdir(Path(__file__).resolve().parent)

    data = settings.load()
    settings.apply(data)

    ui.banner("Бот-нарезчик TikTok / Shorts",
              "YouTube и TikTok → вертикальные клипы → Google Диск + письмо")
    if dry_run:
        ui.warn("ПРОВЕРОЧНЫЙ ЗАПУСК: на Диск ничего не грузим, файлы не удаляем")

    for folder in (cfg.WORK_DIR, cfg.OUTPUT_DIR):
        Path(folder).mkdir(parents=True, exist_ok=True)

    con = downloader.init_state()
    downloader.enable_doh_fallback()
    progress.purge_processed(con)
    unfinished = progress.pending()
    if unfinished and not dry_run:
        ui.info(f"незавершённых видео с прошлого раза: {len(unfinished)} — "
                f"продолжу с места остановки")

    profiles = _profiles(data, channels)
    if not profiles:
        ui.warn("Нет включённых ботов с каналами. Меню → «+» → добавьте каналы")
        return 1

    stats = {"data": data, "videos_started": 0, "videos_ok": 0, "clips_made": 0,
             "clips_uploaded": 0, "skipped_parts": 0, "mails": 0}
    errors: list[str] = []

    for number, profile in enumerate(profiles, start=1):
        if len(profiles) > 1:
            ui.step(number, len(profiles), f"бот {profile['name'] or 'без имени'}")
        if _run_profile(profile, con, dry_run, stats, errors):
            break

    # ------------------------------------------------------------ итог
    ui.banner("Итог")
    ui.kv("Видео обработано", stats["videos_ok"])
    ui.kv("Клипов смонтировано", stats["clips_made"])
    ui.kv("Загружено на Google Диск", stats["clips_uploaded"])
    ui.kv("Пропущено частей", stats["skipped_parts"])
    ui.kv("Писем отправлено", stats["mails"])
    ui.kv("Ошибок", len(errors))
    ui.kv("Время", ui.fmt_duration(time.time() - started))

    if errors:
        ui.blank()
        ui.header_line("Что не получилось:")
        for error in errors:
            ui.fail(error)

    if errors and any(word in " ".join(errors).lower()
                      for word in ("resolve", "connection", "reset", "timed out")):
        ui.blank()
        ui.warn("Похоже, провайдер блокирует YouTube. Включите VPN "
                "или укажите PROXY в config.py (см. README).")

    ui.blank()
    ui.info(f"Отчёт: {Path(cfg.OUTPUT_DIR) / uploader.REPORT_NAME}")
    ui.info("Журнал: bot.log")
    ui.blank()

    return 0 if not errors else 1


# ---------------------------------------------------------------- вход

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Бот-нарезчик Shorts/TikTok из YouTube-видео")
    parser.add_argument("--no-menu", action="store_true",
                        help="запустить сразу, без стартового меню")
    parser.add_argument("--channel", action="append",
                        help="обработать только этот канал (можно несколько раз)")
    parser.add_argument("--dry-run", action="store_true",
                        help="всё делаем, но на Диск не грузим и файлы не удаляем")
    parser.add_argument("--once", action="store_true", help="устаревший синоним обычного запуска")
    args = parser.parse_args(argv)

    os.chdir(Path(__file__).resolve().parent)
    setup_logging()

    if args.no_menu or args.channel or args.dry_run:
        return run_all(channels=args.channel, dry_run=args.dry_run)

    from bot import menu

    try:
        menu.loop()
    except KeyboardInterrupt:
        ui.blank()
        ui.info("Выход")
    return 0


if __name__ == "__main__":
    sys.exit(main())
