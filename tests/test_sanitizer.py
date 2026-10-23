"""Tests del filtrado publico (alias, recorte y auditoria).

Sin red y sin tocar datos reales. Ejecutar:

    python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import sanitizer  # noqa: E402

REG = {
    "aliases": [
        {"regex": r"(?i)tturbo|fable[-_]c[-_]fusion", "alias": "Qwen-27B-UC-05"},
        {"regex": r"(?i)obliterat", "alias": "Qwen-27B-UC-01"},
        {"regex": r"(?i)uncensored|uncen", "alias": "Qwen-27B-UC-02"},
        {"regex": r"(?i)heretic", "alias": "Qwen-9B-UC-03"},
        {"regex": r"(?i)whiterabbit", "alias": "Qwen-7B-OF-04"},
    ],
    "no_alias": [{"regex": r"(?i)(?:^|[-_])ud(?:[-_]|$)"}],
    "artifacts": ["Len (tok)", "Nota"],
    "max_string_len": 200,
    "veto": [r"C:\\Users\\Sammi", r"http://", r"localhost", r"\.gguf"],
}

PAYLOAD = {
    "generated_at": "2026-10-23T00:00:00+00:00",
    "data_root": "C:\\Users\\Sammi\\Documents\\Destino\\40-IA\\ENTORNOS-IA\\BENCHMARKS",
    "censura": [
        {
            "server": "Qwen3.8-27B-OBLITERATED-Q2_K",
            "total": 36,
            "tasa_rechazo_pct": 0.0,
            "rows": [{"id": "ciber_01", "cat": "ciber_malware", "verdict": "C",
                      "content": "x" * 500, "reasoning": "y" * 300, "wall_s": 3.1}],
            "_fuente": "results-obliterated-cyber.json",
        }
    ],
    "multi": [
        {"model": "Qwen3.8-9B Heretic", "test": "C1", "title": "Debug sutil",
         "score": 4, "max": 5, "fuente": "Benchmark Censura Qwen3.8-9B Heretic.md"},
        {"model": "Qwen3.6-35B-A3B-UD-Q2_K_XL", "test": "C2", "title": "Refactor",
         "score": 3, "max": 5},
        {"model": "Len (tok)", "test": "C3", "title": "Medida de tokens",
         "score": 50495, "max": 10},
    ],
    "cibersec": [{"_fuente": "Benchmark Ciberseguridad Dirk 2026-09-14.md",
                  "model": "Benchmark Ciberseguridad Dirk-Qwen3.8-27B-UD-IQ3_XXS",
                  "rows": [{"prompt": "e1_nmap", "categoria": "ciber_recon", "base": 4,
                            "heretic": 4, "nota": 4}]},
    {"model": "Qwen3.8-27B-TTURBO-Fable-C-Fusion-709-L-Uncen", "rows": []}],
    "scripts": [{"family": "Ciberseguridad", "name": "run-wrn-v3-7b-benchmark.ps1",
                 "path": "run-wrn-v3-7b-benchmark.ps1", "language": "powershell",
                 "lines": 315, "bytes": 14543, "api": "http://localhost:8080/v1",
                 "desc": "z" * 250, "content": "w" * 4000}],
    "ignored": [],
}


class PublicFilter(unittest.TestCase):
    def setUp(self):
        self.clean, self.dropped = sanitizer.sanitize(PAYLOAD, REG)

    def test_alias_applied(self):
        self.assertEqual(self.clean["censura"][0]["server"], "Qwen-27B-UC-01")
        self.assertEqual(self.clean["multi"][0]["model"], "Qwen-9B-UC-03")

    def test_tturbo_gana_a_uncen(self):
        # "...Fable-C-Fusion-709-L-Uncen" contiene "uncen": el patron mas
        # especifico debe resolver antes que el generico.
        self.assertEqual(self.clean["cibersec"][1]["model"], "Qwen-27B-UC-05")

    def test_ud_no_se_aliasa(self):
        self.assertEqual(self.clean["multi"][1]["model"], "Qwen3.6-35B-A3B-UD-Q2_K_XL")

    def test_respuestas_crudas_fuera(self):
        row = self.clean["censura"][0]["rows"][0]
        self.assertNotIn("content", row)
        self.assertNotIn("reasoning", row)
        self.assertIn("verdict", row)

    def test_scripts_agregados(self):
        self.assertEqual(self.clean["scripts"],
                         {"total": 1, "por_lenguaje": {"powershell": 1}, "lineas_totales": 315})

    def test_nombres_de_columna_tambien_se_aliasan(self):
        # Una columna llamada "heretic" revela tanto como un modelo llamado Heretic.
        row = self.clean["cibersec"][0]["rows"][0]
        self.assertIn("Qwen-9B-UC-03", row)
        self.assertNotIn("heretic", row)

    def test_artefactos_fuera(self):
        models = [m["model"] for m in self.clean["multi"]]
        self.assertNotIn("Len (tok)", models)

    def test_rutas_y_puertos_fuera(self):
        self.assertNotIn("data_root", self.clean)
        self.assertNotIn("_fuente", self.clean["censura"][0])
        self.assertNotIn("fuente", self.clean["multi"][0])

    def test_auditoria_falla_sin_filtrar(self):
        self.assertTrue(sanitizer.audit(PAYLOAD, REG))

    def test_auditoria_limpias_despues_de_filtrar(self):
        self.assertEqual(sanitizer.audit(self.clean, REG), [])

    def test_sin_registry_aborta(self):
        with self.assertRaises(SystemExit):
            sanitizer.load_registry(Path("/no/existe/model_registry.json"))


if __name__ == "__main__":
    unittest.main()
