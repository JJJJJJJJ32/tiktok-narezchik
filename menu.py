# -*- coding: utf-8 -*-
"""Стартовое меню: запуск, настройки, статистика, проверка связи."""

import csv
import importlib.util
import os
import shutil
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

import composer
import config as cfg
import downloader
import settings
import ui
import uploader


# ---------------------------------------------------------------- утилиты

def _clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _ask(prompt: str = "Выбор", default: str = "") -> str:
    suffix = f" {ui.dim('[' + default + ']')}" if default else ""
    try:
        return input(f"   {ui.bold(prompt)}{suffix}: ").strip()
    except EOFError:
        return "0"


def _pause() -> None:
    try:
        input(f"\n   {ui.dim('Нажмите Enter, чтобы продолжить…')}")
    except EOFError:
        pass


def _open_path(path: Path) -> None:
    path = Path(path).resolve()
    if not path.exists():
        ui.warn(f"Нет файла: {path}")
        return
    try:
        if os.name == "nt":
            os.startfile(str(path))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        ui.ok(f"Открываю: {path}")
    except Exception as e:
        ui.fail(f"Не смог открыть: {e}")


def _save(data: dict) -> None:
    settings.save(data)
    settings.apply(data)


# ---------------------------------------------------------------- запуск

def _start() -> None:
    while True:
        _clear()
        ui.banner("Запуск")
        ui.choice(1, "ПОЛНЫЙ ЗАПУСК")
        ui.choice(2, "ПРОВЕРОЧНЫЙ ЗАПУСК")
        ui.choice(0, "НАЗАД")
        answer = _ask()
        if answer == "1":
            _run(dry_run=False)
            return
        if answer == "2":
            _run(dry_run=True)
            return
        if answer in ("0", ""):
            return


def _run(dry_run: bool) -> None:
    import main as runner

    try:
        code = runner.run_all(dry_run=dry_run)
    except KeyboardInterrupt:
        ui.blank()
        ui.warn("Остановлено вручную")
    except Exception as e:
        ui.blank()
        ui.fail(f"Запуск прервался: {e}")
        ui.note("Подробности — в bot.log")
    else:
        if code == 0:
            ui.ok("Запуск завершён без ошибок")
    _pause()


# ---------------------------------------------------------------- статистика

def _statistics() -> None:
    _clear()
    ui.banner("Статистика")

    con = downloader.init_state()
    total = con.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    ui.kv("Видео в базе (обработано)", total)

    rows = con.execute(
        "SELECT channel, COUNT(*) FROM videos GROUP BY channel ORDER BY 2 DESC"
    ).fetchall()
    if rows:
        ui.section("По каналам")
        for channel, count in rows:
            ui.info(f"{channel or 'без имени'} — {count}")

    last = con.execute("SELECT MAX(processed_at) FROM videos").fetchone()[0]
    ui.kv("Последняя обработка", last.replace("T", " ") if last else "—")

    clips = list(Path(cfg.OUTPUT_DIR).rglob("*.mp4")) if Path(cfg.OUTPUT_DIR).exists() else []
    size = sum(p.stat().st_size for p in clips)
    ui.kv("Клипов в папке output", f"{len(clips)} ({ui.fmt_size(size)})")

    report = Path(cfg.OUTPUT_DIR) / uploader.REPORT_NAME
    if report.exists():
        with report.open(encoding="utf-8-sig", newline="") as f:
            count = max(0, sum(1 for _ in csv.reader(f, delimiter=";")) - 1)
        ui.kv("Строк в отчёте", count)

    ui.blank()
    ui.choice(1, "ОТКРЫТЬ ОТЧЁТ")
    ui.choice(2, "ОТКРЫТЬ ПАПКУ OUTPUT")
    ui.choice(0, "НАЗАД")
    answer = _ask()
    if answer == "1":
        _open_path(report)
        _pause()
    elif answer == "2":
        _open_path(Path(cfg.OUTPUT_DIR))
        _pause()


# ---------------------------------------------------------------- боты

def _bots_menu() -> None:
    """Список ботов: выбрать или добавить нового."""
    while True:
        data = settings.load()
        bots = data["bots"]
        _clear()
        ui.banner("Боты")
        for number, bot in enumerate(bots, start=1):
            mark = "" if bot.get("enabled", True) else "  (выключен)"
            ui.choice(number, f"{bot.get('name') or f'Бот {number}'}{mark}",
                      f"каналов: {len(bot.get('channels') or [])}")
        ui.blank()
        ui.choice("a", "ДОБАВИТЬ БОТА", "своя почта, свой Диск, свои каналы")
        ui.choice(0, "НАЗАД")

        answer = _ask()
        if answer.isdigit() and 1 <= int(answer) <= len(bots):
            _bot_menu(int(answer) - 1)
        elif answer.lower() in ("a", "д", "add"):
            _create_bot()
        elif answer in ("0", ""):
            return


def _bot_menu(index: int) -> None:
    while True:
        data = settings.load()
        bots = data["bots"]
        if index >= len(bots):
            return

        bot = bots[index]
        name = (bot.get("name") or f"Бот {index + 1}").upper()

        _clear()
        ui.banner(name)
        ui.info(f"каналов: {len(bot['channels'])} · диск: {bot.get('drive_root')} · "
                f"почта: {bot.get('email_to') or 'свой адрес'}")
        if not bot.get("enabled", True):
            ui.warn("бот выключен — в запуске не участвует")
        ui.blank()
        ui.choice(1, "КАНАЛЫ (YouTube и TikTok)")
        ui.choice(2, "ФОН И КАДР", _mode_title())
        ui.choice(3, "РАЗМЕР ФОНА, ВИДЕО И РАСПОЛОЖЕНИЕ")
        ui.choice(4, "GOOGLE: ПОЧТА И ДИСК")
        ui.choice(5, "ВКЛЮЧЁН / ВЫКЛЮЧЕН",
                  "включён" if bot.get("enabled", True) else "выключен")
        ui.choice(6, "УДАЛИТЬ")
        ui.choice(0, "НАЗАД")

        answer = _ask()
        if answer == "1":
            _channels(index)
        elif answer == "2":
            _backgrounds()
        elif answer == "3":
            _layout()
        elif answer == "4":
            _bot_google(index)
        elif answer == "5":
            bot["enabled"] = not bot.get("enabled", True)
            _save(data)
            ui.ok("Бот " + ("включён" if bot["enabled"] else "выключен"))
            time.sleep(0.8)
        elif answer == "6":
            if len(bots) == 1:
                ui.warn("Последнего бота удалить нельзя")
                _pause()
                continue
            if _ask(f"Удалить «{name}»? yes/no", "no").lower() in ("yes", "y", "да"):
                bots.pop(index)
                _save(data)
                ui.ok(f"{name} удалён")
                time.sleep(0.7)
                return
        elif answer in ("0", ""):
            return


def _mode_title() -> str:
    return {
        "width": "на всю ширину + чёрные поля",
        "crop": "на весь кадр с обрезкой",
        "folder": "сверху видео, снизу фон",
        "blur": "сверху видео, снизу размытие",
        "black": "сверху видео, снизу чёрный",
    }.get(cfg.BACKGROUND_MODE, cfg.BACKGROUND_MODE)


def _bot_google(index: int) -> None:
    """Свой Google-аккаунт у бота: токен, папка на Диске, почта."""
    while True:
        data = settings.load()
        bots = data["bots"]
        if index >= len(bots):
            return
        bot = bots[index]
        settings.apply(data, bot)

        _clear()
        ui.banner(f"{bot['name']} — Google")
        ui.info(f"Диск: {bot['drive_root']} · почта: {bot.get('email_to') or 'свой адрес'}")
        token = Path(bot["token"])
        if token.exists():
            try:
                import json
                scopes = json.loads(token.read_text(encoding="utf-8")).get("scopes") or []
                ui.ok(f"вход выполнен, прав: {len(scopes)}")
            except Exception:
                ui.warn("файл токена не читается")
        else:
            ui.warn(f"входа нет — файла {bot['token']} не существует")
        ui.blank()
        ui.choice(1, "ВОЙТИ В GOOGLE", "откроется браузер")
        ui.choice(2, "ФАЙЛ ТОКЕНА", bot["token"])
        ui.choice(3, "ФАЙЛ КЛЮЧЕЙ", bot["credentials"])
        ui.choice(4, "ПАПКА НА ДИСКЕ", bot["drive_root"])
        ui.choice(5, "АДРЕС ПОЧТЫ", bot.get("email_to") or "свой")
        ui.choice(6, "ПРОВЕРИТЬ ВХОД")
        ui.choice(0, "НАЗАД")

        answer = _ask()
        if answer == "1":
            _google_login(index)
        elif answer == "2":
            value = _ask("Файл токена", bot["token"])
            if value:
                bot["token"] = value
                _save(data)
                ui.ok(f"Токен: {value}")
            time.sleep(0.8)
        elif answer == "3":
            value = _ask("Файл ключей (credentials.json)", bot["credentials"])
            if value:
                bot["credentials"] = value
                _save(data)
                ui.ok(f"Ключи: {value}")
            time.sleep(0.8)
        elif answer == "4":
            value = _ask("Корневая папка на Диске", bot["drive_root"])
            if value:
                bot["drive_root"] = value
                _save(data)
                ui.ok(f"Папка: {value}")
            time.sleep(0.8)
        elif answer == "5":
            value = _ask("Адрес (пусто — адрес Google-аккаунта)",
                         bot.get("email_to") or "")
            bot["email_to"] = value or None
            _save(data)
            ui.ok(f"Почта: {value or 'адрес аккаунта'}")
            time.sleep(0.8)
        elif answer == "6":
            _google_check(index)
        elif answer in ("0", ""):
            return


def _google_login(index: int) -> None:
    data = settings.load()
    bot = data["bots"][index]
    settings.apply(data, bot)
    _clear()
    ui.banner(f"{bot['name']} — вход в Google")
    ui.info("Сейчас откроется браузер. Войдите тем аккаунтом, на чей Диск "
            "и почту должен работать этот бот.")
    try:
        drive = uploader.DriveUploader()
        mailer = uploader.Mailer(drive_service=drive.service)
        address = mailer.account()
    except Exception as e:
        ui.fail(f"Не получилось: {e}")
    else:
        ui.ok(f"Готово. Аккаунт: {address}")
        ui.note(f"Клипы пойдут в папку «{bot['drive_root']}», "
                f"письма — на {bot.get('email_to') or address}")
    _pause()


def _google_check(index: int) -> None:
    data = settings.load()
    bot = data["bots"][index]
    settings.apply(data, bot)
    _clear()
    ui.banner(f"{bot['name']} — проверка")
    try:
        drive = uploader.DriveUploader()
        folder = drive.ensure_folder(cfg.DRIVE_ROOT_FOLDER)
        address = uploader.Mailer(drive_service=drive.service).account()
    except Exception as e:
        ui.fail(f"Не работает: {e}")
    else:
        ui.ok(f"Диск доступен, папка: {folder}")
        ui.ok(f"Аккаунт: {address}")
        if bot.get("email_to"):
            ui.info(f"Письма пойдут на {bot['email_to']}")
    _pause()


def _create_bot() -> bool:
    data = settings.load()
    if len(data["bots"]) >= 10:
        ui.warn("Больше 10 ботов не поддерживается")
        _pause()
        return False
    default = f"Бот {len(data['bots']) + 1}"
    name = _ask("Название бота", default) or default
    bot = settings.new_bot(data, name)
    _save(data)
    ui.ok(f"Создан «{name}»")
    ui.note(f"Диск: {bot['drive_root']} · токен: {bot['token']}")
    ui.note("Дальше: «GOOGLE: ПОЧТА И ДИСК» → войти в свой аккаунт, "
            "затем добавить каналы")
    _pause()
    return True


def _channels(index: int) -> None:
    while True:
        data = settings.load()
        bot = data["bots"][index]
        channels = bot["channels"]

        _clear()
        ui.banner(f"{bot['name']} — каналы")
        if channels:
            for number, channel in enumerate(channels, start=1):
                ui.choice(number, channel)
        else:
            ui.warn("Пока ни одного канала")
        ui.blank()
        ui.choice("a", "ДОБАВИТЬ КАНАЛ", "ссылка на YouTube-канал или TikTok-профиль")
        ui.choice("d", "УДАЛИТЬ КАНАЛ", "номер из списка")
        ui.choice(0, "НАЗАД")

        answer = _ask()
        if answer.lower() in ("a", "д", "add"):
            url = _ask("Ссылка", "https://www.youtube.com/@")
            if url and url.startswith("http"):
                if url in channels:
                    ui.warn("Такой канал уже есть")
                else:
                    channels.append(url)
                    _save(data)
                    kind = "TikTok" if "tiktok.com" in url else "YouTube"
                    ui.ok(f"Добавлен ({kind}): {url}")
            else:
                ui.warn("Нужна полная ссылка, начиная с https://")
            time.sleep(0.8)
        elif answer.lower() in ("d", "у", "del") and channels:
            number = _ask("Какой номер удалить")
            if number.isdigit() and 1 <= int(number) <= len(channels):
                removed = channels.pop(int(number) - 1)
                _save(data)
                ui.ok(f"Удалён: {removed}")
            else:
                ui.warn("Нет такого номера")
            time.sleep(0.8)
        elif answer in ("0", ""):
            return


def _backgrounds() -> None:
    while True:
        data = settings.load()
        _clear()
        ui.banner("Фон и кадр")
        ui.info(f"сейчас: {_mode_title()}")
        ui.blank()
        ui.choice(1, "ВИДЕО НА ВСЮ ШИРИНУ + ЧЁРНЫЕ ПОЛЯ", "как в примере")
        ui.choice(2, "ВИДЕО НА ВЕСЬ КАДР С ОБРЕЗКОЙ", "без полей, 9:16 целиком")
        ui.choice(3, "СВЕРХУ ВИДЕО, СНИЗУ ФОН ИЗ ПАПКИ")
        ui.choice(4, "СВЕРХУ ВИДЕО, СНИЗУ РАЗМЫТИЕ")
        ui.choice(5, "СВЕРХУ ВИДЕО, СНИЗУ ЧЁРНЫЙ")
        ui.choice(6, "СМЕНИТЬ ПАПКУ ФОНОВ")
        ui.choice(0, "НАЗАД")

        answer = _ask()
        if answer == "1":
            data["background_mode"] = "width"
            _save(data)
            ui.ok("Готово: видео на всю ширину, сверху и снизу чёрные поля")
            time.sleep(1.0)
        elif answer == "2":
            data["background_mode"] = "crop"
            _save(data)
            ui.ok("Готово: видео заполняет весь кадр, края обрезаются")
            time.sleep(1.0)
        elif answer == "3":
            data["background_mode"] = "folder"
            _save(data)
            files = composer.list_backgrounds()
            ui.ok(f"Готово: беру фоны из {cfg.BACKGROUND_DIR} ({len(files)} файлов)")
            if not files:
                ui.warn("Папка пуста — положите туда видео или выберите другой вариант")
            time.sleep(1.2)
        elif answer == "4":
            data["background_mode"] = "blur"
            _save(data)
            ui.ok("Готово: низ будет размытой копией видео")
            time.sleep(0.8)
        elif answer == "5":
            data["background_mode"] = "black"
            _save(data)
            ui.ok("Готово: нижняя половина будет чёрной")
            time.sleep(0.8)
        elif answer == "6":
            folder = _ask("Папка с фонами", cfg.BACKGROUND_DIR)
            if folder:
                data["backgrounds_dir"] = folder
                _save(data)
                ui.ok(f"Папка: {folder}")
                time.sleep(0.8)
        elif answer in ("0", ""):
            return


def _layout() -> None:
    while True:
        data = settings.load()
        _clear()
        ui.banner("Размер и расположение")
        ui.info(f"сверху {round(cfg.TOP_RATIO * 100)} %, "
                f"клип {cfg.CLIP_DURATION:.0f} с, шрифт {cfg.FONT_SIZE}")
        ui.blank()
        ui.choice(1, "ВЫСОТА ВИДЕО")
        ui.choice(2, "ДЛИНА КЛИПА")
        ui.choice(3, "РАЗМЕР СУБТИТРОВ")
        ui.choice(0, "НАЗАД")

        answer = _ask()
        if answer == "1":
            value = _ask("Процентов под видео сверху", str(round(cfg.TOP_RATIO * 100)))
            if value.isdigit() and 20 <= int(value) <= 80:
                data["top_ratio"] = int(value) / 100
                _save(data)
                ui.ok(f"Теперь сверху {value} %")
            else:
                ui.warn("Нужно число от 20 до 80")
            time.sleep(0.9)
        elif answer == "2":
            value = _ask("Длина клипа, секунд", str(round(cfg.CLIP_DURATION)))
            if value.isdigit() and 10 <= int(value) <= 120:
                data["clip_duration"] = int(value)
                _save(data)
                ui.ok(f"Длина клипа: {value} с")
            else:
                ui.warn("Нужно число от 10 до 120")
            time.sleep(0.9)
        elif answer == "3":
            value = _ask("Размер шрифта", str(cfg.FONT_SIZE))
            if value.isdigit() and 40 <= int(value) <= 160:
                data["font_size"] = int(value)
                _save(data)
                ui.ok(f"Размер субтитров: {value}")
            else:
                ui.warn("Нужно число от 40 до 160")
            time.sleep(0.9)
        elif answer in ("0", ""):
            return


# ---------------------------------------------------------------- общие настройки

def _general() -> None:
    while True:
        data = settings.load()
        _clear()
        ui.banner("Общие настройки")
        ui.choice(1, "ПАПКА С ФОНАМИ", data["backgrounds_dir"])
        ui.choice(2, "УДАЛЯТЬ ПОСЛЕ ОТПРАВКИ",
                  "да" if data["delete_after_upload"] else "нет")
        ui.choice(3, "ОТПРАВЛЯТЬ НА ПОЧТУ",
                  "да" if data["email_enabled"] else "нет")
        ui.choice(4, "АДРЕС ПОЧТЫ",
                  data["email_to"] or "свой")
        ui.choice(5, "ПОПЫТОК НА ЧАСТЬ", f"1 + {data['clip_retries']}")
        ui.choice(6, "ВИДЕО ОТ АВТОРА", data["videos_per_author"])
        ui.choice(7, "ВИДЕО ЗА ЗАПУСК", data["max_videos_per_run"] or "все")
        ui.choice(8, "ОТКРЫТЬ CONFIG.PY")
        ui.choice(9, "ОТКРЫТЬ SETTINGS.JSON")
        ui.choice(10, "МУЗЫКА",
                  "вкл" if data["music_enabled"] else "выкл")
        ui.choice(11, "ПЕРЕВОД И ОЗВУЧКА",
                  "вкл" if data["translate"] else "выкл")
        ui.choice(12, "РЕКЛАМНЫЕ ВСТАВКИ",
                  "вкл" if data["banner_enabled"] else "выкл")
        ui.choice(0, "НАЗАД")

        answer = _ask()
        if answer == "1":
            folder = _ask("Папка с фонами", data["backgrounds_dir"])
            if folder:
                data["backgrounds_dir"] = folder
                _save(data)
                ui.ok(f"Папка: {folder}")
            time.sleep(0.8)
        elif answer == "2":
            data["delete_after_upload"] = not data["delete_after_upload"]
            _save(data)
            ui.ok("Удаление после отправки: "
                  + ("включено" if data["delete_after_upload"] else "выключено"))
            time.sleep(0.8)
        elif answer == "3":
            data["email_enabled"] = not data["email_enabled"]
            _save(data)
            ui.ok("Письма: " + ("включены" if data["email_enabled"] else "выключены"))
            time.sleep(0.8)
        elif answer == "4":
            address = _ask("Адрес (пусто — свой)", data["email_to"] or "")
            data["email_to"] = address or None
            _save(data)
            ui.ok(f"Почта: {address or 'свой адрес'}")
            time.sleep(0.8)
        elif answer == "5":
            value = _ask("Сколько ДОПОЛНИТЕЛЬНЫХ попыток", str(data["clip_retries"]))
            if value.isdigit() and 0 <= int(value) <= 10:
                data["clip_retries"] = int(value)
                _save(data)
                ui.ok(f"Попыток на часть: 1 + {value}")
            else:
                ui.warn("Нужно число от 0 до 10")
            time.sleep(0.8)
        elif answer == "6":
            value = _ask("Сколько видео брать у одного автора за заход",
                         str(data["videos_per_author"]))
            if value.isdigit() and 1 <= int(value) <= 100:
                data["videos_per_author"] = int(value)
                _save(data)
                ui.ok(f"Видео от автора за заход: {value}")
            else:
                ui.warn("Нужно число от 1 до 100")
            time.sleep(0.8)
        elif answer == "7":
            value = _ask("Сколько видео УСПЕШНО обработать (0 = все)",
                         str(data["max_videos_per_run"]))
            if value.isdigit():
                data["max_videos_per_run"] = int(value)
                _save(data)
                ui.ok(f"Лимит: {value or 'без ограничения'}")
            else:
                ui.warn("Нужно число")
            time.sleep(0.8)
        elif answer == "8":
            _open_path(Path("config.py"))
            _pause()
        elif answer == "9":
            _open_path(Path("settings.json"))
            _pause()
        elif answer == "10":
            _music()
        elif answer == "11":
            _voice()
        elif answer == "12":
            _banners()
        elif answer in ("0", ""):
            return


# ---------------------------------------------------------------- музыка, озвучка, реклама

VOICES = [
    ("ru-RU-DmitryNeural", "мужской — Дмитрий"),
    ("ru-RU-SvetlanaNeural", "женский — Светлана"),
    ("ru-RU-DariyaNeural", "женский — Дария"),
]


def _music() -> None:
    while True:
        data = settings.load()
        _clear()
        ui.banner("Музыка")
        ui.info(f"сейчас: {'включена' if data['music_enabled'] else 'выключена'} · "
                f"папка {data['music_dir']} · {len(composer.list_music())} треков · "
                f"громкость {round(data['music_volume'] * 100)} %")
        ui.blank()
        ui.choice(1, "ВКЛЮЧИТЬ / ВЫКЛЮЧИТЬ",
                  "выключить" if data["music_enabled"] else "включить")
        ui.choice(2, "ПАПКА С МУЗЫКОЙ", data["music_dir"])
        ui.choice(3, "ГРОМКОСТЬ", f"{round(data['music_volume'] * 100)} %")
        ui.choice(0, "НАЗАД")

        answer = _ask()
        if answer == "1":
            data["music_enabled"] = not data["music_enabled"]
            _save(data)
            ui.ok("Музыка: " + ("включена" if data["music_enabled"] else "выключена"))
            time.sleep(0.8)
        elif answer == "2":
            folder = _ask("Папка с музыкой", data["music_dir"])
            if folder:
                data["music_dir"] = folder
                _save(data)
                ui.ok(f"Папка: {folder} ({len(composer.list_music())} треков)")
            time.sleep(0.8)
        elif answer == "3":
            value = _ask("Громкость в процентах (5–100)",
                         str(round(data["music_volume"] * 100)))
            if value.isdigit() and 5 <= int(value) <= 100:
                data["music_volume"] = int(value) / 100
                _save(data)
                ui.ok(f"Громкость музыки: {value} %")
            else:
                ui.warn("Нужно число от 5 до 100")
            time.sleep(0.8)
        elif answer in ("0", ""):
            return


def _voice() -> None:
    while True:
        data = settings.load()
        _clear()
        ui.banner("Перевод и озвучка")
        ui.info(f"сейчас: {'включено' if data['translate'] else 'выключено'} · "
                f"язык {data['translate_to']} · голос {data['voice_name']}")
        ui.note("Речь распознаётся, переводится на русский и озвучивается. "
                "Субтитры тоже на русском.")
        ui.blank()
        ui.choice(1, "ВКЛЮЧИТЬ / ВЫКЛЮЧИТЬ",
                  "выключить" if data["translate"] else "включить")
        ui.choice(2, "ЯЗЫК ПЕРЕВОДА", data["translate_to"])
        ui.choice(3, "ГОЛОС", data["voice_name"])
        ui.choice(4, "ОСТАВИТЬ ОРИГИНАЛЬНЫЙ ЗВУК",
                  "да" if data["keep_original_audio"] else "нет")
        ui.choice(5, "ПРОВЕРИТЬ ГОЛОС")
        ui.choice(0, "НАЗАД")

        answer = _ask()
        if answer == "1":
            data["translate"] = not data["translate"]
            _save(data)
            ui.ok("Перевод и озвучка: "
                  + ("включены" if data["translate"] else "выключены"))
            time.sleep(0.9)
        elif answer == "2":
            value = _ask("Код языка перевода (ru, en, uk…)", data["translate_to"])
            if value:
                data["translate_to"] = value.strip().lower()
                _save(data)
                ui.ok(f"Язык: {data['translate_to']}")
            time.sleep(0.8)
        elif answer == "3":
            ui.blank()
            for number, (name, title) in enumerate(VOICES, start=1):
                ui.choice(number, title, name)
            value = _ask("Номер голоса", "1")
            if value.isdigit() and 1 <= int(value) <= len(VOICES):
                data["voice_name"] = VOICES[int(value) - 1][0]
                _save(data)
                ui.ok(f"Голос: {data['voice_name']}")
            else:
                ui.warn("Нет такого номера")
            time.sleep(0.9)
        elif answer == "4":
            data["keep_original_audio"] = not data["keep_original_audio"]
            _save(data)
            ui.ok("Оригинальный звук: "
                  + ("оставлен тихо под озвучкой" if data["keep_original_audio"]
                     else "выключен"))
            time.sleep(0.9)
        elif answer == "5":
            data = settings.load()
            settings.apply(data)
            _clear()
            ui.banner("Проверка голоса")
            ui.info(f"Голос: {data['voice_name']}, синтезирую пробную фразу…")
            try:
                import voiceover
                path = Path(cfg.WORK_DIR) / "voice_check.mp3"
                path.parent.mkdir(parents=True, exist_ok=True)
                if voiceover._synthesize("Привет! Так будет звучать озвучка видео.",
                                          path, data["voice_name"]):
                    ui.ok(f"Готово: {path}")
                    _open_path(path)
                else:
                    ui.fail("Не получилось — проверьте интернет")
            except Exception as e:
                ui.fail(f"Ошибка: {e}")
            _pause()
        elif answer in ("0", ""):
            return


def _banners() -> None:
    while True:
        data = settings.load()
        count = len(composer.list_banners())
        _clear()
        ui.banner("Рекламные вставки")
        ui.info(f"сейчас: {'включены' if data['banner_enabled'] else 'выключены'} · "
                f"папка {data['banner_dir']} · роликов: {count}")
        ui.note("Клип до 2 минут — одна вставка посередине. Длиннее — "
                f"каждые {data['banner_every']} секунд.")
        ui.blank()
        ui.choice(1, "ВКЛЮЧИТЬ / ВЫКЛЮЧИТЬ",
                  "выключить" if data["banner_enabled"] else "включить")
        ui.choice(2, "ПАПКА С РЕКЛАМОЙ", data["banner_dir"])
        ui.choice(3, "КАЖДЫЕ N СЕКУНД", f"{data['banner_every']} с")
        ui.choice(4, "ДЛИТЕЛЬНОСТЬ ВСТАВКИ", f"{data['banner_duration']} с")
        ui.choice(5, "УБИРАТЬ ОДНОТОННЫЙ ФОН",
                  "да" if data["banner_green"] else "нет")
        ui.choice(0, "НАЗАД")

        answer = _ask()
        if answer == "1":
            data["banner_enabled"] = not data["banner_enabled"]
            _save(data)
            ui.ok("Реклама: " + ("включена" if data["banner_enabled"] else "выключена"))
            if data["banner_enabled"] and not count:
                ui.warn(f"Папка {data['banner_dir']} пуста — положите туда ролики")
            time.sleep(1.0)
        elif answer == "2":
            folder = _ask("Папка с рекламой", data["banner_dir"])
            if folder:
                data["banner_dir"] = folder
                _save(data)
                ui.ok(f"Папка: {folder} ({len(composer.list_banners())} роликов)")
            time.sleep(0.8)
        elif answer == "3":
            value = _ask("Вставлять каждые N секунд", str(data["banner_every"]))
            if value.isdigit() and 10 <= int(value) <= 600:
                data["banner_every"] = int(value)
                _save(data)
                ui.ok(f"Каждые {value} с")
            else:
                ui.warn("Нужно число от 10 до 600")
            time.sleep(0.8)
        elif answer == "4":
            value = _ask("Сколько секунд показывать рекламу",
                         str(data["banner_duration"]))
            if value.isdigit() and 1 <= int(value) <= 60:
                data["banner_duration"] = int(value)
                _save(data)
                ui.ok(f"Длительность: {value} с")
            else:
                ui.warn("Нужно число от 1 до 60")
            time.sleep(0.8)
        elif answer == "5":
            data["banner_green"] = not data["banner_green"]
            _save(data)
            ui.ok("Фон рекламы: "
                  + ("вырезаем" if data["banner_green"] else "оставляем как есть"))
            time.sleep(0.9)
        elif answer in ("0", ""):
            return


# ---------------------------------------------------------------- проверка связи

def _check_import(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _diagnostics() -> None:
    _clear()
    ui.banner("Проверка связи и настроек")
    total_steps = 8
    problems: list[str] = []

    ui.step(1, total_steps, "Программа и библиотеки")
    ui.ok(f"Python {sys.version.split()[0]}")
    missing = [name for name in ("yt_dlp", "faster_whisper", "googleapiclient",
                                 "google_auth_oauthlib") if not _check_import(name)]
    if missing:
        ui.fail("Не хватает: " + ", ".join(missing))
        ui.note("Выполните: pip install -r requirements.txt")
        problems.append("библиотеки")
    else:
        ui.info("yt-dlp, faster-whisper, google-api — на месте")

    ui.step(2, total_steps, "FFmpeg")
    ffmpeg_ok = False
    for tool in ("ffmpeg", "ffprobe"):
        try:
            out = subprocess.run([tool, "-version"], capture_output=True, text=True,
                                 timeout=20)
            ffmpeg_ok = ffmpeg_ok or out.returncode == 0
        except Exception:
            pass
    if ffmpeg_ok:
        ui.ok("ffmpeg и ffprobe найдены")
    else:
        ui.fail("ffmpeg не найден в PATH")
        problems.append("ffmpeg")

    ui.step(3, total_steps, "Шрифт и фоны")
    if Path(cfg.FONT_FILE).exists():
        ui.ok(f"шрифт: {cfg.FONT_FILE}")
    else:
        ui.fail(f"нет шрифта {cfg.FONT_FILE}")
        problems.append("шрифт")
    backgrounds = composer.list_backgrounds()
    if cfg.BACKGROUND_MODE == "folder":
        if backgrounds:
            ui.ok(f"фоновых видео: {len(backgrounds)}")
        else:
            ui.fail(f"в {cfg.BACKGROUND_DIR} нет видео")
            problems.append("фоны")
    else:
        ui.info(f"фон: {_mode_title()} — файлы не нужны")

    ui.step(4, total_steps, "Интернет и YouTube")
    internet = False
    try:
        google_ip = socket.getaddrinfo("google.com", 443)[0][4][0]
        internet = True
        ui.ok(f"интернет есть (google.com {ui.ARROW} {google_ip})")
    except Exception as e:
        ui.fail(f"интернет не отвечает: {str(e)[:120]}")
        ui.note("Проверьте Wi-Fi или кабель. Если VPN включён — выключите и включите заново.")

    if internet:
        downloader.enable_doh_fallback()
        try:
            ip = socket.getaddrinfo("www.youtube.com", 443)[0][4][0]
            ui.ok(f"YouTube найден: {ip}")
            context = ssl.create_default_context()
            with socket.create_connection((ip, 443), timeout=10) as sock:
                with context.wrap_socket(sock, server_hostname="www.youtube.com"):
                    ui.ok("защищённое соединение с YouTube установлено")
        except Exception as e:
            ui.fail(f"YouTube недоступен: {str(e)[:140]}")
            ui.note("Провайдер режет YouTube — включите VPN и проверьте снова.")
            problems.append("YouTube")
    else:
        ui.skip("YouTube без интернета не проверить")
        problems.append("интернет")

    ui.step(5, total_steps, "Google Диск")
    token = Path(cfg.GOOGLE_TOKEN)
    if not token.exists():
        ui.fail("нет token.json — нужен вход в Google")
        problems.append("Диск")
    elif not set(uploader.DRIVE_SCOPES).issubset(set(uploader._token_scopes(token))):
        ui.fail("в token.json нет прав на Диск")
        problems.append("Диск")
    else:
        ui.ok("token.json есть, права на Диск выданы")
        try:
            service = uploader._service("drive", "v3", uploader.DRIVE_SCOPES)
            about = service.about().get(fields="user").execute()
            ui.ok(f"Диск отвечает: {about.get('user', {}).get('emailAddress', '')}")
        except Exception as e:
            text = str(e)
            low = text.lower()
            if any(key in low for key in ("find the server", "getaddrinfo",
                                          "name or service", "nodename nor servname")):
                ui.fail("Диск недоступен: не удалось найти сервер")
                ui.note("Пропал интернет или DNS. Проверьте подключение и VPN.")
            else:
                ui.fail(f"Диск не ответил: {text[:150]}")
            problems.append("Диск")

    ui.step(6, total_steps, "Почта Gmail")
    if not cfg.EMAIL_ENABLED:
        ui.skip("отправка писем выключена в настройках")
    elif not token.exists():
        ui.fail("нет token.json — нужен вход в Google")
        problems.append("почта")
    elif not set(uploader.GMAIL_SCOPES).issubset(set(uploader._token_scopes(token))):
        ui.warn("права на отправку ещё не выданы")
        ui.note("Появятся при первом запуске: откроется браузер, разрешите отправку.")
    else:
        ui.ok("права на отправку писем есть")

    ui.step(7, total_steps, "Место на диске")
    try:
        usage = shutil.disk_usage(".")
        ui.ok(f"свободно {ui.fmt_size(usage.free)}")
        if usage.free < 5 * 1024 ** 3:
            ui.warn("меньше 5 ГБ — может не хватить для длинных видео")
    except Exception as e:
        ui.warn(f"не удалось узнать: {e}")

    ui.step(8, total_steps, "Итог")
    if problems:
        ui.warn("Требует внимания: " + ", ".join(dict.fromkeys(problems)))
        if "интернет" in problems:
            ui.note("Без интернета бот не скачает видео и не загрузит его на Диск.")
    else:
        ui.ok("Всё в порядке — можно запускать")

    ui.blank()
    _pause()


# ---------------------------------------------------------------- отчёт

def _report() -> None:
    report = Path(cfg.OUTPUT_DIR) / uploader.REPORT_NAME
    _clear()
    ui.banner("Отчёт")
    if not report.exists():
        ui.warn("Отчёт пока пуст — ещё не было ни одного запуска")
        _pause()
        return

    with report.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))
    body = rows[1:]
    width = len(uploader.REPORT_HEADER)
    body = [row + [""] * (width - len(row)) for row in body]
    ui.kv("Записей", len(body))
    ui.blank()
    if body:
        ui.header_line("Последние 10:")
        for row in body[-10:]:
            date, channel, _author, title, _url, parts, _names, links, missing = row[:9]
            ui.info(f"{date}  {channel}  «{title[:48]}» — клипов {parts}"
                    + (f", пропущено: {missing}" if missing else ""))
            first_link = links.split(" | ")[0] if links else ""
            if first_link.startswith("http"):
                ui.note(first_link)
    ui.blank()
    ui.choice(1, "ОТКРЫТЬ report.csv")
    ui.choice(2, "ОТКРЫТЬ ПАПКУ OUTPUT")
    ui.choice(0, "НАЗАД")
    answer = _ask()
    if answer == "1":
        _open_path(report)
        _pause()
    elif answer == "2":
        _open_path(Path(cfg.OUTPUT_DIR))
        _pause()


# ---------------------------------------------------------------- главное меню

def _bots_summary(bots: list[dict]) -> str:
    """Короткая строка под пунктом «БОТЫ»: кого сколько и кто выключен."""
    if not bots:
        return "ботов нет"
    names = ", ".join(bot.get("name") or "без имени" for bot in bots[:3])
    if len(bots) > 3:
        names += f" и ещё {len(bots) - 3}"
    off = sum(1 for bot in bots if not bot.get("enabled", True))
    if off:
        return f"{names} · выключено: {off}"
    return names


def loop() -> None:
    while True:
        _clear()
        data = settings.load()
        settings.apply(data)
        bots = data["bots"]

        ui.banner("Бот-нарезчик TikTok / Shorts")
        ui.choice(1, "НАЧАТЬ ЗАПУСК")
        ui.choice(2, "СТАТИСТИКА")
        ui.choice(3, f"БОТЫ ({len(bots)})", _bots_summary(bots))
        ui.choice(4, "ОБЩИЕ НАСТРОЙКИ")
        ui.choice(5, "ПРОВЕРКА СВЯЗИ")
        ui.choice(6, "ОТЧЁТ")
        ui.choice(0, "ВЫХОД")
        ui.blank()

        answer = _ask("Выберите пункт", "1")
        if answer == "1":
            _start()
        elif answer == "2":
            _statistics()
        elif answer == "3":
            _bots_menu()
        elif answer == "4":
            _general()
        elif answer == "5":
            _diagnostics()
        elif answer == "6":
            _report()
        elif answer == "0":
            _clear()
            ui.banner("До встречи")
            return
