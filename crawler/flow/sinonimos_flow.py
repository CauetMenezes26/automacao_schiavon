"""FLUXO 1 — Sincroniza o de-para de vocabulário de item (planilha -> banco).

Preâmbulo do pipeline: lê `files/sinonimos/*.xlsx` e faz upsert idempotente em
`dim_item_sinonimo`, para a conciliação rodar com o vocabulário atualizado.

A leitura/validação e o upsert moram em `conciliacao/sinonimos.py` (testável sem
Postgres). Aqui só a orquestração + o contrato de fluxo.

**Preâmbulo não derruba o pipeline.** Planilha travada no Excel, banco fora do
ar ou erro de leitura -> aviso e a conciliação segue com o último estado bom.
Por isso este fluxo engole também o erro técnico (comportamento documentado,
diferente dos demais).
"""

from __future__ import annotations

from commons.exception import BusinessException
from commons.logging_config import get_logger
from conciliacao.sinonimos import _sincronizar

log = get_logger(__name__)


def sinonimos_flow(env: dict) -> None:
    """Abre conexão, sincroniza a planilha DE-PARA, sai."""
    from commons.db import connect_db

    try:
        conn = connect_db(env)
    except Exception as exc:  # noqa: BLE001 — preambulo nao derruba o pipeline
        log.warning("sinonimos: sem conexao com o banco, pulando (%s)", exc)
        return

    try:
        if _sincronizar(conn) is None:
            log.info("sinonimos: sem planilha em files/sinonimos/ - nada a sincronizar")
    except BusinessException as exc:
        log.warning("sinonimos: %s", exc)
    except Exception as exc:  # noqa: BLE001 — preambulo nao derruba o pipeline
        log.warning("sinonimos: sincronizacao falhou, pulando nesta execucao (%s)", exc)
    finally:
        _fechar(conn)


def _fechar(conn) -> None:
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        log.warning("sinonimos: falha ao fechar conexao", exc_info=True)


# Compat: nome antigo da fachada.
sincronizar_sinonimos = sinonimos_flow
