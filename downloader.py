# -*- coding: utf-8 -*-
"""Скачивание новых видео с YouTube через yt-dlp + учёт уже обработанных."""

import json
import logging
import os
import socket
import sqlite3
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import yt_dlp

import config as cfg
import ui

log = logging.getLogger(__name__)

# Провайдеры иногда отдают NXDOMAIN на youtube.com, хотя сам сайт доступен.
# Резолвим эти домены через Google DoH (https://dns.google) — без прав администратора.
_DOH_DOMAINS = ("youtube.com", "youtu.be", "googlevideo.com", "ytimg.com",
                "ggpht.com", "youtube-nocookie.com", "googleusercontent.com",
                "googleapis.com", "tiktok.com", "tiktokv.com", "tiktokcdn.com",
                "byteoversea.com", "ibytedtos.com")
_DOH_TTL = 300
_doh_cache: dict[str, tuple[float, list[str]]] = {}
_orig_getaddrinfo = socket.getaddrinfo

# Несколько независимых DNS-over-HTTPS серверов: если один недоступен, пробуем следующий.
_DOH_ENDPOINTS = (
    "https://dns.google/resolve?name={host}&type=A",
    "https://cloudflare-dns.com/dns-query?name={host}&type=A",
    "https://dns.adguard-dns.com/resolve?name={host}&type=A",
)


def _retry_sleep(attempt: int) -> float:
    """Пауза перед повтором запроса к YouTube: 2, 4, 8, 16, 24, 30… секунд."""
    return float(min(30, 2 ** min(attempt, 5)))


def platform(url: str) -> str:
    """Откуда качаем: TikTok или YouTube."""
    return "tiktok" if "tiktok.com" in (url or "").lower() else "youtube"


def _doh_ips(host: str) -> list[str]:
    cached = _doh_cache.get(host)
    if cached and time.time() - cached[0] < _DOH_TTL:
        return cached[1]

    errors = []
    for template in _DOH_ENDPOINTS:
        url = template.format(host=host)
        request = urllib.request.Request(
            url, headers={"Accept": "application/dns-json",
                          "User-Agent": "tiktok-bot/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=5) as resp:
                data = json.load(resp)
            ips = [a["data"] for a in data.get("Answer", []) if a.get("type") == 1]
            if ips:
                _doh_cache[host] = (time.time(), ips)
                return ips
        except Exception as e:
            errors.append(f"{url.split('/')[2]}: {e}")

    if errors:
        log.debug("DoH не ответил для %s (%s)", host, "; ".join(errors))
    return []


def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host and any(host == d or host.endswith("." + d) for d in _DOH_DOMAINS):
        try:
            ips = _doh_ips(host)
            if ips:
                socktype = type or socket.SOCK_STREAM
                protocol = proto or (socket.IPPROTO_TCP if socktype == socket.SOCK_STREAM else socket.IPPROTO_UDP)
                return [(socket.AF_INET, socktype, protocol, "", (ip, port or 0)) for ip in ips]
        except Exception as e:
            log.debug("DoH не сработал для %s (%s), обычный DNS", host, e)
    return _orig_getaddrinfo(host, port, family, type, proto, flags)


def enable_doh_fallback() -> None:
    """Включает резолв доменов YouTube через DoH, если это нужно."""
    if cfg.USE_DOH_FALLBACK and socket.getaddrinfo is not _patched_getaddrinfo:
        socket.getaddrinfo = _patched_getaddrinfo
        log.debug("DNS-обход YouTube через DoH включён")


# ---------------------------------------------------------------- метаданные исходников

def meta_path(video_id: str) -> Path:
    """Рядом со скачанным исходником храним название, канал и автора."""
    return Path(cfg.WORK_DIR) / "raw" / f"{video_id}.json"


def read_meta(video_id: str) -> dict | None:
    path = meta_path(video_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.debug("Не удалось прочитать метаданные %s: %s", path.name, e)
        return None
    return data if isinstance(data, dict) else None


def write_meta(meta: dict) -> None:
    video_id = meta.get("video_id")
    if not video_id:
        return
    path = meta_path(video_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    data = {key: meta.get(key) for key in
            ("video_id", "title", "channel", "author", "url", "duration")}
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def delete_meta(video_id: str) -> None:
    meta_path(video_id).unlink(missing_ok=True)


def init_state() -> sqlite3.Connection:
    con = sqlite3.connect(cfg.STATE_DB)
    con.execute(
        "CREATE TABLE IF NOT EXISTS videos ("
        "video_id TEXT PRIMARY KEY, channel TEXT, title TEXT, processed_at TEXT)"
    )
    con.commit()
    return con


def is_processed(con: sqlite3.Connection, video_id: str) -> bool:
    return con.execute("SELECT 1 FROM videos WHERE video_id = ?", (video_id,)).fetchone() is not None


def mark_processed(con: sqlite3.Connection, video_id: str, channel: str, title: str) -> None:
    con.execute(
        "INSERT OR REPLACE INTO videos VALUES (?, ?, ?, ?)",
        (video_id, channel, title, datetime.now().isoformat(timespec="seconds")),
    )
    con.commit()


class _QuietLogger:
    """Глушит собственные сообщения yt-dlp: об ошибке узнаём из исключения."""

    def debug(self, message):
        log.debug(message)

    def info(self, message):
        log.debug(message)

    def warning(self, message):
        log.debug(message)

    def error(self, message):
        log.debug(message)


def _base_opts() -> dict:
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
            "logger": _QuietLogger()}
    if cfg.COOKIES_FILE:
        opts["cookiefile"] = cfg.COOKIES_FILE
    if cfg.PROXY:
        opts["proxy"] = cfg.PROXY
    return opts


def _flatten_entries(entries):
    """yt-dlp может вернуть вкладки канала («Videos», «Shorts») — разворачиваем."""
    for entry in entries or []:
        if not entry:
            continue
        if entry.get("_type") == "playlist" and entry.get("entries"):
            yield from _flatten_entries(entry["entries"])
        else:
            yield entry


def list_new_videos(channel_url: str, con: sqlite3.Connection,
                    limit: int | None = None) -> list[dict]:
    """Последние видео канала (YouTube или TikTok), которые ещё не обрабатывались.

    limit=None — берём cfg.MAX_VIDEOS_PER_CHANNEL (0 = все новые).
    """
    site = platform(channel_url)
    url = channel_url.rstrip("/")
    if site == "youtube" and not url.endswith(("/videos", "/shorts", "/streams")):
        url += "/videos"

    cap = cfg.MAX_VIDEOS_PER_CHANNEL if limit is None else max(0, limit)
    scan = cap * 8 if cap else 200
    opts = _base_opts() | {
        "extract_flat": True,
        "skip_download": True,
        "playlistend": max(scan, 24),  # запас на уже обработанные
        "extractor_retries": 5,
        "socket_timeout": 30,
        "retry_sleep_functions": {"extractor": _retry_sleep},
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        return []
    # Ссылка на одно видео (а не на профиль): берём его самого.
    raw_entries = list(_flatten_entries(info.get("entries"))) or [info]

    fresh = []
    min_duration = (cfg.MIN_VIDEO_DURATION_TIKTOK if site == "tiktok"
                    else cfg.MIN_VIDEO_DURATION)
    for entry in raw_entries:
        if not entry.get("id"):
            continue
        entry["platform"] = site
        duration = entry.get("duration")
        if duration and not (min_duration <= duration <= cfg.MAX_VIDEO_DURATION):
            continue
        if is_processed(con, entry["id"]):
            continue
        fresh.append(entry)
        if cap and len(fresh) >= cap:
            break
    return fresh


def download_video(entry: dict) -> dict | None:
    """Скачивает видео в WORK_DIR/raw. Возвращает метаданные или None."""
    raw_dir = Path(cfg.WORK_DIR) / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    url = entry.get("url") or entry.get("webpage_url") or entry["id"]

    state = {"time": 0.0, "percent": -1, "finished": False}

    def progress(d: dict) -> None:
        if d.get("status") == "finished":
            # yt-dlp сообщает о конце каждой дорожки (видео и звук) —
            # пишем строку один раз, при склейке.
            if not state["finished"]:
                state["finished"] = True
                log.info("   • скачано, склеиваю дорожки…")
            return
        if d.get("status") != "downloading":
            return
        now = time.time()
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        done = d.get("downloaded_bytes") or 0
        percent = int(done * 100 / total) if total else -1
        # Пишем не чаще раза в 10 секунд и только когда процент заметно вырос.
        if now - state["time"] < 10 or percent < state["percent"] + 5:
            return
        state["time"] = now
        state["percent"] = percent
        parts = []
        if percent >= 0:
            parts.append(f"{percent}%")
        parts.append(f"{ui.fmt_size(done)} из {ui.fmt_size(total)}"
                     if total else ui.fmt_size(done))
        speed = d.get("speed")
        if speed:
            parts.append(f"{ui.fmt_size(speed)}/с")
        eta = d.get("eta")
        if eta:
            parts.append(f"осталось ~{ui.fmt_duration(eta)}")
        log.info("   • скачано %s", " · ".join(parts))

    site = entry.get("platform") or platform(url)
    opts = _base_opts() | {
        "format": cfg.TIKTOK_FORMAT if site == "tiktok" else cfg.YTDLP_FORMAT,
        "merge_output_format": "mp4",
        "outtmpl": str(raw_dir / "%(id)s.%(ext)s"),
        "progress_hooks": [progress],
        "socket_timeout": 30,
        # YouTube часто отвечает 503/403 и рвёт соединение: даём больше попыток
        # и паузы между ними, иначе видео теряется из-за одной сетевой ошибки.
        "retries": 10,
        "fragment_retries": 20,
        "extractor_retries": 5,
        "retry_sleep_functions": {
            "http": _retry_sleep,
            "fragment": _retry_sleep,
            "extractor": _retry_sleep,
        },
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    duration = float(info.get("duration") or 0)
    min_duration = (cfg.MIN_VIDEO_DURATION_TIKTOK if site == "tiktok"
                    else cfg.MIN_VIDEO_DURATION)
    if duration and not (min_duration <= duration <= cfg.MAX_VIDEO_DURATION):
        log.info("Пропуск «%s»: длительность %.0f с вне диапазона", info.get("title"), duration)
        return None

    path = None
    for item in info.get("requested_downloads") or []:
        candidate = Path(item.get("filepath", ""))
        if candidate.exists():
            path = candidate
            break
    if path is None:
        path = Path(ydl.prepare_filename(info))
    if not path.exists():
        raise FileNotFoundError(f"yt-dlp не создал файл для {url}")

    meta = {
        "video_id": info.get("id") or entry["id"],
        "title": info.get("title") or info.get("description") or "video",
        "channel": info.get("channel") or info.get("uploader") or "channel",
        "author": info.get("uploader") or info.get("channel") or "author",
        "url": info.get("webpage_url") or url,
        "duration": duration,
        "platform": site,
        "path": path,
    }
    write_meta(meta)
    return meta
