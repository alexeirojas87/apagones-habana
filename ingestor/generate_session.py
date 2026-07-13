"""Genera la StringSession de Telegram para el ingestor.

Ejecutar UNA VEZ en tu máquina (interactivo: pide teléfono y código de login):

    pip install telethon
    TELEGRAM_API_ID=xxx TELEGRAM_API_HASH=yyy python generate_session.py

Copia la cadena que imprime y guárdala como secret TELEGRAM_SESSION en GitHub.
Usa una cuenta de Telegram DEDICADA (no la personal) suscrita al canal
@EmpresaElectricaDeLaHabana y a su grupo de discusión.
"""

import os

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(os.environ["TELEGRAM_API_ID"])
api_hash = os.environ["TELEGRAM_API_HASH"]

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\n=== Copia esta cadena como secret TELEGRAM_SESSION ===\n")
    print(client.session.save())
