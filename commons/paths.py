"""Diretórios do projeto, resolvidos num lugar só.

Antes cada módulo recalculava a raiz com `Path(__file__).resolve().parent.parent`,
o que amarra o caminho à profundidade do arquivo: mover um módulo de pasta fazia
os diretórios apontarem para o lugar errado, em silêncio. Aqui a raiz é derivada
uma vez e todo mundo importa daqui.
"""

from __future__ import annotations

from pathlib import Path

# utils/paths.py -> utils/ -> projeto_schiavon_agente/
ROOT = Path(__file__).resolve().parent.parent

ENV_PATH = ROOT / ".env"

FILES_DIR = ROOT / "files"

# Etapa 1 — invoices
DOWNLOAD_DIR = FILES_DIR / "unprocessed_files"   # baixadas, ainda não lidas
READ_DIR = FILES_DIR / "read_files"              # já lidas e persistidas

# Etapa 2 — cotação
PRICE_QUOTE_DIR = FILES_DIR / "price_quote"      # entrada do importador manual
OUTBOUND_DIR = FILES_DIR / "quotation_outbound"  # planilhas geradas por fornecedor

# Etapa 3 — conciliação
SINONIMOS_DIR = FILES_DIR / "sinonimos"          # planilha DE-PARA de vocabulário de item

# Migrações e DDL
SQL_DIR = ROOT / "sql"

# Painel operacional (relatório do cliente, sobrescrito a cada execução)
REPORTS_DIR = FILES_DIR / "relatorios"
PAINEL_XLSX = REPORTS_DIR / "painel_operacao.xlsx"
