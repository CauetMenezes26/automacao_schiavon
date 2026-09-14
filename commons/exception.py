"""Hierarquia de exceções do RPA.

O objetivo é **classificar a falha** para que cada camada saiba o que fazer com
ela:

    RpaError                     base — nunca levante diretamente
    ├── BusinessException        erro ESPERADO de regra de negócio
    ├── CrawlerException         erro TÉCNICO do robô / infraestrutura
    │   ├── IntegracaoException  falha de sistema externo (API, SharePoint, SMTP)
    │   └── DataAccessException  falha ao ler/gravar no banco
    └── ConfigException          configuração ausente ou inválida (.env / profile)

Impacto (ver `.cursor/rules/tratamento-erro.mdc` e `monitoramento.mdc`):

| Exceção              | Pipeline | Log     | Ação no fluxo                                  |
|----------------------|----------|---------|-----------------------------------------------|
| BusinessException    | OK       | WARNING | marca o caso e segue para o próximo           |
| CrawlerException     | ERRO     | ERROR   | pode abortar se acumular                      |
| IntegracaoException  | ERRO     | ERROR   | aborta o fluxo; `registrar_acesso(ok=False)`  |
| DataAccessException  | ERRO     | ERROR   | aborta o fluxo                                |
| ConfigException      | ERRO     | ERROR   | aborta o fluxo (não adianta repetir)          |

"OK"/"ERRO" é o veredito que `crawler/pipeline.py::Pipeline.rodar` põe no RESUMO.
Não há healthcheck neste projeto — o monitoramento é `dim_sistema` + `alerta`,
alimentado por `monitor.registrar_acesso` no ponto do login.

Regra: `BusinessException` nunca deve ser usada para erro de rede, timeout,
elemento não encontrado ou NULL inesperado — isso é `CrawlerException`.
"""

from __future__ import annotations

__all__ = [
    "RpaError",
    "BusinessException",
    "CrawlerException",
    "IntegracaoException",
    "DataAccessException",
    "ConfigException",
]


class RpaError(Exception):
    """Raiz de toda exceção lançada de propósito pelo projeto.

    `except RpaError` pega tudo que o robô classificou; `except Exception`
    continua pegando o que escapou sem classificação (tratado como técnico).
    """


class BusinessException(RpaError):
    """Erro esperado de regra de negócio. **Não** indica problema no robô.

    Exemplos: nota sem fornecedor correspondente no de-para, item da nota sem
    par na cotação, planilha do fornecedor sem preço preenchido, semana sem
    cotação para conciliar.

    Não abre alerta no monitor. Registrada como WARNING. O fluxo marca o caso
    com o status de negócio adequado e segue para o próximo item; o `Pipeline`
    ainda considera o fluxo OK.
    """


class CrawlerException(RpaError):
    """Erro técnico do robô ou da infraestrutura. Requer atenção.

    Exemplos: falha ao abrir a sessão do navegador, elemento não encontrado,
    resposta malformada, timeout, `KeyError`/`None` inesperado.

    Propaga para `Pipeline.rodar`, que marca o fluxo ERRO no RESUMO e sai com
    código 1. Registrada como ERROR com stack trace. Sempre propague a causa:
    `raise CrawlerException("...") from exc`.
    """


class IntegracaoException(CrawlerException):
    """Falha de comunicação com um sistema externo.

    Exemplos: SharePoint fora do ar, API Anthropic recusando, SMTP inacessível,
    Twilio retornando 5xx.

    É `CrawlerException`, mas nomeada à parte porque a reação costuma ser
    **abortar o fluxo imediatamente** (e chamar `monitor.registrar_acesso(conn,
    sistema, ok=False, mensagem=...)`) em vez de tentar de novo item a item — o
    sistema todo está indisponível.
    """


class DataAccessException(CrawlerException):
    """Falha ao ler ou gravar no banco de dados.

    Envolve o erro do driver (`psycopg2.Error`) com contexto de negócio:
    `raise DataAccessException(f"falha ao gravar processo id={pid}") from exc`.
    """


class ConfigException(RpaError):
    """Configuração obrigatória ausente ou inválida.

    Exemplos: chave faltando no profile, caminho inexistente, credencial de
    sistema crítico não definida. Repetir a execução não resolve — o fluxo
    aborta e o alerta aponta a chave.
    """
