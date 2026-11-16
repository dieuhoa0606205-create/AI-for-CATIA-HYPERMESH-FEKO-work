from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hyperworks_mcp import launcher  # noqa: E402

SETTINGS = (
    '<?xml version="1.0"?>\n<settings>\n'
    '<entry name="@launcher_show_on_startup" value="1" type="bool"/>\n'
    "</settings>\n"
)
PROFILE = (
    '<?xml version="1.0"?>\n<profile>\n'
    '<entry name="@show_on_startup" value="1" type="bool"/>\n'
    "</profile>\n"
)
DOCUMENTS = (
    '<?xml version="1.0" encoding="UTF-16"?>\n<settings>\n'
    '<entry name="launcher_show_on_startup" value="1" type="bool"/>\n'
    "</settings>\n"
)


def make_home(root: Path, settings: str = SETTINGS, profile: str = PROFILE,
              documents: str = DOCUMENTS) -> Path:
    hwx = root / ".altair" / "2025.0" / "hwx"
    (hwx / "profiles").mkdir(parents=True)
    (hwx / "settings.xml").write_text(settings, encoding="latin-1")
    (hwx / "profiles" / "HyperworksBaseProfile.xml").write_text(profile, encoding="latin-1")
    (root / "Documents").mkdir()
    (root / "Documents" / "hwsettings.xml").write_text(documents, encoding="latin-1")
    return root


class LauncherSettingTest(unittest.TestCase):
    def test_finds_all_three_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(Path(tmp))
            paths = launcher.setting_files(home)
            self.assertEqual(len(paths), 3)
            self.assertTrue(any(path.name == "settings.xml" for path in paths))
            self.assertTrue(any(path.name == "HyperworksBaseProfile.xml" for path in paths))
            self.assertTrue(any(path.name == "hwsettings.xml" for path in paths))

    def test_reports_that_the_selector_will_appear(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(Path(tmp))
            report = launcher.read_setting(home)
            self.assertTrue(report["will_show_launcher"])
            self.assertEqual([item["value"] for item in report["files"]], [1, 1, 1])
            self.assertIn("selector", report["consequence"])

    def test_one_file_left_on_is_enough(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(
                Path(tmp),
                settings=SETTINGS.replace('value="1"', 'value="0"'),
                profile=PROFILE.replace('value="1"', 'value="0"'),
            )
            self.assertTrue(launcher.read_setting(home)["will_show_launcher"])

    def test_all_zero_reports_no_selector(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(
                Path(tmp),
                settings=SETTINGS.replace('value="1"', 'value="0"'),
                profile=PROFILE.replace('value="1"', 'value="0"'),
                documents=DOCUMENTS.replace('value="1"', 'value="0"'),
            )
            report = launcher.read_setting(home)
            self.assertFalse(report["will_show_launcher"])
            self.assertIn("starts directly", report["consequence"])

    def test_set_writes_every_file_and_keeps_backups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(Path(tmp))
            before = {path: path.read_bytes() for path in launcher.setting_files(home)}
            result = launcher.set_setting(False, home)
            self.assertFalse(result["now"]["will_show_launcher"])
            self.assertEqual(len(result["changed"]), 3)
            self.assertEqual(len(result["backups"]), 3)
            for backup, raw in zip(result["backups"], before.values()):
                self.assertTrue(Path(backup).is_file())
                self.assertEqual(Path(backup).read_bytes(), raw)
            for path in launcher.setting_files(home):
                self.assertEqual(launcher.ATTRIBUTE.search(path.read_bytes()).group(2), b"0")

    def test_set_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(Path(tmp))
            launcher.set_setting(False, home)
            again = launcher.set_setting(False, home)
            self.assertEqual(again["changed"], [])
            self.assertEqual(again["backups"], [])

    def test_set_back_to_one_restores_display(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = make_home(Path(tmp))
            launcher.set_setting(False, home)
            result = launcher.set_setting(True, home)
            self.assertTrue(result["now"]["will_show_launcher"])
            self.assertEqual(len(result["changed"]), 3)

    def test_attribute_does_not_match_unrelated_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hwx = root / ".altair" / "2025.0" / "hwx"
            hwx.mkdir(parents=True)
            (hwx / "settings.xml").write_text(
                '<entry name="other_show_on_startup" value="1" type="bool"/>',
                encoding="latin-1",
            )
            self.assertEqual(launcher.setting_files(root), [])

    def test_missing_home_is_empty_and_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = launcher.read_setting(Path(tmp))
            self.assertEqual(report["files"], [])
            self.assertFalse(report["will_show_launcher"])
            self.assertEqual(launcher.set_setting(True, Path(tmp))["changed"], [])

    def test_window_enumeration_returns_a_list(self) -> None:
        titles = launcher.launcher_windows()
        self.assertIsInstance(titles, list)
        for title in titles:
            self.assertTrue(title.startswith(launcher.LAUNCHER_TITLE_PREFIX))

    def test_wait_returns_quickly_when_nothing_appears(self) -> None:
        # Only meaningful when no selector is on screen; a visible one just returns early.
        found = launcher.wait_for_launcher(timeout=0.2, interval=0.05)
        self.assertIsInstance(found, list)


if __name__ == "__main__":
    unittest.main()
