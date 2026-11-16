"""Cliente de invocação remota: ``doOperation`` com uma interface amigável.

O cliente de baixo nível é :func:`rrp.protocol.do_operation`; esta classe
apenas empacota os argumentos, chama ``doOperation`` e desempacota a
resposta, transformando respostas de erro em exceções Python.
"""

from __future__ import annotations

from typing import Any

from . import message
from .operations import Operation
from .protocol import (
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
    RemoteRef,
    do_operation,
)
from .protocol import RequestReplyTimeout  # reexportado por conveniência


class RemoteError(RuntimeError):
    """A operação foi executada no servidor, mas terminou com erro."""

    def __init__(
        self,
        operation_id: int,
        detail: str,
        *,
        remote: RemoteRef | None = None,
        request_id: int | None = None,
    ) -> None:
        self.operation_id = operation_id
        self.detail = detail
        self.remote = remote
        self.request_id = request_id
        where = f" em {remote}" if remote is not None else ""
        super().__init__(f"operação {operation_id}{where} falhou: {detail}")


class RPCClient:
    """Proxy para um servidor remoto.

    Exemplo::

        client = RPCClient.connect("127.0.0.1", 5000)
        print(client.add(2, 3))        # 5
        print(client.call(99))         # levanta RemoteError (op. desconhecida)
    """

    def __init__(
        self,
        remote: RemoteRef,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout deve ser positivo")
        if retries < 0:
            raise ValueError("retries não pode ser negativo")
        self.remote = remote
        self.timeout = timeout
        self.retries = retries

    @classmethod
    def connect(
        cls,
        host: str = "127.0.0.1",
        port: int = 5000,
        **kwargs: Any,
    ) -> "RPCClient":
        """Atalho para ``RPCClient(RemoteRef(host, port), ...)``."""
        return cls(RemoteRef(host, port), **kwargs)

    # -- primitiva doOperation -------------------------------------------
    def call_raw(self, operation_id: int, arguments: bytes = b"") -> message.Reply:
        """Envia argumentos já empacotados e devolve a resposta crua."""
        return do_operation(
            self.remote,
            operation_id,
            arguments,
            timeout=self.timeout,
            retries=self.retries,
        )

    def call(self, operation_id: int, *arguments: Any) -> Any:
        """Invoca uma operação remota e devolve o resultado desempacotado.

        Levanta :class:`RemoteError` se o servidor responder com status de
        erro e :class:`RequestReplyTimeout` se não houver resposta.
        """
        reply = self.call_raw(operation_id, message.marshal(arguments))
        if not reply.ok:
            raise RemoteError(
                operation_id,
                reply.error or "erro sem detalhe",
                remote=self.remote,
                request_id=reply.request_id,
            )
        return reply.decode_result()

    # -- operações do serviço de demonstração ----------------------------
    def add(self, a: Any, b: Any) -> Any:
        return self.call(Operation.ADD, a, b)

    def subtract(self, a: Any, b: Any) -> Any:
        return self.call(Operation.SUBTRACT, a, b)

    def multiply(self, a: Any, b: Any) -> Any:
        return self.call(Operation.MULTIPLY, a, b)

    def divide(self, a: Any, b: Any) -> Any:
        return self.call(Operation.DIVIDE, a, b)

    def echo(self, value: Any) -> Any:
        return self.call(Operation.ECHO, value)

    def uppercase(self, text: str) -> str:
        return self.call(Operation.UPPERCASE, text)

    def now(self) -> str:
        return self.call(Operation.NOW)

    def system_info(self) -> dict[str, Any]:
        return self.call(Operation.SYSTEM_INFO)

    def sleep(self, seconds: Any) -> float:
        return self.call(Operation.SLEEP, seconds)


__all__ = [
    "RPCClient",
    "RemoteError",
    "RemoteRef",
    "RequestReplyTimeout",
    "do_operation",
]
