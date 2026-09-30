"""Cliente Twilio a partir da config tipada (`ConfigTwilio`).

Antes lia `ACCOUNT_SID`/`AUTH_TOKEN` de `os.getenv` depois de um
`load_dotenv` do arquivo generico; agora a credencial chega por parametro,
montada em `domain/config.py` a partir do profile do ambiente ativo.
"""

from dataclasses import dataclass, field

from twilio.rest import Client


@dataclass(frozen=True)
class ConfigTwilio:
    account_sid: str
    auth_token: str = field(repr=False)
    numero: str
    content_sid: str


class Twilio:
    @staticmethod
    def returnClient(cfg: ConfigTwilio) -> Client:
        return Client(cfg.account_sid, cfg.auth_token)
