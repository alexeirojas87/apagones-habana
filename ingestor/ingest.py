"""Ingesta incremental del canal de la EELH y su grupo de discusión hacia Supabase.

Pensado para ejecutarse de forma periódica (GitHub Actions cron): se conecta,
baja los mensajes nuevos desde el último message_id guardado y termina.

Variables de entorno requeridas:
    TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION
    SUPABASE_URL, SUPABASE_SERVICE_KEY
"""

import asyncio
import os
import sys

from supabase import create_client
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import GetFullChannelRequest

CANAL = "EmpresaElectricaDeLaHabana"
MAX_POR_CORRIDA = 1000   # tope incremental por chat y por corrida
BACKFILL_INICIAL = 2000  # con la tabla vacía: solo los N mensajes más recientes
                         # (la historia completa son años de canal y millones de
                         # comentarios: no cabe en el tier gratis ni hace falta)


def db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def ultimo_id(sb, chat: str) -> int:
    res = (
        sb.table("mensajes")
        .select("message_id")
        .eq("chat", chat)
        .order("message_id", desc=True)
        .limit(1)
        .execute()
    )
    return res.data[0]["message_id"] if res.data else 0


def a_fila(chat: str, msg) -> dict:
    # No guardamos el dict completo de Telethon en 'raw': eran varios KB por
    # mensaje que nada aguas abajo lee y llenaban el tier gratis de Supabase
    # (~180 MB/mes). El extractor solo usa texto/fecha/chat/message_id/reply_to.
    return {
        "chat": chat,
        "message_id": msg.id,
        "fecha": msg.date.isoformat() if msg.date else None,
        "texto": msg.message or "",
        "reply_to": msg.reply_to.reply_to_msg_id if msg.reply_to else None,
    }


async def ingerir_chat(client, sb, entidad, chat: str) -> int:
    desde = ultimo_id(sb, chat)
    filas = []
    if desde == 0:
        # Primera corrida: solo los mensajes más recientes, del más nuevo al más viejo.
        async for msg in client.iter_messages(entidad, limit=BACKFILL_INICIAL):
            if msg.message:  # ignora mensajes de servicio sin texto
                filas.append(a_fila(chat, msg))
    else:
        # Corridas siguientes: incremental desde el último id guardado.
        async for msg in client.iter_messages(
            entidad, min_id=desde, limit=MAX_POR_CORRIDA, reverse=True
        ):
            if msg.message:
                filas.append(a_fila(chat, msg))

    for i in range(0, len(filas), 100):
        sb.table("mensajes").upsert(
            filas[i : i + 100], on_conflict="chat,message_id"
        ).execute()
    return len(filas)


async def main():
    client = TelegramClient(
        StringSession(os.environ["TELEGRAM_SESSION"]),
        int(os.environ["TELEGRAM_API_ID"]),
        os.environ["TELEGRAM_API_HASH"],
    )
    sb = db()

    async with client:
        canal = await client.get_entity(CANAL)
        n_canal = await ingerir_chat(client, sb, canal, "canal")

        # El grupo de discusión vinculado es donde viven los comentarios.
        full = await client(GetFullChannelRequest(canal))
        linked_id = full.full_chat.linked_chat_id
        n_com = 0
        if linked_id:
            grupo = await client.get_entity(linked_id)
            n_com = await ingerir_chat(client, sb, grupo, "comentarios")
        else:
            print("AVISO: el canal no tiene grupo de discusión vinculado", file=sys.stderr)

    print(f"Ingesta OK: {n_canal} mensajes del canal, {n_com} comentarios")


if __name__ == "__main__":
    asyncio.run(main())
