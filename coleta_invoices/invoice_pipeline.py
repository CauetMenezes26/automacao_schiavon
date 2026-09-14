"""Pipeline de leitura e persistência de invoices via Claude Vision."""

from __future__ import annotations

from pathlib import Path

from coleta_invoices.invoices_db import config_from_filename, save_invoice
from .vision import (
    BatchItem,
    collect_batch_results,
    read_invoice,
    submit_batch,
    wait_for_batch,
)


def collect_items(conn, results: list[dict], download_dir: Path) -> list[BatchItem]:
    """
    Monta a lista de BatchItem combinando:
      1. Arquivos baixados na execução atual (de results)
      2. Arquivos órfãos já presentes em download_dir de execuções anteriores
    """
    items: list[BatchItem] = []
    current_files: set[str] = set()

    for r in results:
        if not r["downloaded"]:
            continue
        record = r["config"]
        for file_str in r["downloaded"]:
            fp = Path(file_str)
            current_files.add(fp.name)
            items.append(BatchItem(
                file_path=fp,
                config_id=record["id"],
                config_name=record["name"],
            ))

    extensions = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif"}
    for fp in download_dir.iterdir():
        if fp.suffix.lower() not in extensions or fp.name in current_files:
            continue
        cfg = config_from_filename(fp.name)
        if cfg is None:
            print(f"  ⚠ Arquivo órfão sem config reconhecido: {fp.name} (ignorado)")
            continue
        config_id, config_name = cfg
        # Sem dependencia de execution_log: o proprio arquivo vira um caso.
        print(f"  + Órfão incluído: {fp.name}")
        items.append(BatchItem(
            file_path=fp,
            config_id=config_id,
            config_name=config_name,
        ))

    return items


def _persist_and_move(conn, item: BatchItem, invoice_data, read_dir: Path) -> float:
    """Salva invoice no banco, move o arquivo para read_dir. Retorna custo."""
    header_id, n_items = save_invoice(
        conn,
        id_loja=item.config_id,
        file_path=item.file_path,
        data=invoice_data,
        custo=invoice_data.cost_read,
    )
    print(f"    ✓ header_id={header_id}  itens={n_items}  "
          f"custo=${invoice_data.cost_read:.4f}")
    dest = read_dir / item.file_path.name
    item.file_path.rename(dest)
    print(f"    → files/read_files/{item.file_path.name}")
    return invoice_data.cost_read


def process_invoices_batch(
    conn,
    api_key: str,
    results: list[dict],
    model: str,
    download_dir: Path,
    read_dir: Path,
) -> None:
    """
    Envia todos os arquivos para o Batch API de uma vez (50% mais barato).
    Inclui arquivos da execução atual e órfãos de execuções anteriores.
    """
    items = collect_items(conn, results, download_dir)
    if not items:
        print("\nNenhum arquivo para processar.")
        return

    print(f"\n{'='*60}")
    print(f"Batch API — {len(items)} arquivo(s)  modelo={model}")

    batch_id, id_map = submit_batch(items, api_key, model)
    wait_for_batch(batch_id, api_key, poll_interval=20)

    print("\n  Processando resultados...")
    batch_results = collect_batch_results(batch_id, api_key, id_map, model)

    total_cost = 0.0
    read_dir.mkdir(parents=True, exist_ok=True)

    for item, invoice_data, error in batch_results:
        print(f"\n  [{item.config_name}] {item.file_path.name}")
        if error or invoice_data is None:
            print(f"    ✗ Erro: {error}")
            continue
        print(f"    confiança : {invoice_data.reading_confidence:.0f}%  "
              f"status={invoice_data.reading_status}")
        if invoice_data.reading_notes:
            print(f"    notas IA  : {invoice_data.reading_notes[:120]}")
        try:
            total_cost += _persist_and_move(conn, item, invoice_data, read_dir)
        except Exception as exc:
            conn.rollback()
            print(f"    ✗ Erro ao gravar no banco: {exc}")

    print(f"\n  Custo total do batch: ${total_cost:.4f} USD")


def process_invoices_sync(
    conn,
    api_key: str,
    results: list[dict],
    model: str,
    download_dir: Path,
    read_dir: Path,
) -> None:
    """
    Processa cada arquivo imediatamente via chamada síncrona à API.
    Mais rápido para poucos arquivos; sem desconto de preço.
    """
    items = collect_items(conn, results, download_dir)
    if not items:
        print("\nNenhum arquivo para processar.")
        return

    print(f"\n{'='*60}")
    print(f"Síncrono — {len(items)} arquivo(s)  modelo={model}")

    total_cost = 0.0
    read_dir.mkdir(parents=True, exist_ok=True)

    for item in items:
        print(f"\n  [{item.config_name}] {item.file_path.name}")
        try:
            invoice_data = read_invoice(item.file_path, api_key, model)
        except Exception as exc:
            print(f"    ✗ Erro na leitura: {exc}")
            continue
        print(f"    confiança : {invoice_data.reading_confidence:.0f}%  "
              f"status={invoice_data.reading_status}")
        if invoice_data.reading_notes:
            print(f"    notas IA  : {invoice_data.reading_notes[:120]}")
        try:
            total_cost += _persist_and_move(conn, item, invoice_data, read_dir)
        except Exception as exc:
            conn.rollback()
            print(f"    ✗ Erro ao gravar no banco: {exc}")

    print(f"\n  Custo total: ${total_cost:.4f} USD")
