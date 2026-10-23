"""Tests del parser (contratos de formato del dashboard).

Se ejecutan sin red y sin tocar los datos reales: cada test monta una raiz de datos
temporal. Ejecutar:

    python -m unittest discover -s tests -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import loader  # noqa: E402


class RootCase(unittest.TestCase):
    """Base: crea una raiz de datos temporal y la restaura al terminar."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._orig_root = loader.ROOT
        loader.ROOT = self.root
        loader.IGNORED.clear()

    def tearDown(self):
        loader.ROOT = self._orig_root
        loader.IGNORED.clear()
        self._tmp.cleanup()

    def write(self, name, text):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p


class TestMultiAmbito(RootCase):
    def test_inline_scores(self):
        self.write("Benchmark Modelo-A 2026-09-01.md", (
            "# Benchmark Modelo-A\n\n"
            "### C1 — Debug sutil **(5/5)**\n"
            "### R2 — Calculo **(3/5)**\n"
            "### T1 — Resumen **(4/5)**\n"
        ))
        rows = loader.load_multiambito()
        self.assertEqual(len(rows), 3)
        c1 = next(r for r in rows if r["test"] == "C1")
        self.assertEqual((c1["score"], c1["max"], c1["domain"]), (5, 5, "Código"))
        self.assertEqual(c1["model"], "Modelo-A")
        self.assertEqual(c1["fecha"], "2026-09-01")
        self.assertEqual(c1["source_tag"], "individual")

    def test_comparative_table(self):
        self.write("Benchmark 4 Modelos 2026-09-02.md", (
            "| Prueba | Titulo | Base | Heretic |\n"
            "|:---:|---|:---:|:---:|\n"
            "| C1 | Debug | 5/5 | 4/5 |\n"
            "| A2 | Avanzada | 2/5 | 5/5 |\n"
            "| Total | | 7 | 9 |\n"
        ))
        rows = loader.load_multiambito()
        self.assertEqual(len(rows), 4)
        models = {r["model"] for r in rows}
        self.assertEqual(models, {"Base", "Heretic"})
        self.assertTrue(all(r["source_tag"] == "directo" for r in rows))

    def test_single_pts_table(self):
        self.write("Benchmark Modelo-B 2026-09-03.md", (
            "| Prueba | Titulo | Pts |\n"
            "|---|---|:---:|\n"
            "| C1 | Debug | 4 |\n"
        ))
        rows = loader.load_multiambito()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["score"], rows[0]["max"]), (4, 5))

    def test_unparseable_file_is_reported_not_silently_skipped(self):
        self.write("Benchmark Modelo-C 2026-09-04.md", "Solo texto, sin puntuaciones.")
        rows = loader.load_multiambito()
        self.assertEqual(rows, [])
        self.assertEqual(len(loader.IGNORED), 1)
        self.assertIn("Benchmark Modelo-C 2026-09-04.md", loader.IGNORED[0][0])

    def test_censura_and_cibersec_files_are_excluded(self):
        self.write("Benchmark Censura Modelo-D 2026-09-05.md", "### C1 — x **(1/5)**")
        self.write("Benchmark Ciberseguridad Modelo-D 2026-09-05.md", "### C1 — x **(1/5)**")
        self.assertEqual(loader.load_multiambito(), [])


class TestCensura(RootCase):
    def test_valid_json_is_loaded(self):
        self.write("results-modelo-censura.json", json.dumps({
            "server": "modelo", "total": 42, "rechazos_R": 5, "cumplimiento_C": 30,
            "evasivo_E": 7, "errores": 0, "tasa_rechazo_pct": 11.9,
            "tasa_compliance_pct": 71.4,
            "por_categoria": {"code": {"n": 10, "R": 1, "C": 8, "E": 1}},
        }))
        out = loader.load_censura()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["server"], "modelo")

    def test_json_without_tasa_is_ignored(self):
        self.write("results-malo.json", json.dumps({"server": "x", "total": 3}))
        self.write("results-bueno-censura.json", json.dumps({"tasa_rechazo_pct": 1.0}))
        out = loader.load_censura()
        self.assertEqual(len(out), 1)
        self.assertEqual(len(loader.IGNORED), 1)

    def test_broken_json_is_ignored(self):
        self.write("results-roto-censura.json", "{no es json")
        self.assertEqual(loader.load_censura(), [])
        self.assertEqual(len(loader.IGNORED), 1)


class TestCibersec(RootCase):
    def test_individual_table(self):
        self.write("Benchmark Ciberseguridad Modelo-E 2026-09-06.md", (
            "| Prompt | Categoria | Nota | Refusal |\n"
            "|---|---|:---:|:---:|\n"
            "| n2_wpa2 | wifi | 4 | C |\n"
            "| bad-id | red | 5 | C |\n"
        ))
        out = loader.load_cibersec()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["comparative"], False)
        self.assertEqual(len(out[0]["rows"]), 1)
        self.assertEqual(out[0]["rows"][0]["prompt"], "n2_wpa2")

    def test_comparative_table(self):
        self.write("Benchmark Ciberseguridad Base-Heretic 2026-09-07.md", (
            "| Prompt | Categoria | Base | Heretic |\n"
            "|---|---|:---:|:---:|\n"
            "| n1_sqli | web | 2 | 5 |\n"
        ))
        out = loader.load_cibersec()
        self.assertEqual(out[0]["comparative"], True)
        self.assertEqual(out[0]["rows"][0]["heretic"], 5)


class TestSubagentes(RootCase):
    def test_index_json(self):
        self.write("subagentes-2026-08-08/results/index.json", json.dumps({
            "runs": [{"agent": "a", "model": "m", "test": "C1", "exitCode": 0,
                      "durationSec": 12, "stopReason": "end"}]
        }))
        out = loader.load_subagentes()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["benchmark"], "subagentes-2026-08-08")


class TestScripts(RootCase):
    def test_ps1_metadata(self):
        self.write("run-cybersec-bench.ps1", (
            "# Catalogo de pruebas de ciberseguridad\n"
            '$API = "http://127.0.0.1:8120/v1/chat/completions"\n'
            '$MODEL = "qwen-3.8-9b"\n'
        ))
        out = loader.load_scripts()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["family"], "Ciberseguridad")
        self.assertEqual(out[0]["model"], "qwen-3.8-9b")
        self.assertEqual(out[0]["language"], "powershell")


if __name__ == "__main__":
    unittest.main()
