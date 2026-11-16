"""Linha de comando: sobe o servidor, invoca operações ou roda o demo.

Exemplos::

    python -m rrp server --port 5000
    python -m rrp client --port 5000 add 2 3
    python -m rrp demo --local
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from typing import Any

from .client import RPCClient, RemoteError
from .operations import OPERATION_NAMES
from .protocol import RequestReplyTimeout
from .server import RPCServer

_OPERATIONS_BY_NAME = {name: int(op) for op, name in OPERATION_NAMES.items()}


def _parse_argument(text: str) -> Any:
    """Converte um argumento da CLI: número, booleano, JSON ou string."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m rrp",
        description="Protocolo requisição-resposta sobre UDP (doOperation/getRequest/sendReply).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    server = sub.add_parser("server", help="sobe o servidor e atende requisições")
    server.add_argument("--host", default="0.0.0.0", help="interface de escuta (padrão: 0.0.0.0)")
    server.add_argument("--port", type=int, default=5000, help="porta UDP (padrão: 5000)")
    server.add_argument("--workers", type=int, default=8, help="threads de execução (padrão: 8)")
    server.add_argument("--quiet", action="store_true", help="não imprime cada requisição")

    client = sub.add_parser("client", help="invoca uma operação remota")
    client.add_argument("--host", default="127.0.0.1", help="host do servidor (padrão: 127.0.0.1)")
    client.add_argument("--port", type=int, default=5000, help="porta do servidor (padrão: 5000)")
    client.add_argument("--timeout", type=float, default=1.0, help="timeout por tentativa em s")
    client.add_argument("--retries", type=int, default=3, help="retransmissões (padrão: 3)")
    client.add_argument(
        "operation",
        choices=sorted(_OPERATIONS_BY_NAME),
        help="operação a invocar",
    )
    client.add_argument("arguments", nargs="*", help="argumentos (números, JSON ou texto)")

    demo = sub.add_parser("demo", help="executa uma bateria de chamadas remotas")
    demo.add_argument("--host", default="127.0.0.1", help="host do servidor")
    demo.add_argument("--port", type=int, default=5000, help="porta do servidor")
    demo.add_argument(
        "--local",
        action="store_true",
        help="sobe um servidor temporário na própria máquina e encerra no final",
    )
    return parser


def _cmd_server(args: argparse.Namespace) -> int:
    if not args.quiet:
        print(f"servidor escutando em udp://{args.host}:{args.port} (Ctrl+C para sair)")
    server = RPCServer(args.host, args.port, workers=args.workers)
    if args.port == 0:
        print(f"porta efetiva: {server.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nencerrando servidor...")
    finally:
        server.stop()
    if server.error is not None:
        print(f"erro no laço do servidor: {server.error!r}", file=sys.stderr)
        return 1
    print(f"estatísticas: {server.stats.snapshot()}")
    return 0


def _cmd_client(args: argparse.Namespace) -> int:
    operation_id = _OPERATIONS_BY_NAME[args.operation]
    arguments = [_parse_argument(item) for item in args.arguments]
    client = RPCClient.connect(
        args.host, args.port, timeout=args.timeout, retries=args.retries
    )
    started = time.perf_counter()
    try:
        result = client.call(operation_id, *arguments)
    except RemoteError as exc:
        print(f"erro remoto: {exc.detail}", file=sys.stderr)
        return 2
    except RequestReplyTimeout as exc:
        print(f"timeout: {exc}", file=sys.stderr)
        return 3
    elapsed = (time.perf_counter() - started) * 1000
    if isinstance(result, str):
        print(result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=None))
    print(f"({elapsed:.1f} ms)", file=sys.stderr)
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    server: RPCServer | None = None
    if args.local:
        server = RPCServer("127.0.0.1", 0, workers=8).start()
        host, port = server.address
        print(f"servidor temporário em udp://{host}:{port}")
    else:
        host, port = args.host, args.port

    client = RPCClient.connect(host, port, timeout=2.0, retries=2)
    failures = 0

    def show(label: str, func: Any, expect_error: bool = False) -> None:
        nonlocal failures
        try:
            value = func()
        except (RemoteError, RequestReplyTimeout) as exc:
            if expect_error:
                print(f"{label:<38} -> erro esperado: {exc}")
            else:
                failures += 1
                print(f"{label:<38} -> FALHOU: {exc}")
        else:
            if expect_error:
                failures += 1
                print(f"{label:<38} -> FALHOU: esperava erro, veio {value!r}")
            else:
                print(f"{label:<38} -> {value!r}")

    print("\n== operações aritméticas ==")
    show("add(2, 3)", lambda: client.add(2, 3))
    show("subtract(10, 4)", lambda: client.subtract(10, 4))
    show("multiply(6, 7)", lambda: client.multiply(6, 7))
    show("divide(10, 4)", lambda: client.divide(10, 4))
    show("divide(1, 0)  [erro no servidor]", lambda: client.divide(1, 0), expect_error=True)

    print("\n== marshalling (ida e volta) ==")
    payload = {"texto": "ação", "lista": [1, 2.5, None, True], "nulo": None}
    show("echo({...})", lambda: client.echo(payload))
    show("uppercase('sistemas distribuídos')", lambda: client.uppercase("sistemas distribuídos"))

    print("\n== provas de que a execução foi remota ==")
    show("now()", lambda: client.now())
    show("system_info()", lambda: client.system_info())
    show("operacao inexistente (id 999)", lambda: client.call(999), expect_error=True)

    print("\n== concorrência (10 chamadas em paralelo) ==")
    results: list[Any] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        value = client.add(i, i)
        with lock:
            results.append(value)

    started = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.perf_counter() - started
    correct = sorted(results) == [2 * i for i in range(10)]
    print(f"resultados corretos: {correct} em {elapsed * 1000:.1f} ms")

    if server is not None:
        print(f"\nestatísticas do servidor: {server.stats.snapshot()}")
        server.stop()

    return 0 if failures == 0 else 2


def _configure_stdio() -> None:
    """Exibe acentos corretamente também no console legado do Windows."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)  # type: ignore[attr-defined]
        except (AttributeError, OSError):  # pragma: no cover
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()

    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "server":
        return _cmd_server(args)
    if args.command == "client":
        return _cmd_client(args)
    if args.command == "demo":
        return _cmd_demo(args)
    parser.error("comando inválido")  # pragma: no cover
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
