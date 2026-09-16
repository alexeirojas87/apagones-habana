"""La tabla de alias MANUALES de circuitos (correcciones.json, sección
circuitos_alias): la UNE a veces publica el código truncado sin el prefijo de
subestación ('L53' por AL53) y esos one-offs nunca llegan a los >= 3 posts que
exige el aprendiz, así que se verifican a mano y canonico()/es_conocido()
los aplican con precedencia sobre los alias aprendidos.

Dos mundos: el repo REAL (los 7 alias confirmados con evidencia de mensaje)
y un correcciones.json tmp para precedencia y defensas (patrón
ConocidoWiringTest de test_aprende_circuitos.py).
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import circuitos_id as ci  # noqa: E402
import correcciones  # noqa: E402  (el mismo módulo que _alias_manual importa)


class AliasManualesRepoTest(unittest.TestCase):
    """Con el correcciones.json REAL del repo: los 7 códigos truncados
    verificados a mano resuelven a su canónico y cuentan como conocidos."""

    def setUp(self):
        self._cat = ci._CATALOGO
        self._ap = ci._APRENDIDOS
        self._am = ci._ALIAS_MANUAL
        ci.recargar()  # lee el par de archivos reales del repo
        self.addCleanup(setattr, ci, "_CATALOGO", self._cat)
        self.addCleanup(setattr, ci, "_APRENDIDOS", self._ap)
        self.addCleanup(setattr, ci, "_ALIAS_MANUAL", self._am)
        self.addCleanup(ci.recargar)

    def test_los_7_truncados_resuelven_y_son_conocidos(self):
        esperado = {"L53": "AL53", "L55": "AL55", "1565": "A1565",
                    "SR86": "SR860", "D110": "D1100", "OP31": "OP312",
                    "1430": "A1430"}
        for truncado, canon in esperado.items():
            self.assertEqual(ci.canonico(truncado), canon, truncado)
            self.assertTrue(ci.es_conocido(truncado),
                            f"{truncado} debe resolver al conocido {canon}")

    def test_alias_aprendido_sigue_funcionando(self):
        """La tabla manual no estorba al aprendiz: '581' -> SF581 se mantiene."""
        self.assertEqual(ci.canonico("581"), "SF581")
        self.assertTrue(ci.es_conocido("581"))

    def test_passthrough_de_canonicos_y_desconocidos(self):
        """Un canónico o un código que no es alias se devuelve tal cual."""
        self.assertEqual(ci.canonico("AL53"), "AL53")
        self.assertEqual(ci.canonico("NOPE99"), "NOPE99")


class PrecedenciaYDefensasTest(unittest.TestCase):
    """Con correcciones.json tmp (se parchea _RUTA y se recarga la caché):
    el alias manual manda sobre el aprendido, la entrada sin alias_de se
    ignora y recargar() refresca la tabla manual."""

    def setUp(self):
        self._cat = ci._CATALOGO
        self._ap = ci._APRENDIDOS
        self._am = ci._ALIAS_MANUAL
        self._ruta_original = correcciones._RUTA
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ruta = os.path.join(self.tmp.name, "correcciones.json")
        self.addCleanup(setattr, correcciones, "_RUTA", self._ruta_original)
        self.addCleanup(setattr, ci, "_CATALOGO", self._cat)
        self.addCleanup(setattr, ci, "_APRENDIDOS", self._ap)
        self.addCleanup(setattr, ci, "_ALIAS_MANUAL", self._am)
        self.addCleanup(ci.recargar)

    def _escribir(self, alias):
        """Escribe la sección tmp y repunta la ruta con la caché recargada."""
        json.dump({"circuitos_alias": alias}, open(self.ruta, "w"),
                  ensure_ascii=False)
        correcciones._RUTA = self.ruta
        ci.recargar()  # fuerza la re-lectura bajo el tmp

    def test_manual_gana_sobre_aprendido(self):
        self.assertEqual(ci.canonico("581"), "SF581")  # aprendido, mundo real
        self._escribir({"581": {"alias_de": "XX581"}})
        self.assertEqual(ci.canonico("581"), "XX581")  # el manual precede

    def test_entrada_sin_alias_de_se_ignora(self):
        """Un registro sin 'alias_de' no se convierte en alias fantasma."""
        self._escribir({"MAL": {"motivo": "sin alias_de"}})
        self.assertEqual(ci.canonico("MAL"), "MAL")

    def test_recargar_refresca_la_tabla_manual(self):
        self._escribir({"581": {"alias_de": "XX581"}})
        self.assertEqual(ci.canonico("581"), "XX581")
        json.dump({"circuitos_alias": {"581": {"alias_de": "YY581"}}},
                  open(self.ruta, "w"), ensure_ascii=False)
        self.assertEqual(ci.canonico("581"), "XX581")  # la caché sigue vigente
        ci.recargar()
        self.assertEqual(ci.canonico("581"), "YY581")  # recargada desde el disco


if __name__ == "__main__":
    unittest.main()
