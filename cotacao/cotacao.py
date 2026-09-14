"""Fachada movida para `crawler.flow.cotacao_flow`. Shim de compatibilidade (Fase 3).

Mantém `python -m cotacao.cotacao` funcionando (o cron que só precisa avançar a
cotação, sem rodar o pipeline inteiro).
"""

from crawler.flow.cotacao_flow import avancar_cotacao, cotacao_flow  # noqa: F401

if __name__ == "__main__":
    from commons.db import load_env
    from commons.paths import ENV_PATH

    avancar_cotacao(load_env(ENV_PATH))
