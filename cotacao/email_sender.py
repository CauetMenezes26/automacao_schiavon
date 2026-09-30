"""E-mail de cotação ao fornecedor.

Monta o corpo HTML da cotação semanal; o transporte SMTP fica em `commons/email_client.py`
(compartilhado com os alertas à operação; recebe o `ConfigSmtp` do projeto).
"""

from __future__ import annotations

from datetime import datetime

from commons.email_client import ConfigSmtp, enviar_email


def send_quotation_email(
    smtp: ConfigSmtp,
    recipient_email: str,
    supplier_name: str,
    sharepoint_url: str,
    week_label: str,
) -> dict:
    """Envia o e-mail com o link da cotação semanal.

    Retorna o dict de log de `commons.email_client.enviar_email` (`status` = 'sent' |
    'error'). Levanta `ConfigException` se o SMTP não estiver configurado.
    """
    formatted_date = datetime.now().strftime("%d/%m/%Y")
    subject = f"Cotação Semanal de Carnes - {week_label} - DataGuvi"

    html_body = f"""\
<html>
<body style="font-family: Arial, sans-serif; color: #333;">
  <h2>Cotação Semanal - {week_label}</h2>
  <p>Olá, <strong>{supplier_name}</strong>!</p>
  <p>
    Solicitamos a atualização dos preços das carnes para a cotação semanal
    para o parceiro <strong>Rokka</strong>.
  </p>
  <p>
    Clique no botão abaixo para acessar e preencher os valores na planilha de cotação:
  </p>
  <p style="margin: 24px 0;">
    <a href="{sharepoint_url}"
       style="background-color: #0078D4; color: white; padding: 12px 24px;
              text-decoration: none; border-radius: 4px; font-weight: bold;">
      Preencher Cotação
    </a>
  </p>
  <p style="font-size: 0.9em; color: #666;">
    Quando estiver indisponível nesta semana, por favor responda este e-mail
    informando sua ausência.
  </p>
  <hr style="border: none; border-top: 1px solid #ddd; margin: 24px 0;">
  <p style="font-size: 0.8em; color: #999;">
    DataGuvi — Sistema de Cotação Automatizado<br>
    Enviado em {formatted_date}
  </p>
</body>
</html>"""

    text_body = (
        f"Cotação Semanal - {week_label}\n\n"
        f"Olá, {supplier_name}!\n\n"
        f"Solicitamos a atualização dos preços das carnes para a cotação semanal "
        f"para o parceiro Rokka.\n\n"
        f"Acesse a planilha: {sharepoint_url}\n\n"
        f"Quando estiver indisponível nesta semana, responda este e-mail "
        f"informando sua ausência.\n\n"
        f"DataGuvi — Enviado em {formatted_date}"
    )

    return enviar_email(smtp, recipient_email, subject, html_body, text_body)
