"""Serviço de demonstração: operações e registro de operações.

O servidor não conhece nomes de métodos: ele recebe um ``operationId``
inteiro e procura a função correspondente no :class:`ServiceRegistry`.
Isso mantém o protocolo genérico, como descrito nos slides.
"""

from __future__ import annotations

import os
import platform
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .operations import OPERATION_NAMES, Operation

#: Assinatura de uma operação registrada no servidor.
Handler = Callable[..., Any]

#: Limite de tempo aceito pela operação ``sleep``.
MAX_SLEEP_SECONDS = 30.0


class UnknownOperation(LookupError):
    """O ``operationId`` recebido não está registrado no servidor."""

    def __init__(self, operation_id: int) -> None:
        super().__init__(f"operação desconhecida: {operation_id}")
        self.operation_id = operation_id


class InvalidArguments(ValueError):
    """Os argumentos recebidos não servem para a operação solicitada."""


@dataclass
class ServiceRegistry:
    """Mapeia ``operationId`` -> função que executa a operação."""

    _handlers: dict[int, Handler] = field(default_factory=dict)
    _names: dict[int, str] = field(default_factory=dict)

    def register(
        self,
        operation_id: int,
        handler: Handler,
        name: str | None = None,
    ) -> Handler:
        """Registra (ou substitui) uma operação."""
        if not 0 < operation_id < 2**31:
            raise ValueError(f"operationId fora do intervalo: {operation_id}")
        if not callable(handler):
            raise TypeError("handler precisa ser chamável")
        self._handlers[operation_id] = handler
        self._names[operation_id] = name or getattr(handler, "__name__", str(operation_id))
        return handler

    def operation(self, operation_id: int) -> Handler:
        """Devolve a função registrada; levanta :class:`UnknownOperation`."""
        try:
            return self._handlers[operation_id]
        except KeyError:
            raise UnknownOperation(operation_id) from None

    def name(self, operation_id: int) -> str:
        return self._names.get(operation_id, str(operation_id))

    def operations(self) -> dict[int, str]:
        return dict(self._names)

    def __contains__(self, operation_id: object) -> bool:
        return operation_id in self._handlers

    def __len__(self) -> int:
        return len(self._handlers)


# ---------------------------------------------------------------------------
# Operações do serviço de demonstração
# ---------------------------------------------------------------------------

def _number(value: Any, label: str) -> float | int:
    """Valida um argumento numérico (``bool`` não conta como número)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidArguments(f"'{label}' deve ser um número, recebido {value!r}")
    return value


def add(a: Any, b: Any) -> float | int:
    """Soma dois números."""
    return _number(a, "a") + _number(b, "b")


def subtract(a: Any, b: Any) -> float | int:
    """Subtrai ``b`` de ``a``."""
    return _number(a, "a") - _number(b, "b")


def multiply(a: Any, b: Any) -> float | int:
    """Multiplica dois números."""
    return _number(a, "a") * _number(b, "b")


def divide(a: Any, b: Any) -> float:
    """Divide ``a`` por ``b`` (divisão por zero vira erro remoto)."""
    a_num, b_num = _number(a, "a"), _number(b, "b")
    if b_num == 0:
        raise ZeroDivisionError("divisão por zero")
    return a_num / b_num


def echo(value: Any) -> Any:
    """Devolve o argumento recebido (útil para inspecionar o marshalling)."""
    return value


def uppercase(text: Any) -> str:
    """Converte o texto para maiúsculas."""
    if not isinstance(text, str):
        raise InvalidArguments(f"'text' deve ser uma string, recebido {text!r}")
    return text.upper()


def now() -> str:
    """Data/hora do servidor em ISO-8601 (UTC)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def system_info() -> dict[str, Any]:
    """Informações do processo servidor (prova de que executou do outro lado)."""
    return {
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def sleep(seconds: Any) -> float:
    """Bloqueia o servidor por até :data:`MAX_SLEEP_SECONDS` (teste de timeout)."""
    value = float(_number(seconds, "seconds"))
    if value < 0:
        raise InvalidArguments("'seconds' não pode ser negativo")
    value = min(value, MAX_SLEEP_SECONDS)
    time.sleep(value)
    return value


def build_default_registry() -> ServiceRegistry:
    """Cria o registro com todas as operações de demonstração."""
    registry = ServiceRegistry()
    handlers: dict[Operation, Handler] = {
        Operation.ADD: add,
        Operation.SUBTRACT: subtract,
        Operation.MULTIPLY: multiply,
        Operation.DIVIDE: divide,
        Operation.ECHO: echo,
        Operation.UPPERCASE: uppercase,
        Operation.NOW: now,
        Operation.SYSTEM_INFO: system_info,
        Operation.SLEEP: sleep,
    }
    for operation, handler in handlers.items():
        registry.register(int(operation), handler, OPERATION_NAMES[int(operation)])
    return registry
