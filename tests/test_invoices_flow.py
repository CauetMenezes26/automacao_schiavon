"""Spec coleta-arquivos-soltos: so arquivo solto na pasta da semana conta."""

from crawler.flow.invoices_flow import _contar_arquivos


def test_subpastas_nao_contam_como_arquivo_encontrado():
    entries = [
        {"name": "LANCADAS", "type": "pasta"},
        {"name": "PENDENCIAS", "type": "pasta"},
    ]
    assert _contar_arquivos(entries) == 0


def test_conta_so_os_arquivos_soltos():
    entries = [
        {"name": "LANCADAS", "type": "pasta"},
        {"name": "nota1.pdf", "type": "arquivo"},
        {"name": "nota2.pdf", "type": "arquivo"},
    ]
    assert _contar_arquivos(entries) == 2
