from __future__ import annotations

import argparse
import os

import uvicorn

from app_core.runtime_controller import RuntimeController
from backend_service.api_app import build_service_app
from config import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Lulu's local backend service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--launch-token")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    launch_token = args.launch_token or os.getenv("LULU_SERVICE_LAUNCH_TOKEN")
    if not launch_token:
        raise SystemExit("Provide --launch-token or set LULU_SERVICE_LAUNCH_TOKEN.")
    controller = RuntimeController(Settings())
    controller.bootstrap()
    app = build_service_app(controller, launch_token=launch_token, enforce_loopback=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
