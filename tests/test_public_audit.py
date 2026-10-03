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

    def test_public_audit_rejects_incomplete_requested_artifacts_and_comment_markers(self):
        module = _module()
        self.assertEqual(module.audit(require_dist=True)["result"], "blocked")
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(module.audit(root)["result"], "blocked")
            (root / "demo.whl").write_bytes(b"wheel")
            (root / "demo.tar.gz").write_bytes(b"sdist")
            (root / "SHA256SUMS").write_text("garbage\n", encoding="utf-8")
            self.assertEqual(module.audit(root)["result"], "blocked")
            original_root = module.ROOT
            try:
                module.ROOT = root
                (root / ".github" / "workflows").mkdir(parents=True)
                (root / ".github" / "workflows" / "release.yml").write_text("# SHA256SUMS refs/tags build gh release\n", encoding="utf-8")
                (root / "PROVENANCE.md").write_text("public provenance", encoding="utf-8")
                (root / "SECURITY.md").write_text("public security", encoding="utf-8")
                self.assertEqual(module._release_provenance()["state"], "blocked")
            finally:
                module.ROOT = original_root

    def test_public_audit_blocks_empty_tracked_file_scan(self):
        module = _module()
        original = module._tracked_files
        module._tracked_files = lambda: []
        try:
            self.assertEqual(module._secret_scan()["state"], "blocked")
        finally:
            module._tracked_files = original
