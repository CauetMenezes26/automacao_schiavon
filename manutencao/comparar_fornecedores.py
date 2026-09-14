"""Compara os precos cotados entre os fornecedores de um ciclo.

    python -m manutencao.comparar_fornecedores            ciclo aberto
    python -m manutencao.comparar_fornecedores --ciclo 27

E relatorio de apoio a compra, nao etapa do pipeline: nao grava nada e nao
depende das invoices. Por isso vive aqui e nao no FLUXO 2.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cotacao.quotation import compare_prices  # noqa: E402
from utils.connection import load_env  # noqa: E402
from utils.paths import ENV_PATH  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ciclo", type=int, default=None,
                        help="id do quotation_request. Sem a flag, usa o ciclo aberto.")
    args = parser.parse_args()

    compare_prices(load_env(ENV_PATH), request_id=args.ciclo)


if __name__ == "__main__":
    main()
