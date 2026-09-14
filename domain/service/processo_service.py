"""Ciclo de vida do caso — a tabela `processo` do dwschiavon2.

Um caso é um arquivo de nota **ou** um ciclo de cotação. Esta é a única porta
de escrita em `processo`: os três fluxos passam por aqui, e é isso que impede
cada um de inventar sua própria regra de status e percentual.

O status nunca é escolhido à mão. Sai de `estado_apos` / `estado_falha`
(`utils.status_exec`), que derivam do `EtapaEnum`: a última etapa do fluxo fecha
o caso como FINALIZADO, as outras deixam EM_ANDAMENTO, e uma etapa que falha
não avança o percentual.
"""

from __future__ import annotations

from datetime import date

from psycopg2.extras import RealDictCursor

from domain.enums import (
    EtapaEnum,
    StatusExecEnum,
    estado_apos,
    estado_falha,
)

# Qualificado de propósito: a migração é por fluxo, e o resto do sistema ainda
# roda no dwschiavon via search_path. Um lugar só para mudar quando terminar.
SCHEMA = "dwschiavon2"

__all__ = [
    "SCHEMA",
    "abrir",
    "concluir_etapa",
    "aguardar_etapa",
    "falhar_etapa",
    "registrar_contagem",
    "buscar",
]


def abrir(
    conn,
    cod_tipo: str,
    chave_natural: str,
    id_loja: int | None = None,
    referencia: date | None = None,
) -> int:
    """Abre o caso, ou recupera o que já existe. Retorna o id.

    Idempotente pela chave natural: rodar de novo o mesmo arquivo (ou a mesma
    semana) não cria um caso duplicado — incrementa `tentativas`. É o que
    permite reprocessar sem sujar o banco.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {SCHEMA}.processo
                (cod_tipo, chave_natural, id_loja, referencia,
                 cod_status, status_exec, percent_exec, tentativas)
            VALUES (%s, %s, %s, %s, %s, %s, 0, 1)
            ON CONFLICT (cod_tipo, chave_natural) DO UPDATE
               SET tentativas    = {SCHEMA}.processo.tentativas + 1,
                   atualizado_em = now() AT TIME ZONE 'America/Sao_Paulo'
            RETURNING id
            """,
            (cod_tipo, chave_natural, id_loja, referencia,
             int(StatusExecEnum.PENDENTE), StatusExecEnum.PENDENTE.name),
        )
        id_processo = cur.fetchone()[0]
    conn.commit()
    return id_processo


def _marcar(conn, id_processo, etapa, status, percent, mensagem, custo) -> None:
    """Grava etapa, status e percentual de uma vez. O custo é acumulado."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {SCHEMA}.processo
               SET cod_etapa     = %s,
                   etapa_exec    = %s,
                   cod_status    = %s,
                   status_exec   = %s,
                   percent_exec  = %s,
                   mensagem      = %s,
                   custo_usd     = COALESCE(custo_usd, 0) + COALESCE(%s, 0),
                   atualizado_em = now() AT TIME ZONE 'America/Sao_Paulo'
             WHERE id = %s
            """,
            (int(etapa), etapa.name, int(status), status.name, percent,
             mensagem, custo, id_processo),
        )
    conn.commit()


def concluir_etapa(
    conn,
    id_processo: int,
    etapa: EtapaEnum,
    com_alerta: bool = False,
    custo: float | None = None,
) -> StatusExecEnum:
    """Registra a etapa como concluída. Retorna o status resultante.

    `com_alerta` só tem efeito na última etapa do fluxo: fecha o caso como
    FINALIZADO_COM_ALERTA em vez de FINALIZADO, quando algo ficou para conferir.
    """
    status, percent = estado_apos(etapa, com_alerta)
    _marcar(conn, id_processo, etapa, status, percent, None, custo)
    return status


def aguardar_etapa(
    conn,
    id_processo: int,
    etapa: EtapaEnum,
    status: StatusExecEnum = StatusExecEnum.AGUARDANDO_RESPOSTA,
) -> None:
    """Marca o caso como parado à espera de algo externo.

    O percentual é o da etapa: ela fez o que podia, quem falta é o fornecedor.
    """
    _marcar(conn, id_processo, etapa, status, etapa.percent_exec, None, None)


def falhar_etapa(
    conn,
    id_processo: int,
    etapa: EtapaEnum,
    status: StatusExecEnum,
    mensagem: str | None = None,
) -> None:
    """Registra a falha. O percentual fica no da etapa anterior."""
    status, percent = estado_falha(etapa, status)
    _marcar(conn, id_processo, etapa, status, percent,
            (mensagem or "")[:2000] or None, None)


def registrar_contagem(
    conn,
    id_processo: int,
    encontrados: int | None = None,
    baixados: int | None = None,
) -> None:
    """Grava a contagem da varredura no caso 'coleta'.

    Métrica, não estado: não mexe em `cod_etapa`/`cod_status`. Passa por aqui só
    para manter a porta única de escrita em `processo`. `COALESCE` deixa cada
    lado ser gravado numa chamada separada (navegação grava `encontrados`,
    download grava `baixados`).
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {SCHEMA}.processo
               SET arquivos_encontrados = COALESCE(%s, arquivos_encontrados),
                   arquivos_baixados    = COALESCE(%s, arquivos_baixados),
                   atualizado_em        = now() AT TIME ZONE 'America/Sao_Paulo'
             WHERE id = %s
            """,
            (encontrados, baixados, id_processo),
        )
    conn.commit()


def buscar(conn, cod_tipo: str, chave_natural: str) -> dict | None:
    """Recupera o caso pela chave natural, ou None."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT id, cod_tipo, chave_natural, id_loja, referencia,
                   cod_etapa, etapa_exec, cod_status, status_exec,
                   percent_exec, tentativas, mensagem, custo_usd
              FROM {SCHEMA}.processo
             WHERE cod_tipo = %s AND chave_natural = %s
            """,
            (cod_tipo, chave_natural),
        )
        row = cur.fetchone()
    return dict(row) if row else None
