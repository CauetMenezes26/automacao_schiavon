"""Scripts de intervenção manual — fora do pipeline.

Nada aqui roda no tick automático. São correções de dados, cargas iniciais e
relatórios que alguém dispara à mão:

    python -m manutencao.importar_precos          Excel de cotação -> price_quote
    python -m manutencao.comparar_fornecedores    relatório de preço entre fornecedores
    python -m manutencao.seed_meat_suppliers      cadastro dos fornecedores de carne
    python -m manutencao.seed_supplier_alias      de-para de nomes de fornecedor
    python -m manutencao.migrar_sinonimos_sheets  carga inicial do de-para -> aba De-Para (Google Sheets)
    python -m manutencao.importar_sinonimos       força sync da aba De-Para (Sheets) -> dim_item_sinonimo
    python -m manutencao.backfill_price_quote     correções no histórico de price_quote
    python -m manutencao.migrate_suppliers        carga inicial vinda de planilha externa

Por serem correções pontuais, é normal que atravessem mais de uma etapa — o que
o código do pipeline evita fazer.
"""
