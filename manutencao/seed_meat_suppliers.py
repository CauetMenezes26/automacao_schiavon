"""Cadastra em meat_suppliers os fornecedores de carne que aparecem nas invoices.

    python -m manutencao.seed_meat_suppliers            (mostra o que faria)
    python -m manutencao.seed_meat_suppliers --aplicar

Por que existe: meat_suppliers tinha 4 registros, dois inativos e dois de teste,
e nenhum dos fornecedores de carne reais estava lá. Sem eles, supplier_alias não
tem destino e a conciliação não sai do lugar.

Os registros nascem INATIVOS de propósito: `active = TRUE` faz o ciclo semanal
começar a mandar WhatsApp para o fornecedor, e ainda não há contato cadastrado.
Preencha e-mail/WhatsApp e ative quando quiser passar a cobrar cotação deles.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commons.db import connect_db  # noqa: E402
from domain.config import carregar_config  # noqa: E402


# Fornecedores de carne identificados nas invoices já lidas (invoice_header).
FORNECEDORES = [
    ("Eastern Quality Foods", "Carne — identificado nas invoices"),
    ("Prime Meats", "Carne — nao confundir com 'Prime Distribution USA' (mercearia)"),
    ("Kelly's Foods, Inc.", "Carne — identificado nas invoices"),
    ("RAMAX, LLC", "Carne — identificado nas invoices"),
    ("MEAT DEPOT PHILADELPHIA", "Carne — identificado nas invoices"),
]


def seed(conn, aplicar: bool) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT lower(nome) FROM dwschiavon2.dim_fornecedor")
        existentes = {r[0] for r in cur.fetchall()}

    novos = 0
    for nome, notas in FORNECEDORES:
        if nome.lower() in existentes:
            print(f"  = ja existe: {nome}")
            continue
        if not aplicar:
            print(f"  + inseriria: {nome}")
            novos += 1
            continue
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dwschiavon2.dim_fornecedor
                    (nome, canal, ativo, cotado, notas)
                VALUES (%s, 'whatsapp', FALSE, TRUE, %s)
                RETURNING id
                """,
                (nome, notas),
            )
            novo_id = cur.fetchone()[0]
        conn.commit()
        print(f"  + inserido (inativo): {nome}  id={novo_id}")
        novos += 1
    return novos


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aplicar", action="store_true",
                        help="Grava no banco. Sem a flag, so mostra o que faria.")
    args = parser.parse_args()

    conn = connect_db(carregar_config().banco)
    try:
        print(f"\n{'='*60}")
        print("Cadastro de fornecedores de carne"
              + ("" if args.aplicar else "   [SIMULACAO — use --aplicar]"))
        print(f"{'='*60}")

        novos = seed(conn, args.aplicar)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT nome AS name, ativo AS active FROM dwschiavon2.dim_fornecedor"
                " WHERE NOT ativo ORDER BY nome"
            )
            inativos = cur.fetchall()

        print(f"\n  {novos} fornecedor(es) {'inserido(s)' if args.aplicar else 'a inserir'}")
        if inativos:
            print("\n  Inativos (nao recebem cotacao ate serem ativados):")
            for nome, _ in inativos:
                print(f"    - {nome}")
            print("\n  Para ativar, preencha o contato e rode:")
            print("    UPDATE dwschiavon2.dim_fornecedor SET email = ..., whatsapp = ...,")
            print("           active = TRUE WHERE name = '<nome>';")
        print(f"{'='*60}\n")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
