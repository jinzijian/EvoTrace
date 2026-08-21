import io
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from evotrace.builder import BuildResult
from evotrace.catalog import record_run
from evotrace.cli import main
from evotrace.store import Store
from evotrace.util import write_json, write_jsonl


class CatalogCliTests(unittest.TestCase):
    def make_store(self, root: Path, *, sensitive: bool = False) -> tuple[Store, str]:
        store = Store(root / "store")
        store.initialize()
        session_id = "codex-cli-example"
        session = store.sessions / session_id
        session.mkdir()
        write_json(
            session / "trajectory.json",
            {
                "schema_version": "0.1",
                "session_id": session_id,
                "created_at": "2026-01-01T00:00:00Z",
                "source": {
                    "kind": "history_import",
                    "agent": "codex",
                    "path": "/private/history.jsonl",
                },
                "task": {"text": "Fix the payment retry race without breaking clients."},
                "repository": {
                    "root": "/private/payment-service",
                    "origin": "https://github.com/example/payment-service.git",
                    "base_commit": "abc123",
                    "reconstruction_confidence": "high",
                },
                "verification": {"commands": ["python -m pytest -q"]},
                "outcome": {"status": "imported"},
            },
        )
        write_jsonl(
            session / "events.jsonl",
            [{"kind": "message.user", "data": {"text": "Fix the payment retry race."}}],
        )
        write_json(
            store.candidates / f"{session_id}.json",
            {
                "schema_version": "0.1",
                "session_id": session_id,
                "source": "codex",
                "score": 9,
                "labels": [
                    "useful",
                    "human_corrected",
                    "execution_verifiable",
                    "preference_candidate",
                    "recovery_trajectory",
                    "sft_candidate",
                    *(("sensitive_content",) if sensitive else ()),
                ],
                "evidence": [
                    "3 code-edit tool calls",
                    "human correction language after agent work",
                    "failure/correction followed by recovery work",
                ],
                "signals": {
                    "repository_available": True,
                    "reconstruction_confidence": "high",
                    "verification_commands": ["python -m pytest -q"],
                    "sensitive_content": sensitive,
                },
            },
        )
        return store, session_id

    def make_bundle(self, store: Store, session_id: str) -> Path:
        bundle = store.benchmarks / session_id
        bundle.mkdir()
        write_json(
            bundle / "task.json",
            {
                "schema_version": "0.1",
                "id": session_id,
                "task": "Fix the payment retry race without breaking clients.",
                "environment": {"unsupported_reasons": []},
                "reproducibility": {
                    "confidence": "medium",
                    "reference_patch": True,
                },
                "verifier": {"commands": ["python -m pytest -q"]},
            },
        )
        (bundle / "task.md").write_text("Fix the payment retry race.\n", encoding="utf-8")
        (bundle / "verifier.py").write_text("print('candidate')\n", encoding="utf-8")
        return bundle

    def run_cli(self, root: Path, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--home", str(root / "store"), *args])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_candidates_and_show_make_mined_data_browsable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_store(root)

            code, output, _ = self.run_cli(root, "candidates")
            self.assertEqual(code, 0)
            self.assertIn("EvoTrace Candidates", output)
            self.assertIn("RL environment", output)
            self.assertIn("Fix the payment retry race", output)
            self.assertIn("Open:  et show 1", output)

            code, output, _ = self.run_cli(root, "show", "1")
            self.assertEqual(code, 0)
            self.assertIn("Why valuable", output)
            self.assertIn("Training assets", output)
            self.assertIn("RL readiness", output)
            self.assertIn("Repository base recovered", output)
            self.assertIn("Next:         et build codex-cli-example", output)

    def test_numeric_build_selector_resolves_to_session_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_store(root)
            result = BuildResult(
                "codex-cli-example",
                "built",
                str(root / "store" / "benchmarks" / "codex-cli-example"),
            )
            with patch("evotrace.cli.build_mined_candidates", return_value=[result]) as build:
                code, output, _ = self.run_cli(root, "build", "1")
            self.assertEqual(code, 0)
            self.assertEqual(build.call_args.kwargs["session_id"], "codex-cli-example")
            self.assertIn("Validate: et validate codex-cli-example", output)

    def test_search_finds_task_words(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_store(root)

            code, output, _ = self.run_cli(root, "search", "payment", "retry")
            self.assertEqual(code, 0)
            self.assertIn("Fix the payment retry race", output)

            code, output, _ = self.run_cli(root, "search", "unrelated")
            self.assertEqual(code, 0)
            self.assertIn("No matching candidates", output)

    def test_assets_runs_and_portable_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store, session_id = self.make_store(root)
            self.make_bundle(store, session_id)
            record_run(
                store,
                kind="docker-validation",
                session_id=session_id,
                result={
                    "protocol": "docker-two-state-v0.1",
                    "provider": "docker",
                    "passed": True,
                    "image": {"reference_isolated": True},
                    "gates": {
                        "base_behavioral_failure": True,
                        "reference_passes": True,
                    },
                    "controls": {
                        "network": "none",
                        "host_mounts": [],
                        "docker_socket": False,
                        "privileged": False,
                        "no_new_privileges": True,
                    },
                },
                passed=True,
                score=1.0,
                bundle=store.benchmarks / session_id,
            )
            calibration = store.calibrations / session_id / "cal-example"
            write_json(
                calibration / "calibration.json",
                {"protocol": "deepseek-self-play-v0.1", "status": "calibrated"},
            )
            evolution = store.evolutions / session_id / "evo-example"
            write_json(
                evolution / "evolution.json",
                {"protocol": "execution-experience-evolution-v0.1", "status": "smoke_only"},
            )

            code, output, _ = self.run_cli(root, "assets")
            self.assertEqual(code, 0)
            self.assertIn("EvoTrace Assets", output)
            self.assertIn("Verified", output)

            code, output, _ = self.run_cli(root, "runs")
            self.assertEqual(code, 0)
            self.assertIn("EvoTrace Runs", output)
            self.assertIn("PASS", output)
            self.assertIn("100.0%", output)

            archive = root / "payment.evotrace.tar.gz"
            code, output, _ = self.run_cli(
                root, "export", "1", "--output", str(archive)
            )
            self.assertEqual(code, 0)
            self.assertTrue(archive.exists())
            self.assertIn("Nothing was uploaded", output)
            with tarfile.open(archive, "r:gz") as exported:
                names = exported.getnames()
            self.assertIn(f"{session_id}/manifest.json", names)
            self.assertIn(f"{session_id}/candidate.json", names)
            self.assertIn(f"{session_id}/events.jsonl", names)
            self.assertIn(f"{session_id}/bundle/task.json", names)
            self.assertTrue(any(name.startswith(f"{session_id}/runs/") for name in names))
            self.assertIn(
                f"{session_id}/calibrations/cal-example/calibration.json",
                names,
            )
            self.assertIn(
                f"{session_id}/evolutions/evo-example/evolution.json",
                names,
            )

    def test_sensitive_export_requires_explicit_consent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store, session_id = self.make_store(root, sensitive=True)
            self.make_bundle(store, session_id)
            code, _, error = self.run_cli(
                root, "export", session_id, "--output", str(root / "asset.tar.gz")
            )
            self.assertEqual(code, 2)
            self.assertIn("marked sensitive", error)

    def test_host_verifier_run_cannot_promote_asset_to_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store, session_id = self.make_store(root)
            self.make_bundle(store, session_id)
            record_run(
                store,
                kind="verify",
                session_id=session_id,
                result={"passed": True, "score": 1.0, "checks": []},
                passed=True,
                score=1.0,
                bundle=store.benchmarks / session_id,
            )
            code, output, _ = self.run_cli(root, "assets")
            self.assertEqual(code, 0)
            self.assertIn("Bundle ready", output)
            self.assertNotIn("Verified", output)

    def test_calibration_requires_upload_consent_and_reports_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store, session_id = self.make_store(root)
            bundle = self.make_bundle(store, session_id)
            code, _, error = self.run_cli(root, "calibrate", "1")
            self.assertEqual(code, 2)
            self.assertIn("--allow-upload", error)

            outcome = SimpleNamespace(
                report={"status": "calibrated"},
                pass_count=2,
                trials=5,
                target_passes=2,
                status="calibrated",
                rounds=1,
                calibration_dir=root / "calibration",
                run_dir=root / "run",
            )
            with patch("evotrace.cli.calibrate_bundle", return_value=outcome) as calibrate:
                code, output, _ = self.run_cli(
                    root,
                    "calibrate",
                    "1",
                    "--allow-upload",
                )
            self.assertEqual(code, 0)
            self.assertIn("Result:       2/5 passed", output)
            self.assertEqual(calibrate.call_args.args[0], bundle.resolve())

    def test_evolution_requires_upload_consent_and_reports_paired_gain(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store, session_id = self.make_store(root)
            bundle = self.make_bundle(store, session_id)
            code, _, error = self.run_cli(root, "evolve", "1")
            self.assertEqual(code, 2)
            self.assertIn("--allow-upload", error)

            outcome = SimpleNamespace(
                report={
                    "paired_evaluation": {"absolute_gain": 0.4},
                    "held_out_independent": False,
                    "held_out_validated": True,
                    "curriculum": {"direction": "harder"},
                },
                baseline_passes=1,
                conditioned_passes=3,
                trials=5,
                utility="useful",
                status="smoke_only",
                evolution_dir=root / "evolution",
                run_dir=root / "run",
            )
            with patch("evotrace.cli.evolve_experience", return_value=outcome) as evolve:
                code, output, _ = self.run_cli(
                    root,
                    "evolve",
                    "1",
                    "--allow-upload",
                )
            self.assertEqual(code, 0)
            self.assertIn("With experience: 3/5 passed", output)
            self.assertIn("evaluation checks the wiring only", output)
            self.assertEqual(evolve.call_args.args[0], bundle.resolve())
            self.assertEqual(evolve.call_args.args[1], bundle.resolve())

    def test_hardening_requires_consent_verified_parent_and_reports_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store, session_id = self.make_store(root)
            bundle = self.make_bundle(store, session_id)

            code, _, error = self.run_cli(root, "harden", "1")
            self.assertEqual(code, 2)
            self.assertIn("--allow-upload", error)

            code, _, error = self.run_cli(root, "harden", "1", "--allow-upload")
            self.assertEqual(code, 2)
            self.assertIn("verified parent bundle", error)

            record_run(
                store,
                kind="docker-validation",
                session_id=session_id,
                result={
                    "protocol": "docker-two-state-v0.1",
                    "provider": "docker",
                    "passed": True,
                    "image": {"reference_isolated": True},
                    "gates": {
                        "base_behavioral_failure": True,
                        "reference_passes": True,
                    },
                    "controls": {
                        "network": "none",
                        "host_mounts": [],
                        "docker_socket": False,
                        "privileged": False,
                        "no_new_privileges": True,
                    },
                },
                passed=True,
                score=1.0,
                bundle=bundle,
            )
            outcome = SimpleNamespace(
                report={"status": "training_ready"},
                child_id=f"{session_id}-hard-child",
                bundle=root / "child-bundle",
                validation_run=root / "child-validation",
                calibration=SimpleNamespace(pass_count=2, trials=5),
            )
            with patch("evotrace.cli.harden_bundle", return_value=outcome) as harden:
                code, output, _ = self.run_cli(
                    root,
                    "harden",
                    "1",
                    "--allow-upload",
                )
            self.assertEqual(code, 0)
            self.assertIn("EvoTrace Task Hardening", output)
            self.assertIn("2/5 passed", output)
            self.assertEqual(harden.call_args.args[0], bundle.resolve())


if __name__ == "__main__":
    unittest.main()
