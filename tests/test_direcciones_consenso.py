"""Tests de la dirección de circuito por CONSENSO corroborado del canal.

Cubre las funciones puras de scripts/build_circuitos.py:
  1. _peso_recencia: la mención reciente pesa más que la vieja.
  2. _racimos: agrupa variantes de escritura y no mezcla zonas distintas.
  3. _mejor_ajena: la dirección de otro circuito que mejor solapa.
  4. resolver_direcciones: histéresis (el error suelto no vuelve permanente),
     cambio real sostenido, empate disputado, mención compartida y cruce.
  5. replay_canal: recolección de votos y marca de mención compartida.

Sin red: cat/votos/previos/oficial se arman a mano.
"""

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).parents[1] / "extractor"))

RUTA = SCRIPTS / "build_circuitos.py"
SPEC = importlib.util.spec_from_file_location("build_circuitos", RUTA)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

# Horizonte fijo del build: todos los pesos de recencia se miden contra él.
GEN = datetime(2026, 8, 31, tzinfo=timezone.utc)


def _voto(texto, dias_atras=0, compartida=False):
    """Un voto con fecha relativa al horizonte (0 = el mismo día)."""
    return {"fecha": (GEN - timedelta(days=dias_atras)).isoformat(),
            "texto": texto, "compartida": compartida}


def _resolver(cod, votos, previos=None, oficial=None, calles=None):
    """Corre resolver_direcciones sobre un catálogo de UN código."""
    previos = previos or {}
    cat = {cod: {"calles": calles if calles is not None else previos.get(cod, "")}}
    rev = MOD.resolver_direcciones(cat, {cod: votos}, previos, GEN, oficial or {})
    return cat[cod], rev


class TestPesoRecencia(unittest.TestCase):
    """Peso por antigüedad: 0.5 ** (días / vida media)."""

    def test_reciente_pesa_mas_que_vieja(self):
        reciente = MOD._peso_recencia(_voto("x", 0)["fecha"], GEN)
        vieja = MOD._peso_recencia(_voto("x", 90)["fecha"], GEN)
        self.assertGreater(reciente, vieja)
        self.assertAlmostEqual(reciente, 1.0)

    def test_sin_generado_cuenta_reciente(self):
        self.assertEqual(MOD._peso_recencia("2020-01-01T00:00:00+00:00", None), 1.0)

    def test_fecha_irrecuperable_cuenta_reciente(self):
        self.assertEqual(MOD._peso_recencia("no-es-fecha", GEN), 1.0)


class TestRacimos(unittest.TestCase):
    """Agrupado greedy de variantes de escritura del mismo lugar."""

    def test_agrupa_variantes(self):
        votos = [_voto("Zonas: 13; 15 y Micro X", 10),
                 _voto("ZONAS: 13; 15 Y MICRO X", 5),
                 _voto("Micro X, zonas 13 y 15", 1)]
        racs = MOD._racimos(votos, GEN)
        self.assertEqual(len(racs), 1)
        self.assertEqual(racs[0]["n"], 3)

    def test_no_mezcla_zonas_distintas(self):
        racs = MOD._racimos([_voto("Reparto Alfa", 5),
                             _voto("Reparto Beta", 5)], GEN)
        self.assertEqual(len(racs), 2)

    def test_ordenado_por_peso(self):
        votos = [_voto("Reparto Alfa", 60), _voto("Reparto Beta", 0)]
        racs = MOD._racimos(votos, GEN)
        self.assertEqual(racs[0]["texto"], "Reparto Beta")


class TestMejorAjena(unittest.TestCase):
    """La dirección de otro circuito que mejor solapa (excluye el propio)."""

    def test_ignora_el_propio_codigo(self):
        refs = {"D1125": "Reparto Guiteras- Párraga", "PG930": "Averoff, El Retiro"}
        cod, cob = MOD._mejor_ajena("Reparto Guiteras- Párraga", "D1125", refs)
        self.assertIsNone(cod)
        self.assertEqual(cob, 0.0)

    def test_detecta_la_ajena(self):
        refs = {"PG930": "Averoff, El Retiro, El Calvario"}
        cod, cob = MOD._mejor_ajena("Averoff, El Retiro, El Calvario", "D1125", refs)
        self.assertEqual(cod, "PG930")
        self.assertGreaterEqual(cob, 0.7)


class TestResolverConsenso(unittest.TestCase):
    """La dirección se decide por consenso, con histéresis y detector de cruce."""

    CORRECTO = "Zonas: 13; 15; 16; 17; 18; 21 y Micro X"
    CRUZADO = "Zonas: 1, 2, 3, 5, 24, 8, 7"

    def test_error_suelto_no_vuelve_permanente(self):
        # 60 votos correctos repartidos en 2 meses + 2 cruzados del último día.
        votos = [_voto(self.CORRECTO, d) for d in range(2, 62)]
        votos += [_voto(self.CRUZADO, 0), _voto(self.CRUZADO, 0)]
        r, rev = _resolver("AL56", votos, previos={"AL56": self.CORRECTO})
        self.assertEqual(r["calles"], self.CORRECTO)
        # Si se marca, tiene que ser por cruce; nunca cambia el texto publicado.
        for e in rev:
            self.assertEqual(e["motivo"], "cruzada")

    def test_arranque_en_frio_elige_mayoria(self):
        votos = [_voto(self.CORRECTO, d) for d in range(1, 31)]
        votos += [_voto(self.CRUZADO, 0), _voto(self.CRUZADO, 0)]
        r, _ = _resolver("AL56", votos, previos={})
        self.assertEqual(r["calles"], self.CORRECTO)

    def test_cambio_real_sostenido_gana(self):
        nuevo = "Calle L desde Línea hasta Malecón"
        viejo = "Soterrado Vedado 2 Hotel Nacional"
        votos = [_voto(nuevo, d) for d in range(0, 25)]
        votos += [_voto(viejo, 70), _voto(viejo, 75)]
        r, rev = _resolver("X", votos, previos={"X": viejo})
        self.assertEqual(r["calles"], nuevo)
        self.assertNotIn("direccion_en_revision", r)
        self.assertEqual(rev, [])

    def test_empate_disputado_conserva_vigente(self):
        alfa, beta = "Reparto Alfa", "Reparto Beta"
        # ~45% / ~45%: ni uno llega al 60%; el racimo top es Beta (primero).
        votos = [_voto(beta, 5)] * 45 + [_voto(alfa, 5)] * 44
        r, rev = _resolver("Y", votos, previos={"Y": alfa})
        self.assertEqual(r["calles"], alfa)
        self.assertEqual(r["direccion_en_revision"], "disputada")
        self.assertEqual(len(rev), 1)
        self.assertEqual(rev[0]["publicada"], alfa)

    def test_disputa_restaura_la_vigente(self):
        # El replay pudo dejar en `calles` una última mención (p. ej. el texto
        # cruzado): sin candidato propio, la vigente tiene que volver.
        alfa, beta = "Reparto Alfa", "Reparto Beta"
        votos = [_voto(beta, 5)] * 45 + [_voto(alfa, 5)] * 44
        r, _ = _resolver("Y", votos, previos={"Y": alfa},
                         calles="ultima mencion cruzada")
        self.assertEqual(r["calles"], alfa)
        self.assertEqual(r["direccion_en_revision"], "disputada")

    def test_mencion_compartida_no_vota(self):
        a, b = "Zonas: 1, 2, 3", "Soterrado Vedado, Calle 23"
        votos = [_voto(a, 1, compartida=True) for _ in range(40)]
        votos += [_voto(b, 2) for _ in range(3)]
        r, _ = _resolver("Z", votos, previos={})
        self.assertEqual(r["calles"], b)

    def test_cruce_no_adopta_direccion_ajena(self):
        guiteras = "Reparto Guiteras- Párraga"
        averoff = "Averoff, El Retiro, El Calvario, María Antonia, 13 de Agosto"
        previos = {"D1125": guiteras, "PG930": averoff}
        votos = [_voto(averoff, 1) for _ in range(40)]
        votos += [_voto(guiteras, 40) for _ in range(25)]
        r, rev = _resolver("D1125", votos, previos=previos)
        self.assertEqual(r["calles"], guiteras)
        self.assertEqual(r["direccion_en_revision"], "cruzada")
        self.assertEqual(r["direccion_de"], "PG930")
        self.assertEqual(rev[0]["motivo"], "cruzada")
        self.assertEqual(rev[0]["de"], "PG930")


class TestReplayVotos(unittest.TestCase):
    """replay_canal acumula votos y marca la mención compartida."""

    def test_voto_regex_simple(self):
        votos = {}
        MOD.replay_canal(
            [{"message_id": 1, "fecha": "2026-07-05T14:00:00+00:00",
              "texto": "🔻 Afectación\n👉 AL53: Zona 24, Edf 3"}],
            {}, set(), {}, votos)
        self.assertIn("AL53", votos)
        self.assertFalse(votos["AL53"][0]["compartida"])
        self.assertEqual(votos["AL53"][0]["texto"], "Zona 24, Edf 3")

    def test_voto_regex_multicodigo_es_compartido(self):
        votos = {}
        MOD.replay_canal(
            [{"message_id": 2, "fecha": "2026-07-05T14:00:00+00:00",
              "texto": "🔻 Afectación\n👉 AL53, AL56: Zonas: 1, 2, 3"}],
            {}, set(), {}, votos)
        self.assertTrue(votos["AL53"][0]["compartida"])
        self.assertTrue(votos["AL56"][0]["compartida"])

    def test_voto_llm_degenerado_no_vota(self):
        votos = {}
        llm = {"3": {"via": "llm", "validador_version": 2,
                     "circuitos": [{"codigos": ["AL53"],
                                    "codigos_estado": ["AL53"],
                                    "estado": "sin servicio",
                                    "calles": "uda uda uda uda uda uda"}]}}
        MOD.replay_canal(
            [{"message_id": 3, "fecha": "2026-07-05T14:00:00+00:00",
              "texto": "parte sin viñeta"}],
            {}, set(), llm, votos)
        self.assertEqual(votos.get("AL53", []), [])


if __name__ == "__main__":
    unittest.main()
