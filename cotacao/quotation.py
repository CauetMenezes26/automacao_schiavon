"""Orquestrador do sistema de cotação semanal de carnes."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from pathlib import Path

from commons.datas import week_bounds
from commons.catapult import fechar_browser, parar_playwright
from commons.db import conexao, connect_db
from commons.exception import ConfigException, IntegracaoException
from commons.logging_config import get_logger

from domain.service import sistema_service as monitor
from domain.service import processo_service as proc
from domain.sistemas import Sistema
from domain.enums import EnvioCotacao, Etapa, StatusExecucao as Status

log = get_logger(__name__)

# A sessão SharePoint da cotação usa a mesma credencial das duas lojas: uma
# falha de login derruba o acesso das duas.
_SHAREPOINTS = (Sistema.SHAREPOINT_WINDERMERE, Sistema.SHAREPOINT_DRPHILLIPS)


def _marcar_sharepoint(conn, ok: bool, mensagem: str | None = None) -> None:
    for s in _SHAREPOINTS:
        monitor.registrar_acesso(conn, s, ok=ok, mensagem=mensagem)

from domain.service.cotacao_service import (
    close_quotation_request,
    contar_cobrancas,
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
    current_month_sheet_name,
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
from domain.model.cotacao import PriceRow
from domain.service.prices_service import save_price_quotes
from decimal import Decimal

from commons.paths import OUTBOUND_DIR  # noqa: F401
from domain.config import Config

SHAREPOINT_SITE_URL = "https://dataguvicombr.sharepoint.com/sites/DATA-GUVI-SCHIAVON"
SHAREPOINT_BASE_FOLDER = "/sites/DATA-GUVI-SCHIAVON/Cotaes"

MAX_FOLLOWUP_DAYS = 5
FOLLOWUP_INTERVAL_HOURS = 4
FOLLOWUP_WINDOW_START_HOUR = 6
FOLLOWUP_WINDOW_END_HOUR = 18


def _week_label(d: date) -> str:
    return f"{d.year}-W{d.isocalendar()[1]:02d}"


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

def start_weekly_quotation(config: Config, supplier_filter: str | None = None) -> None:
    """
    Fluxo principal: gera/atualiza Excel por fornecedor, sobe ao SharePoint,
    envia notificação via WhatsApp ou email.

    `supplier_filter`: busca parcial (case-insensitive) pelo nome do fornecedor.
    Sem ela, roda para TODOS os fornecedores de carne ativos — o que dispara
    notificação real para cada um. Usar para restringir a um teste pontual.
    """
    conn = connect_db(config.banco)
    try:
        _abrir_ciclo_e_notificar(conn, config, supplier_filter)
    finally:
        _fechar_conexao(conn)


def _fechar_conexao(conn) -> None:
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        log.warning("cotacao: falha ao fechar conexao", exc_info=True)


def _abrir_ciclo_e_notificar(conn, config: Config, supplier_filter: str | None) -> None:
    """Decide o passo (abrir ciclo novo ou nada a fazer) e, se abrir, publica e
    notifica todos os fornecedores. Sem try: cada chamada abaixo já isola sua
    própria falha (sessão SharePoint, cleanup do navegador)."""
    today = date.today()
    week_start, week_end = week_bounds(today)
    label = _week_label(today)

    existing = fetch_open_quotation_request(conn)
    if existing and existing["week_label"] == label:
        log.info(
            "Cotacao da semana %s ja esta aberta (id=%s). Use --cotacao-check para verificar respostas.",
            label, existing['id'],
        )
        return

    request_id = create_quotation_request(conn, label, week_start, week_end)
    id_proc = fetch_processo_do_ciclo(conn, request_id)
    proc.concluir_etapa(conn, id_proc, Etapa.ABRIR_CICLO)
    log.info("Cotacao Semanal - %s (%s a %s)", label, week_start, week_end)
    log.info("Request ID: %s", request_id)

    suppliers = fetch_active_meat_suppliers(conn)
    if not suppliers:
        log.info("Nenhum fornecedor de carne ativo cadastrado.")
        proc.falhar_etapa(conn, id_proc, Etapa.PUBLICAR_PLANILHA,
                          Status.ERRO_API,
                          "Nenhum fornecedor ativo e cotado.")
        return

    if supplier_filter:
        termo = supplier_filter.strip().lower()
        total_antes = len(suppliers)
        suppliers = [s for s in suppliers if termo in s["name"].lower()]
        log.info(
            "Filtro --fornecedor %r: %s de %s fornecedor(es) ativo(s).",
            supplier_filter, len(suppliers), total_antes,
        )
        if not suppliers:
            log.info("Nenhum fornecedor ativo bate com o filtro.")
            return

    log.info("%s fornecedor(es) ativo(s).", len(suppliers))
    OUTBOUND_DIR.mkdir(parents=True, exist_ok=True)

    sessao = _abrir_sessao_sharepoint(conn, config, id_proc)
    _publicar_e_notificar_fornecedores(conn, config, sessao, suppliers, request_id, today, label)

    proc.concluir_etapa(conn, id_proc, Etapa.PUBLICAR_PLANILHA)
    proc.concluir_etapa(conn, id_proc, Etapa.NOTIFICAR)
    # daqui em diante depende do fornecedor: e espera, nao falha
    proc.aguardar_etapa(conn, id_proc, Etapa.AGUARDAR_RESPOSTA)

    log.info("Cotacao %s enviada para %s fornecedor(es).", label, len(suppliers))


def _abrir_sessao_sharepoint(conn, config: Config, id_proc: int | None):
    """Abre a sessão SharePoint da cotação. Um único try: falha de login marca
    a etapa, registra o monitor e sobe classificada — quem chama não precisa
    saber que veio de um browser."""
    username = config.sharepoint_cotacao.usuario
    password = config.sharepoint_cotacao.senha
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
    conn, config: Config, sessao, suppliers: list[dict],
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
                conn, config, context, supplier, request_id, today, label, supplier_folder,
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
    conn, config: Config, context, supplier: dict, request_id: int,
    today: date, label: str, supplier_folder: str,
) -> None:
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
        aba=current_month_sheet_name(today),
    )

    if file_url:
        _send_notification(
            conn, config, supplier, file_url, label, response_id,
        )
    else:
        log.warning(
            "AVISO: sem link cadastrado em planilha_url - nao notificado: %s",
            sp_file_path,
        )


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
    log.info("Excel existente baixado do SharePoint.")
    return local_path


def _send_notification(
    conn, config, supplier: dict, file_url: str,
    week_label: str, response_id: int,
) -> None:
    """Envia notificação ao fornecedor pelo canal preferido. Sem try aqui: cada
    canal isola sua própria falha numa função auxiliar."""
    channel = supplier.get("channel", "whatsapp")

    if channel in ("whatsapp", "both") and supplier.get("whatsapp"):
        sucesso = _enviar_whatsapp(conn, config, supplier, file_url, response_id)
        if sucesso or channel == "whatsapp":
            return

    if channel in ("email", "both") and supplier.get("email"):
        _enviar_email(conn, config, supplier, file_url, week_label, response_id)
        return

    log.warning("AVISO: nenhum canal disponivel para %s", supplier['name'])
    update_quotation_response_sent(
        conn, response_id, channel="nenhum",
        envio_status=str(EnvioCotacao.SEM_CANAL))


def _enviar_whatsapp(conn, config: Config, supplier: dict, file_url: str, response_id: int) -> bool:
    """Tenta notificar por WhatsApp. Retorna True se despachado com sucesso."""
    from commons.messaging.messenger import send_whatsapp_quote_notification

    try:
        result = send_whatsapp_quote_notification(
            config.twilio,
            recipient_whatsapp_number=supplier["whatsapp"],
            supplier_sharepoint_url=file_url,
            supplier_name=supplier.get("contact_name") or supplier["name"],
        )
    except ConfigException as exc:
        log.warning("cotacao: Twilio nao configurado - %s", exc)
        monitor.registrar_acesso(conn, Sistema.TWILIO_WHATSAPP, ok=False, mensagem=str(exc))
        update_quotation_response_sent(
            conn, response_id, channel="whatsapp",
            envio_status=str(EnvioCotacao.FALHOU), envio_detalhe=str(exc))
        return False
    except Exception as exc:
        log.error("AVISO: WhatsApp falhou: %s", exc)
        monitor.registrar_acesso(conn, Sistema.TWILIO_WHATSAPP, ok=False, mensagem=str(exc))
        update_quotation_response_sent(
            conn, response_id, channel="whatsapp",
            envio_status=str(EnvioCotacao.FALHOU), envio_detalhe=str(exc))
        return False

    monitor.registrar_acesso(conn, Sistema.TWILIO_WHATSAPP, ok=True)
    update_quotation_response_sent(
        conn, response_id,
        channel="whatsapp",
        message_sid=result.get("sid"),
        envio_status=str(EnvioCotacao.do_twilio(result.get("status"))),
    )
    return True


def _enviar_email(
    conn, config, supplier: dict, file_url: str, week_label: str, response_id: int,
) -> bool:
    """Tenta notificar por e-mail. Retorna True se despachado com sucesso."""
    from .email_sender import send_quotation_email

    try:
        result = send_quotation_email(
            config.smtp,
            recipient_email=supplier["email"],
            supplier_name=supplier.get("contact_name") or supplier["name"],
            sharepoint_url=file_url,
            week_label=week_label,
        )
    except Exception as exc:
        log.error("AVISO: Email falhou: %s", exc)
        monitor.registrar_acesso(conn, Sistema.SMTP_EMAIL, ok=False, mensagem=str(exc))
        update_quotation_response_sent(
            conn, response_id, channel="email",
            envio_status=str(EnvioCotacao.FALHOU), envio_detalhe=str(exc))
        return False

    ok = result.get("status") == "sent"
    monitor.registrar_acesso(conn, Sistema.SMTP_EMAIL, ok=ok,
                             mensagem=result.get("error"))
    update_quotation_response_sent(
        conn, response_id, channel="email",
        envio_status=str(EnvioCotacao.ENVIADO if ok else EnvioCotacao.FALHOU),
        envio_detalhe=result.get("error"),
    )
    return True


# ---------------------------------------------------------------------------
# 2. Verificar respostas
# ---------------------------------------------------------------------------

def check_responses(config: Config) -> None:
    """
    Baixa os Excels do SharePoint, verifica se os fornecedores preencheram
    a coluna da cotação atual, e importa os preços no banco.
    """
    _verificar_ausencias_whatsapp(config)
    with conexao(config.banco) as conn:
        _checar_respostas(conn, config)


def _verificar_ausencias_whatsapp(config: Config) -> None:
    """Best-effort: ausência no WhatsApp não impede importar os Excels."""
    try:
        process_whatsapp_replies(config)
    except Exception as exc:  # noqa: BLE001 — ver docstring
        log.warning("cotacao: erro ao verificar ausencias no WhatsApp - %s", exc)


def _checar_respostas(conn, config: Config) -> None:
    """Importa as respostas do ciclo aberto e fecha (ou mantém) a etapa."""
    open_request = fetch_open_quotation_request(conn)
    if not open_request:
        log.info("cotacao: nenhum ciclo de cotacao aberto")
        return

    log.info("cotacao: verificando respostas - ciclo %s (id=%s)",
             open_request["week_label"], open_request["id"])
    id_proc = fetch_processo_do_ciclo(conn, open_request["id"])

    pending = fetch_pending_responses(conn, open_request["id"])
    if not pending:
        # nada pendente = todos ja importados, o ciclo terminou
        log.info("cotacao: nenhuma resposta pendente - ciclo completo")
        proc.concluir_etapa(conn, id_proc, Etapa.IMPORTAR_PRECOS)
        return

    log.info("cotacao: %d resposta(s) pendente(s)", len(pending))

    pw, browser, context = _abrir_sessao_cotacao(conn, id_proc, config)
    try:
        importados = sum(
            _importar_uma_resposta(conn, context, resp, open_request)
            for resp in pending
        )
    finally:
        fechar_browser(browser)
        parar_playwright(pw)

    log.info("cotacao: %d fornecedor(es) responderam neste check", importados)
    if importados:
        # Preço novo pode destravar nota que fechou como "sem cotação"
        # nesta mesma janela — sem isto ela fica encerrada para sempre.
        n_reproc = marcar_reprocesso_por_cotacao(conn)
        if n_reproc:
            log.info("cotacao: %d nota(s) remarcada(s) para reconciliar de novo "
                     "(tinham fechado sem cotacao)", n_reproc)

    if not fetch_pending_responses(conn, open_request["id"]):
        proc.concluir_etapa(conn, id_proc, Etapa.IMPORTAR_PRECOS)
        log.info("cotacao: ciclo completo - todos os fornecedores importados")
    else:
        proc.aguardar_etapa(conn, id_proc, Etapa.AGUARDAR_RESPOSTA)


def _abrir_sessao_cotacao(conn, id_proc: int, config: Config):
    """Abre a sessão SharePoint do site de Cotações. Marca o monitor nos dois
    desfechos e, na falha, carimba `ERRO_LOGIN` antes de propagar."""
    # Mesmo usuário SharePoint de start_weekly_quotation — é a conta com
    # acesso ao site de Cotações, diferente das duas de invoices (loja).
    username = config.sharepoint_cotacao.usuario
    password = config.sharepoint_cotacao.senha
    try:
        pw, browser, context, _page = open_sharepoint_session(
            username, password, SHAREPOINT_SITE_URL, headless=False,
        )
    except Exception as exc:
        proc.falhar_etapa(conn, id_proc, Etapa.IMPORTAR_PRECOS,
                          Status.ERRO_LOGIN, f"SharePoint (cotação): {exc}")
        _marcar_sharepoint(conn, ok=False, mensagem=str(exc))
        raise
    _marcar_sharepoint(conn, ok=True)
    return pw, browser, context


def _importar_uma_resposta(conn, context, resp: dict, open_request: dict) -> bool:
    """Baixa o Excel de UM fornecedor e importa os preços. True se importou."""
    supplier_name = resp["supplier_name"]
    file_name = resp["file_name"]
    col_index = resp["column_index"]
    quote_date = resp["quote_date"]

    if not file_name or not col_index:
        return False

    supplier_folder = _sharepoint_folder_for_supplier()
    local_path = _baixar_ou_criar_planilha(
        context, supplier_folder, file_name, supplier_name, quote_date,
    )
    if local_path is None:
        log.info("cotacao: %s - planilha criada, aguardando preenchimento",
                 supplier_name)
        return False

    # add_quotation_column escreve na aba do mes da cotacao, mas
    # read_quotation_column sem sheet_name cai em wb.active — que pode
    # ser outra aba. Sem isto le a coluna certa da aba errada.
    aba = resp.get("aba") or (
        current_month_sheet_name(quote_date) if quote_date else None)
    prices = read_quotation_column(local_path, col_index, aba)
    if not has_responses(prices):
        log.info("cotacao: %s - aguardando preenchimento", supplier_name)
        return False

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
    log.info("cotacao: %s - %d preco(s) importado(s)", supplier_name, inserted)
    return True


def _baixar_ou_criar_planilha(
    context, supplier_folder: str, file_name: str, supplier_name: str, quote_date,
):
    """Baixa a planilha do fornecedor. Se não existe no SharePoint, cria e
    sobe uma em branco, e devolve `None` — não há o que importar nesta volta."""
    local_path = OUTBOUND_DIR / file_name
    try:
        download_single_file(
            context, SHAREPOINT_SITE_URL, f"{supplier_folder}/{file_name}", local_path,
        )
    except RuntimeError:
        ref = quote_date or date.today()
        nova = create_initial_quotation_excel(
            supplier_name, [], OUTBOUND_DIR, ref_date=ref,
        )
        add_quotation_column(nova, ref)
        upload_file_to_sharepoint(
            context, SHAREPOINT_SITE_URL, supplier_folder, nova,
        )
        return None
    return local_path


# ---------------------------------------------------------------------------
# 3. Cobranças / Follow-ups
# ---------------------------------------------------------------------------

def send_followups(config: Config) -> None:
    """
    Envia lembretes para fornecedores que não responderam a cotação, a cada
    `FOLLOWUP_INTERVAL_HOURS` horas, só dentro do horário comercial
    (`FOLLOWUP_WINDOW_START_HOUR`-`FOLLOWUP_WINDOW_END_HOUR`). Fora da janela,
    não cobra — quem chama de novo mais tarde (o cron externo) é quem decide
    a frequência real de checagem. Fornecedores ausentes são ignorados.
    """
    hora_atual = datetime.now().hour
    if not (FOLLOWUP_WINDOW_START_HOUR <= hora_atual < FOLLOWUP_WINDOW_END_HOUR):
        log.info(
            "Fora do horario comercial (%sh-%sh) - cobranca adiada.",
            FOLLOWUP_WINDOW_START_HOUR, FOLLOWUP_WINDOW_END_HOUR,
        )
        return

    _verificar_ausencias_whatsapp(config)
    with conexao(config.banco) as conn:
        _cobrar_atrasados(conn, config)


def _cobrar_atrasados(conn, config: Config) -> None:
    """Cobra, um a um, os fornecedores sem resposta do ciclo aberto."""
    open_request = fetch_open_quotation_request(conn)
    if not open_request:
        log.info("cotacao: nenhum ciclo de cotacao aberto")
        return

    log.info("cotacao: cobrancas do ciclo %s", open_request["week_label"])
    overdue = fetch_responses_for_followup(
        conn, open_request["id"], min_hours=FOLLOWUP_INTERVAL_HOURS,
    )
    if not overdue:
        log.info("cotacao: nenhum fornecedor pendente de cobranca")
        return

    log.info("cotacao: %d fornecedor(es) sem resposta ha mais de %dh",
             len(overdue), FOLLOWUP_INTERVAL_HOURS)

    for resp in overdue:
        _cobrar_um(conn, config, resp, open_request)


def _cobrar_um(conn, config: Config, resp: dict, open_request: dict) -> None:
    """Manda UMA cobrança, pelo canal que o fornecedor usou no envio."""
    supplier_name = resp["supplier_name"]
    followup_count = resp["followup_count"]

    sent_at = resp["sent_at"]
    if sent_at:
        days_since = (date.today() - sent_at.date()).days
        if days_since > MAX_FOLLOWUP_DAYS:
            log.info("cotacao: %s - prazo expirado (%d dias), ignorando",
                     supplier_name, days_since)
            return

    channel = _canal_da_cobranca(resp)
    notes = f"Cobrança #{followup_count + 1}"

    if channel in ("whatsapp", "both") and resp.get("whatsapp"):
        _cobrar_por_whatsapp(conn, config, resp, supplier_name, notes)
    elif channel in ("email", "both") and resp.get("email"):
        _cobrar_por_email(conn, config, resp, open_request["week_label"],
                          supplier_name, notes)
    else:
        log.warning("cotacao: %s - sem canal disponivel para cobranca", supplier_name)


def _cobrar_por_whatsapp(
    conn, config: Config, resp: dict, supplier_name: str, notes: str,
) -> None:
    """Cobrança por WhatsApp. Falha de um fornecedor não para os demais."""
    from commons.messaging.messenger import send_whatsapp_quote_notification

    try:
        result = send_whatsapp_quote_notification(
            config.twilio,
            recipient_whatsapp_number=resp["whatsapp"],
            supplier_sharepoint_url=resp.get("file_url") or "",
            supplier_name=resp.get("contact_name") or supplier_name,
        )
        save_followup(
            conn, resp["id"],
            channel="whatsapp",
            message_sid=result.get("sid"),
            notes=notes,
        )
    except Exception as exc:  # noqa: BLE001 — laco de item
        log.error("cotacao: %s - cobranca por WhatsApp falhou - %s", supplier_name, exc)
        return
    log.info("cotacao: %s - %s enviada por WhatsApp", supplier_name, notes)


def _cobrar_por_email(
    conn, config: Config, resp: dict, week_label: str,
    supplier_name: str, notes: str,
) -> None:
    """Cobrança por e-mail. Falha de um fornecedor não para os demais."""
    from .email_sender import send_quotation_email

    try:
        send_quotation_email(
            config.smtp,
            recipient_email=resp["email"],
            supplier_name=resp.get("contact_name") or supplier_name,
            sharepoint_url=resp.get("file_url", ""),
            week_label=week_label,
        )
        save_followup(conn, resp["id"], channel="email", notes=notes)
    except Exception as exc:  # noqa: BLE001 — laco de item
        log.error("cotacao: %s - cobranca por e-mail falhou - %s", supplier_name, exc)
        return
    log.info("cotacao: %s - %s enviada por e-mail", supplier_name, notes)


def _canal_da_cobranca(resp: dict) -> str:
    """Canal a usar: o mesmo do envio original, com o cadastro como fallback."""
    sent_ch = resp.get("sent_channel")
    return (sent_ch if sent_ch and sent_ch != "nenhum" else None) or resp.get("channel", "whatsapp")


# ---------------------------------------------------------------------------
# 4. Processar respostas de ausência (WhatsApp)
# ---------------------------------------------------------------------------

def process_whatsapp_replies(config: Config) -> None:
    """Busca respostas de 'Ausente' no Twilio e marca no ciclo atual."""
    conn = connect_db(config.banco)
    try:
        from .excel_handler import check_twilio_absences
        count = check_twilio_absences(conn, config.twilio)
        log.info("%s ausencia(s) processada(s).", count)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 5. Comparação de preços
# ---------------------------------------------------------------------------

def compare_prices(config: Config, request_id: int | None = None) -> None:
    """
    Compara preços entre fornecedores para o ciclo especificado ou o mais recente.
    """
    conn = connect_db(config.banco)

    try:
        if request_id is None:
            open_req = fetch_open_quotation_request(conn)
            if open_req:
                request_id = open_req["id"]
                label = open_req["week_label"]
            else:
                log.info("Nenhum ciclo de cotacao encontrado.")
                return
        else:
            label = f"id={request_id}"

        log.info("Comparacao de Precos - Ciclo %s", label)

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
            log.info("Nenhum preco importado para comparacao.")
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

        # Os dois `print` desta funcao sao APRESENTACAO, nao log: uma tabela
        # de largura fixa que o operador le no terminal (`compare_prices` e
        # chamada a mao, nao pelo pipeline). Pelo `logging` cada linha
        # ganharia prefixo de data/nivel e o alinhamento das colunas se
        # perderia. Mesma excecao de `commons/banner.py`.
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

        log.info(
            "%s item(ns) comparado(s) entre %s fornecedor(es).",
            len(items), len(supplier_list),
        )

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 6. Ciclo automatizado
# ---------------------------------------------------------------------------

CHECK_INTERVAL_MINUTES = 2
MAX_AUTOMATED_CYCLES = 48


def run_automated_cycle(
    config: Config,
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

    with conexao(config.banco) as conn:
        existing = fetch_open_quotation_request(conn)

    if not existing or existing["week_label"] != label:
        start_weekly_quotation(config)
    else:
        log.info("cotacao: ciclo %s ja aberto (id=%s), retomando monitoramento",
                 label, existing["id"])

    for cycle in range(1, max_cycles + 1):
        next_check = datetime.now() + timedelta(minutes=check_interval_minutes)
        log.info("cotacao: aguardando %dmin - ciclo %d/%d - proxima verificacao %s",
                 check_interval_minutes, cycle, max_cycles,
                 next_check.strftime("%d/%m %H:%M"))
        time.sleep(check_interval_minutes * 60)

        _verificar_ausencias_whatsapp(config)
        check_responses(config)

        with conexao(config.banco) as conn:
            if _cobrar_ou_encerrar(conn, config) == "encerrado":
                break

    _print_monitoring_report(config)


def _cobrar_ou_encerrar(conn, config: Config) -> str:
    """Decide o passo do ciclo automatizado: cobra os pendentes, ou encerra.

    Devolve "encerrado" quando não há mais o que esperar (ciclo fechado ou
    todos responderam) — quem chama usa isso para sair do laço.
    """
    open_req = fetch_open_quotation_request(conn)
    if not open_req:
        log.info("cotacao: ciclo encerrado")
        return "encerrado"

    pending = fetch_pending_responses(conn, open_req["id"])
    if not pending:
        log.info("cotacao: todos os fornecedores responderam")
        close_quotation_request(conn, open_req["id"])
        return "encerrado"

    log.info("cotacao: %d fornecedor(es) pendente(s), enviando cobrancas", len(pending))
    _send_followup_to_pending(conn, config, pending, open_req["week_label"])
    return "cobrado"


def _send_followup_to_pending(
    conn, config: Config, pending: list[dict], week_label: str,
) -> None:
    """Envia cobranca WhatsApp/email para fornecedores que nao responderam.

    Reusa `_cobrar_por_whatsapp`/`_cobrar_por_email` — era uma segunda cópia
    do mesmo envio, com os mesmos dois `try`, diferindo só no texto da nota.
    """
    for resp in pending:
        supplier_name = resp["supplier_name"]
        channel = _canal_da_cobranca(resp)
        if channel in ("whatsapp", "both") and resp.get("whatsapp"):
            notes = f"Cobranca automatica #{contar_cobrancas(conn, resp['id']) + 1}"
            _cobrar_por_whatsapp(conn, config, resp, supplier_name, notes)
        elif channel in ("email", "both") and resp.get("email"):
            _cobrar_por_email(conn, config, resp, week_label, supplier_name,
                              "Cobranca automatica")
        else:
            log.warning("cotacao: %s - sem canal disponivel para cobranca", supplier_name)


def _print_monitoring_report(config: Config) -> None:
    """Imprime relatorio de monitoramento do ciclo atual."""
    from psycopg2.extras import RealDictCursor

    conn = connect_db(config.banco)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, semana_label AS week_label FROM dwschiavon2.dim_ciclo"
                " ORDER BY id DESC LIMIT 1"
            )
            req = cur.fetchone()

        if not req:
            log.info("Nenhum ciclo encontrado para relatorio.")
            return

        request_id = req["id"]
        label = req["week_label"]

        log.info("RELATORIO DE MONITORAMENTO - Ciclo %s", label)

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

        log.info("--- Status dos Fornecedores ---")
        for r in responses:
            icon = status_icons.get(r["status"], "?")
            log.info("[%4s] %-30s %s", icon, r['name'], r['status'])

        responded = sum(1 for r in responses if r["status"] in ("imported", "responded"))
        ausente = sum(1 for r in responses if r["status"] == "ausente")
        pend = sum(1 for r in responses if r["status"] == "sent")
        log.info("Responderam: %s | Ausentes: %s | Pendentes: %s", responded, ausente, pend)

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
            log.info("Nenhum preco importado neste ciclo.")
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
                log.info("--- Maiores Aumentos de Preco ---")
                for c in increases[:10]:
                    log.info(
                        "+%.1f%% %-40s (%s) $%.2f -> $%.2f",
                        c['pct'], c['item'][:40], c['supplier'], c['previous'], c['current'],
                    )

            total_changes = len([c for c in changes if abs(c["pct"]) > 0.5])
            log.info("%s alteracao(oes) de preco identificada(s).", total_changes)
        else:
            log.info("Sem historico anterior para comparacao de precos.")

    finally:
        conn.close()
