# -*- coding: utf-8 -*-
"""Загрузка клипов на Google Drive (OAuth desktop) + отчёт + письмо на Gmail."""

import base64
import csv
import html
import http.client
import io
import json
import logging
import re
import socket
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import config as cfg
import ui

log = logging.getLogger(__name__)

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
SCOPES = DRIVE_SCOPES + GMAIL_SCOPES
FOLDER_MIME = "application/vnd.google-apps.folder"
REPORT_NAME = "report.csv"
REPORT_HEADER = ["дата", "канал", "автор", "название", "ссылка", "частей",
                 "имена файлов", "ссылки на Drive", "пропущено"]

_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}


def _brief(error: Exception, limit: int = 120) -> str:
    text = " ".join(str(error).split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _status_code(error: Exception) -> int | None:
    for value in (getattr(error, "status_code", None),
                  getattr(getattr(error, "resp", None), "status", None)):
        try:
            if value:
                return int(value)
        except (TypeError, ValueError):
            pass
    return None


def _transient(error: Exception) -> bool:
    """Обрыв связи или временный отказ сервера — имеет смысл повторить."""
    if isinstance(error, (http.client.RemoteDisconnected, ConnectionResetError,
                          ConnectionAbortedError, BrokenPipeError,
                          socket.timeout, TimeoutError)):
        return True
    if _status_code(error) in _TRANSIENT_STATUS:
        return True
    text = str(error).lower()
    return any(key in text for key in (
        "remote end closed", "connection reset", "connection aborted",
        "connection broken", "timed out", "temporarily unavailable",
        "broken pipe", "server disconnected", "network is unreachable",
        "getaddrinfo", "read timeout", "unable to find the server",
        "name or service not known", "winerror 10054", "winerror 10060",
        "ssl", "backend error", "internal error",
    ))


def _call(action, what: str, tries: int = 4):
    """Повторяет сетевую операцию Google с нарастающей паузой."""
    last_error: Exception | None = None
    for attempt in range(1, tries + 1):
        try:
            return action()
        except Exception as e:
            last_error = e
            if attempt >= tries or not _transient(e):
                raise
            delay = min(20, 2 * attempt)
            log.info("   • %s: связь прервалась (%s) — повтор %d/%d через %d с",
                     what, _brief(e, 90), attempt + 1, tries, delay)
            time.sleep(delay)
    raise last_error  # сюда не доходим, но пусть будет явно


def _token_scopes(token: Path) -> list[str]:
    try:
        return list(json.loads(token.read_text(encoding="utf-8")).get("scopes") or [])
    except Exception:
        return []


def _service(api: str, version: str, required: list[str]):
    """Собирает клиент Google. Если в token.json не хватает прав — один раз просит вход."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    token = Path(cfg.GOOGLE_TOKEN)
    creds = None
    if token.exists() and set(required).issubset(set(_token_scopes(token))):
        creds = Credentials.from_authorized_user_file(str(token))

    if creds is None:
        if not Path(cfg.GOOGLE_CREDENTIALS).exists():
            raise FileNotFoundError(
                f"Нет {cfg.GOOGLE_CREDENTIALS}. Смотри README, раздел «Google Drive»."
            )
        flow = InstalledAppFlow.from_client_secrets_file(cfg.GOOGLE_CREDENTIALS, SCOPES)
        creds = flow.run_local_server(port=0)
        token.write_text(creds.to_json(), encoding="utf-8")
    elif not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token.write_text(creds.to_json(), encoding="utf-8")
        else:
            flow = InstalledAppFlow.from_client_secrets_file(cfg.GOOGLE_CREDENTIALS, SCOPES)
            creds = flow.run_local_server(port=0)
            token.write_text(creds.to_json(), encoding="utf-8")

    return build(api, version, credentials=creds, cache_discovery=False)


def _escape(text: str) -> str:
    """Экранирование для запроса Drive: апостроф и обратный слэш."""
    return text.replace("\\", "\\\\").replace("'", "\\'")


def sanitize(name: str, limit: int) -> str:
    """Убираем недопустимые символы, схлопываем пробелы, ограничиваем длину."""
    clean = _BAD_CHARS.sub("", name)
    clean = re.sub(r"\s+", "_", clean.strip())
    return clean[:limit].strip("._") or "video"


def clip_filename(channel: str, title: str, index: int, total: int) -> str:
    return (f"{sanitize(channel, 30)}_{sanitize(title, 45)}"
            f"_part{index:02d}of{total:02d}.mp4")


def build_description(video: dict, index: int, total: int, start: float, end: float,
                      backgrounds: list[dict]) -> str:
    bg_names = ", ".join(Path(b["path"]).name for b in backgrounds) or "—"
    return (
        f"Канал: {video['channel']}\n"
        f"Автор: {video['author']}\n"
        f"Оригинал: {video['url']}\n"
        f"Название: {video['title']}\n"
        f"Часть {index} из {total}\n"
        f"Таймкоды в оригинале: {start:.1f}–{end:.1f} с\n"
        f"Фон: {bg_names}\n"
        f"Создано: {datetime.now():%Y-%m-%d %H:%M}"
    )


def _clock(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


class DriveUploader:
    """dry_run=True — ничего не грузим, report.csv пишем локально."""

    def __init__(self, dry_run: bool = False, service=None):
        self.dry_run = dry_run
        self.service = service if service is not None else (
            None if dry_run else _service("drive", "v3", DRIVE_SCOPES))
        self._folders: dict[tuple[str, str | None], str] = {}
        self._report_id: str | None = None
        self._local_report = Path(cfg.OUTPUT_DIR) / REPORT_NAME
        self._local_report.parent.mkdir(parents=True, exist_ok=True)
        if not self._local_report.exists():
            self._write_local_report([REPORT_HEADER])

    # ---------- локальная копия отчёта ----------
    def _write_local_report(self, rows: list[list[str]]) -> None:
        with self._local_report.open("w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f, delimiter=";").writerows(rows)

    @staticmethod
    def _normalize(rows: list[list[str]]) -> list[list[str]]:
        """Приводит старый отчёт (без колонки «пропущено») к новому заголовку."""
        if not rows:
            return [list(REPORT_HEADER)]
        rows[0] = list(REPORT_HEADER)
        width = len(REPORT_HEADER)
        for row in rows[1:]:
            if len(row) < width:
                row.extend([""] * (width - len(row)))
        return rows

    # ---------- папки ----------
    def ensure_folder(self, name: str, parent: str | None = None) -> str:
        key = (name, parent)
        if key in self._folders:
            return self._folders[key]
        if self.dry_run:
            folder_id = f"dry:{parent or ''}:{name}"
        else:
            query = (f"name = '{_escape(name)}' and mimeType = '{FOLDER_MIME}' "
                     f"and trashed = false")
            if parent:
                query += f" and '{parent}' in parents"

            def resolve():
                # На каждой попытке сначала ищем: если предыдущая успела создать
                # папку, но ответ потерялся, второй раз её не создаём.
                found = self.service.files().list(
                    q=query, fields="files(id)", pageSize=1).execute().get("files", [])
                if found:
                    return found[0]["id"]
                body = {"name": name, "mimeType": FOLDER_MIME}
                if parent:
                    body["parents"] = [parent]
                return self.service.files().create(
                    body=body, fields="id").execute()["id"]

            folder_id = _call(resolve, f"Диск: папка «{name}»")
        self._folders[key] = folder_id
        return folder_id

    # ---------- файлы ----------
    def upload_file(self, path: Path, name: str, parent: str, description: str) -> dict:
        if self.dry_run:
            return {"id": f"dry:{name}", "link": f"file://{Path(path).resolve()}"}
        from googleapiclient.http import MediaFileUpload

        query = (f"name = '{_escape(name)}' and '{parent}' in parents "
                 f"and trashed = false")
        attempts = {"n": 0}

        def do_upload():
            attempts["n"] += 1
            if attempts["n"] > 1:
                # Прошлая попытка могла загрузить файл, а ответ потерялся —
                # тогда не грузим второй раз, а берём найденный.
                found = self.service.files().list(
                    q=query, fields="id, webViewLink", pageSize=1,
                ).execute().get("files", [])
                if found:
                    return found[0]
            media = MediaFileUpload(str(path), mimetype="video/mp4", resumable=True)
            return self.service.files().create(
                body={"name": name, "parents": [parent], "description": description},
                media_body=media, fields="id, webViewLink",
            ).execute()

        result = _call(do_upload, f"Диск: файл {name}", tries=5)
        link = result.get("webViewLink") or f"https://drive.google.com/file/d/{result['id']}/view"
        return {"id": result["id"], "link": link}

    # ---------- отчёт ----------
    def _report_file_id(self, root: str) -> str | None:
        if self.dry_run:
            return None
        if self._report_id is None:
            found = _call(
                lambda: self.service.files().list(
                    q=f"name = '{REPORT_NAME}' and '{root}' in parents and trashed = false",
                    fields="files(id)", pageSize=1,
                ).execute(),
                "Диск: поиск отчёта").get("files", [])
            self._report_id = found[0]["id"] if found else ""
        return self._report_id or None

    def _read_report(self, root: str) -> list[list[str]]:
        # В реальном режиме источник истины — Drive, локальный файл лишь зеркало.
        if not self.dry_run:
            file_id = self._report_file_id(root)
            if file_id:
                data = _call(
                    lambda: self.service.files().get_media(fileId=file_id).execute(),
                    "Диск: чтение отчёта")
                rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig")), delimiter=";"))
                rows = self._normalize(rows)
                self._write_local_report(rows)
                return rows
        if self._local_report.exists():
            with self._local_report.open(newline="", encoding="utf-8-sig") as f:
                return self._normalize(list(csv.reader(f, delimiter=";")))
        return [list(REPORT_HEADER)]

    def add_report_row(self, root: str, row: list[str]) -> None:
        rows = self._read_report(root)
        if not self.dry_run:
            # Проверочные строки (file://) на Диск не переносим.
            rows = [rows[0]] + [r for r in rows[1:]
                                if "file://" not in (r[7] if len(r) > 7 else "")]
        # При повторной загрузке после сбоя строка о том же видео заменяется,
        # а не дублируется: 5-я колонка — ссылка на оригинал.
        url = row[4] if len(row) > 4 else ""
        if url:
            rows = [rows[0]] + [r for r in rows[1:]
                                if not (len(r) > 4 and r[4] == url)]
        rows.append(row)
        self._write_local_report(rows)
        if self.dry_run:
            return
        from googleapiclient.http import MediaIoBaseUpload

        text = io.StringIO()
        csv.writer(text, delimiter=";").writerows(rows)
        payload = text.getvalue().encode("utf-8-sig")

        def do_save():
            # Перед каждой попыткой перечитываем, есть ли уже отчёт на Диске:
            # если первая попытка успела создать файл, но ответ потерялся,
            # второй раз его не создаём.
            self._report_id = None
            media = MediaIoBaseUpload(io.BytesIO(payload), mimetype="text/csv")
            file_id = self._report_file_id(root)
            if file_id:
                return self.service.files().update(
                    fileId=file_id, media_body=media).execute()
            created = self.service.files().create(
                body={"name": REPORT_NAME, "parents": [root]},
                media_body=media, fields="id",
            ).execute()
            self._report_id = created["id"]
            return created

        _call(do_save, "Диск: запись отчёта")

    # ---------- весь клип целиком ----------
    def upload_video(self, video: dict, clips: list[dict],
                     missing: list[dict] | None = None,
                     existing: list[dict] | None = None,
                     on_uploaded=None, skip_report: bool = False) -> list[dict]:
        """clips: [{'path','name','description','index','start','end'}] → список загруженного.

        existing — уже загруженные части из прошлого запуска: их не грузим повторно.
        on_uploaded — вызывается после каждой новой части (чтобы сохранить прогресс).
        """
        root = self.ensure_folder(cfg.DRIVE_ROOT_FOLDER)
        channel_dir = self.ensure_folder(sanitize(video["channel"], 60), root)
        video_dir = self.ensure_folder(sanitize(video["title"], 80), channel_dir)
        if not self.dry_run:
            try:
                info = _call(
                    lambda: self.service.files().get(
                        fileId=video_dir, fields="webViewLink").execute(),
                    "Диск: ссылка на папку")
                video["drive_folder_link"] = info.get("webViewLink", "")
            except Exception as e:
                log.debug("Не удалось получить ссылку на папку: %s", e)

        by_index = {item.get("index"): item for item in (existing or [])}
        on_drive: dict[str, dict] = {}
        if not self.dry_run:
            # Если файл уже лежит в папке (например, программу закрыли сразу
            # после загрузки), второй раз его не грузим.
            try:
                listed = _call(
                    lambda: self.service.files().list(
                        q=f"'{video_dir}' in parents and trashed = false",
                        fields="files(id, name, webViewLink)", pageSize=1000,
                    ).execute(),
                    "Диск: список файлов").get("files", [])
                on_drive = {f["name"]: f for f in listed if f.get("name")}
            except Exception as e:
                log.debug("Не удалось проверить файлы на Диске: %s", e)

        uploaded = []
        for clip in clips:
            old = by_index.get(clip.get("index"))
            if old and old.get("link"):
                uploaded.append(old)
                continue
            found = on_drive.get(clip.get("name"))
            if found:
                record = {
                    "name": clip["name"],
                    "link": (found.get("webViewLink")
                             or f"https://drive.google.com/file/d/{found['id']}/view"),
                    "index": clip.get("index"),
                    "start": clip.get("start"),
                    "end": clip.get("end"),
                }
                uploaded.append(record)
                if on_uploaded is not None:
                    on_uploaded(record)
                continue
            res = self.upload_file(clip["path"], clip["name"], video_dir, clip["description"])
            record = {
                "name": clip["name"],
                "link": res["link"],
                "index": clip.get("index"),
                "start": clip.get("start"),
                "end": clip.get("end"),
            }
            uploaded.append(record)
            if on_uploaded is not None:
                on_uploaded(record)

        missing = missing or []
        if not skip_report:
            self.add_report_row(root, [
                datetime.now().strftime("%Y-%m-%d %H:%M"),
                video["channel"],
                video["author"],
                video["title"],
                video["url"],
                str(len(uploaded)),
                " | ".join(item["name"] for item in uploaded),
                " | ".join(item["link"] for item in uploaded),
                " | ".join(f"часть {m['index']}: {m.get('error', '')}" for m in missing),
            ])
        return uploaded


class Mailer:
    """Письмо со ссылками на готовые клипы. Авторизуется только при первой отправке."""

    def __init__(self, dry_run: bool = False, drive_service=None):
        self.dry_run = dry_run
        self._service = None
        self._drive_service = drive_service
        self._address: str | None = None

    def _api(self):
        if self._service is None:
            self._service = _service("gmail", "v1", GMAIL_SCOPES)
        return self._service

    def _drive(self):
        if self._drive_service is None:
            self._drive_service = _service("drive", "v3", DRIVE_SCOPES)
        return self._drive_service

    def address(self) -> str:
        """Куда отправляем письмо (для показа человеку)."""
        return self.recipient()

    def recipient(self) -> str:
        return cfg.EMAIL_TO or self.account()

    def account(self) -> str:
        """Адрес почты самого Google-аккаунта. Gmail-профиль требует прав на
        чтение (у нас только отправка), поэтому берём адрес через Диск —
        там он доступен и с drive.file."""
        if self._address is None:
            email = ""
            try:
                about = _call(
                    lambda: self._drive().about().get(fields="user").execute(),
                    "Диск: адрес аккаунта")
                email = (about.get("user") or {}).get("emailAddress", "")
            except Exception as e:
                log.debug("Не удалось узнать адрес через Диск: %s", e)
            self._address = email
        if not self._address:
            raise RuntimeError(
                "Не удалось определить адрес почты. Укажите его: меню → "
                "Общие настройки → 4. АДРЕС ПОЧТЫ."
            )
        return self._address

    def _build(self, video: dict, clips: list[dict], missing: list[dict]) -> EmailMessage:
        total = len(clips) + len(missing)
        sender = self.account()
        recipient = self.recipient()

        subject = (f"{cfg.EMAIL_SUBJECT_PREFIX}: «{video['title']}» — "
                   f"{len(clips)} из {total} "
                   f"{ui.plural(total, 'клипа', 'клипов', 'клипов')}")
        message = EmailMessage()
        message["To"] = recipient
        message["From"] = sender
        message["Subject"] = subject[:180]

        rows_html = []
        for clip in clips:
            index = clip.get("index") or 0
            span = ""
            if clip.get("start") is not None and clip.get("end") is not None:
                span = f"{_clock(clip['start'])}–{_clock(clip['end'])}"
            rows_html.append(
                "<tr>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>часть {index}</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>{html.escape(span)}</td>"
                f"<td style='padding:6px 10px;border-bottom:1px solid #eee'>"
                f"<a href='{html.escape(clip['link'])}'>открыть на Диске</a></td>"
                "</tr>"
            )

        missing_html = ""
        if missing:
            items = "".join(
                f"<li>часть {m['index']} — {html.escape(str(m.get('error', 'не смонтировалась')))}</li>"
                for m in missing
            )
            missing_html = (
                "<p style='color:#b26a00'><b>Не смонтировались:</b></p>"
                f"<ul style='color:#b26a00'>{items}</ul>"
            )

        html_body = f"""\
<div style="font-family:Segoe UI,Arial,sans-serif;font-size:15px;color:#222;max-width:640px">
  <h2 style="margin:0 0 4px">Готово: {html.escape(video['title'])}</h2>
  <p style="margin:0 0 16px;color:#666">{len(clips)} из {total} {ui.plural(total, 'клипа', 'клипов', 'клипов')} загружены на Google Диск</p>
  <table style="border-collapse:collapse;margin-bottom:16px">
    <tr><td style="padding:4px 10px;color:#666">Канал</td><td style="padding:4px 10px"><b>{html.escape(video['channel'])}</b></td></tr>
    <tr><td style="padding:4px 10px;color:#666">Автор</td><td style="padding:4px 10px">{html.escape(video['author'])}</td></tr>
    <tr><td style="padding:4px 10px;color:#666">Оригинал</td><td style="padding:4px 10px"><a href="{html.escape(video['url'])}">{html.escape(video['url'])}</a></td></tr>
    <tr><td style="padding:4px 10px;color:#666">Длительность</td><td style="padding:4px 10px">{_clock(video.get('duration') or 0)}</td></tr>
    <tr><td style="padding:4px 10px;color:#666">Дата</td><td style="padding:4px 10px">{datetime.now():%d.%m.%Y %H:%M}</td></tr>
  </table>
  {missing_html}
  <table style="border-collapse:collapse;font-size:14px">
    <tr style="background:#f5f5f5">
      <th style="padding:6px 10px;text-align:left">Часть</th>
      <th style="padding:6px 10px;text-align:left">Таймкод</th>
      <th style="padding:6px 10px;text-align:left">Видео</th>
    </tr>
    {''.join(rows_html)}
  </table>
  <p style="margin-top:18px;color:#888;font-size:12px">Это письмо отправил бот-нарезчик.</p>
</div>"""

        text_lines = [
            f"Готово: {video['title']}",
            f"Канал: {video['channel']}",
            f"Автор: {video['author']}",
            f"Оригинал: {video['url']}",
            f"Клипов: {len(clips)} из {total}",
            "",
        ]
        for clip in clips:
            text_lines.append(f"часть {clip.get('index')}: {clip['link']}")
        if missing:
            text_lines.append("")
            text_lines.append("Не смонтировались: "
                              + ", ".join(str(m["index"]) for m in missing))

        message.set_content("\n".join(text_lines))
        message.add_alternative(html_body, subtype="html")
        return message

    def send_video_report(self, video: dict, clips: list[dict],
                          missing: list[dict] | None = None) -> bool:
        """Отправляет письмо. Возвращает True, если ушло (или dry-run)."""
        missing = missing or []
        if self.dry_run:
            log.debug("Почта: проверочный режим, письмо не отправлялось")
            return True
        if not clips:
            return False

        message = self._build(video, clips, missing)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        last_error = None
        for attempt in range(1, 5):
            try:
                self._api().users().messages().send(
                    userId="me", body={"raw": raw}).execute()
                return True
            except Exception as e:  # сеть, лимиты, не включён Gmail API
                last_error = e
                log.debug("Письмо, попытка %d: %s", attempt, e)
                if attempt < 4 and _transient(e):
                    time.sleep(min(20, 3 * attempt))
        raise RuntimeError(_gmail_hint(last_error))


def _gmail_hint(error: Exception | None) -> str:
    text = str(error)
    if "has not been used in project" in text or "accessNotConfigured" in text:
        return ("Gmail API выключен в Google Cloud. Откройте console.cloud.google.com, "
                "выберите свой проект → APIs & Services → Library → Gmail API → Enable.")
    if "insufficient" in text.lower() or "insufficientPermissions" in text:
        return ("Нет прав на отправку почты. Удалите token.json и запустите снова — "
                "бот попросит разрешение на Gmail.")
    return f"Не удалось отправить письмо: {text[:300]}"
