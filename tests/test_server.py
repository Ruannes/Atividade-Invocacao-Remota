"""Testes do servidor: operações, erros, concorrência e at-most-once."""

from __future__ import annotations

import socket
import threading
import unittest

from rrp import message
from rrp.client import RPCClient, RemoteError
from rrp.message import MAX_DATAGRAM
from rrp.protocol import RemoteRef, RequestReplyTimeout
from rrp.server import RPCServer
from rrp.services import ServiceRegistry
from tests.support import client_for, start_server, wait_until


class DefaultServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = start_server()
        self.addCleanup(self.server.stop)
        self.client = client_for(self.server, timeout=1.0, retries=1)

    def test_operacoes_aritmeticas(self) -> None:
        self.assertEqual(self.client.add(2, 3), 5)
        self.assertEqual(self.client.subtract(10, 4), 6)
        self.assertEqual(self.client.multiply(6, 7), 42)
        self.assertEqual(self.client.divide(10, 4), 2.5)

    def test_echo_preserva_estruturas(self) -> None:
        payload = {"texto": "ação", "lista": [1, 2.5, None, True], "nulo": None}
        self.assertEqual(self.client.echo(payload), payload)

    def test_uppercase(self) -> None:
        self.assertEqual(self.client.uppercase("sistemas distribuídos"), "SISTEMAS DISTRIBUÍDOS")

    def test_now_e_iso(self) -> None:
        value = self.client.now()
        self.assertRegex(value, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    def test_system_info_prova_execucao_remota(self) -> None:
        info = self.client.system_info()
        self.assertIn("hostname", info)
        self.assertIn("pid", info)
        self.assertIn("python", info)

    def test_divisao_por_zero_vira_erro_remoto(self) -> None:
        with self.assertRaises(RemoteError) as ctx:
            self.client.divide(1, 0)
        self.assertIn("ZeroDivisionError", ctx.exception.detail)

    def test_operacao_desconhecida(self) -> None:
        with self.assertRaises(RemoteError) as ctx:
            self.client.call(999)
        self.assertIn("UnknownOperation", ctx.exception.detail)
        self.assertEqual(ctx.exception.operation_id, 999)

    def test_argumento_com_tipo_invalido(self) -> None:
        with self.assertRaises(RemoteError) as ctx:
            self.client.add("dois", 3)
        self.assertIn("InvalidArguments", ctx.exception.detail)

    def test_quantidade_de_argumentos_invalida(self) -> None:
        with self.assertRaises(RemoteError) as ctx:
            self.client.call(1, 5)  # add precisa de dois argumentos
        self.assertIn("TypeError", ctx.exception.detail)

    def test_echo_sem_argumentos(self) -> None:
        with self.assertRaises(RemoteError):
            self.client.call(5)

    def test_varias_chamadas_sequenciais(self) -> None:
        for value in range(20):
            self.assertEqual(self.client.add(value, value), 2 * value)

    def test_concorrencia_de_clientes(self) -> None:
        errors: list[BaseException] = []
        results: list[int] = []
        lock = threading.Lock()

        def worker(base: int) -> None:
            try:
                for offset in range(5):
                    value = base + offset
                    result = self.client.add(value, value)
                    with lock:
                        results.append(result)
            except BaseException as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i * 10,)) for i in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 100)
        self.assertEqual(set(results), {2 * (base + offset) for base in range(0, 200, 10) for offset in range(5)})

    def test_estatisticas_do_servidor(self) -> None:
        self.client.add(1, 1)
        self.client.add(2, 2)
        snapshot = self.server.stats.snapshot()
        self.assertGreaterEqual(snapshot["requests"], 2)
        self.assertGreaterEqual(snapshot["replies"], 2)

    def test_servidor_continua_apos_datagrama_invalido(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(b"isto nao e uma requisicao", self.server.address)
        self.assertTrue(wait_until(lambda: self.server.stats.malformed >= 1))
        self.assertEqual(self.client.add(1, 2), 3)

    def test_argumentos_malformados_geram_resposta_de_erro(self) -> None:
        datagram = message.encode_request(4242, 1, b"isto nao e json")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(2)
            sock.sendto(datagram, self.server.address)
            data, _ = sock.recvfrom(MAX_DATAGRAM)
        reply = message.decode_reply(data)
        self.assertEqual(reply.request_id, 4242)
        self.assertFalse(reply.ok)
        self.assertIn("ProtocolError", reply.error or "")

    def test_operacao_lenta_nao_bloqueia_as_demais(self) -> None:
        slow = threading.Thread(target=lambda: self.client.sleep(0.4), daemon=True)
        slow.start()
        try:
            self.assertEqual(self.client.add(1, 1), 2)
        finally:
            slow.join(5)


class TimeoutTest(unittest.TestCase):
    def test_timeout_do_cliente_e_recuperacao(self) -> None:
        server = start_server()
        self.addCleanup(server.stop)
        client = client_for(server, timeout=0.05, retries=0)

        with self.assertRaises(RequestReplyTimeout):
            client.sleep(0.4)

        # o servidor termina a operação lenta (a resposta se perdeu)
        self.assertTrue(wait_until(lambda: server.stats.snapshot()["replies"] >= 1))
        faster = client_for(server, timeout=1.0, retries=1)
        self.assertEqual(faster.add(2, 2), 4)


class AtMostOnceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ServiceRegistry()
        self.calls: list[int] = []

        def contar(value: int) -> int:
            self.calls.append(value)
            return value * 10

        self.registry.register(50, contar, "contar")
        self.server = start_server(registry=self.registry)
        self.addCleanup(self.server.stop)

    def test_retransmissao_nao_executa_a_operacao_duas_vezes(self) -> None:
        datagram = message.encode_request(777, 50, message.marshal([4]))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(2)
            sock.sendto(datagram, self.server.address)
            first, _ = sock.recvfrom(MAX_DATAGRAM)
            sock.sendto(datagram, self.server.address)  # mesma requisição
            second, _ = sock.recvfrom(MAX_DATAGRAM)

        self.assertEqual(self.calls, [4], "operação executada uma única vez")
        self.assertEqual(first, second, "duplicata recebe a resposta em cache")
        self.assertEqual(message.decode_reply(first).decode_result(), 40)
        self.assertTrue(wait_until(lambda: self.server.stats.snapshot()["duplicates"] >= 1))

    def test_request_id_diferente_executa_novamente(self) -> None:
        client = client_for(self.server, timeout=1.0, retries=0)
        self.assertEqual(client.call(50, 1), 10)
        self.assertEqual(client.call(50, 2), 20)
        self.assertEqual(self.calls, [1, 2])


class CustomRegistryTest(unittest.TestCase):
    def test_operacao_registrada_pelo_usuario(self) -> None:
        registry = ServiceRegistry()
        registry.register(42, lambda a, b: a * b + 1, "mult_mais_um")
        registry.register(43, lambda: {"ok": True}, "status")
        server = start_server(registry=registry)
        self.addCleanup(server.stop)
        client = client_for(server)

        self.assertEqual(client.call(42, 3, 4), 13)
        self.assertEqual(client.call(43), {"ok": True})
        self.assertEqual(registry.operations(), {42: "mult_mais_um", 43: "status"})
        self.assertIn(42, registry)
        self.assertEqual(len(registry), 2)

    def test_registro_rejeita_identificadores_invalidos(self) -> None:
        registry = ServiceRegistry()
        with self.assertRaises(ValueError):
            registry.register(0, lambda: None)
        with self.assertRaises(TypeError):
            registry.register(1, "nao chamavel")  # type: ignore[arg-type]


class LifecycleTest(unittest.TestCase):
    def test_start_e_stop(self) -> None:
        server = RPCServer("127.0.0.1", 0)
        self.assertIs(server.start(), server)
        self.addCleanup(server.stop)
        self.assertIsNone(server.error)
        self.assertGreater(server.port, 0)

    def test_nao_pode_iniciar_duas_vezes(self) -> None:
        server = start_server()
        self.addCleanup(server.stop)
        with self.assertRaises(RuntimeError):
            server.start()

    def test_stop_encerra_a_thread(self) -> None:
        server = start_server()
        server.stop()
        self.assertIsNone(server.error)

    def test_dois_servidores_isolados(self) -> None:
        first = start_server()
        second = start_server()
        self.addCleanup(first.stop)
        self.addCleanup(second.stop)
        self.assertNotEqual(first.port, second.port)
        self.assertEqual(client_for(first).add(1, 1), 2)
        self.assertEqual(client_for(second).add(10, 10), 20)

    def test_context_manager(self) -> None:
        with RPCServer("127.0.0.1", 0) as server:
            self.assertEqual(RPCClient(RemoteRef(*server.address)).add(1, 2), 3)


if __name__ == "__main__":
    unittest.main()
