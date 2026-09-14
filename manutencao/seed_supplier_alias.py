"""Monta o de-para entre o fornecedor da invoice e o cadastro em meat_suppliers.

Duas etapas, de propósito:

    python -m manutencao.seed_supplier_alias
        Semeia source='quote' direto de meat_suppliers (1-para-1, sem revisão) e
        gera files/supplier_alias_candidatos.csv com as sugestões para invoice.

    python -m manutencao.seed_supplier_alias --aplicar
        Lê o CSV revisado e grava os aliases aprovados.

O CSV passa por revisão humana porque nome de fornecedor não é confiável para
decidir sozinho: 'Prime Meats' (carne) e 'Prime Distribution USA' (mercearia)
são empresas diferentes, e o nome na invoice é texto cru lido do PDF — as três
grafias de 'FreshPoint Central FL' convivem no banco.

Para aprovar: abra o CSV, ponha 'x' na coluna `aprovar` das linhas corretas
(corrija `canonical_name` se a sugestão estiver errada) e rode com --aplicar.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conciliacao.matcher import match_supplier, norm_supplier  # noqa: E402
from conciliacao.conciliacao_db import (  # noqa: E402
    marcar_reprocesso_por_fornecedor,
    save_supplier_alias,
)
from utils.connection import connect_db, load_env  # noqa: E402

from utils.paths import ENV_PATH, FILES_DIR  # noqa: E402

CSV_PATH = FILES_DIR / "supplier_alias_candidatos.csv"

CAMPOS = ["aprovar", "alias", "alias_norm", "canonical_name", "canonical_id",
          "score", "ocorrencias"]


def _fornecedores_cadastrados(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, nome AS name FROM dwschiavon2.dim_fornecedor"
                    " WHERE ativo ORDER BY nome")
        return [{"id": r[0], "name": r[1]} for r in cur.fetchall()]


def _fornecedores_das_invoices(conn) -> list[tuple[str, int]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT fornecedor_lido AS supplier_name, count(*)"
            " FROM dwschiavon2.fat_invoice"
            " WHERE supplier_name IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1"
        )
        return [(r[0], r[1]) for r in cur.fetchall()]


def gerar_csv(conn) -> int:
    """Semeia o lado 'quote' e escreve o CSV de candidatos do lado 'invoice'."""
    cadastrados = _fornecedores_cadastrados(conn)
    nomes = [f["name"] for f in cadastrados]
    por_nome = {f["name"]: f["id"] for f in cadastrados}

    # Lado cotação: o nome do cadastro é o próprio alias. Não precisa revisão.
    for fornecedor in cadastrados:
        save_supplier_alias(
            conn, fornecedor["id"], fornecedor["name"],
            fornecedor["name"], norm_supplier(fornecedor["name"]), "quote",
        )
    print(f"  ✓ {len(cadastrados)} alias de cotacao semeados (source='quote')")

    linhas = []
    for nome_invoice, ocorrencias in _fornecedores_das_invoices(conn):
        sugestao, score = match_supplier(nome_invoice, nomes)
        linhas.append({
            "aprovar": "",
            "alias": nome_invoice,
            "alias_norm": norm_supplier(nome_invoice),
            "canonical_name": sugestao or "",
            "canonical_id": por_nome.get(sugestao, "") if sugestao else "",
            "score": f"{score:.1f}",
            "ocorrencias": ocorrencias,
        })

    linhas.sort(key=lambda r: (-float(r["score"]), r["alias"]))

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CAMPOS, delimiter=";")
        writer.writeheader()
        writer.writerows(linhas)

    com_sugestao = sum(1 for r in linhas if r["canonical_name"])
    print(f"  ✓ {len(linhas)} fornecedor(es) de invoice, {com_sugestao} com sugestao")
    print(f"  → {CSV_PATH}")
    return len(linhas)


def aplicar_csv(conn) -> int:
    """Grava os aliases marcados com 'x' na coluna `aprovar`."""
    if not CSV_PATH.exists():
        raise SystemExit(f"CSV nao encontrado: {CSV_PATH}\n"
                         "Rode sem --aplicar primeiro para gera-lo.")

    gravados = 0
    ignorados = 0
    with CSV_PATH.open(newline="", encoding="utf-8-sig") as fh:
        for linha in csv.DictReader(fh, delimiter=";"):
            if (linha.get("aprovar") or "").strip().lower() not in ("x", "s", "sim", "1"):
                ignorados += 1
                continue
            canonical_name = (linha.get("canonical_name") or "").strip()
            if not canonical_name:
                print(f"  ⚠ aprovado sem canonical_name, pulando: {linha['alias']}")
                continue
            canonical_id = (linha.get("canonical_id") or "").strip()
            novo = save_supplier_alias(
                conn,
                int(canonical_id) if canonical_id.isdigit() else None,
                canonical_name,
                linha["alias"],
                linha.get("alias_norm") or norm_supplier(linha["alias"]),
                "invoice",
            )
            estado = "gravado" if novo else "ja existia"
            print(f"  ✓ {linha['alias'][:38]:<40} -> {canonical_name}  ({estado})")
            gravados += 1

    print(f"\n  {gravados} alias aprovado(s), {ignorados} sem marcacao")

    if gravados:
        n = marcar_reprocesso_por_fornecedor(conn)
        if n:
            print(f"  -> {n} nota(s) sem fornecedor remarcada(s); o proximo "
                  "'python main.py' reconcilia")

    return gravados


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aplicar", action="store_true",
                        help="Le o CSV revisado e grava os aliases aprovados.")
    args = parser.parse_args()

    conn = connect_db(load_env(ENV_PATH))
    try:
        # as tabelas ja existem: sao criadas pelo DDL do dwschiavon2
        print(f"\n{'='*60}")
        print("De-para de fornecedores" + ("  [aplicando CSV]" if args.aplicar
                                           else "  [gerando candidatos]"))
        print(f"{'='*60}")

        if args.aplicar:
            aplicar_csv(conn)
        else:
            gerar_csv(conn)
            print("\n  Proximo passo: abra o CSV, marque 'x' em `aprovar` nas linhas")
            print("  corretas e rode: python -m manutencao.seed_supplier_alias --aplicar")
        print(f"{'='*60}\n")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
