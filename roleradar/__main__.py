"""`python -m roleradar` starts the local API server."""

import socket
import sys

import uvicorn

from roleradar.api.app import create_app
from roleradar.settings import load_settings


def port_is_free(host: str, port: int) -> bool:
    """Is anything answering on this port? Knocking is more reliable than trying to
    take the port: a port closed seconds ago can't be taken yet, but nobody answers."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return False  # someone answered: the port is in use
    except OSError:
        return True


def main() -> None:
    settings = load_settings()
    if not port_is_free(settings.host, settings.port):
        print(f"Port {settings.port} is already in use by another program.")
        print("Most likely RoleRadar is already running in another window,")
        print("or another local server is using this port.")
        print("Close that program, or start RoleRadar on another port, e.g. in PowerShell:")
        print(f'    $env:ROLERADAR_PORT = "{settings.port + 1}"; .\\start.bat')
        sys.exit(1)

    print(f"RoleRadar running at http://{settings.host}:{settings.port}")
    print(f"API docs:            http://{settings.host}:{settings.port}/docs")
    print("Keep this window open. Press Ctrl+C to stop.")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
