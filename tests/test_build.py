import copy
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from urllib.request import Request

from scripts.build import (
    BuildError, SafeRedirects, build, copy_site, eligible_run, latest_success,
    load_projects, render_index, validate_dispatch,
)


ROOT = Path(__file__).resolve().parents[1]
PROJECT = {
    "slug": "example", "name": "Example", "description": "A project",
    "repository": "owner/example", "branch": "main", "workflow": "ci.yml",
    "source": "github.io/example",
}
RUN = {
    "id": 123, "status": "completed", "conclusion": "success", "head_branch": "main",
    "head_sha": "a" * 40, "event": "push", "path": ".github/workflows/ci.yml",
    "head_repository": {"full_name": "owner/example"},
}


def archive(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        for path, content in files.items():
            member = tarfile.TarInfo(f"repo-commit/{path}")
            if content is None:
                member.type = tarfile.SYMTYPE
                member.linkname = "/etc/passwd"
                bundle.addfile(member)
            else:
                member.size = len(content)
                bundle.addfile(member, io.BytesIO(content))
    return buffer.getvalue()


class FakeGitHub:
    def __init__(self, runs=None, data=None):
        self.runs = [RUN] if runs is None else runs
        self.data = archive({
            "github.io/example/index.html": b"<h1>Example</h1>",
            "github.io/example/assets/icon.bin": b"\x00\xff",
            "private.txt": b"not published",
        }) if data is None else data
        self.calls = []

    def get(self, path, *, binary=False):
        self.calls.append(path)
        if binary:
            return self.data
        if "/actions/runs/" in path:
            return self.runs[0]
        return {"workflow_runs": self.runs}


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_configured_projects_are_valid(self):
        self.assertEqual(load_projects(ROOT / "projects.json")[0]["slug"], "prepper")

    def test_rejects_invalid_configuration(self):
        for changes in (
            {"source": "../secret"}, {"source": "/tmp/docs"}, {"source": "docs//app"},
            {"source": "docs/./app"}, {"source": "docs\\app"},
            {"slug": "../escape"}, {"branch": "develop"},
            {"repository": "owner/repo/extra"}, {"workflow": "../ci.yml"},
            {"name": ""},
        ):
            with self.subTest(changes=changes):
                config = self.root / "projects.json"
                config.write_text(json.dumps({"projects": [dict(PROJECT, **changes)]}))
                with self.assertRaises(BuildError):
                    load_projects(config)
        config.write_text(json.dumps({"projects": [PROJECT, PROJECT]}))
        with self.assertRaisesRegex(BuildError, "duplicate"):
            load_projects(config)

    def test_only_successful_configured_main_builds_qualify(self):
        self.assertTrue(eligible_run(RUN, PROJECT))
        for changes in (
            {"head_branch": "feature"}, {"status": "in_progress"},
            {"conclusion": "failure"}, {"event": "pull_request"},
            {"path": ".github/workflows/other.yml"}, {"head_sha": "main"},
            {"head_repository": {"full_name": "someone/fork"}}, {"head_repository": None},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(eligible_run(dict(RUN, **changes), PROJECT))

    def test_selects_latest_eligible_run_and_fails_without_one(self):
        github = FakeGitHub(runs=[dict(RUN, event="pull_request"), RUN])
        self.assertEqual(latest_success(github, PROJECT), RUN)
        self.assertIn("branch=main&status=success", github.calls[0])
        with self.assertRaisesRegex(BuildError, "no successful"):
            latest_success(FakeGitHub(runs=[]), PROJECT)

    def test_paginates_past_ineligible_runs(self):
        class PaginatedGitHub(FakeGitHub):
            def get(self, path, *, binary=False):
                self.calls.append(path)
                return {"workflow_runs": (
                    [dict(RUN, event="pull_request")] * 100 if "&page=1" in path else [RUN]
                )}
        github = PaginatedGitHub()
        self.assertEqual(latest_success(github, PROJECT), RUN)
        self.assertEqual(len(github.calls), 2)

    def test_validates_dispatch_against_github(self):
        event = {"client_payload": {"repository": "owner/example", "run_id": 123}}
        github = FakeGitHub()
        validate_dispatch(github, [PROJECT], event)
        self.assertEqual(github.calls, ["/repos/owner/example/actions/runs/123"])
        for payload in (
            {}, {"repository": "other/repo", "run_id": 123},
            {"repository": "owner/example", "run_id": "../123"},
        ):
            with self.subTest(payload=payload), self.assertRaises(BuildError):
                validate_dispatch(github, [PROJECT], {"client_payload": payload})
        with self.assertRaises(BuildError):
            validate_dispatch(FakeGitHub(runs=[dict(RUN, conclusion="failure")]), [PROJECT], event)

    def test_build_uses_exact_commit_and_only_selected_folder(self):
        github = FakeGitHub()
        output = self.root / "site"
        build(github, [PROJECT], output, ROOT / "site")
        self.assertIn(f"/repos/owner/example/tarball/{RUN['head_sha']}", github.calls)
        self.assertEqual((output / "example/assets/icon.bin").read_bytes(), b"\x00\xff")
        self.assertFalse((output / "private.txt").exists())
        self.assertIn('./example/', (output / "index.html").read_text())
        self.assertTrue((output / ".nojekyll").is_file())
        self.assertTrue((output / "style.css").is_file())
        self.assertEqual(json.loads((output / "projects.json").read_text())[0]["commit"], RUN["head_sha"])

    def test_cumulative_build_preserves_all_projects(self):
        second = dict(PROJECT, slug="second", name="Second")
        output = self.root / "site"
        build(FakeGitHub(), [PROJECT, second], output, ROOT / "site")
        for slug in ("example", "second"):
            self.assertTrue((output / slug / "index.html").is_file())
            self.assertIn(f'./{slug}/', (output / "index.html").read_text())

    def test_failure_does_not_leave_partial_site(self):
        github = FakeGitHub(data=archive({"README.md": b"Missing docs"}))
        output = self.root / "site"
        with self.assertRaisesRegex(BuildError, "index.html"):
            build(github, [PROJECT], output, ROOT / "site")
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_later_import_failure_does_not_publish_earlier_project(self):
        class LaterFailure(FakeGitHub):
            def get(self, path, *, binary=False):
                if binary and any("/tarball/" in call for call in self.calls):
                    return archive({"README.md": b"Missing docs"})
                return super().get(path, binary=binary)

        output = self.root / "site"
        with self.assertRaisesRegex(BuildError, "index.html"):
            build(
                LaterFailure(), [PROJECT, dict(PROJECT, slug="second")],
                output, ROOT / "site",
            )
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_existing_output_is_never_overwritten(self):
        output = self.root / "site"
        output.mkdir()
        (output / "keep.txt").write_text("keep")
        with self.assertRaisesRegex(BuildError, "already exists"):
            build(FakeGitHub(), [PROJECT], output, ROOT / "site")
        self.assertEqual((output / "keep.txt").read_text(), "keep")

    def test_rejects_links_and_path_traversal(self):
        for relative, content in (("../escape", b"bad"), ("link", None)):
            with self.subTest(relative=relative):
                data = archive({f"github.io/example/{relative}": content})
                destination = self.root / "output"
                with self.assertRaises(BuildError):
                    copy_site(data, PROJECT["source"], destination)
                destination.rmdir()
        self.assertFalse((self.root / "escape").exists())

    def test_renders_safe_metadata_and_empty_state(self):
        project = copy.deepcopy(PROJECT)
        project["name"] = '<script>alert("test")</script>'
        page = render_index([project])
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("Projects are on their way", render_index([]))

    def test_archive_redirect_strips_authorization_and_rejects_http(self):
        request = Request("https://api.github.com/archive", headers={"Authorization": "test-token"})
        redirected = SafeRedirects().redirect_request(
            request, None, 302, "Found", {}, "https://codeload.github.com/archive"
        )
        self.assertIsNone(redirected.get_header("Authorization"))
        with self.assertRaises(BuildError):
            SafeRedirects().redirect_request(
                request, None, 302, "Found", {}, "http://example.com/archive"
            )


if __name__ == "__main__":
    unittest.main()
