"""Servidor do protocolo requisição-resposta.

O laço do servidor traduz literalmente o pseudocódigo dos slides::

    while True:
        (request, clientHost, clientPort) = getRequest()
        reply = execute(request)
        sendReply(reply, clientHost, clientPort)

Duas preocupações práticas foram adicionadas:

* **Concorrência**: cada requisição é executada em uma thread do pool, para
  que uma operação lenta não bloqueie as demais.
* **Semântica at-most-once**: o par (endereço do cliente, ``requestId``)
  identifica a requisição. Se o cliente retransmitir porque a resposta se
  perdeu, o servidor devolve a resposta guardada em cache em vez de executar
  a operação de novo.
"""

from __future__ import annotations

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import message
from .message import ProtocolError, Status
from .protocol import RequestReplyEndpoint
from .services import ServiceRegistry, build_default_registry


class ServerStats:
    """Contadores observáveis do servidor (úteis em testes e no demo)."""

    __slots__ = ("_lock", "requests", "replies", "errors", "duplicates", "malformed")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests = 0
        self.replies = 0
        self.errors = 0
        self.duplicates = 0
        self.malformed = 0

    def inc(self, field: str, amount: int = 1) -> None:
        with self._lock:
            setattr(self, field, getattr(self, field) + amount)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "requests": self.requests,
                "replies": self.replies,
                "errors": self.errors,
                "duplicates": self.duplicates,
                "malformed": self.malformed,
            }

    def __repr__(self) -> str:  # pragma: no cover - conveniência
        return f"ServerStats({self.snapshot()})"


class _Entry:
    """Estado de uma requisição no cache de deduplicação."""

    __slots__ = ("event", "datagram", "expires_at")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.datagram: bytes | None = None
        self.expires_at = float("inf")  # em processamento: nunca expira


class ReplyCache:
    """Cache de respostas por (cliente, ``requestId``) -- at-most-once.

    ``claim`` indica se a thread atual é dona da requisição. Quem não é dono
    espera a resposta ficar pronta (caso típico: retransmissão simultânea) e
    reaproveita o mesmo datagrama.
    """

    def __init__(
        self,
        ttl: float = 120.0,
        wait_timeout: float = 10.0,
        max_entries: int = 4096,
    ) -> None:
        self._ttl = ttl
        self._wait_timeout = wait_timeout
        self._max_entries = max_entries
        self._entries: dict[tuple[str, int, int], _Entry] = {}
        self._lock = threading.Lock()

    def claim(self, key: tuple[str, int, int]) -> tuple[bool, bytes | None]:
        """Retorna ``(sou_dono, resposta_pronta_ou_None)``."""
        with self._lock:
            self._prune_locked()
            entry = self._entries.get(key)
            if entry is None:
                entry = _Entry()
                self._entries[key] = entry
                return True, None

        if entry.event.wait(self._wait_timeout):
            return False, entry.datagram
        return False, None

    def store(self, key: tuple[str, int, int], datagram: bytes) -> None:
        """Publica a resposta e libera quem estiver esperando por ela."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:  # podado enquanto executava
                entry = _Entry()
                self._entries[key] = entry
            entry.datagram = datagram
            entry.expires_at = time.monotonic() + self._ttl
            entry.event.set()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def _prune_locked(self) -> None:
        if len(self._entries) < self._max_entries:
            return
        now = time.monotonic()
        expired = [
            key
            for key, entry in self._entries.items()
            if entry.datagram is not None and entry.expires_at <= now
        ]
        for key in expired:
            del self._entries[key]
        if len(self._entries) >= self._max_entries:
            # Ainda cheio: descarta as respostas mais antigas já concluídas.
            finished = sorted(
                (key for key, entry in self._entries.items() if entry.datagram is not None),
                key=lambda key: self._entries[key].expires_at,
            )
            for key in finished[: max(1, len(finished) // 4)]:
                del self._entries[key]


class RPCServer:
    """Servidor de invocação remota sobre UDP.

    ``port=0`` faz o sistema escolher uma porta livre; leia :attr:`address`
    para descobrir qual foi.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 5000,
        registry: ServiceRegistry | None = None,
        *,
        workers: int = 8,
        recv_timeout: float = 0.3,
        dedup_ttl: float = 120.0,
        max_inflight: int | None = None,
    ) -> None:
        if workers < 1:
            raise ValueError("workers deve ser >= 1")
        self.registry = registry if registry is not None else build_default_registry()
        self.stats = ServerStats()
        self._endpoint = RequestReplyEndpoint(host, port, recv_timeout=recv_timeout)
        self._address = self._endpoint.address
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None
        self._pool = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="rrp-worker"
        )
        self._inflight = threading.BoundedSemaphore(max_inflight or workers * 8)
        self._cache = ReplyCache(ttl=dedup_ttl)

    # -- ciclo de vida ---------------------------------------------------
    @property
    def address(self) -> tuple[str, int]:
        """Endereço efetivo em que o servidor está escutando."""
        return self._address

    @property
    def host(self) -> str:
        return self._address[0]

    @property
    def port(self) -> int:
        return self._address[1]

    @property
    def error(self) -> BaseException | None:
        """Exceção que derrubou o laço principal, se houver."""
        return self._error

    def start(self) -> "RPCServer":
        """Sobe o laço do servidor em uma thread e retorna imediatamente."""
        if self._thread is not None:
            raise RuntimeError("servidor já iniciado")
        self._thread = threading.Thread(
            target=self._run, name="rrp-server", daemon=True
        )
        self._thread.start()
        return self

    def serve_forever(self) -> None:
        """Executa o laço do servidor na thread atual (bloqueante)."""
        if self._thread is not None:
            raise RuntimeError("servidor já iniciado")
        self._run()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    incoming = self._endpoint.get_request()
                except (socket.timeout, ConnectionResetError):
                    # timeout: permite checar o shutdown.
                    # ConnectionResetError: o Windows sinaliza ICMP "porta
                    # inalcançável" quando o cliente desiste e fecha o socket.
                    continue
                except ProtocolError as exc:
                    self.stats.inc("malformed")
                    self._reply_malformed(exc)
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    raise

                if not self._inflight.acquire(blocking=False):
                    self.stats.inc("errors")
                    self._send_error(
                        incoming.client_address,
                        incoming.request.request_id,
                        "servidor sobrecarregado, tente novamente",
                    )
                    continue
                try:
                    self._pool.submit(self._process, incoming)
                except RuntimeError:  # pool encerrado durante o shutdown
                    self._inflight.release()
                    break
        except BaseException as exc:  # noqa: BLE001 - guardado para inspeção
            self._error = exc
            raise
        finally:
            self._stop.set()

    def stop(self) -> None:
        """Encerra o laço e as threads de trabalho."""
        self._stop.set()
        self._endpoint.close()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)
        self._pool.shutdown(wait=True)

    close = stop

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def __enter__(self) -> "RPCServer":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    # -- processamento ---------------------------------------------------
    def _process(self, incoming: Any) -> None:
        try:
            request = incoming.request
            key = (incoming.client_host, incoming.client_port, request.request_id)
            self.stats.inc("requests")

            is_owner, cached = self._cache.claim(key)
            if not is_owner:
                if cached is not None:
                    self.stats.inc("duplicates")
                    self._endpoint.send_reply(cached, incoming.client_host, incoming.client_port)
                # cached is None: outra thread ainda executa; o cliente
                # reenvia e receberá a resposta do cache.
                return

            datagram = self._execute(request)
            self._cache.store(key, datagram)
            self.stats.inc("replies")
            self._endpoint.send_reply(datagram, incoming.client_host, incoming.client_port)
        except OSError:
            # socket fechado durante o shutdown, ou cliente que já desistiu
            # (ICMP "porta inalcançável" no Windows)
            pass
        finally:
            self._inflight.release()

    def _execute(self, request: message.Request) -> bytes:
        """Executa a operação e devolve o datagrama de resposta."""
        try:
            arguments = request.decode_arguments()
            handler = self.registry.operation(request.operation_id)
            result = handler(*arguments)
        except Exception as exc:  # noqa: BLE001 - vira resposta de erro
            self.stats.inc("errors")
            return self._error_datagram(request.request_id, exc)
        try:
            return message.encode_reply(
                request.request_id, Status.OK, message.marshal([result])
            )
        except ProtocolError as exc:
            self.stats.inc("errors")
            return self._error_datagram(request.request_id, exc)

    def _error_datagram(self, request_id: int, exc: BaseException) -> bytes:
        detail = str(exc) or exc.__class__.__name__
        text = f"{type(exc).__name__}: {detail}"
        return message.encode_reply(request_id, Status.ERRO, text.encode("utf-8"))

    def _send_error(
        self, address: tuple[str, int], request_id: int, text: str
    ) -> None:
        try:
            datagram = message.encode_reply(
                request_id, Status.ERRO, text.encode("utf-8")
            )
            self._endpoint.send_reply(datagram, *address)
        except (OSError, ProtocolError):
            pass

    def _reply_malformed(self, exc: ProtocolError) -> None:
        """Responde erro quando dá para recuperar requestId e origem."""
        address = getattr(exc, "client_address", None)
        if exc.request_id is None or address is None:
            return
        self._send_error(address, exc.request_id, f"ProtocolError: {exc}")
