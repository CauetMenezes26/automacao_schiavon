"""Relatório Cotação x Invoice em Excel — visão para o cliente.

Duas abas, o mesmo item visto de dois ângulos:

  1. "Cotacao x Invoice"  — só o que conciliou (preço dentro da tolerância):
     arquivo, fornecedor, item, preço da invoice x preço cotado e a diferença.
  2. "Divergencias ..."   — só o que não fechou: preço fora da tolerância e
     item sem comparação (não achado na cotação / unidade divergente), com
     quantidade, impacto em valor e o motivo.

As duas não se sobrepõem: cada item conciliado aparece em uma aba só.

Sobrescreve `files/relatorios/painel_operacao.xlsx` a cada chamada - é
sempre o estado atual, sem histórico acumulado. Não toca no banco: recebe os
dados já buscados pelo `painel_flow`
(`domain/service/conciliacao_service::fetch_*`) e só formata.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.worksheet import Worksheet

__all__ = ["gerar_relatorio", "ABA_COMPARACAO", "ABA_DIVERGENCIA"]

# Nome das abas. Excel corta em 31 caracteres — "Divergencias Cotacao x Invoice"
# tem 30, e e por isso que nao leva acento nem "de".
ABA_COMPARACAO = "Cotacao x Invoice"
ABA_DIVERGENCIA = "Divergencias Cotacao x Invoice"

# Paleta do mockup (RPA_Schiavon_PowerBI_Mockup_revisao.html).
_NAVY = "1E2B3C"
_INK = "1F2421"
_INK_MUTE = "5B6058"
_VERDE_FUNDO, _VERDE_TEXTO = "E6F0EA", "2F6B4F"
_VERMELHO_FUNDO, _VERMELHO_TEXTO = "F7E9E5", "B4432E"
_AMBAR_FUNDO, _AMBAR_TEXTO = "FBF0DC", "8A6116"

_LINHA = Side(style="thin", color="D8D6CE")
_BORDA = Border(top=_LINHA, bottom=_LINHA, left=_LINHA, right=_LINHA)

_DINHEIRO = '"$"#,##0.0000'
_DINHEIRO_TOTAL = '"$"#,##0.00'

# Rótulo e cor por `fat_conciliacao_item.cod_status` (StatusConciliacaoEnum:
# CONFERIDO=0, PRECO_ACIMA=10, PRECO_ABAIXO=11, SEM_REFERENCIA_ITEM=20,
# UNIDADE_DIVERGENTE=21). CONFERIDO sai como "Conciliado": no relatório do
# cliente o que importa é que a linha fechou contra a cotação, e "conferido"
# sugeria conferência humana. As duas abas usam o mesmo mapa.
_STATUS = {
    0: ("Conciliado", _VERDE_FUNDO, _VERDE_TEXTO),
    10: ("Acima do cotado", _VERMELHO_FUNDO, _VERMELHO_TEXTO),
    11: ("Abaixo do cotado", _VERMELHO_FUNDO, _VERMELHO_TEXTO),
    20: ("Item nao achado na cotacao", _AMBAR_FUNDO, _AMBAR_TEXTO),
    21: ("Unidade divergente", _AMBAR_FUNDO, _AMBAR_TEXTO),
}


def gerar_relatorio(dados: dict, caminho: Path, gerado_em: datetime, inicio: date, fim: date) -> None:
    """Monta as duas abas e salva em `caminho`. Sobrescreve o que já existir."""
    wb = Workbook()

    ws = wb.active
    ws.title = ABA_COMPARACAO
    _preparar_aba(ws, (34, 26, 34, 15, 15, 14, 20))
    linha = _cabecalho_aba(
        ws, "Cotacao x Invoice",
        "Itens que conciliaram - preco dentro da tolerancia, com o arquivo de origem.",
        gerado_em, inicio, fim,
    )
    _tabela_comparacao(ws, linha, dados["comparacao_precos"])

    ws = wb.create_sheet(ABA_DIVERGENCIA)
    _preparar_aba(ws, (34, 26, 30, 30, 10, 15, 15, 14, 10, 16, 28))
    linha = _cabecalho_aba(
        ws, "Divergencias Cotacao x Invoice",
        "Preco fora da tolerancia e item sem comparacao - maior impacto primeiro.",
        gerado_em, inicio, fim,
    )
    _tabela_divergencias(ws, linha, dados["divergencias"])

    caminho.parent.mkdir(parents=True, exist_ok=True)
    wb.save(caminho)


# ---------------------------------------------------------------------------
# Blocos genéricos de layout
# ---------------------------------------------------------------------------

def _preparar_aba(ws: Worksheet, larguras: tuple[int, ...]) -> None:
    ws.sheet_view.showGridLines = False
    for i, largura in enumerate(larguras, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = largura


def _cabecalho_aba(
    ws: Worksheet, titulo: str, subtitulo: str,
    gerado_em: datetime, inicio: date, fim: date,
) -> int:
    """Título, período e legenda da aba. Devolve a linha em que a tabela começa."""
    ws["A1"] = f"RPA Schiavon - {titulo}"
    ws["A1"].font = Font(bold=True, size=15, color=_INK)
    ws["A2"] = f"Semana {inicio:%d/%m} a {fim:%d/%m}  -  gerado em {gerado_em:%d/%m/%Y %H:%M}"
    ws["A2"].font = Font(size=10, color=_INK_MUTE)
    ws["A3"] = subtitulo
    ws["A3"].font = Font(size=10, color=_INK_MUTE, italic=True)
    ws.freeze_panes = "A6"
    return 5


def _nota(ws: Worksheet, linha: int, texto: str) -> int:
    cel = ws.cell(row=linha, column=1, value=texto)
    cel.font = Font(size=10, color=_INK_MUTE, italic=True)
    return linha + 1


def _cabecalho_tabela(ws: Worksheet, linha: int, colunas: list[str]) -> int:
    for i, nome in enumerate(colunas, start=1):
        c = ws.cell(row=linha, column=i, value=nome)
        c.font = Font(bold=True, size=10, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=_NAVY)
        c.border = _BORDA
        c.alignment = Alignment(vertical="center", indent=1)
    ws.row_dimensions[linha].height = 20
    ultima = ws.cell(row=linha, column=len(colunas)).column_letter
    ws.auto_filter.ref = f"A{linha}:{ultima}{linha}"
    return linha + 1


def _celula(ws: Worksheet, linha: int, coluna: int, valor, *, numero=None, cor=None, quebrar=False) -> None:
    c = ws.cell(row=linha, column=coluna, value=valor)
    c.border = _BORDA
    c.font = Font(size=10, color=cor or _INK)
    c.alignment = Alignment(vertical="center", indent=1, wrap_text=quebrar)
    if numero:
        c.number_format = numero
        c.alignment = Alignment(vertical="center", horizontal="right", indent=1)


def _pill(ws: Worksheet, linha: int, coluna: int, texto: str, fundo: str, tinta: str) -> None:
    c = ws.cell(row=linha, column=coluna, value=texto)
    c.border = _BORDA
    c.font = Font(bold=True, size=10, color=tinta)
    c.fill = PatternFill("solid", fgColor=fundo)
    c.alignment = Alignment(horizontal="center", vertical="center")


def _status(ws: Worksheet, linha: int, coluna: int, cod_status) -> None:
    texto, fundo, tinta = _STATUS.get(cod_status, ("-", None, _INK_MUTE))
    if fundo:
        _pill(ws, linha, coluna, texto, fundo, tinta)
    else:
        _celula(ws, linha, coluna, texto, cor=tinta)


def _num(valor) -> float | None:
    """Decimal do psycopg2 -> float; None continua None (celula vazia)."""
    return float(valor) if valor is not None else None


# ---------------------------------------------------------------------------
# Aba 1 — cotação x invoice (todo item comparado)
# ---------------------------------------------------------------------------

def _tabela_comparacao(ws: Worksheet, linha: int, linhas: list[dict]) -> int:
    linha = _cabecalho_tabela(
        ws, linha,
        ["Arquivo", "Fornecedor", "Item", "Preco invoice", "Preco cotado", "Diferenca", "Status"],
    )
    if not linhas:
        return _nota(ws, linha, "Nenhum item conciliado nesta semana.")
    for row in linhas:
        _celula(ws, linha, 1, row["arquivo"])
        _celula(ws, linha, 2, row["fornecedor"])
        _celula(ws, linha, 3, row["item"], quebrar=True)
        _celula(ws, linha, 4, _num(row["preco_invoice"]), numero=_DINHEIRO)
        _celula(ws, linha, 5, _num(row["preco_referencia"]), numero=_DINHEIRO)
        # Sem vermelho: aqui toda diferença está dentro da tolerância — pintar
        # de alerta o que conciliou contradiz o status ao lado.
        _celula(ws, linha, 6, _num(row["dif_unitaria"]), numero=_DINHEIRO)
        _status(ws, linha, 7, row["cod_status"])
        linha += 1
    return linha


# ---------------------------------------------------------------------------
# Aba 2 — divergências cotação x invoice
# ---------------------------------------------------------------------------

def _tabela_divergencias(ws: Worksheet, linha: int, linhas: list[dict]) -> int:
    linha = _cabecalho_tabela(
        ws, linha,
        ["Arquivo", "Fornecedor", "Item na invoice", "Item cotado", "Qtd",
         "Preco invoice", "Preco cotado", "Dif. unitaria", "Dif. %",
         "Impacto (dif. x qtd)", "Motivo"],
    )
    if not linhas:
        return _nota(ws, linha, "Nenhuma divergencia entre cotacao e invoice nesta semana.")

    primeira = linha
    for row in linhas:
        item_cotado = row.get("item_cotado")
        pct = row.get("dif_pct")
        _celula(ws, linha, 1, row["arquivo"])
        _celula(ws, linha, 2, row["fornecedor"])
        _celula(ws, linha, 3, row["item"], quebrar=True)
        _celula(ws, linha, 4, item_cotado or "-", quebrar=True,
                cor=None if item_cotado else _INK_MUTE)
        _celula(ws, linha, 5, _num(row.get("qtd")), numero="#,##0.###")
        _celula(ws, linha, 6, _num(row["preco_invoice"]), numero=_DINHEIRO)
        _celula(ws, linha, 7, _num(row["preco_referencia"]), numero=_DINHEIRO)
        _celula(ws, linha, 8, _num(row.get("dif_unitaria")), numero=_DINHEIRO, cor=_VERMELHO_TEXTO)
        _celula(ws, linha, 9, (_num(pct) / 100) if pct is not None else None,
                numero="0.0%", cor=_VERMELHO_TEXTO)
        _celula(ws, linha, 10, _num(row.get("dif_valor")), numero=_DINHEIRO_TOTAL,
                cor=_VERMELHO_TEXTO)
        _status(ws, linha, 11, row["cod_status"])
        linha += 1

    return _rodape_divergencias(ws, linha, primeira, len(linhas))


def _rodape_divergencias(ws: Worksheet, linha: int, primeira: int, total: int) -> int:
    """Fecha a aba com a contagem e a soma do impacto em valor. A soma é uma
    fórmula, não um número calculado aqui: se quem abre a planilha filtrar ou
    apagar linhas, o total acompanha."""
    rotulo = ws.cell(row=linha, column=9, value=f"{total} divergencia(s)")
    rotulo.font = Font(bold=True, size=10, color=_INK)
    rotulo.alignment = Alignment(horizontal="right", vertical="center", indent=1)

    soma = ws.cell(row=linha, column=10, value=f"=SUM(J{primeira}:J{linha - 1})")
    soma.font = Font(bold=True, size=10, color=_VERMELHO_TEXTO)
    soma.number_format = _DINHEIRO_TOTAL
    soma.alignment = Alignment(horizontal="right", vertical="center", indent=1)
    soma.border = Border(top=Side(style="thick", color=_NAVY))
    return linha + 1
