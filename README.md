# Invocação Remota de Métodos — Protocolo Requisição-Resposta sobre UDP

Trabalho da disciplina de **Sistemas Distribuídos** (Atividade de Sala — 60
minutos). Implementa o protocolo de middleware **requisição-resposta**
descrito nos slides de Invocação Remota de Métodos (Coulouris), com as
primitivas:

| Primitiva dos slides | Implementação |
| --- | --- |
| `doOperation(RemoteRef, operationId, arguments)` | `rrp.protocol.do_operation` / `RPCClient.call` |
| `getRequest()` | `rrp.protocol.RequestReplyEndpoint.get_request` |
| `sendReply(reply, clientHost, clientPort)` | `rrp.protocol.RequestReplyEndpoint.send_reply` |

Tudo sobre **sockets de datagrama (UDP)**, sem nenhuma dependência externa.

> **Entrega**: link do repositório (GitHub/GitLab) + este README com o
> descritivo de como instanciar a aplicação e os testes.

## Integrantes

| Nome | GitHub |
| --- | --- |
| _(preencha)_ | `@Ruannes` |

## O que a atividade pedia — e o que foi entregue

| Item do enunciado | Situação |
| --- | --- |
| "1) Implemente este protocolo usando Socket com Datagrama" | ✅ biblioteca `rrp/` + servidor e cliente executáveis |
| "2) Teste a sua implementação com a de um outro colega" | ✅ implementação independente em `tests/test_interop.py` e `tools/interop_peer.py` (cliente e servidor de referência) |
| README com como instanciar a aplicação e os testes | ✅ este arquivo |

## Requisitos

* **Python 3.10 ou superior** (testado com Python 3.14 no Windows 10).
* Nenhuma biblioteca externa — só a biblioteca padrão (`socket`, `struct`,
  `json`, `threading`, `unittest`).
* Nenhuma instalação necessária: os comandos abaixo rodam a partir da raiz
  do repositório.

Verifique a versão:

```bash
python --version
```

## Como instanciar a aplicação

O protocolo tem dois papéis: **servidor** (fica escutando) e **cliente**
(envia operações). Abra **dois terminais** na raiz do projeto.

### 1. Terminal 1 — subir o servidor

```bash
python -m rrp server --host 0.0.0.0 --port 5000
```

Saída esperada:

```text
servidor escutando em udp://0.0.0.0:5000 (Ctrl+C para sair)
```

> Use `--host 0.0.0.0` para aceitar conexões de outras máquinas (necessário
> no teste com o colega). No Windows, autorize o Python no firewall quando
> solicitado. `--workers N` controla quantas requisições são executadas em
> paralelo (padrão 8).

### 2. Terminal 2 — invocar operações

```bash
python -m rrp client add 2 3
# 5

python -m rrp client echo '{"texto": "ação", "lista": [1, 2.5, null]}'
# {"texto": "ação", "lista": [1, 2.5, null]}

python -m rrp client uppercase "sistemas distribuídos"
# SISTEMAS DISTRIBUÍDOS

python -m rrp client system_info
# {"hostname": "...", "pid": 1234, "python": "3.14.7", "platform": "..."}

python -m rrp client divide 1 0
# erro remoto: ZeroDivisionError: divisão por zero   (código de saída 2)
```

Opções úteis do cliente: `--host`, `--port`, `--timeout` (segundos por
tentativa) e `--retries` (retransmissões).

### 3. Demonstração em um único comando

Sobe um servidor temporário, executa todas as operações, mostra erros
remotos, prova a execução do outro lado e dispara 10 chamadas concorrentes:

```bash
python -m rrp demo --local
```

Saída (resumida):

```text
== operações aritméticas ==
add(2, 3)                              -> 5
divide(1, 0)  [erro no servidor]       -> erro esperado: ... ZeroDivisionError: divisão por zero
== marshalling (ida e volta) ==
echo({...})                            -> {'texto': 'ação', ...}
== provas de que a execução foi remota ==
system_info()                          -> {'hostname': '...', 'pid': ...}
== concorrência (10 chamadas em paralelo) ==
resultados corretos: True em 5.2 ms
estatísticas do servidor: {'requests': 20, 'replies': 20, 'errors': 2, ...}
```

Sem `--local`, o demo chama um servidor já em execução
(`python -m rrp demo --host 127.0.0.1 --port 5000`).

### 4. Usando como biblioteca

```python
from rrp import RPCClient, RPCServer

# servidor
server = RPCServer("0.0.0.0", 5000).start()

# cliente (em outro processo/máquina)
client = RPCClient.connect("127.0.0.1", 5000, timeout=1.0, retries=3)
print(client.add(2, 3))          # 5
print(client.echo({"ok": True})) # {'ok': True}

server.stop()
```

Para expor operações próprias, registre um `operationId` e a função:

```python
from rrp import RPCServer, ServiceRegistry

registry = ServiceRegistry()
registry.register(100, lambda a, b: a ** b, "potencia")
server = RPCServer("0.0.0.0", 5000, registry).start()

# cliente: client.call(100, 2, 10) -> 1024
```

### 5. Instalação opcional

Se quiser o comando `rrp` no PATH:

```bash
pip install -e .
rrp demo --local
```

## Operações disponíveis

| `operationId` | Nome na CLI | Argumentos | Resultado |
| --- | --- | --- | --- |
| 1 | `add` | 2 números | soma |
| 2 | `subtract` | 2 números | diferença |
| 3 | `multiply` | 2 números | produto |
| 4 | `divide` | 2 números | quociente (erro se divisor = 0) |
| 5 | `echo` | qualquer valor | o mesmo valor |
| 6 | `uppercase` | 1 texto | texto em maiúsculas |
| 7 | `now` | — | data/hora UTC do servidor |
| 8 | `system_info` | — | hostname, pid, versão do Python |
| 9 | `sleep` | segundos | dorme e devolve o valor (teste de timeout) |

Na CLI, argumentos são convertidos automaticamente: `42` → inteiro,
`2.5` → decimal, `true`/`false`/`null` → booleanos/nulo, `{"a": 1}` → objeto,
e qualquer outra coisa vira texto.

## Como rodar os testes

A suíte usa apenas `unittest` (biblioteca padrão), com **85 testes**:

```bash
python -m unittest discover -s tests -t . -v
```

Resultado esperado:

```text
Ran 85 tests in 1.7s

OK
```

Também funciona com `pytest`, se preferir:

```bash
python -m pytest -q
```

### O que cada arquivo cobre

| Arquivo | Testes | Foco |
| --- | --- | --- |
| `tests/test_message.py` | 25 | layout binário (13/10 bytes, big-endian), marshalling JSON, datagramas truncados/corrompidos, limites |
| `tests/test_protocol.py` | 13 | `doOperation`/`getRequest`/`sendReply` crus, timeout, retransmissão com o mesmo `requestId`, descarte de respostas estranhas |
| `tests/test_server.py` | 26 | todas as operações, erros remotos, concorrência, at-most-once, servidor sobrevive a lixo, ciclo de vida |
| `tests/test_interop.py` | 14 | **interoperabilidade**: cliente independente ↔ nosso servidor e nosso cliente ↔ servidor independente |
| `tests/test_cli.py` | 7 | comandos `python -m rrp client/server/demo` e conversão de argumentos |

Os testes abrem portas UDP efêmeras em `127.0.0.1`, então não conflitam com
o servidor da demonstração nem exigem rede externa.

## Teste com a implementação de um colega

O protocolo está documentado em [`docs/PROTOCOLO.md`](docs/PROTOCOLO.md)
(formato dos datagramas, marshalling, semântica). Com esse documento, o
colega implementa em Java/Node/etc. e há três formas de validar:

### Opção A — script pronto (recomendado)

`tools/interop_peer.py` é uma **segunda implementação independente** do
protocolo (não importa o pacote `rrp`). Ela serve tanto de cliente quanto de
servidor:

```bash
# 1) Você testa o servidor do colega
python tools/interop_peer.py client --host <IP_DO_COLEGA> --port 5000 --op 1 --args 2 3
# -> status=OK payload=[5]

# 2) O colega testa o seu servidor
python -m rrp server --host 0.0.0.0 --port 5000
python tools/interop_peer.py client --host <SEU_IP> --port 5000 --op 5 --args '"oi"'

# 3) Você vira servidor de referência para o cliente do colega
python tools/interop_peer.py server --port 5001
```

O cliente do script imprime o datagrama em hexadecimal, o `status` e o
payload recebido — dá para comparar byte a byte com a implementação do
colega. Use `--repeat 2` para reenviar a mesma requisição e observar a
semântica at-most-once (respostas idênticas, operação executada uma vez).

### Opção B — suíte automatizada

`tests/test_interop.py` embute uma implementação de referência e verifica as
duas direções, incluindo erro remoto, acentos (UTF-8) e duplicatas.

### Opção C — teste manual

Envie os bytes do exemplo do documento e confira a resposta:

```text
requisição: 00 00 00 00 01 00 00 00 01 00 00 00 05 5b 32 2c 33 5d   (add 2 3)
resposta:   01 00 00 00 01 00 00 00 00 03 5b 35 5d           (5)
```

## Estrutura do repositório

```text
.
├── README.md                  # este arquivo
├── pyproject.toml             # metadados, comando `rrp` e config do pytest
├── docs/
│   ├── PROTOCOLO.md           # especificação do protocolo (para o colega)
│   └── enunciado/
│       └── invocacao-remota.pdf   # slides da aula
├── rrp/
│   ├── __init__.py            # API pública
│   ├── __main__.py            # CLI: server | client | demo
│   ├── message.py             # formato do datagrama e marshalling
│   ├── protocol.py            # doOperation, getRequest, sendReply
│   ├── server.py              # laço do servidor, pool de threads, cache at-most-once
│   ├── client.py              # proxy do cliente (RPCClient)
│   ├── services.py            # registro de operações + serviço de demonstração
│   └── operations.py          # operationIds
├── tools/
│   └── interop_peer.py        # implementação independente (cliente/servidor)
└── tests/
    ├── support.py
    ├── test_message.py
    ├── test_protocol.py
    ├── test_server.py
    ├── test_interop.py
    └── test_cli.py
```

## Mapeamento com os slides

| Conceito da aula | Onde está no código |
| --- | --- |
| Protocolo = regras de formatação, empacotamento e desempacotamento | `rrp/message.py` (cabeçalhos + JSON/UTF-8) |
| Endereçamento / numeração e sequência | `RemoteRef` (`host:porta`) e `requestId` por requisição |
| Confirmação de recepção, controle de erro e retransmissão | timeout + retransmissão em `do_operation`, status `OK/ERRO` |
| Conversão de código | marshalling/unmarshalling JSON ↔ objetos da linguagem |
| Controle de fluxo | timeout de recepção, pool limitado, recusa com erro quando sobrecarregado |
| Protocolo Sockets (UDP) | `socket.SOCK_DGRAM`, `sendto`/`recvfrom` |
| Requisição-Resposta (`doOperation`, `getRequest`, `sendReply`) | `rrp/protocol.py` |
| "A mensagem" | `docs/PROTOCOLO.md`, seção 2 |

## Decisões de projeto

* **UDP puro**, como pede o enunciado; a confiabilidade vem do protocolo
  (timeout, retransmissão e cache de respostas), não do transporte.
* **`operationId` inteiro** em vez de nome de método: menos bytes, sem
  ambiguidade e independente da linguagem do colega.
* **Argumentos como lista JSON UTF-8**: simples de implementar em qualquer
  linguagem e suficiente para os tipos usados em aula.
* **At-most-once** por `(cliente, requestId)`: retransmissão nunca executa a
  operação duas vezes.
* **Pool de threads** no servidor: uma operação lenta (`sleep`) não bloqueia
  os outros clientes — há teste automatizado para isso.
* **Sem dependências**: `pip install` não é necessário para rodar nem testar.

## Limitações

* não há autenticação/autorização nem criptografia;
* o cache at-most-once é em memória (some se o servidor reiniciar);
* datagramas acima de ~1.400 bytes podem ser fragmentados no IP e se perder
  com mais facilidade — o protocolo não implementa fragmentação própria;
* a identificação do cliente usa IP e porta de origem, então um cliente que
  troca de porta a cada chamada perde a proteção contra duplicatas.

## Referências

* Slides **Sistemas Distribuídos — Invocação Remota de Métodos**
  (`docs/enunciado/invocacao-remota.pdf`).
* COULOURIS, G.; DOLLIMORE, J.; KINDBERG, T.; BLAIR, G.
  *Distributed Systems: Concepts and Design*. 5. ed. Addison-Wesley.
* Documentação Python: [`socket`](https://docs.python.org/3/library/socket.html),
  [`struct`](https://docs.python.org/3/library/struct.html),
  [`json`](https://docs.python.org/3/library/json.html).
