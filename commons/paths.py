"""Diretórios do projeto, resolvidos num lugar só.

Antes cada módulo recalculava a raiz com `Path(__file__).resolve().parent.parent`,
o que amarra o caminho à profundidade do arquivo: mover um módulo de pasta fazia
os diretórios apontarem para o lugar errado, em silêncio. Aqui a raiz é derivada
uma vez e todo mundo importa daqui.
"""

from __future__ import annotations

from pathlib import Path

# commons/paths.py -> commons/ -> projeto_schiavon_agente/
ROOT = Path(__file__).resolve().parent.parent

# Segredos locais, nunca versionados — ver .gitignore ("resources/config-*.env").
# Um profile por ambiente (`config-dev.env`, `config-prod.env`); nao existe
# arquivo generico. O ativo sai de `RPA_ENV` (default `prod`).
RESOURCES_DIR = ROOT / "resources"

AMBIENTES = ("dev", "prod")
AMBIENTE_PADRAO = "prod"


def profile_path(ambiente: str) -> Path:
    """Caminho do profile de um ambiente: `resources/config-<ambiente>.env`."""
    return RESOURCES_DIR / f"config-{ambiente}.env"


FILES_DIR = ROOT / "files"

# Etapa 1 — invoices
DOWNLOAD_DIR = FILES_DIR / "unprocessed_files"   # baixadas, ainda não lidas
READ_DIR = FILES_DIR / "read_files"              # já lidas e persistidas

# Etapa 2 — cotação
PRICE_QUOTE_DIR = FILES_DIR / "price_quote"      # entrada do importador manual
OUTBOUND_DIR = FILES_DIR / "quotation_outbound"  # planilhas geradas por fornecedor

# Migrações e DDL
SQL_DIR = ROOT / "sql"

# Painel operacional (relatório do cliente, sobrescrito a cada execução)
REPORTS_DIR = FILES_DIR / "relatorios"
PAINEL_XLSX = REPORTS_DIR / "painel_operacao.xlsx"

# Relatório .docx por invoice com divergência (FLUXO 4 — reconcile_erp_flow)
DIVERGENCIAS_ERP_DIR = REPORTS_DIR / "divergencias_erp"

# Relatório .docx por invoice 100% conciliada (mesmo fluxo; exclusivo com o acima)
SUCESSOS_ERP_DIR = REPORTS_DIR / "sucessos_erp"

# OAuth Gmail — leitura do código do Cloudflare Access (login no Catapult)
GMAIL_CLIENT_SECRET = RESOURCES_DIR / "gmail" / "client_secret.json"
GMAIL_TOKEN_PATH = RESOURCES_DIR / "gmail" / "token.json"

# Service Account — Google Sheets (planilha De-Para/Pendentes de sinônimo de item)
GOOGLE_SERVICE_ACCOUNT_PATH = RESOURCES_DIR / "google" / "service_account.json"
