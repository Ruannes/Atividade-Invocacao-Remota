# Invocação Remota de Métodos — Protocolo Requisição-Resposta sobre UDP

Trabalho de Sistemas Distribuídos: implementação do protocolo de middleware
requisição-resposta (`doOperation`, `getRequest`, `sendReply`) sobre sockets
de datagrama (UDP), sem dependências externas.

## Requisitos

* Python 3.10 ou superior (testado com Python 3.14 no Windows 10).
* Nenhuma biblioteca externa: basta a biblioteca padrão do Python.
* Execute todos os comandos a partir da raiz do repositório.

```bash
python --version
```

## Como instanciar a aplicação

A aplicação tem dois papéis: **servidor** (fica escutando requisições) e
**cliente** (envia operações). Abra dois terminais na raiz do projeto.

### 1. Terminal 1 — servidor

```bash
python -m rrp server --host 0.0.0.0 --port 5000
```

Saída esperada:

```text
servidor escutando em udp://0.0.0.0:5000 (Ctrl+C para sair)
```

Use `--host 0.0.0.0` para aceitar chamadas de outras máquinas (necessário no
teste com um colega). No Windows, autorize o Python no firewall se for
solicitado. `--workers N` define quantas requisições são executadas em
paralelo (padrão: 8).

### 2. Terminal 2 — cliente

```bash
python -m rrp client add 2 3
# 5

python -m rrp client echo '{"texto": "ação", "lista": [1, 2.5, null]}'
# {"texto": "ação", "lista": [1, 2.5, null]}

python -m rrp client divide 1 0
# erro remoto: ZeroDivisionError: divisão por zero   (código de saída 2)
```

O cliente aceita `--host`, `--port`, `--timeout` (segundos por tentativa) e
`--retries` (retransmissões).

### 3. Demonstração em um único comando

Sobe um servidor temporário, executa todas as operações, mostra erros remotos
e faz 10 chamadas concorrentes:

```bash
python -m rrp demo --local
```

Sem `--local`, o demo chama um servidor já em execução:

```bash
python -m rrp demo --host 127.0.0.1 --port 5000
```

### Operações disponíveis

| Nome na CLI | Argumentos | Resultado |
| --- | --- | --- |
| `add` | 2 números | soma |
| `subtract` | 2 números | diferença |
| `multiply` | 2 números | produto |
| `divide` | 2 números | quociente (erro se divisor = 0) |
| `echo` | qualquer valor | o mesmo valor |
| `uppercase` | 1 texto | texto em maiúsculas |
| `now` | — | data/hora UTC do servidor |
| `system_info` | — | hostname, pid e versão do Python do servidor |
| `sleep` | segundos | dorme e devolve o valor (teste de timeout) |

Na CLI, os argumentos são convertidos automaticamente: `42` → inteiro,
`2.5` → decimal, `true`/`false`/`null` → booleanos/nulo, `{"a": 1}` → objeto
e qualquer outro texto vira string.

## Como rodar os testes

A suíte usa apenas `unittest` (biblioteca padrão) e tem **85 testes**:

```bash
python -m unittest discover -s tests -t . -v
```

Resultado esperado:

```text
Ran 85 tests in 1.7s

OK
```

Os testes abrem portas UDP efêmeras em `127.0.0.1`, portanto não conflitam
com o servidor da demonstração nem exigem rede externa.

| Arquivo | Testes | O que verifica |
| --- | --- | --- |
| `tests/test_message.py` | 25 | formato binário da mensagem, marshalling e datagramas inválidos |
| `tests/test_protocol.py` | 13 | `doOperation`, `getRequest`, `sendReply`, timeout e retransmissão |
| `tests/test_server.py` | 26 | operações, erros remotos, concorrência, duplicatas e ciclo de vida |
| `tests/test_interop.py` | 14 | interoperabilidade com uma implementação independente do protocolo |
| `tests/test_cli.py` | 7 | comandos `server`, `client` e `demo` |

Para testar a interoperabilidade com a implementação de um colega, há o
script `tools/interop_peer.py` (implementação independente, que atua como
cliente ou servidor); o formato do protocolo está em `docs/PROTOCOLO.md`.
