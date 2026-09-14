"""Bootstrap do de-para de vocabulário de item — dwschiavon2.dim_item_sinonimo.

Estas entradas viviam hardcoded em `conciliacao/matcher.py` (`_SINONIMOS_ITEM`).
Este script é a **única cópia que sobra no repo** e existe só para a migração:
carrega as linhas no banco e grava `files/sinonimos/DE_PARA_ITENS.xlsx` como
planilha viva. Daí em diante o time edita a planilha e o pipeline sincroniza a
cada execução (`conciliacao/sinonimos.py`).

    python -m manutencao.seed_item_sinonimos            # dry-run: mostra e gera o .xlsx
    python -m manutencao.seed_item_sinonimos --aplicar  # + grava no banco

RACIONAL (herdado do comentário do matcher). Três origens de divergência entre
os dois lados da comparação:

    abreviação de mercado   a invoice escreve 'CHIX', a cotação 'CHICKEN'
    tradução                a cotação traz o corte em inglês E em português
                            ('CHICKEN - Tender / Sassami de Frango')
    sinônimo de corte       'ALCATRA' é o 'SIRLOIN' do outro lado

Mapeia-se sempre PARA o termo em inglês, que é o que a invoice usa. Aplicado aos
dois lados: `token_set_ratio` trabalha com conjunto, então token repetido não
penaliza. Errar para menos é seguro (o item só deixa de casar); errar para mais
casa produtos diferentes.

NÃO incluir 'PEITO': é ambíguo entre os dois lados do cardápio. 'Peito de Frango'
é BREAST, 'Maçã de Peito' é BRISKET — as duas grafias existem em base. Um token
ambíguo casa o produto errado, que é o erro caro aqui.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl  # noqa: E402

from conciliacao.matcher import norm_text  # noqa: E402
from conciliacao.sinonimos import Sinonimo  # noqa: E402
from conciliacao.conciliacao_db import upsert_item_sinonimos  # noqa: E402
from utils.connection import connect_db, load_env  # noqa: E402
from utils.paths import ENV_PATH, SINONIMOS_DIR  # noqa: E402

XLSX_PATH = SINONIMOS_DIR / "DE_PARA_ITENS.xlsx"

# Entradas originais de _SINONIMOS_ITEM (conciliacao/matcher.py).
_DE_PARA: dict[str, str] = {
    # proteína
    "CHIX": "CHICKEN",
    "CHKN": "CHICKEN",
    "FRANGO": "CHICKEN",
    "BOI": "BEEF",
    "PORCO": "PORK",
    "SUINO": "PORK",
    # cortes e miúdos
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


def _linhas() -> list[Sinonimo]:
    return [
        Sinonimo(termo=de, termo_norm=norm_text(de), canonico=norm_text(para))
        for de, para in _DE_PARA.items()
    ]


def _gravar_planilha(rows: list[Sinonimo]) -> None:
    SINONIMOS_DIR.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "DE-PARA"
    ws.append(["DE", "PARA"])
    for s in rows:
        ws.append([s.termo, s.canonico])
    wb.save(XLSX_PATH)
    print(f"  ✓ planilha viva gerada: {XLSX_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--aplicar", action="store_true",
                        help="Grava os sinônimos no banco (sem a flag é dry-run).")
    args = parser.parse_args()

    rows = _linhas()

    print(f"\n{'='*60}")
    print("Seed de sinônimos de item" + ("  [aplicando]" if args.aplicar
                                         else "  [dry-run]"))
    print(f"{'='*60}")
    for s in rows:
        print(f"  {s.termo_norm:<12} -> {s.canonico}")
    print(f"  {len(rows)} entrada(s)")

    _gravar_planilha(rows)

    if args.aplicar:
        conn = connect_db(load_env(ENV_PATH))
        try:
            n = upsert_item_sinonimos(conn, rows)
        finally:
            conn.close()
        print(f"  ✓ {n} sinônimo(s) gravado(s) em dwschiavon2.dim_item_sinonimo")
    else:
        print("\n  Próximo passo: python -m manutencao.seed_item_sinonimos --aplicar")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
