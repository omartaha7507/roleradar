"""Command line:

    python -m roleradar                     start the server and dashboard
    python -m roleradar new-profile omar    create profiles/omar/ from the example
    python -m roleradar check               show the setup checklist in the terminal
"""

from __future__ import annotations

import argparse
import re
import shutil
import socket
import sys
import webbrowser

from roleradar.settings import load_settings


def port_is_free(host: str, port: int) -> bool:
    """Is anything answering on this port? Knocking is more reliable than trying to
    take the port: a port closed seconds ago can't be taken yet, but nobody answers."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return False  # someone answered: the port is in use
    except OSError:
        return True


def serve(open_browser: bool) -> None:
    import uvicorn

    from roleradar.api.app import create_app

    settings = load_settings()
    if not port_is_free(settings.host, settings.port):
        print(f"Port {settings.port} is already in use by another program.")
        print("Most likely RoleRadar is already running in another window,")
        print("or another local server is using this port.")
        print("Close that program, or set another port in settings.env, e.g.:")
        print(f"    ROLERADAR_PORT={settings.port + 1}")
        sys.exit(1)

    url = f"http://{settings.host}:{settings.port}"
    print(f"RoleRadar is running: {url}")
    print("Keep this window open. Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning")


def new_profile(name: str) -> None:
    from roleradar.profiles import PROFILE_NAME_RE

    settings = load_settings()
    if not PROFILE_NAME_RE.match(name):
        sys.exit("Use lowercase letters, digits, - and _ only (for example: omar or sara-k).")
    target = settings.profiles_dir / name
    if target.exists():
        sys.exit(f"profiles/{name}/ already exists.")
    shutil.copytree(settings.profiles_dir / "example", target)
    yaml_path = target / "profile.yaml"
    pretty = " ".join(part.capitalize() for part in re.split(r"[-_]", name))
    yaml_path.write_text(yaml_path.read_text(encoding="utf-8").replace("Alex Example", pretty), encoding="utf-8")
    print(f"Created profiles/{name}/. Now edit these three files with your own details:")
    for file in ("profile.yaml", "identity.md", "projects.md"):
        print(f"  {target / file}")
    print("Only facts written there can ever appear in a CV.")


def check() -> None:
    from roleradar.checks import run_checks
    from roleradar.db import connect, migrate
    from roleradar.profiles import list_profile_names

    settings = load_settings()
    conn = connect(settings.db_path)
    migrate(conn)
    names = [n for n in list_profile_names(settings.profiles_dir) if n != "example"]
    for item in run_checks(conn, settings, names[0] if names else None):
        mark = "OK " if item["ok"] else "FIX"
        print(f"[{mark}] {item['label']}: {item['detail']}")
        if item["fix"]:
            print(f"      -> {item['fix']}")
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="roleradar", description="Local-first job search pipeline")
    sub = parser.add_subparsers(dest="command")
    serve_cmd = sub.add_parser("serve", help="start the server (the default)")
    serve_cmd.add_argument("--no-browser", action="store_true", help="don't open the dashboard")
    profile_cmd = sub.add_parser("new-profile", help="create a profile from the example")
    profile_cmd.add_argument("name")
    sub.add_parser("check", help="show the setup checklist")
    args = parser.parse_args()

    if args.command == "new-profile":
        new_profile(args.name)
    elif args.command == "check":
        check()
    else:
        serve(open_browser=not getattr(args, "no_browser", False))


if __name__ == "__main__":
    main()
