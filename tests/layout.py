# -*- coding: utf-8 -*-
"""Раскладка кадра: рекламные вставки, позиция субтитров, режимы кадра.

Ничего не рендерит и в сеть не ходит — только арифметика и строки фильтров.
Запуск:  .venv\\Scripts\\python.exe tests\\layout.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import composer
from bot import config as cfg

HEIGHT = cfg.HEIGHT


# ---------------------------------------------------------------- реклама

def check_banner_midpoints():
    # Баннер — в середине каждой минуты.
    assert composer.banner_positions(30) == [15.0]
    assert composer.banner_positions(45) == [22.5]      # неполная минута — посередине
    assert composer.banner_positions(60) == [30.0]
    assert composer.banner_positions(90) == [30.0, 75.0]
    assert composer.banner_positions(120) == [30.0, 90.0]
    assert composer.banner_positions(135) == [30.0, 90.0, 127.5]
    assert composer.banner_positions(150) == [30.0, 90.0, 135.0]


def check_banner_tail():
    # Хвост короче 15 секунд отдельного баннера не получает.
    assert composer.banner_positions(70) == [30.0]
    assert composer.banner_positions(75) == [30.0, 67.5]
    # Совсем короткий клип рекламу не получает.
    assert composer.banner_positions(10) == []


def check_banner_never_past_end():
    for total in (20, 45, 91, 121, 300, 601):
        for moment in composer.banner_positions(total):
            assert 0 < moment < total - 0.001, (total, moment)


# ---------------------------------------------------------------- субтитры

def check_caption_width_mode():
    old = cfg.BACKGROUND_MODE
    cfg.BACKGROUND_MODE = "width"
    try:
        # 16:9 на всю ширину: 1920x1080 → высота видео 608, сверху поле 656.
        y = composer.caption_y((1920, 1080))
        assert 656 < y < 656 + 608, y
        # Вертикальное видео на всю ширину занимает почти весь кадр.
        y = composer.caption_y((1080, 1920))
        assert 1500 < y < HEIGHT, y
        # Субтитры всегда внутри кадра.
        for size in ((1920, 1080), (1080, 1920), (640, 360), (3840, 2160)):
            y = composer.caption_y(size)
            assert 0 < y < HEIGHT - 40, (size, y)
    finally:
        cfg.BACKGROUND_MODE = old


def check_caption_other_modes():
    old_mode, old_ratio = cfg.BACKGROUND_MODE, cfg.TOP_RATIO
    try:
        cfg.BACKGROUND_MODE = "crop"
        assert composer.caption_y((1920, 1080)) == int(HEIGHT * 0.82)

        cfg.BACKGROUND_MODE = "folder"
        cfg.TOP_RATIO = 0.5
        y = composer.caption_y()
        assert int(HEIGHT * 0.5) < y < HEIGHT, y      # ниже стыка половин

        cfg.BACKGROUND_MODE = "blur"
        cfg.TOP_RATIO = 0.6
        assert composer.caption_y() == int(HEIGHT * (0.6 + 0.4 * 0.35))
    finally:
        cfg.BACKGROUND_MODE, cfg.TOP_RATIO = old_mode, old_ratio


# ---------------------------------------------------------------- кадр 9:16

def check_full_frame_chains():
    old = cfg.BACKGROUND_MODE
    try:
        cfg.BACKGROUND_MODE = "width"
        chain = composer._full_frame_chain("[0:v]", 1.0, 2.0)
        assert f"scale={cfg.WIDTH}:-2" in chain, chain
        assert f"pad={cfg.WIDTH}:{cfg.HEIGHT}" in chain, chain
        assert "crop=" not in chain, chain            # без обрезки краёв

        cfg.BACKGROUND_MODE = "crop"
        chain = composer._full_frame_chain("[0:v]", 1.0, 2.0)
        assert f"scale={cfg.WIDTH}:{cfg.HEIGHT}" in chain, chain
        assert f"crop={cfg.WIDTH}:{cfg.HEIGHT}" in chain, chain
        assert "pad=" not in chain, chain
    finally:
        cfg.BACKGROUND_MODE = old


def check_pick_backgrounds_skipped():
    old = cfg.BACKGROUND_MODE
    try:
        for mode in ("width", "crop", "blur", "black"):
            cfg.BACKGROUND_MODE = mode
            assert composer.pick_backgrounds(30.0) == [], mode
    finally:
        cfg.BACKGROUND_MODE = old


def main():
    check_banner_midpoints()
    check_banner_tail()
    check_banner_never_past_end()
    check_caption_width_mode()
    check_caption_other_modes()
    check_full_frame_chains()
    check_pick_backgrounds_skipped()
    print("ВСЁ ОК")


if __name__ == "__main__":
    main()
