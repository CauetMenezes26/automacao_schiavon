"""Motor de match e comparacao — normalizacao, tolerancia e casamento de itens.

Este modulo NAO importa nada do projeto (so stdlib + rapidfuzz), de proposito:
assim ele e testavel sem Postgres e pode ser reaproveitado pela frente ERP. O
de-para de vocabulario de item (antes o dict `_SINONIMOS_ITEM`) entra por
parametro (`sinonimos`), carregado do banco pelo chamador.

A cascata de match esta em `match_items()`; a justificativa de por que o preco
nao pode ser chave unica do par esta no docstring de la.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, NamedTuple, Sequence

from rapidfuzz import fuzz

# Tolerancia padrao: so e divergencia se estourar os DOIS limites.
ABS_TOL = Decimal("0.01")
PCT_TOL = Decimal("0.005")

# -----------------------------------------------------------------------------
# Pisos de similaridade
#
# Os tres numeros abaixo foram RECALIBRADOS junto com `norm_item()`. Antes dele
# a comparacao era feita sobre o texto cru, onde marca, codigo de catalogo e o
# nome em portugues diluiam o score; os pisos antigos (60/50/70) refletiam essa
# nuvem achatada — o comentario original registrava "o melhor par CORRETO
# observado faz 69".
#
# Com `norm_item()`, medido sobre os 4 pares corretos e 5 pares errados que
# existem em base, as duas nuvens se separam:
#
#     pares corretos   88.0 .. 91.9
#     pares errados    24.6 .. 50.0   (o pior e Chuck Roll x Knuckle, 50.0)
#
# Vao de 38 pontos. Os pisos ficam dentro dele, longe das duas bordas.
#
# ATENCAO ao mexer: sao 9 pares medidos, amostra pequena. Subir demais volta a
# perder par correto em silencio (sai como 'no_quote_for_item'); descer demais
# ressuscita o falso positivo por preco que o teste
# `test_preco_igual_nao_basta_para_casar_produtos_sem_relacao` guarda.
# Qualquer mudanca aqui deve ser medida de novo, nao estimada.
# -----------------------------------------------------------------------------

# Etapa 2 — a descricao decide SOZINHA, sem apoio do preco. Piso mais alto que
# o da Etapa 1 justamente por isso.
#
# token_set_ratio no lugar de token_sort_ratio: a invoice carrega marca e pack
# size a mais, e o sort_ratio pune diferenca de tamanho.
FUZZY_THRESHOLD = 70.0

# Etapa 1 — o preco ja bate, entao a descricao so precisa dar apoio minimo ao
# par. Sem este piso, dois produtos sem relacao casam so por custarem o mesmo:
# "Beef, Chuck Roll 'Neck Off' Boneless" casou com "BEEF - Knuckle / Patinho"
# porque ambos custavam 4.99 e era o unico candidato.
EXACT_MIN_SCORE = 65.0

# Acima do piso mas abaixo disto, o par se sustenta em pouca evidencia textual.
# Aceita, mas marca para conferencia humana: se o produto estiver errado, a
# divergencia verdadeira passa despercebida como "coerente".
#
# Mantido em 70 de proposito. O corpus nao sustenta um valor mais alto: tirando
# os pares que `grades_conflict` ja barra antes do score, o pior par CORRETO faz
# 88.0 e o pior par ERRADO faz 86.8 ('CHICKEN Breast Boneless' x 'CHICKEN
# Thighs Boneless', que so diferem no corte). As nuvens quase se tocam, entao
# nao existe fronteira de confianca defensavel nessa faixa — quem separa esse
# par e a atribuicao 1-para-1 gulosa da Etapa 2, nao o piso.
CONFIDENT_SCORE = 70.0

# Sufixos societarios removidos ao normalizar nome de fornecedor.
_COMPANY_SUFFIXES = frozenset({
    "INC", "LLC", "LLP", "LP", "CORP", "CORPORATION", "CO", "COMPANY",
    "LTD", "LTDA", "SA", "SL", "GROUP", "USA",
})

# Grades de carne que nunca podem casar entre si.
_GRADES = ("CHOICE", "SELECT", "PRIME", "ANGUS", "WAGYU")

# Rotulos de cabecalho que vazaram da planilha para dentro dos dados.
HEADER_LABELS = frozenset({"ITEM", "PRICE", "COMMENTS", "PRECO", "PRODUTO"})

# Cauda de catalogo da invoice: ' - MARCA, Item #NNNNN' no fim da descricao.
# Padrao estrutural, presente nas quatro descricoes reais em base.
_CAUDA_INVOICE_RE = re.compile(r"\s*-\s*[^,]+,\s*ITEM\s*#?\s*\d+\s*$", re.IGNORECASE)

# Codigo interno no fim da linha de cotacao: ' - 40622'.
_CAUDA_COTACAO_RE = re.compile(r"\s*-\s*\d{4,6}\s*$")

# De-para de vocabulario entre os dois lados (abreviacao de mercado, traducao
# PT/EN, sinonimo de corte). ERA um dict hardcoded aqui; virou dado de banco
# (dwschiavon2.dim_item_sinonimo), injetado via parametro `sinonimos` para nao
# quebrar a regra de este modulo nao importar nada do projeto. O racional e as
# entradas iniciais estao em manutencao/seed_item_sinonimos.py.
#
# Contrato: mapa {token_normalizado -> termo alvo}, aplicado token a token aos
# DOIS lados. Ausencia de sinonimos so enfraquece o match (errar para menos e
# seguro); nunca casa produto errado.

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_ITEM_CODE_RE = re.compile(r"-\s*(\d{4,6})\s*$")


# ---------------------------------------------------------------------------
# Normalizacao
# ---------------------------------------------------------------------------

def norm_text(value: str | None) -> str:
    """Maiusculas, sem acento, sem pontuacao, espacos colapsados.

    Trata o espaco nao-separavel (U+00A0) que aparece nos nomes vindos do Excel.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("\xa0", " ").upper()
    text = re.sub(r"[^0-9A-Z]+", " ", text)
    return " ".join(text.split())


def norm_supplier(value: str | None) -> str:
    """`norm_text` + remocao dos sufixos societarios (INC, LLC, CORP...)."""
    tokens = [t for t in norm_text(value).split() if t not in _COMPANY_SUFFIXES]
    return " ".join(tokens)


def norm_item(value: str | None, sinonimos: Mapping[str, str] | None = None) -> str:
    """Normaliza descricao de item para COMPARACAO entre os dois lados.

    `norm_text` sozinho nao basta porque os dois lados falam linguas diferentes
    sobre o mesmo produto. Medido nos 4 itens reais em base, so 1 dos 4 pares
    corretos alcancava o piso de 60; com os dois passos abaixo, os 4 passam de
    88, com margem de +41 a +58 sobre o segundo colocado.

    Passo 1 — corta a cauda de catalogo. E estrutura, nao vocabulario: a marca
    e o codigo interno do distribuidor ('- AMICK, Item #227059') nao existem do
    lado da cotacao, entao so diluem o score.

    Passo 2 — aplica o de-para de vocabulario `sinonimos` (mapa
    {token -> termo alvo}, vem de dim_item_sinonimo). Sem ele, o unico token em
    comum entre 'CHIX TENDER JUMBO CVP' e 'CHICKEN - Tender / Sassami de Frango'
    e 'TENDER', e o par correto faz 46. `sinonimos=None` -> Passo 2 nao traduz
    (degrada com seguranca).

    NAO usar para exibir nome ao usuario: o resultado e texto de comparacao.
    """
    de_para = sinonimos or {}
    texto = _CAUDA_INVOICE_RE.sub("", value or "")
    texto = _CAUDA_COTACAO_RE.sub("", texto)
    return " ".join(de_para.get(t, t) for t in norm_text(texto).split())


def is_header_row(item_name: str | None) -> bool:
    """True para a linha de cabecalho da planilha que virou dado (item_name='ITEM')."""
    return norm_text(item_name) in HEADER_LABELS


def extract_item_code(item_name: str | None) -> str | None:
    """Codigo numerico ao final do nome do item ('... - 40122' -> '40122')."""
    if not item_name:
        return None
    match = _ITEM_CODE_RE.search(str(item_name).replace("\xa0", " ").strip())
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Preco
# ---------------------------------------------------------------------------

def parse_price_range(price_raw: str | None) -> tuple[Decimal, Decimal] | None:
    """Faixa de preco a partir do texto original da celula.

    '$4.95/5.35/$5.41' -> (4.95, 5.41)      '$5,38' -> (5.38, 5.38)
    'N/A' / '' / None  -> None

    O fornecedor as vezes cota uma faixa em vez de um valor unico; nesse caso a
    faixa inteira e considerada dentro do combinado.
    """
    if price_raw is None:
        return None
    cleaned = str(price_raw).strip().replace("$", "").replace(",", ".")
    if not cleaned or cleaned.upper() in ("N/A", "NA", "-"):
        return None
    values: list[Decimal] = []
    for token in _NUMBER_RE.findall(cleaned):
        try:
            values.append(Decimal(token))
        except InvalidOperation:
            continue
    if not values:
        return None
    return min(values), max(values)


def within_tolerance(
    a: Decimal | float | None,
    b: Decimal | float | None,
    abs_tol: Decimal = ABS_TOL,
    pct_tol: Decimal = PCT_TOL,
) -> bool:
    """True se `a` e `b` sao considerados iguais.

    So diverge se estourar os DOIS limites: uma diferenca de 1 centavo num item
    de 2 dolares e percentualmente grande mas irrelevante, e 0,3% num item de
    500 dolares e o arredondamento de sempre.
    """
    if a is None or b is None:
        return False
    a, b = Decimal(str(a)), Decimal(str(b))
    diff = abs(a - b)
    if diff <= abs_tol:
        return True
    if b == 0:
        return False
    return (diff / abs(b)) <= pct_tol


class PriceVerdict(NamedTuple):
    status: str                  # 'ok' | 'above' | 'below'
    diff: Decimal | None         # invoice - cotacao
    diff_pct: Decimal | None     # em %


def is_unit_priced(
    quantity: Decimal | float | None,
    unit_price: Decimal | float | None,
    total_price: Decimal | float | None,
    rel_tol: Decimal = Decimal("0.01"),
) -> bool:
    """True se a linha esta precificada por unidade/caixa, e nao por libra.

    A cotacao e sempre por LB. Na invoice de carne o `unit_price` tambem e por
    LB, mesmo com `unit='CS'`: o total fecha com o peso, nao com a quantidade
    de caixas (3 CS x 6.01 != 1256.09, mas 209.00 lb x 6.01 == 1256.09).

    Numa linha vendida por caixa ou unidade, ao contrario, quantidade x preco
    fecha o total (1 x 85.90 == 85.90 de uma caixa de alface). Essa linha nao e
    comparavel com um preco por libra — daí `unit_mismatch`.

    A coluna `unit` nao serve para isso: esta suja ('CASE', 'case', 'CS', 'CX',
    '25#') e nula em quase 10% dos itens.
    """
    if quantity is None or unit_price is None or total_price is None:
        return False
    qty = Decimal(str(quantity))
    price = Decimal(str(unit_price))
    total = Decimal(str(total_price))
    if total == 0:
        return False
    esperado = qty * price
    return abs(esperado - total) <= abs(total) * rel_tol


def compare_price(
    invoice_price: Decimal | float | None,
    quote_price: Decimal | float | None,
    price_raw: str | None = None,
    abs_tol: Decimal = ABS_TOL,
    pct_tol: Decimal = PCT_TOL,
) -> PriceVerdict:
    """Compara o preco faturado com o cotado, respeitando faixa e tolerancia.

    Convencao do projeto: `diff = invoice - cotacao` (positivo = cobraram a mais).
    O percentual e sempre relativo ao preco principal da cotacao.
    """
    if invoice_price is None or quote_price is None:
        return PriceVerdict("ok", None, None)

    invoice = Decimal(str(invoice_price))
    quote = Decimal(str(quote_price))

    diff = invoice - quote
    diff_pct = (diff / quote * 100) if quote else None

    low, high = quote, quote
    band = parse_price_range(price_raw)
    if band is not None:
        low, high = min(band[0], quote), max(band[1], quote)

    if low <= invoice <= high:
        return PriceVerdict("ok", diff, diff_pct)
    if within_tolerance(invoice, high, abs_tol, pct_tol) or \
            within_tolerance(invoice, low, abs_tol, pct_tol):
        return PriceVerdict("ok", diff, diff_pct)
    return PriceVerdict("above" if invoice > high else "below", diff, diff_pct)


# ---------------------------------------------------------------------------
# Grades de carne
# ---------------------------------------------------------------------------

def grades_in(text: str | None) -> frozenset[str]:
    """Grades citadas no texto ('BEEF Coulotte 2pc (Select)' -> {'SELECT'})."""
    tokens = set(norm_text(text).split())
    return frozenset(g for g in _GRADES if g in tokens)


def grades_conflict(a: str | None, b: str | None) -> bool:
    """True se os dois lados citam grades e elas nao tem nada em comum.

    CHOICE e SELECT sao cortes diferentes com precos diferentes; casa-los
    produziria uma divergencia que nao existe.
    """
    ga, gb = grades_in(a), grades_in(b)
    return bool(ga and gb and not (ga & gb))


# ---------------------------------------------------------------------------
# Match de itens
# ---------------------------------------------------------------------------

class InvoiceLine(NamedTuple):
    key: Any                       # invoice_items.id
    description: str | None
    unit_price: Decimal | None


class QuoteLine(NamedTuple):
    key: Any                       # price_quote.id
    item_name: str | None
    price: Decimal
    price_raw: str | None = None


class ItemMatch(NamedTuple):
    invoice_key: Any
    quote_key: Any | None
    match_level: str               # 'exact' | 'fuzzy' | 'unmatched'
    match_score: float | None
    ambiguous: bool = False        # empate no desempate -> conferencia humana


def match_supplier(
    name: str | None,
    candidates: Sequence[str],
    threshold: float = 80.0,
) -> tuple[str | None, float]:
    """Melhor candidato para um nome de fornecedor, por `token_set_ratio`.

    `token_set_ratio` porque a razao social da invoice costuma ser um
    superconjunto do nome curto do cadastro ('CHENEY BROTHERS, INC.' / 'Cheney').

    ATENCAO: usar so para SUGERIR de-para para revisao humana, nunca para
    decidir sozinho — 'Prime Meats' (carne) e 'Prime Distribution USA'
    (mercearia) pontuam alto e sao fornecedores diferentes.
    """
    target = norm_supplier(name)
    if not target or not candidates:
        return None, 0.0
    best, best_score = None, 0.0
    for candidate in candidates:
        score = fuzz.token_set_ratio(target, norm_supplier(candidate))
        if score > best_score:
            best, best_score = candidate, score
    return (best, best_score) if best_score >= threshold else (None, best_score)


def match_items(
    invoice_lines: Sequence[InvoiceLine],
    quote_lines: Sequence[QuoteLine],
    fuzzy_threshold: float = FUZZY_THRESHOLD,
    abs_tol: Decimal = ABS_TOL,
    pct_tol: Decimal = PCT_TOL,
    *,
    sinonimos: Mapping[str, str] | None = None,
) -> list[ItemMatch]:
    """Casa itens da invoice com itens da cotacao, em cascata.

    Etapa 1 — assinatura de preco. Casa o que e facil e exato. Uma linha da
    cotacao e uma tabela de preco, entao pode servir a varias linhas da invoice
    (N-para-1 e legitimo aqui).

    Etapa 2 — nome do item, so sobre o residuo. Por construcao, tudo que sobrou
    da Etapa 1 tem preco que nao bate com nenhuma cotacao — ou seja, e
    exatamente onde estao as divergencias. Guloso e 1-para-1.

    Etapa 3 — o que sobrou fica 'unmatched'.

    Por que o preco nao pode ser chave unica do par: se o par so existisse
    quando o preco bate, todo item faturado com preco errado sairia como "sem
    cotacao" e a divergencia que se quer achar ficaria invisivel.

    `sinonimos`: de-para de vocabulario ({token -> termo alvo}, de
    dim_item_sinonimo), carregado uma vez pelo chamador e repassado a todas as
    normalizacoes. `None` -> match sobre texto cru, so mais fraco.

    A ordem de `invoice_lines` e preservada no retorno.
    """
    quotes = [q for q in quote_lines if q.price is not None and Decimal(str(q.price)) > 0]

    matches: dict[Any, ItemMatch] = {}
    residual: list[InvoiceLine] = []

    # --- Etapa 1: assinatura de preco -------------------------------------
    for line in invoice_lines:
        if line.unit_price is None:
            residual.append(line)
            continue

        candidates = [
            q for q in quotes
            if within_tolerance(line.unit_price, q.price, abs_tol, pct_tol)
            and not grades_conflict(line.description, q.item_name)
        ]
        if not candidates:
            residual.append(line)
            continue

        # Preco igual nao basta: dois produtos sem relacao podem custar o mesmo.
        # A descricao precisa dar ao menos um apoio minimo ao par.
        scored = sorted(
            ((float(fuzz.token_set_ratio(norm_item(line.description, sinonimos),
                                         norm_item(q.item_name, sinonimos))), q)
             for q in candidates),
            key=lambda pair: pair[0],
            reverse=True,
        )
        top_score, top_quote = scored[0]
        if top_score < EXACT_MIN_SCORE:
            residual.append(line)
            continue

        empatado = sum(1 for score, _ in scored if score == top_score) > 1
        matches[line.key] = ItemMatch(
            line.key, top_quote.key, "exact", top_score,
            ambiguous=empatado or top_score < CONFIDENT_SCORE,
        )

    # --- Etapa 2: nome do item, sobre o residuo ---------------------------
    if residual and quotes:
        inv_texts = [norm_item(line.description, sinonimos) for line in residual]
        quo_texts = [norm_item(q.item_name, sinonimos) for q in quotes]

        # Laco simples em vez de rapidfuzz.process.cdist: cdist exige numpy so
        # para montar a matriz, e aqui ela e minuscula (dezenas x dezenas, um
        # fornecedor por vez). `score_cutoff` faz o proprio rapidfuzz descartar
        # o que esta abaixo do limiar, devolvendo 0.
        pairs: list[tuple[float, int, int]] = []
        for i, inv in enumerate(inv_texts):
            for j, quo in enumerate(quo_texts):
                score = fuzz.token_set_ratio(inv, quo, score_cutoff=fuzzy_threshold)
                if score and not grades_conflict(residual[i].description,
                                                 quotes[j].item_name):
                    pairs.append((float(score), i, j))
        pairs.sort(key=lambda p: (-p[0], p[1], p[2]))

        used_invoice: set[int] = set()
        used_quote: set[int] = set()
        for score, i, j in pairs:
            if i in used_invoice or j in used_quote:
                continue
            used_invoice.add(i)
            used_quote.add(j)
            matches[residual[i].key] = ItemMatch(
                residual[i].key, quotes[j].key, "fuzzy", score,
                ambiguous=score < CONFIDENT_SCORE,
            )

    # --- Etapa 3: o que sobrou --------------------------------------------
    return [
        matches.get(line.key, ItemMatch(line.key, None, "unmatched", None))
        for line in invoice_lines
    ]
