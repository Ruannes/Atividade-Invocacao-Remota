"""Codificação, decodificação e marshalling das mensagens do protocolo.

O protocolo requisição-resposta é implementado sobre UDP (sockets de
datagrama), como descrito nos slides de Invocação Remota de Métodos.

Formato do datagrama -- network byte order (big-endian), sem padding:

Requisição (13 bytes de cabeçalho + argumentos)::

    0      1  tipo da mensagem      (0 = REQUISICAO)
    1..4   4  requestId             (int32, identifica a requisição do cliente)
    5..8   4  operationId           (int32, identifica a operação; melhor que o nome)
    9..12  4  tamanho dos argumentos em bytes (uint32)
    13..   n  argumentos serializados (lista JSON em UTF-8)

Resposta (10 bytes de cabeçalho + conteúdo)::

    0      1  tipo da mensagem      (1 = RESPOSTA)
    1..4   4  requestId             (int32, o mesmo da requisição)
    5      1  status                (0 = OK, 1 = ERRO)
    6..9   4  tamanho do conteúdo em bytes (uint32)
    10..   n  conteúdo (resultado JSON em UTF-8 se OK;
                        mensagem de erro em UTF-8 se ERRO)

Os argumentos e o resultado trafegam como um vetor de bytes. Nesta
implementação esse vetor é um documento JSON contendo uma lista, o que
permite transportar ``int``, ``float``, ``str``, ``bool``, ``null``,
listas e objetos sem depender de classes Python.
"""

from __future__ import annotations

import itertools
import json
import random
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Iterable

#: Maior payload aceito em uma mensagem (proteção contra datagramas absurdos).
MAX_PAYLOAD = 64 * 1024

#: Tamanho máximo de um datagrama UDP (65535 - cabeçalhos IP/UDP).
MAX_DATAGRAM = 65507

REQUEST_HEADER = struct.Struct("!BiiI")  # tipo, requestId, operationId, tamanho
REPLY_HEADER = struct.Struct("!BiBI")    # tipo, requestId, status, tamanho


class MessageType(IntEnum):
    """Tipo de mensagem do protocolo (slide "A mensagem")."""

    REQUISICAO = 0
    RESPOSTA = 1


class Status(IntEnum):
    """Resultado da execução remota."""

    OK = 0
    ERRO = 1


class ProtocolError(ValueError):
    """Mensagem corrompida, truncada ou fora do formato do protocolo.

    ``request_id`` é preenchido quando o cabeçalho permitiu recuperar o
    identificador da requisição -- nesse caso o servidor consegue responder
    com uma mensagem de erro em vez de simplesmente descartar o datagrama.
    """

    def __init__(self, message: str, request_id: int | None = None) -> None:
        super().__init__(message)
        self.request_id = request_id


@dataclass(frozen=True, slots=True)
class Request:
    """Requisição recebida pelo servidor."""

    request_id: int
    operation_id: int
    arguments: bytes = b""

    def decode_arguments(self) -> list[Any]:
        """Desempacota o vetor de bytes de argumentos."""
        return unmarshal(self.arguments)


@dataclass(frozen=True, slots=True)
class Reply:
    """Resposta devolvida pelo servidor (e recebida pelo cliente)."""

    request_id: int
    status: Status = Status.OK
    payload: bytes = b""

    @property
    def ok(self) -> bool:
        return self.status is Status.OK

    @property
    def error(self) -> str | None:
        """Mensagem de erro, quando ``status`` é ``ERRO``."""
        if self.status is Status.OK:
            return None
        return self.payload.decode("utf-8", errors="replace")

    def decode_result(self) -> Any:
        """Desempacota o resultado (primeiro item da lista JSON)."""
        if self.status is not Status.OK:
            raise ProtocolError(f"resposta de erro: {self.error}")
        values = unmarshal(self.payload)
        if not values:
            return None
        return values[0]


# ---------------------------------------------------------------------------
# Vetor de argumentos (marshalling)
# ---------------------------------------------------------------------------

def marshal(values: Iterable[Any]) -> bytes:
    """Empacota valores em um vetor de bytes (lista JSON em UTF-8)."""
    try:
        text = json.dumps(
            list(values),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"valores não serializáveis: {exc}") from exc
    return text.encode("utf-8")


def unmarshal(payload: bytes) -> list[Any]:
    """Desempacota o vetor de bytes em uma lista de valores."""
    if not payload:
        return []
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError(f"payload não é UTF-8 válido: {exc}") from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"payload não é JSON válido: {exc}") from exc
    if not isinstance(value, list):
        raise ProtocolError("payload deve ser uma lista JSON")
    return value


# ---------------------------------------------------------------------------
# Codificação dos datagramas
# ---------------------------------------------------------------------------

def encode_request(request_id: int, operation_id: int, arguments: bytes = b"") -> bytes:
    """Monta o datagrama de requisição."""
    _check_id("requestId", request_id)
    _check_id("operationId", operation_id)
    _check_payload("argumentos", arguments)
    return REQUEST_HEADER.pack(
        MessageType.REQUISICAO, request_id, operation_id, len(arguments)
    ) + arguments


def decode_request(data: bytes) -> Request:
    """Desmonta o datagrama de requisição, validando o formato."""
    if len(data) < REQUEST_HEADER.size:
        raise ProtocolError(
            f"datagrama truncado: {len(data)} bytes, mínimo {REQUEST_HEADER.size}"
        )
    msg_type, request_id, operation_id, length = REQUEST_HEADER.unpack_from(data)
    if msg_type != MessageType.REQUISICAO:
        raise ProtocolError(f"esperava REQUISICAO (0), recebeu tipo {msg_type}")
    if length > MAX_PAYLOAD:
        raise ProtocolError(
            f"argumentos excedem o limite: {length} > {MAX_PAYLOAD}", request_id
        )
    body = data[REQUEST_HEADER.size:]
    if len(body) != length:
        raise ProtocolError(
            f"tamanho declarado ({length}) difere do recebido ({len(body)})",
            request_id,
        )
    return Request(request_id=request_id, operation_id=operation_id, arguments=body)


def encode_reply(
    request_id: int,
    status: Status = Status.OK,
    payload: bytes = b"",
) -> bytes:
    """Monta o datagrama de resposta."""
    _check_id("requestId", request_id)
    _check_payload("resposta", payload)
    return REPLY_HEADER.pack(MessageType.RESPOSTA, request_id, status, len(payload)) + payload


def decode_reply(data: bytes) -> Reply:
    """Desmonta o datagrama de resposta, validando o formato."""
    if len(data) < REPLY_HEADER.size:
        raise ProtocolError(
            f"datagrama truncado: {len(data)} bytes, mínimo {REPLY_HEADER.size}"
        )
    msg_type, request_id, status, length = REPLY_HEADER.unpack_from(data)
    if msg_type != MessageType.RESPOSTA:
        raise ProtocolError(f"esperava RESPOSTA (1), recebeu tipo {msg_type}")
    try:
        status_enum = Status(status)
    except ValueError as exc:
        raise ProtocolError(f"status desconhecido: {status}", request_id) from exc
    if length > MAX_PAYLOAD:
        raise ProtocolError(
            f"resposta excede o limite: {length} > {MAX_PAYLOAD}", request_id
        )
    body = data[REPLY_HEADER.size:]
    if len(body) != length:
        raise ProtocolError(
            f"tamanho declarado ({length}) difere do recebido ({len(body)})",
            request_id,
        )
    return Reply(request_id=request_id, status=status_enum, payload=body)


def _check_payload(nome: str, payload: bytes) -> None:
    if len(payload) > MAX_PAYLOAD:
        raise ProtocolError(
            f"{nome} excedem o limite: {len(payload)} > {MAX_PAYLOAD}"
        )


def _check_id(nome: str, value: int) -> None:
    if not isinstance(value, int) or not 1 <= value < 2**31:
        raise ProtocolError(f"{nome} fora do intervalo int32 positivo: {value!r}")


# ---------------------------------------------------------------------------
# Identificadores de requisição
# ---------------------------------------------------------------------------

#: Sequência de requestId. Um cliente pode ter vários em voo; a combinação
#: (endereço do cliente, requestId) identifica unicamente uma requisição e é
#: usada pelo servidor para descartar duplicatas (semântica at-most-once).
_request_ids = itertools.count(random.randrange(1, 1 << 20))


def next_request_id() -> int:
    """Próximo identificador de requisição (sempre positivo, int32)."""
    return next(_request_ids) % (2**31 - 1) or 1
