import importlib.util
from pathlib import Path
import unittest


def _module():
    path = Path(__file__).parents[1] / "scripts" / "audit_public_surface.py"
    spec = importlib.util.spec_from_file_location("audit_public_surface", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PublicAuditTests(unittest.TestCase):
    def test_public_audit_passes_static_surface_and_marks_missing_artifacts_unavailable(self):
        report = _module().audit()
        self.assertEqual(report["schema"], "chatlens-public-audit/v1")
        self.assertEqual(report["result"], "pass")
        self.assertEqual(report["dependency_inventory"]["state"], "pass")
        self.assertEqual(report["license_inventory"]["state"], "pass")
        self.assertEqual(report["release_provenance"]["state"], "pass")
        self.assertEqual(report["privacy_scan"]["state"], "pass")
        self.assertEqual(report["artifact_audit"]["state"], "unavailable")

    def test_public_audit_flags_high_signal_private_key(self):
        module = _module()
        original = module._tracked_files
        with self.subTest("synthetic marker"):
            import tempfile

            with tempfile.TemporaryDirectory() as directory:
                fake = Path(directory) / "fixture.txt"
                fake.write_bytes(b"-----BEGIN " + b"PRIVATE KEY-----\nsynthetic\n")
                module._tracked_files = lambda: [fake]
                try:
                    result = module._secret_scan()
                finally:
                    module._tracked_files = original
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(result["findings"], [{"path": "fixture.txt", "class": "private_key"}])

    def test_public_audit_blocks_checksum_mismatch(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory)
            (dist / "demo.whl").write_bytes(b"wheel")
            (dist / "demo.tar.gz").write_bytes(b"sdist")
            (dist / "SHA256SUMS").write_text("0" * 64 + "  demo.whl\n" + "1" * 64 + "  demo.tar.gz\n", encoding="utf-8")
            report = _module().audit(dist)
        self.assertEqual(report["artifact_audit"]["state"], "blocked")
        self.assertEqual(report["result"], "blocked")
