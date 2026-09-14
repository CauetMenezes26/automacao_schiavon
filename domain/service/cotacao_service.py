"""Acesso a banco da cotação — schema `dwschiavon2`.

Portado do `dwschiavon`. Os **nomes e assinaturas das funções foram mantidos**
de propósito: o `quotation.py` continua chamando `create_quotation_request`,
`fetch_pending_responses` e companhia, e só a persistência mudou. O risco da
migração fica contido nesta camada.

De-para das tabelas:

    quotation_requests   -> dim_ciclo            (+ processo, tipo 'cotacao')
    quotation_responses  -> fat_cotacao_envio
    quotation_followups  -> fat_cotacao_cobranca
    meat_suppliers       -> dim_fornecedor

O schema é **qualificado explicitamente**, mesmo com o `search_path` já apontando
para o `dwschiavon2`. É cinto e suspensório: se o `.env` mudar por engano, a query
falha em vez de gravar no lugar errado em silêncio.

Uma diferença de modelagem: `quotation_responses.status` (pending/sent/imported/
ausente) não existe mais. O estado é derivado dos carimbos de tempo —
`enviado_em`, `respondido_em`, `importado_em` — mais a flag `ausente`. Guardar
um status redundante ao lado dos timestamps é o tipo de coisa que sai de sincronia.
"""

from __future__ import annotations

from psycopg2.extras import RealDictCursor

from domain.categorias import CATEGORIAS_COTADAS, CategoriaFornecedor
from domain.service import processo_service as proc
from domain.service.processo_service import SCHEMA


# ---------------------------------------------------------------------------
# Fornecedores
# ---------------------------------------------------------------------------

def fetch_active_meat_suppliers(conn) -> list[dict]:
    """Fornecedores ativos que participam do ciclo de cotação.

    Três condições, e cada uma responde por uma coisa diferente:

        categoria   O QUE ele vende. Fato sobre o fornecedor.
        cotado      SE ele entra no ciclo semanal. Interruptor de operação.
        ativo       se o cadastro ainda vale.

    Antes só as duas últimas existiam, e "é de carne" ficava por conta de quem
    marcava `cotado` à mão — cadastrar um distribuidor de papel e marcar a caixa
    o colocava no ciclo sem nada reclamar.

    As colunas saem com os nomes antigos (`name`, `contact_name`,
    `sharepoint_file_id`) para o orquestrador não precisar mudar.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT id,
                   nome         AS name,
                   contato      AS contact_name,
                   email,
                   whatsapp,
                   canal        AS channel,
                   planilha_url AS sharepoint_file_id,
                   categoria
              FROM {SCHEMA}.dim_fornecedor
             WHERE ativo AND cotado AND categoria = ANY(%s)
             ORDER BY nome
            """,
            (list(CATEGORIAS_COTADAS),),
        )
        return [dict(row) for row in cur.fetchall()]


def save_meat_supplier(
    conn,
    name: str,
    contact_name: str | None = None,
    email: str | None = None,
    whatsapp: str | None = None,
    channel: str = "whatsapp",
    sharepoint_file_id: str | None = None,
    notes: str | None = None,
    categoria: str = CategoriaFornecedor.CARNE,
) -> int:
    """Cadastra ou atualiza um fornecedor. Retorna o id.

    `categoria` nasce CARNE aqui porque é o que o nome da função promete — quem
    chama `save_meat_supplier` está cadastrando carne. Fornecedor que chega por
    qualquer outro caminho (INSERT manual, script novo) cai no default da
    coluna, que é `outros`, e fica fora do ciclo até alguém classificar.

    A categoria NÃO é sobrescrita no conflito: reprocessar o seed não deve
    desfazer uma reclassificação feita à mão no banco.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {SCHEMA}.dim_fornecedor
                (nome, contato, email, whatsapp, canal, cotado, planilha_url,
                 categoria)
            VALUES (%s, %s, %s, %s, %s, true, %s, %s)
            ON CONFLICT (nome) DO UPDATE
               SET contato      = COALESCE(EXCLUDED.contato, {SCHEMA}.dim_fornecedor.contato),
                   email        = COALESCE(EXCLUDED.email, {SCHEMA}.dim_fornecedor.email),
                   whatsapp     = COALESCE(EXCLUDED.whatsapp, {SCHEMA}.dim_fornecedor.whatsapp),
                   canal        = EXCLUDED.canal,
                   planilha_url = COALESCE(EXCLUDED.planilha_url,
                                           {SCHEMA}.dim_fornecedor.planilha_url)
            RETURNING id
            """,
            (name, contact_name, email, whatsapp, channel, sharepoint_file_id,
             str(categoria)),
        )
        supplier_id = cur.fetchone()[0]
    conn.commit()
    return supplier_id


def update_meat_supplier_sharepoint_file_id(
    conn, supplier_id: int, sharepoint_file_id: str,
) -> None:
    """Atualiza o link da planilha do fornecedor."""
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {SCHEMA}.dim_fornecedor SET planilha_url = %s WHERE id = %s",
            (sharepoint_file_id, supplier_id),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Ciclo semanal
# ---------------------------------------------------------------------------

def create_quotation_request(conn, week_label: str, week_start, week_end) -> int:
    """Abre o ciclo da semana. Retorna o id do ciclo.

    Cria também o **caso** em `processo` (tipo 'cotacao', chave = rótulo da
    semana) e amarra os dois. É o processo que carrega etapa, status e
    percentual; o ciclo carrega as datas.
    """
    id_processo = proc.abrir(
        conn, cod_tipo="cotacao", chave_natural=week_label, referencia=week_start,
    )
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {SCHEMA}.dim_ciclo
                (id_processo, semana_label, inicio, fim, aberto)
            VALUES (%s, %s, %s, %s, true)
            ON CONFLICT (semana_label) DO UPDATE
               SET aberto     = true,
                   fechado_em = NULL
            RETURNING id
            """,
            (id_processo, week_label, week_start, week_end),
        )
        request_id = cur.fetchone()[0]
    conn.commit()
    return request_id


def fetch_open_quotation_request(conn) -> dict | None:
    """Ciclo aberto mais recente, ou None.

    Devolve `status` como 'open'/'closed' porque o orquestrador ainda fala
    nesses termos; no banco a informação é a flag `aberto`.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT id,
                   id_processo,
                   semana_label AS week_label,
                   inicio       AS week_start,
                   fim          AS week_end,
                   CASE WHEN aberto THEN 'open' ELSE 'closed' END AS status,
                   criado_em    AS created_at
              FROM {SCHEMA}.dim_ciclo
             WHERE aberto
             ORDER BY id DESC
             LIMIT 1
            """
        )
        row = cur.fetchone()
    return dict(row) if row else None


def fetch_processo_do_ciclo(conn, request_id: int) -> int | None:
    """Id do caso em `processo` que controla este ciclo."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id_processo FROM {SCHEMA}.dim_ciclo WHERE id = %s", (request_id,)
        )
        row = cur.fetchone()
    return row[0] if row else None


def close_quotation_request(conn, request_id: int) -> None:
    """Fecha o ciclo."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {SCHEMA}.dim_ciclo
               SET aberto     = false,
                   fechado_em = now() AT TIME ZONE 'America/Sao_Paulo'
             WHERE id = %s
            """,
            (request_id,),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Envio por fornecedor
# ---------------------------------------------------------------------------

def save_quotation_response(
    conn,
    id_request: int,
    id_supplier: int,
    file_name: str | None = None,
    file_url: str | None = None,
    column_index: int | None = None,
    quote_date=None,
    aba: str | None = None,
) -> int:
    """Registra o envio da planilha a um fornecedor. Retorna o id.

    `ON CONFLICT` porque reabrir o mesmo ciclo não deve duplicar o envio —
    atualiza a coluna e a aba da semana.
    """
    id_processo = fetch_processo_do_ciclo(conn, id_request)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {SCHEMA}.fat_cotacao_envio
                (id_ciclo, id_fornecedor, id_processo, arquivo, arquivo_path, aba,
                 coluna_idx, data_cotacao)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id_ciclo, id_fornecedor) DO UPDATE
               SET arquivo      = EXCLUDED.arquivo,
                   arquivo_path = EXCLUDED.arquivo_path,
                   aba          = EXCLUDED.aba,
                   coluna_idx   = EXCLUDED.coluna_idx,
                   data_cotacao = EXCLUDED.data_cotacao,
                   id_processo  = EXCLUDED.id_processo
            RETURNING id
            """,
            (id_request, id_supplier, id_processo, file_name, file_url, aba,
             column_index, quote_date),
        )
        response_id = cur.fetchone()[0]
    conn.commit()
    return response_id


def update_quotation_response_sent(
    conn, response_id: int, channel: str, message_sid: str | None = None,
    envio_status: str | None = None, envio_detalhe: str | None = None,
) -> None:
    """Marca o envio como despachado.

    `envio_status`/`envio_detalhe` (G11) guardam o que o provedor devolveu —
    inclusive falha de envio, que antes só saía num print.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {SCHEMA}.fat_cotacao_envio
               SET enviado_em    = now() AT TIME ZONE 'America/Sao_Paulo',
                   canal         = %s,
                   message_sid   = COALESCE(%s, message_sid),
                   envio_status  = COALESCE(%s, envio_status),
                   envio_detalhe = %s
             WHERE id = %s
            """,
            (channel, message_sid, envio_status, envio_detalhe, response_id),
        )
    conn.commit()


def update_quotation_response_responded(conn, response_id: int) -> None:
    """Marca que o fornecedor preencheu."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {SCHEMA}.fat_cotacao_envio
               SET respondido_em = COALESCE(respondido_em,
                                            now() AT TIME ZONE 'America/Sao_Paulo')
             WHERE id = %s
            """,
            (response_id,),
        )
    conn.commit()


def update_quotation_response_imported(conn, response_id: int) -> None:
    """Marca que os preços foram importados."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {SCHEMA}.fat_cotacao_envio
               SET importado_em = now() AT TIME ZONE 'America/Sao_Paulo'
             WHERE id = %s
            """,
            (response_id,),
        )
    conn.commit()


def update_quotation_response_ausente(conn, response_id: int) -> None:
    """Fornecedor avisou que não cota nesta semana."""
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {SCHEMA}.fat_cotacao_envio SET ausente = true WHERE id = %s",
            (response_id,),
        )
    conn.commit()


def update_quotation_response_file_url(conn, response_id: int, file_url: str) -> None:
    """Grava o link do Excel criado manualmente no SharePoint.

    Usado quando `create_file_sharing_link` não pode ser chamado (tenant sem
    link anônimo habilitado) — o link é gerado à mão na UI e colado aqui antes
    de notificar o fornecedor.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {SCHEMA}.fat_cotacao_envio SET arquivo_path = %s WHERE id = %s",
            (file_url, response_id),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Consultas do ciclo
# ---------------------------------------------------------------------------

# Enviado e ainda sem importar, e o fornecedor nao se declarou ausente.
# Substitui o antigo `status = 'sent'`.
_PENDENTE = "e.enviado_em IS NOT NULL AND e.importado_em IS NULL AND NOT e.ausente"


def fetch_pending_responses(conn, request_id: int) -> list[dict]:
    """Envios à espera de preenchimento."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT e.id,
                   e.id_fornecedor AS id_supplier,
                   e.arquivo       AS file_name,
                   e.arquivo_path  AS file_url,
                   e.aba,
                   e.coluna_idx    AS column_index,
                   e.data_cotacao  AS quote_date,
                   e.enviado_em    AS sent_at,
                   e.canal         AS sent_channel,
                   f.nome          AS supplier_name,
                   f.whatsapp,
                   f.email,
                   f.canal         AS channel,
                   f.planilha_url  AS sharepoint_file_id,
                   f.contato       AS contact_name
              FROM {SCHEMA}.fat_cotacao_envio e
              JOIN {SCHEMA}.dim_fornecedor    f ON f.id = e.id_fornecedor
             WHERE e.id_ciclo = %s AND {_PENDENTE}
             ORDER BY f.nome
            """,
            (request_id,),
        )
        return [dict(row) for row in cur.fetchall()]


def fetch_responses_for_followup(conn, request_id: int, min_hours: int = 4) -> list[dict]:
    """Envios sem resposta cujo último contato (cobrança anterior, ou o envio
    original se nunca cobrou) passou de N horas.

    Usa o último contato, não só `enviado_em` — sem isso, uma vez vencido o
    prazo o fornecedor seria cobrado de novo a cada execução do fluxo, em vez
    de respeitar o intervalo entre cobranças.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT e.id,
                   e.id_fornecedor AS id_supplier,
                   e.arquivo       AS file_name,
                   e.arquivo_path  AS file_url,
                   e.enviado_em    AS sent_at,
                   e.canal         AS sent_channel,
                   f.nome          AS supplier_name,
                   f.whatsapp,
                   f.email,
                   f.canal         AS channel,
                   f.contato       AS contact_name,
                   COALESCE(c.ultimo_contato, e.enviado_em) AS last_contact_at,
                   COALESCE(c.followup_count, 0)            AS followup_count
              FROM {SCHEMA}.fat_cotacao_envio e
              JOIN {SCHEMA}.dim_fornecedor    f ON f.id = e.id_fornecedor
              LEFT JOIN LATERAL (
                       SELECT count(*)      AS followup_count,
                              max(enviado_em) AS ultimo_contato
                         FROM {SCHEMA}.fat_cotacao_cobranca
                        WHERE id_envio = e.id
                   ) c ON true
             WHERE e.id_ciclo = %s
               AND {_PENDENTE}
               AND e.respondido_em IS NULL
               AND COALESCE(c.ultimo_contato, e.enviado_em)
                   < (now() AT TIME ZONE 'America/Sao_Paulo') - make_interval(hours => %s)
             ORDER BY e.enviado_em
            """,
            (request_id, min_hours),
        )
        return [dict(row) for row in cur.fetchall()]


def fetch_sent_responses_by_supplier_phone(conn, request_id: int) -> dict[str, dict]:
    """Mapa dos 9 últimos dígitos do WhatsApp → envio, para o ciclo dado."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT e.id,
                   e.id_fornecedor AS id_supplier,
                   f.nome          AS supplier_name,
                   f.whatsapp
              FROM {SCHEMA}.fat_cotacao_envio e
              JOIN {SCHEMA}.dim_fornecedor    f ON f.id = e.id_fornecedor
             WHERE e.id_ciclo = %s AND {_PENDENTE}
             ORDER BY f.nome
            """,
            (request_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]

    result: dict[str, dict] = {}
    for r in rows:
        digitos = "".join(filter(str.isdigit, r.get("whatsapp") or ""))
        local = digitos[-9:] if len(digitos) >= 9 else digitos
        if local:
            result[local] = r
    return result


# ---------------------------------------------------------------------------
# Cobranças
# ---------------------------------------------------------------------------

def save_followup(
    conn, response_id: int, channel: str, message_sid: str | None = None,
    notes: str | None = None,
) -> int:
    """Registra uma cobrança enviada. Retorna o id."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {SCHEMA}.fat_cotacao_cobranca
                (id_envio, id_processo, canal, message_sid, observacao)
            SELECT %s, e.id_processo, %s, %s, %s
              FROM {SCHEMA}.fat_cotacao_envio e WHERE e.id = %s
            RETURNING id
            """,
            (response_id, channel, message_sid, notes, response_id),
        )
        followup_id = cur.fetchone()[0]
    conn.commit()
    return followup_id
