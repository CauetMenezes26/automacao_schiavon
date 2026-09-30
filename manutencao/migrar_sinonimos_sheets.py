"""Migração única: de-para de vocabulário de item -> aba De-Para (Google Sheets).

Essas 21 entradas viviam em `files/sinonimos/DE_PARA_ITENS.xlsx` (a planilha
local que o time editava até aqui, gerada originalmente por
`manutencao/seed_item_sinonimos.py`, hoje removido). O de-para virou Google
Sheets — este script escreve essas mesmas linhas na aba `De-Para` da planilha
configurada em `SINONIMOS_SHEET_ID`, uma vez, pra não perder o vocabulário já
curado na migração.

    python -m manutencao.migrar_sinonimos_sheets            # dry-run: só mostra
    python -m manutencao.migrar_sinonimos_sheets --aplicar  # grava na aba De-Para

Depois de confirmar (rode `python -m manutencao.importar_sinonimos` e confira
que os 21 sinônimos aparecem em `dim_item_sinonimo`), pode apagar
`files/sinonimos/DE_PARA_ITENS.xlsx`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from domain.config import carregar_config  # noqa: E402
from commons.sheets import append_values, read_values  # noqa: E402

# Conteúdo integral de files/sinonimos/DE_PARA_ITENS.xlsx no momento da
# migração (conferido linha a linha nesta sessão).
_DE_PARA: dict[str, str] = {
    "CHIX": "CHICKEN",
    "CHKN": "CHICKEN",
    "FRANGO": "CHICKEN",
    "BOI": "BEEF",
    "PORCO": "PORK",
    "SUINO": "PORK",
    "SASSAMI": "TENDER",
    "RABO": "OXTAIL",
    "ALCATRA": "SIRLOIN",
    "BARRIGA": "BELLY",
    "PATINHO": "KNUCKLE",
    "MAMINHA": "TRI TIP",
    "PICANHA": "COULOTTE",
    "ACEM": "CHUCK",
    "SOBRECOXA": "THIGHS",
    "CORACAO": "HEARTS",
    "MOCOTO": "FEET",
    "FIGADO": "LIVER",
    "BUCHO": "TRIPE",
    "COSTELA": "RIBS",
    "FRALDINHA": "FLANK",
}

_RANGE_HEADER = "De-Para!A1:B1"
_RANGE_DE_PARA = "De-Para!A:B"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--aplicar", action="store_true",
                        help="Grava na aba De-Para (sem a flag é dry-run).")
    args = parser.parse_args()

    config = carregar_config()
    sheet_id = config.sinonimos_sheet_id or None
    if not sheet_id:
        raise SystemExit("SINONIMOS_SHEET_ID ausente no profile")

    print(f"\n{'='*60}")
    print("Migração de-para de item -> Google Sheets" +
          ("  [aplicando]" if args.aplicar else "  [dry-run]"))
    print(f"  planilha: {sheet_id}")
    print(f"{'='*60}")
    for de, para in _DE_PARA.items():
        print(f"  {de:<12} -> {para}")
    print(f"  {len(_DE_PARA)} entrada(s)")

    existentes = read_values(sheet_id, _RANGE_DE_PARA)
    if not args.aplicar:
        print(f"\n  Aba 'De-Para' tem hoje {len(existentes)} linha(s) "
              "(cabeçalho incluso, se houver).")
        print("  Próximo passo: python -m manutencao.migrar_sinonimos_sheets --aplicar")
        print(f"{'='*60}\n")
        return

    linhas: list[list[str]] = []
    if not existentes:
        linhas.append(["DE", "PARA"])
    linhas.extend([de, para] for de, para in _DE_PARA.items())

    append_values(sheet_id, _RANGE_DE_PARA, linhas)
    print(f"\n  ✓ {len(_DE_PARA)} sinônimo(s) gravado(s) na aba 'De-Para'")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
