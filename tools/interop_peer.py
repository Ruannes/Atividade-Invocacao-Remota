#!/usr/bin/env python3
"""Par de interoperabilidade: implementação independente do protocolo.

Serve para a segunda parte da atividade -- "teste a sua implementação com a
de um outro colega". Este arquivo NÃO importa o pacote ``rrp``: ele monta e
desmonta os datagramas direto com ``struct``/``json``, seguindo o mesmo
formato de mensagem descrito em ``rrp/message.py``.

Uso como cliente (contra o servidor do colega)::

    python tools/interop_peer.py client --host 192.168.0.10 --port 5000 --op add --args 2 3
    python tools/interop_peer.py client --port 5000 --op echo --args '{"ok": true}'
    python tools/interop_peer.py client --port 5000 --op add --args 1 1 --repeat 2

Uso como servidor (para o colega testar contra você)::

    python tools/interop_peer.py server --port 5001
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import struct
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable

REQ = struct.Struct("!BiiI")  # tipo(1) requestId(4) operationId(4) tamanho(4)
REP = struct.Struct("!BiBI")  # tipo(1) requestId(4) status(1) tamanho(4)
TIPO_REQ, TIPO_REP = 0, 1
OK, ERRO = 0, 1
MAX_DATAGRAM = 65507

NAMES = {
    1: "add",
    2: "subtract",
    3: "multiply",
    4: "divide",
    5: "echo",
    6: "uppercase",
    7: "now",
    8: "system_info",
    9: "sleep",
}


# ---------------------------------------------------------------------------
# Formato da mensagem
# ---------------------------------------------------------------------------

def encode_request(request_id: int, operation_id: int, arguments: list[Any]) -> bytes:
    payload = json.dumps(arguments, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return REQ.pack(TIPO_REQ, request_id, operation_id, len(payload)) + payload


def decode_request(data: bytes) -> tuple[int, int, list[Any]]:
    if len(data) < REQ.size:
        raise ValueError("datagrama truncado")
    tipo, request_id, operation_id, length = REQ.unpack_from(data)
    if tipo != TIPO_REQ:
        raise ValueError(f"tipo inesperado: {tipo}")
    if len(data) - REQ.size != length:
        raise ValueError("tamanho declarado difere do recebido")
    return request_id, operation_id, json.loads(data[REQ.size:].decode("utf-8"))


def encode_reply(request_id: int, status: int, payload: bytes) -> bytes:
    return REP.pack(TIPO_REP, request_id, status, len(payload)) + payload


def decode_reply(data: bytes) -> tuple[int, int, bytes]:
    if len(data) < REP.size:
        raise ValueError("datagrama truncado")
    tipo, request_id, status, length = REP.unpack_from(data)
    if tipo != TIPO_REP or len(data) - REP.size != length:
        raise ValueError("resposta inválida")
    return request_id, status, data[REP.size:]


# ---------------------------------------------------------------------------
# Operações de referência (mesmos operationIds do serviço de demonstração)
# ---------------------------------------------------------------------------

def _num(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"esperava número, veio {value!r}")
    return value


OPERATIONS: dict[int, Callable[[list[Any]], Any]] = {
    1: lambda a: _num(a[0]) + _num(a[1]),
    2: lambda a: _num(a[0]) - _num(a[1]),
    3: lambda a: _num(a[0]) * _num(a[1]),
    4: lambda a: _num(a[0]) / _num(a[1]),
    5: lambda a: a[0] if a else None,
    6: lambda a: str(a[0]).upper(),
    7: lambda a: datetime.now(timezone.utc).isoformat(timespec="seconds"),
    8: lambda a: {
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "python": platform.python_version(),
        "platform": platform.platform(),
    },
    9: lambda a: time.sleep(min(float(_num(a[0])), 30.0)) or float(a[0]),
}


def dispatch(operation_id: int, arguments: list[Any]) -> tuple[int, bytes]:
    handler = OPERATIONS.get(operation_id)
    if handler is None:
        return ERRO, f"operação desconhecida: {operation_id}".encode("utf-8")
    try:
        result = handler(arguments)
    except Exception as exc:  # noqa: BLE001
        return ERRO, f"{type(exc).__name__}: {exc}".encode("utf-8")
    return OK, json.dumps([result], ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# Cliente / servidor de linha de comando
# ---------------------------------------------------------------------------

def parse_argument(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def run_client(args: argparse.Namespace) -> int:
    arguments = [parse_argument(item) for item in args.args]
    datagram = encode_request(args.request_id, args.op, arguments)
    print(f"-> {len(datagram)} bytes para {args.host}:{args.port}: {datagram.hex(' ')}")

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(args.timeout)
        replies: dict[int, bytes] = {}
        for attempt in range(args.repeat):
            sock.sendto(datagram, (args.host, args.port))
            try:
                while True:
                    data, _ = sock.recvfrom(MAX_DATAGRAM)
                    request_id, status, payload = decode_reply(data)
                    if request_id != args.request_id:
                        continue
                    replies[attempt] = data
                    label = "OK" if status == OK else "ERRO"
                    text = payload.decode("utf-8", errors="replace")
                    print(f"<- tentativa {attempt + 1}: status={label} payload={text}")
                    if status == OK:
                        print(f"   resultado: {json.loads(text)}")
                    break
            except socket.timeout:
                print(f"<- tentativa {attempt + 1}: timeout após {args.timeout}s")
                return 3

        if (
            args.repeat > 1
            and len(replies) == args.repeat
            and len(set(replies.values())) == 1
        ):
            print("todas as respostas idênticas: duplicata atendida pelo cache (at-most-once)")
    return 0


def run_server(args: argparse.Namespace) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((args.host, args.port))
        print(f"peer de interoperabilidade escutando em udp://{args.host}:{sock.getsockname()[1]}")
        print("Ctrl+C para sair")
        try:
            while True:
                try:
                    data, client = sock.recvfrom(MAX_DATAGRAM)
                except ConnectionResetError:  # ICMP do Windows
                    continue
                try:
                    request_id, operation_id, arguments = decode_request(data)
                except (ValueError, json.JSONDecodeError) as exc:
                    print(f"datagrama descartado de {client}: {exc}")
                    continue
                status, payload = dispatch(operation_id, arguments)
                try:
                    sock.sendto(encode_reply(request_id, status, payload), client)
                except ConnectionResetError:
                    print(f"{client} já encerrou; resposta descartada")
                    continue
                print(
                    f"{client[0]}:{client[1]} requestId={request_id} "
                    f"op={operation_id}({NAMES.get(operation_id, '?')}) args={arguments} "
                    f"-> {'OK' if status == OK else 'ERRO'} {payload.decode('utf-8', 'replace')}"
                )
        except KeyboardInterrupt:
            print("\nencerrando peer")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(
        description="Implementação independente do protocolo requisição-resposta (UDP)."
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    client = sub.add_parser("client", help="envia uma requisição para um servidor")
    client.add_argument("--host", default="127.0.0.1")
    client.add_argument("--port", type=int, default=5000)
    client.add_argument("--op", type=int, required=True, help="operationId (1=add, 5=echo, ...)")
    client.add_argument("--args", nargs="*", default=[], help="argumentos (números, JSON ou texto)")
    client.add_argument("--timeout", type=float, default=2.0)
    client.add_argument("--request-id", type=int, default=1)
    client.add_argument("--repeat", type=int, default=1, help="reenvia a mesma mensagem N vezes")
    client.set_defaults(func=run_client)

    server = sub.add_parser("server", help="atende requisições (peer para o colega)")
    server.add_argument("--host", default="0.0.0.0")
    server.add_argument("--port", type=int, default=5001)
    server.set_defaults(func=run_server)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        print(f"erro de rede: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
