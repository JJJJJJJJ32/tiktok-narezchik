# -*- coding: utf-8 -*-
"""Красивый вывод в консоль: заголовки, шаги, галочки, итоговая таблица.

Все сообщения идут через logging: в консоль — как есть, в bot.log — с датой
и без цветовых кодов.
"""

import logging
import os
import re
import sys

log = logging.getLogger("ui")

WIDTH = 62
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _enable_ansi() -> bool:
    """Включает UTF-8 и ANSI-цвета в консоли Windows."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
            return True
    except Exception:
        pass
    return False


COLOR = _enable_ansi() and not os.environ.get("NO_COLOR")


def _c(code: str, text: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if COLOR else text


def bold(text): return _c("1", text)
def dim(text): return _c("2", text)
def green(text): return _c("32", text)
def red(text): return _c("31", text)
def yellow(text): return _c("33", text)
def cyan(text): return _c("36", text)
def magenta(text): return _c("35", text)


def _supports(symbol: str) -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        symbol.encode(encoding)
        return True
    except Exception:
        return False


OK = "✅" if _supports("✅") else "[OK]"
SKIP = "⏭" if _supports("⏭") else "[>>]"
FAIL = "❌" if _supports("❌") else "[!!]"
WARN = "⚠" if _supports("⚠") else "[!]"
DOT = "•" if _supports("•") else "-"
ARROW = "→" if _supports("→") else "->"
BAR = "│" if _supports("│") else "|"
TOP = "┌" if _supports("┌") else "+"
BOTTOM = "└" if _supports("└") else "+"


class FileFormatter(logging.Formatter):
    """В файл пишем без цветовых кодов."""

    def format(self, record):
        return _ANSI_RE.sub("", super().format(record))


# ---------------- базовые элементы ----------------

def blank(count: int = 1) -> None:
    for _ in range(count):
        log.info("")


def rule() -> None:
    log.info(dim("=" * WIDTH))


def banner(title: str, subtitle: str = "") -> None:
    blank()
    rule()
    log.info(bold(cyan(title.upper().center(WIDTH))))
    if subtitle:
        log.info(dim(subtitle.center(WIDTH)))
    rule()
    blank()


def section(title: str) -> None:
    blank()
    log.info(bold(title))
    log.info(dim("-" * min(WIDTH, max(12, len(title) + 4))))


def step(index: int, total: int, text: str) -> None:
    blank()
    log.info(bold(f"[{index}/{total}] ") + bold(text))


def ok(text: str) -> None:
    log.info(f"   {green(OK)} {text}")


def skip(text: str) -> None:
    log.info(f"   {cyan(SKIP)} {dim(text)}")


def fail(text: str) -> None:
    log.info(f"   {red(FAIL)} {text}")


def warn(text: str) -> None:
    log.info(f"   {yellow(WARN)} {text}")


def info(text: str) -> None:
    log.info(f"   {DOT} {text}")


def note(text: str) -> None:
    log.info(f"     {dim(text)}")


def line(text: str) -> None:
    log.info(f"   {text}")


def block(mark: str, text: str) -> None:
    """Строка внутри рамки одного видео."""
    log.info(f"   {BAR}  {mark} {text}")


def open_block(title: str) -> None:
    log.info(f"   {TOP} {bold(title)}")


def close_block(text: str) -> None:
    log.info(f"   {BOTTOM} {text}")


def kv(label: str, value, dots_width: int = 30) -> None:
    dots = "." * max(3, dots_width - len(label) - len(str(value)))
    log.info(f"   {label} {dim(dots)} {bold(str(value))}")


def choice(number, text: str, hint: str = "") -> None:
    tail = f"  {dim(hint)}" if hint else ""
    log.info(f"   {bold(str(number))}. {text}{tail}")


def header_line(text: str) -> None:
    log.info(bold(cyan(text)))


# ---------------- форматирование чисел ----------------

def fmt_size(num_bytes: float) -> str:
    size = float(num_bytes)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ГБ"


def fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} с"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} мин {sec:02d} с"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes:02d} мин"


def fmt_clock(seconds: float) -> str:
    seconds = int(round(seconds))
    minutes, sec = divmod(seconds, 60)
    return f"{minutes}:{sec:02d}"


def plural(count: int, one: str, few: str, many: str) -> str:
    count = abs(int(count)) % 100
    if 10 < count < 20:
        return many
    last = count % 10
    if 1 < last < 5:
        return few
    if last == 1:
        return one
    return many


def clips(count: int) -> str:
    return f"{count} {plural(count, 'клип', 'клипа', 'клипов')}"


__all__ = [
    "FileFormatter", "COLOR", "OK", "SKIP", "FAIL", "WARN", "DOT", "ARROW",
    "bold", "dim", "green", "red", "yellow", "cyan", "magenta",
    "blank", "rule", "banner", "section", "step", "ok", "skip", "fail",
    "warn", "info", "note", "line", "block", "open_block", "close_block",
    "kv", "choice", "header_line", "fmt_size", "fmt_duration", "fmt_clock",
    "plural", "clips",
]
