"""Testes do gerador de .docx (commons/docx_report.py).

    python -m pytest tests/test_docx_report.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commons.docx_report import _limpar, gerar_com_seguranca, gerar_relatorio  # noqa: E402


def test_limpar_remove_controle_e_preserva_tab_e_quebra():
    assert _limpar("a\x00b\x0bc\x1fd\te\nf") == "abcd\te\nf"


def test_relatorio_com_caractere_de_controle_nao_levanta(tmp_path):
    caminho = gerar_relatorio(
        tmp_path / "r.docx", "Titulo\x00", ["cab\x0b"],
        [("Secao", ["Col"], [("val\x1f",)])],
    )
    assert caminho.exists()


def test_gerar_com_seguranca_engole_value_error():
    def gerador():
        raise ValueError("xml invalido")

    assert gerar_com_seguranca(gerador, "teste") == (None, True)
