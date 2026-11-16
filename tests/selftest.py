# -*- coding: utf-8 -*-
"""Проверки конвейера на сгенерированных данных (без YouTube и без Google Drive).

Запуск: python tests/selftest.py            — всё, кроме тяжёлого Whisper
       python tests/selftest.py --whisper   — плюс распознавание русской речи
       python tests/selftest.py --render    — плюс сборка клипа и превью
"""

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import composer  # noqa: E402
import config as cfg  # noqa: E402
import cutter  # noqa: E402
import subtitles  # noqa: E402
import uploader  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
checks = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append(ok)
    print(("  OK  " if ok else "  FAIL") + f"  {name}" + (f" — {detail}" if detail else ""))


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def ffprobe(path: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-show_entries",
               "format=duration:stream=codec_name,width,height,r_frame_rate",
               "-of", "json", str(path)])
    return json.loads(out.stdout)


# ---------------- 1. Поиск пауз: C++ против Python ----------------
def make_synth_audio(path: Path, duration: float = 100.0, pauses=((44.2, 45.0), (89.6, 90.4))):
    sr = cfg.SILENCE_SAMPLE_RATE
    t = np.arange(int(sr * duration)) / sr
    rng = np.random.default_rng(0)
    signal = 0.3 * np.sin(2 * np.pi * 220 * t) + rng.normal(0, 0.01, len(t))
    for a, b in pauses:
        signal[(t >= a) & (t < b)] = rng.normal(0, 0.0005, int(((t >= a) & (t < b)).sum()))
    pcm = (np.clip(signal, -1, 1) * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return pcm


def test_silence():
    print("\n[1] C++ и Python ищут одинаковые паузы")
    FIX.mkdir(parents=True, exist_ok=True)
    pcm = make_synth_audio(FIX / "synth.wav")

    py = cutter._python_find(pcm)
    check("Python нашёл 2 реза", len(py["cuts"]) == 2, str(py["cuts"]))
    check("Python попал в паузу 1 (44.6±0.1)", abs(py["cuts"][0] - 44.6) <= 0.1, f"{py['cuts'][0]:.3f}")
    check("Python попал в паузу 2 (90.0±0.1)", abs(py["cuts"][1] - 90.0) <= 0.1, f"{py['cuts'][1]:.3f}")

    if not cutter.CPP_BIN.exists():
        check("C++ собран", False, f"нет {cutter.CPP_BIN.name}, запусти python build_cpp.py")
        return
    cpp = cutter._cpp_find(pcm)
    diff = max(abs(a - b) for a, b in zip(cpp["cuts"], py["cuts"]))
    check("C++ и Python дают одинаковый результат (±0.01 с)", diff <= 0.01, f"макс. разница {diff:.4f} с")


# ---------------- 2. Нарезка длинного видео ----------------
def make_test_video(path: Path, duration: int = 200):
    if path.exists():
        return
    print(f"  генерация тестового видео {duration} с…")
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc=size=1280x720:rate=30:duration={duration}",
         "-f", "lavfi", "-i", f"sine=frequency=300:duration={duration}",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)])


def test_segments():
    print("\n[2] Нарезка 200-секундного видео")
    video = FIX / "test200.mp4"
    make_test_video(video)
    result = cutter.find_cuts(cutter.extract_pcm(video))
    segments = cutter.make_segments(result)
    lengths = [round(b - a, 2) for a, b in segments]
    check("4 реза по 45 с (200 с → 45×4 + хвост 20)", len(segments) == 5, str(lengths))
    check("все клипы, кроме хвоста, ~45 с", all(abs(x - 45) <= 0.2 for x in lengths[:-1]), str(lengths))
    check("хвост ровно 20 с и не отброшен", abs(lengths[-1] - 20) <= 0.2, str(lengths[-1]))

    short = cutter.make_segments({"duration": 100.0, "cuts": [45.0, 90.0], "threshold": 0})
    check("хвост 10 с (< MIN_LAST_CLIP=20) отброшен", len(short) == 2, str(short))


# ---------------- 3. Фон ----------------
def make_backgrounds():
    bg_dir = FIX / "bg"
    bg_dir.mkdir(parents=True, exist_ok=True)
    for i, color in enumerate(("blue", "green")):
        path = bg_dir / f"bg{i}.mp4"
        if not path.exists():
            run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                 "-i", f"color=c={color}:size=1280x720:rate=30:duration=20",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
                 "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)])


def test_backgrounds():
    print("\n[3] Фон: склейка коротких роликов до нужной длины")
    make_backgrounds()
    old_dir, old_mode = cfg.BACKGROUND_DIR, cfg.BACKGROUND_MODE
    cfg.BACKGROUND_DIR = str(FIX / "bg")
    cfg.BACKGROUND_MODE = "folder"      # фоны нужны только в этом режиме
    try:
        for need in (45.0, 12.5, 61.0):
            frags = composer.pick_backgrounds(need)
            total = sum(f["duration"] for f in frags)
            check(f"фон {need} с собран за {len(frags)} фрагм. (сумма {total:.2f})",
                  abs(total - need) <= 0.05, str([round(f['duration'], 2) for f in frags]))
        check("для 45 с из роликов по 20 с нужно 3 фрагмента",
              len(composer.pick_backgrounds(45.0)) == 3)
    finally:
        cfg.BACKGROUND_DIR, cfg.BACKGROUND_MODE = old_dir, old_mode


# ---------------- 4. Субтитры ----------------
def make_speech(duration: int = 14) -> Path:
    wav = FIX / "speech.wav"
    if wav.exists():
        return wav
    FIX.mkdir(parents=True, exist_ok=True)
    text = ("Привет, друзья! Сегодня мы проходим мимо школы. "
            "Смотрите, какая интересная история начинается прямо сейчас. "
            "Подписывайтесь, будет ещё много всего интересного!")
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.SelectVoice('Microsoft Irina Desktop'); $s.Rate = 1; "
        f"$s.SetOutputToWaveFile('{wav}'); $s.Speak('{text}'); $s.Dispose()"
    )
    run(["powershell", "-NoProfile", "-Command", ps])
    if not wav.exists():
        return wav
    # ровно duration секунд
    out = FIX / "speech_loop.wav"
    run(["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-i", str(wav),
         "-t", str(duration), "-ac", "1", "-ar", "16000", str(out)])
    return out


def test_subtitles():
    print("\n[4] Субтитры: faster-whisper → .ass")
    wav = make_speech()
    if not wav.exists():
        check("русская речь сгенерирована (SAPI)", False)
        return
    words = subtitles.transcribe(wav)
    check("Whisper распознал слова", len(words) > 5, f"{len(words)} слов")
    if words:
        check("есть кириллица", any(any("а" <= c.lower() <= "я" for c in w["word"]) for w in words),
              " ".join(w["word"] for w in words[:8]))

    captions = subtitles.group_words(words)
    check("1–3 слова на строку", all(len(c["text"].split()) <= cfg.WORDS_PER_CAPTION for c in captions))
    check("текст КАПСОМ", all(c["text"] == c["text"].upper() for c in captions))

    ass = subtitles.write_ass(captions, FIX / "sub.ass")
    content = ass.read_text(encoding="utf-8")
    check(".ass содержит стиль с обводкой", "Style: Default" in content and f",{cfg.OUTLINE},0,5," in content)
    check("позиция ровно на стыке половин", f"\\pos({cfg.WIDTH // 2},{cfg.HEIGHT // 2})" in content)
    check("таймкоды в формате ASS", "Dialogue: 0,0:00:" in content)


# ---------------- 5. Сборка клипа ----------------
def test_render():
    print("\n[5] Сборка 1080×1920 одним проходом FFmpeg")
    source = FIX / "speech_video.mp4"
    if not source.exists():
        make_test_video(source, 20)
    old_bg = cfg.BACKGROUND_DIR
    cfg.BACKGROUND_DIR = str(FIX / "bg")
    try:
        words = subtitles.transcribe(make_speech())
        captions = subtitles.group_words(words)
        part = ROOT / "work" / "selftest"
        part.mkdir(parents=True, exist_ok=True)
        ass = subtitles.write_ass(captions, part / "sub.ass")
        out = ROOT / "output" / "selftest_clip.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)

        job = {
            "clip_path": str(source),
            "start": 0.0,
            "end": 18.0,
            "backgrounds": composer.pick_backgrounds(18.0),
            "ass_path": str(ass),
            "out_path": str(out),
        }
        composer.render_clip(job)

        data = ffprobe(out)
        stream = data["streams"][0]
        check("разрешение 1080×1920", (stream["width"], stream["height"]) == (1080, 1920),
              f"{stream['width']}×{stream['height']}")
        check("частота 30 кадров/с", stream["r_frame_rate"] == "30/1", stream["r_frame_rate"])
        check("видео H.264", stream["codec_name"] == "h264", stream["codec_name"])
        check("аудио AAC", any(s["codec_name"] == "aac" for s in data["streams"]))
        duration = float(data["format"]["duration"])
        check("длительность = сегменту (18 с ±0.05)", abs(duration - 18.0) <= 0.05, f"{duration:.3f} с")

        preview = composer.make_preview(out, ROOT / "output" / "selftest_preview.png")
        check("превью сохранено", preview.exists(), str(preview))
    finally:
        cfg.BACKGROUND_DIR = old_bg


# ---------------- 6. Имена, description, report.csv (без Drive) ----------------
class RecordingUploader(uploader.DriveUploader):
    def __init__(self):
        super().__init__(dry_run=True)
        self.calls = []

    def ensure_folder(self, name, parent=None):
        self.calls.append(("folder", name, parent))
        return f"folder:{name}"

    def upload_file(self, path, name, parent, description):
        self.calls.append(("file", name, parent, description))
        return {"id": "x", "link": f"https://drive/{name}"}


def test_upload_meta():
    print("\n[6] Имена файлов, папки, description, report.csv (dry-run)")
    video = {"channel": "История / Школа", "title": "Почему школа выглядит именно так?",
             "author": "Автор", "url": "https://youtube.com/watch?v=abc"}
    name = uploader.clip_filename(video["channel"], video["title"], 3, 12)
    check("имя без запрещённых символов", name == "История_Школа_Почему_школа_выглядит_именно_так_part03of12.mp4", name)

    desc = uploader.build_description(video, 3, 12, 90.0, 135.0,
                                      [{"path": "backgrounds/game1.mp4"}])
    check("description содержит часть 3 из 12", "Часть 3 из 12" in desc)
    check("description содержит таймкоды и ссылку", "90.0–135.0" in desc and video["url"] in desc)
    check("description содержит фон", "game1.mp4" in desc)

    up = RecordingUploader()
    clips = [{"path": ROOT / "output" / "a.mp4", "name": name, "description": desc}]
    links = up.upload_video(video, clips)
    kinds = [c[0] for c in up.calls]
    check("созданы папки корень/канал/видео", kinds == ["folder", "folder", "folder", "file"], str(kinds))
    check("вложенность папок верная",
          up.calls[0][1] == cfg.DRIVE_ROOT_FOLDER and up.calls[1][2] == f"folder:{cfg.DRIVE_ROOT_FOLDER}")
    check("ссылка на файл получена",
          links and links[0]["link"].startswith("https://drive/"), str(links))

    report = ROOT / "output" / "report.csv"
    rows = report.read_text(encoding="utf-8-sig").strip().splitlines()
    check("report.csv содержит строку видео", len(rows) >= 2 and "part03of12" in rows[-1], rows[-1][:80])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--whisper", action="store_true", help="включить распознавание речи")
    parser.add_argument("--render", action="store_true", help="включить сборку клипа")
    args = parser.parse_args()

    test_silence()
    test_segments()
    test_backgrounds()
    if args.whisper or args.render:
        test_subtitles()
    if args.render:
        test_render()
    test_upload_meta()

    ok = sum(checks)
    print(f"\nИтого: {ok}/{len(checks)} проверок пройдено")
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
