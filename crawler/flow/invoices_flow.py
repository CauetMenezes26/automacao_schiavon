"""FLUXO 2 — Coleta e leitura de invoices.

SharePoint -> download -> Claude Vision -> fat_invoice / fat_invoice_item.

A coordenação de arquivos + leitura + persistência mora em
`coleta_invoices/invoice_pipeline.py`; o cliente de navegação em
`commons/sharepoint/`; o cliente do Vision em `commons/vision/`. Aqui a
orquestração da varredura por loja + o contrato de fluxo.
"""

from __future__ import annotations

import json
from datetime import date

from commons.db import connect_db
from commons.exception import BusinessException
from commons.logging_config import get_logger
from commons.paths import DOWNLOAD_DIR, READ_DIR, ROOT
from domain.service.invoice_service import (
    abrir_coleta,
    falhar_login,
    falhar_navegacao,
    fetch_all_configs,
    registrar_download,
    registrar_navegacao,
)
from domain.service import sistema_service
from domain.sistemas import Sistema

log = get_logger(__name__)

# dim_loja.id -> sistema de SharePoint correspondente (para o inventario/alertas).
_SISTEMA_POR_LOJA = {
    1: Sistema.SHAREPOINT_WINDERMERE,
    2: Sistema.SHAREPOINT_DRPHILLIPS,
}

# Modo de leitura das invoices. False = Batch API (50% mais barato, resultado no
# proximo tick); True = processa na hora. Toggle em vez de flag de CLI.
MODO_SINCRONO = True


def invoices_flow(env: dict) -> None:
    """Baixa as invoices da semana, manda ler pelo Vision e persiste.

    Imports tardios de proposito: `playwright` e `anthropic` sao pesados e so
    fazem falta aqui.
    """
    try:
        _coletar(env)
    except BusinessException as exc:
        log.warning("invoices: caso de negocio - %s", exc)


def _coletar(env: dict) -> None:
    from coleta_invoices.invoice_pipeline import (
        process_invoices_batch,
        process_invoices_sync,
    )
    from commons.sharepoint import (
        build_nav_steps,
        current_month_folder,
        current_year_folder,
        process_all_configs,
    )

    username = env.get("SHAREPOINT_USERNAME", "")
    password = env.get("SHAREPOINT_PASSWORD", "")
    api_key = env.get("schiavon_key_vision", "")
    ai_model = env.get("VISION_MODEL", "claude-sonnet-4-6")
    today = date.today()
    ref_date = date.today()  # TODO: voltar para a semana corrente (hoje) quando sair de teste

    print(f"Data    : {today.strftime('%d/%m/%Y')}")
    print(f"Caminho : Invoices Fornecedores / {current_year_folder(ref_date)} / "
          f"_Invoices para Lançamento / {current_month_folder(ref_date)} / "
          f"semana do dia {ref_date.day} [download aqui]")

    conn = connect_db(env)
    try:
        records = fetch_all_configs(conn)
        all_output: list[dict] = []
        print(f"\n{len(records)} config(s) encontrado(s) no banco.\n")

        def on_record_done(r: dict) -> None:
            """Fecha o caso 'coleta' desta loja.

            A varredura tem vida própria: existe mesmo quando não achou
            arquivo, que é o caso mais comum de falha e o que antes sumia.
            """
            record = r["record"]
            final_path = r["final_path"]
            error = r.get("error")
            achados = len(r["entries"])
            sistema = _SISTEMA_POR_LOJA.get(record["id"])

            id_coleta = abrir_coleta(conn, record["id"], ref_date)
            if r["status"]:
                registrar_navegacao(conn, id_coleta, final_path, achados)
                if achados:
                    registrar_download(conn, id_coleta, len(r.get("downloaded", [])))
                if sistema:
                    sistema_service.registrar_acesso(conn, sistema, ok=True)
            elif r.get("error_tipo") == "login":
                falhar_login(conn, id_coleta, str(error))
                if sistema:
                    sistema_service.registrar_acesso(conn, sistema, ok=False, mensagem=str(error))
            else:
                falhar_navegacao(conn, id_coleta, str(error))

            label = "OK  " if r["status"] else "ERRO"
            print(f"  [{label}] loja id={record['id']}  "
                  f"files={achados}  processo={id_coleta}  -> gravado")
            if error:
                print(f"         erro: {error}")

            all_output.append({
                "config":     record,
                "status":     r["status"],
                "final_path": final_path,
                "entries":    r["entries"],
                "downloaded": r.get("downloaded", []),
                "id_coleta":  id_coleta,
            })

        process_all_configs(
            records, username, password, build_nav_steps(ref_date),
            headless=False,
            keep_open=False,
            download_dir=DOWNLOAD_DIR,
            skip_dirs=[READ_DIR],
            on_record_done=on_record_done,
        )

        if api_key:
            if MODO_SINCRONO:
                print("\nModo: sincrono (MODO_SINCRONO = True)")
                process_invoices_sync(conn, api_key, all_output, ai_model, DOWNLOAD_DIR, READ_DIR)
            else:
                print("\nModo: Batch API (MODO_SINCRONO = False)")
                process_invoices_batch(conn, api_key, all_output, ai_model, DOWNLOAD_DIR, READ_DIR)
        else:
            print("\nAVISO: 'schiavon_key_vision' nao configurado no .env — "
                  "leitura de invoices ignorada.")

        output_file = ROOT / "resultado.json"
        output_file.write_text(
            json.dumps(
                [{k: v for k, v in r.items() if k != "id_coleta"} for r in all_output],
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"\nResultado salvo em: {output_file.name}")

    finally:
        _fechar(conn)


def _fechar(conn) -> None:
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        log.warning("invoices: falha ao fechar conexao", exc_info=True)


# Compat: nome antigo da fachada.
coletar_invoices = invoices_flow
