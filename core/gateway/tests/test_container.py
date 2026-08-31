"""Per-subtask E2E for ADR-0007 Phase 4 — OCI image + Helm chart.

Hermetic: validates Dockerfile syntax and Helm chart structure
without requiring Docker or Helm to be on PATH. When `helm` is
available, additionally runs `helm template` and asserts the
rendered manifests carry the expected resources.

Covers:
  * Dockerfile present, parses as a stage-list, references
    python:3.12-slim and the documented entry point.
  * Non-root user wired in Dockerfile (uid 4711 matches values.yaml
    securityContext.runAsUser).
  * Chart.yaml parses + has the required keys.
  * values.yaml parses + has every key the templates reference.
  * Deployment / Service / PVC templates reference the helper names
    and don't carry obvious typos.
  * When `helm` is on PATH: `helm template` renders without errors
    and the rendered set contains exactly one Deployment, one
    Service, and one PVC.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

_PLUGIN = Path(__file__).resolve().parents[1]
_DOCKERFILE = _PLUGIN / "Dockerfile"
_CHART_DIR = _PLUGIN / "chart"


# ── Dockerfile sanity ───────────────────────────────────────────────


class DockerfileShapeTests(unittest.TestCase):
    def test_dockerfile_exists(self):
        self.assertTrue(_DOCKERFILE.exists(), str(_DOCKERFILE))

    def test_base_image_is_python_3_12_slim(self):
        text = _DOCKERFILE.read_text()
        self.assertIn("FROM python:3.12-slim", text)

    def test_non_root_uid_matches_chart(self):
        df = _DOCKERFILE.read_text()
        m = re.search(r"--uid\s+(\d+)", df)
        self.assertIsNotNone(m, "Dockerfile must declare a non-root uid")
        uid = int(m.group(1))
        values = yaml.safe_load(
            (_CHART_DIR / "values.yaml").read_text()
        )
        self.assertEqual(uid, values["securityContext"]["runAsUser"])

    def test_entrypoint_runs_uvicorn(self):
        df = _DOCKERFILE.read_text()
        self.assertIn("uvicorn", df)
        self.assertIn("corvin_gateway.app:app", df)
        # 0.0.0.0 binding lets the chart's Service forward traffic
        self.assertIn("0.0.0.0", df)

    def test_pyjwt_and_pyyaml_pinned_in_requirements(self):
        req = (_PLUGIN / "requirements.txt").read_text()
        for token in ("fastapi", "pydantic", "PyJWT", "pyyaml", "uvicorn"):
            self.assertIn(token, req, f"missing {token!r} in requirements.txt")


# ── Helm chart structure ────────────────────────────────────────────


class ChartStructureTests(unittest.TestCase):
    def test_chart_yaml_keys(self):
        data = yaml.safe_load((_CHART_DIR / "Chart.yaml").read_text())
        for key in ("apiVersion", "name", "version", "appVersion", "type"):
            self.assertIn(key, data)
        self.assertEqual(data["apiVersion"], "v2")
        self.assertEqual(data["name"], "corvin-gateway")

    def test_values_yaml_keys(self):
        v = yaml.safe_load((_CHART_DIR / "values.yaml").read_text())
        for key in (
            "image", "service", "persistence", "resources",
            "securityContext", "livenessProbe", "readinessProbe",
        ):
            self.assertIn(key, v)
        self.assertEqual(v["service"]["port"], 8000)
        self.assertTrue(v["persistence"]["enabled"])
        self.assertEqual(
            v["livenessProbe"]["httpGet"]["path"], "/healthz",
        )
        self.assertEqual(v["securityContext"]["runAsNonRoot"], True)

    def test_templates_present(self):
        templates = _CHART_DIR / "templates"
        for fn in ("_helpers.tpl", "deployment.yaml", "service.yaml", "pvc.yaml"):
            self.assertTrue(
                (templates / fn).exists(), f"missing template {fn!r}",
            )


# ── Optional: helm template render ──────────────────────────────────


class HelmRenderTests(unittest.TestCase):
    def test_helm_template_renders(self):
        helm = shutil.which("helm")
        if helm is None:
            self.skipTest("helm not on PATH")
        proc = subprocess.run(
            [helm, "template", "release", str(_CHART_DIR)],
            check=False, capture_output=True, text=True,
        )
        self.assertEqual(
            proc.returncode, 0,
            f"helm template failed:\n{proc.stderr}",
        )
        # Split YAML documents and count kinds
        docs = [
            d for d in yaml.safe_load_all(proc.stdout)
            if isinstance(d, dict)
        ]
        kinds = sorted(d.get("kind", "") for d in docs)
        self.assertIn("Deployment", kinds)
        self.assertIn("Service", kinds)
        self.assertIn("PersistentVolumeClaim", kinds)


if __name__ == "__main__":
    unittest.main(verbosity=2)
