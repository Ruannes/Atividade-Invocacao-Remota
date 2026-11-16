# Protocolo Requisição-Resposta sobre UDP

Especificação do protocolo implementado neste repositório. Qualquer
implementação que siga estas regras (Java, Python, Node, C, ...) conversa com
este código — é exatamente o que a segunda parte da atividade pede: *"teste a
sua implementação com a de um outro colega"*.

## 1. Transporte

| Item | Valor |
| --- | --- |
| Protocolo de transporte | UDP (`SOCK_DGRAM`) |
| Primitivas usadas | `bind`, `sendto` / `recvfrom`, `close` |
| Ordem dos bytes | big-endian (network byte order, `!` no `struct`) |
| Porta padrão do servidor | 5000 |
| Datagrama máximo aceito | 65.507 bytes (limite do UDP) |
| Payload máximo aceito | 65.536 bytes (proteção da implementação) |

Como o UDP não garante entrega, o protocolo implementa timeout,
retransmissão e detecção de duplicatas (seção 5).

## 2. Mensagens

O protocolo tem dois tipos de mensagem: **requisição** (`tipo = 0`) e
**resposta** (`tipo = 1`).

### 2.1 Requisição

| Offset | Bytes | Campo | Tipo | Descrição |
| --- | --- | --- | --- | --- |
| 0 | 1 | `tipo` | uint8 | sempre `0` (REQUISIÇÃO) |
| 1 | 4 | `requestId` | int32 | identifica a requisição (1 .. 2³¹−1) |
| 5 | 4 | `operationId` | int32 | identifica a operação (1 .. 2³¹−1) |
| 9 | 4 | `tamanho` | uint32 | tamanho de `argumentos` em bytes |
| 13 | `n` | `argumentos` | bytes | lista JSON em UTF-8 |

Cabeçalho: **13 bytes**.

### 2.2 Resposta

| Offset | Bytes | Campo | Tipo | Descrição |
| --- | --- | --- | --- | --- |
| 0 | 1 | `tipo` | uint8 | sempre `1` (RESPOSTA) |
| 1 | 4 | `requestId` | int32 | o mesmo `requestId` da requisição |
| 5 | 1 | `status` | uint8 | `0` = OK, `1` = ERRO |
| 6 | 4 | `tamanho` | uint32 | tamanho de `conteúdo` em bytes |
| 10 | `n` | `conteúdo` | bytes | resultado JSON (`status=0`) ou texto do erro em UTF-8 (`status=1`) |

Cabeçalho: **10 bytes**.

### 2.3 Exemplo real — `add(2, 3)`

Requisição (18 bytes), com `requestId = 1` e `operationId = 1`:

```
00 | 00 00 00 01 | 00 00 00 01 | 00 00 00 05 | 5b 32 2c 33 5d
tipo  requestId     operationId   tamanho       "[2,3]"
```

Resposta (13 bytes):

```
01 | 00 00 00 01 | 00 | 00 00 00 03 | 5b 35 5d
tipo  requestId    OK    tamanho       "[5]"
```

## 3. Representação dos argumentos (marshalling)

Argumentos e resultado são **uma lista JSON em UTF-8**. Isso permite
transportar `int`, `float`, `bool`, `null`, `string`, listas e objetos sem
depender de classes da linguagem.

| Linguagem | Empacotar | Desempacotar |
| --- | --- | --- |
| Python | `json.dumps(lista, ensure_ascii=False, separators=(",", ":"))` | `json.loads(texto)` |
| Java | `new JSONArray(...).toString().getBytes(UTF_8)` | `new JSONArray(texto)` |
| Node | `Buffer.from(JSON.stringify(lista), "utf8")` | `JSON.parse(texto)` |

Regras:

* o payload **deve** ser uma lista JSON (mesmo com zero ou um argumento);
* lista vazia (`[]`) é permitida;
* `NaN`/`Infinity` são recusados (não são JSON válido);
* se `status = 1`, o conteúdo é o texto do erro em UTF-8, **não** JSON.

## 4. Operações do serviço de demonstração

O `operationId` é um inteiro fixo — "a identidade da operação (melhor que o
nome)", como nos slides.

| ID | Nome | Argumentos | Resultado |
| --- | --- | --- | --- |
| 1 | `add` | 2 números | soma |
| 2 | `subtract` | 2 números | diferença |
| 3 | `multiply` | 2 números | produto |
| 4 | `divide` | 2 números | quociente (erro se divisor = 0) |
| 5 | `echo` | 1 valor qualquer | o mesmo valor |
| 6 | `uppercase` | 1 string | string em maiúsculas |
| 7 | `now` | nenhum | data/hora UTC ISO-8601 do servidor |
| 8 | `system_info` | nenhum | hostname, pid, versão do Python, plataforma |
| 9 | `sleep` | segundos | dorme até 30 s e devolve o valor |

Operações novas podem ser registradas com qualquer inteiro
(`ServiceRegistry.register`), sem alterar o protocolo.

## 5. Semântica

### 5.1 `doOperation` (cliente)

1. gera um `requestId` novo;
2. empacota os argumentos e envia o datagrama;
3. bloqueia esperando resposta;
4. se nada chegar em `timeout` (padrão 1 s), **retransmite a mesma
   mensagem**, com o **mesmo `requestId`**, até `retries` vezes (padrão 3);
5. descarta datagramas inválidos ou com `requestId` diferente (podem ser
   respostas antigas);
6. devolve a resposta ou levanta `RequestReplyTimeout`.

### 5.2 `getRequest` / `sendReply` (servidor)

```text
enquanto verdadeiro:
    (requisição, hostCliente, portaCliente) = getRequest()   # bloqueia
    resposta = executar(requisição)
    sendReply(resposta, hostCliente, portaCliente)           # encerra o ciclo
```

A execução ocorre em um pool de threads, então uma operação lenta não
bloqueia as outras. Se o servidor estiver sobrecarregado, responde
`status = 1` em vez de descartar silenciosamente.

### 5.3 Entrega no máximo uma vez (at-most-once)

A chave do cache é `(IP do cliente, porta do cliente, requestId)`.

* primeira chegada: executa e guarda o datagrama de resposta;
* retransmissão (mesma chave): devolve a resposta guardada **sem executar**
  a operação de novo;
* uma retransmissão que chega enquanto a original executa espera a resposta
  ficar pronta e reaproveita o mesmo datagrama;
* respostas ficam no cache por 120 s (configurável).

Consequência: operações com efeito colateral não são repetidas por causa de
uma resposta perdida. Um cliente que queira executar a operação duas vezes
deve usar um `requestId` novo.

### 5.4 Mensagens corrompidas ou mal formadas

| Situação | Comportamento |
| --- | --- |
| menos de 13/10 bytes, tipo desconhecido | datagrama descartado |
| `tamanho` declarado ≠ tamanho recebido, mas cabeçalho legível | resposta `status = 1` com o `requestId` recuperado |
| `status` de resposta desconhecido | datagrama descartado pelo cliente |
| argumentos que não são JSON / não são lista | resposta `status = 1` |
| argumentos incompatíveis com a operação | resposta `status = 1` (ex.: `TypeError`) |
| exceção dentro da operação | resposta `status = 1` com `Tipo: mensagem` |

## 6. Como validar a interoperabilidade com o colega

Há três caminhos, do mais rápido ao mais completo.

### 6.1 Script pronto (`tools/interop_peer.py`)

É uma segunda implementação, independente do pacote `rrp` (só `struct` +
`json`), servindo de cliente **e** de servidor:

```bash
# você como cliente do servidor do colega
python tools/interop_peer.py client --host <IP_DO_COLEGA> --port 5000 --op 1 --args 2 3

# você como servidor para o colega testar
python tools/interop_peer.py server --port 5001
```

Saída esperada do cliente: `status=OK payload=[5]`.

### 6.2 Suíte automatizada (`tests/test_interop.py`)

Contém uma implementação de referência embutida e prova as duas direções:

* cliente independente → servidor deste repositório;
* cliente deste repositório → servidor independente.

### 6.3 Teste manual com `netcat` ou `nc`

Envie os bytes do exemplo da seção 2.3 e verifique a resposta:

```text
00 00 00 00 01 00 00 00 01 00 00 00 05 5b 32 2c 33 5d
```

## 7. Limitações conhecidas

* não há autenticação, autorização nem criptografia (UDP em texto claro);
* a semântica é at-most-once apenas para o par cliente/`requestId`; se o
  processo do servidor reiniciar, o cache se perde;
* payloads maiores que o MTU podem ser fragmentados no IP e perdidos com
  mais facilidade — o protocolo não implementa fragmentação própria;
* um cliente que muda de porta a cada chamada perde a proteção contra
  duplicatas (a chave inclui a porta de origem).
