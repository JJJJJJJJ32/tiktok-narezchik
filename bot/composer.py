# -*- coding: utf-8 -*-
"""Подбор фона, музыки и сборка вертикального клипа одним проходом FFmpeg."""

import hashlib
import logging
import os
import random
import shutil
import subprocess
from pathlib import Path

from . import config as cfg

log = logging.getLogger(__name__)

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac", ".aac"}

# Режимы, где видео занимает весь кадр (фон не нужен).
FULL_FRAME = {"width", "crop"}


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe: {out.stderr.strip()[:200]}")
    return float(out.stdout.strip())


def probe_size(path: Path) -> tuple[int, int]:
    """Ширина и высота первого видеопотока."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "csv=s=x:p=0", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        width, height = out.stdout.strip().split("x")[:2]
        return int(width), int(height)
    except Exception:
        return 0, 0


def list_backgrounds() -> list[Path]:
    folder = Path(cfg.BACKGROUND_DIR)
    if not folder.exists():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in VIDEO_EXT)


def pick_backgrounds(total: float, rng: random.Random | None = None) -> list[dict]:
    """Фрагменты фона общей длиной ровно total (склейка, если ролик короче)."""
    if getattr(cfg, "BACKGROUND_MODE", "folder") in FULL_FRAME:
        return []  # видео занимает весь кадр — отдельные файлы не нужны
    if getattr(cfg, "BACKGROUND_MODE", "folder") != "folder":
        return []  # размытое видео или чёрный фон — тоже не нужны
    rng = rng or random
    files = list_backgrounds()
    if not files:
        raise RuntimeError(f"В папке {cfg.BACKGROUND_DIR} нет фоновых видео")

    frags, acc = [], 0.0
    while acc < total - 0.01:
        path = rng.choice(files)
        duration = probe_duration(path)
        take = min(duration, total - acc)
        if take < 0.05:
            break
        start = rng.uniform(0.0, max(0.0, duration - take))
        frags.append({"path": str(path), "start": round(start, 3), "duration": round(take, 3)})
        acc += take
    return frags


def list_music() -> list[Path]:
    folder = Path(cfg.MUSIC_DIR)
    if not folder.exists():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in AUDIO_EXT)


def pick_music(rng: random.Random | None = None) -> Path | None:
    """Случайный трек из папки музыки (None — музыки нет)."""
    if not getattr(cfg, "MUSIC_ENABLED", False):
        return None
    files = list_music()
    if not files:
        log.warning("Музыка включена, но папка %s пуста", cfg.MUSIC_DIR)
        return None
    return (rng or random).choice(files)


def probe_audio(path: Path) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return bool(out.stdout.strip())


def list_banners() -> list[Path]:
    folder = Path(cfg.BANNER_DIR)
    if not folder.exists():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in VIDEO_EXT)


def banner_positions(total: float, minute: float | None = None,
                     min_tail: float | None = None) -> list[float]:
    """Середина каждой минуты; последняя неполная — посередине неё.

    60 с → [30]; 90 с → [30, 75]; 45 с → [22.5]. Совсем короткий хвост
    (меньше min_tail) отдельного баннера не получает.
    """
    minute = float(minute if minute is not None else cfg.BANNER_MINUTE)
    min_tail = float(min_tail if min_tail is not None else cfg.BANNER_MIN_TAIL)
    minute = max(20.0, minute)
    if total < 20:
        return []

    points: list[float] = []
    start = 0.0
    while start < total - 0.001:
        end = min(start + minute, total)
        length = end - start
        if points and length < min_tail:
            break
        point = start + length / 2
        # После баннера должно остаться хотя бы 5 секунд клипа.
        if point < total - 5:
            points.append(round(point, 3))
        start = end
    return points


def pick_banners(total: float, rng: random.Random | None = None) -> list[dict]:
    """Рекламные вставки для клипа: [{'path','at','duration'}]."""
    if not getattr(cfg, "BANNER_ENABLED", False):
        return []
    files = list_banners()
    if not files:
        log.debug("Реклама включена, но папка %s пуста", cfg.BANNER_DIR)
        return []

    rng = rng or random
    positions = banner_positions(total)
    result, previous = [], None
    for moment in positions:
        choice = rng.choice([p for p in files if p != previous] or files)
        previous = choice
        duration = cfg.BANNER_DURATION
        if not duration or duration <= 0:
            try:
                duration = probe_duration(choice)
            except Exception:
                duration = 4.0
        duration = min(float(duration), float(getattr(cfg, "BANNER_MAX", 15.0)),
                       max(0.5, total - moment))
        result.append({"path": str(choice), "at": moment,
                       "duration": round(duration, 3)})
    return result


# Готовые сцены рекламы: размытый стоп-кадр клипа + сам баннер сверху.
_ready_cache: dict[str, Path] = {}


def _freeze_frame(clip: Path, moment: float, out: Path) -> None:
    """Стоп-кадр клипа в момент вставки — из него делаем размытый фон."""
    moment = max(0.0, float(moment))
    error = ""
    for seek in (["-ss", f"{moment:.3f}"], ["-sseof", "-0.2"]):
        result = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", *seek, "-i", str(clip),
             "-frames:v", "1", "-q:v", "2", str(out)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode == 0 and out.exists() and out.stat().st_size > 0:
            return
        error = (result.stderr or "").strip()[-300:]
    raise RuntimeError(f"кадр из {clip.name}: {error}")


def banner_scene(banner: Path, seconds: float, clip: Path, moment: float) -> Path:
    """Сцена рекламы 1080x1920: размытый стоп-кадр клипа + баннер по центру.

    Фон у баннера не вырезаем: кладём ролик поверх как есть, сохраняя его
    прозрачность (HEVC alpha и т.п.). Иначе однотонный фон съедал картинку.
    """
    seconds = round(float(seconds), 3)
    moment = round(float(moment), 3)
    key = hashlib.md5(
        f"scene|{banner.resolve()}|{banner.stat().st_mtime if banner.exists() else 0}|"
        f"{seconds}|{clip.resolve()}|{clip.stat().st_mtime if clip.exists() else 0}|"
        f"{moment}|{cfg.WIDTH}x{cfg.HEIGHT}|{cfg.FPS}".encode("utf-8")
    ).hexdigest()[:16]
    if key in _ready_cache and _ready_cache[key].exists():
        return _ready_cache[key]

    folder = Path(cfg.WORK_DIR) / "banners_ready"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"scene_{key}.mp4"
    if out.exists() and out.stat().st_size > 0:
        try:
            if probe_duration(out) > 0.1:
                _ready_cache[key] = out
                return out
        except Exception:
            pass  # битый файл от прошлого сбоя — перерендерим

    frame = folder / f"frame_{key}.jpg"
    _freeze_frame(clip, moment, frame)

    # Ролик короче нужной вставки — зацикливаем, иначе реклама оборвётся
    # и сегменты клипа разъедутся по времени.
    loop: list[str] = []
    try:
        if probe_duration(banner) < seconds - 0.05:
            loop = ["-stream_loop", "-1"]
    except Exception:
        pass

    has_audio = probe_audio(banner)
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-loop", "1", "-framerate", str(cfg.FPS), "-t", f"{seconds:.3f}",
           "-i", str(frame.resolve()), *loop, "-i", str(banner.resolve())]
    if not has_audio:
        cmd += ["-f", "lavfi", "-t", f"{seconds:.3f}",
                "-i", "anullsrc=r=44100:cl=stereo"]

    # Фон — стоп-кадр клипа на весь кадр, сильно размытый.
    background = (f"[0:v]scale={cfg.WIDTH}:{cfg.HEIGHT}:"
                  f"force_original_aspect_ratio=increase,"
                  f"crop={cfg.WIDTH}:{cfg.HEIGHT},boxblur=24:2,"
                  f"fps={cfg.FPS},setsar=1[bg]")
    # Баннер вписываем целиком, прозрачность сохраняем — тогда виден и фон.
    foreground = (f"[1:v]trim=0:{seconds:.3f},setpts=PTS-STARTPTS,format=rgba,"
                  f"scale={cfg.WIDTH}:{cfg.HEIGHT}:"
                  f"force_original_aspect_ratio=decrease:flags=lanczos[fg]")
    mix = (f"[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1,"
           f"format=yuv420p,fps={cfg.FPS},setsar=1[v]")

    source = "[1:a]" if has_audio else "[2:a]"
    audio = (f"{source}atrim=0:{seconds:.3f},asetpts=PTS-STARTPTS,"
             f"aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
             f"apad,atrim=0:{seconds:.3f}[a]")

    # Пишем во временный файл своего процесса и переименовываем: при
    # параллельном монтаже два процесса не должны портить общий кэш.
    tmp = out.with_name(f"{out.stem}.{os.getpid()}.tmp.mp4")
    try:
        result = subprocess.run(
            [*cmd, "-filter_complex",
             f"{background};{foreground};{mix};{audio}",
             "-map", "[v]", "-map", "[a]",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
             "-t", f"{seconds:.3f}", "-movflags", "+faststart", str(tmp.resolve())],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            detail = (result.stderr or "").strip()[-500:] or "ffmpeg ничего не написал"
            raise RuntimeError(f"реклама {banner.name}: {detail}")
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
        frame.unlink(missing_ok=True)

    _ready_cache[key] = out
    return out


def caption_y(video_size: tuple[int, int] | None = None) -> int:
    """Где ставить субтитры: на стыке видео и нижней части кадра."""
    mode = getattr(cfg, "BACKGROUND_MODE", "folder")
    if mode == "crop" or (video_size and min(video_size) <= 0):
        return int(cfg.HEIGHT * 0.82)
    if mode in ("folder", "blur", "black"):
        # Ровно стык: верхняя половина — видео, нижняя — фон.
        return int(round(cfg.HEIGHT * cfg.TOP_RATIO))
    if mode == "width" and video_size:
        width, height = video_size
        video_h = round(cfg.WIDTH * height / width)
        video_h = min(cfg.HEIGHT, max(1, video_h))
        top = (cfg.HEIGHT - video_h) // 2
        # Нижняя кромка видео — там же, где начинаются поля, но не у самого
        # края кадра (иначе вертикальное видео обрежет субтитры).
        return min(top + video_h, int(cfg.HEIGHT * 0.92))
    return int(cfg.HEIGHT * 0.82)


def _video_chain(label: str, start: float, end: float, top_h: int) -> str:
    return (
        f"trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,"
        f"scale={cfg.WIDTH}:{top_h}:force_original_aspect_ratio=increase,"
        f"crop={cfg.WIDTH}:{top_h},fps={cfg.FPS},setsar=1[{label}]"
    )


def _full_frame_chain(source: str, start: float, end: float) -> str:
    """Видео на весь кадр 9:16: по ширине с полями или с обрезкой краёв."""
    trim = (f"trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,")
    if getattr(cfg, "BACKGROUND_MODE", "width") == "crop":
        return (f"{source}{trim}"
                f"scale={cfg.WIDTH}:{cfg.HEIGHT}:force_original_aspect_ratio=increase,"
                f"crop={cfg.WIDTH}:{cfg.HEIGHT},"
                f"fps={cfg.FPS},setsar=1[base]")
    # width: вписываем по ширине и добиваем чёрным сверху/снизу.
    return (f"{source}{trim}"
            f"scale={cfg.WIDTH}:-2:flags=lanczos,"
            f"pad={cfg.WIDTH}:{cfg.HEIGHT}:0:(oh-ih)/2:color=black,"
            f"fps={cfg.FPS},setsar=1[base]")


def render_clip(job: dict) -> str:
    """Собирает один клип.

    job: clip_path, start, end, backgrounds, ass_path, out_path,
         voice_path (необязательно), music_path (необязательно).
    """
    clip = Path(job["clip_path"])
    start, end = job["start"], job["end"]
    # В дочернем процессе настройки из меню не наследуются: Windows запускает
    # новый интерпретатор с заводским config.py. Применяем их из задания.
    for name, value in (job.get("settings") or {}).items():
        setattr(cfg, name, value)
    ass_path = Path(job["ass_path"]).resolve()
    out_path = Path(job["out_path"]).resolve()
    total = round(end - start, 3)
    top_h = int(cfg.HEIGHT * cfg.TOP_RATIO)
    bottom_h = cfg.HEIGHT - top_h

    work_dir = ass_path.parent
    work_dir.mkdir(parents=True, exist_ok=True)

    inputs: list[str] = []

    def add_input(path, extra: tuple = ()) -> int:
        inputs.extend([*extra, "-i", str(Path(path).resolve())])
        return sum(1 for item in inputs if item == "-i") - 1

    clip_index = add_input(clip)
    backgrounds = job.get("backgrounds") or []
    bg_indexes = [add_input(frag["path"]) for frag in backgrounds]
    voice_path = job.get("voice_path")
    music_path = job.get("music_path")
    voice_index = add_input(voice_path) if voice_path else None
    # Музыку зацикливаем, но чтение ограничиваем длиной клипа: бесконечный
    # вход не даёт ffmpeg нормально завершить файл.
    music_index = (add_input(music_path, ("-stream_loop", "-1",
                                          "-t", f"{total:.3f}"))
                   if music_path else None)

    # Рекламные вставки готовим заранее отдельным проходом: сборка сцены
    # внутри общего фильтра на длинном клипе съедает память и роняет ffmpeg.
    banner_specs = []
    for item in pick_banners(total):
        try:
            ready = banner_scene(Path(item["path"]), item["duration"],
                                 clip, start + item["at"])
        except Exception as e:
            log.warning("Реклама %s не подготовлена, пропускаю: %s", item["path"], e)
            continue
        banner_specs.append({
            "index": add_input(ready),
            "at": item["at"],
            "duration": item["duration"],
        })

    mode = getattr(cfg, "BACKGROUND_MODE", "width")
    chains: list[str] = []
    bg_audio: str | None = None

    if mode in FULL_FRAME:
        chains.append(_full_frame_chain(f"[{clip_index}:v]", start, end))
    else:
        chains.append(_video_chain("top", start, end, top_h))
        if mode == "blur":
            # Нижняя половина — то же видео, растянутое и размытое.
            chains.append(
                f"[{clip_index}:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,"
                f"scale={cfg.WIDTH}:{bottom_h}:force_original_aspect_ratio=increase,"
                f"crop={cfg.WIDTH}:{bottom_h},boxblur=24:2,fps={cfg.FPS},setsar=1[bottom]"
            )
        elif mode == "black":
            chains.append(
                f"color=c=black:s={cfg.WIDTH}x{bottom_h}:r={cfg.FPS}:d={total:.3f},"
                f"setsar=1[bottom]"
            )
        else:
            labels, audio_labels = [], []
            for number, (index, frag) in enumerate(zip(bg_indexes, backgrounds), start=1):
                f_start = frag["start"]
                f_end = f_start + frag["duration"]
                chains.append(
                    f"[{index}:v]trim=start={f_start:.3f}:end={f_end:.3f},setpts=PTS-STARTPTS,"
                    f"scale={cfg.WIDTH}:{bottom_h}:force_original_aspect_ratio=increase,"
                    f"crop={cfg.WIDTH}:{bottom_h},fps={cfg.FPS},setsar=1[bg{number}]"
                )
                labels.append(f"[bg{number}]")
                if cfg.BACKGROUND_VOLUME > 0:
                    chains.append(
                        f"[{index}:a]atrim=start={f_start:.3f}:end={f_end:.3f},"
                        f"asetpts=PTS-STARTPTS,volume={cfg.BACKGROUND_VOLUME}[bga{number}]"
                    )
                    audio_labels.append(f"[bga{number}]")

            if cfg.BACKGROUND_VOLUME > 0:
                chains.append("".join(labels) + "".join(audio_labels)
                              + f"concat=n={len(labels)}:v=1:a=1[bottom][bgmix]")
                bg_audio = "[bgmix]"
            else:
                chains.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0[bottom]")
        chains.append("[top][bottom]vstack=inputs=2[base]")

    # ---------------- звук ----------------
    audio: list[str] = []
    if voice_index is not None:
        chains.append(f"[{voice_index}:a]aformat=sample_fmts=fltp:sample_rates=44100:"
                      f"channel_layouts=stereo[voice]")
        audio.append("[voice]")
        if cfg.KEEP_ORIGINAL_AUDIO:
            chains.append(f"[{clip_index}:a]atrim=start={start:.3f}:end={end:.3f},"
                          f"asetpts=PTS-STARTPTS,volume=0.20[orig]")
            audio.append("[orig]")
    else:
        chains.append(f"[{clip_index}:a]atrim=start={start:.3f}:end={end:.3f},"
                      f"asetpts=PTS-STARTPTS[aorig]")
        audio.append("[aorig]")

    if music_index is not None:
        fade = max(0.0, total - 2.0)
        chains.append(
            f"[{music_index}:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,"
            f"volume={cfg.MUSIC_VOLUME:.3f},"
            f"afade=t=out:st={fade:.3f}:d={min(2.0, total):.3f}[music]"
        )
        audio.append("[music]")

    if bg_audio is not None:
        audio.append(bg_audio)

    if len(audio) > 1:
        chains.append("".join(audio)
                      + f"amix=inputs={len(audio)}:duration=first:dropout_transition=0:"
                        f"normalize=0[aout]")
    else:
        chains.append(f"{audio[0]}anull[aout]")

    chains.append("[base]ass=filename=sub.ass:fontsdir=.[video]")

    # ---------------- рекламные вставки ----------------
    duration_out = total
    if banner_specs:
        points = [0.0] + [b["at"] for b in banner_specs] + [total]
        segments = len(points) - 1
        chains.append(f"[video]split={segments}"
                      + "".join(f"[v{i}]" for i in range(segments)))
        chains.append("[aout]aformat=sample_fmts=fltp:sample_rates=44100:"
                      "channel_layouts=stereo[aouta]")
        chains.append(f"[aouta]asplit={segments}"
                      + "".join(f"[a{i}]" for i in range(segments)))

        order: list[str] = []
        for i in range(segments):
            seg_start, seg_end = points[i], points[i + 1]
            length = max(0.05, seg_end - seg_start)
            chains.append(f"[v{i}]trim=start={seg_start:.3f}:end={seg_end:.3f},"
                          f"setpts=PTS-STARTPTS,fps={cfg.FPS},setsar=1[cv{i}]")
            chains.append(f"[a{i}]atrim=start={seg_start:.3f}:end={seg_end:.3f},"
                          f"asetpts=PTS-STARTPTS,apad,atrim=0:{length:.3f}[ca{i}]")
            order += [f"[cv{i}]", f"[ca{i}]"]

            if i < len(banner_specs):
                banner = banner_specs[i]
                seconds = banner["duration"]
                # Ролик уже 1080×1920 с чёрной заливкой вместо фона.
                chains.append(f"[{banner['index']}:v]trim=0:{seconds:.3f},"
                              f"setpts=PTS-STARTPTS,fps={cfg.FPS},setsar=1[bv{i}]")
                chains.append(f"[{banner['index']}:a]atrim=0:{seconds:.3f},"
                              f"asetpts=PTS-STARTPTS,"
                              f"aformat=sample_fmts=fltp:sample_rates=44100:"
                              f"channel_layouts=stereo,apad,atrim=0:{seconds:.3f}[ba{i}]")
                order += [f"[bv{i}]", f"[ba{i}]"]
                duration_out += seconds

        chains.append("".join(order)
                      + f"concat=n={len(order) // 2}:v=1:a=1[vout][aout2]")
        video_label, audio_label = "[vout]", "[aout2]"
    else:
        video_label, audio_label = "[video]", "[aout]"

    codec_args = ["-c:v", cfg.VIDEO_CODEC, "-pix_fmt", "yuv420p"]
    if "nvenc" in cfg.VIDEO_CODEC:
        codec_args += ["-rc", "vbr", "-cq", str(cfg.CRF), "-preset", "p5"]
    else:
        codec_args += ["-crf", str(cfg.CRF), "-preset", cfg.PRESET]

    cmd = (
        ["ffmpeg", "-y", "-v", "error", *inputs,
         "-filter_complex", ";".join(chains),
         "-map", video_label, "-map", audio_label,
         *codec_args, "-c:a", "aac", "-b:a", "192k",
         "-t", f"{duration_out:.3f}", "-movflags", "+faststart", str(out_path.resolve())]
    )

    # Шрифт кладём рядом с .ass: filtersdir=. и никаких проблем с ':' в путях Windows.
    font_src = Path(cfg.FONT_FILE)
    if font_src.exists():
        shutil.copyfile(font_src, work_dir / "font.ttf")

    log.debug("ffmpeg: %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(work_dir), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        detail = result.stderr.strip()[-800:]
        if not detail:
            # ffmpeg упал молча — обычно это нехватка памяти или убитый процесс.
            detail = (f"код {result.returncode}, ffmpeg ничего не написал "
                      f"(чаще всего не хватило памяти)")
        raise RuntimeError(f"ffmpeg: {detail}")
    return str(out_path)


def make_preview(video_path: Path, png_path: Path) -> Path:
    """Кадр из середины клипа — для визуальной проверки раскладки."""
    middle = probe_duration(video_path) / 2
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{middle:.2f}", "-i", str(video_path),
         "-frames:v", "1", str(png_path)],
        check=True, capture_output=True,
    )
    return png_path
