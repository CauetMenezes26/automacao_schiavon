"""Consentimento OAuth único da caixa Gmail que recebe o código do Cloudflare
Access (login no Catapult).

Roda à mão, uma vez (ou de novo se `token.json` for revogado/apagado): abre o
navegador, você autoriza a conta que recebe o código, e o script salva o
token em `resources/gmail/token.json` (fora do git, ver `.gitignore`).

Pré-requisito: `resources/gmail/client_secret.json` baixado do Google Cloud
Console (credencial OAuth "App para computador").

    python -m manutencao.gmail_oauth_setup
"""

from __future__ import annotations

from google_auth_oauthlib.flow import InstalledAppFlow

from commons.gmail import SCOPES
from commons.paths import GMAIL_CLIENT_SECRET, GMAIL_TOKEN_PATH


def main() -> None:
    if not GMAIL_CLIENT_SECRET.exists():
        raise SystemExit(
            f"'{GMAIL_CLIENT_SECRET}' não encontrado. Baixe a credencial OAuth "
            "'App para computador' no Google Cloud Console e salve nesse caminho."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(GMAIL_CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(port=0)

    GMAIL_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GMAIL_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    print(f"Token salvo em '{GMAIL_TOKEN_PATH}'.")


if __name__ == "__main__":
    main()
