from __future__ import annotations

import argparse
import json
import os
import socket
import sys

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


def _build_startup_socket(host: str, port: int) -> socket.socket:
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((host, port))
    server_socket.listen(socket.SOMAXCONN)
    server_socket.set_inheritable(True)
    return server_socket


def _emit_startup_payload(*, host: str, port: int, startup_nonce: str, contract_version: str) -> None:
    payload = {
        "contract_version": contract_version,
        "host": host,
        "port": port,
        "startup_nonce": startup_nonce,
        "service": "lulu-backend",
    }
    print(f"LULU_BACKEND_STARTUP:{json.dumps(payload, sort_keys=True)}", flush=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    launch_token = args.launch_token or os.getenv("LULU_SERVICE_LAUNCH_TOKEN")
    startup_nonce = os.getenv("LULU_SERVICE_STARTUP_NONCE", "").strip()
    startup_contract = os.getenv("LULU_SERVICE_STARTUP_CONTRACT", "v1").strip() or "v1"
    if not launch_token:
        raise SystemExit("Provide --launch-token or set LULU_SERVICE_LAUNCH_TOKEN.")
    if not startup_nonce:
        raise SystemExit("Set LULU_SERVICE_STARTUP_NONCE for desktop helper startup.")
    controller = RuntimeController(Settings())
    controller.bootstrap()
    app = build_service_app(controller, launch_token=launch_token, enforce_loopback=True)
    config = uvicorn.Config(app, host=args.host, port=args.port, log_level="info")
    server = uvicorn.Server(config)
    startup_socket = _build_startup_socket(args.host, args.port)
    try:
        bound_host, bound_port = startup_socket.getsockname()[:2]
        _emit_startup_payload(
            host=str(bound_host),
            port=int(bound_port),
            startup_nonce=startup_nonce,
            contract_version=startup_contract,
        )
        server.run(sockets=[startup_socket])
    finally:
        startup_socket.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
