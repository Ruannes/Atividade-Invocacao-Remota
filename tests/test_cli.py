"""Testes da linha de comando (``python -m rrp ...``)."""

from __future__ import annotations

import contextlib
import io
import unittest

from rrp.__main__ import _parse_argument, main
from tests.support import free_udp_port, start_server


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = start_server()
        self.addCleanup(self.server.stop)
        self.port = str(self.server.port)

    def run_cli(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_client_executa_operacao(self) -> None:
        code, out, _err = self.run_cli(["client", "--port", self.port, "add", "2", "3"])
        self.assertEqual(code, 0)
        self.assertIn("5", out)

    def test_client_com_argumentos_json(self) -> None:
        code, out, _err = self.run_cli(
            ["client", "--port", self.port, "echo", '{"a": [1, 2]}']
        )
        self.assertEqual(code, 0)
        self.assertIn('"a"', out)

    def test_client_com_erro_remoto(self) -> None:
        code, _out, err = self.run_cli(["client", "--port", self.port, "divide", "1", "0"])
        self.assertEqual(code, 2)
        self.assertIn("ZeroDivisionError", err)

    def test_client_com_timeout(self) -> None:
        code, _out, err = self.run_cli(
            [
                "client",
                "--port",
                str(free_udp_port()),
                "--timeout",
                "0.05",
                "--retries",
                "0",
                "now",
            ]
        )
        self.assertEqual(code, 3)
        self.assertIn("timeout", err)

    def test_demo_local_de_ponta_a_ponta(self) -> None:
        code, out, _err = self.run_cli(["demo", "--local"])
        self.assertEqual(code, 0)
        self.assertIn("resultados corretos: True", out)
        self.assertIn("erro esperado", out)

    def test_operacao_inexistente_na_cli(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main(["client", "operacao_que_nao_existe"])


class ArgumentParserTest(unittest.TestCase):
    def test_converte_numeros_booleanos_e_json(self) -> None:
        self.assertEqual(_parse_argument("42"), 42)
        self.assertEqual(_parse_argument("-2.5"), -2.5)
        self.assertIs(_parse_argument("true"), True)
        self.assertIsNone(_parse_argument("null"))
        self.assertEqual(_parse_argument('{"a": 1}'), {"a": 1})
        self.assertEqual(_parse_argument("texto solto"), "texto solto")


if __name__ == "__main__":
    unittest.main()
