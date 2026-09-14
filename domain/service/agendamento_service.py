"""Agendamento e heartbeat dos fluxos (G4).

A tabela `dwschiavon2.agendamento` é a fonte da verdade da cadência para o
painel: guarda a expressão cron por fluxo e, a cada execução, o heartbeat
(`ultima_exec`, `ultimo_status`, `proxima_exec` calculada da cron).

O disparo real segue sendo o cron externo do servidor — esta tabela documenta o
que se espera e é o que o painel lê para mostrar "próxima execução".
"""

from __future__ import annotations

from datetime import datetime

from domain.service.processo_service import SCHEMA

__all__ = ["registrar_heartbeat", "fluxos_atrasados", "proxima_execucao"]


def proxima_execucao(cron_expr: str, base: datetime | None = None) -> datetime | None:
    """Próximo horário previsto para uma expressão cron. `None` se `croniter`
    não estiver instalado ou a expressão for inválida — o heartbeat ainda grava
    `ultima_exec`, só não projeta a próxima."""
    base = base or datetime.now()
    try:
        from croniter import croniter

        return croniter(cron_expr, base).get_next(datetime)
    except Exception:  # noqa: BLE001 — sem croniter / cron inválida: só não projeta
        return None


def registrar_heartbeat(conn, fluxo: str, status: str) -> None:
    """Marca a execução de `fluxo` agora e projeta a próxima pela cron."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT cron_expr FROM {SCHEMA}.agendamento WHERE fluxo = %s",
            (fluxo,),
        )
        row = cur.fetchone()
        if row is None:
            return  # fluxo não agendado: nada a registrar
        prox = proxima_execucao(row[0])
        cur.execute(
            f"""
            UPDATE {SCHEMA}.agendamento
               SET ultima_exec   = now() AT TIME ZONE 'America/Sao_Paulo',
                   ultimo_status = %s,
                   proxima_exec  = %s,
                   atualizado_em = now() AT TIME ZONE 'America/Sao_Paulo'
             WHERE fluxo = %s
            """,
            (status, prox, fluxo),
        )
    conn.commit()


def fluxos_atrasados(conn, horas: int = 12) -> list[dict]:
    """Fluxos ativos cuja última execução passou de `horas` atrás (ou nunca
    rodaram)."""
    from psycopg2.extras import RealDictCursor

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT fluxo, ultima_exec, ultimo_status
              FROM {SCHEMA}.agendamento
             WHERE ativo
               AND (ultima_exec IS NULL
                    OR ultima_exec < (now() AT TIME ZONE 'America/Sao_Paulo')
                                     - make_interval(hours => %s))
            """,
            (horas,),
        )
        return [dict(r) for r in cur.fetchall()]
