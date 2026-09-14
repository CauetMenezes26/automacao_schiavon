"""FLUXO 5 — Monitor.

Consolida o status de acesso dos sistemas (`dim_sistema`) e abre/fecha alertas à
operação (`alerta`, com dedupe + e-mail). Lê o que os quatro fluxos anteriores
deixaram; não depende de nenhum ter dado certo, e não levanta.

A implementação vive em `domain/service/sistema_service.py` — que também expõe
`registrar_acesso` / `checar_ambiente`, a API usada pelos outros fluxos no ponto
do login. Este módulo é um wrapper fino sobre `sistema_service.verificar`.

Não há healthcheck aqui: o monitoramento é próprio do projeto — `dim_sistema` +
`alerta` + e-mail à operação.
"""

from __future__ import annotations

from commons.logging_config import get_logger
from domain.service import sistema_service

log = get_logger(__name__)


def monitor_flow(env: dict) -> None:
    """Consolida o estado dos sistemas e abre/fecha alertas."""
    sistema_service.verificar(env)


# Compat / uso direto.
verificar = monitor_flow
