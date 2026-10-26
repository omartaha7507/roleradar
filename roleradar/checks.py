"""The setup checklist on the dashboard: is everything RoleRadar needs in place?

Each check says what is wrong in plain words and how to fix it, so a friend
setting RoleRadar up doesn't need to read the code.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone

from roleradar import store
from roleradar.ollama import OllamaClient, OllamaError
from roleradar.pdf import find_browser
from roleradar.profiles import ProfileError, list_profile_names, load_profile
from roleradar.scraping import extension_last_seen
from roleradar.settings import Settings


def _check(key: str, label: str, ok: bool, detail: str, fix: str | None = None) -> dict:
    return {"key": key, "label": label, "ok": ok, "detail": detail, "fix": None if ok else fix}


def run_checks(conn: sqlite3.Connection, settings: Settings, profile: str | None,
               ollama: OllamaClient | None = None) -> list[dict]:
    checks = []

    # 1. A real profile (not just the example)
    names = [n for n in list_profile_names(settings.profiles_dir) if n != "example"]
    if profile and profile != "example":
        try:
            load_profile(settings.profiles_dir, profile)
            checks.append(_check("profile", "Your profile", True, f"profiles/{profile}/ is valid"))
        except ProfileError as exc:
            checks.append(_check("profile", "Your profile", False, str(exc), f"Fix profiles/{profile}/profile.yaml"))
    else:
        checks.append(_check("profile", "Your profile", bool(names),
                             f"{len(names)} personal profile(s)" if names else "Only the example profile exists",
                             "Run: python -m roleradar new-profile <your-name>, then edit the three files"))

    # 2. At least one saved search
    if profile:
        searches = [s for s in store.list_searches(conn, profile) if s["enabled"]]
        checks.append(_check("searches", "Saved searches", bool(searches), f"{len(searches)} enabled",
                             "Open a LinkedIn job search in Chrome, click the RoleRadar icon, 'Save this LinkedIn search'"))

    # 3. Chrome extension has checked in recently
    seen = extension_last_seen(conn)
    minutes = None
    if seen:
        then = datetime.strptime(seen, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        minutes = (datetime.now(timezone.utc) - then).total_seconds() / 60
    checks.append(_check(
        "extension", "Chrome extension", minutes is not None and minutes < 3,
        "Checked in just now" if minutes is not None and minutes < 3
        else (f"Last seen {int(minutes)} minutes ago" if minutes is not None else "Never seen"),
        "In Chrome: chrome://extensions, Developer mode on, 'Load unpacked', pick the extension folder; keep Chrome open",
    ))

    # 4. Ollama and the ranking model
    ollama = ollama or OllamaClient(settings.ollama_url, 5)
    try:
        has = ollama.has_model(settings.ollama_model)
        checks.append(_check("ollama", "Ollama (local AI)", True, "Running"))
        checks.append(_check("model", f"Model {settings.ollama_model}", has,
                             "Downloaded" if has else "Not downloaded",
                             f"Run: ollama pull {settings.ollama_model}"))
    except OllamaError:
        checks.append(_check("ollama", "Ollama (local AI)", False, "Not running",
                             "Install from ollama.com and open the Ollama app"))

    # 5. Claude Code, for writing
    found = shutil.which(settings.claude_command)
    checks.append(_check("claude", "Claude Code (CV writer)", bool(found),
                         "Installed" if found else "Not found",
                         "Install Claude Code (see README), then run 'claude' once in a terminal and sign in"))

    # 6. Chrome or Edge, for PDFs
    browser = find_browser()
    checks.append(_check("pdf", "PDF printing", bool(browser),
                         "Chrome or Edge found" if browser else "No Chrome or Edge found (HTML only)",
                         "Install Google Chrome"))
    return checks
