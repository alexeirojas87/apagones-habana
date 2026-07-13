"""Genera web/data/partes.json: el feed de partes OFICIALES del canal de la
Empresa Eléctrica / UNE (últimos días), para leerlos dentro de la app sin tener
que ir a Telegram. Solo posts del canal (chat='canal'), no comentarios.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone

from supabase import create_client

RAIZ = os.path.join(os.path.dirname(__file__), "..")
DIAS = 7          # ventana del feed
MAX = 250         # tope de posts
CANAL = "EmpresaElectricaDeLaHabana"  # para el enlace "ver en Telegram"


def etiqueta(texto):
    """Etiqueta corta para escanear el feed de un vistazo (no altera el texto)."""
    t = texto.lower()
    if re.search(r"desconexi[oó]n total del (sistema electroenerg|sen\b)", t):
        return "🔴 Desconexión SEN"
    if "actualización de afectaciones" in t or "actualizacion de afectaciones" in t:
        return "📊 Parte de afectación"
    if "averías existentes" in t or "averias existentes" in t:
        return "🚧 Averías"
    if re.search(r"emergencia en la generaci", t):
        return "⚠️ Emergencia"
    if re.search(r"\brestableci", t):
        return "✅ Restablecimiento"
    if re.search(r"disparo autom[aá]tico|\bdaf\b", t):
        return "🟡 DAF"
    if re.search(r"actualizaci[oó]n del sistema electroenerg", t):
        return "⚡ Parte del SEN"
    if re.search(r"se afecta|afectaci[oó]n|afectados", t):
        return "🔻 Afectación"
    return "📢 Aviso"


def main():
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    desde = (datetime.now(timezone.utc) - timedelta(days=DIAS)).isoformat()
    filas = (
        sb.table("mensajes").select("message_id,fecha,texto")
        .eq("chat", "canal").gte("fecha", desde)
        .order("fecha", desc=True).limit(MAX).execute().data
    )
    partes = [
        {
            "id": f["message_id"],
            "fecha": f["fecha"],                     # ISO con tz (UTC); el front lo pasa a hora de Cuba
            "texto": (f["texto"] or "").strip(),
            "tag": etiqueta(f["texto"] or ""),
        }
        for f in filas
        if (f.get("texto") or "").strip()
    ]
    salida = {"generado": datetime.now(timezone.utc).isoformat(), "canal": CANAL, "partes": partes}
    destino = os.path.join(RAIZ, "web", "data", "partes.json")
    json.dump(salida, open(destino, "w"), ensure_ascii=False)
    print(f"partes.json: {len(partes)} partes ({os.path.getsize(destino) // 1024} KB)")


if __name__ == "__main__":
    main()
