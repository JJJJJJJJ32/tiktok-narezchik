# -*- coding: utf-8 -*-
"""Сборка C++-модуля silence_finder (MinGW g++ или MSVC cl).

Запуск: python build_cpp.py
Результат: cpp/bin/silence_finder[.exe]
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "cpp" / "silence_finder.cpp"
BIN_DIR = ROOT / "cpp" / "bin"
EXE = BIN_DIR / ("silence_finder.exe" if os.name == "nt" else "silence_finder")


def find_gxx():
    """Ищем g++ в PATH и в локальной папке tools/mingw64."""
    local = ROOT / "tools" / "mingw64" / "bin" / ("g++.exe" if os.name == "nt" else "g++")
    if local.exists():
        return str(local)
    return shutil.which("g++")


def find_vs_devcmd():
    """Ищем VsDevCmd.bat через vswhere."""
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / \
        "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.exists():
        return None
    try:
        out = subprocess.run(
            [str(vswhere), "-latest", "-products", "*", "-property", "installationPath"],
            capture_output=True, text=True, timeout=30,
        )
        path = out.stdout.strip()
    except Exception:
        return None
    if not path:
        return None
    bat = Path(path) / "Common7" / "Tools" / "VsDevCmd.bat"
    return str(bat) if bat.exists() else None


def build():
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    gxx = find_gxx()
    if gxx:
        cmd = [gxx, "-O2", "-std=c++17", "-static", "-o", str(EXE), str(SRC)]
        print("Сборка MinGW:", " ".join(cmd))
    else:
        bat = find_vs_devcmd()
        if not bat:
            print("Не найден ни g++, ни MSVC. Установите MinGW-w64 или Visual Studio Build Tools.")
            return 1
        cmd = ["cmd", "/c", f'call "{bat}" >nul && cl /nologo /O2 /EHsc /std:c++17 '
                            f'/Fe:"{EXE}" "{SRC}" /Fo:"{BIN_DIR}\\"']
        print("Сборка MSVC:", " ".join(cmd))

    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        print("СБОРКА НЕ УДАЛАСЬ")
        return result.returncode
    print(f"Готово: {EXE}")
    return 0


if __name__ == "__main__":
    sys.exit(build())
