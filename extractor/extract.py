"""Extractor: convierte mensajes crudos (canal + comentarios) en eventos estructurados.

Basado en reglas/regex: los posts oficiales de la EELH siguen plantillas muy
regulares. Los comentarios se procesan de forma conservadora (solo se emite un
evento cuando el texto es inequívoco); lo que no matchea queda marcado como
procesado igualmente y puede re-procesarse en el futuro con un parser mejor.

Tipos de evento emitidos:
    afectacion            (posts oficiales: DAF, déficit, averías, sobrecarga)
    restablecimiento      (posts oficiales)
    reporte_sin_servicio  (comentarios de usuarios)
    reporte_con_servicio  (comentarios de usuarios)

Variables de entorno: SUPABASE_URL, SUPABASE_SERVICE_KEY
"""

import os
import re
import unicodedata

from supabase import create_client

LOTE = 500

MUNICIPIOS = {
    "10 de octubre": "10 de Octubre",
    "diez de octubre": "10 de Octubre",
    "arroyo naranjo": "Arroyo Naranjo",
    "boyeros": "Boyeros",
    "centro habana": "Centro Habana",
    "cerro": "Cerro",
    "cotorro": "Cotorro",
    "guanabacoa": "Guanabacoa",
    "habana del este": "Habana del Este",
    "habana vieja": "Habana Vieja",
    "la lisa": "La Lisa",
    "lisa": "La Lisa",
    "marianao": "Marianao",
    "playa": "Playa",
    "plaza de la revolucion": "Plaza",
    "plaza": "Plaza",
    "regla": "Regla",
    "san miguel del padron": "San Miguel del Padrón",
}

CAUSAS = [
    (r"desconexi[oó]n total del (sistema electroenerg|sen\b)", "desconexión total del SEN"),
    (r"disparo autom[aá]tico por frecuencia|\bDAF\b", "DAF"),
    (r"d[eé]ficit de generaci[oó]n|afectaciones? por d[eé]ficit", "déficit de generación"),
    (r"disparo por sobrecarga|\bsobrecarga\b", "sobrecarga"),
    (r"aver[ií]a", "avería"),
    (r"trabajos? de mantenimiento|mantenimiento programado", "mantenimiento"),
]

RE_BLOQUE = re.compile(r"\bb(?:loque)?s?\s*(?:no\.?\s*)?#?\s*([1-6])\b", re.IGNORECASE)
# enumeraciones: "bloques 3 y 4", "bloques #1, #2 y #5" — captura la lista completa
RE_BLOQUES_LISTA = re.compile(
    r"\bbloques\s*((?:(?:no\.?\s*)?#?\s*[1-6]\s*[,y]?\s*)+)", re.IGNORECASE
)

SIN_SERVICIO = re.compile(
    r"sin (corriente|luz|electricidad|servicio)"
    r"|no (hay|tenemos|tengo|llega la?) (corriente|luz|electricidad|servicio)"
    r"|llevamos .{0,20}(horas?|d[ií]as?) sin"
    r"|cu[aá]ndo .{0,30}(ponen|llega|restablecen)",
    re.IGNORECASE,
)
CON_SERVICIO = re.compile(
    r"(lleg[oó]|pusieron|vino|regres[oó]|volvi[oó]) la (corriente|luz|electricidad)"
    r"|ya (hay|tenemos|lleg[oó]|puso|vino) .{0,15}(corriente|luz|electricidad)"
    r"|gracias por (la corriente|restablecer)",
    re.IGNORECASE,
)


def normalizar(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn"
    )


def municipios_en(texto: str) -> list:
    plano = normalizar(texto)
    encontrados = []
    for clave, nombre in MUNICIPIOS.items():
        if re.search(rf"\b{re.escape(clave)}\b", plano) and nombre not in encontrados:
            encontrados.append(nombre)
    return encontrados


def causa_en(texto: str):
    for patron, nombre in CAUSAS:
        if re.search(patron, texto, re.IGNORECASE):
            return nombre
    return None


def bloques_en(texto: str) -> list:
    nums = {int(b) for b in RE_BLOQUE.findall(texto)}
    for lista in RE_BLOQUES_LISTA.findall(texto):
        nums.update(int(n) for n in re.findall(r"[1-6]", lista))
    return sorted(nums)


def zonas_en(texto: str) -> list:
    """Líneas de bullet ('👉 Municipio: zonas', '💥Dirección: ...') de posts oficiales."""
    zonas = []
    for linea in texto.split("\n"):
        linea = linea.strip()
        if not linea.startswith(("👉", "💥", "📌", "📈")):
            continue
        contenido = linea.lstrip("👉💥📌📈🏼").strip()
        contenido = re.sub(r"[🚧📉✅🔔‼️⚡️📣📌]+$", "", contenido).strip()
        if ":" in contenido:
            clave, resto = contenido.split(":", 1)
            claven = normalizar(clave.strip())
            if claven == "municipio":
                continue  # el municipio ya se captura aparte
            # conserva el prefijo 'Zonas:'/'Zona:' (lo necesita la regla Alamar) y
            # el CÓDIGO de circuito ('AL56 - Zonas: 13; 15...' -> sin esto se botaba
            # el identificador); el resto de etiquetas (Dirección, etc.) se recortan.
            if not (claven.startswith("zona")
                    or re.match(r"(?:[a-z]{1,3}\d{1,4}|\d{2,4})\b", claven)):
                contenido = resto.strip()
        if contenido:
            zonas.append(contenido)
    return zonas


def extraer_de_post(texto: str) -> list:
    """Eventos de un post oficial del canal. Devuelve lista de dicts parciales."""
    plano = normalizar(texto)
    # Desconexión total del SEN = apagón NACIONAL: caen los 6 bloques a la vez.
    # El aviso no nombra bloques, así que forzamos los seis con su causa propia;
    # el conteo de horas arranca desde este mensaje. El restablecimiento llega
    # luego en avisos normales (restablecimiento / parte de actualización).
    if re.search(r"desconexi[oó]n total del (sistema electroenerg|sen\b)", plano):
        return [
            {"municipios": [], "zonas": [], "causa": "desconexión total del SEN",
             "tipo": "afectacion", "bloque": b}
            for b in range(1, 7)
        ]
    restablece = bool(
        re.search(
            r"restableci|quedan? reparad|con servicio los siguientes"
            r"|teniendo con servicio|quedan? con servicio",
            plano,
        )
    )
    afecta = bool(
        re.search(
            r"se (ha )?afect|afectados? por|afectaciones|se localizan? aver"
            r"|se detect[oa]|se interrump|averias? existentes"
            r"|inicia la afectacion|afectando|circuitos? disparad",
            plano,
        )
    )
    if not restablece and not afecta:
        return []

    tipo = "restablecimiento" if restablece and not afecta else "afectacion"
    # posts mixtos ("restablecido X, continúa afectado Y") se clasifican por el inicio
    if restablece and afecta:
        tipo = "restablecimiento" if re.search(r"^\W*(✅|restableci|queda reparada)", texto.lower()) else "afectacion"
    # Restablecimiento PARCIAL: el bloque sigue afectado y solo se listan algunos
    # circuitos. Señales: "se continúa/se trabaja/inicia... restablecimiento",
    # "de forma gradual/paulatina", o que enumere "los siguientes circuitos".
    if tipo == "restablecimiento" and re.search(
        r"(continua|se trabaja|inicia)\b.{0,45}restablecimiento"
        r"|de (forma|manera) (gradual|paulatina)"
        r"|siguientes circuitos|servicio en los siguientes",
        plano,
    ):
        tipo = "restablecimiento_parcial"

    bloques = bloques_en(texto)
    if re.search(r"circuitos? de emergencia", plano):
        bloques.append(0)  # 0 = Circuitos de Emergencia
    base = {
        "municipios": municipios_en(texto),
        "zonas": zonas_en(texto),
        "causa": causa_en(texto),
        "tipo": tipo,
    }
    if bloques:
        return [{**base, "bloque": b} for b in bloques]
    return [{**base, "bloque": None}]


def extraer_de_comentario(texto: str) -> list:
    if len(texto) > 600:  # descarta copypastas largos
        return []
    sin = bool(SIN_SERVICIO.search(texto))
    con = bool(CON_SERVICIO.search(texto))
    if sin == con:  # ni uno ni otro, o ambos (ambiguo)
        return []
    bloques = bloques_en(texto)
    return [
        {
            "tipo": "reporte_sin_servicio" if sin else "reporte_con_servicio",
            "bloque": bloques[0] if len(bloques) == 1 else None,
            "municipios": municipios_en(texto),
            "zonas": [],
            "causa": None,
        }
    ]


def extraer(chat: str, texto: str) -> list:
    if not texto:
        return []
    return extraer_de_post(texto) if chat == "canal" else extraer_de_comentario(texto)


def main():
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    total_msgs = total_eventos = 0

    while True:
        res = (
            sb.table("mensajes")
            .select("chat,message_id,fecha,texto")
            .eq("procesado", False)
            .order("message_id")
            .limit(LOTE)
            .execute()
        )
        if not res.data:
            break

        eventos = []
        for m in res.data:
            for ev in extraer(m["chat"], m["texto"]):
                eventos.append(
                    {**ev, "chat": m["chat"], "message_id": m["message_id"], "fecha": m["fecha"]}
                )

        if eventos:
            sb.table("eventos").insert(eventos).execute()
        # marcar procesados en bloque, por chat
        for chat in {m["chat"] for m in res.data}:
            ids = [m["message_id"] for m in res.data if m["chat"] == chat]
            sb.table("mensajes").update({"procesado": True}).eq("chat", chat).in_(
                "message_id", ids
            ).execute()

        total_msgs += len(res.data)
        total_eventos += len(eventos)

    print(f"Extracción OK: {total_msgs} mensajes procesados, {total_eventos} eventos")


if __name__ == "__main__":
    main()
