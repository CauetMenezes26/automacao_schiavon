"""Monta o de-para entre o fornecedor lido na invoice e o nome/prefixo que o
Catapult usa (`dim_fornecedor_alias.origem='erp'`, povoado por
`manutencao/coletar_e_gravar_nomes_erp.py` — coluna `Name` da tela
Worksheets, cortada no primeiro `'-'`).

Duas etapas, de propósito (mesmo padrão de `seed_supplier_alias.py`, que faz
isso pro lado 'cotacao'/carne):

    python -m manutencao.seed_erp_supplier_alias
        Sugere, pra cada fornecedor distinto já lido de invoice
        (`fat_invoice.nome_fornecedor`), o alias 'erp' mais parecido (fuzzy,
        `commons.matcher.match_supplier`) e escreve
        files/erp_supplier_alias_candidatos.csv pra revisão humana.

    python -m manutencao.seed_erp_supplier_alias --aplicar
        Lê o CSV revisado e grava os aliases 'invoice' aprovados, apontando
        pro MESMO `id_fornecedor` do alias 'erp' escolhido — é isso que liga
        os dois lados.

O CSV passa por revisão humana pelo mesmo motivo do de-para de carne: nome
parecido não decide sozinho (`commons/matcher.py::match_supplier` já avisa
isso na docstring — ex. real: 'Prime Distribution USA' e 'Prime Meats'
pontuam alto por aproximação e são empresas diferentes). Aqui o risco é
simétrico: o prefixo do Catapult é truncado (ex. 'Mena Impor'), então dois
fornecedores de nome parecido no início podem colidir por coincidência.

Para aprovar: abra o CSV, ponha 'x' na coluna `aprovar` das linhas corretas
e rode com --aplicar.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commons.db import connect_db  # noqa: E402
from domain.config import carregar_config  # noqa: E402
from commons.matcher import match_supplier, norm_supplier  # noqa: E402
from commons.paths import FILES_DIR  # noqa: E402
from domain.service.conciliacao_service import save_supplier_alias  # noqa: E402

CSV_PATH = FILES_DIR / "erp_supplier_alias_candidatos.csv"

CAMPOS = ["aprovar", "alias_invoice", "alias_invoice_norm", "erp_nome",
          "id_fornecedor", "score", "ocorrencias"]


def _fornecedores_erp(conn) -> list[tuple[int, str]]:
    """`(id_fornecedor, nome)` de cada alias `origem='erp'` ativo."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT df.id, df.nome
                 FROM dwschiavon2.dim_fornecedor_alias dfa
                 JOIN dwschiavon2.dim_fornecedor df ON df.id = dfa.id_fornecedor
                WHERE dfa.origem = 'erp' AND dfa.ativo
                ORDER BY df.nome"""
        )
        return [(r[0], r[1]) for r in cur.fetchall()]


def _fornecedores_das_invoices(conn) -> list[tuple[str, int]]:
    """`(nome_fornecedor, ocorrencias)` distintos, mais frequente primeiro."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT nome_fornecedor, count(*)
                 FROM dwschiavon2.fat_invoice
                WHERE nome_fornecedor IS NOT NULL
                GROUP BY 1 ORDER BY 2 DESC, 1"""
        )
        return [(r[0], r[1]) for r in cur.fetchall()]


def _alias_norms_ja_resolvidos(conn) -> set[str]:
    """`alias_norm` já cadastrado em `origem='invoice'` — não entra no CSV.

    Sem isto, aprovar uma sugestão pra um fornecedor que já tem alias
    'invoice' de carne (ex. 'RAMAX, LLC', 'Prime Meats' — já resolvidos pelo
    de-para antigo, `manutencao/seed_supplier_alias.py`) REPONTA
    `id_fornecedor` pro novo cadastro 'erp' (`ON CONFLICT ... DO UPDATE` de
    `save_supplier_alias`), perdendo o vínculo com o cadastro de carne de
    verdade (contato, categoria='carne', usado na cotação semanal). Ligar
    esses fornecedores já resolvidos a um alias 'erp' é trabalho separado —
    tem que apontar pro `id_fornecedor` deles, não criar um novo."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT alias_norm FROM dwschiavon2.dim_fornecedor_alias
                WHERE origem = 'invoice' AND ativo"""
        )
        return {r[0] for r in cur.fetchall()}


def gerar_csv(conn) -> int:
    candidatos = _fornecedores_erp(conn)
    nomes = [nome for _id, nome in candidatos]
    por_nome = {nome: id_ for id_, nome in candidatos}
    ja_resolvidos = _alias_norms_ja_resolvidos(conn)

    linhas = []
    pulados = 0
    for nome_fornecedor, ocorrencias in _fornecedores_das_invoices(conn):
        if norm_supplier(nome_fornecedor) in ja_resolvidos:
            pulados += 1
            continue
        sugestao, score = match_supplier(nome_fornecedor, nomes)
        linhas.append({
            "aprovar": "",
            "alias_invoice": nome_fornecedor,
            "alias_invoice_norm": norm_supplier(nome_fornecedor),
            "erp_nome": sugestao or "",
            "id_fornecedor": por_nome.get(sugestao, "") if sugestao else "",
            "score": f"{score:.1f}",
            "ocorrencias": ocorrencias,
        })

    linhas.sort(key=lambda r: (-float(r["score"]), r["alias_invoice"]))

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CAMPOS, delimiter=";")
        writer.writeheader()
        writer.writerows(linhas)

    com_sugestao = sum(1 for r in linhas if r["erp_nome"])
    print(f"  {len(candidatos)} alias 'erp' cadastrado(s) pra sugerir contra")
    print(f"  {pulados} fornecedor(es) ja resolvido(s) (alias 'invoice' existente), pulado(s)")
    print(f"  {len(linhas)} fornecedor(es) de invoice no CSV, {com_sugestao} com sugestao")
    print(f"  -> {CSV_PATH}")
    return len(linhas)


def aplicar_csv(conn) -> int:
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
            id_fornecedor = (linha.get("id_fornecedor") or "").strip()
            erp_nome = (linha.get("erp_nome") or "").strip()
            if not id_fornecedor or not erp_nome:
                print(f"  ! aprovado sem id_fornecedor/erp_nome, pulando: {linha['alias_invoice']}")
                continue

            alias_invoice = linha["alias_invoice"]
            alias_norm = linha.get("alias_invoice_norm") or norm_supplier(alias_invoice)
            novo = save_supplier_alias(
                conn, int(id_fornecedor), erp_nome, alias_invoice, alias_norm, "invoice",
            )
            estado = "gravado" if novo else "ja existia"
            print(f"  {alias_invoice[:38]:<40} -> {erp_nome}  ({estado})")
            gravados += 1

    print(f"\n  {gravados} alias aprovado(s), {ignorados} sem marcacao")
    return gravados


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aplicar", action="store_true",
                         help="Le o CSV revisado e grava os aliases aprovados.")
    args = parser.parse_args()

    config = carregar_config()
    conn = connect_db(config.banco)
    try:
        print(f"\n{'='*60}")
        print("De-para invoice -> Catapult" + ("  [aplicando CSV]" if args.aplicar
                                                else "  [gerando candidatos]"))
        print(f"{'='*60}")

        if args.aplicar:
            aplicar_csv(conn)
        else:
            gerar_csv(conn)
            print("\n  Proximo passo: abra o CSV, marque 'x' em `aprovar` nas linhas")
            print("  corretas e rode: python -m manutencao.seed_erp_supplier_alias --aplicar")
        print(f"{'='*60}\n")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
