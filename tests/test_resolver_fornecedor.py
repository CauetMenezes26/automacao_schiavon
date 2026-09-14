"""Testes do de-para de fornecedor da conciliacao (_resolver_fornecedor).

Fixtures em memoria — nao tocam o Postgres.

    python -m pytest tests/test_resolver_fornecedor.py -v
    python tests/test_resolver_fornecedor.py          (sem pytest instalado)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conciliacao.reconcile_quote import (  # noqa: E402
    SUPPLIER_ERP_ONLY,
    SUPPLIER_UNMAPPED,
    _reconcile_one_invoice,
    _resolver_fornecedor,
)
from domain.categorias import CategoriaFornecedor, eh_cotavel  # noqa: E402

CARNE = str(CategoriaFornecedor.CARNE)
PAPEL = str(CategoriaFornecedor.PAPEL)
OUTROS = str(CategoriaFornecedor.OUTROS)


def _alias(id_, nome, categoria=CARNE):
    return {"canonical_id": id_, "canonical_name": nome, "categoria": categoria}


# Espelha `fetch_supplier_aliases(source='invoice')`: chave = alias_norm.
ALIASES = {
    "CHENEY BROTHERS CBI":     _alias(1, "Cheney"),
    "EASTERN QUALITY FOODS":   _alias(5, "Eastern Quality Foods"),
    "PRIME MEATS":             _alias(6, "Prime Meats"),
    "MEAT DEPOT PHILADELPHIA": _alias(9, "Meat Depot"),
}

# Nota de papel/bebida: cadastrada, com alias, mas fora do escopo de carne.
ALIASES_COM_PAPEL = {
    **ALIASES,
    "ALL FLORIDA PAPER": _alias(20, "All Florida Paper", PAPEL),
    "PRIME DISTRIBUTION": _alias(21, "Prime Distribution USA", OUTROS),
}


def _header(nome):
    return {"id": 1, "id_loja": 2, "id_processo": 3, "supplier_name": nome,
            "invoice_number": "X", "invoice_date": None, "total_amount": None}


def test_nome_identico_casa_por_alias_sem_score():
    alias, nivel, score = _resolver_fornecedor("Cheney Brothers (CBI)", ALIASES)
    assert alias["canonical_id"] == 1
    assert nivel == "alias"
    assert score is None   # caminho exato nao gasta fuzzy


def test_razao_social_longa_casa_por_aproximacao():
    # O caso real que era descartado: a invoice traz a distribuidora junto.
    alias, nivel, score = _resolver_fornecedor(
        "Cheney Brothers - Southern Hospitality Foodservice Distributors", ALIASES,
    )
    assert alias["canonical_id"] == 1
    assert nivel == "fuzzy"
    assert score >= 80


def test_sufixo_societario_nao_impede_o_alias_exato():
    # norm_supplier remove INC/LLC, entao 'Inc.' ainda cai no caminho exato.
    alias, nivel, _ = _resolver_fornecedor("Eastern Quality Foods, Inc.", ALIASES)
    assert alias["canonical_id"] == 5
    assert nivel == "alias"


def test_fornecedor_parecido_de_outro_ramo_nao_casa():
    # O falso positivo que a docstring de match_supplier avisa: mercearia x carne.
    alias, nivel, score = _resolver_fornecedor("Prime Distribution USA", ALIASES)
    assert alias is None
    assert nivel == "unmatched"
    assert score < 80


def test_papel_e_bebida_continuam_fora():
    for nome in ("Florida Paper Company", "Coca-Cola Bottling", "Brazilian Fruits LLC"):
        alias, nivel, _ = _resolver_fornecedor(nome, ALIASES)
        assert alias is None, f"{nome} nao deveria casar"
        assert nivel == "unmatched"


def test_nome_vazio_nao_explode():
    for nome in (None, "", "   "):
        alias, nivel, _ = _resolver_fornecedor(nome, ALIASES)
        assert alias is None
        assert nivel == "unmatched"


def test_cadastro_vazio_nao_explode():
    alias, nivel, _ = _resolver_fornecedor("Cheney Brothers", {})
    assert alias is None
    assert nivel == "unmatched"


# ---------------------------------------------------------------------------
# Escopo por categoria
# ---------------------------------------------------------------------------

def test_so_carne_ganha_a_comparacao_por_cotacao():
    """`eh_cotavel` responde "entra TAMBEM na cotacao?", nao "interessa?".

    Todas as categorias vao para o ERP; so carne ganha a segunda comparacao.
    """
    assert eh_cotavel(CARNE) is True
    for so_erp in (PAPEL, OUTROS, "bebida", "hortifruti", "mercearia"):
        assert eh_cotavel(so_erp) is False, so_erp


def test_categoria_ausente_nao_entra_na_cotacao_por_omissao():
    # Sem categoria registrada o fornecedor segue so no ERP ate alguem decidir.
    assert eh_cotavel(None) is False
    assert eh_cotavel("") is False


def test_nota_de_papel_e_encaminhada_ao_erp_e_nao_tratada_como_erro():
    """Papel casa o cadastro, mas nao tem cotacao com que comparar.

    O desenho e: carne vai para ERP + cotacao; o resto vai so para o ERP. Entao
    esta nota NAO e um erro nem um descarte — ela e conciliada em outro lugar,
    com `fat_conciliacao.comparacao = 'erp'`.

    Antes isto dependia de a nota NAO ter alias. Com o de-para por aproximacao
    esse acidente deixou de proteger, e a categoria e o que segura agora.

    `conn=None` de proposito: a checagem acontece antes de qualquer ida ao
    banco, e o teste falha se alguem inverter essa ordem.
    """
    resultado = _reconcile_one_invoice(
        None, _header("All Florida Paper, LLC"), [], ALIASES_COM_PAPEL,
    )
    cabecalho = resultado["header"]
    assert SUPPLIER_ERP_ONLY in cabecalho["issue_codes"]
    assert SUPPLIER_UNMAPPED not in cabecalho["issue_codes"]
    # Roteamento nao e pendencia: ninguem precisa conferir isto.
    assert cabecalho["has_issue"] is False
    assert cabecalho["needs_review"] is False
    assert resultado["items"] == []
    # O fornecedor fica identificado — o fluxo de ERP vai precisar dele.
    assert cabecalho["id_supplier"] == 20
    assert cabecalho["categoria"] == PAPEL


def test_carne_segue_para_a_comparacao_por_cotacao():
    """Carne nao para no roteamento: segue e vai buscar o ciclo de cotacao.

    Com `conn=None` isso se manifesta como AttributeError ao usar a conexao —
    que aqui e justamente a evidencia de que passou. Se algum dia carne for
    desviada para o ERP, a funcao retorna antes e este teste falha.
    """
    try:
        resultado = _reconcile_one_invoice(
            None, _header("Prime Meats"), [], {"PRIME MEATS": _alias(6, "Prime Meats")},
        )
    except AttributeError:
        return   # chegou no banco, logo seguiu para a cotacao
    assert SUPPLIER_ERP_ONLY not in resultado["header"]["issue_codes"], \
        "fornecedor de carne foi desviado para o fluxo de ERP"


def test_mercearia_de_nome_parecido_nao_entra_pelo_fuzzy():
    """'Prime Distribution USA' esta na base e mede 62.5 contra 'Prime Meats'.

    Hoje o piso de 80 ja barra. Este teste guarda o segundo anteparo: mesmo que
    o de-para aproximado passasse a aceitar o par, a categoria impede que uma
    nota de mercearia seja comparada com cotacao de carne.
    """
    resultado = _reconcile_one_invoice(
        None, _header("Prime Distribution USA"), [], ALIASES_COM_PAPEL,
    )
    codigos = resultado["header"]["issue_codes"]
    assert SUPPLIER_ERP_ONLY in codigos or SUPPLIER_UNMAPPED in codigos
    assert resultado["items"] == []


if __name__ == "__main__":
    falhas = 0
    testes = [(n, o) for n, o in sorted(globals().items())
              if n.startswith("test_") and callable(o)]
    for nome, func in testes:
        try:
            func()
            print(f"  ok   {nome}")
        except AssertionError as exc:
            falhas += 1
            print(f"  FALHA {nome}: {exc or 'assert'}")
        except Exception as exc:  # noqa: BLE001
            falhas += 1
            print(f"  ERRO  {nome}: {type(exc).__name__}: {exc}")
    print(f"\n{len(testes) - falhas}/{len(testes)} passaram")
    sys.exit(1 if falhas else 0)
