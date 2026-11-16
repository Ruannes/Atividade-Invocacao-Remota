"""Implementação do protocolo requisição-resposta sobre UDP.

Trabalho da disciplina de Sistemas Distribuídos -- Invocação Remota de
Métodos. As primitivas ``doOperation``, ``getRequest`` e ``sendReply``
seguem os slides de Coulouris.
"""

from __future__ import annotations

from .client import RPCClient, RemoteError, RemoteRef, RequestReplyTimeout, do_operation
from .message import (
    MAX_DATAGRAM,
    MAX_PAYLOAD,
    MessageType,
    ProtocolError,
    Reply,
    Request,
    Status,
)
from .operations import OPERATION_NAMES, Operation
from .protocol import IncomingRequest, RequestReplyEndpoint
from .server import RPCServer
from .services import ServiceRegistry, build_default_registry

__version__ = "1.0.0"

__all__ = [
    "MAX_DATAGRAM",
    "MAX_PAYLOAD",
    "OPERATION_NAMES",
    "IncomingRequest",
    "MessageType",
    "Operation",
    "ProtocolError",
    "RPCClient",
    "RPCServer",
    "RemoteError",
    "RemoteRef",
    "Reply",
    "Request",
    "RequestReplyEndpoint",
    "RequestReplyTimeout",
    "ServiceRegistry",
    "Status",
    "build_default_registry",
    "do_operation",
    "__version__",
]
