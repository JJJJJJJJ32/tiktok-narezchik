# -*- coding: utf-8 -*-
"""Проверка повторов сетевых операций Google Диска (uploader._call).

Запуск:  .venv\\Scripts\\python.exe tests\\retry.py
Паузы отключены, чтобы тест шёл мгновенно.
"""

import http.client
import io
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import uploader

logging.basicConfig(level=logging.CRITICAL)
uploader.time.sleep = lambda seconds: None


class Flaky:
    """Первые два вызова рвут соединение, третий проходит."""

    def __init__(self, fail=2):
        self.n = 0
        self.fail = fail

    def __call__(self):
        self.n += 1
        if self.n <= self.fail:
            raise http.client.RemoteDisconnected(
                "Remote end closed connection without response")
        return "ok"


class ServerError:
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        if self.n < 3:
            error = RuntimeError("boom")
            error.resp = type("Resp", (), {"status": 503})()
            raise error
        return "ok"


def check_retry():
    action = Flaky()
    assert uploader._call(action, "тест", tries=4) == "ok"
    assert action.n == 3, action.n


def check_server_error():
    action = ServerError()
    assert uploader._call(action, "тест", tries=4) == "ok"
    assert action.n == 3, action.n


def check_gives_up():
    action = Flaky(fail=10)
    try:
        uploader._call(action, "тест", tries=3)
    except http.client.RemoteDisconnected:
        pass
    else:
        raise AssertionError("обрыв не проброшен наружу")
    assert action.n == 3, action.n


def check_no_retry_for_logic_error():
    calls = []

    def action():
        calls.append(1)
        raise ValueError("битый запрос")

    try:
        uploader._call(action, "тест")
    except ValueError:
        pass
    else:
        raise AssertionError("логическая ошибка не проброшена")
    assert len(calls) == 1, len(calls)


class FakeFilesDrive:
    """Папки и файлы: первый create успевает создать объект, но ответ теряется."""

    def __init__(self):
        self.folders = []
        self.blobs = []
        self.created = 0
        self.drops = 1

    def files(self):
        return self

    def list(self, q="", fields="", pageSize=1, **kwargs):
        if "mimeType" in q:
            found = list(self.folders)
        else:
            found = [dict(item) for item in self.blobs]

        def go():
            return {"files": found[:pageSize]}

        return Req(go)

    def create(self, body, fields="id", media_body=None):
        def go():
            self.created += 1
            if body.get("mimeType"):
                self.folders.append({"id": f"folder{len(self.folders) + 1}"})
            else:
                self.blobs.append({"id": f"file{len(self.blobs) + 1}",
                                   "name": body["name"],
                                   "webViewLink": "https://drive.google.com/file/d/x/view"})
            if self.drops:
                self.drops -= 1
                raise http.client.RemoteDisconnected(
                    "Remote end closed connection without response")
            if body.get("mimeType"):
                return {"id": self.folders[-1]["id"]}
            return dict(self.blobs[-1])

        return Req(go)


def check_folder_not_duplicated():
    drive = FakeFilesDrive()
    first = uploader.DriveUploader(dry_run=False, service=drive)
    folder = first.ensure_folder("Канал")

    second = uploader.DriveUploader(dry_run=False, service=drive)  # как новый запуск
    assert second.ensure_folder("Канал") == folder
    assert drive.created == 1, drive.created     # папка одна
    assert len(drive.folders) == 1, drive.folders


def check_file_not_duplicated():
    drive = FakeFilesDrive()
    upload = uploader.DriveUploader(dry_run=False, service=drive)
    path = Path(tempfile.mkdtemp()) / "clip.mp4"
    path.write_bytes(b"video")

    result = upload.upload_file(path, "clip.mp4", "folder1", "описание")
    assert drive.created == 1, drive.created     # файл загружен один раз
    assert len(drive.blobs) == 1, drive.blobs
    assert result["link"] == "https://drive.google.com/file/d/x/view", result


class Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeDrive:
    """Отчёт уже есть на Диске; первая запись рвёт соединение."""

    def __init__(self):
        self.payload = None
        self.updates = 0
        self.creates = 0
        self.drop = True

    def files(self):
        return self

    def list(self, **kwargs):
        files = [{"id": "report-id"}] if self.payload is not None else []
        return Req(lambda: {"files": files})

    def get_media(self, fileId):
        return Req(lambda: self.payload)

    def update(self, fileId, media_body):
        def go():
            self.updates += 1
            if self.drop:
                self.drop = False
                raise http.client.RemoteDisconnected(
                    "Remote end closed connection without response")
            self.payload = media_body.getbytes(0, -1)
            return {"id": fileId}
        return Req(go)

    def create(self, body, media_body, fields="id"):
        def go():
            self.creates += 1
            self.payload = media_body.getbytes(0, -1)
            return {"id": "new"}
        return Req(go)


def check_report_not_duplicated():
    import csv

    drive = FakeDrive()
    # Отчёт уже есть на Диске и содержит одну запись о другом видео.
    drive.payload = ("дата;канал;автор;название;ссылка;частей;имена файлов;"
                     "ссылки на Drive;пропущено\n"
                     "2026-11-14;A;B;C;https://youtu.be/old;1;old.mp4;d;\n"
                     ).encode("utf-8-sig")
    upload = uploader.DriveUploader(dry_run=False, service=drive)
    upload._local_report = Path(tempfile.mkdtemp()) / "report.csv"
    upload._write_local_report([uploader.REPORT_HEADER])

    upload.add_report_row("root", ["2026-11-15", "канал", "автор", "название",
                                   "https://youtu.be/x", "1", "f.mp4", "d", ""])
    assert drive.updates == 2, drive.updates     # первая попытка оборвалась
    assert drive.creates == 0, drive.creates     # второй файл не создан
    rows = list(csv.reader(io.StringIO(drive.payload.decode("utf-8-sig")),
                           delimiter=";"))
    assert len(rows) == 3, rows                  # шапка + старое видео + новое
    assert rows[-1][4] == "https://youtu.be/x", rows[-1]

    # Повторная запись того же видео заменяет строку, а не добавляет новую.
    upload.add_report_row("root", ["2026-11-15", "канал", "автор", "название",
                                   "https://youtu.be/x", "1", "f.mp4", "d", ""])
    rows = list(csv.reader(io.StringIO(drive.payload.decode("utf-8-sig")),
                           delimiter=";"))
    assert len(rows) == 3, rows


def main():
    check_retry()
    check_server_error()
    check_gives_up()
    check_no_retry_for_logic_error()
    check_report_not_duplicated()
    check_folder_not_duplicated()
    check_file_not_duplicated()
    print("ВСЁ ОК")


if __name__ == "__main__":
    main()
