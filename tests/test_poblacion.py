"""S11 (fuente única) del delta spec municipios-info: la tabla de población por
municipio vive en scripts/estado.py, se emite SIEMPRE en estado.json como
`poblacion_municipio`, y el header de la portada (web/app.js) la prefiere sobre
la constante legada (que queda solo como respaldo para JSON commiteado viejo).

Pruebas de forma sobre las FUENTES (AST/regex, hermet): sin tablas duplicadas
con números divergentes — un solo lugar donde se corrige un censo.
"""

import ast
import os
import re
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Verdad de prueba (una sola vez aquí): censo municipal ~1.75M por municipio.
TABLA_ESPERADA = {
    "Playa": 142245, "Plaza": 104629, "Centro Habana": 105713, "Habana Vieja": 64104,
    "Regla": 36181, "Habana del Este": 141392, "Guanabacoa": 109066,
    "San Miguel del Padrón": 134978, "10 de Octubre": 158569, "Cerro": 101381,
    "Marianao": 111744, "La Lisa": 126593, "Boyeros": 170577,
    "Arroyo Naranjo": 174298, "Cotorro": 68494,
}
TOTAL_ESPERADO = 1749964


def _asignaciones(path):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src, filename=path)
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = node
    return src, out


class TablaEnEstadoTest(unittest.TestCase):
    def setUp(self):
        self.src, self.asigs = _asignaciones(os.path.join(RAIZ, "scripts", "estado.py"))

    def test_estado_py_define_poblacion_municipio(self):
        node = self.asigs.get("POBLACION_MUNICIPIO")
        self.assertIsNotNone(node, "estado.py debe ser la fuente de la tabla")
        tabla = ast.literal_eval(node.value)
        self.assertEqual(tabla, TABLA_ESPERADA)

    def test_poblacion_habana_derivada_de_la_tabla(self):
        # La verdad del comentario (POBLACION_HABANA = suma exacta de la tabla)
        # pasa a ser DERIVACIÓN, no un número que puede divergir de ella.
        node = self.asigs.get("POBLACION_HABANA")
        self.assertIsNotNone(node)
        lineas = self.src.splitlines()
        rhs = lineas[node.value.lineno - 1][node.value.col_offset:]
        self.assertRegex(rhs.replace(" ", ""), r"^sum\(POBLACION_MUNICIPIO\.values\(\)\)")
        self.assertEqual(sum(TABLA_ESPERADA.values()), TOTAL_ESPERADO)

    def test_estado_json_emite_poblacion_municipio(self):
        # La emisión es SIEMPRE presente (no depende de ningún parte oficial):
        # la clave sale del diccionario de salida de main() literal.
        self.assertIn('"poblacion_municipio"', self.src)


class AppJsPreferenteTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(RAIZ, "web", "app.js"), encoding="utf-8") as f:
            self.src = f.read()

    def _tabla_js(self):
        m = re.search(r"const POB_MUNI = \{(.*?)\};", self.src, re.DOTALL)
        self.assertIsNotNone(m, "POB_MUNI debe quedar como respaldo declarado")
        body = m.group(1)
        return {k: int(v.replace("_", "")) for k, v in
                re.findall(r'"([^"]+)"\s*:\s*([\d_]+)', body)}

    def test_la_constante_legada_es_respaldo_y_no_bifurca_numeros(self):
        # Fallback deliberado (tasks U-B): se conserva esta ronda para JSON
        # commiteado viejo; la guarda es que sus valores sean IGUALES a la fuente.
        self.assertEqual(self._tabla_js(), TABLA_ESPERADA)

    def test_el_header_prefiere_el_json_de_estado(self):
        self.assertIn("estado.poblacion_municipio", self.src)
        # y el estimado del header usa la tabla efectiva, no la constante fija:
        m = re.search(r"estado\.poblacion_municipio[^;]*POB_MUNI", self.src)
        self.assertIsNotNone(m, "falta la preferencia estado.poblacion_municipio || POB_MUNI")


class FixtureParidadTest(unittest.TestCase):
    def test_mini_estado_trae_la_tabla_como_la_emite_productor(self):
        import json
        _, asigs = _asignaciones(os.path.join(RAIZ, "scripts", "estado.py"))
        fuente = ast.literal_eval(asigs["POBLACION_MUNICIPIO"].value)
        with open(os.path.join(RAIZ, "tests", "fixtures", "mini_estado.json"),
                  encoding="utf-8") as f:
            fixture = json.load(f)
        self.assertEqual(fixture.get("poblacion_municipio"), fuente,
                         "el fixture debe reflejar la emisión actual de estado.py")


if __name__ == "__main__":
    unittest.main()
