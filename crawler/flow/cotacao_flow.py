"""FLUXO 3 — Cotação semanal de carnes.

**Dá um passo e sai.** Quem define o ritmo é o cron, não um `sleep` dentro do
processo — senão `python main.py` ficaria horas parado aqui. O laço antigo
continua em `cotacao/quotation.py::run_automated_cycle()`.

O passo é decidido pelo estado no banco:

    sem ciclo aberto   -> abre o ciclo da semana e notifica os fornecedores
    com ciclo aberto   -> verifica respostas no SharePoint e cobra os atrasados

O ciclo completo (geração de Excel, envio, verificação, importação) mora em
`cotacao/quotation.py`. Aqui só a decisão do passo + o contrato de fluxo.
"""

from __future__ import annotations

from commons.db import connect_db
from commons.exception import BusinessException
from commons.logging_config import get_logger
from cotacao.quotation import check_responses, send_followups, start_weekly_quotation
from domain.service.cotacao_service import fetch_open_quotation_request

log = get_logger(__name__)


def cotacao_flow(env: dict) -> str:
    """Avança um passo do ciclo de cotação. Retorna o passo executado."""
    try:
        conn = connect_db(env)
        try:
            aberto = fetch_open_quotation_request(conn)
        finally:
            _fechar(conn)

        if aberto is None:
            log.info("cotacao: nenhum ciclo aberto - iniciando a cotacao da semana")
            start_weekly_quotation(env)
            return "iniciado"

        log.info("cotacao: ciclo %s aberto (id=%s) - verificando respostas",
                 aberto["week_label"], aberto["id"])
        check_responses(env)
        send_followups(env)
        return "avancado"

    except BusinessException as exc:
        log.warning("cotacao: caso de negocio - %s", exc)
        return "negocio"


def _fechar(conn) -> None:
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        log.warning("cotacao: falha ao fechar conexao", exc_info=True)


# Compat: nome antigo da fachada.
avancar_cotacao = cotacao_flow
