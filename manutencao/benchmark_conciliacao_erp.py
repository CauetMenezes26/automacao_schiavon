"""Benchmark manual: roda o motor de conciliacao ERP contra 10 invoices reais.

Mesma disciplina de `teste_conciliacao_erp.py` (NAO grava no banco, roda visivel,
so pra ver o motor contra dado real do Catapult) — mas em lote: agrupa as
invoices por loja pra logar uma vez so por loja (Windermere / Dr. Phillips),
busca o PO de cada uma, roda `conciliacao.reconcile_erp.reconcile_items_against_po`
e junta tudo num relatorio `.xlsx` na raiz do projeto.

As invoices abaixo sao fixtures transcritas a mao dos PDFs em
`files/read_files/` (mesmo padrao do `INVOICE_ITEMS` de
`teste_conciliacao_erp.py` — nao roda Vision aqui). `item_code`/`upc`: só
preenchido quando o PDF imprime um código de catálogo confiável (coluna
"SKU"/"Item Code"/"UPC Code"/"ITEM NO."); os números escritos à mão nas notas
de carne (ex. '40102') são código da COTAÇÃO semanal, não do Catapult — não
entram aqui de propósito, pra não forçar um match de código errado.

    python -m manutencao.benchmark_conciliacao_erp
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl  # noqa: E402
from openpyxl.styles import Font, PatternFill  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402

from commons.catapult import (  # noqa: E402
    CatapultLoginError,
    open_catapult_session,
    open_purchase_order,
    open_worksheets,
    scrape_po_items,
    search_purchase_orders_by_supplier,
    to_po_lines,
)
from commons.db import connect_db  # noqa: E402
from domain.config import Config, carregar_config  # noqa: E402
from commons.matcher import POLine  # noqa: E402
from commons.paths import ROOT  # noqa: E402
from conciliacao.reconcile_erp import (  # noqa: E402
    PRICE_MISMATCH_PO,
    QTY_MISMATCH_PO,
    escolher_po_por_itens,
    reconcile_items_against_po,
)
from domain.service.conciliacao_service import fetch_item_sinonimos  # noqa: E402

_URL_ATTR_POR_LOJA = {"windermere": "url_windermere", "drphillips": "url_drphilips"}


@dataclass
class InvoiceFixture:
    arquivo: str
    fornecedor: str
    loja: str  # "windermere" | "drphillips"
    invoice_number: str
    total_invoice: Decimal
    items: list[dict]


def _item(id_, desc, qty, preco, total, item_code=None, upc=None, hw=False, cases=None,
          handwritten_code=None):
    return {
        "id": id_, "description": desc, "quantity": Decimal(qty),
        "unit_price": Decimal(preco), "total_price": Decimal(total),
        "item_code": item_code, "upc": upc, "handwritten_notes": hw,
        "cases": None if cases is None else Decimal(cases),
        "handwritten_code": handwritten_code,
    }


# ---------------------------------------------------------------------------
# Fixtures — transcritas a mao dos PDFs de files/read_files/ (ver docstring)
# ---------------------------------------------------------------------------

FIXTURES: list[InvoiceFixture] = [

    InvoiceFixture(
        arquivo="drphil_Cheney Brothers_9100063327__001_26-06-2026.pdf",
        fornecedor="Cheney Brothers", loja="drphillips",
        invoice_number="05-9100063327",
        total_invoice=Decimal("2177.58"),
        items=[
            # item_code = coluna impressa "ITEM NO." (catalogo interno da
            # Cheney) -- confirmado contra o Catapult real que NAO bate com
            # nada la. handwritten_code = anotacao a mao na nota (40102 etc),
            # que E o scancode de verdade -- `match_items_po` tenta os dois,
            # nessa ordem (commons/matcher.py).
            #
            # cases = coluna "CASES" impressa na nota, separada de "WEIGHT"
            # (peso, em quantity). Acougue/peso variavel — achado do cliente
            # (Leandro Rocha): a divergencia de quantidade compara cases contra
            # Ordered do Catapult, nao o peso (ver reconcile_erp._comparar_grupo).
            _item(1, "BEEF SIRLOIN TOP BUTT XT ANGUS N/R", "209.00", "6.01", "1256.09",
                  item_code="10112883", handwritten_code="40102", cases="3"),
            _item(2, "CHIX TENDER JUMBO CVP", "80.00", "1.85", "148.00",
                  item_code="227059", handwritten_code="40622", cases="2"),
            _item(3, "BEEF OXTAIL STEER WHOLE FROZEN WHOLE", "30.00", "6.09", "182.70",
                  item_code="204077", handwritten_code="40120", cases="2"),
            _item(4, "PORK BELLY IMPORTED AURORA FROZEN BRAZIL AURORA SKIN ON",
                  "180.74", "3.23", "583.79",
                  item_code="10101096", handwritten_code="40202", cases="5"),
        ],
    ),

    InvoiceFixture(
        arquivo="drphil_Prime Meats_1175605__001_19-06-2026.pdf",
        fornecedor="Prime Meats", loja="drphillips",
        invoice_number="1175605",
        total_invoice=Decimal("8129.24"),
        items=[
            # cases = coluna "ORDER QTY" impressa na nota (o "Order QTY" e o
            # "Ship QTY" batem, ambos em caixa) — separada de "WEIGHT", que
            # segue em quantity. Mesmo padrao Cheney: divergencia de
            # quantidade compara cases x Ordered do Catapult, nao o peso.
            _item(1, "BF PICANA SEL GEN FRSH", "511.60", "7.99", "4087.68", item_code="B439",
                  cases="7"),
            _item(2, "BF PICANA GEN CH 1 PC BAG", "145.30", "8.19", "1190.01", item_code="B450",
                  cases="2"),
            _item(3, "BF B/L BANANA SHANK GEN FZN", "45.02", "4.89", "220.15", item_code="B465F",
                  cases="1"),
            # Item 4 (congelado) e item 9 (fresco) sao o MESMO peito de
            # frango pro cliente (achado dele, Leandro Rocha) — Catapult
            # deve ter UMA linha de PO so pra ele. Nao precisa de tratamento
            # especial aqui: `_herdar_po_de_variante_de_estado` cuida disso
            # em `conciliacao/reconcile_erp.py`.
            _item(4, "CHKN BREAST BL/SL DRY GEN FZN", "600.00", "1.41", "846.00", item_code="C031F",
                  cases="15"),
            _item(5, "CHKN HEARTS S/MTN FZN #20", "400.00", "1.69", "676.00", item_code="C041",
                  cases="20"),
            _item(6, "CHKN CUT WINGS 1&2 JNT GEN FRSH 5-8 PCS/LB", "200.00", "1.25", "250.00",
                  item_code="C192", cases="5"),
            _item(7, "CHKN TENDERLOIN JUMBO GEN FZN", "120.00", "1.93", "231.60", item_code="C170F",
                  cases="3"),
            _item(8, "CHKN LIVER GEN FZN", "20.00", "1.29", "25.80", item_code="C144", cases="1"),
            _item(9, "CHKN BREAST BL/SL UNSIZED DRY GEN FRSH CVP", "400.00", "1.48", "592.00",
                  item_code="C105F", cases="10"),
        ],
    ),

    InvoiceFixture(
        arquivo="drphil_Sorvepan_0977__001_19-06-2026.pdf",
        fornecedor="Sorvepan", loja="drphillips",
        invoice_number="INV0977",
        total_invoice=Decimal("1295.00"),
        items=[
            _item(1, "PUDIM/FLAN MINI MOLD CRISPLASTIC(50) BAG 10 X 140 ML", "50", "3.70", "185.00"),
            _item(2, "CHOCOLATE BAR WHITE SICAO MAIS(5) BAR 4.62 LB/2.1 KG", "30", "37.00", "1110.00"),
        ],
    ),

    InvoiceFixture(
        arquivo="drphil_Ramax_4230__001_19-06-2026.pdf",
        fornecedor="Ramax", loja="drphillips",
        invoice_number="4230",
        total_invoice=Decimal("2595.40"),
        items=[
            # cases = coluna "CARTONS" impressa na nota (Ramax chama caixa de
            # cartons) — mesmo padrao Cheney/Prime Meats: WEIGHT e a soma dos
            # pesos individuais entre parenteses na coluna, ja o valor unico
            # transcrito em quantity.
            _item(1, "BEEF TENDERLOIN // FROZEN", "259.72", "9.19", "2386.83", cases="6"),
            _item(2, "TOMAHAWK", "30.90", "6.75", "208.58", cases="2"),
        ],
    ),

    InvoiceFixture(
        arquivo="drphil_Zap_3555__001_19-06-2026.pdf",
        fornecedor="Zap Foods", loja="drphillips",
        invoice_number="3555",
        total_invoice=Decimal("49.99"),
        items=[
            _item(1, "PE DE MOLEQUE GUIMARAES QUEROMAIS 25X100G", "1", "49.99", "49.99",
                  item_code="2110"),
        ],
    ),

    InvoiceFixture(
        arquivo="drphil_Mena_11131__001_19-06-2026.pdf",
        fornecedor="Mena Imports", loja="drphillips",
        invoice_number="11131",
        total_invoice=Decimal("239.85"),
        items=[
            _item(1, "REI DA MASSA PASTEL DE FEIRA RECORTADA 6X2 KG", "3", "79.95", "239.85",
                  item_code="00130"),
        ],
    ),

    InvoiceFixture(
        arquivo="wind_ZAP_06262026074322_001_26-06-2026.pdf",
        fornecedor="Zap Foods", loja="windermere",
        invoice_number="3816",
        total_invoice=Decimal("532.65"),
        items=[
            _item(1, "SANTA MASSA - PAO DE ALHO TRADICIONAL (BAGUETE) - 10X400G", "15", "35.51",
                  "532.65", item_code="45321"),
        ],
    ),

    InvoiceFixture(
        arquivo="wind_MENA_06192026103934_001_19-06-2026.pdf",
        fornecedor="Mena Imports", loja="windermere",
        invoice_number="11126",
        total_invoice=Decimal("3518.78"),
        items=[
            _item(1, "QUALY MARGARINA TRAD. C/SAL 12X500 GR", "3", "59.95", "179.85", item_code="00152"),
            _item(2, "REI DA MASSA PASTEL DE DISCO REDONDA 16X500 GR", "1", "69.95", "69.95", item_code="00136"),
            _item(3, "REI DA MASSA PASTEL DE ROLO 20X500 GR", "1", "69.95", "69.95", item_code="00133"),
            _item(4, "SOCOCO AGUA DE COCO 12X1 LT", "2", "39.95", "79.90", item_code="00208"),
            _item(5, "SOCOCO AGUA DE COCO PRYSMA 12X330 ML", "5", "25.95", "129.75", item_code="00207"),
            _item(6, "SOCOCO AGUA DE COCO TP 24X200 ML", "2", "25.95", "51.90", item_code="00209"),
            _item(7, "TANG SUCO DE LARANJA 18X18 GR", "1", "8.95", "8.95", item_code="00214"),
            _item(8, "TODDYNHO ACHOCOLATADO 27X200 ML", "5", "39.95", "199.75", item_code="00224"),
            _item(9, "LACTA CHOCOLATE BIS AO LEITE 65X100.8 GR", "2", "169.95", "339.90", item_code="00356"),
            _item(10, "LACTA CHOCOLATE BIS BRANCO 65X100.8 GR", "2", "164.95", "329.90", item_code="00357"),
            _item(11, "PREDILECTA GELEIA MOCOTO TUTTI-FRUTTI 24X180 GR", "1", "47.95", "47.95", item_code="00374"),
            _item(12, "NESTLE BISCOITO PASSATEMPO CHOC MIX CHOCOLATE 70X130GR", "1", "59.95", "59.95", item_code="01994"),
            _item(13, "NESTLE BISCOITO PASSATEMPO CHOCOLATE 70X130 GR", "1", "59.95", "59.95", item_code="00526"),
            _item(14, "TRAKINAS BISCOITO RECHEADO MORANGO 54X126 GR", "0.5", "63.95", "31.98", item_code="00566"),
            _item(15, "AMAFIL FARINHA MANDIOCA BIJU TORRADA 16X500 GR", "1", "35.95", "35.95", item_code="00677"),
            _item(16, "AMAFIL POLVILHO AZEDO PREMIUM 10X1 KG", "1", "38.95", "38.95", item_code="00664"),
            _item(17, "CAMIL FEIJAO CARIOCA 10X1 KG", "10", "35.95", "359.50", item_code="00786"),
            _item(18, "KNORR CALDO CUBINHO DE CARNE 10X57 GR", "1", "8.95", "8.95", item_code="00887"),
            _item(19, "LEBRE SAL GROSSO 10X1 KG", "4", "12.95", "51.80", item_code="00901"),
            _item(20, "MENINA LEITE DE COCO 24X200 ML", "1", "33.95", "33.95", item_code="00910"),
            _item(21, "NESTLE LEITE NINHO INTEGRAL INSTANT 12X360 GR", "4", "91.95", "367.80", item_code="00937"),
            _item(22, "SAZON PARA CARNE (VERMELHO) 12X60 GR", "2", "18.50", "37.00", item_code="01427"),
            _item(23, "SINHA FUBA MIMOSO 30X500 GR", "1", "34.95", "34.95", item_code="01019"),
            _item(24, "YOKI BATATA PALHA TRAD. 20X105 GR", "10", "49.95", "499.50", item_code="01012"),
            _item(25, "YOKI BATATA PALHA XTRA FINA 20X105 GR", "5", "49.95", "249.75", item_code="01011"),
            _item(26, "IMPALA ESMALTE CROCHE 6X7.5 ML", "1", "12.95", "12.95", item_code="02019"),
            _item(27, "RISQUE ESMALTE ASTRAL 6X8 ML", "2", "12.95", "25.90", item_code="01526"),
            _item(28, "RISQUE ESMALTE LINDA LEVE NUDE 6X8 ML", "1", "12.95", "12.95", item_code="01553"),
            _item(29, "RISQUE ESMALTE LOVE 6X8 ML", "2", "12.95", "25.90", item_code="01594"),
            _item(30, "RISQUE ESMALTE NUDE 6X8 ML", "2", "12.95", "25.90", item_code="01580"),
            _item(31, "RISQUE ESMALTE PLATINO 6X8 ML", "1", "12.95", "12.95", item_code="01557"),
            _item(32, "SKALA EXPERT POTAO DESMAIADO 2EM1 6X1 KG 24669", "1", "24.50", "24.50", item_code="01292"),
        ],
    ),

    InvoiceFixture(
        arquivo="wind_JULINA_06232026155238_001_24-06-2026.pdf",
        fornecedor="Julina Foods", loja="windermere",
        invoice_number="30012178",
        total_invoice=Decimal("762.50"),
        items=[
            _item(1, "Ole Ervilhas Vidro 170gr", "2", "12.60", "25.20", item_code="01209", upc="7891032012351"),
            _item(2, "Ole Milho Vidro 200gr", "4", "14.60", "58.40", item_code="01211", upc="7891032013259"),
            _item(3, "Ole Cogumelos Vidro 100g", "1", "58.20", "58.20", item_code="01212", upc="7891032013969"),
            _item(4, "Jazam Pingo de Leite Pote", "1", "23.95", "23.95", item_code="03540", upc="7896383051424"),
            _item(5, "Jazam Granulado Coloreti Sortido", "1", "29.75", "29.75", item_code="03546", upc="7896383072764"),
            _item(6, "Jazam Pacoca da Fazenda Premium", "1", "46.60", "46.60", item_code="03549", upc="7896383071866"),
            _item(7, "GRT Choc Barra Meio Amargo 80g", "2", "27.50", "55.00", item_code="09546", upc="7891008124026"),
            _item(8, "GRT Talento ChoBranc Cerea Pass", "1", "24.95", "24.95", item_code="09549", upc="7891008121674"),
            _item(9, "C.Suica Bolo Prem Classic Lar 370g", "1", "25.85", "25.85", item_code="12623", upc="7897173001070"),
            _item(10, "C.Suica Bol Caf Man. Cen Cho", "1", "25.85", "25.85", item_code="12641", upc="7897173095680"),
            _item(11, "GRT Tab Chocolate ao Leite 80g DSP", "2", "31.90", "63.80", item_code="13181", upc="7891008123975"),
            _item(12, "GRT Bombons Sortidos 8949 220gr", "1", "119.00", "119.00", item_code="13291", upc="7891008116632"),
            _item(13, "Cafe Brasileiro CappuccinoTrad", "1", "82.80", "82.80", item_code="16189", upc="7891018004745"),
            _item(14, "Vitarella Biscoito Agua e Sal 5414", "1", "23.80", "23.80", item_code="16229", upc="7896213006242"),
            _item(15, "Vitarella Bisc.Cream Crackers 5411", "1", "25.35", "25.35", item_code="16230", upc="7896213006235"),
            _item(16, "Vitarella Biscoito Maizena 5435", "1", "23.80", "23.80", item_code="16231", upc="7896213006396"),
            _item(17, "Jazam Amendoim Crocante Original", "2", "25.10", "50.20", item_code="20894", upc="7896383071569"),
        ],
    ),

    InvoiceFixture(
        arquivo="wind_TRIUNFO_08262026114223335_0001_26-08-2026.pdf",
        fornecedor="Triunfo Foods", loja="windermere",
        invoice_number="50013453",
        total_invoice=Decimal("2200.25"),
        items=[
            _item(1, "Pocos de Caldas Requeijao Cremoso", "5", "60.50", "302.50", item_code="01427", upc="7898955617397"),
            _item(2, "Pocos de Caldas Requeijao Crem 400g", "5", "60.15", "300.75", item_code="01429", upc="7891097001581"),
            _item(3, "Doriana Margarina Com Sal 250gr", "0", "42.05", "0.00", item_code="14440", upc="7891515901035", hw=True),
            _item(4, "Corte's Paio 12oz", "1", "48.55", "48.55", item_code="26001", upc="8004626001"),
            _item(5, "Corte's Linguica Toscana 14oz", "10", "51.30", "513.00", item_code="26135", upc="80046261351"),
            _item(6, "Corte's Linguica Toscana C/Herbs 14", "1", "51.30", "51.30", item_code="26136", upc="8004616136"),
            _item(7, "Corte's Linguica Toscana Picante 14", "1", "51.30", "51.30", item_code="26137", upc="080046261375"),
            _item(8, "Corte's Feijoada Mix 12oz", "1", "59.75", "59.75", item_code="26141", upc="8004626141"),
            _item(9, "Corte's Colombian Chorizo 14oz", "1", "45.10", "45.10", item_code="26225", upc="8004626225"),
            _item(10, "Corte's Argentinean Chorizo 14oz", "4", "45.10", "180.40", item_code="26227", upc="8004626227"),
            _item(11, "Corte's Brazilian Calabreza 14oz", "1", "45.10", "45.10", item_code="26230", upc="8004626230"),
            _item(12, "Corte's Linguica Toscana W/ Bacon", "4", "51.20", "204.80", item_code="26259", upc=None),
            _item(13, "Corte's Toscana Biquinho 14oz", "2", "59.65", "119.30", item_code="26260", upc="080046262600"),
            _item(14, "Corte's Linguica Toscana w/coalho", "1", "62.40", "62.40", item_code="26318", upc=None),
            _item(15, "Ice Fruit Pulp Acai 400gr", "2", "37.90", "75.80", item_code="17982", upc="7896304221004"),
            _item(16, "Ice Fruit Pulp Abacaxi 400gr", "2", "35.20", "70.40", item_code="17985", upc="7896304220106"),
            _item(17, "Ice Fruit Pulp Goiaba 400gr", "2", "34.90", "69.80", item_code="17987", upc="7896304220496"),
        ],
    ),
]


# ---------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------

def _achar_po(page, fornecedor: str, items: list[dict], sinonimos: dict):
    """Busca PO(s) 'Ordered' do fornecedor. Sem nenhum, `(None, None)`. Com
    mais de um, desempata pelos itens da fixture (`escolher_po_por_itens`) e
    devolve `raw_rows` já raspado do vencedor (senão `None`, ainda por
    raspar)."""
    print(f"    [worksheets] buscando Supplier contains '{fornecedor}', Status=Ordered...")
    try:
        achados = search_purchase_orders_by_supplier(page, fornecedor)
    except RuntimeError as exc:
        print(f"      ⚠ busca suspeita: {exc}")
        return None, None
    if not achados:
        print("      nada encontrado")
        return None, None

    print(f"      {len(achados)} PO(s) 'Ordered' encontrado(s): "
          f"{', '.join(a['name'] for a in achados)}")
    if len(achados) == 1:
        return achados[0], None

    candidatos_raw: list[list[dict]] = []
    candidatos_po_lines: list[list[POLine]] = []
    for achado in achados:
        open_purchase_order(page, achado["href"])
        raw = scrape_po_items(page)
        candidatos_raw.append(raw)
        candidatos_po_lines.append(to_po_lines(raw))

    indice = escolher_po_por_itens(items, candidatos_po_lines, sinonimos)
    if indice is None:
        print("      ⚠ nenhum candidato casou com os itens da invoice — usando o primeiro")
        indice = 0
    print(f"      escolhido: {achados[indice]['name']}")
    return achados[indice], candidatos_raw[indice]


def _linha(fixture: InvoiceFixture, item: dict, **extra) -> dict:
    base = {
        "arquivo": fixture.arquivo, "fornecedor": fixture.fornecedor,
        "loja": fixture.loja, "invoice_number": fixture.invoice_number,
        "item_invoice": item["description"],
        "qty_invoice": item["quantity"], "cases_invoice": item.get("cases"),
        "preco_invoice": item["unit_price"],
        "item_code": item.get("item_code"), "upc": item.get("upc"),
        "status": "", "motivo": "", "match_level": "", "match_score": None,
        "item_po": "", "preco_po": None, "dif_preco": None,
    }
    base.update(extra)
    return base


def _processar_fixture(
    page, url: str, fixture: InvoiceFixture, linhas: list[dict], sinonimos: dict,
) -> None:
    print(f"\n  -- {fixture.fornecedor}  ({fixture.arquivo})  "
          f"{len(fixture.items)} item(ns) --")
    # `open_purchase_order` (fixture anterior) navega pra FORA da tela
    # Worksheets via page.goto(href) — a busca da proxima invoice precisa
    # voltar pra la primeiro, ou o dropdown de categoria nem existe na pagina
    # (mesmo bug existiria em reconcile_erp_flow.py com >1 nota por loja).
    open_worksheets(page, url)
    po_achado, raw_rows_ja_raspado = _achar_po(page, fixture.fornecedor, fixture.items, sinonimos)

    if po_achado is None:
        for item in fixture.items:
            linhas.append(_linha(
                fixture, item, status="SEM_PO",
                motivo=f"nenhum PO 'Ordered' encontrado no Catapult para "
                       f"{fixture.fornecedor!r}",
            ))
        return

    try:
        open_purchase_order(page, po_achado["href"])
        raw_rows = raw_rows_ja_raspado if raw_rows_ja_raspado is not None else scrape_po_items(page)
        po_lines = to_po_lines(raw_rows)
    except Exception as exc:  # noqa: BLE001 — benchmark, quero ver o que quebrou
        print(f"    ✗ falha raspando o PO: {exc}")
        for item in fixture.items:
            linhas.append(_linha(
                fixture, item, status="ERRO_TECNICO",
                motivo=f"falha raspando a grade Items do PO: {exc}",
            ))
        return

    print(f"    [po] {po_achado['name']}: {len(po_lines)} item(ns) na grade Items")
    for po in po_lines:
        print(f"        - {po.item_name}  (receipt_alias={po.receipt_alias!r}, "
              f"supplier_unit_id={po.supplier_unit_id}, scancode={po.scancode})")

    resultado = reconcile_items_against_po(fixture.items, po_lines, sinonimos=sinonimos)
    po_by_key = {po.key: po for po in po_lines}

    for item, linha in zip(fixture.items, resultado["items"]):
        if linha["match_level"] == "unmatched":
            linhas.append(_linha(
                fixture, item, status="SEM_PAR_NO_PO",
                motivo="nenhum item do PO bateu por codigo nem por nome",
            ))
            continue

        issues = linha["issue_codes"]
        problemas = []
        if QTY_MISMATCH_PO in issues:
            problemas.append("quantidade diverge")
        if PRICE_MISMATCH_PO in issues:
            problemas.append("valor diverge")
        status = "DIVERGENCIA" if problemas else "OK"

        linhas.append(_linha(
            fixture, item, status=status, motivo="; ".join(problemas),
            match_level=linha["match_level"], match_score=linha.get("match_score"),
            item_po=linha.get("item_name_po"), preco_po=linha.get("price_other"),
            dif_preco=linha.get("price_diff"),
        ))


def _rodar_loja(
    config: Config, loja: str, fixtures: list[InvoiceFixture], linhas: list[dict],
    sinonimos: dict,
) -> None:
    url = getattr(config.ecrs, _URL_ATTR_POR_LOJA[loja])
    access_email = config.ecrs.access_email
    usuario = config.ecrs.usuario
    senha = config.ecrs.senha

    print(f"\n{'='*70}\nLoja: {loja}  ({len(fixtures)} invoice(s))\n{'='*70}")
    if not all((url, access_email, usuario, senha)):
        print(f"  ⚠ credencial/URL ausente para {loja}, pulando")
        for f in fixtures:
            for item in f.items:
                linhas.append(_linha(f, item, status="ERRO_TECNICO",
                                      motivo="credencial/URL do Catapult ausente"))
        return

    try:
        pw, browser, page = open_catapult_session(url, access_email, usuario, senha, headless=False)
    except CatapultLoginError as exc:
        print(f"  ✗ login falhou: {exc}")
        for f in fixtures:
            for item in f.items:
                linhas.append(_linha(f, item, status="ERRO_TECNICO", motivo=f"login falhou: {exc}"))
        return

    try:
        for fixture in fixtures:
            _processar_fixture(page, url, fixture, linhas, sinonimos)
    finally:
        browser.close()
        pw.stop()


# ---------------------------------------------------------------------------
# Relatorio xlsx
# ---------------------------------------------------------------------------

_COLUNAS = [
    ("arquivo", "Arquivo"), ("fornecedor", "Fornecedor"), ("loja", "Loja"),
    ("invoice_number", "Invoice #"), ("item_invoice", "Item (invoice)"),
    ("qty_invoice", "Qtd"), ("cases_invoice", "Caixas"), ("preco_invoice", "Preco invoice"),
    ("item_code", "Item code"), ("upc", "UPC"),
    ("status", "Status"), ("motivo", "Motivo"),
    ("match_level", "Match level"), ("match_score", "Score"),
    ("item_po", "Item no PO"), ("preco_po", "Preco PO"), ("dif_preco", "Dif preco"),
]

_COR_STATUS = {
    "OK": "C6EFCE", "DIVERGENCIA": "FFEB9C",
    "SEM_PAR_NO_PO": "FFC7CE", "SEM_PO": "FFC7CE", "ERRO_TECNICO": "D9D9D9",
}


def _gerar_xlsx(linhas: list[dict]) -> Path:
    wb = openpyxl.Workbook()

    ws = wb.active
    ws.title = "Detalhe"
    ws.append([label for _, label in _COLUNAS])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for linha in linhas:
        ws.append([linha.get(key) for key, _ in _COLUNAS])
        cor = _COR_STATUS.get(linha.get("status"))
        if cor:
            preenchimento = PatternFill("solid", fgColor=cor)
            ws.cell(row=ws.max_row, column=_col_idx("status")).fill = preenchimento
    for i, (_, label) in enumerate(_COLUNAS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = max(12, len(label) + 2, 18)
    ws.freeze_panes = "A2"

    ws2 = wb.create_sheet("Resumo")
    ws2.append(["Métrica", "Valor"])
    for cell in ws2[1]:
        cell.font = Font(bold=True)
    total = len(linhas)
    por_status: dict[str, int] = {}
    for linha in linhas:
        por_status[linha["status"]] = por_status.get(linha["status"], 0) + 1
    ws2.append(["Total de itens (10 invoices)", total])
    ws2.append([])
    ws2.append(["Status", "Qtd", "%"])
    for status, label in (
        ("OK", "OK — casou e preco/qtd batem"),
        ("DIVERGENCIA", "Casou, mas preco/qtd diverge (nao e erro de match)"),
        ("SEM_PAR_NO_PO", "PO existe, item sem par (vocabulario/RPA ou item nao pedido)"),
        ("SEM_PO", "Nota sem PO encontrado no Catapult"),
        ("ERRO_TECNICO", "Falha tecnica (login/scraping)"),
    ):
        n = por_status.get(status, 0)
        pct = f"{n / total * 100:.1f}%" if total else "0%"
        ws2.append([label, n, pct])
    ws2.column_dimensions["A"].width = 60
    ws2.column_dimensions["B"].width = 10

    ws3 = wb.create_sheet("Por invoice")
    ws3.append(["Arquivo", "Fornecedor", "Loja", "Itens", "OK", "Divergencia",
                "Sem par/PO", "Erro tecnico"])
    for cell in ws3[1]:
        cell.font = Font(bold=True)
    por_arquivo: dict[str, dict] = {}
    for linha in linhas:
        d = por_arquivo.setdefault(linha["arquivo"], {
            "fornecedor": linha["fornecedor"], "loja": linha["loja"],
            "total": 0, "OK": 0, "DIVERGENCIA": 0, "SEM": 0, "ERRO": 0,
        })
        d["total"] += 1
        if linha["status"] == "OK":
            d["OK"] += 1
        elif linha["status"] == "DIVERGENCIA":
            d["DIVERGENCIA"] += 1
        elif linha["status"] in ("SEM_PAR_NO_PO", "SEM_PO"):
            d["SEM"] += 1
        else:
            d["ERRO"] += 1
    for arquivo, d in por_arquivo.items():
        ws3.append([arquivo, d["fornecedor"], d["loja"], d["total"], d["OK"],
                    d["DIVERGENCIA"], d["SEM"], d["ERRO"]])
    for i in range(1, 9):
        ws3.column_dimensions[get_column_letter(i)].width = 18
    ws3.column_dimensions["A"].width = 55

    destino = ROOT / "benchmark_conciliacao_erp.xlsx"
    wb.save(destino)
    return destino


def _col_idx(key: str) -> int:
    return [k for k, _ in _COLUNAS].index(key) + 1


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fornecedor", help="Roda so a(s) fixture(s) desse fornecedor "
                                              "(reverificacao pontual, nao gera o xlsx sozinho).")
    args = parser.parse_args()

    config = carregar_config()
    linhas: list[dict] = []

    fixtures_a_rodar = FIXTURES
    if args.fornecedor:
        fixtures_a_rodar = [f for f in FIXTURES if args.fornecedor.lower() in f.fornecedor.lower()]
        if not fixtures_a_rodar:
            raise SystemExit(f"nenhuma fixture pro fornecedor {args.fornecedor!r}")

    conn = connect_db(config.banco)
    try:
        sinonimos = fetch_item_sinonimos(conn)
    finally:
        conn.close()
    print(f"  {len(sinonimos)} sinônimo(s) carregado(s) de dim_item_sinonimo "
          f"(mesmo vocabulário que reconcile_erp_flow.py usaria em produção)")

    por_loja: dict[str, list[InvoiceFixture]] = {}
    for f in fixtures_a_rodar:
        por_loja.setdefault(f.loja, []).append(f)

    print(f"{'='*70}")
    print(f"Benchmark conciliacao ERP — {len(fixtures_a_rodar)} invoice(s), "
          f"{sum(len(f.items) for f in fixtures_a_rodar)} item(ns)")
    print(f"{'='*70}")

    for loja, fixtures in por_loja.items():
        _rodar_loja(config, loja, fixtures, linhas, sinonimos)

    if args.fornecedor:
        print(f"\n{'='*70}\nRodada parcial (--fornecedor) — xlsx NAO gerado automaticamente.\n{'='*70}")
        for linha in linhas:
            print(linha)
        return

    destino = _gerar_xlsx(linhas)
    print(f"\n{'='*70}")
    print(f"Relatorio gravado em: {destino}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
