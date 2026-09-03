"""S1-S7 del delta spec municipios-info: el concepto bloque/rotación desaparece
de toda superficie visible, y el plano de datos latente (estado.py, geozonas,
bots) queda INTACTO como cláusula KEEP.

Escaneos sobre páginas generadas con fixtures (S1), tokens prohibidos en los
archivos commiteados de la app (S3), ausencia en git (S2) y guardas de que los
constructores que aún usan `bloque` como campo de datos no se tocaron (S6/S7).
"""

import os
import re
import subprocess
import sys
import unicodedata
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_seo  # noqa: E402  (mismo árbol de fixtures y helpers)

RAIZ = test_seo.RAIZ
MOD = test_seo.MOD


def _plano(texto):
    """minúsculas y sin acentos: «rotación» == «rotacion»."""
    plegado = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in plegado if unicodedata.category(c) != "Mn")


def _sin_valores_crudos(html):
    """Secciones del HTML donde un valor crudo de datos (calles) PUEDE decir
    «bloque» sin violar S1: el contenido de .circ-calles."""
    return re.sub(r'<div class="circ-calles">.*?</div>', "", html, flags=re.DOTALL | re.IGNORECASE)


def _git(*args):
    try:
        r = subprocess.run(["git", *args], cwd=str(RAIZ),
                           capture_output=True, text=True, timeout=30)
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return None, ""


class S1SuperficiesGeneradasTest(test_seo.BaseArbol):
    """S1: dadas las páginas armadas con fixtures, «bloque»/«rotación» solo
    sobreviven dentro de valores crudos (calles); title/meta/JSON-LD limpios."""

    def setUp(self):
        test_seo.BaseArbol.setUp(self)
        self.slugs15 = sorted(MOD.slug(n) for n in test_seo.MUNICIPIOS_15)
        self.correr()

    def test_ninguna_superficie_menciona_bloque_o_rotacion(self):
        paginas = ["index.html", "analitica.html", os.path.join("municipios", "index.html")]
        paginas += [os.path.join("municipio", s, "index.html") for s in self.slugs15]
        for rel in paginas:
            html = self._leer(*rel.split(os.sep))
            cuerpo = _sin_valores_crudos(html)
            plano = _plano(cuerpo)
            for veda in ("bloque", "rotaci"):
                self.assertNotIn(veda, plano, "%s menciona «%s»" % (rel, veda))

    def test_la_excepcion_de_valor_crudo_sigue_viva(self):
        # El fixture PG940 trae «...del Bloque» en CALLES: debe seguir pintándose
        # (la veda es del concepto de superficie, no de los datos crudos).
        p = self._leer("municipio", "playa", "index.html")
        self.assertIn("del Bloque", p)  # presente...
        self.assertNotIn("del Bloque", _sin_valores_crudos(p))  # ...solo en .circ-calles

    def test_el_index_no_ofrece_el_json_de_bloques(self):
        idx = self._leer("index.html")
        self.assertNotIn("bloques_por_municipio.json", idx)
        self.assertNotIn("bloques_por_municipio.json", _plano(idx))

    def test_las_metas_de_las_estaticas_hablan_de_circuitos(self):
        for archivo in ("index.html", "analitica.html"):
            desc = MOD.PAGINAS[archivo][2]
            self.assertNotIn("bloque", _plano(desc))
            self.assertNotIn("rotaci", _plano(desc))
            html = (RAIZ / "web" / archivo).read_text(encoding="utf-8")
            meta = re.search(r'<meta name="description" content="(.*?)">', html)
            self.assertIsNotNone(meta, archivo)
            self.assertNotIn("bloque", _plano(meta.group(1)), archivo)
            self.assertNotIn("rotaci", _plano(meta.group(1)), archivo)


class S2ArchivosCommiteadosTest(unittest.TestCase):
    """S2 (hc): git ya no lista el JSON de bloques por municipio ni la demo de
    rotación; el directorio de páginas hijas ni siquiera se commitea."""

    def test_git_ls_files_sin_rastro_de_bloques(self):
        # S2: tras el borrado, todo rastro en el índice debe estar marcado como
        # borrado en el árbol de trabajo (`git ls-files --deleted`), y la demo de
        # rotación nunca fue commiteada. Después del commit ambas listas quedan
        # vacías: la paridad tracked == deleted prueba el estado en ambos momentos.
        rutas = ["web/data/bloques_por_municipio.json", "web/municipio/_demo-n0"]
        self.assertFalse((RAIZ / "web" / "data" / "bloques_por_municipio.json").exists())
        rc1, tracked = _git("ls-files", "--", *rutas)
        rc2, deleted = _git("ls-files", "--deleted", "--", *rutas)
        if rc1 is None or rc2 is None:
            self.skipTest("git no disponible")
        self.assertEqual(tracked.strip(), deleted.strip())


class S3TokensLatentesTest(unittest.TestCase):
    """S3: el código latente del pintado por bloque fue borrado por decisión del
    owner (dormancia revocada): cero tokens en los archivos commiteados."""

    TOKENS_JS = ("MOSTRAR_BLOQUES", "pintarBloques", "bloqueEn",
                 "muestraBloques", "bloquesMun")

    def _scan(self, rel, regexes):
        texto = (RAIZ / rel).read_text(encoding="utf-8")
        for rx in regexes:
            m = re.search(rx, texto)
            self.assertIsNone(m, "%s contiene %r (en %r)" % (rel, rx, texto[m.start():m.start() + 40] if m else ""))

    def test_app_js_sin_tokens_del_pintado(self):
        self._scan("web/app.js", [re.escape(t) for t in self.TOKENS_JS])

    def test_index_html_sin_seccion_bloques(self):
        self._scan("web/index.html", [r'id="bloques"', r"#bloques"])

    def test_style_css_sin_clases_de_bloque(self):
        self._scan("web/style.css", [r"#bloques\b", r"\.bloque\b(?!s)"])


class S6MantencionTest(unittest.TestCase):
    """S6 (KEEP): el plano de datos sigue emitiendo bloques latentes: estado.py
    conserva `bloques{}` y `ventanas.bloque_horas`; los constructores de zonas
    (geozonas con properties.bloque, circuitos con campo bloque) intactos."""

    def test_estado_py_sigume_emitiendo_bloques(self):
        src = (RAIZ / "scripts" / "estado.py").read_text(encoding="utf-8")
        self.assertIn('"bloques": bloques', src)
        self.assertIn('"bloque_horas"', src)
        self.assertIn("POBLACION_HABANA", src)

    def test_los_constructores_geozonas_no_se_pegaron(self):
        rc, out = _git("diff", "--name-only", "HEAD", "--",
                       "scripts/build_barrios.py", "scripts/build_poligonos.py",
                       "scripts/build_lineas.py", "scripts/build_lineas_local.py",
                       "scripts/build_cuadrantes.py", "scripts/build_no_rota.py",
                       "scripts/geocode_zonas.py", "scripts/build_mapa_bloques.py",
                       "scripts/correcciones.py")
        if rc is None:
            self.skipTest("git no disponible")
        self.assertEqual(out.strip(), "", "constructores de zonas modificados")
        for script in ("scripts/build_poligonos.py", "scripts/build_lineas_local.py"):
            src = (RAIZ / script).read_text(encoding="utf-8")
            self.assertIn("bloques.json", src, "%s debe seguir leyendo data/bloques.json" % script)

    def test_build_circuitos_y_extractor_conservan_el_campo_bloque(self):
        for rel in ("scripts/build_circuitos.py", "extractor/extract.py"):
            src = (RAIZ / rel).read_text(encoding="utf-8")
            self.assertIn('"bloque"', src, rel)

    def test_la_capa_azul_protegida_sigue_viva_en_app(self):
        src = (RAIZ / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("const MOSTRAR_PROTEGIDAS = true;", src)
        self.assertIn("Posibles zonas sin apagón", src)


class S5PayloadsMuertosTest(unittest.TestCase):
    """S5 (precondición S4 verificada en main): build_analitica ya no emite los
    payloads por bloque sin consumidores, y conserva los que sí se consumen."""

    def test_build_analitica_sin_payloads_por_bloque(self):
        src = (RAIZ / "scripts" / "build_analitica.py").read_text(encoding="utf-8")
        for veda in ('"parte_horas"', '"horas_sin_dia"', '"patrones"', '"alertas"',
                     "detectar_patrones", "generar_alertas", "RE_BLOQUE_H"):
            self.assertNotIn(veda, src)
        # lo vivo del join por circuito sigue presente:
        self.assertIn('"averias"', src)
        self.assertIn('"circuitos_partes"', src)


class S7BotsYCausaTest(unittest.TestCase):
    """S7 (veto del owner, hc): los archivos del bot no se tocan; la causa DAF y
    el texto verbatim de los partes siguen renderizando."""

    def test_archivos_del_bot_inalterados(self):
        rc, out = _git("diff", "--name-only", "HEAD", "--",
                       "workers/bot-worker.js", "web/_worker.js")
        if rc is None:
            self.skipTest("git no disponible")
        self.assertEqual(out.strip(), "", "los archivos del bot cambiaron en esta rama")

    def test_worker_sigue_ordenando_no_hablar_de_bloques(self):
        src = (RAIZ / "web" / "_worker.js").read_text(encoding="utf-8")
        self.assertIn("nunca hables de ellos", src)


class S7CausaVivaTest(test_seo.BaseArbol):
    """S7: la causa «DAF» (circuito A1443 del fixture) sigue renderizando en la
    página después de quitar el chip de bloque — es valor de dato, no concepto."""

    def setUp(self):
        test_seo.BaseArbol.setUp(self)
        self.correr()

    def test_causa_daf_sigue_pintandose_en_la_pagina(self):
        p = self._leer("municipio", "playa", "index.html")
        self.assertIn("Causa: DAF", p)


if __name__ == "__main__":
    unittest.main()
