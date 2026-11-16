import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import generate_manifest
import multiplayer_warning_policy as policy
import network_logging_policy


class MultiplayerWarningPolicyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.pack = Path(temp.name)
        self.option = self.pack / policy.RELATIVE_PATH

    def test_import_without_option_file(self):
        policy.prepare(self.pack)
        self.assertEqual(self.option.read_bytes(), b"!skipMultiplayerWarning:true\n")
        policy.verify(self.pack)
        saved = self.option.stat().st_mtime_ns
        policy.prepare(self.pack)
        self.assertEqual(self.option.stat().st_mtime_ns, saved)

    def test_merge_only_changes_warning_lines(self):
        cases = [
            (b"!someOtherFlag:false\n", b"!someOtherFlag:false\n!skipMultiplayerWarning:true\n"),
            (b"fov:0.7", b"fov:0.7\n!skipMultiplayerWarning:true\n"),
            (b"\xef\xbb\xbf# Comment\r\n ! skipMultiplayerWarning : false\r\n"
             b"resourcePacks:[\"vanilla\"]\r\nskipMultiplayerWarning:true\r\nlang:en_us",
             b"\xef\xbb\xbf# Comment\r\n!skipMultiplayerWarning:true\r\n"
             b"resourcePacks:[\"vanilla\"]\r\nlang:en_us"),
        ]
        self.option.parent.mkdir()
        for before, after in cases:
            with self.subTest(before=before):
                self.option.write_bytes(before)
                policy.prepare(self.pack)
                self.assertEqual(self.option.read_bytes(), after)
                policy.verify(self.pack)

    def test_generation_rejects_missing_unheld_false_and_duplicate(self):
        (self.pack / "portable-pack.json").write_text('{"minecraftVersion":"26.3"}', encoding="utf-8")
        logging = self.pack / "config/log4j2.xml"
        logging.parent.mkdir()
        logging.write_bytes((Path(__file__).resolve().parents[1] / "config/log4j2.xml").read_bytes())
        network_logging_policy.prepare(self.pack)
        cases = (None, b"fov:0.6\n", b"skipMultiplayerWarning:true\n",
                 b"!skipMultiplayerWarning:false\n",
                 b"skipMultiplayerWarning:false\n!skipMultiplayerWarning:true\n")
        for data in cases:
            with self.subTest(data=data):
                if data is not None:
                    self.option.write_bytes(data)
                output = self.pack / "dist"
                with self.assertRaisesRegex(ValueError, "exactly one"):
                    generate_manifest.generate(self.pack, output, "test")
                self.assertFalse(output.exists())

    def test_verify_only_rejects_false_inside_consistent_staged_archive(self):
        assets = self.pack / "assets"
        assets.mkdir()
        staged = assets / "launcher.zip"
        data = b"!skipMultiplayerWarning:false\n"
        with zipfile.ZipFile(staged, "w") as archive:
            archive.writestr(policy.RELATIVE_PATH, data)
        manifest = {
            "files": [{"path": policy.RELATIVE_PATH, "sha256": hashlib.sha256(data).hexdigest(),
                       "sizeBytes": len(data)}],
            "assets": [{"name": staged.name, "kind": "zipRoot", "covers": [policy.RELATIVE_PATH],
                        "sha256": generate_manifest.sha256_file(staged), "sizeBytes": staged.stat().st_size}],
        }
        (self.pack / "pack-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "exactly one"):
            generate_manifest.verify_only(self.pack)
