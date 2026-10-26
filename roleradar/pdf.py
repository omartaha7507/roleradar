"""Turning the CV and letter HTML into PDFs with the Chrome you already have.

Chrome can print any page to PDF without opening a window ("headless").
That gives a proper text PDF (searchable, ATS-friendly) and needs no extra
Python package. Microsoft Edge works the same way, so it is the fallback.
If neither is found, the HTML files are still saved; open one and print.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def find_browser() -> str | None:
    from roleradar.settings import read_settings_file

    override = os.environ.get("ROLERADAR_CHROME") or read_settings_file().get("ROLERADAR_CHROME")
    if override:
        return override if Path(override).exists() else None
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    ]
    for path in candidates:
        if path.is_absolute() and path.is_file():  # skips paths built from an empty variable
            return str(path)
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            return found
    return None


def html_to_pdf(html_path: Path, pdf_path: Path, browser: str | None = None, timeout: int = 60) -> bool:
    browser = browser or find_browser()
    if not browser:
        return False
    pdf_path.unlink(missing_ok=True)
    extra = []
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        extra.append("--no-sandbox")  # Linux servers running as root; never needed on Windows
    try:
        subprocess.run(
            [browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--no-first-run",
             # Printing a local file needs no internet: skip update checks and sync.
             "--disable-background-networking", "--disable-component-update", "--disable-sync",
             "--no-default-browser-check", *extra,
             f"--user-data-dir={pdf_path.parent / '.chrome-profile'}",
             f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()],
            capture_output=True, timeout=timeout, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    finally:
        shutil.rmtree(pdf_path.parent / ".chrome-profile", ignore_errors=True)
    return pdf_path.exists() and pdf_path.stat().st_size > 1000
