"""FLUXO 1 — Sincroniza o de-para de vocabulário de item (Google Sheets -> banco).

Preâmbulo do pipeline: lê a aba `De-Para` da planilha configurada em
`SINONIMOS_SHEET_ID` e faz upsert idempotente em `dim_item_sinonimo`, para a
conciliação rodar com o vocabulário atualizado.

A leitura/validação e o upsert moram em `conciliacao/sinonimos.py` (testável sem
Postgres). Aqui só a orquestração + o contrato de fluxo.

**Preâmbulo não derruba o pipeline.** `SINONIMOS_SHEET_ID` ausente, Sheets fora
do ar, banco fora do ar ou erro de leitura -> aviso e a conciliação segue com o
último estado bom. Por isso este fluxo engole também o erro técnico
(comportamento documentado, diferente dos demais).
"""

from __future__ import annotations

from commons.exception import BusinessException, ConfigException, DataAccessException
from commons.logging_config import get_logger
from conciliacao.sinonimos import sincronizar
from domain.config import Config

log = get_logger(__name__)


def sinonimos_flow(config: Config) -> None:
    """Abre conexão, sincroniza a aba De-Para do Google Sheets, sai."""
    from commons.db import conexao

    # Um `try` so, varios handlers: o preambulo nao derruba o pipeline em
    # NENHUM dos casos (sem banco, regra de negocio, ou falha inesperada),
    # so muda a mensagem. Antes eram dois `try` porque o `close` precisava
    # de um `finally` proprio — agora e o `with` que fecha.
    try:
        with conexao(config.banco) as conn:
            if sincronizar(conn, config.sinonimos_sheet_id or None) is None:
                log.info("sinonimos: SINONIMOS_SHEET_ID ausente ou leitura falhou - "
                         "nada a sincronizar")
    except BusinessException as exc:
        log.warning("sinonimos: %s", exc)
    except (ConfigException, DataAccessException) as exc:
        log.warning("sinonimos: sem conexao com o banco, pulando (%s)", exc)
    except Exception as exc:  # noqa: BLE001 — preambulo nao derruba o pipeline
        log.warning("sinonimos: sincronizacao falhou, pulando nesta execucao (%s)", exc)



# Compat: nome antigo da fachada.
sincronizar_sinonimos = sinonimos_flow
