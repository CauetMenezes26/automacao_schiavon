-- =============================================================================
-- dwschiavon2 — delta para o estado exigido pelo código atual.
--
-- Estado encontrado em 2026-09-04: schema existe, 14 tabelas com dados. Faltam
-- as 3 tabelas de monitoramento, colunas em tabelas populadas e a view de
-- estado da cotação.
--
-- Decisão de projeto: status de execução e reprocesso se concentram na tabela
-- `processo` (`cod_status` / `status_exec`). NÃO há views derivando faixa de
-- status (vw_processo / vw_pipeline_saude / vw_fila_reprocesso) — a fila de
-- reprocesso é `SELECT ... FROM processo WHERE cod_status BETWEEN 50 AND 59`.
--
-- IDEMPOTENTE (ADD COLUMN / CREATE TABLE / CREATE INDEX IF NOT EXISTS,
-- CREATE OR REPLACE VIEW, seeds com guard). Aplicar numa transação:
--   .venv/Scripts/python.exe sql/_apply.py
-- ou:
--   psql "$CONN" -1 -f sql/migrate_dwschiavon2_atual.sql
-- =============================================================================

SET search_path = dwschiavon2, public;

-- -----------------------------------------------------------------------------
-- COLUNAS — persistir o que já passa pelo fluxo
-- -----------------------------------------------------------------------------

-- contagem da varredura (nulas fora de cod_tipo 'coleta').
-- Espelha domain/service/processo_service.py::registrar_contagem.
ALTER TABLE processo
    ADD COLUMN IF NOT EXISTS arquivos_encontrados smallint,
    ADD COLUMN IF NOT EXISTS arquivos_baixados    smallint;

-- sinalização de revisão e conferência de soma na nota.
-- `revisar`/`motivo_revisao`: leitura fraca fica visível sem travar a nota.
--   Piso em domain/service/invoice_service.py::CONFIANCA_MINIMA_PAINEL.
-- `soma_itens`: total das linhas tipo_linha='item'. `fecha`: |total - soma| ok.
ALTER TABLE fat_invoice
    ADD COLUMN IF NOT EXISTS revisar        boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS motivo_revisao varchar(40),
    ADD COLUMN IF NOT EXISTS soma_itens     numeric(14,2),
    ADD COLUMN IF NOT EXISTS fecha          boolean;

CREATE INDEX IF NOT EXISTS ix_fat_invoice_revisar
    ON fat_invoice (revisar) WHERE revisar;

-- issue_codes por nota e por linha + nível/score do match de fornecedor.
-- Vocabulário em domain/conciliacao_codes.py.
ALTER TABLE fat_conciliacao
    ADD COLUMN IF NOT EXISTS issue_codes text[],
    ADD COLUMN IF NOT EXISTS match_nivel varchar(20),
    ADD COLUMN IF NOT EXISTS match_score numeric(6,2);

ALTER TABLE fat_conciliacao_item
    ADD COLUMN IF NOT EXISTS issue_codes text[];

-- apoio à seleção limitada do reprocesso por vocabulário
-- (domain/service/conciliacao_service.py::marcar_reprocesso_por_vocabulario).
CREATE INDEX IF NOT EXISTS ix_fat_conc_item_unmatched
    ON fat_conciliacao_item (id_conciliacao)
    WHERE match_nivel = 'unmatched' OR cod_status = 20;

-- -----------------------------------------------------------------------------
-- TABELAS — monitoramento
-- -----------------------------------------------------------------------------

-- cadência esperada por fluxo + heartbeat de cada run.
-- domain/service/agendamento_service.py.
CREATE TABLE IF NOT EXISTS agendamento (
    id            smallserial  PRIMARY KEY,
    fluxo         varchar(20)  NOT NULL UNIQUE,
    cron_expr     varchar(60)  NOT NULL,
    descricao     varchar(120),
    ativo         boolean      NOT NULL DEFAULT true,
    ultima_exec   timestamp,
    ultimo_status varchar(10),
    proxima_exec  timestamp,
    atualizado_em timestamp    NOT NULL DEFAULT (now() AT TIME ZONE 'America/Sao_Paulo'),
    CONSTRAINT agendamento_fluxo_chk CHECK (fluxo IN
        ('pipeline', 'sinonimos', 'invoices', 'cotacao', 'conciliacao', 'monitor')),
    CONSTRAINT agendamento_status_chk CHECK (
        ultimo_status IS NULL OR ultimo_status IN ('ok', 'erro'))
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM agendamento) THEN
        INSERT INTO agendamento (fluxo, cron_expr, descricao) VALUES
            ('pipeline', '0 6 * * *', 'python main.py -- ajustar para o cron real do servidor');
        RAISE NOTICE 'agendamento semeada com a linha pipeline';
    ELSE
        RAISE NOTICE 'agendamento ja populada; seed ignorado';
    END IF;
END $$;

-- inventário dos sistemas externos + status de acesso in-place.
-- codigo espelha domain/sistemas.py::Sistema.
CREATE TABLE IF NOT EXISTS dim_sistema (
    id             smallserial PRIMARY KEY,
    codigo         varchar(40) NOT NULL UNIQUE,
    nome           varchar(80) NOT NULL,
    tipo           varchar(20) NOT NULL,
    critico        boolean     NOT NULL DEFAULT true,
    ativo          boolean     NOT NULL DEFAULT true,
    ultimo_ok      timestamp,
    ultimo_erro    timestamp,
    ultima_mensagem text,
    atualizado_em  timestamp   NOT NULL DEFAULT (now() AT TIME ZONE 'America/Sao_Paulo'),
    CONSTRAINT dim_sistema_tipo_chk CHECK (tipo IN
        ('sharepoint', 'twilio', 'smtp', 'api', 'erp'))
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM dim_sistema) THEN
        INSERT INTO dim_sistema (codigo, nome, tipo, critico, ativo) VALUES
            ('sharepoint_windermere',  'SharePoint Windermere',   'sharepoint', true,  true),
            ('sharepoint_drphillips',  'SharePoint Dr. Phillips',  'sharepoint', true,  true),
            ('twilio_whatsapp',        'Twilio WhatsApp',          'twilio',     true,  true),
            ('smtp_email',             'E-mail SMTP',              'smtp',       true,  true),
            ('anthropic_vision',       'Anthropic Vision',         'api',        true,  true),
            ('erp_catapult',           'ERP Catapult',             'erp',        false, false);
        RAISE NOTICE 'dim_sistema semeada com 6 sistemas';
    ELSE
        RAISE NOTICE 'dim_sistema ja populada; seed ignorado';
    END IF;
END $$;

-- ledger de alertas à operação, com dedupe (no máximo UM aberto por
-- (tipo, chave_dedupe)). Vocabulário em domain/alertas.py::TipoAlerta.
CREATE TABLE IF NOT EXISTS alerta (
    id            bigserial    PRIMARY KEY,
    tipo          varchar(24)  NOT NULL,
    origem        varchar(40),
    chave_dedupe  varchar(120) NOT NULL,
    severidade    varchar(10)  NOT NULL DEFAULT 'erro',
    mensagem      text         NOT NULL,
    detectado_em  timestamp    NOT NULL DEFAULT (now() AT TIME ZONE 'America/Sao_Paulo'),
    notificado_em timestamp,
    resolvido_em  timestamp,
    CONSTRAINT alerta_severidade_chk CHECK (severidade IN ('info', 'alerta', 'erro'))
);

CREATE UNIQUE INDEX IF NOT EXISTS alerta_aberto_uk
    ON alerta (tipo, chave_dedupe) WHERE resolvido_em IS NULL;
CREATE INDEX IF NOT EXISTS ix_alerta_aberto
    ON alerta (tipo, detectado_em) WHERE resolvido_em IS NULL;

-- status de entrega/erro do envio da cotação (o que o Twilio devolve).
-- Espelha domain/enums.py::EnvioCotacaoEnum.
ALTER TABLE fat_cotacao_envio
    ADD COLUMN IF NOT EXISTS envio_status  varchar(12),
    ADD COLUMN IF NOT EXISTS envio_detalhe text;

ALTER TABLE fat_cotacao_envio DROP CONSTRAINT IF EXISTS fat_cotacao_envio_envio_status_chk;
ALTER TABLE fat_cotacao_envio ADD CONSTRAINT fat_cotacao_envio_envio_status_chk
    CHECK (envio_status IS NULL OR envio_status IN
        ('enfileirado', 'enviado', 'entregue', 'falhou', 'sem_canal'));

-- -----------------------------------------------------------------------------
-- id_processo direto nas tabelas satélite
--
-- Hoje só dim_ciclo e fat_invoice apontam direto para processo (id_processo,
-- UK). fat_conciliacao só chega lá indiretamente (via fat_invoice), e as
-- tabelas de cotação só via dim_ciclo — quem quer validar se um caso foi bem
-- sucedido precisa saber a cadeia de join certa. Espelha aqui o mesmo padrão
-- de fat_invoice/dim_ciclo: FK direta, sem UK (uma nota pode ter até duas
-- linhas em fat_conciliacao — cotacao/erp — e um ciclo tem várias em
-- fat_cotacao_envio/_preco, então não é 1-para-1 como fat_invoice/dim_ciclo).
-- Nullable: backfill abaixo cobre o histórico; escrita nova passa a preencher
-- (domain/service/conciliacao_service.py, cotacao_service.py, prices_service.py).
-- -----------------------------------------------------------------------------

ALTER TABLE fat_conciliacao      ADD COLUMN IF NOT EXISTS id_processo bigint;
ALTER TABLE fat_cotacao_envio    ADD COLUMN IF NOT EXISTS id_processo bigint;
ALTER TABLE fat_cotacao_cobranca ADD COLUMN IF NOT EXISTS id_processo bigint;
ALTER TABLE fat_cotacao_preco    ADD COLUMN IF NOT EXISTS id_processo bigint;

UPDATE fat_conciliacao fc
   SET id_processo = fi.id_processo
  FROM fat_invoice fi
 WHERE fi.id = fc.id_invoice AND fc.id_processo IS NULL;

UPDATE fat_cotacao_envio fe
   SET id_processo = dc.id_processo
  FROM dim_ciclo dc
 WHERE dc.id = fe.id_ciclo AND fe.id_processo IS NULL;

UPDATE fat_cotacao_cobranca fk
   SET id_processo = fe.id_processo
  FROM fat_cotacao_envio fe
 WHERE fe.id = fk.id_envio AND fk.id_processo IS NULL;

UPDATE fat_cotacao_preco fp
   SET id_processo = dc.id_processo
  FROM dim_ciclo dc
 WHERE dc.id = fp.id_ciclo AND fp.id_processo IS NULL;

ALTER TABLE fat_conciliacao      DROP CONSTRAINT IF EXISTS fat_conciliacao_id_processo_fkey;
ALTER TABLE fat_conciliacao      ADD  CONSTRAINT fat_conciliacao_id_processo_fkey
    FOREIGN KEY (id_processo) REFERENCES processo (id);

ALTER TABLE fat_cotacao_envio    DROP CONSTRAINT IF EXISTS fat_cotacao_envio_id_processo_fkey;
ALTER TABLE fat_cotacao_envio    ADD  CONSTRAINT fat_cotacao_envio_id_processo_fkey
    FOREIGN KEY (id_processo) REFERENCES processo (id);

ALTER TABLE fat_cotacao_cobranca DROP CONSTRAINT IF EXISTS fat_cotacao_cobranca_id_processo_fkey;
ALTER TABLE fat_cotacao_cobranca ADD  CONSTRAINT fat_cotacao_cobranca_id_processo_fkey
    FOREIGN KEY (id_processo) REFERENCES processo (id);

ALTER TABLE fat_cotacao_preco    DROP CONSTRAINT IF EXISTS fat_cotacao_preco_id_processo_fkey;
ALTER TABLE fat_cotacao_preco    ADD  CONSTRAINT fat_cotacao_preco_id_processo_fkey
    FOREIGN KEY (id_processo) REFERENCES processo (id);

CREATE INDEX IF NOT EXISTS ix_fat_conciliacao_id_processo   ON fat_conciliacao (id_processo);
CREATE INDEX IF NOT EXISTS ix_fat_cotacao_envio_id_processo ON fat_cotacao_envio (id_processo);
CREATE INDEX IF NOT EXISTS ix_fat_cotacao_cobr_id_processo  ON fat_cotacao_cobranca (id_processo);
CREATE INDEX IF NOT EXISTS ix_fat_cotacao_preco_id_processo ON fat_cotacao_preco (id_processo);

-- -----------------------------------------------------------------------------
-- VIEW — estado da cotação por fornecedor
--
-- O grão de `processo` é o ciclo; o estado por fornecedor vive nos carimbos de
-- tempo de `fat_cotacao_envio`. Esta view os recompõe (espelha o CASE de
-- cotacao/quotation.py). Única view do schema — as de status de execução foram
-- retiradas, `processo` responde direto.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_cotacao_envio_status AS
SELECT  e.id,
        e.id_ciclo,
        e.id_fornecedor,
        c.semana_label,
        CASE WHEN e.ausente                  THEN 'ausente'
             WHEN e.importado_em  IS NOT NULL THEN 'importado'
             WHEN e.respondido_em IS NOT NULL THEN 'respondido'
             WHEN e.enviado_em    IS NOT NULL THEN 'enviado'
             ELSE 'pendente'
        END                                                   AS status,
        e.canal,
        e.enviado_em,
        e.respondido_em,
        e.importado_em,
        ROUND(
            EXTRACT(EPOCH FROM (
                (now() AT TIME ZONE 'America/Sao_Paulo') - e.enviado_em
            )) / 3600
        )                                                     AS horas_desde_envio,
        (SELECT count(*)
           FROM dwschiavon2.fat_cotacao_cobranca k
          WHERE k.id_envio = e.id)                            AS cobrancas
   FROM dwschiavon2.fat_cotacao_envio e
   JOIN dwschiavon2.dim_ciclo         c ON c.id = e.id_ciclo;
