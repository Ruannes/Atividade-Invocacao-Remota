"""Identificadores das operações remotas.

Nos slides, ``operationId`` é "a identidade da operação (melhor que o nome)":
um inteiro pequeno e estável, independente do nome usado pela aplicação.
Novas operações podem ser registradas com qualquer inteiro; estes são os
identificadores do serviço de demonstração (:mod:`rrp.services`).
"""

from __future__ import annotations

from enum import IntEnum


class Operation(IntEnum):
    """Operações oferecidas pelo serviço de demonstração."""

    ADD = 1
    SUBTRACT = 2
    MULTIPLY = 3
    DIVIDE = 4
    ECHO = 5
    UPPERCASE = 6
    NOW = 7
    SYSTEM_INFO = 8
    SLEEP = 9


#: Nome legível de cada operação (usado pela CLI, pelos testes e no README).
OPERATION_NAMES: dict[int, str] = {
    Operation.ADD: "add",
    Operation.SUBTRACT: "subtract",
    Operation.MULTIPLY: "multiply",
    Operation.DIVIDE: "divide",
    Operation.ECHO: "echo",
    Operation.UPPERCASE: "uppercase",
    Operation.NOW: "now",
    Operation.SYSTEM_INFO: "system_info",
    Operation.SLEEP: "sleep",
}
