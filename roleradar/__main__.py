"""`python -m roleradar` starts the local API server."""

import socket
import sys

import uvicorn

from roleradar.api.app import create_app
from roleradar.settings import load_settings


def port_is_free(host: str, port: int) -> bool:
    """Try to take the port for a moment. If another program holds it, this fails."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
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
