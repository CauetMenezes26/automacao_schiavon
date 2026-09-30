"""Testes de `conciliacao/relatorio_sucesso.py` e `commons/docx_report.py::gerar_com_seguranca`.

Sem banco nem Catapult — `header`/`resultado` são dicts fake no formato de
`reconcile_items_against_po`. Os diretórios de saída são redirecionados para
`tmp_path`.

    python -m pytest tests/test_relatorio_sucesso.py -v
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from docx import Document  # noqa: E402

from commons.docx_report import DocxReportError, gerar_com_seguranca  # noqa: E402
from conciliacao import relatorio_divergencia, relatorio_sucesso  # noqa: E402
from domain.conciliacao_codes import IssueCode  # noqa: E402

_HEADER = {
    "id": 7, "invoice_number": "1001", "supplier_name": "Cheney Brothers",
    "invoice_date": "2026-09-09", "id_loja": 1,
}


def _item(ordem: int, **overrides) -> dict:
    base = {
        "item_order": ordem, "description_invoice": f"ITEM {ordem}",
        "item_name_po": f"Item PO {ordem}", "qty_invoice": Decimal("4"),
        "qty_po": Decimal("4"), "price_invoice": Decimal("18.5"),
        "total_invoice": Decimal("74"), "total_po": Decimal("74"),
        "issue_codes": [], "has_issue": False, "needs_review": False,
    }
    base.update(overrides)
    return base


def _resultado(*itens: dict, header_codes: tuple = ()) -> dict:
    return {
        "items": list(itens), "issue_codes": list(header_codes),
        "has_issue": any(i["has_issue"] for i in itens) or bool(header_codes),
        "needs_review": False, "po_orphans": [],
    }


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(relatorio_sucesso, "SUCESSOS_ERP_DIR", tmp_path / "sucessos")
    monkeypatch.setattr(relatorio_divergencia, "DIVERGENCIAS_ERP_DIR", tmp_path / "divergencias")
    return tmp_path


def test_nota_toda_ok_gera_so_o_relatorio_de_sucesso(tmp_path):
    resultado = _resultado(_item(1), _item(2))

    sucesso = relatorio_sucesso.gerar_relatorio_sucesso_erp(_HEADER, resultado)
    divergencia = relatorio_divergencia.gerar_relatorio_divergencia_erp(_HEADER, resultado)

    assert sucesso == tmp_path / "sucessos" / "sucesso_7_1001.docx"
    assert sucesso.exists()
    assert divergencia is None

    tabela = Document(sucesso).tables[0]
    assert [c.text for c in tabela.rows[0].cells][:3] == ["Item", "Descrição Invoice", "Descrição PO"]
    assert len(tabela.rows) == 3  # cabeçalho + 2 itens
    assert [c.text for c in tabela.rows[1].cells] == [
        "1", "ITEM 1", "Item PO 1", "4.00", "4.00", "18.50", "74.00", "74.00",
    ]


def test_um_item_divergente_tira_a_nota_inteira_do_sucesso(tmp_path):
    divergente = _item(
        2, qty_po=Decimal("3"), qty_diff=Decimal("1"),
        issue_codes=[IssueCode.QTY_MISMATCH_PO], has_issue=True,
    )
    resultado = _resultado(_item(1), divergente)

    assert relatorio_sucesso.gerar_relatorio_sucesso_erp(_HEADER, resultado) is None
    assert relatorio_divergencia.gerar_relatorio_divergencia_erp(_HEADER, resultado) is not None
    assert not (tmp_path / "sucessos").exists()


def test_nota_sem_po_nao_gera_sucesso():
    resultado = _resultado(_item(1), header_codes=(IssueCode.PO_NAO_ENCONTRADA,))
    assert relatorio_sucesso.gerar_relatorio_sucesso_erp(_HEADER, resultado) is None


def test_nota_sem_itens_nao_gera_arquivo(tmp_path):
    assert relatorio_sucesso.gerar_relatorio_sucesso_erp(_HEADER, _resultado()) is None
    assert not (tmp_path / "sucessos").exists()


def test_sem_numero_de_nota_usa_id_do_header(tmp_path):
    header = {**_HEADER, "invoice_number": None}
    caminho = relatorio_sucesso.gerar_relatorio_sucesso_erp(header, _resultado(_item(1)))
    assert caminho.name == "sucesso_7_sem_numero.docx"


def test_numero_com_barra_nao_cria_subpasta(tmp_path):
    header = {**_HEADER, "invoice_number": "AB/12 3"}
    caminho = relatorio_sucesso.gerar_relatorio_sucesso_erp(header, _resultado(_item(1)))
    assert caminho == tmp_path / "sucessos" / "sucesso_7_AB_12_3.docx"


def test_mesmo_numero_em_notas_diferentes_nao_sobrescreve():
    a = relatorio_sucesso.gerar_relatorio_sucesso_erp(_HEADER, _resultado(_item(1)))
    b = relatorio_sucesso.gerar_relatorio_sucesso_erp({**_HEADER, "id": 8}, _resultado(_item(1)))
    assert a != b and a.exists() and b.exists()


def test_gerar_com_seguranca_devolve_o_caminho(tmp_path):
    alvo = tmp_path / "x.docx"
    assert gerar_com_seguranca(lambda: alvo, "teste") == (alvo, False)


def test_gerar_com_seguranca_distingue_nada_a_gerar():
    assert gerar_com_seguranca(lambda: None, "teste") == (None, False)


def test_gerar_com_seguranca_engole_docx_report_error():
    def falha():
        raise DocxReportError("disco cheio")

    assert gerar_com_seguranca(falha, "teste") == (None, True)


def test_gerar_com_seguranca_engole_value_error():
    def texto_invalido():
        raise ValueError("caractere invalido vindo do Vision")

    assert gerar_com_seguranca(texto_invalido, "teste") == (None, True)


def test_gerar_com_seguranca_nao_engole_outros_erros():
    def bug():
        raise RuntimeError("bug de verdade")

    with pytest.raises(RuntimeError):
        gerar_com_seguranca(bug, "teste")
