from twilio.rest import Client
import os
from dotenv import load_dotenv

class Twilio:
    @staticmethod
    def returnClient():
        load_dotenv()
        return Client(os.getenv("ACCOUNT_SID"), os.getenv("AUTH_TOKEN"))
