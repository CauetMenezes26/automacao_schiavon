"""Monitoramento — inventário de sistemas (`dim_sistema`) + alertas à operação (`alerta`).

`registrar_acesso` / `checar_ambiente` são a API usada pelos fluxos no ponto do
login. `verificar` é o corpo do FLUXO 5 (`crawler/flow/monitor_flow.py` só o
chama): roda como último passo do pipeline, lê o estado deixado pelos fluxos
anteriores e:

  * consolida em `dim_sistema` o acesso de cada sistema;
  * abre um `alerta` para cada sistema crítico com acesso falho e para cada
    fluxo parado há mais de 12h;
  * dispara e-mail à operação **uma vez** por alerta (dedupe via índice único
    parcial `alerta_aberto_uk`);
  * resolve sozinho os alertas cuja causa deixou de existir.

Não é healthcheck: é o mecanismo próprio do projeto.
"""

from __future__ import annotations

from domain import alertas as alr
from domain import sistemas as sis
from domain.service.agendamento_service import fluxos_atrasados
from domain.service.processo_service import SCHEMA

__all__ = [
    "registrar_acesso", "abrir_alerta", "verificar", "checar_ambiente",
    "HORAS_SEM_LEITURA_ALERTA",
]

HORAS_SEM_LEITURA_ALERTA = 12
_DEST_PADRAO = "dataguvi@gmail.com"


# ---------------------------------------------------------------------------
# dim_sistema — status de acesso in-place
# ---------------------------------------------------------------------------

def checar_ambiente(env: dict) -> None:
    """Preâmbulo: proxy barato de "dá para autenticar?" para cada sistema ativo
    (env-vars presentes). O resultado real de login, durante os fluxos,
    sobrescreve isto depois — aqui é o piso para sistemas que um dado run nem
    exercita."""
    try:
        from commons.db import connect_db

        conn = connect_db(env)
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠ monitor: sem conexão para checar ambiente ({exc})")
        return
    try:
        for sistema in sis.Sistema:
            if str(sistema) not in sis.CRITICOS:
                continue
            ok, msg = sis.checar_env(sistema, env)
            registrar_acesso(conn, sistema, ok=ok, mensagem=msg)
    finally:
        conn.close()


def registrar_acesso(conn, sistema: sis.Sistema, ok: bool, mensagem: str | None = None) -> None:
    """Carimba `ultimo_ok` ou `ultimo_erro` do sistema. Chamado no ponto exato
    do sucesso/falha de login dentro de cada fluxo."""
    campo = "ultimo_ok" if ok else "ultimo_erro"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {SCHEMA}.dim_sistema
               SET {campo}       = now() AT TIME ZONE 'America/Sao_Paulo',
                   ultima_mensagem = %s,
                   atualizado_em = now() AT TIME ZONE 'America/Sao_Paulo'
             WHERE codigo = %s
            """,
            (mensagem, str(sistema)),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# alerta — ledger com dedupe + e-mail
# ---------------------------------------------------------------------------

def abrir_alerta(
    conn, env: dict, tipo: alr.TipoAlerta, origem: str | None, mensagem: str,
) -> None:
    """Abre um alerta se ainda não houver um aberto para (tipo, chave_dedupe).
    Se abriu agora e nunca foi notificado, manda o e-mail e carimba
    `notificado_em`."""
    chave = alr.chave(tipo, origem)
    sev = alr.SEVERIDADE[tipo]
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {SCHEMA}.alerta (tipo, origem, chave_dedupe, severidade, mensagem)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (tipo, chave_dedupe) WHERE resolvido_em IS NULL DO NOTHING
            RETURNING id
            """,
            (str(tipo), origem, chave, sev, mensagem),
        )
        row = cur.fetchone()
    conn.commit()
    if row is None:
        return  # já havia um aberto — não reenvia

    enviado = _notificar(env, tipo, origem, mensagem)
    if enviado:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {SCHEMA}.alerta
                   SET notificado_em = now() AT TIME ZONE 'America/Sao_Paulo'
                 WHERE id = %s
                """,
                (row[0],),
            )
        conn.commit()


def _notificar(env: dict, tipo: alr.TipoAlerta, origem: str | None, mensagem: str) -> bool:
    """Manda o e-mail do alerta. Best-effort: se o SMTP não estiver configurado
    ou falhar, retorna False e o alerta segue aberto (retry no próximo run)."""
    from commons.email_client import enviar_email

    destino = (env.get("ALERTA_EMAIL") or _DEST_PADRAO).strip()
    assunto = f"[RPA Schiavon] {tipo.descricao}" + (f" — {origem}" if origem else "")
    html = (
        f"<p><strong>{tipo.descricao}</strong></p>"
        f"<p>Origem: {origem or '-'}</p>"
        f"<p>{mensagem}</p>"
        f"<hr><p style='font-size:.8em;color:#999'>Alerta automático do pipeline "
        f"RPA Schiavon. Enquanto a causa persistir, este alerta segue aberto e "
        f"não será reenviado.</p>"
    )
    try:
        res = enviar_email(env, destino, assunto, html)
        return res.get("status") == "sent"
    except EnvironmentError as exc:
        print(f"  ⚠ alerta registrado, e-mail não enviado ({exc})")
        return False


def _resolver_alertas(conn, tipo: alr.TipoAlerta, chaves_ativas: list[str]) -> int:
    """Fecha os alertas abertos de `tipo` cuja chave não está mais na lista de
    causas ativas."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {SCHEMA}.alerta
               SET resolvido_em = now() AT TIME ZONE 'America/Sao_Paulo'
             WHERE tipo = %s AND resolvido_em IS NULL
               AND NOT (chave_dedupe = ANY(%s))
            """,
            (str(tipo), chaves_ativas),
        )
        n = cur.rowcount
    conn.commit()
    return n


# ---------------------------------------------------------------------------
# Entrypoint do FLUXO "Monitor"
# ---------------------------------------------------------------------------

def verificar(env: dict) -> None:
    """Consolida o estado dos sistemas e abre/fecha alertas. Não levanta."""
    from psycopg2.extras import RealDictCursor

    from utils.connection import connect_db

    try:
        conn = connect_db(env)
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠ monitor: sem conexão com o banco, pulando ({exc})")
        return

    try:
        # --- sistemas críticos com acesso falho ---
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT codigo, ultimo_erro, ultimo_ok, ultima_mensagem
                  FROM {SCHEMA}.dim_sistema
                 WHERE ativo AND critico AND ultimo_erro IS NOT NULL
                   AND (ultimo_ok IS NULL OR ultimo_erro > ultimo_ok)
                """
            )
            falhos = [dict(r) for r in cur.fetchall()]

        chaves_login = []
        for s in falhos:
            chaves_login.append(alr.chave(alr.TipoAlerta.LOGIN_FALHA, s["codigo"]))
            abrir_alerta(
                conn, env, alr.TipoAlerta.LOGIN_FALHA, s["codigo"],
                s["ultima_mensagem"] or "acesso falhou",
            )
        _resolver_alertas(conn, alr.TipoAlerta.LOGIN_FALHA, chaves_login)

        # --- pipeline / leitura parada ---
        atrasados = fluxos_atrasados(conn, HORAS_SEM_LEITURA_ALERTA)
        chaves_parado = []
        for f in atrasados:
            tipo = (alr.TipoAlerta.LEITURA_PARADA if f["fluxo"] in ("invoices", "sinonimos")
                    else alr.TipoAlerta.PIPELINE_PARADO)
            chaves_parado.append(alr.chave(tipo, f["fluxo"]))
            quando = f["ultima_exec"].strftime("%d/%m %H:%M") if f["ultima_exec"] else "nunca"
            abrir_alerta(conn, env, tipo, f["fluxo"],
                         f"fluxo '{f['fluxo']}' sem execução desde {quando} "
                         f"(> {HORAS_SEM_LEITURA_ALERTA}h)")
        for tipo in (alr.TipoAlerta.PIPELINE_PARADO, alr.TipoAlerta.LEITURA_PARADA):
            _resolver_alertas(conn, tipo,
                              [c for c in chaves_parado if c.startswith(str(tipo))])

        abertos = len(falhos) + len(atrasados)
        print(f"  ✓ monitor: {abertos} alerta(s) aberto(s), "
              f"{len(falhos)} sistema(s) com acesso falho")
    finally:
        conn.close()
