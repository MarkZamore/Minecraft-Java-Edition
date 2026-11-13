import json
import io
import hashlib
import pathlib
import tempfile
import unittest
import zipfile
from pack_library import build_library, read_mods, stage_library, verify_library


CURRENT_MODS = {"emi": "EMI", "iris": "Iris", "sodium": "Sodium",
                "lanmc_pause_actions": "Pause Menu Actions"}


class LibraryPublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.pack = pathlib.Path(self.temp.name) / "Example Pack"
        (self.pack / "mods").mkdir(parents=True)
        (self.pack / "library").mkdir()
        source = pathlib.Path(__file__).resolve().parent.parent
        self.runtime = json.loads((source / "portable-pack.json").read_text(encoding="utf-8"))
        (self.pack / "portable-pack.json").write_text(json.dumps(self.runtime))
        self.content = json.loads((source / "library/content.json").read_text(encoding="utf-8"))
        self.pages = json.loads((source / "library/mod-pages.json").read_text(encoding="utf-8"))
        self.write_pages()
        self.write_content()
        (self.pack / "library/preview.jpg").write_bytes(b"test preview bytes")
        for mod_id, name in CURRENT_MODS.items():
            with zipfile.ZipFile(self.pack / "mods" / (mod_id + ".jar"), "w") as archive:
                archive.writestr("META-INF/neoforge.mods.toml", f'[[mods]]\nmodId="{mod_id}"\ndisplayName="{name}"\nversion="1.2.3"\n')
                if mod_id == "sodium":
                    # The current Sodium NeoForge wrapper repeats its own ID in
                    # the selected embedded JAR. Every bundle fixture keeps that
                    # real composition instead of an unrelated generic addon.
                    nested = io.BytesIO()
                    with zipfile.ZipFile(nested, "w") as inner:
                        inner.writestr("META-INF/neoforge.mods.toml", '[[mods]]\nmodId="sodium"\ndisplayName="Sodium"\nversion="1.2.3"\n')
                    archive.writestr("META-INF/jarjar/embedded.jar", nested.getvalue())
                    archive.writestr("META-INF/jarjar/metadata.json", json.dumps({"jars": [{"path": "META-INF/jarjar/embedded.jar"}]}))

    def tearDown(self):
        self.temp.cleanup()

    def write_pages(self):
        (self.pack / "library/mod-pages.json").write_text(json.dumps(self.pages))

    def write_content(self):
        (self.pack / "library/content.json").write_text(json.dumps(self.content))

    def test_current_neoforge_names_links_and_stable_identity_are_published(self):
        with zipfile.ZipFile(io.BytesIO(build_library(self.pack, "revision-one"))) as archive:
            data = json.loads(archive.read("library.json"))
        mods = {mod["id"]: mod for mod in data["mods"]}
        self.assertEqual(set(CURRENT_MODS), set(mods))
        self.assertEqual(len(CURRENT_MODS), len(data["mods"]))
        for mod_id, name in CURRENT_MODS.items():
            self.assertEqual(name, mods[mod_id]["name"])
            self.assertEqual(self.pages[mod_id], mods[mod_id]["websiteUrl"])
            self.assertTrue(mods[mod_id]["isLoadedMod"])
            self.assertFalse(mods[mod_id]["isLibrary"])
            self.assertIsNone(mods[mod_id]["parentId"])
        self.assertEqual("26.3", data["minecraftVersion"])
        self.assertEqual("neoforge", data["loader"])
        self.assertEqual("26.3.0.99", self.runtime["loader"]["version"])
        locked = json.loads((pathlib.Path(__file__).resolve().parent / "distribution-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(set(CURRENT_MODS), {mod_id for row in locked["files"] for mod_id in row["modIds"]})
        self.assertEqual("revision-one", data["revision"])
        self.assertEqual("minecraft-26-2", data["id"])
        self.assertEqual("Minecraft Java Edition", data["relativePath"])
        self.assertEqual(self.content["name"], data["name"])

    def test_bundle_is_deterministic_and_tampering_fails_verification(self):
        self.assertEqual(build_library(self.pack, "r1"), build_library(self.pack, "r1"))
        out = pathlib.Path(self.temp.name) / "out"
        descriptor = stage_library(self.pack, out, "r1")
        verify_library(out, descriptor)
        (out / "assets" / descriptor["name"]).write_bytes(b"tampered")
        with self.assertRaises(AssertionError):
            verify_library(out, descriptor)

    def test_new_mod_requires_an_explicit_official_project_page(self):
        del self.pages["iris"]
        self.write_pages()
        with self.assertRaisesRegex(ValueError, "official project page for mod: iris"):
            build_library(self.pack, "r1")

    def test_only_https_project_pages_are_published(self):
        verified_page = self.pages["iris"]
        for value in ("javascript:alert(1)", "file:///C:/mod.jar", "https://user:secret@example.com/mod", ""):
            self.pages["iris"] = value
            self.write_pages()
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_library(self.pack, "r1")
        self.pages["iris"] = verified_page
        self.write_pages()
        self.assertTrue(build_library(self.pack, "r1"))

    def test_unavailable_author_page_is_preserved_but_not_clickable(self):
        self.pages["iris"] = {"url": "https://github.com/author/unavailable", "available": False}
        self.write_pages()
        payload = build_library(self.pack, "r1")
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            mods = json.loads(archive.read("library.json"))["mods"]
        mod = next(mod for mod in mods if mod["id"] == "iris")
        self.assertFalse(mod["websiteAvailable"])
        self.assertEqual(self.pages["iris"]["url"], mod["websiteUrl"])

    def test_cyclic_addon_map_cannot_be_published(self):
        self.content["parents"] = {"iris": "sodium", "sodium": "iris"}
        self.write_content()
        with self.assertRaisesRegex(ValueError, "Cyclic"):
            build_library(self.pack, "r1")

    def test_neoforge_metadata_uses_manifest_version_and_ignores_fabric_only_nested_libraries(self):
        fabric = io.BytesIO()
        with zipfile.ZipFile(fabric, "w") as archive:
            archive.writestr("fabric.mod.json", json.dumps({"id": "fabric_only", "version": "1.0"}))
        carrier = io.BytesIO()
        with zipfile.ZipFile(carrier, "w") as archive:
            archive.writestr("META-INF/MANIFEST.MF", "Implementation-Version: 2.4.1\n")
            archive.writestr("META-INF/neoforge.mods.toml", '[[mods]]\nmodId="real_mod"\nversion="${file.jarVersion}"\n')
            archive.writestr("META-INF/jarjar/metadata.json", json.dumps({"jars": [{"path": "META-INF/jarjar/fabric.jar"}]}))
            archive.writestr("META-INF/jarjar/fabric.jar", fabric.getvalue())
        mods = read_mods(carrier.getvalue(), "carrier.jar", loader="neoforge")
        self.assertEqual(["real_mod"], [mod["id"] for mod in mods])
        self.assertEqual("2.4.1", mods[0]["version"])

    def test_actual_loader_inventory_resolves_selected_ids_and_versions_and_rejects_stale_jars(self):
        inventory = {"schemaVersion": 1,
                     "minecraftVersion": self.runtime["minecraftVersion"], "loader": self.runtime["loader"],
                     "sourceJars": {jar.relative_to(self.pack).as_posix(): hashlib.sha256(jar.read_bytes()).hexdigest()
                                    for jar in (self.pack / "mods").glob("*.jar")},
                     "mods": [{"id": mid, "name": mid.title(), "version": "selected-2.0"}
                              for mid in CURRENT_MODS]}
        (self.pack / "library/loaded-mods.json").write_text(json.dumps(inventory))
        with zipfile.ZipFile(io.BytesIO(build_library(self.pack, "r1"))) as archive:
            mods = json.loads(archive.read("library.json"))["mods"]
        self.assertEqual(set(CURRENT_MODS), {mod["id"] for mod in mods})
        self.assertEqual({"selected-2.0"}, {mod["version"] for mod in mods})
        with zipfile.ZipFile(self.pack / "mods/iris.jar", "a") as archive:
            archive.writestr("changed.txt", "new author version")
        with self.assertRaisesRegex(ValueError, "inventory does not match"):
            build_library(self.pack, "r2")


if __name__ == "__main__":
    unittest.main()
