"""Testes do formato da mensagem e do marshalling (vetor de bytes)."""

from __future__ import annotations

import struct
import unittest

from rrp import message
from rrp.message import (
    MAX_PAYLOAD,
    MessageType,
    ProtocolError,
    Reply,
    Status,
)


class HeaderLayoutTest(unittest.TestCase):
    """O layout binário precisa ser exatamente o documentado no README."""

    def test_request_header_is_13_bytes_big_endian(self) -> None:
        datagram = message.encode_request(7, 1, b"[]")
        self.assertEqual(len(datagram), 13 + 2)
        msg_type, request_id, operation_id, length = struct.unpack("!BiiI", datagram[:13])
        self.assertEqual((msg_type, request_id, operation_id, length), (0, 7, 1, 2))
        self.assertEqual(datagram[13:], b"[]")

    def test_reply_header_is_10_bytes_big_endian(self) -> None:
        datagram = message.encode_reply(9, Status.OK, b"[5]")
        self.assertEqual(len(datagram), 10 + 3)
        msg_type, request_id, status, length = struct.unpack("!BiBI", datagram[:10])
        self.assertEqual((msg_type, request_id, status, length), (1, 9, 0, 3))
        self.assertEqual(datagram[10:], b"[5]")


class MarshalTest(unittest.TestCase):
    def test_roundtrip_of_supported_types(self) -> None:
        values = [42, -1, 2.5, True, False, None, "ação", [1, [2, 3]], {"k": "v"}]
        self.assertEqual(message.unmarshal(message.marshal(values)), values)

    def test_empty_payload_means_no_arguments(self) -> None:
        self.assertEqual(message.marshal([]), b"[]")
        self.assertEqual(message.unmarshal(b""), [])

    def test_unserializable_value_is_rejected(self) -> None:
        with self.assertRaises(ProtocolError):
            message.marshal([{1, 2, 3}])

    def test_nan_is_rejected(self) -> None:
        with self.assertRaises(ProtocolError):
            message.marshal([float("nan")])

    def test_invalid_json_payload(self) -> None:
        with self.assertRaises(ProtocolError):
            message.unmarshal(b"nao e json")

    def test_json_that_is_not_a_list(self) -> None:
        with self.assertRaises(ProtocolError):
            message.unmarshal(b'{"operacao": 1}')

    def test_invalid_utf8_payload(self) -> None:
        with self.assertRaises(ProtocolError):
            message.unmarshal(b"\xff\xfe\x00")


class RequestCodecTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        arguments = message.marshal([2, 3])
        request = message.decode_request(message.encode_request(11, 4, arguments))
        self.assertEqual(request.request_id, 11)
        self.assertEqual(request.operation_id, 4)
        self.assertEqual(request.decode_arguments(), [2, 3])

    def test_empty_arguments(self) -> None:
        request = message.decode_request(message.encode_request(1, 7))
        self.assertEqual(request.arguments, b"")
        self.assertEqual(request.decode_arguments(), [])

    def test_truncated_datagram(self) -> None:
        with self.assertRaises(ProtocolError) as ctx:
            message.decode_request(b"\x00\x00")
        self.assertIsNone(ctx.exception.request_id)

    def test_wrong_message_type(self) -> None:
        reply = message.encode_reply(1, Status.OK, b"[]")
        with self.assertRaises(ProtocolError):
            message.decode_request(reply)

    def test_declared_length_mismatch(self) -> None:
        datagram = message.encode_request(5, 1, b"[]")
        with self.assertRaises(ProtocolError) as ctx:
            message.decode_request(datagram + b"extra")
        # o cabeçalho foi lido: o servidor consegue responder com erro
        self.assertEqual(ctx.exception.request_id, 5)

    def test_length_beyond_limit(self) -> None:
        header = struct.pack("!BiiI", 0, 3, 1, MAX_PAYLOAD + 1)
        with self.assertRaises(ProtocolError) as ctx:
            message.decode_request(header)
        self.assertEqual(ctx.exception.request_id, 3)

    def test_encode_rejects_oversized_payload(self) -> None:
        with self.assertRaises(ProtocolError):
            message.encode_request(1, 1, b"x" * (MAX_PAYLOAD + 1))

    def test_encode_rejects_invalid_identifiers(self) -> None:
        for request_id, operation_id in [(-1, 1), (0, 1), (2**31, 1), (1, -5), (1, 2**31)]:
            with self.subTest(request_id=request_id, operation_id=operation_id):
                with self.assertRaises(ProtocolError):
                    message.encode_request(request_id, operation_id)


class ReplyCodecTest(unittest.TestCase):
    def test_roundtrip_ok(self) -> None:
        reply = message.decode_reply(
            message.encode_reply(3, Status.OK, message.marshal([2.5]))
        )
        self.assertEqual(reply.request_id, 3)
        self.assertTrue(reply.ok)
        self.assertIsNone(reply.error)
        self.assertEqual(reply.decode_result(), 2.5)

    def test_roundtrip_error(self) -> None:
        reply = message.decode_reply(
            message.encode_reply(4, Status.ERRO, "divisão por zero".encode("utf-8"))
        )
        self.assertFalse(reply.ok)
        self.assertEqual(reply.status, Status.ERRO)
        self.assertEqual(reply.error, "divisão por zero")
        with self.assertRaises(ProtocolError):
            reply.decode_result()

    def test_empty_ok_reply_has_no_result(self) -> None:
        reply = Reply(request_id=1)
        self.assertIsNone(reply.decode_result())

    def test_unknown_status(self) -> None:
        datagram = struct.pack("!BiBI", 1, 2, 99, 0)
        with self.assertRaises(ProtocolError) as ctx:
            message.decode_reply(datagram)
        self.assertEqual(ctx.exception.request_id, 2)

    def test_truncated_reply(self) -> None:
        with self.assertRaises(ProtocolError):
            message.decode_reply(b"\x01\x00")

    def test_reply_declared_length_mismatch(self) -> None:
        datagram = message.encode_reply(6, Status.OK, b"[]")
        with self.assertRaises(ProtocolError):
            message.decode_reply(datagram[:-1])


class RequestIdTest(unittest.TestCase):
    def test_request_ids_are_positive_and_unique(self) -> None:
        ids = [message.next_request_id() for _ in range(1000)]
        self.assertTrue(all(0 < value < 2**31 for value in ids))
        self.assertEqual(len(set(ids)), len(ids))

    def test_message_type_enum_matches_wire_values(self) -> None:
        self.assertEqual(int(MessageType.REQUISICAO), 0)
        self.assertEqual(int(MessageType.RESPOSTA), 1)


if __name__ == "__main__":
    unittest.main()
