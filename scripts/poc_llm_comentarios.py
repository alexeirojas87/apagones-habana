"""PoC: extraer señal estructurada de los comentarios de vecinos con el LLM
(Workers AI). Los comentarios son lenguaje libre — aquí las reglas se quedan
cortas y el LLM debería aportar cobertura real.

Uso:
    CLOUDFLARE_ACCOUNT_ID=... CLOUDFLARE_AI_TOKEN=... \
    MODELO=@cf/meta/llama-3.3-70b-instruct-fp8-fast python scripts/poc_llm_comentarios.py
"""

import json
import os
import re
import urllib.request

MODELO = os.environ.get("MODELO", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
MUESTRA = os.path.join(os.path.dirname(__file__), "..", "data", "muestra_comentarios_eval.json")

PROMPT = (
    "Eres un analista de reportes ciudadanos sobre apagones en La Habana. "
    "Cada mensaje es un comentario de un vecino. Devuelve SOLO un objeto JSON, sin texto extra:\n"
    '{"reporta": uno de ["sin_corriente","con_corriente","pregunta","queja","irrelevante"],\n'
    ' "lugar": nombre del reparto/barrio/calle que menciona el vecino, o null,\n'
    ' "bloque": entero 1-6 si lo menciona explícitamente, o null,\n'
    ' "horas_sin_luz": número de horas sin electricidad si lo dice o se deduce ("desde ayer a las 7am"), o null}\n'
    "'sin_corriente' = afirma no tener luz. 'con_corriente' = dice que ya llegó. "
    "'pregunta' = solo pregunta cuándo. 'queja' = protesta sin dato útil. "
    "'irrelevante' = spam, saludos, config de Telegram, política sin dato de zona. "
    "El 'lugar' debe ser un topónimo real (reparto/calle), NO una frase."
)


def llm(texto, account, token):
    body = json.dumps({
        "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": texto[:800]}],
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{MODELO}",
        data=body, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    r = json.load(urllib.request.urlopen(req, timeout=60)).get("result", {})
    salida = r.get("response")
    if not isinstance(salida, str):
        try:
            salida = r["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None
    salida = salida.replace("```json", "").replace("```", "")
    m = re.search(r"\{.*\}", salida, re.DOTALL)
    return json.loads(m.group(0)) if m else None


def main():
    account, token = os.environ["CLOUDFLARE_ACCOUNT_ID"], os.environ["CLOUDFLARE_AI_TOKEN"]
    muestra = json.load(open(MUESTRA))
    util, fallos = 0, 0
    for p in muestra:
        t = p["texto"]
        try:
            r = llm(t, account, token)
        except Exception as e:
            print("err:", str(e)[:50]); fallos += 1; continue
        if not isinstance(r, dict):
            fallos += 1; continue
        rep = r.get("reporta")
        if rep in ("sin_corriente", "con_corriente") and r.get("lugar"):
            util += 1
        print(f"[{rep:14}] lugar={str(r.get('lugar'))[:28]:28} b={r.get('bloque')} h={r.get('horas_sin_luz')}")
        print(f"                {t[:70]!r}")
    print(f"\nComentarios con señal accionable (sin/con + lugar): {util}/{len(muestra)} | fallos JSON: {fallos}")


if __name__ == "__main__":
    main()
