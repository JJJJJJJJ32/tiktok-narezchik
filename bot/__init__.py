# -*- coding: utf-8 -*-
"""Бот-нарезчик YouTube/TikTok → Shorts.

Модули:
    config      — все настройки и пути
    settings    — settings.json, профили ботов
    ui          — консольный интерфейс
    menu        — интерактивное меню
    progress    — прогресс и продолжение после закрытия
    downloader  — скачивание YouTube/TikTok, состояние в state.sqlite
    cutter      — поиск паузы (C++ или numpy)
    subtitles   — распознавание речи и .ass
    composer    — монтаж клипа: кадр, музыка, реклама
    voiceover   — перевод и озвучка
    uploader    — Google Диск, письмо, отчёт

Точка входа — main.py в корне проекта: python main.py
"""

__version__ = "2.0"
