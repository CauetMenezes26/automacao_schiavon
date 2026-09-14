# RPA Schiavon

Pipeline que baixa invoices de fornecedores do SharePoint, lê os dados com o Claude Vision,
mantém a cotação semanal de carnes com os fornecedores e concilia o preço cotado contra o
preço faturado. Tudo persiste no PostgreSQL (Azure).

## O pipeline

Uma execução de `python main.py` faz **uma passada** e termina, nesta ordem:

```
FLUXO 1/4   Sincroniza sinonimos   files/sinonimos/*.xlsx -> dim_item_sinonimo
FLUXO 2/4   Coleta de invoices     SharePoint -> Claude Vision -> fat_invoice / fat_invoice_item
FLUXO 3/4   Cotacao semanal        avanca UM passo do ciclo -> fat_cotacao_preco
FLUXO 4/4   Conciliacao            fat_cotacao_preco x fat_invoice_item -> fat_conciliacao/_item

FLUXO 5/5   Conciliacao com ERP    proxima versao (CONCILIAR_ERP_ATIVO = False em main.py)
```

**Não há flags.** `python main.py` sempre roda os quatro fluxos, em ordem. O `main.py` só
monta o `Pipeline` (`utils/pipeline.py`) e chama uma fachada por fluxo. O recorte por fluxo
e os atalhos da cotação saíram: quem precisa de um passo isolado chama o módulo direto
(`python -m cotacao.quotation`, `python -m manutencao.*`).

Cada fluxo roda isolado no `Pipeline`: se um falhar, os outros seguem, o RESUMO no fim lista
OK/ERRO e o processo sai com código 1, para o cron alertar. A ordem está no `main.py` e é
para ser lida de cima a baixo.

O FLUXO 4 lê do banco, não do que a coleta acabou de trazer — então concilia mesmo quando o
SharePoint falhou, só com dados um pouco mais velhos. O FLUXO 1 sincroniza a planilha DE-PARA
de sinônimos; se ela estiver aberta no Excel, é pulada e retomada na próxima execução.

---

## Comandos disponíveis

### Pipeline

| Comando | O que faz |
|---|---|
| `python main.py` | Roda os quatro fluxos, em ordem. Sem flags. |

Ajustes que antes eram flags viraram constantes no topo da fachada:
`coleta_invoices/coleta.py::MODO_SINCRONO` (Batch API x imediato) e
`main.py::CONCILIAR_ERP_ATIVO`. Período/fornecedor do FLUXO 4 usam o padrão
(semana da data de hoje).

### Cotação — passos avulsos

Chamados direto no módulo, fora do `main.py`:

| Comando | O que faz |
|---|---|
| `python -m cotacao.cotacao` | Avança um passo do ciclo (o mesmo que o FLUXO 3 do pipeline) |
| `python -c "from utils.connection import load_env; from utils.paths import ENV_PATH; from cotacao.quotation import start_weekly_quotation; start_weekly_quotation(load_env(ENV_PATH))"` | Abre o ciclo da semana e notifica os fornecedores |

`cotacao/quotation.py` mantém `start_weekly_quotation`, `check_responses`,
`send_followups` e `run_automated_cycle` para chamada manual.

### Manutenção — rodados à mão, nunca pelo cron

| Comando | O que faz |
|---|---|
| `python -m manutencao.importar_precos` | Importa planilhas de `files/price_quote/` para `price_quote` |
| `python -m manutencao.comparar_fornecedores` | Relatório de preço entre fornecedores de um ciclo |
| `python -m manutencao.seed_meat_suppliers` | Cadastra os fornecedores de carne |
| `python -m manutencao.seed_supplier_alias` | Gera o CSV do de-para; `--aplicar` grava os aprovados |
| `python -m manutencao.seed_item_sinonimos` | Bootstrap do de-para de vocabulário de item; `--aplicar` grava e gera `files/sinonimos/DE_PARA_ITENS.xlsx` |
| `python -m manutencao.importar_sinonimos` | Confere/força a carga do Excel DE-PARA de item para `dim_item_sinonimo` |
| `python -m manutencao.backfill_price_quote` | Corrige o histórico de `price_quote` |
| `python -m manutencao.migrate_suppliers --tudo` | Carga inicial vinda de planilha externa |

Os scripts de manutenção **simulam por padrão** e só gravam com `--aplicar`.

### Sinônimos de item (de-para de vocabulário)

O de-para que traduz `CHIX→CHICKEN`, `SASSAMI→TENDER` etc. mora em
`dwschiavon2.dim_item_sinonimo`, editado pela planilha `files/sinonimos/DE_PARA_ITENS.xlsx`
(colunas `DE` | `PARA`). Todo `python main.py` (pipeline completa) e `python main.py
--so-conciliar` **sincronizam a planilha no início da execução** antes de conciliar;
planilha aberta no Excel é pulada e retomada na próxima execução. `--so-invoices` /
`--so-cotacao` isolados não sincronizam (não há consumidor).

### Testes

```bash
python -m pytest tests/test_matcher.py -v
python tests/test_matcher.py              # roda sem pytest instalado
```

---

## Estrutura do projeto

Uma pasta por etapa do pipeline, com nome de ação. A **ordem não está na árvore** — está no
`main.py`. O que cruza etapas mora em `utils/`; o que é forma de dado, em `models/`.

```
projeto_schiavon_agente/
│
├── main.py                       # Orquestrador: monta o Pipeline, FLUXO 1/4 -> 4/4
│
├── utils/                        # Infraestrutura compartilhada
│   ├── connection.py             #   load_env, connect_db
│   ├── paths.py                  #   diretorios do projeto, resolvidos num lugar so
│   ├── pipeline.py               #   classe Pipeline: roda os fluxos isolando falha + RESUMO
│   ├── sharepoint.py             #   cliente SharePoint (REST + Playwright)
│   ├── processo.py               #   ciclo de vida do caso (tabela processo)
│   ├── texto.py                  #   truncar()
│   └── banner.py                 #   cabecalhos e reguas do console
│
├── models/                       # Forma dos dados
│   ├── invoice.py                #   InvoiceData / InvoiceItem (schema que o Vision preenche)
│   └── cotacao.py                #   PriceRow / QuotationPrice
│
├── coleta_invoices/              # FLUXO 2
│   ├── coleta.py                 #   coletar_invoices(env)   <- fachada  (MODO_SINCRONO)
│   ├── vision.py                 #   leitura de PDF/imagem via Claude Vision + Batch API
│   ├── invoice_pipeline.py       #   coleta arquivos, le com o Claude, persiste
│   └── invoices_db.py            #   configs, invoice_header, invoice_items
│
├── cotacao/                      # FLUXO 3
│   ├── cotacao.py                #   avancar_cotacao(env)    <- fachada (roda tb. via -m)
│   ├── quotation.py              #   ciclo semanal completo
│   ├── quotation_generator.py    #   gera/atualiza o Excel por fornecedor
│   ├── prices.py                 #   le a planilha de precos
│   ├── messenger.py              #   WhatsApp (Twilio Content API)
│   ├── email_sender.py           #   e-mail (SMTP)
│   ├── excel_handler.py          #   polling de ausencias na Twilio
│   ├── config_connection_twilio.py
│   └── cotacao_db.py             #   meat_suppliers, quotation_*
│
├── conciliacao/                  # FLUXOS 1 e 4
│   ├── sinonimos.py              #   sincronizar_sinonimos(env)    <- fachada do FLUXO 1
│   ├── reconcile_quote.py        #   reconcile_quote(env)          <- fachada do FLUXO 4
│   ├── matcher.py                #   motor de match e tolerancia
│   └── conciliacao_db.py         #   dim_fornecedor_alias, dim_item_sinonimo, fat_conciliacao_*
│
├── manutencao/                   # Scripts avulsos, fora do pipeline
│   ├── importar_precos.py · comparar_fornecedores.py
│   ├── seed_meat_suppliers.py · seed_supplier_alias.py
│   └── backfill_price_quote.py · migrate_suppliers.py
│
├── sql/                          # DDL e migracoes
│   ├── schema_invoices.sql · schema_quotations.sql
│   ├── schema_supplier_alias.sql · schema_reconciliation.sql
│   └── alter_add_sharepoint_file_id.sql · alter_price_quote_cycle.sql
│
├── tests/test_matcher.py         # Testes do motor (em memoria, sem Postgres)
│
├── files/
│   ├── unprocessed_files/        # baixados, ainda nao lidos
│   ├── read_files/               # ja lidos e gravados
│   ├── price_quote/              # entrada do importador manual
│   └── quotation_outbound/       # Excel de cotacao gerados
│
├── .env · requirements.txt · resultado.json · LEIA.md
```

### Dependências entre módulos

A regra é que **nenhuma etapa importa outra etapa**. O que duas etapas precisam vai para
`utils/`.

```
utils/*                       <- sem imports do projeto (exceto utils.paths)
models/*                      <- sem imports do projeto
conciliacao/matcher.py        <- sem imports do projeto, de proposito (ver abaixo)

coleta_invoices/invoices_db.py       ->  utils.texto
coleta_invoices/vision.py            ->  models.invoice
coleta_invoices/invoice_pipeline.py  ->  coleta_invoices.*, utils.processo
coleta_invoices/coleta.py            ->  coleta_invoices.*, utils.{connection,paths,sharepoint}

cotacao/prices.py             ->  models.cotacao, utils.paths
cotacao/quotation.py          ->  cotacao.*, utils.{connection,paths,sharepoint}
cotacao/cotacao.py            ->  cotacao.{quotation,cotacao_db}, utils.{connection,paths}

conciliacao/sinonimos.py      ->  conciliacao.{matcher,conciliacao_db}, utils.{connection,paths}
conciliacao/reconcile_quote.py ->  conciliacao.{matcher,conciliacao_db,sinonimos}, utils.connection

utils/pipeline.py             ->  utils.banner
main.py                       ->  as quatro fachadas + utils.{banner,connection,paths,pipeline}
manutencao/*                  ->  atravessa etapas de proposito (sao correcoes pontuais)
```

Duas escolhas que valem explicação:

- **`utils/sharepoint.py`** tem dois consumidores — a coleta de invoices e a cotação, que
  reusa `open_sharepoint_session`, `download_single_file` e `upload_file_to_sharepoint`.
  Deixá-lo dentro do FLUXO 1 faria o FLUXO 2 depender do FLUXO 1.
- **`conciliacao/matcher.py`** não importa nada do projeto (só stdlib e `rapidfuzz`) e por
  isso guarda os próprios objetos de valor em vez de usar `models/`. É o que permite
  testá-lo sem Postgres e reaproveitá-lo na frente ERP.

Scripts em subpastas rodam como módulo, a partir da raiz: `python -m manutencao.<nome>`.

---

## Módulo: `main.py` — Orquestração

Fino de propósito: monta o `Pipeline` (`utils/pipeline.py`) e chama uma fachada por fluxo.
Sem argparse, sem recorte, sem atalhos — a lógica toda está nas fachadas.

```python
pipeline = Pipeline(TOTAL_FLUXOS)
pipeline.rodar(1, "Sinônimos",   lambda: sincronizar_sinonimos(env))
pipeline.rodar(2, "Invoices",    lambda: coletar_invoices(env))
pipeline.rodar(3, "Cotação",     lambda: avancar_cotacao(env))
pipeline.rodar(4, "Conciliação", lambda: reconcile_quote(env))
pipeline.resumo()   # imprime OK/ERRO por fluxo e sai com 1 se algo falhou
```

`Pipeline.rodar` executa cada fluxo em `try/except`: uma falha não derruba os demais, só
entra como `ERRO` no RESUMO. Os imports pesados (`playwright`, `anthropic`) são tardios,
dentro da fachada de cada etapa.

**O FLUXO 2 dá um passo e sai** — sem ciclo aberto, abre a semana; com ciclo aberto, verifica
respostas e cobra os atrasados. Quem define o ritmo é o cron, não um `sleep` dentro do
processo. O laço antigo continua em `--cotacao-auto`.

---

## Módulo: `coleta_invoices/invoice_pipeline.py` — Pipeline de Invoices

Coordena a coleta de arquivos (filesystem + banco) com a leitura via Claude e a persistência.

| Função | Descrição |
|---|---|
| `collect_items(conn, results, download_dir)` | Monta lista de BatchItem: execução atual + arquivos órfãos |
| `process_invoices_batch(conn, api_key, results, model, download_dir, read_dir)` | Envia para Batch API (50% desconto) |
| `process_invoices_sync(conn, api_key, results, model, download_dir, read_dir)` | Processa síncronamente (--agora) |
| `_persist_and_move(conn, item, invoice_data, read_dir)` | Salva no banco e move arquivo para read_files/ |

---

## Acesso a banco — um módulo por etapa

Não existe mais um `db.py` central. Cada etapa faz o próprio acesso, e o que é comum ficou
em `utils/`.

| Módulo | Tabelas |
|---|---|
| `utils/connection.py` | `load_env(path)`, `connect_db(env)` |
| `utils/execution_log.py` | `execution_log` — `save_execution_log(...)`, `latest_log_id(...)` |
| `coleta_invoices/invoices_db.py` | `configs`, `invoice_header`, `invoice_items`, `config_from_filename` |
| `cotacao/cotacao_db.py` | `meat_suppliers`, `quotation_items`, `quotation_requests`, `quotation_responses`, `quotation_followups` |
| `conciliacao/conciliacao_db.py` | `supplier_alias`, `reconciliation_run` / `_header` / `_item`, e as leituras de invoice usadas na conciliação |

As leituras de `invoice_header` / `invoice_items` para conciliar vivem em
`conciliacao_db.py`, não em `invoices_db.py`: quem consome o dado é o FLUXO 3.

---

## Módulo: `coleta_invoices/vision.py` — Claude Vision

### Preços (USD por 1M tokens)

| Modelo | Síncrono (cheio) | Batch (50% desconto) |
|---|---|---|
| `claude-haiku-4-5` | $1.00 / $5.00 | $0.50 / $2.50 |
| `claude-sonnet-4-6` | $3.00 / $15.00 | $1.50 / $7.50 |
| `claude-opus-4-8` | $5.00 / $25.00 | $2.50 / $12.50 |

- `_PRICES_SYNC` — usado em `read_invoice()` (chamada síncrona)
- `_PRICES` — usado em `collect_batch_results()` (Batch API)

### Notas importantes

- **NÃO usar `messages.parse()`** — `Grammar compilation timed out` com schemas complexos
- **NÃO usar `thinking={"type": "adaptive"}`** — incompatível com JSON schema
- Resposta: `messages.create()` com prompt pedindo JSON puro, parseado com `re.search + json.loads`
- `_repair_truncated_json()` fecha colchetes/chaves abertas quando `stop_reason=max_tokens`

---

## Configuração do `.env`

```env
# Banco de dados PostgreSQL (Azure)
HOST="..."
PORT="5432"
DATABASE="dw02"
SCHEMA="dwschiavon"
USER="guvi"
PASSWORD="..."

# SharePoint Microsoft 365
SHAREPOINT_USERNAME="carlos.rozaboni@dataguvi.com.br"
SHAREPOINT_PASSWORD="..."

# Claude Vision API (Anthropic)
schiavon_key_vision="sk-ant-..."
VISION_MODEL=claude-sonnet-4-6       # haiku-4-5 | sonnet-4-6 | opus-4-8
```

---

## Banco de Dados — schema `dwschiavon`

### `configs`

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | smallserial PK | — |
| `name(80)` | varchar | Nome do config |
| `tool` | varchar(80) | `sharepoint` |
| `url` | varchar | URL de destino |

**Registros ativos:**
- `id=1` → tool=sharepoint → Scanner-Windermere
- `id=2` → tool=sharepoint → Scanner-Dr.Phillips

### `execution_log`

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | serial PK | — |
| `id_config` | int2 FK | → configs.id |
| `created_at` | timestamp | Horário SP (UTC-3) |
| `status` | bool | true=sucesso |
| `path_navigated` | varchar | Caminho percorrido ou `"ERRO: mensagem"` |
| `files_found` | int2 | Itens encontrados |

### `invoice_header` / `invoice_items`

Tabelas de invoices extraídas pelo Claude Vision. Ver `coleta_invoices/schema_invoices.sql` para DDL completo.
Campos chave: `invoice_number`, `invoice_date`, `total_amount`, `reading_confidence`, `cost_read`.

---

## Estrutura de pastas SharePoint

```
Windermere/ (ou Dr. Phillips/)
└── Invoices Fornecedores/
    └── 2026/
        └── _Invoices para Lançamento/
            └── 06 JUN - 2026/
                └── 22 A 30/         ← resolve_week_folder() detecta pelo dia (hoje, ou hoje-7 com --semana-anterior)
```

`build_nav_steps(reference)` recebe uma data de referência (padrão: hoje) e a propaga para
ano/mês/semana, então `--semana-anterior` também ajusta corretamente o mês/ano quando a
semana anterior cai no mês/ano anterior.

Prefixos de arquivo: `wind_` (Windermere) / `drphil_` (Dr. Phillips)
Formato: `{prefixo}_{nome_original}_{dd-mm-yyyy}.{ext}`

---

## O que NÃO funciona (não tentar de novo)

- **MSAL device flow / ROPC** — app público não autorizado no tenant `rokkasmarket.com`
- **`requests` com basic auth** — SharePoint moderno não aceita
- **`client.messages.parse()`** — `Grammar compilation timed out` para schemas complexos
- **`thinking={"type": "adaptive"}` com structured outputs** — causa timeout

---

## Próximos passos

- Integração com o ERP Catapult será refeita do zero (acesso via API descontinuado)
- Reprocessar arquivos com `reading_status='failed'` ou `reading_confidence < 70`
- Dashboard de custo acumulado (`SUM(cost_read)`)
