"""Publication must keep the pack-owned action on the same native pause button."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import generate_manifest
import multiplayer_warning_policy
import network_logging_policy


class PauseActionsPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pack = self.root / "pack"
        (self.pack / "mods").mkdir(parents=True)
        self.source = self.pack / "tools/pause-actions/contract.json"
        self.source.parent.mkdir(parents=True)
        self.contract = json.loads((Path(__file__).parent / "pause-actions/contract.json").read_text())
        self.source.write_text(json.dumps(self.contract), encoding="utf-8")
        self.portable = {"schemaVersion": 1, "minecraftVersion": "26.3",
                         "loader": {"type": "neoforge", "version": "26.3.0.99"}}
        self.jvm = self.pack / "launcher/jvm-args.txt"
        self.jvm.parent.mkdir()
        self.jvm_text = ("-Dlog4j.configurationFile=config/log4j2.xml\n"
                         "-Dminecraft.portable.identity.pauseLanRowEnabled=false\n"
                         "-Dunrelated.player.choice=true\n")
        self.jvm.write_text(self.jvm_text, encoding="utf-8")
        self.jar = self.pack / self.contract["artifactPath"]
        self.mixin_config = {"required": True, "minVersion": "0.8.7",
                             "package": "lanmc.pauseactions.mixin", "compatibilityLevel": "JAVA_25",
                             "client": ["ButtonActionAccessor"], "injectors": {"defaultRequire": 1}}
        self.write_helper()
        logging = self.pack / "config/log4j2.xml"
        logging.parent.mkdir()
        logging.write_bytes((Path(__file__).resolve().parent.parent / "config/log4j2.xml").read_bytes())
        network_logging_policy.prepare(self.pack)
        multiplayer_warning_policy.prepare(self.pack)

    def write_helper(self, *, mod_id=None, policy=None, project=None, version=None,
                     embedded=True, mixins=None, registered=True, missing_class=None,
                     nested_spoof=False):
        metadata = ('modLoader="javafml"\nloaderVersion="[12,)"\n[[mods]]\n'
                    f'modId="{mod_id or self.contract["modId"]}"\n'
                    f'version="{version or self.contract["modVersion"]}"\n'
                    f'displayURL="{project or self.contract["projectUrl"]}"\n')
        if registered:
            metadata += '\n[[mixins]]\nconfig="lanmc-pause-actions.mixins.json"\n'
        with zipfile.ZipFile(self.jar, "w") as archive:
            archive.writestr("META-INF/neoforge.mods.toml", metadata)
            if embedded:
                archive.writestr(generate_manifest.PAUSE_ACTIONS_EMBEDDED_CONTRACT,
                                 json.dumps(self.contract if policy is None else policy))
            archive.writestr(generate_manifest.PAUSE_ACTIONS_MIXINS,
                             json.dumps(self.mixin_config if mixins is None else mixins))
            # This suite validates delivery metadata. The separate native build
            # probe transforms and executes the real accessor and component.
            for name in ("lanmc/pauseactions/PauseActions.class",
                         "lanmc/pauseactions/mixin/ButtonActionAccessor.class",
                         "lanmc/pauseactions/SteamWorldDiscovery.class"):
                if name != missing_class:
                    archive.writestr(name, b"publication-fixture-class")
            if nested_spoof:
                nested = io.BytesIO()
                with zipfile.ZipFile(nested, "w") as inner:
                    inner.writestr("META-INF/neoforge.mods.toml",
                                   '[[mods]]\nmodId="lanmc_pause_actions"\nversion="1.0.0"\n')
                archive.writestr("META-INF/jarjar/helper.jar", nested.getvalue())
                archive.writestr("META-INF/jarjar/metadata.json", json.dumps({"jars": [{
                    "path": "META-INF/jarjar/helper.jar"}]}))
        self.refresh_lock()

    def refresh_lock(self):
        (self.pack / "portable-pack.json").write_text(json.dumps(self.portable), encoding="utf-8")
        files = []
        for jar in sorted((self.pack / "mods").glob("*.jar")):
            data = jar.read_bytes()
            ids = sorted({mod["id"] for mod in generate_manifest.read_mods(
                data, jar.name, loader="neoforge") if mod["isLoadedMod"]})
            files.append({"path": jar.relative_to(self.pack).as_posix(),
                          "sizeBytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                          "modIds": ids})
        lock = {**self.portable, "files": files}
        (self.pack / "tools/distribution-lock.json").write_text(json.dumps(lock), encoding="utf-8")
        # All negative fixtures keep the ordinary hash/ID lock internally valid.
        shipped = [row["path"] for row in files]
        meta = {row["path"]: (row["sha256"], row["sizeBytes"]) for row in files}
        generate_manifest.validate_mod_lock(self.pack, self.portable, shipped, meta)

    def generate(self):
        out = self.root / "dist"
        out.mkdir(exist_ok=True)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            generate_manifest.generate(self.pack, out, "pause-actions-test")
        return json.loads((out / "pack-manifest.json").read_text())

    def assert_refused(self):
        with self.assertRaises(SystemExit):
            self.generate()
        self.assertFalse((self.root / "dist/pack-manifest.json").exists())

    def test_current_and_future_runtime_descriptors_keep_menu_and_discovery_with_reviewed_logging(self):
        for game, loader in (("26.3", "26.3.0.99"), ("26.4", "26.4.0.1")):
            with self.subTest(minecraft=game):
                self.portable["minecraftVersion"] = game
                self.portable["loader"]["version"] = loader
                self.refresh_lock()
                before = self.jvm.read_bytes()
                self.assertEqual(generate_manifest.PAUSE_ACTIONS_CONTRACT["steamDiscovery"],
                                 self.contract["steamDiscovery"])
                if game != "26.3":
                    # The menu's forward contract remains valid independently;
                    # publishing a new runtime also needs its logging caller proof.
                    generate_manifest.validate_pause_actions(self.pack, self.portable,
                        [self.contract["artifactPath"], "launcher/jvm-args.txt"])
                    with self.assertRaisesRegex(ValueError, "native proof does not cover Minecraft 26.4"):
                        self.generate()
                    self.assertEqual(before, self.jvm.read_bytes())
                    continue
                manifest = self.generate()
                self.assertEqual(game, manifest["minecraftVersion"])
                self.assertIn(self.contract["artifactPath"], [row["path"] for row in manifest["files"]])
                self.assertEqual(1, manifest["modCount"])
                self.assertEqual(before, self.jvm.read_bytes())
                jvm = next(row for row in manifest["files"] if row["path"] == "launcher/jvm-args.txt")
                self.assertEqual(hashlib.sha256(before).hexdigest(), jvm["sha256"])

    def test_removing_helper_and_its_lock_row_together_still_refuses_publication(self):
        self.jar.unlink()
        self.refresh_lock()
        self.assert_refused()

    def test_renaming_helper_with_an_updated_consistent_lock_refuses_publication(self):
        self.jar.rename(self.jar.with_name("another-name.jar"))
        self.refresh_lock()
        self.assert_refused()

    def test_wrong_root_mod_id_is_not_rescued_by_a_matching_nested_mod(self):
        self.write_helper(mod_id="unrelated", nested_spoof=True)
        self.assert_refused()

    def test_wrong_embedded_policy_or_source_identity_refuses_publication(self):
        mutations = {"translationKey": "menu.options", "screen": "net.minecraft.client.gui.screens.Screen",
                     "lanTranslationKey": "menu.options", "reportsTranslationKey": "menu.options",
                     "replaceInPlace": False, "clientOnly": False,
                     "sourceProject": "someone/another-project",
                     "modVersion": "1.0.0", "artifactPath": "mods/lanmc-pause-actions-1.0.0.jar"}
        for field, value in mutations.items():
            with self.subTest(field=field):
                self.write_helper(policy={**self.contract, field: value})
                self.assert_refused()

    def test_missing_embedded_contract_refuses_publication(self):
        self.write_helper(embedded=False)
        self.assert_refused()

    def test_different_mod_version_or_project_refuses_publication(self):
        for overrides in ({"version": "0.0.0"}, {"project": "https://github.com/someone/another-project"}):
            with self.subTest(overrides=overrides):
                self.write_helper(**overrides)
                self.assert_refused()

    def test_changing_both_contract_copies_cannot_restore_the_removed_policy(self):
        changed = {**self.contract, "replaceInPlace": False}
        self.source.write_text(json.dumps(changed), encoding="utf-8")
        self.write_helper(policy=changed)
        self.assert_refused()

    def test_replace_in_place_boolean_cannot_be_replaced_with_an_integer(self):
        self.source.write_text(json.dumps({**self.contract, "replaceInPlace": 1}), encoding="utf-8")
        self.assert_refused()

    def test_removing_or_changing_both_discovery_contract_copies_refuses_publication(self):
        changed_contracts = [{key: value for key, value in self.contract.items()
                              if key != "steamDiscovery"}]
        for key, value in self.contract["steamDiscovery"].items():
            replacement = False if isinstance(value, bool) else "unrelated"
            changed_contracts.append({**self.contract, "steamDiscovery": {
                **self.contract["steamDiscovery"], key: replacement}})
        changed_contracts.append({**self.contract, "steamDiscovery": {
            **self.contract["steamDiscovery"], "e4steamVersion": "0.3.4"}})
        for changed in changed_contracts:
            with self.subTest(policy=changed):
                self.source.write_text(json.dumps(changed), encoding="utf-8")
                self.write_helper(policy=changed)
                self.assert_refused()

    def test_discovery_nested_boolean_cannot_be_replaced_with_an_integer(self):
        for field, value in self.contract["steamDiscovery"].items():
            if not isinstance(value, bool):
                continue
            with self.subTest(field=field):
                changed = {**self.contract, "steamDiscovery": {
                    **self.contract["steamDiscovery"], field: 1}}
                self.source.write_text(json.dumps(changed), encoding="utf-8")
                self.write_helper(policy=changed)
                self.assert_refused()

    def test_accessor_must_be_required_and_registered_only_on_the_client(self):
        mutations = ({"required": False}, {"client": []}, {"client": ["AnotherAccessor"]},
                     {"server": ["ButtonActionAccessor"]}, {"mixins": ["ButtonActionAccessor"]},
                     {"injectors": None}, {"injectors": {"defaultRequire": 0}})
        for change in mutations:
            with self.subTest(change=change):
                self.write_helper(mixins={**self.mixin_config, **change})
                self.assert_refused()

    def test_missing_toml_mixin_registration_refuses_publication(self):
        self.write_helper(registered=False)
        self.assert_refused()

    def test_missing_component_accessor_or_discovery_bridge_class_refuses_publication(self):
        for missing in ("lanmc/pauseactions/PauseActions.class",
                        "lanmc/pauseactions/mixin/ButtonActionAccessor.class",
                        "lanmc/pauseactions/SteamWorldDiscovery.class"):
            with self.subTest(missing=missing):
                self.write_helper(missing_class=missing)
                self.assert_refused()

    def test_required_jvm_property_must_appear_exactly_once_and_be_false(self):
        key = "-Dminecraft.portable.identity.pauseLanRowEnabled"
        for text in ("", key + "=true\n", key + "\n", key + "=False\n",
                     key + "=false\n" + key + "=false\n", key + "=false\n" + key + "=true\n"):
            with self.subTest(text=text):
                self.jvm.write_text("-Dlog4j.configurationFile=config/log4j2.xml\n-Dunrelated.player.choice=true\n" + text, encoding="utf-8")
                self.assert_refused()

    def test_missing_jvm_file_refuses_publication(self):
        self.jvm.unlink()
        with self.assertRaisesRegex(ValueError, "Missing pack logging file: launcher/jvm-args.txt"):
            self.generate()
        self.assertFalse((self.root / "dist/pack-manifest.json").exists())

    def test_commented_out_property_is_ignored_and_other_arguments_are_untouched(self):
        self.jvm.write_text(self.jvm_text + "# -Dminecraft.portable.identity.pauseLanRowEnabled=true\n", encoding="utf-8")
        original = self.jvm.read_bytes()
        self.generate()
        self.assertEqual(original, self.jvm.read_bytes())

    def test_retired_helper_cannot_be_active_even_with_a_matching_lock(self):
        for name, mod_id in (("lanmc-world-menu-1.0.0.jar", "unrelated"), ("renamed.jar", "lanmc_world_menu")):
            with self.subTest(name=name):
                old = self.pack / "mods" / name
                with zipfile.ZipFile(old, "w") as archive:
                    archive.writestr("META-INF/neoforge.mods.toml", f'[[mods]]\nmodId="{mod_id}"\nversion="1"\n')
                self.refresh_lock()
                self.assert_refused()
                old.unlink()

    def test_different_loader_cannot_silently_skip_the_required_component(self):
        self.portable["loader"] = {"type": "fabric", "version": "0.19.3"}
        self.refresh_lock()
        self.assert_refused()

    def test_generic_fixture_without_the_new_contract_keeps_working(self):
        self.source.unlink()
        self.jar.unlink()
        self.refresh_lock()
        manifest = self.generate()
        self.assertEqual(0, manifest["modCount"])


if __name__ == "__main__":
    unittest.main()
