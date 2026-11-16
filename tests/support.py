"""Utilidades compartilhadas pelos testes."""

from __future__ import annotations

import socket
import time
from typing import Callable

from rrp.client import RPCClient
from rrp.protocol import RemoteRef
from rrp.server import RPCServer


def start_server(**kwargs: object) -> RPCServer:
    """Sobe um servidor em ``127.0.0.1`` com porta escolhida pelo SO."""
    server = RPCServer("127.0.0.1", 0, **kwargs)  # type: ignore[arg-type]
    return server.start()


def client_for(server: RPCServer, **kwargs: object) -> RPCClient:
    host, port = server.address
    return RPCClient(RemoteRef(host, port), **kwargs)  # type: ignore[arg-type]


def free_udp_port() -> int:
    """Porta UDP livre -- usada para simular um servidor que não responde."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Espera uma condição virar verdadeira (sem sleep fixo)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()
