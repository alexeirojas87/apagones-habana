"""Transcribe mensajes de voz del grupo de comentarios usando Whisper en NaN.

Se ejecuta después del ingestor: descarga los audios nuevos (los que no tienen
texto asociado), los transcribe con Whisper de NaN Builders, y guarda el texto
transcrito en la tabla mensajes como si fuera un comentario de texto normal,
con flag 'via: whisper'.

Así los mensajes de voz entran al mismo pipeline de comentarios_llm.py y
generan reportes vecinales sin que nadie escriba una palabra.

Env: TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION,
     SUPABASE_URL, SUPABASE_SERVICE_KEY, NAN_API_KEY
"""

import asyncio
import io
import json
import os
import sys
import urllib.request

from supabase import create_client
from telethon import TelegramClient
from telethon.sessions import StringSession

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GRUPO = "EmpresaElectricaDeLaHabana"
LIMITE_POR_CORRIDA = 20

NAN_BASE_URL = os.environ.get("NAN_BASE_URL", "https://api.nanbuilders.ai/v1")
MODELO_WHISPER = os.environ.get("MODELO_WHISPER", "whisper")


def db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def transcribir(audio_bytes, api_key):
    boundary = "----FormBoundary7MA4YWxkTrZu0gW"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="model"\r\n\r\n'
        f"{MODELO_WHISPER}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="audio.ogg"\r\n'
        f"Content-Type: audio/ogg\r\n\r\n"
    ).encode("utf-8") + audio_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        f"{NAN_BASE_URL}/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        data = json.load(urllib.request.urlopen(req, timeout=120))
        return data.get("text") or data.get("response")
    except Exception as e:
        print(f"  Whisper error: {e}")
        return None


async def main():
    sb = db()
    api_key = os.environ.get("NAN_API_KEY")
    if not api_key:
        print("audio_a_texto: sin NAN_API_KEY, se omite")
        return

    ultimo = (sb.table("mensajes")
              .select("message_id").eq("chat", "voz_procesado")
              .order("message_id", desc=True).limit(1).execute().data)
    ultimo_id = ultimo[0]["message_id"] if ultimo else 0

    client = TelegramClient(
        StringSession(os.environ["TELEGRAM_SESSION"]),
        int(os.environ["TELEGRAM_API_ID"]),
        os.environ["TELEGRAM_API_HASH"],
    )
    await client.start()

    grupo = await client.get_entity(GRUPO)
    transcritos = 0
    async for msg in client.iter_messages(grupo, limit=LIMITE_POR_CORRIDA,
                                          offset_id=ultimo_id if ultimo_id else 0):
        if not msg.voice and not msg.audio:
            continue
        if msg.id <= ultimo_id:
            continue
        audio_data = await msg.download_media(bytes=io.BytesIO())
        if not audio_data:
            continue
        print(f"  Transcribiendo voz msg_id={msg.id}...")
        texto = transcribir(audio_data.getvalue(), api_key)
        if not texto:
            continue
        sb.table("mensajes").upsert({
            "chat": "comentarios",
            "message_id": msg.id,
            "fecha": msg.date.isoformat() if msg.date else None,
            "texto": f"[transcripción por voz] {texto[:2000]}",
            "reply_to": msg.reply_to.reply_to_msg_id if msg.reply_to else None,
            "via": "whisper",
        }, on_conflict="chat,message_id").execute()
        transcritos += 1

    # Guarda último procesado
    if transcritos:
        sb.table("mensajes").upsert({
            "chat": "voz_procesado",
            "message_id": msg.id if transcritos else ultimo_id,
            "fecha": None, "texto": "",
        }, on_conflict="chat,message_id").execute()

    await client.disconnect()
    print(f"audio_a_texto: {transcritos} mensajes de voz transcritos")


if __name__ == "__main__":
    asyncio.run(main())