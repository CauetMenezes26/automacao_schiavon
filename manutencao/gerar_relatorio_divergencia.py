"""Gera o relatório .docx de divergência (frente ERP) a partir de dado real
do banco — não roda o fluxo inteiro (sem login no Catapult, sem gravar nada),
só lê uma invoice já conciliada com divergência e monta o documento.

    python -m manutencao.gerar_relatorio_divergencia            mais recente
    python -m manutencao.gerar_relatorio_divergencia --invoice 123
"""

from __future__ import annotations

import argparse

from commons.db import connect_db
from domain.config import carregar_config
from conciliacao.relatorio_divergencia import gerar_relatorio_divergencia_erp
from domain.service.conciliacao_service import fetch_divergencia_erp_para_relatorio


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--invoice", type=int, default=None, dest="id_invoice",
                         help="id_invoice específico; sem a flag, pega a divergência mais recente")
    args = parser.parse_args()

    conn = connect_db(carregar_config().banco)
    try:
        achado = fetch_divergencia_erp_para_relatorio(conn, id_invoice=args.id_invoice)
    finally:
        conn.close()

    if achado is None:
        print("Nenhuma invoice com divergência ERP encontrada no banco.")
        return

    header, resultado = achado
    caminho = gerar_relatorio_divergencia_erp(header, resultado)
    print(f"Invoice {header.get('invoice_number')} (id={header['id']}) — "
          f"{len(resultado['items'])} item(ns) divergente(s)")
    print(f"Salvo em: {caminho}")


if __name__ == "__main__":
    main()
