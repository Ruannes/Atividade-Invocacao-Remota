"""Primitivas do protocolo requisição-resposta sobre UDP.

Mapeamento direto com os slides de Invocação Remota de Métodos:

* ``doOperation(RemoteRef, operationId, arguments)`` -> :func:`do_operation`
  (lado cliente: envia a requisição e bloqueia até a resposta).
* ``getRequest()`` -> :meth:`RequestReplyEndpoint.get_request`
  (lado servidor: bloqueia até chegar uma requisição).
* ``sendReply(reply, clientHost, clientPort)`` ->
  :meth:`RequestReplyEndpoint.send_reply`
  (lado servidor: encerra o ciclo e desbloqueia o ``doOperation``).

Todo o transporte é feito com ``socket.SOCK_DGRAM`` (UDP), usando apenas as
operações ``sendto``/``recvfrom`` -- a garantia de entrega fica a cargo do
próprio protocolo, via timeout e retransmissão.
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass

from . import message
from .message import MAX_DATAGRAM, ProtocolError, Reply, Request

#: Tempo (s) de espera por uma resposta antes de retransmitir.
DEFAULT_TIMEOUT = 1.0

#: Número de retransmissões antes de desistir.
DEFAULT_RETRIES = 3


class RequestReplyTimeout(TimeoutError):
    """Nenhuma resposta recebida após esgotar o timeout e as retransmissões."""


@dataclass(frozen=True, slots=True)
class RemoteRef:
    """Referência para um servidor remoto (``RemoteRef s`` nos slides)."""

    host: str
    port: int

    def __post_init__(self) -> None:
        if not self.host:
            raise ValueError("host do servidor remoto é obrigatório")
        if not 0 <= self.port <= 65535:
            raise ValueError(f"porta inválida: {self.port}")

    @property
    def address(self) -> tuple[str, int]:
        return (self.host, self.port)

    def __str__(self) -> str:  # pragma: no cover - conveniência
        return f"{self.host}:{self.port}"


@dataclass(frozen=True, slots=True)
class IncomingRequest:
    """Resultado de ``getRequest()``: a requisição e a origem do cliente."""

    request: Request
    client_host: str
    client_port: int

    @property
    def client_address(self) -> tuple[str, int]:
        return (self.client_host, self.client_port)


# ---------------------------------------------------------------------------
# Lado cliente: doOperation()
# ---------------------------------------------------------------------------

def do_operation(
    remote: RemoteRef,
    operation_id: int,
    arguments: bytes = b"",
    *,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    request_id: int | None = None,
) -> Reply:
    """Invoca uma operação remota e devolve a resposta (chamada bloqueante).

    ``arguments`` é o vetor de bytes a ser enviado ao servidor. Se nenhuma
    resposta chegar em ``timeout`` segundos, a mesma requisição (mesmo
    ``request_id``) é retransmitida até ``retries`` vezes, o que permite ao
    servidor descartar duplicatas sem executar a operação duas vezes.

    Levanta :class:`RequestReplyTimeout` quando todas as tentativas falham.
    """
    if timeout <= 0:
        raise ValueError("timeout deve ser positivo")
    if retries < 0:
        raise ValueError("retries não pode ser negativo")

    request_id = message.next_request_id() if request_id is None else request_id
    datagram = message.encode_request(request_id, operation_id, arguments)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for attempt in range(retries + 1):
            try:
                sock.sendto(datagram, remote.address)
            except ConnectionResetError:
                # Windows: ICMP "port unreachable" da tentativa anterior.
                pass
            reply = _receive_reply(sock, request_id, timeout)
            if reply is not None:
                return reply
            if attempt < retries:
                time.sleep(0)  # cede a CPU; a próxima tentativa reenvia
        raise RequestReplyTimeout(
            f"sem resposta de {remote} para a operação {operation_id} "
            f"após {retries + 1} tentativa(s)"
        )


def _receive_reply(
    sock: socket.socket, request_id: int, timeout: float
) -> Reply | None:
    """Espera pela resposta com o ``request_id`` esperado.

    Datagramas inválidos ou relativos a outra requisição são ignorados --
    em UDP eles podem chegar fora de ordem ou vindos de qualquer origem.
    """
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        sock.settimeout(remaining)
        try:
            data, _addr = sock.recvfrom(MAX_DATAGRAM)
        except socket.timeout:
            return None
        except ConnectionResetError:
            # Windows: ICMP "port unreachable" pode aparecer como reset.
            return None
        try:
            reply = message.decode_reply(data)
        except ProtocolError:
            continue
        if reply.request_id != request_id:
            continue
        return reply


# ---------------------------------------------------------------------------
# Lado servidor: getRequest() e sendReply()
# ---------------------------------------------------------------------------

class RequestReplyEndpoint:
    """Extremidade servidora do protocolo (UDP).

    ``get_request()`` implementa a primitiva ``getRequest`` e
    ``send_reply()`` implementa ``sendReply``.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 0,
        *,
        recv_timeout: float | None = None,
    ) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._sock.bind((host, port))
        except OSError:
            self._sock.close()
            raise
        self._sock.settimeout(recv_timeout)
        self._closed = False

    # -- informações -----------------------------------------------------
    @property
    def address(self) -> tuple[str, int]:
        """Endereço efetivo (útil quando ``port=0``)."""
        return self._sock.getsockname()

    @property
    def closed(self) -> bool:
        return self._closed

    # -- primitivas ------------------------------------------------------
    def get_request(self) -> IncomingRequest:
        """Bloqueia até receber uma requisição e a devolve já decodificada.

        Levanta :class:`ProtocolError` para datagramas malformados (o
        servidor decide se responde com erro ou descarta) e
        :class:`socket.timeout` se ``recv_timeout`` expirar.
        """
        data, (client_host, client_port) = self._sock.recvfrom(MAX_DATAGRAM)
        try:
            request = message.decode_request(data)
        except ProtocolError as exc:
            # guarda a origem para que o servidor possa responder erro
            exc.client_address = (client_host, client_port)  # type: ignore[attr-defined]
            raise
        return IncomingRequest(request, client_host, client_port)

    def send_reply(
        self,
        reply: Reply | bytes,
        client_host: str,
        client_port: int,
    ) -> None:
        """Envia a resposta ao cliente, encerrando o ciclo requisição-resposta."""
        datagram = reply if isinstance(reply, bytes) else message.encode_reply(
            reply.request_id, reply.status, reply.payload
        )
        self._sock.sendto(datagram, (client_host, client_port))

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._sock.close()

    def __enter__(self) -> "RequestReplyEndpoint":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
