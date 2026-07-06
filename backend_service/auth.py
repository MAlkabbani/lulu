from __future__ import annotations

from fastapi import HTTPException, Request, WebSocket, status
from starlette.websockets import WebSocketDisconnect


def is_loopback_host(host: str | None) -> bool:
    return host in {"127.0.0.1", "::1", "localhost"}


def extract_bearer_token(header_value: str | None) -> str | None:
    if not header_value:
        return None
    prefix = "bearer "
    if not header_value.lower().startswith(prefix):
        return None
    token = header_value[len(prefix) :].strip()
    return token or None


def require_http_auth(
    request: Request,
    *,
    expected_token: str,
    enforce_loopback: bool,
) -> None:
    if enforce_loopback and not is_loopback_host(getattr(request.client, "host", None)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Loopback access required."
        )
    provided_token = extract_bearer_token(request.headers.get("authorization"))
    if provided_token != expected_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid launch token."
        )


async def require_websocket_auth(
    websocket: WebSocket,
    *,
    expected_token: str,
    enforce_loopback: bool,
) -> None:
    if enforce_loopback and not is_loopback_host(getattr(websocket.client, "host", None)):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        raise WebSocketDisconnect(code=status.WS_1008_POLICY_VIOLATION)
    provided_token = extract_bearer_token(websocket.headers.get("authorization"))
    if provided_token != expected_token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        raise WebSocketDisconnect(code=status.WS_1008_POLICY_VIOLATION)
