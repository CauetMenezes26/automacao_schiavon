"""Força a sincronização da aba De-Para (Google Sheets) à mão — conferência.

O caminho normal é automático: o pipeline (`main.py`) sincroniza a aba
`De-Para` da planilha configurada em `SINONIMOS_SHEET_ID` no início de cada
execução (FLUXO 1). Este script serve para conferir a planilha antes de um
ciclo ou forçar a carga fora do pipeline.

    python -m manutencao.importar_sinonimos            # dry-run: só mostra o relatório
    python -m manutencao.importar_sinonimos --aplicar  # grava em dim_item_sinonimo
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commons.db import connect_db  # noqa: E402
from domain.config import carregar_config  # noqa: E402
from conciliacao.sinonimos import sincronizar  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--aplicar", action="store_true",
                        help="Grava no banco (sem a flag é dry-run).")
    args = parser.parse_args()

    config = carregar_config()
    sheet_id = config.sinonimos_sheet_id or None

    print(f"\n{'='*60}")
    print("De-para de sinônimos de item" + ("  [aplicando]" if args.aplicar
                                            else "  [dry-run]"))
    print(f"  origem: aba 'De-Para' da planilha {sheet_id or '(SINONIMOS_SHEET_ID ausente)'}")
    print(f"{'='*60}")

    conn = connect_db(config.banco)
    try:
        relatorio = sincronizar(conn, sheet_id, aplicar=args.aplicar)
    finally:
        conn.close()

    if relatorio is None:
        print("  Nada sincronizado — confira SINONIMOS_SHEET_ID, a credencial "
              "da Service Account e se a aba 'De-Para' existe com esse nome.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
