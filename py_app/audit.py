from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Request


def ensure_tailscale_peer_map(file_path: str) -> None:
    path = Path(file_path).resolve()
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"peers": {}}, indent=2) + "\n", encoding="utf-8")


def append_audit_log(
    audit_file: str,
    peers_file: str,
    *,
    request: Request,
    action: str,
    target: str = "",
    note: str = "",
    result: str = "ok",
    error: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actor = resolve_request_actor(request, peers_file)
    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "action": str(action or "").strip(),
        "target": str(target or "").strip(),
        "note": str(note or "").strip(),
        "result": "error" if str(result or "").lower() == "error" else "ok",
        "requestIp": actor["requestIp"],
        "tailscaleIp": actor["tailscaleIp"],
        "displayName": actor["displayName"],
        "hostname": actor["hostname"],
    }
    if error:
        entry["error"] = str(error)
    if extra:
        entry["extra"] = extra

    path = Path(audit_file).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
    return entry


def read_recent_audit_entries(audit_file: str, *, limit: int = 20) -> list[dict[str, Any]]:
    path = Path(audit_file).resolve()
    if not path.exists():
        return []

    lines = path.read_text(encoding="utf-8").splitlines()
    rows: list[dict[str, Any]] = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
        if len(rows) >= max(1, int(limit)):
            break
    return rows


def resolve_request_actor(request: Request, peers_file: str) -> dict[str, str]:
    request_ip = ""
    try:
        request_ip = str((request.client.host if request.client else "") or "").strip()
    except Exception:
        request_ip = ""

    peer = _load_peer_entry(peers_file, request_ip)
    display_name = ""
    hostname = ""

    if isinstance(peer, str):
        display_name = peer.strip()
    elif isinstance(peer, dict):
        display_name = str(peer.get("displayName") or peer.get("label") or "").strip()
        hostname = str(peer.get("hostname") or peer.get("machineName") or "").strip()

    if request_ip and not hostname:
        try:
            hostname = str(socket.gethostbyaddr(request_ip)[0] or "").strip()
        except Exception:
            hostname = ""

    if not display_name:
        display_name = hostname or request_ip or "unknown"

    return {
        "requestIp": request_ip,
        "tailscaleIp": request_ip,
        "displayName": display_name,
        "hostname": hostname,
    }


def _load_peer_entry(peers_file: str, request_ip: str) -> str | dict[str, Any] | None:
    if not request_ip:
        return None

    path = Path(peers_file).resolve()
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    peers: dict[str, Any] = {}
    if isinstance(data, dict):
        if isinstance(data.get("peers"), dict):
            peers = data["peers"]
        else:
            peers = data
    return peers.get(request_ip)
