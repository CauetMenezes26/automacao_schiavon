"""Importa a planilha DE-PARA de sinônimos de item à mão — conferência / força.

O caminho normal é automático: o pipeline (`main.py`) sincroniza
`files/sinonimos/*.xlsx` no início de cada execução. Este script serve para
conferir a planilha antes de um ciclo ou forçar a carga fora do pipeline.

    python -m manutencao.importar_sinonimos            # dry-run: só mostra o relatório
    python -m manutencao.importar_sinonimos --aplicar  # grava em dim_item_sinonimo
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conciliacao.sinonimos import _sincronizar  # noqa: E402
from utils.connection import connect_db, load_env  # noqa: E402
from utils.paths import ENV_PATH, SINONIMOS_DIR  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--aplicar", action="store_true",
                        help="Grava no banco (sem a flag é dry-run).")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print("De-para de sinônimos de item" + ("  [aplicando]" if args.aplicar
                                            else "  [dry-run]"))
    print(f"  origem: {SINONIMOS_DIR}")
    print(f"{'='*60}")

    conn = connect_db(load_env(ENV_PATH))
    try:
        relatorio = _sincronizar(conn, aplicar=args.aplicar)
    finally:
        conn.close()

    if relatorio is None:
        print("  Nenhuma planilha .xlsx/.xls em files/sinonimos/.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
