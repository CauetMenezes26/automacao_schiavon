"""Sincronização do de-para de vocabulário de item a partir da planilha DE-PARA.

Até aqui os sinônimos (`CHIX→CHICKEN`, `SASSAMI→TENDER`, ...) viviam hardcoded em
`conciliacao/matcher.py`. Agora são dado de banco (`dwschiavon2.dim_item_sinonimo`),
alimentado por uma planilha que o time de domínio edita
(`files/sinonimos/DE_PARA_ITENS.xlsx`, colunas `DE` | `PARA`).

O pipeline (`main.py`) chama `sincronizar_sinonimos(env)` como **preâmbulo**, no
início de cada execução: lê a planilha e faz upsert idempotente. Se a planilha
estiver aberta/travada no Excel, **pula e deixa para a próxima execução** — o
preâmbulo nunca derruba o pipeline, e a tabela segue valendo com o último estado
bom.

A leitura/validação (`sinonimos_de_planilha`) não toca o banco, de propósito:
testável sem Postgres, como `matcher.py`.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Callable, NamedTuple

import openpyxl

from commons.matcher import norm_text
from commons.paths import SINONIMOS_DIR

_COL_DE = "DE"
_COL_PARA = "PARA"


class Sinonimo(NamedTuple):
    """Uma linha pronta para `dim_item_sinonimo`."""

    termo:      str          # como veio na planilha, limpo
    termo_norm: str          # norm_text(termo): chave de busca, 1 token
    canonico:   str          # norm_text(PARA): termo alvo, como a invoice escreve


class Relatorio(NamedTuple):
    """Resumo de uma leitura de planilha."""

    lidas:      int
    validas:    int
    invalidas:  int
    duplicadas: int
    descartes:  list[tuple[int, str]]   # (linha, motivo) — inválidas e duplicadas

    @staticmethod
    def vazio() -> "Relatorio":
        return Relatorio(0, 0, 0, 0, [])

    def somar(self, outro: "Relatorio") -> "Relatorio":
        return Relatorio(
            self.lidas + outro.lidas,
            self.validas + outro.validas,
            self.invalidas + outro.invalidas,
            self.duplicadas + outro.duplicadas,
            self.descartes + outro.descartes,
        )


def _clean_cell(value) -> str:
    """Texto da célula sem espaço não-separável (U+00A0, vem do Excel) e sem
    espaços sobrando."""
    return str(value).replace("\xa0", " ").strip() if value is not None else ""


def _mapa_colunas(header: tuple) -> dict[str, int]:
    """Índice das colunas `DE` e `PARA` na linha de cabeçalho, em qualquer ordem."""
    mapa: dict[str, int] = {}
    for idx, cell in enumerate(header or ()):
        rotulo = _clean_cell(cell).upper()
        if rotulo in (_COL_DE, _COL_PARA) and rotulo not in mapa:
            mapa[rotulo] = idx
    faltando = [c for c in (_COL_DE, _COL_PARA) if c not in mapa]
    if faltando:
        raise ValueError(
            f"Excel inválido: coluna(s) {', '.join(faltando)} não encontrada(s) "
            f"na primeira linha (cabeçalho esperado: 'DE' | 'PARA')."
        )
    return mapa


def sinonimos_de_planilha(path: Path) -> tuple[list[Sinonimo], Relatorio]:
    """Lê a planilha DE-PARA e devolve os sinônimos válidos + o relatório.

    Regras:
      * primeira aba; linha 1 = cabeçalho com `DE` e `PARA` (qualquer ordem);
      * linha totalmente vazia é ignorada em silêncio;
      * só um lado preenchido, `DE` que normaliza para vazio, ou `termo_norm` com
        mais de um token (nunca casaria no `.split()` de `norm_item`) → inválida;
      * `termo_norm` repetido no arquivo → a última linha vence.

    Não toca o banco. `ValueError` só quando a planilha nem tem as colunas.
    """
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        linhas = ws.iter_rows(values_only=True)
        try:
            header = next(linhas)
        except StopIteration:
            raise ValueError("Excel inválido: planilha sem linhas.")
        cols = _mapa_colunas(header)
        i_de, i_para = cols[_COL_DE], cols[_COL_PARA]

        por_termo: dict[str, Sinonimo] = {}
        lidas = validas = invalidas = duplicadas = 0
        descartes: list[tuple[int, str]] = []

        for n, row in enumerate(linhas, start=2):
            de_raw = _clean_cell(row[i_de] if len(row) > i_de else None)
            para_raw = _clean_cell(row[i_para] if len(row) > i_para else None)

            if not de_raw and not para_raw:
                continue
            lidas += 1

            if bool(de_raw) != bool(para_raw):
                invalidas += 1
                descartes.append((n, "só um lado preenchido"))
                continue

            termo_norm = norm_text(de_raw)
            canonico = norm_text(para_raw)
            if not termo_norm:
                invalidas += 1
                descartes.append((n, f"DE sem conteúdo útil: {de_raw!r}"))
                continue
            if not canonico:
                invalidas += 1
                descartes.append((n, f"PARA sem conteúdo útil: {para_raw!r}"))
                continue
            if " " in termo_norm:
                invalidas += 1
                descartes.append(
                    (n, f"DE com mais de um token nunca casaria: {termo_norm!r}")
                )
                continue

            if termo_norm in por_termo:
                duplicadas += 1
                descartes.append((n, f"termo repetido, última vence: {termo_norm!r}"))
            por_termo[termo_norm] = Sinonimo(de_raw, termo_norm, canonico)

        validas = len(por_termo)
        return list(por_termo.values()), Relatorio(
            lidas, validas, invalidas, duplicadas, descartes
        )
    finally:
        wb.close()


def _planilhas_em(directory: Path) -> list[Path]:
    """Arquivos Excel do diretório, ignorando o lock do Excel (`~$...`)."""
    if not directory.exists():
        return []
    return sorted(
        p for p in directory.iterdir()
        if p.is_file()
        and p.suffix.lower() in (".xlsx", ".xls")
        and not p.name.startswith("~$")
    )


def _sincronizar(
    conn,
    directory: Path = SINONIMOS_DIR,
    *,
    _leitor: Callable[[Path], tuple[list[Sinonimo], Relatorio]] = sinonimos_de_planilha,
    aplicar: bool = True,
) -> Relatorio | None:
    """Lê as planilhas DE-PARA do diretório e faz upsert dos sinônimos.

    Retorna `None` quando não há nada a fazer (diretório inexistente ou vazio).
    Uma planilha ilegível (aberta no Excel, gravação parcial) é **pulada com
    aviso**, nunca propaga. Idempotente: rodar de novo com a mesma planilha não
    muda nada.

    `_leitor` é injetável só para teste. `aplicar=False` faz dry-run (não grava).
    """
    from domain.service.conciliacao_service import (
        fetch_item_sinonimos,
        marcar_reprocesso_por_vocabulario,
        upsert_item_sinonimos,
    )

    planilhas = _planilhas_em(directory)
    if not planilhas:
        return None

    por_termo: dict[str, Sinonimo] = {}
    relatorio = Relatorio.vazio()
    lidas_ok = 0

    for path in planilhas:
        try:
            rows, rel = _leitor(path)
        except (PermissionError, OSError, zipfile.BadZipFile, KeyError, ValueError) as exc:
            print(f"  ⚠ planilha de sinônimos indisponível ({path.name}: {exc}); "
                  "pulando nesta execução")
            continue
        except Exception as exc:  # noqa: BLE001 — preâmbulo não pode derrubar o pipeline
            print(f"  ⚠ erro lendo {path.name}: {exc}; pulando nesta execução")
            continue

        lidas_ok += 1
        relatorio = relatorio.somar(rel)
        for s in rows:                       # último arquivo vence em caso de choque
            por_termo[s.termo_norm] = s

    if not lidas_ok:
        return None

    linhas = list(por_termo.values())
    if aplicar:
        # O que mudou de fato: termo novo ou com destino diferente. Só isso
        # justifica reprocessar notas antigas (G10).
        atual = fetch_item_sinonimos(conn)
        mudou = any(atual.get(s.termo_norm) != s.canonico for s in linhas)
        upsert_item_sinonimos(conn, linhas)

    verbo = "sincronizados" if aplicar else "lidos (dry-run)"
    print(f"  ✓ {len(linhas)} sinônimo(s) {verbo}"
          f"  ({relatorio.invalidas} inválida(s), {relatorio.duplicadas} duplicada(s))")
    for linha, motivo in relatorio.descartes:
        print(f"      linha {linha}: {motivo}")

    if aplicar and mudou:
        n = marcar_reprocesso_por_vocabulario(conn)
        if n < 0:
            print("  ⚠ vocabulário mudou, mas há notas demais para remarcar "
                  "automaticamente — rode a reconciliação por período à mão")
        elif n:
            print(f"  → {n} nota(s) remarcada(s) para reconciliação (vocabulário mudou)")

    return relatorio
