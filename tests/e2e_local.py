# -*- coding: utf-8 -*-
"""Локальный прогон одного короткого видео: без сети, без Диска, без удаления."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))

    from bot import config as cfg
    from bot import downloader
    import main as runner
    from bot import ui
    from bot import uploader

    source = root / "work" / "raw" / "pKm3HiOv3gg.mp4"
    if not source.exists():
        print("нет исходника для теста")
        return

    tmp = Path(tempfile.mkdtemp(prefix="e2e_"))
    raw = tmp / "raw"
    raw.mkdir()
    sample = raw / "test.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-t", "55", "-i", str(source),
                    "-c", "copy", str(sample)], check=True)

    cfg.OUTPUT_DIR = str(tmp / "output")
    cfg.WORK_DIR = str(tmp / "work")
    cfg.STATE_DB = str(tmp / "state.sqlite")
    cfg.DELETE_LOCAL_AFTER_UPLOAD = False
    cfg.BACKGROUND_MODE = "folder"
    cfg.BACKGROUND_DIR = str(root / "backgrounds")
    cfg.FONT_FILE = str(root / "fonts" / "Montserrat-Black.ttf")
    cfg.CLIP_RETRIES = 1

    runner.setup_logging()
    con = downloader.init_state()
    video = {
        "video_id": "test", "title": "Тестовое видео", "channel": "Тест",
        "author": "Автор", "url": "https://youtu.be/test", "duration": 55.0,
        "path": sample,
    }
    result = runner.process_video(
        video, con, uploader.DriveUploader(dry_run=True),
        uploader.Mailer(dry_run=True), dry_run=True)

    print("РЕЗУЛЬТАТ:", result)
    assert result["made"] >= 1, result
    assert Path(cfg.OUTPUT_DIR, "test").exists()
    assert sample.exists(), "в dry-run исходник удалять нельзя"
    shutil.rmtree(tmp, ignore_errors=True)
    ui.banner("Локальный прогон пройден")


if __name__ == "__main__":
    main()
