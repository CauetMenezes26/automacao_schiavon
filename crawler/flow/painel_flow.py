"""FLUXO 6 - Relatório Cotação x Invoice em Excel.

Sobrescreve `files/relatorios/painel_operacao.xlsx` com o que o cliente pediu
ver, em duas abas que não se sobrepõem: "Cotacao x Invoice" (só o que
conciliou — arquivo, fornecedor, preço da invoice x preço cotado) e
"Divergencias Cotacao x Invoice" (só o que não fechou: preço fora da
tolerância e item sem comparação). Lê do banco (mesmas tabelas que os outros
fluxos escrevem); não grava nada.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from commons.db import connect_db
from commons.exception import (
    BusinessException,
    ConfigException,
    CrawlerException,
    DataAccessException,
    IntegracaoException,
)
from commons.logging_config import get_logger
from commons.paths import PAINEL_XLSX
from crawler.reports.painel_excel import gerar_relatorio
from domain.service import conciliacao_service

log = get_logger(__name__)


def painel_flow(env: dict) -> None:
    """Gera o snapshot da semana corrente em `PAINEL_XLSX`."""
    conn = None
    try:
        log.info("painel - iniciando")
        conn = connect_db(env)
        inicio, fim = _semana_atual()
        gerado_em = datetime.now()
        dados = _coletar_dados(conn, inicio, fim)
        _gravar_excel(dados, gerado_em, inicio, fim)
        log.info("painel - fim - arquivo: %s", PAINEL_XLSX.name)
    except BusinessException as exc:
        log.warning("painel - caso de negocio: %s", exc)
    except (IntegracaoException, DataAccessException, ConfigException):
        log.exception("painel - abortado")
    except Exception:
        log.exception("painel - erro nao classificado")
    finally:
        _fechar(conn)


def _semana_atual(hoje: date | None = None) -> tuple[date, date]:
    """Segunda a domingo da semana que contem `hoje` (padrao: hoje)."""
    hoje = hoje or date.today()
    inicio = hoje - timedelta(days=hoje.weekday())
    return inicio, inicio + timedelta(days=6)


def _coletar_dados(conn, inicio: date, fim: date) -> dict:
    """Roda as consultas do relatorio uma unica vez e devolve tudo num dict
    simples, consumido por `painel_excel.gerar_relatorio` - uma chave por aba."""
    return {
        "comparacao_precos": conciliacao_service.fetch_comparacao_precos(conn, inicio, fim),
        "divergencias": conciliacao_service.fetch_divergencias(conn, inicio, fim),
    }


def _gravar_excel(dados: dict, gerado_em: datetime, inicio: date, fim: date) -> None:
    try:
        gerar_relatorio(dados, PAINEL_XLSX, gerado_em, inicio, fim)
    except PermissionError as exc:
        raise BusinessException(
            "painel - planilha aberta no Excel, gravacao adiada"
        ) from exc
    except OSError as exc:
        raise CrawlerException("painel - falha ao gravar planilha") from exc


def _fechar(conn) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        log.warning("painel - falha ao fechar conexao", exc_info=True)
