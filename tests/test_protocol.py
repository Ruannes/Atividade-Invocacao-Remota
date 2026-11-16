"""Testes das primitivas doOperation / getRequest / sendReply sobre UDP."""

from __future__ import annotations

import contextlib
import socket
import threading
import time
import unittest
from typing import Callable, Iterator

from rrp import message
from rrp.message import MAX_DATAGRAM, ProtocolError, Status
from rrp.protocol import (
    IncomingRequest,
    RemoteRef,
    RequestReplyEndpoint,
    RequestReplyTimeout,
    do_operation,
)
from tests.support import free_udp_port

Handler = Callable[[RequestReplyEndpoint, IncomingRequest], None]


@contextlib.contextmanager
def udp_peer(
    handler: Handler, *, ignore_first: int = 0
) -> Iterator[tuple[tuple[str, int], list, list]]:
    """Sobe um extremo servidor descartável e devolve (endereço, requisições, erros)."""
    endpoint = RequestReplyEndpoint("127.0.0.1", 0, recv_timeout=0.2)
    received: list[message.Request] = []
    errors: list[BaseException] = []
    stop = threading.Event()

    def run() -> None:
        while not stop.is_set():
            try:
                incoming = endpoint.get_request()
            except socket.timeout:
                continue
            except OSError:
                break
            received.append(incoming.request)
            if len(received) <= ignore_first:
                continue
            try:
                handler(endpoint, incoming)
            except OSError:
                break
            except BaseException as exc:  # noqa: BLE001 - reportado no teste
                errors.append(exc)
                break

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        yield endpoint.address, received, errors
    finally:
        stop.set()
        endpoint.close()
        thread.join(5)
        assert not thread.is_alive()


def echo_handler(endpoint: RequestReplyEndpoint, incoming: IncomingRequest) -> None:
    """Devolve os argumentos recebidos (servidor mínimo de eco)."""
    endpoint.send_reply(
        message.encode_reply(
            incoming.request.request_id, Status.OK, incoming.request.arguments
        ),
        incoming.client_host,
        incoming.client_port,
    )


class GetRequestAndSendReplyTest(unittest.TestCase):
    def test_ciclo_completo_com_socket_cru(self) -> None:
        with udp_peer(echo_handler) as (address, received, errors):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(2)
                sock.sendto(message.encode_request(77, 5, message.marshal(["oi"])), address)
                data, _ = sock.recvfrom(MAX_DATAGRAM)

        self.assertEqual(errors, [])
        self.assertEqual(len(received), 1)
        reply = message.decode_reply(data)
        self.assertEqual(reply.request_id, 77)
        self.assertTrue(reply.ok)
        self.assertEqual(reply.decode_result(), "oi")

    def test_get_request_expoe_endereco_do_cliente(self) -> None:
        origem: list[tuple[str, int]] = []

        def handler(endpoint: RequestReplyEndpoint, incoming: IncomingRequest) -> None:
            origem.append(incoming.client_address)
            endpoint.send_reply(
                message.Reply(incoming.request.request_id), *incoming.client_address
            )

        with udp_peer(handler) as (address, _received, errors):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(2)
                sock.bind(("127.0.0.1", 0))
                local = sock.getsockname()
                sock.sendto(message.encode_request(1, 5), address)
                sock.recvfrom(MAX_DATAGRAM)

            self.assertEqual(origem, [local])
        self.assertEqual(errors, [])

    def test_endpoint_com_timeout_de_recepcao(self) -> None:
        with RequestReplyEndpoint("127.0.0.1", 0, recv_timeout=0.05) as endpoint:
            with self.assertRaises(socket.timeout):
                endpoint.get_request()

    def test_datagrama_malformado_levanta_protocolo_error_com_origem(self) -> None:
        with RequestReplyEndpoint("127.0.0.1", 0, recv_timeout=2) as endpoint:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.sendto(b"\x01\x02", endpoint.address)
                with self.assertRaises(ProtocolError) as ctx:
                    endpoint.get_request()
        self.assertIsNone(ctx.exception.request_id)
        self.assertIsNotNone(getattr(ctx.exception, "client_address", None))


class DoOperationTest(unittest.TestCase):
    def test_roundtrip_com_servidor_de_eco(self) -> None:
        with udp_peer(echo_handler) as (address, received, errors):
            reply = do_operation(RemoteRef(*address), 5, message.marshal([{"a": [1, 2]}]))

        self.assertEqual(errors, [])
        self.assertEqual(reply.decode_result(), {"a": [1, 2]})
        self.assertEqual(received[0].operation_id, 5)

    def test_request_id_explicito_e_preservado(self) -> None:
        with udp_peer(echo_handler) as (address, received, _errors):
            reply = do_operation(RemoteRef(*address), 5, message.marshal([1]), request_id=1234)

        self.assertEqual(reply.request_id, 1234)
        self.assertEqual([request.request_id for request in received], [1234])

    def test_timeout_quando_ninguem_responde(self) -> None:
        # extremidade UDP que recebe os datagramas e nunca responde
        with RequestReplyEndpoint("127.0.0.1", 0) as silent:
            started = time.monotonic()
            with self.assertRaises(RequestReplyTimeout):
                do_operation(RemoteRef(*silent.address), 1, message.marshal([1, 2]),
                             timeout=0.1, retries=1)
            elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 0.15, "deve aguardar timeout + retransmissão")
        self.assertLess(elapsed, 3.0)

    def test_porta_fechada_tambem_resulta_em_timeout(self) -> None:
        remote = RemoteRef("127.0.0.1", free_udp_port())
        with self.assertRaises(RequestReplyTimeout):
            do_operation(remote, 1, message.marshal([1, 2]), timeout=0.2, retries=1)

    def test_retransmite_mantendo_o_mesmo_request_id(self) -> None:
        with udp_peer(echo_handler, ignore_first=2) as (address, received, errors):
            reply = do_operation(
                RemoteRef(*address), 1, message.marshal([2, 3]), timeout=0.2, retries=3
            )

        self.assertEqual(errors, [])
        self.assertEqual(message.unmarshal(reply.payload), [2, 3])
        self.assertEqual(len(received), 3, "duas perdas -> três transmissões")
        self.assertEqual(len({request.request_id for request in received}), 1)
        self.assertEqual([request.operation_id for request in received], [1, 1, 1])

    def test_ignora_datagramas_invalidos_e_de_outra_requisicao(self) -> None:
        def handler(endpoint: RequestReplyEndpoint, incoming: IncomingRequest) -> None:
            address = incoming.client_address
            endpoint.send_reply(b"lixo", *address)  # não é uma resposta válida
            endpoint.send_reply(
                message.encode_reply(incoming.request.request_id + 1, Status.OK, b"[0]"),
                *address,
            )
            endpoint.send_reply(
                message.encode_reply(
                    incoming.request.request_id, Status.OK, message.marshal(["certa"])
                ),
                *address,
            )

        with udp_peer(handler) as (address, _received, errors):
            reply = do_operation(RemoteRef(*address), 5, b"[]", timeout=1, retries=0)

        self.assertEqual(errors, [])
        self.assertEqual(reply.decode_result(), "certa")

    def test_resposta_de_erro_chega_ao_cliente(self) -> None:
        def handler(endpoint: RequestReplyEndpoint, incoming: IncomingRequest) -> None:
            endpoint.send_reply(
                message.encode_reply(
                    incoming.request.request_id, Status.ERRO, "falhou".encode("utf-8")
                ),
                *incoming.client_address,
            )

        with udp_peer(handler) as (address, _received, _errors):
            reply = do_operation(RemoteRef(*address), 1, b"[]", timeout=1, retries=0)

        self.assertFalse(reply.ok)
        self.assertEqual(reply.error, "falhou")

    def test_validacao_de_parametros(self) -> None:
        remote = RemoteRef("127.0.0.1", 9)
        with self.assertRaises(ValueError):
            do_operation(remote, 1, b"[]", timeout=0)
        with self.assertRaises(ValueError):
            do_operation(remote, 1, b"[]", retries=-1)

    def test_remote_ref_valida_host_e_porta(self) -> None:
        with self.assertRaises(ValueError):
            RemoteRef("", 5000)
        with self.assertRaises(ValueError):
            RemoteRef("127.0.0.1", 70000)


if __name__ == "__main__":
    unittest.main()
