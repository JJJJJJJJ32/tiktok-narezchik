# -*- coding: utf-8 -*-
"""Быстрая самопроверка: импорты, письмо, отчёт, режимы фона."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import composer  # noqa: E402
import config as cfg  # noqa: E402
import settings  # noqa: E402
import subtitles  # noqa: E402
import ui  # noqa: E402
import uploader  # noqa: E402

assert settings.load()["bots"], "settings.json без ботов"
settings.save(settings.load())  # запись/чтение без потерь
settings.apply(settings.load())

# --- письмо ---
mailer = uploader.Mailer(dry_run=True)
mailer._address = "me@example.com"
video = {
    "video_id": "abc", "title": "Тестовое видео", "channel": "Канал",
    "author": "Автор", "url": "https://youtu.be/abc", "duration": 484.7,
}
clips = [
    {"name": "p1.mp4", "link": "https://drive.google.com/file/d/1/view",
     "index": 1, "start": 0.0, "end": 45.2},
    {"name": "p2.mp4", "link": "https://drive.google.com/file/d/2/view",
     "index": 2, "start": 45.2, "end": 90.4},
]
message = mailer._build(video, clips, [{"index": 3, "error": "ffmpeg упал"}])
assert "Тестовое видео" in message["Subject"]
assert "https://drive.google.com/file/d/1/view" in message.get_body(
    preferencelist=("html",)).get_content()
print("письмо: OK")

# --- отчёт ---
tmp = Path(tempfile.mkdtemp(prefix="bot_test_"))
cfg.OUTPUT_DIR = str(tmp)
drive = uploader.DriveUploader(dry_run=True)
links = drive.upload_video(video, [
    {"path": tmp / "p1.mp4", "name": "p1.mp4", "description": "d",
     "index": 1, "start": 0.0, "end": 45.2},
], missing=[{"index": 2, "error": "нет"}])
assert links and links[0]["link"].startswith("file://")
rows = list(__import__("csv").reader((tmp / uploader.REPORT_NAME).open(
    encoding="utf-8-sig", newline=""), delimiter=";"))
assert rows[0] == uploader.REPORT_HEADER, rows[0]
assert len(rows[0]) == len(rows[1]) == 9, rows
print("отчёт: OK")

# --- режимы фона ---
sample = tmp / "sample.mp4"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                "-i", "testsrc=size=640x360:rate=30:duration=5",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                "-shortest", str(sample)], check=True)
ass = subtitles.write_ass([], tmp / "sub.ass")
shutil.copyfile(ROOT / cfg.FONT_FILE, tmp / "font.ttf")

for mode in ("black", "blur"):
    cfg.BACKGROUND_MODE = mode
    job = {
        "clip_path": str(sample), "start": 0.0, "end": 3.0,
        "backgrounds": [], "ass_path": str(ass),
        "out_path": str(tmp / f"out_{mode}.mp4"),
    }
    composer.render_clip(job)
    size = (tmp / f"out_{mode}.mp4").stat().st_size
    assert size > 10000, (mode, size)
    print(f"фон {mode}: OK ({size} байт)")

cfg.BACKGROUND_MODE = "folder"
cfg.OUTPUT_DIR = "output/"
shutil.rmtree(tmp, ignore_errors=True)
ui.banner("Самопроверка пройдена")
print("ВСЁ ОК")
