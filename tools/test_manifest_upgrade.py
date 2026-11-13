"""Guard the upgrade's active/retired JAR boundary before publication."""
import hashlib
import json
import pathlib
import tempfile
import unittest
import zipfile
import io

import generate_manifest
import network_logging_policy


class UpgradeManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.pack = self.root / "pack"
        (self.pack / "mods").mkdir(parents=True)
        (self.pack / "tools").mkdir()
        self.portable = {"minecraftVersion": "26.3", "loader": {"type": "neoforge", "version": "26.3.0.99"}}
        (self.pack / "portable-pack.json").write_text(json.dumps(self.portable))
        logging = self.pack / "config/log4j2.xml"
        logging.parent.mkdir()
        logging.write_bytes((pathlib.Path(__file__).resolve().parent.parent / "config/log4j2.xml").read_bytes())
        network_logging_policy.prepare(self.pack)
        self.jar = self.pack / "mods/new.jar"
        with zipfile.ZipFile(self.jar, "w") as archive:
            archive.writestr("META-INF/neoforge.mods.toml", '[[mods]]\nmodId="emi"\ndisplayName="EMI"\nversion="new"\n')
        self.active = {"path": "mods/new.jar", "sizeBytes": self.jar.stat().st_size,
                       "sha256": hashlib.sha256(self.jar.read_bytes()).hexdigest(), "modIds": ["emi"]}
        self.write_lock("distribution-lock.json", {**self.portable, "files": [self.active]})
        self.legacy = {"path": "mods/old.jar", "sizeBytes": 100, "sha256": "1" * 64}
        self.write_lock("legacy-files.json", {"files": [self.legacy]})

    def tearDown(self):
        self.temp.cleanup()

    def write_lock(self, name, content):
        (self.pack / "tools" / name).write_text(json.dumps({"schemaVersion": 1, **content}))

    def generate(self):
        output = self.root / "dist"
        output.mkdir(exist_ok=True)
        generate_manifest.generate(self.pack, output, "test-revision")
        return json.loads((output / "pack-manifest.json").read_text())

    def test_retired_jar_is_exact_hash_metadata_not_an_active_download(self):
        # Preserve every real migration: EMI 26.2, the retired World Menu and
        # Pause Actions 1.0.0. The generic active/legacy guard is shared by all.
        retired = json.loads((pathlib.Path(__file__).parent / "legacy-files.json").read_text(encoding="utf-8"))["files"]
        self.assertEqual(4, len(retired))
        for legacy in retired:
            with self.subTest(path=legacy["path"]):
                self.write_lock("legacy-files.json", {"files": [legacy]})
                manifest = self.generate()
                self.assertEqual([legacy], manifest["legacyFiles"])
                self.assertNotIn(legacy["path"], [row["path"] for row in manifest["files"]])
                self.assertNotIn(legacy["path"], [path for asset in manifest["assets"] for path in asset["covers"]])
                self.assertEqual(1, manifest["modCount"])

    def test_changed_bytes_and_accidentally_retained_old_jar_stop_publication(self):
        original = self.jar.read_bytes()
        self.jar.write_bytes(original + b"changed")
        with self.assertRaises(SystemExit):
            self.generate()
        self.jar.write_bytes(original)
        (self.pack / "mods/old.jar").write_bytes(original)
        with self.assertRaises(SystemExit):
            self.generate()

    def test_wrapper_and_embedded_same_mod_id_are_one_locked_id(self):
        # Sodium's official NeoForge wrapper and selected inner jar both carry
        # the same id/version. The lock records unique runtime ids, not two mods.
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as archive:
            archive.writestr("META-INF/neoforge.mods.toml", '[[mods]]\nmodId="emi"\ndisplayName="EMI"\nversion="new"\n')
        with zipfile.ZipFile(self.jar, "a") as archive:
            archive.writestr("META-INF/jarjar/embedded.jar", inner.getvalue())
            archive.writestr("META-INF/jarjar/metadata.json", json.dumps({"jars": [{
                "identifier": {"group": "author", "artifact": "embedded"},
                "version": {"range": "[1,)", "artifactVersion": "1"},
                "path": "META-INF/jarjar/embedded.jar"}]}))
        active = {**self.active, "sizeBytes": self.jar.stat().st_size,
                  "sha256": hashlib.sha256(self.jar.read_bytes()).hexdigest()}
        self.write_lock("distribution-lock.json", {**self.portable, "files": [active]})
        manifest = self.generate()
        self.assertEqual(1, manifest["modCount"])

    def test_legacy_active_collision_and_traversal_stop_publication(self):
        for path in ("mods/new.jar", "mods/../outside.jar"):
            self.write_lock("legacy-files.json", {"files": [{**self.legacy, "path": path}]})
            with self.assertRaises(SystemExit):
                self.generate()


if __name__ == "__main__":
    unittest.main()
