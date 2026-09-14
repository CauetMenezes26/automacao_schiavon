"""Conciliação Cotação × Invoice — regras da nota.

`_resolver_fornecedor` acha o fornecedor do nome lido; `_reconcile_one_invoice`
compara o preço faturado com o cotado, item a item, e devolve o resultado (não
grava). A orquestração do período e a gravação moram em
`crawler/flow/conciliacao_flow.py`; o motor puro em `commons/matcher.py`.

Testável sem Postgres — é o que `tests/test_resolver_fornecedor.py` exercita.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from commons.matcher import (
    InvoiceLine,
    QuoteLine,
    compare_price,
    is_unit_priced,
    match_items,
    match_supplier,
    norm_supplier,
)
from domain.categorias import eh_cotavel
from domain.conciliacao_codes import IssueCode
from domain.service.conciliacao_service import (
    fetch_quote_lines,
    fetch_request_for_date,
)

# Abaixo disto a leitura do PDF não é confiável o bastante para acusar erro.
MIN_READING_CONFIDENCE = Decimal("70")

# Piso do de-para aproximado de fornecedor. Medido contra o cadastro atual: o
# pior falso positivo ('Prime Distribution USA' x 'Prime Meats') fica em 62.5, e
# papel/bebida não passa de 43 — o vão até o primeiro acerto legítimo
# ('Cheney Brothers - Southern Hospitality...' x 'Cheney Brothers (CBI)', 88.2)
# é largo. Subir este número volta a derrubar a nota; descer encosta no ruído.
SUPPLIER_FUZZY_THRESHOLD = 80.0

# Códigos de divergência: vocabulário e descrições em utils/conciliacao_codes.py.
# Apelidos locais para não reescrever o corpo do módulo. IssueCode é StrEnum, então
# cada apelido É a string ("supplier_unmapped" etc.) — comparações e `sorted` seguem
# iguais, e o SUPPLIER_ERP_ONLY continua significando "não-carne: compara com o ERP,
# não é fora de escopo".
SUPPLIER_UNMAPPED = IssueCode.SUPPLIER_UNMAPPED
SUPPLIER_FUZZY_MATCH = IssueCode.SUPPLIER_FUZZY_MATCH
SUPPLIER_ERP_ONLY = IssueCode.SUPPLIER_ERP_ONLY
NO_QUOTE_FOR_SUPPLIER = IssueCode.NO_QUOTE_FOR_SUPPLIER
NO_QUOTE_FOR_ITEM = IssueCode.NO_QUOTE_FOR_ITEM
PRICE_ABOVE_QUOTE = IssueCode.PRICE_ABOVE_QUOTE
PRICE_BELOW_QUOTE = IssueCode.PRICE_BELOW_QUOTE
UNIT_MISMATCH = IssueCode.UNIT_MISMATCH
LOW_VISION_CONFIDENCE = IssueCode.LOW_VISION_CONFIDENCE
HANDWRITTEN_PRESENT = IssueCode.HANDWRITTEN_PRESENT


def _week_bounds(reference: date) -> tuple[date, date]:
    """Segunda a domingo da semana da data informada."""
    start = reference - timedelta(days=reference.weekday())
    return start, start + timedelta(days=6)


def _resolver_fornecedor(
    nome: str | None, aliases: dict,
) -> tuple[dict | None, str, float | None]:
    """Acha o fornecedor do nome lido na nota. Retorna (alias, nível, score).

    Dois passos, nesta ordem:

      1. `alias` — o nome normalizado é chave da tabela de de-para. Barato e
         inequívoco, e continua sendo o caminho normal.
      2. `fuzzy` — a razão social da invoice costuma ser um superconjunto do
         nome do cadastro ('Cheney Brothers - Southern Hospitality Foodservice
         Distributors' contra 'Cheney Brothers (CBI)'). Antes disto a nota era
         descartada como `supplier_unmapped`, e o de-para aproximado que já
         existia em `match_supplier` nunca era chamado daqui.

    O passo 2 nunca decide sozinho: quem chama marca a nota para conferência,
    porque nomes parecidos podem ser fornecedores diferentes — é o caso de
    'Prime Meats' (carne) e 'Prime Distribution USA' (mercearia).
    """
    chave = norm_supplier(nome)
    exato = aliases.get(chave)
    if exato:
        return exato, "alias", None

    candidato, score = match_supplier(
        nome, list(aliases.keys()), threshold=SUPPLIER_FUZZY_THRESHOLD,
    )
    if candidato:
        return aliases[candidato], "fuzzy", score
    return None, "unmatched", score


def _reconcile_one_invoice(
    conn, header: dict, items: list[dict], aliases: dict, sinonimos: dict | None = None,
) -> dict:
    """Concilia uma invoice. Retorna {header: ..., items: [...]}.

    Para nos primeiros passos quando não há como comparar: fornecedor não
    mapeado ou sem cotação no ciclo não são divergência de preço, e descer aos
    itens nesses casos só produziria ruído (a maioria das invoices do banco é
    de papel e bebida, fora do escopo da cotação de carne).
    """
    base = {
        "id_invoice_header": header["id"],
        "id_loja": header["id_loja"],
        "id_processo": header["id_processo"],
        "supplier_invoice": header.get("supplier_name"),
        "invoice_number": header.get("invoice_number"),
        "invoice_date": header.get("invoice_date"),
        "total_invoice": header.get("total_amount"),
    }

    alias, nivel, score = _resolver_fornecedor(header.get("supplier_name"), aliases)
    if not alias:
        return {
            "header": {**base, "match_level": "unmatched",
                       "supplier_match_score": score,
                       "issue_codes": [SUPPLIER_UNMAPPED],
                       "has_issue": True, "needs_review": True},
            "items": [],
            "quote_lines": [],
        }

    # Não é carne: esta comparação (`cotacao`) não se aplica. A nota não morre
    # aqui — ela segue para a comparação contra o ERP, que é a única que existe
    # para papel, bebida e hortifruti. Ver `fat_conciliacao.comparacao`, que
    # admite as duas linhas para a MESMA nota.
    if not eh_cotavel(alias.get("categoria")):
        return {
            "header": {**base, "match_level": nivel,
                       "supplier_match_score": score,
                       "id_supplier": alias["canonical_id"],
                       "supplier_canonical": alias["canonical_name"],
                       "categoria": alias.get("categoria"),
                       "issue_codes": [SUPPLIER_ERP_ONLY],
                       "has_issue": False, "needs_review": False},
            "items": [],
            "quote_lines": [],
        }

    # Casou por aproximação: a nota entra na conciliação, mas sai marcada.
    por_aproximacao = nivel == "fuzzy"
    if por_aproximacao:
        print(f"  ≈ {str(header.get('supplier_name'))[:38]:<40} "
              f"→ {alias['canonical_name']}  ({score:.0f}%, conferir)")

    base.update({
        "id_supplier": alias["canonical_id"],
        "supplier_canonical": alias["canonical_name"],
        "match_level": nivel,
        "supplier_match_score": score,
    })

    request = fetch_request_for_date(conn, header["invoice_date"])
    quotes = (
        fetch_quote_lines(conn, request["id"], alias["canonical_id"])
        if request and alias["canonical_id"] else []
    )
    if not quotes:
        codigos = [NO_QUOTE_FOR_SUPPLIER]
        if por_aproximacao:
            codigos.append(SUPPLIER_FUZZY_MATCH)
        return {
            "header": {**base,
                       "id_request": request["id"] if request else None,
                       "issue_codes": sorted(codigos),
                       "has_issue": True, "needs_review": True},
            "items": [],
            "quote_lines": [],
        }

    base["id_request"] = request["id"]

    confidence = header.get("reading_confidence")
    leitura_fraca = confidence is not None and Decimal(str(confidence)) < MIN_READING_CONFIDENCE

    by_id = {q["id"]: q for q in quotes}
    matches = match_items(
        [InvoiceLine(i["id"], i.get("description"), i.get("unit_price")) for i in items],
        [QuoteLine(q["id"], q["item_name"], q["price"], q.get("price_raw")) for q in quotes],
        sinonimos=sinonimos,
    )
    matches_by_item = {m.invoice_key: m for m in matches}

    linhas: list[dict] = []
    for item in items:
        match = matches_by_item[item["id"]]
        quote = by_id.get(match.quote_key) if match.quote_key else None

        row = {
            "id_invoice_item": item["id"],
            "description_invoice": item.get("description"),
            "qty_invoice": item.get("quantity"),
            "unit_invoice": item.get("unit"),
            "price_invoice": item.get("unit_price"),
            "match_level": match.match_level,
            "match_score": match.match_score,
        }

        issues: list[str] = []
        needs_review = bool(match.ambiguous) or leitura_fraca
        if leitura_fraca:
            issues.append(LOW_VISION_CONFIDENCE)
        if item.get("handwritten_notes"):
            issues.append(HANDWRITTEN_PRESENT)
            needs_review = True

        if quote is None:
            issues.append(NO_QUOTE_FOR_ITEM)
            row.update({"issue_codes": issues, "has_issue": True,
                        "needs_review": needs_review})
            linhas.append(row)
            continue

        row.update({
            "id_price_quote": quote["id"],
            "item_name_quote": quote["item_name"],
            "item_code": quote.get("item_code"),
            "price_other": quote["price"],
            "price_other_raw": quote.get("price_raw"),
            "quote_date": quote.get("quote_date"),
        })

        # Preço por caixa não é comparável com preço por libra: marca para
        # conferência humana sem calcular diferença.
        if is_unit_priced(item.get("quantity"), item.get("unit_price"),
                          item.get("total_price")):
            issues.append(UNIT_MISMATCH)
            row.update({"issue_codes": issues, "has_issue": True,
                        "needs_review": True})
            linhas.append(row)
            continue

        verdict = compare_price(item.get("unit_price"), quote["price"],
                                quote.get("price_raw"))
        row["price_diff"] = verdict.diff
        row["price_diff_pct"] = verdict.diff_pct
        if verdict.status == "above":
            issues.append(PRICE_ABOVE_QUOTE)
        elif verdict.status == "below":
            issues.append(PRICE_BELOW_QUOTE)

        divergente = verdict.status != "ok"
        row.update({
            "issue_codes": issues,
            "has_issue": divergente or needs_review,
            "needs_review": needs_review,
        })
        linhas.append(row)

    header_issues = {c for r in linhas for c in r["issue_codes"]}
    if por_aproximacao:
        header_issues.add(SUPPLIER_FUZZY_MATCH)
    return {
        "header": {**base,
                   "issue_codes": sorted(header_issues),
                   "has_issue": any(r["has_issue"] for r in linhas) or por_aproximacao,
                   "needs_review": (any(r["needs_review"] for r in linhas)
                                    or por_aproximacao)},
        "items": linhas,
        "quote_lines": [q["id"] for q in quotes],
    }
