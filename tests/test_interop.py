"""Teste de interoperabilidade: valida a especificação contra uma
implementação independente ("o colega").

Este arquivo NÃO usa :mod:`rrp.message` nem :mod:`rrp.protocol` para montar
ou desmontar datagramas: ele reimplementa o protocolo com ``struct`` e
``json``, como faria alguém escrevendo em outra linguagem.

* cliente independente -> nosso servidor
* nosso cliente -> servidor independente

Se estes testes passam, a implementação está aderente ao formato publicado
e deve conversar com a implementação de outro colega.
"""

from __future__ import annotations

import json
import socket
import struct
import threading
import unittest
from typing import Any, Callable

from rrp import message
from rrp.client import RemoteError
from rrp.message import MAX_DATAGRAM
from rrp.services import ServiceRegistry
from tests.support import client_for, start_server

# --- implementação de referência (independente) ---------------------------
# big-endian, sem padding: requisicao = tipo(1) requestId(4) op(4) tam(4)
REQ = struct.Struct("!BiiI")
# big-endian, sem padding: resposta = tipo(1) requestId(4) status(1) tam(4)
REP = struct.Struct("!BiBI")

TIPO_REQ, TIPO_REP = 0, 1
OK, ERRO = 0, 1


def ref_request_bytes(request_id: int, operation_id: int, arguments: list[Any]) -> bytes:
    payload = json.dumps(
        arguments, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return REQ.pack(TIPO_REQ, request_id, operation_id, len(payload)) + payload


def ref_parse_request(data: bytes) -> tuple[int, int, list[Any]]:
    if len(data) < REQ.size:
        raise ValueError("datagrama curto")
    tipo, request_id, operation_id, length = REQ.unpack_from(data)
    if tipo != TIPO_REQ or len(data) - REQ.size != length:
        raise ValueError("datagrama inválido")
    payload = data[REQ.size:]
    return request_id, operation_id, json.loads(payload.decode("utf-8"))


def ref_parse_reply(data: bytes) -> tuple[int, int, bytes]:
    if len(data) < REP.size:
        raise ValueError("datagrama curto")
    tipo, request_id, status, length = REP.unpack_from(data)
    if tipo != TIPO_REP or len(data) - REP.size != length:
        raise ValueError("datagrama inválido")
    return request_id, status, data[REP.size:]


def ref_call(
    address: tuple[str, int],
    request_id: int,
    operation_id: int,
    arguments: list[Any],
    *,
    timeout: float = 2.0,
) -> tuple[int, bytes]:
    """Cliente de referência: envia uma requisição e devolve (status, payload)."""
    datagram = ref_request_bytes(request_id, operation_id, arguments)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(datagram, address)
        while True:
            data, _ = sock.recvfrom(MAX_DATAGRAM)
            try:
                reply_id, status, payload = ref_parse_reply(data)
            except ValueError:
                continue
            if reply_id == request_id:
                return status, payload


class RefServer(threading.Thread):
    """Servidor de referência que atende requisições até ser encerrado."""

    def __init__(self, handlers: dict[int, Callable[[list[Any]], Any]]) -> None:
        super().__init__(daemon=True)
        self.handlers = handlers
        self.received: list[tuple[int, int]] = []
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.settimeout(0.2)
        self.address: tuple[str, int] = self._sock.getsockname()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                data, client = self._sock.recvfrom(MAX_DATAGRAM)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                request_id, operation_id, arguments = ref_parse_request(data)
            except ValueError:
                continue
            self.received.append((request_id, operation_id))
            handler = self.handlers.get(operation_id)
            try:
                if handler is None:
                    raise LookupError(f"operação desconhecida: {operation_id}")
                result = handler(arguments)
            except Exception as exc:  # noqa: BLE001
                payload = f"{type(exc).__name__}: {exc}".encode("utf-8")
                status = ERRO
            else:
                payload = json.dumps([result], ensure_ascii=False).encode("utf-8")
                status = OK
            self._sock.sendto(REP.pack(TIPO_REP, request_id, status, len(payload)) + payload, client)

    def stop(self) -> None:
        self._stop.set()
        self._sock.close()
        self.join(5)


class LayoutConformanceTest(unittest.TestCase):
    def test_cabecalhos_tem_o_tamanho_publicado(self) -> None:
        self.assertEqual(REQ.size, 13)
        self.assertEqual(REP.size, 10)

    def test_nosso_datagrama_e_identico_ao_da_referencia(self) -> None:
        for request_id, operation_id, arguments in [
            (1, 1, [2, 3]),
            (2**30, 5, ["ação", {"k": [1, None]}]),
            (7, 9, []),
        ]:
            with self.subTest(arguments=arguments):
                ours = message.encode_request(
                    request_id, operation_id, message.marshal(arguments)
                )
                theirs = ref_request_bytes(request_id, operation_id, arguments)
                self.assertEqual(ours, theirs)

    def test_nossa_resposta_e_identica_ao_da_referencia(self) -> None:
        payload = json.dumps([42]).encode("utf-8")
        ours = message.encode_reply(3, message.Status.OK, payload)
        theirs = REP.pack(TIPO_REP, 3, OK, len(payload)) + payload
        self.assertEqual(ours, theirs)


class RefClientAgainstOurServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = start_server()
        self.addCleanup(self.server.stop)

    def test_add(self) -> None:
        status, payload = ref_call(self.server.address, 101, 1, [7, 5])
        self.assertEqual(status, OK)
        self.assertEqual(json.loads(payload.decode("utf-8")), [12])

    def test_echo_de_estrutura_com_acentos(self) -> None:
        value = {"texto": "ação", "lista": [1, 2.5, None, True]}
        status, payload = ref_call(self.server.address, 102, 5, [value])
        self.assertEqual(status, OK)
        self.assertEqual(json.loads(payload.decode("utf-8")), [value])

    def test_operacao_desconhecida_devolve_erro(self) -> None:
        status, payload = ref_call(self.server.address, 103, 404, [])
        self.assertEqual(status, ERRO)
        self.assertIn("UnknownOperation", payload.decode("utf-8"))

    def test_divisao_por_zero_devolve_erro_utf8(self) -> None:
        status, payload = ref_call(self.server.address, 104, 4, [1, 0])
        self.assertEqual(status, ERRO)
        self.assertIn("ZeroDivisionError", payload.decode("utf-8"))

    def test_requisicao_minima_sem_argumentos(self) -> None:
        status, payload = ref_call(self.server.address, 105, 7, [])
        self.assertEqual(status, OK)
        self.assertIsInstance(json.loads(payload.decode("utf-8"))[0], str)


class OurClientAgainstRefServerTest(unittest.TestCase):
    def setUp(self) -> None:
        def add(args: list[Any]) -> Any:
            return args[0] + args[1]

        self.ref = RefServer(
            {
                1: add,
                5: lambda args: args[0],
                6: lambda args: args[0].upper(),
            }
        )
        self.ref.start()
        self.addCleanup(self.ref.stop)
        self.client = client_for(self.ref, timeout=1.0, retries=0)  # type: ignore[arg-type]

    def test_add(self) -> None:
        self.assertEqual(self.client.add(2, 3), 5)

    def test_echo(self) -> None:
        self.assertEqual(self.client.echo([1, {"a": "b"}]), [1, {"a": "b"}])

    def test_uppercase(self) -> None:
        self.assertEqual(self.client.uppercase("teste"), "TESTE")

    def test_erro_de_operacao_desconhecida(self) -> None:
        with self.assertRaises(RemoteError) as ctx:
            self.client.call(999)
        self.assertIn("LookupError", ctx.exception.detail)

    def test_servidor_de_referencia_viu_os_operation_ids(self) -> None:
        self.client.add(1, 1)
        self.client.echo("x")
        self.assertEqual([op for _rid, op in self.ref.received], [1, 5])
        request_ids = [rid for rid, _op in self.ref.received]
        self.assertEqual(len(set(request_ids)), len(request_ids), "requestId por chamada")


class DuplicateFromRefClientTest(unittest.TestCase):
    def test_mesma_requisicao_executa_uma_vez(self) -> None:
        registry = ServiceRegistry()
        calls: list[int] = []
        registry.register(50, lambda value: calls.append(value) or value * 10, "contar")
        server = start_server(registry=registry)
        self.addCleanup(server.stop)

        datagram = ref_request_bytes(900, 50, [6])
        replies = []
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(2)
            for _ in range(2):
                sock.sendto(datagram, server.address)
                data, _ = sock.recvfrom(MAX_DATAGRAM)
                replies.append(ref_parse_reply(data))

        self.assertEqual(calls, [6])
        self.assertEqual(replies[0], replies[1])
        self.assertEqual(json.loads(replies[0][2].decode("utf-8")), [60])


if __name__ == "__main__":
    unittest.main()
