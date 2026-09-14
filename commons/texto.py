"""Helpers de string usados na gravacao em banco."""

from __future__ import annotations


def truncar(value: str | None, max_len: int) -> str | None:
    """Corta a string no limite da coluna; evita 'value too long for VARCHAR'.

    Varios campos vem de leitura de PDF e nao tem tamanho garantido - o nome do
    fornecedor lido pelo Vision, por exemplo, pode vir com o endereco colado.
    """
    if value is None:
        return None
    return value[:max_len] if len(value) > max_len else value
