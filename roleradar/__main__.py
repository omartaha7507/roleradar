"""`python -m roleradar` starts the local API server."""

import uvicorn

from roleradar.api.app import create_app
from roleradar.settings import load_settings


def main() -> None:
    settings = load_settings()
    print(f"RoleRadar running at http://{settings.host}:{settings.port}")
    print(f"API docs:            http://{settings.host}:{settings.port}/docs")
    print("Keep this window open. Press Ctrl+C to stop.")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
