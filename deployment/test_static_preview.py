"""Static publication boundary, real anonymous content and subpath asset checks."""
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest

from deployment.build_static_preview import ROOT, SAMPLE, build, validate_base


class StaticPreviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.output = cls.root / "root-preview"
        cls.subpath = cls.root / "pages-preview"
        cls.result = build(cls.output)
        build(cls.subpath, "/TPC-public-review/")
        cls.manifest = json.loads((cls.output / "preview-data.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_anonymous_routes_and_stable_lesson_identity(self):
        routes = self.manifest["routes"]
        self.assertEqual(routes["/api/session"], {"status": 200, "body": {"user": None}})
        course = routes["/api/catalog"]["body"]["courses"][0]
        self.assertEqual(course["id"], "preflop-foundations")
        lesson = routes["/api/courses/preflop-foundations/lessons/position"]["body"]["lesson"]
        self.assertEqual(lesson["id"], "position")
        self.assertFalse(lesson["watchProgress"]["canSave"])
        self.assertFalse(lesson["learning"]["canRecord"])

    def test_private_content_and_original_database_excluded(self):
        paths = [p.relative_to(self.output).as_posix() for p in self.output.rglob("*") if p.is_file()]
        allowed = {"index.html", "styles.css", "preview-data.json", ".nojekyll"}
        for path in paths:
            self.assertTrue(path in allowed or path.startswith(("src/", "assets/", "proposal/", "public-media/")), path)
            self.assertNotRegex(path, r"(?i)(sqlite|\.env|\.git/|control-state|project/|server/|report/|private/)")
        routes = self.manifest["routes"]
        self.assertNotIn("/api/me", routes)
        self.assertNotIn("/api/manage/users", routes)
        self.assertNotIn("/api/courses/live-cash/lessons/multiway", routes)

    def test_original_permission_denial_is_preserved(self):
        routes = self.manifest["routes"]
        self.assertEqual(routes["/api/qa/questions"]["status"], 401)
        self.assertEqual(routes["/api/community/posts?board=study"]["status"], 401)
        self.assertEqual(routes["/api/community/posts?board=general"]["body"]["posts"], [])

    def test_owned_mp4_and_browser_native_media_paths(self):
        expected = hashlib.sha256(SAMPLE.read_bytes()).hexdigest()
        self.assertEqual(self.manifest["sampleSha256"], expected)
        target = self.output / "public-media" / (self.manifest["sampleId"] + ".mp4")
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), expected)
        player = (self.output / "src/video-player.js").read_text()
        self.assertNotIn("/api/media/", player)
        self.assertIn('src="${e(previewMediaUrl(lesson.videoAssetId))}"', player)
        self.assertIn("player.src = previewMediaUrl(lesson.videoAssetId);", player)
        self.assertIn('await installStaticPreview', (self.output / 'src/preview-entry.js').read_text())

    def test_github_pages_subpath_rebases_all_root_asset_namespaces(self):
        for file in self.subpath.rglob("*"):
            if file.is_file() and file.suffix in (".html", ".js", ".css", ".svg"):
                self.assertNotRegex(file.read_text(), r'([\x22\x27`(])/(assets/|src/|proposal/|styles\.css|public-media/)', str(file))
        html = (self.subpath / "index.html").read_text()
        self.assertIn('src="/TPC-public-review/src/preview-entry.js"', html)
        self.assertIn('href="/TPC-public-review/styles.css"', html)
        self.assertIn('new URL("../", import.meta.url)', (self.subpath / "src/preview-entry.js").read_text())

    def test_no_credentials_form_or_fabricated_signup_in_preview(self):
        app = (self.output / "src/app.js").read_text()
        self.assertIn('function openAuth(mode = "login") {\n  return showPreviewInfo();', app)
        self.assertIn('path.startsWith("/account")', app)
        self.assertNotIn('(await renderIdentity(path, params, ctx, identityLinks)) ||', app)
        self.assertIn('data-preview="static"', (self.output / "index.html").read_text())
        self.assertNotIn('계정을 만들고 무료 강의부터 시작할 수 있습니다.', (self.output / 'src/platform-ui.js').read_text())

    def test_exported_module_dependencies_are_present(self):
        for source in (self.output / "src").glob("*.js"):
            for match in re.finditer(r'(?:from\s*|import\s*)[\x22\x27]([^\x22\x27]+)[\x22\x27]', source.read_text()):
                if match[1].startswith("."):
                    self.assertTrue((source.parent / match[1]).is_file(), (source.name, match[1]))

    def test_invalid_paths_and_existing_output_fail_without_deleting(self):
        for value in ("TPC", "//evil/", "/../", "/TPC?x=/", "/TPC<script>/"):
            with self.assertRaises(ValueError):
                validate_base(value)
        before = (self.output / "index.html").read_bytes()
        with self.assertRaises(ValueError):
            build(self.output)
        self.assertEqual((self.output / "index.html").read_bytes(), before)

    def test_core_application_sources_are_not_rewritten(self):
        self.assertNotIn("showPreviewInfo", (ROOT / "src/app.js").read_text())
        self.assertIn('src="/api/media/', (ROOT / "src/video-player.js").read_text())


if __name__ == "__main__":
    unittest.main()
