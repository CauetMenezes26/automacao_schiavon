"""Spec coleta-arquivos-soltos: so arquivo solto na pasta da semana conta."""

from crawler.flow.invoices_flow import _contar_arquivos


def test_subpastas_nao_contam_como_arquivo_encontrado():
    entries = [
        {"name": "LANCADAS", "type": "pasta"},
        {"name": "PENDENCIAS", "type": "pasta"},
    ]
    assert _contar_arquivos(entries) == 0


def test_pasta_da_semana_sem_fallback_para_a_semana_anterior():
    """A semana anterior e varrida por conta propria (R4); o fallback gravava
    os arquivos dela no caso da semana atual (R7)."""
    from datetime import date

    from commons.sharepoint import resolve_week_folder

    pastas = [{"name": "21 A 27", "type": "pasta"}]
    assert resolve_week_folder(pastas, date(2026, 9, 25))["name"] == "21 A 27"
    assert resolve_week_folder(pastas, date(2026, 10, 2)) is None


def test_conta_so_os_arquivos_soltos():
    entries = [
        {"name": "LANCADAS", "type": "pasta"},
        {"name": "nota1.pdf", "type": "arquivo"},
        {"name": "nota2.pdf", "type": "arquivo"},
    ]
    assert _contar_arquivos(entries) == 2
