"""Orquestrador do sistema de cotação semanal de carnes."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from pathlib import Path

from commons.db import connect_db, load_env
from commons.exception import IntegracaoException
from commons.logging_config import get_logger

from domain.service import sistema_service as monitor
from domain.service import processo_service as proc
from domain.sistemas import Sistema
from domain.enums import EnvioCotacaoEnum, EtapaEnum as Etapa, StatusExecEnum as Status

log = get_logger(__name__)

# A sessão SharePoint da cotação usa a mesma credencial das duas lojas: uma
# falha de login derruba o acesso das duas.
_SHAREPOINTS = (Sistema.SHAREPOINT_WINDERMERE, Sistema.SHAREPOINT_DRPHILLIPS)


def _marcar_sharepoint(conn, ok: bool, mensagem: str | None = None) -> None:
    for s in _SHAREPOINTS:
        monitor.registrar_acesso(conn, s, ok=ok, mensagem=mensagem)

from .cotacao_db import (
    close_quotation_request,
    create_quotation_request,
    fetch_processo_do_ciclo,
    fetch_active_meat_suppliers,
    fetch_open_quotation_request,
    fetch_pending_responses,
    fetch_responses_for_followup,
    save_followup,
    save_quotation_response,
    update_quotation_response_ausente,
    update_quotation_response_imported,
    update_quotation_response_responded,
    update_quotation_response_sent,
)
from domain.service.conciliacao_service import marcar_reprocesso_por_cotacao
from .quotation_generator import (
    _current_month_sheet_name,
    add_quotation_column,
    create_initial_quotation_excel,
    has_responses,
    read_quotation_column,
)
from commons.sharepoint import (
    download_single_file,
    ensure_folder_exists,
    open_sharepoint_session,
    upload_file_to_sharepoint,
)
from .prices import save_price_quotes, PriceRow
from decimal import Decimal

from commons.paths import ENV_PATH, OUTBOUND_DIR  # noqa: F401

SHAREPOINT_SITE_URL = "https://dataguvicombr.sharepoint.com/sites/DATA-GUVI-SCHIAVON"
SHAREPOINT_BASE_FOLDER = "/sites/DATA-GUVI-SCHIAVON/Cotaes"

MAX_FOLLOWUP_DAYS = 5
FOLLOWUP_INTERVAL_HOURS = 4
FOLLOWUP_WINDOW_START_HOUR = 6
FOLLOWUP_WINDOW_END_HOUR = 18


def _week_label(d: date) -> str:
    return f"{d.year}-W{d.isocalendar()[1]:02d}"


def _week_bounds(d: date) -> tuple[date, date]:
    """Retorna (segunda-feira, domingo) da semana do date dado."""
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


SHAREPOINT_SUPPLIER_FOLDER = f"{SHAREPOINT_BASE_FOLDER}/Fornecedores"


def _sharepoint_folder_for_supplier() -> str:
    """Caminho server-relative da pasta fixa de cotações dos fornecedores."""
    return SHAREPOINT_SUPPLIER_FOLDER


def _file_name_for_supplier(supplier_name: str) -> str:
    """Nome do arquivo Excel de cotação para um fornecedor."""
    clean = supplier_name.upper().replace(" ", "_")
    return f"COTACAO_{clean}.xlsx"


# ---------------------------------------------------------------------------
# 1. Iniciar ciclo semanal
# ---------------------------------------------------------------------------

def start_weekly_quotation(env: dict[str, str], supplier_filter: str | None = None) -> None:
    """
    Fluxo principal: gera/atualiza Excel por fornecedor, sobe ao SharePoint,
    envia notificação via WhatsApp ou email.

    `supplier_filter`: busca parcial (case-insensitive) pelo nome do fornecedor.
    Sem ela, roda para TODOS os fornecedores de carne ativos — o que dispara
    notificação real para cada um. Usar para restringir a um teste pontual.
    """
    conn = connect_db(env)
    try:
        _abrir_ciclo_e_notificar(conn, env, supplier_filter)
    finally:
        _fechar_conexao(conn)


def _fechar_conexao(conn) -> None:
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        log.warning("cotacao: falha ao fechar conexao", exc_info=True)


def _abrir_ciclo_e_notificar(conn, env: dict[str, str], supplier_filter: str | None) -> None:
    """Decide o passo (abrir ciclo novo ou nada a fazer) e, se abrir, publica e
    notifica todos os fornecedores. Sem try: cada chamada abaixo já isola sua
    própria falha (sessão SharePoint, cleanup do navegador)."""
    today = date(2026, 6, 26)  # TESTE: ciclo da semana da invoice Eastern (2026-W26); era date.today()
    week_start, week_end = _week_bounds(today)
    label = _week_label(today)

    existing = fetch_open_quotation_request(conn)
    if existing and existing["week_label"] == label:
        print(f"Cotação da semana {label} já está aberta (id={existing['id']}). "
              "Use --cotacao-check para verificar respostas.")
        return

    request_id = create_quotation_request(conn, label, week_start, week_end)
    id_proc = fetch_processo_do_ciclo(conn, request_id)
    proc.concluir_etapa(conn, id_proc, Etapa.ABRIR_CICLO)
    print(f"\n{'='*60}")
    print(f"Cotação Semanal — {label} ({week_start} a {week_end})")
    print(f"Request ID: {request_id}")
    print(f"{'='*60}")

    suppliers = fetch_active_meat_suppliers(conn)
    if not suppliers:
        print("Nenhum fornecedor de carne ativo cadastrado.")
        proc.falhar_etapa(conn, id_proc, Etapa.PUBLICAR_PLANILHA,
                          Status.ERRO_API,
                          "Nenhum fornecedor ativo e cotado.")
        return

    if supplier_filter:
        termo = supplier_filter.strip().lower()
        total_antes = len(suppliers)
        suppliers = [s for s in suppliers if termo in s["name"].lower()]
        print(f"\nFiltro --fornecedor {supplier_filter!r}: "
              f"{len(suppliers)} de {total_antes} fornecedor(es) ativo(s).")
        if not suppliers:
            print("Nenhum fornecedor ativo bate com o filtro.")
            return

    print(f"\n{len(suppliers)} fornecedor(es) ativo(s).")
    OUTBOUND_DIR.mkdir(parents=True, exist_ok=True)

    sessao = _abrir_sessao_sharepoint(conn, env, id_proc)
    _publicar_e_notificar_fornecedores(conn, env, sessao, suppliers, request_id, today, label)

    proc.concluir_etapa(conn, id_proc, Etapa.PUBLICAR_PLANILHA)
    proc.concluir_etapa(conn, id_proc, Etapa.NOTIFICAR)
    # daqui em diante depende do fornecedor: e espera, nao falha
    proc.aguardar_etapa(conn, id_proc, Etapa.AGUARDAR_RESPOSTA)

    print(f"\n{'='*60}")
    print(f"Cotação {label} enviada para {len(suppliers)} fornecedor(es).")
    print(f"{'='*60}")


def _abrir_sessao_sharepoint(conn, env: dict[str, str], id_proc: int | None):
    """Abre a sessão SharePoint da cotação. Um único try: falha de login marca
    a etapa, registra o monitor e sobe classificada — quem chama não precisa
    saber que veio de um browser."""
    username = env.get("SHAREPOINT_USERNAME2", "")
    password = env.get("SHAREPOINT_PASSWORD2", "")
    try:
        sessao = open_sharepoint_session(
            username, password, SHAREPOINT_SITE_URL, headless=False,
        )
    except Exception as exc:
        proc.falhar_etapa(conn, id_proc, Etapa.PUBLICAR_PLANILHA,
                          Status.ERRO_LOGIN, f"SharePoint (cotação): {exc}")
        _marcar_sharepoint(conn, ok=False, mensagem=str(exc))
        raise IntegracaoException("cotacao: falha ao abrir sessao SharePoint") from exc

    _marcar_sharepoint(conn, ok=True)
    return sessao


def _publicar_e_notificar_fornecedores(
    conn, env: dict[str, str], sessao, suppliers: list[dict],
    request_id: int, today: date, label: str,
) -> None:
    """Publica a planilha de cada fornecedor e notifica. Único try: a limpeza
    do navegador vai para uma função auxiliar que nunca levanta."""
    pw, browser, context, page = sessao
    supplier_folder = _sharepoint_folder_for_supplier()
    try:
        ensure_folder_exists(context, SHAREPOINT_SITE_URL, supplier_folder)
        for supplier in suppliers:
            _publicar_e_notificar_um_fornecedor(
                conn, env, context, supplier, request_id, today, label, supplier_folder,
            )
    finally:
        _fechar_navegador(pw, browser)


def _fechar_navegador(pw, browser) -> None:
    try:
        browser.close()
        pw.stop()
    except Exception:  # noqa: BLE001
        log.warning("cotacao: falha ao fechar navegador", exc_info=True)


def _publicar_e_notificar_um_fornecedor(
    conn, env: dict[str, str], context, supplier: dict, request_id: int,
    today: date, label: str, supplier_folder: str,
) -> None:
    print(f"\n--- {supplier['name']} ---")
    file_name = _file_name_for_supplier(supplier["name"])
    sp_file_path = f"{supplier_folder}/{file_name}"

    local_path = _baixar_ou_criar_planilha(context, supplier, file_name, sp_file_path, today)
    col_index = add_quotation_column(local_path, today)

    upload_file_to_sharepoint(
        context, SHAREPOINT_SITE_URL, supplier_folder, local_path,
    )

    # O tenant do SharePoint não permite link anônimo via API
    # (create_file_sharing_link volta 400 - "Operation is not
    # valid due to the current state of the object"), então o
    # link de cada arquivo é criado à mão na UI uma vez e colado
    # em dim_fornecedor.planilha_url (persiste — o mesmo Excel é
    # reaproveitado toda semana, overwrite=true). Sem token
    # cadastrado ainda, não notifica: mandar o fallback de pasta
    # (:f:/:x: sem id) reabriria o problema original.
    sharing_token = supplier.get("sharepoint_file_id")
    file_url = (
        f"{SHAREPOINT_SITE_URL.replace('/sites/', '/:x:/s/')}/{sharing_token}"
        if sharing_token else ""
    )

    response_id = save_quotation_response(
        conn,
        id_request=request_id,
        id_supplier=supplier["id"],
        file_name=file_name,
        file_url=file_url,
        column_index=col_index,
        quote_date=today,
        aba=_current_month_sheet_name(today),
    )

    if file_url:
        _send_notification(
            conn, env, supplier, file_url, label, response_id,
        )
    else:
        print(f"  AVISO: sem link cadastrado em planilha_url - nao notificado: {sp_file_path}")


def _baixar_ou_criar_planilha(
    context, supplier: dict, file_name: str, sp_file_path: str, today: date,
):
    """Baixa o Excel do fornecedor se já existir no SharePoint; senão cria um
    novo. Único try: `RuntimeError` de "não achei o arquivo" é o contrato de
    `download_single_file`, não uma falha a propagar."""
    local_path = OUTBOUND_DIR / file_name
    try:
        download_single_file(context, SHAREPOINT_SITE_URL, sp_file_path, local_path)
    except RuntimeError:
        return create_initial_quotation_excel(
            supplier["name"], [], OUTBOUND_DIR, ref_date=today,
        )
    print(f"  Excel existente baixado do SharePoint.")
    return local_path


def _send_notification(
    conn, env, supplier: dict, file_url: str,
    week_label: str, response_id: int,
) -> None:
    """Envia notificação ao fornecedor pelo canal preferido. Sem try aqui: cada
    canal isola sua própria falha numa função auxiliar."""
    channel = supplier.get("channel", "whatsapp")

    if channel in ("whatsapp", "both") and supplier.get("whatsapp"):
        sucesso = _enviar_whatsapp(conn, supplier, file_url, response_id)
        if sucesso or channel == "whatsapp":
            return

    if channel in ("email", "both") and supplier.get("email"):
        _enviar_email(conn, env, supplier, file_url, week_label, response_id)
        return

    print(f"  AVISO: nenhum canal disponivel para {supplier['name']}")
    update_quotation_response_sent(
        conn, response_id, channel="nenhum",
        envio_status=str(EnvioCotacaoEnum.SEM_CANAL))


def _enviar_whatsapp(conn, supplier: dict, file_url: str, response_id: int) -> bool:
    """Tenta notificar por WhatsApp. Retorna True se despachado com sucesso."""
    from .messenger import send_whatsapp_quote_notification

    try:
        result = send_whatsapp_quote_notification(
            recipient_whatsapp_number=supplier["whatsapp"],
            supplier_sharepoint_url=file_url,
            supplier_name=supplier.get("contact_name") or supplier["name"],
        )
    except EnvironmentError as exc:
        print(f"  AVISO: Twilio nao configurado: {exc}")
        monitor.registrar_acesso(conn, Sistema.TWILIO_WHATSAPP, ok=False, mensagem=str(exc))
        update_quotation_response_sent(
            conn, response_id, channel="whatsapp",
            envio_status=str(EnvioCotacaoEnum.FALHOU), envio_detalhe=str(exc))
        return False
    except Exception as exc:
        print(f"  AVISO: WhatsApp falhou: {exc}")
        monitor.registrar_acesso(conn, Sistema.TWILIO_WHATSAPP, ok=False, mensagem=str(exc))
        update_quotation_response_sent(
            conn, response_id, channel="whatsapp",
            envio_status=str(EnvioCotacaoEnum.FALHOU), envio_detalhe=str(exc))
        return False

    monitor.registrar_acesso(conn, Sistema.TWILIO_WHATSAPP, ok=True)
    update_quotation_response_sent(
        conn, response_id,
        channel="whatsapp",
        message_sid=result.get("sid"),
        envio_status=str(EnvioCotacaoEnum.do_twilio(result.get("status"))),
    )
    return True


def _enviar_email(
    conn, env, supplier: dict, file_url: str, week_label: str, response_id: int,
) -> bool:
    """Tenta notificar por e-mail. Retorna True se despachado com sucesso."""
    from .email_sender import send_quotation_email

    try:
        result = send_quotation_email(
            env,
            recipient_email=supplier["email"],
            supplier_name=supplier.get("contact_name") or supplier["name"],
            sharepoint_url=file_url,
            week_label=week_label,
        )
    except Exception as exc:
        print(f"  AVISO: Email falhou: {exc}")
        monitor.registrar_acesso(conn, Sistema.SMTP_EMAIL, ok=False, mensagem=str(exc))
        update_quotation_response_sent(
            conn, response_id, channel="email",
            envio_status=str(EnvioCotacaoEnum.FALHOU), envio_detalhe=str(exc))
        return False

    ok = result.get("status") == "sent"
    monitor.registrar_acesso(conn, Sistema.SMTP_EMAIL, ok=ok,
                             mensagem=result.get("error"))
    update_quotation_response_sent(
        conn, response_id, channel="email",
        envio_status=str(EnvioCotacaoEnum.ENVIADO if ok else EnvioCotacaoEnum.FALHOU),
        envio_detalhe=result.get("error"),
    )
    return True


# ---------------------------------------------------------------------------
# 2. Verificar respostas
# ---------------------------------------------------------------------------

def check_responses(env: dict[str, str]) -> None:
    """
    Baixa os Excels do SharePoint, verifica se os fornecedores preencheram
    a coluna da cotação atual, e importa os preços no banco.
    """
    try:
        process_whatsapp_replies(env)
    except Exception as exc:
        print(f"  AVISO: erro ao verificar ausencias no WhatsApp: {exc}")

    conn = connect_db(env)

    try:
        open_request = fetch_open_quotation_request(conn)
        if not open_request:
            print("Nenhum ciclo de cotação aberto.")
            return

        print(f"\nVerificando respostas — Ciclo {open_request['week_label']} (id={open_request['id']})")
        id_proc = fetch_processo_do_ciclo(conn, open_request["id"])

        pending = fetch_pending_responses(conn, open_request["id"])
        if not pending:
            print("Nenhuma resposta pendente.")
            # nada pendente = todos ja importados, o ciclo terminou
            proc.concluir_etapa(conn, id_proc, Etapa.IMPORTAR_PRECOS)
            return

        print(f"{len(pending)} resposta(s) pendente(s).\n")

        # Mesmo usuário SharePoint de start_weekly_quotation — é a conta com
        # acesso ao site de Cotações, diferente das duas de invoices (loja).
        username = env.get("SHAREPOINT_USERNAME2", "")
        password = env.get("SHAREPOINT_PASSWORD2", "")

        try:
            pw, browser, context, page = open_sharepoint_session(
                username, password, SHAREPOINT_SITE_URL, headless=False,
            )
            _marcar_sharepoint(conn, ok=True)
        except Exception as exc:
            proc.falhar_etapa(conn, id_proc, Etapa.IMPORTAR_PRECOS,
                              Status.ERRO_LOGIN, f"SharePoint (cotação): {exc}")
            _marcar_sharepoint(conn, ok=False, mensagem=str(exc))
            raise

        imported_count = 0

        try:
            for resp in pending:
                supplier_name = resp["supplier_name"]
                file_name = resp["file_name"]
                col_index = resp["column_index"]
                quote_date = resp["quote_date"]

                if not file_name or not col_index:
                    continue

                print(f"  {supplier_name}: ", end="")

                supplier_folder = _sharepoint_folder_for_supplier()
                sp_path = f"{supplier_folder}/{file_name}"
                local_path = OUTBOUND_DIR / file_name

                try:
                    download_single_file(context, SHAREPOINT_SITE_URL, sp_path, local_path)
                except RuntimeError:
                    print("arquivo não encontrado, criando... ", end="")
                    ref = quote_date or date.today()
                    local_path = create_initial_quotation_excel(
                        supplier_name, [], OUTBOUND_DIR, ref_date=ref,
                    )
                    add_quotation_column(local_path, ref)
                    upload_file_to_sharepoint(
                        context, SHAREPOINT_SITE_URL, supplier_folder, local_path,
                    )
                    print("aguardando preenchimento")
                    continue

                # add_quotation_column escreve na aba do mes da cotacao, mas
                # read_quotation_column sem sheet_name cai em wb.active — que pode
                # ser outra aba. Sem isto le a coluna certa da aba errada.
                aba = resp.get("aba") or (
                    _current_month_sheet_name(quote_date) if quote_date else None)
                prices = read_quotation_column(local_path, col_index, aba)
                if has_responses(prices):
                    update_quotation_response_responded(conn, resp["id"])

                    price_rows = [
                        PriceRow(
                            supplier=supplier_name,
                            item_code=p.item_code,
                            item_name=p.item_name,
                            price=p.price,
                            price_raw=p.price_raw,
                            comments=None,
                            file_name=file_name,
                            # Vínculo com o ciclo: sem isto não há como saber de
                            # que semana é o preço na hora de conciliar.
                            id_request=open_request["id"],
                            id_supplier=resp["id_supplier"],
                            quote_date=quote_date,
                        )
                        for p in prices if p.price > 0
                    ]
                    inserted = save_price_quotes(conn, price_rows)
                    update_quotation_response_imported(conn, resp["id"])

                    print(f"OK: {inserted} preco(s) importado(s)")
                    imported_count += 1
                else:
                    print("aguardando preenchimento")

        finally:
            browser.close()
            pw.stop()

        print(f"\n{imported_count} fornecedor(es) responderam neste check.")

        if imported_count:
            # Preço novo pode destravar nota que fechou como "sem cotação"
            # nesta mesma janela — sem isto ela fica encerrada para sempre.
            n_reproc = marcar_reprocesso_por_cotacao(conn)
            if n_reproc:
                print(f"{n_reproc} nota(s) remarcada(s) para reconciliar "
                      f"de novo (tinham fechado sem cotação).")

        if not fetch_pending_responses(conn, open_request["id"]):
            proc.concluir_etapa(conn, id_proc, Etapa.IMPORTAR_PRECOS)
            print("Ciclo completo: todos os fornecedores importados.")
        else:
            proc.aguardar_etapa(conn, id_proc, Etapa.AGUARDAR_RESPOSTA)

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. Cobranças / Follow-ups
# ---------------------------------------------------------------------------

def send_followups(env: dict[str, str]) -> None:
    """
    Envia lembretes para fornecedores que não responderam a cotação, a cada
    `FOLLOWUP_INTERVAL_HOURS` horas, só dentro do horário comercial
    (`FOLLOWUP_WINDOW_START_HOUR`-`FOLLOWUP_WINDOW_END_HOUR`). Fora da janela,
    não cobra — quem chama de novo mais tarde (o cron externo) é quem decide
    a frequência real de checagem. Fornecedores ausentes são ignorados.
    """
    hora_atual = datetime.now().hour
    if not (FOLLOWUP_WINDOW_START_HOUR <= hora_atual < FOLLOWUP_WINDOW_END_HOUR):
        print(f"Fora do horário comercial ({FOLLOWUP_WINDOW_START_HOUR}h-"
              f"{FOLLOWUP_WINDOW_END_HOUR}h) — cobrança adiada.")
        return

    try:
        process_whatsapp_replies(env)
    except Exception as exc:
        print(f"  AVISO: erro ao verificar ausencias no WhatsApp: {exc}")

    conn = connect_db(env)

    try:
        open_request = fetch_open_quotation_request(conn)
        if not open_request:
            print("Nenhum ciclo de cotação aberto.")
            return

        print(f"\nCobranças — Ciclo {open_request['week_label']}")

        overdue = fetch_responses_for_followup(
            conn, open_request["id"], min_hours=FOLLOWUP_INTERVAL_HOURS,
        )

        if not overdue:
            print("Nenhum fornecedor pendente de cobrança.")
            return

        print(f"{len(overdue)} fornecedor(es) sem resposta há mais de "
              f"{FOLLOWUP_INTERVAL_HOURS}h.\n")

        for resp in overdue:
            supplier_name = resp["supplier_name"]
            followup_count = resp["followup_count"]

            sent_at = resp["sent_at"]
            if sent_at:
                days_since = (date.today() - sent_at.date()).days
                if days_since > MAX_FOLLOWUP_DAYS:
                    print(f"  {supplier_name}: prazo expirado ({days_since} dias). Ignorando.")
                    continue

            print(f"  {supplier_name} (cobrança #{followup_count + 1}): ", end="")

            sent_ch = resp.get("sent_channel")
            channel = (sent_ch if sent_ch and sent_ch != "nenhum" else None) or resp.get("channel", "whatsapp")

            if channel in ("whatsapp", "both") and resp.get("whatsapp"):
                try:
                    from .messenger import send_whatsapp_quote_notification
                    result = send_whatsapp_quote_notification(
                        recipient_whatsapp_number=resp["whatsapp"],
                        supplier_sharepoint_url=resp.get("file_url", ""),
                        supplier_name=resp.get("contact_name") or supplier_name,
                    )
                    save_followup(
                        conn, resp["id"],
                        channel="whatsapp",
                        message_sid=result.get("sid"),
                        notes=f"Cobrança #{followup_count + 1}",
                    )
                    print(f"OK: WhatsApp enviado")
                except Exception as exc:
                    print(f"FALHA: WhatsApp falhou: {exc}")

            elif channel in ("email", "both") and resp.get("email"):
                try:
                    from .email_sender import send_quotation_email
                    send_quotation_email(
                        env,
                        recipient_email=resp["email"],
                        supplier_name=resp.get("contact_name") or supplier_name,
                        sharepoint_url=resp.get("file_url", ""),
                        week_label=open_request["week_label"],
                    )
                    save_followup(
                        conn, resp["id"],
                        channel="email",
                        notes=f"Cobrança #{followup_count + 1}",
                    )
                    print(f"OK: Email enviado")
                except Exception as exc:
                    print(f"FALHA: Email falhou: {exc}")
            else:
                print(f"AVISO: sem canal disponivel")

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 4. Processar respostas de ausência (WhatsApp)
# ---------------------------------------------------------------------------

def process_whatsapp_replies(env: dict[str, str]) -> None:
    """Busca respostas de 'Ausente' no Twilio e marca no ciclo atual."""
    conn = connect_db(env)
    try:
        from .excel_handler import check_twilio_absences
        count = check_twilio_absences(conn)
        print(f"\n{count} ausência(s) processada(s).")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 5. Comparação de preços
# ---------------------------------------------------------------------------

def compare_prices(env: dict[str, str], request_id: int | None = None) -> None:
    """
    Compara preços entre fornecedores para o ciclo especificado ou o mais recente.
    """
    conn = connect_db(env)

    try:
        if request_id is None:
            open_req = fetch_open_quotation_request(conn)
            if open_req:
                request_id = open_req["id"]
                label = open_req["week_label"]
            else:
                print("Nenhum ciclo de cotação encontrado.")
                return
        else:
            label = f"id={request_id}"

        print(f"\n{'='*80}")
        print(f"Comparação de Preços — Ciclo {label}")
        print(f"{'='*80}")

        from psycopg2.extras import RealDictCursor
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT pq.id_fornecedor AS id_supplier,
                       f.nome AS supplier_name,
                       pq.item_codigo AS item_code, pq.item_nome AS item_name,
                       pq.preco AS price, pq.preco_raw AS price_raw
                FROM dwschiavon2.fat_cotacao_preco pq
                    JOIN dwschiavon2.dim_fornecedor f ON f.id = pq.id_fornecedor
                WHERE pq.id_ciclo = %s
                ORDER BY pq.item_nome, pq.preco
                """,
                (request_id,),
            )
            rows = [dict(r) for r in cur.fetchall()]

        if not rows:
            print("Nenhum preço importado para comparação.")
            return

        items: dict[str, list[dict]] = {}
        for r in rows:
            items.setdefault(r["item_name"], []).append(r)

        suppliers_seen: set[str] = set()
        for r in rows:
            suppliers_seen.add(r["supplier_name"])
        supplier_list = sorted(suppliers_seen)

        header = f"{'Item':<55}"
        for s in supplier_list:
            header += f" {s[:12]:>12}"
        header += f" {'Melhor':>12}"

        print(f"\n{header}")
        print("-" * len(header))

        for item_name in sorted(items.keys()):
            price_map = {r["supplier_name"]: float(r["price"]) for r in items[item_name]}
            line = f"{item_name[:54]:<55}"

            best_price = None
            best_supplier = None

            for s in supplier_list:
                p = price_map.get(s)
                if p and p > 0:
                    line += f" ${p:>10.2f}"
                    if best_price is None or p < best_price:
                        best_price = p
                        best_supplier = s
                else:
                    line += f" {'—':>11}"

            if best_supplier:
                line += f" {best_supplier[:12]:>12}"
            else:
                line += f" {'—':>12}"

            print(line)

        print(f"\n{len(items)} item(ns) comparado(s) entre {len(supplier_list)} fornecedor(es).")

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 6. Ciclo automatizado
# ---------------------------------------------------------------------------

CHECK_INTERVAL_MINUTES = 2
MAX_AUTOMATED_CYCLES = 48


def run_automated_cycle(
    env: dict[str, str],
    check_interval_minutes: int = CHECK_INTERVAL_MINUTES,
    max_cycles: int = MAX_AUTOMATED_CYCLES,
) -> None:
    """
    Ciclo automatizado de cotação semanal:
    1. Envia mensagens aos fornecedores (ou retoma ciclo aberto)
    2. Aguarda N horas
    3. Verifica respostas no SharePoint e ausências no WhatsApp
    4. Cobra quem não respondeu
    5. Repete até todos responderem ou atingir max_cycles
    6. Gera relatório de monitoramento
    """
    today = date.today()
    label = _week_label(today)

    conn = connect_db(env)
    try:
        existing = fetch_open_quotation_request(conn)
    finally:
        conn.close()

    if not existing or existing["week_label"] != label:
        start_weekly_quotation(env)
    else:
        print(f"\nCotacao {label} ja aberta (id={existing['id']}). Retomando monitoramento.")

    for cycle in range(1, max_cycles + 1):
        next_check = datetime.now() + timedelta(minutes=check_interval_minutes)
        print(f"\n{'='*60}")
        print(f"Aguardando {check_interval_minutes}min — Ciclo {cycle}/{max_cycles}")
        print(f"Proxima verificacao: {next_check.strftime('%d/%m %H:%M')}")
        print(f"{'='*60}")
        time.sleep(check_interval_minutes * 60)

        print(f"\n--- Verificando ausencias (WhatsApp) ---")
        try:
            process_whatsapp_replies(env)
        except Exception as exc:
            print(f"  AVISO: erro ao verificar ausencias: {exc}")

        print(f"\n--- Verificando respostas (SharePoint) ---")
        check_responses(env)

        conn = connect_db(env)
        try:
            open_req = fetch_open_quotation_request(conn)
            if not open_req:
                print("\nCiclo encerrado.")
                break

            pending = fetch_pending_responses(conn, open_req["id"])

            if not pending:
                print(f"\nTodos os fornecedores responderam!")
                close_quotation_request(conn, open_req["id"])
                break

            print(f"\n{len(pending)} fornecedor(es) pendente(s). Enviando cobrancas...")
            _send_followup_to_pending(conn, env, pending, open_req["week_label"])
        finally:
            conn.close()

    _print_monitoring_report(env)


def _send_followup_to_pending(
    conn, env: dict, pending: list[dict], week_label: str,
) -> None:
    """Envia cobranca WhatsApp/email para fornecedores que nao responderam."""
    for resp in pending:
        supplier_name = resp["supplier_name"]
        sent_ch = resp.get("sent_channel")
        channel = (sent_ch if sent_ch and sent_ch != "nenhum" else None) or resp.get("channel", "whatsapp")

        print(f"  {supplier_name}: ", end="")

        if channel in ("whatsapp", "both") and resp.get("whatsapp"):
            try:
                from .messenger import send_whatsapp_quote_notification

                result = send_whatsapp_quote_notification(
                    recipient_whatsapp_number=resp["whatsapp"],
                    supplier_sharepoint_url=resp.get("file_url") or "",
                    supplier_name=resp.get("contact_name") or supplier_name,
                )

                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM dwschiavon2.fat_cotacao_cobranca"
                        " WHERE id_envio = %s",
                        (resp["id"],),
                    )
                    count = cur.fetchone()[0]

                save_followup(
                    conn, resp["id"],
                    channel="whatsapp",
                    message_sid=result.get("sid"),
                    notes=f"Cobranca automatica #{count + 1}",
                )
                print(f"WhatsApp enviado")
            except Exception as exc:
                print(f"WhatsApp falhou: {exc}")

        elif channel in ("email", "both") and resp.get("email"):
            try:
                from .email_sender import send_quotation_email
                send_quotation_email(
                    env,
                    recipient_email=resp["email"],
                    supplier_name=resp.get("contact_name") or supplier_name,
                    sharepoint_url=resp.get("file_url", ""),
                    week_label=week_label,
                )
                save_followup(conn, resp["id"], channel="email", notes="Cobranca automatica")
                print(f"Email enviado")
            except Exception as exc:
                print(f"Email falhou: {exc}")
        else:
            print(f"Sem canal disponivel")


def _print_monitoring_report(env: dict[str, str]) -> None:
    """Imprime relatorio de monitoramento do ciclo atual."""
    from psycopg2.extras import RealDictCursor

    conn = connect_db(env)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, semana_label AS week_label FROM dwschiavon2.dim_ciclo"
                " ORDER BY id DESC LIMIT 1"
            )
            req = cur.fetchone()

        if not req:
            print("Nenhum ciclo encontrado para relatorio.")
            return

        request_id = req["id"]
        label = req["week_label"]

        print(f"\n{'='*80}")
        print(f"RELATORIO DE MONITORAMENTO — Ciclo {label}")
        print(f"{'='*80}")

        # --- Status dos fornecedores ---
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT f.nome AS name,
                       CASE WHEN e.ausente             THEN 'ausente'
                            WHEN e.importado_em IS NOT NULL THEN 'imported'
                            WHEN e.respondido_em IS NOT NULL THEN 'responded'
                            WHEN e.enviado_em IS NOT NULL   THEN 'sent'
                            ELSE 'pending' END AS status,
                       e.enviado_em    AS sent_at,
                       e.respondido_em AS responded_at
                FROM dwschiavon2.fat_cotacao_envio e
                    JOIN dwschiavon2.dim_fornecedor f ON f.id = e.id_fornecedor
                WHERE e.id_ciclo = %s
                ORDER BY f.nome
            """, (request_id,))
            responses = [dict(r) for r in cur.fetchall()]

        status_icons = {
            "imported": "OK", "responded": "OK", "ausente": "AUS",
            "sent": "PEND", "pending": "...",
        }

        print(f"\n--- Status dos Fornecedores ---")
        for r in responses:
            icon = status_icons.get(r["status"], "?")
            print(f"  [{icon:>4}] {r['name']:<30} {r['status']}")

        responded = sum(1 for r in responses if r["status"] in ("imported", "responded"))
        ausente = sum(1 for r in responses if r["status"] == "ausente")
        pend = sum(1 for r in responses if r["status"] == "sent")
        print(f"\n  Responderam: {responded} | Ausentes: {ausente} | Pendentes: {pend}")

        # --- Precos e alteracoes ---
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT pq.item_nome AS item_name, f.nome AS supplier,
                       pq.preco AS price, pq.item_codigo AS item_code
                FROM dwschiavon2.fat_cotacao_preco pq
                    JOIN dwschiavon2.dim_fornecedor f ON f.id = pq.id_fornecedor
                WHERE pq.id_ciclo = %s
                ORDER BY pq.item_nome, f.nome
            """, (request_id,))
            current_prices = [dict(r) for r in cur.fetchall()]

        if not current_prices:
            print(f"\n  Nenhum preco importado neste ciclo.")
            print(f"\n{'='*80}")
            return

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT DISTINCT ON (pq.item_nome, f.nome)
                    pq.item_nome AS item_name, f.nome AS supplier, pq.preco AS price
                FROM dwschiavon2.fat_cotacao_preco pq
                    JOIN dwschiavon2.dim_fornecedor f ON f.id = pq.id_fornecedor
                WHERE pq.id_ciclo < %s
                ORDER BY pq.item_nome, f.nome, pq.id_ciclo DESC, pq.criado_em DESC
            """, (request_id,))
            prev_map = {
                (r["item_name"], r["supplier"]): float(r["price"])
                for r in cur.fetchall()
            }

        changes = []
        for cp in current_prices:
            current = float(cp["price"])
            previous = prev_map.get((cp["item_name"], cp["supplier"]))
            if previous and previous > 0 and current > 0:
                pct = ((current - previous) / previous) * 100
                changes.append({
                    "item": cp["item_name"], "supplier": cp["supplier"],
                    "previous": previous, "current": current, "pct": pct,
                })

        if changes:
            increases = sorted(
                [c for c in changes if c["pct"] > 0], key=lambda x: x["pct"], reverse=True,
            )

            if increases:
                print(f"\n--- Maiores Aumentos de Preco ---")
                for c in increases[:10]:
                    print(
                        f"  +{c['pct']:.1f}%  {c['item'][:40]:<40} "
                        f"({c['supplier']}) ${c['previous']:.2f} -> ${c['current']:.2f}"
                    )

            total_changes = len([c for c in changes if abs(c["pct"]) > 0.5])
            print(f"\n  {total_changes} alteracao(oes) de preco identificada(s).")
        else:
            print(f"\n  Sem historico anterior para comparacao de precos.")

        print(f"\n{'='*80}")
    finally:
        conn.close()
