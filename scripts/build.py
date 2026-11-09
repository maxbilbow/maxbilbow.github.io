"""Aggregate explicitly configured static sites from successful GitHub builds."""

import argparse
import html
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class BuildError(Exception):
    pass


class SafeRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise BuildError("Refusing a non-HTTPS GitHub archive redirect")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


class GitHub:
    def __init__(self, token):
        self.token = token
        self.opener = build_opener(SafeRedirects())

    def get(self, path, *, binary=False):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "maxbilbow-project-archive",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(f"https://api.github.com{path}", headers=headers)
        try:
            with self.opener.open(request, timeout=120) as response:
                body = response.read()
        except HTTPError as error:
            raise BuildError(
                f"GitHub request {path} failed with HTTP {error.code}; "
                "check the repository, workflow, and token permissions"
            ) from error
        except URLError as error:
            raise BuildError(f"GitHub request {path} failed: {error.reason}") from error
        return body if binary else json.loads(body)


def load_projects(path):
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise BuildError("Configuration must be an object")
    projects = config["projects"]
    if not isinstance(projects, list):
        raise BuildError("projects must be a list")
    slugs = set()
    for project in projects:
        if not isinstance(project, dict):
            raise BuildError("Each project must be an object")
        for field in ("slug", "name", "description", "repository", "branch", "workflow", "source"):
            if not isinstance(project.get(field), str) or not project[field].strip():
                raise BuildError(f"Each project needs a nonempty {field}")
        slug = project["slug"]
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) or slug in slugs:
            raise BuildError(f"Invalid or duplicate project slug: {slug}")
        slugs.add(slug)
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", project["repository"], flags=re.ASCII):
            raise BuildError("repository must be owner/name")
        if not re.fullmatch(r"[\w.-]+\.ya?ml", project["workflow"], flags=re.ASCII):
            raise BuildError("workflow must be a YAML filename")
        source = project["source"]
        if source.startswith("/") or "\\" in source or any(
            part in ("", ".", "..") for part in source.split("/")
        ):
            raise BuildError("source must be a relative folder without traversal")
        if project["branch"] != "main":
            raise BuildError("Only main-branch builds may be published")
    return projects


def eligible_run(run, project):
    return (
        run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and run.get("head_branch") == project["branch"]
        and run.get("event") in ("push", "workflow_dispatch")
        and (run.get("head_repository") or {}).get("full_name", "").lower()
        == project["repository"].lower()
        and run.get("path") == f".github/workflows/{project['workflow']}"
        and re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", "")) is not None
    )


def latest_success(github, project):
    base = f"/repos/{project['repository']}/actions/workflows/{project['workflow']}/runs"
    page = 1
    while True:
        query = urlencode({
            "branch": project["branch"], "status": "success", "per_page": 100, "page": page
        })
        runs = github.get(f"{base}?{query}")["workflow_runs"]
        for run in runs:
            if eligible_run(run, project):
                return run
        if len(runs) < 100:
            raise BuildError(
                f"{project['repository']}: no successful main-branch "
                f"{project['workflow']} push/manual build found"
            )
        page += 1


def validate_dispatch(github, projects, event):
    payload = event.get("client_payload", {})
    if not isinstance(payload, dict):
        raise BuildError("Dispatch client_payload must be an object")
    repository = payload.get("repository")
    project = next(
        (project for project in projects if project["repository"] == repository), None
    )
    run_id = payload.get("run_id")
    if project is None or not re.fullmatch(r"[0-9]+", str(run_id)):
        raise BuildError("Dispatch requires a configured repository and numeric run_id")
    run = github.get(f"/repos/{repository}/actions/runs/{run_id}")
    if not eligible_run(run, project):
        raise BuildError("Dispatch does not identify a successful configured main-branch build")


def copy_site(archive, source, destination):
    destination.mkdir()
    copied = set()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        roots = {member.name.split("/")[0] for member in bundle.getmembers()}
        if len(roots) != 1:
            raise BuildError("Expected one repository root in GitHub archive")
        prefix = f"{roots.pop()}/{source}/"
        for member in bundle.getmembers():
            if not member.name.startswith(prefix):
                continue
            relative = member.name[len(prefix):]
            if not relative:
                continue
            parts = PurePosixPath(relative).parts
            if relative.startswith("/") or "\\" in relative or ".." in parts:
                raise BuildError(f"Unsafe documentation path: {relative}")
            target = destination.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                if relative in copied:
                    raise BuildError(f"Duplicate documentation file: {relative}")
                copied.add(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = bundle.extractfile(member)
                if stream is None:
                    raise BuildError(f"Cannot read documentation file: {relative}")
                with stream, target.open("wb") as output:
                    shutil.copyfileobj(stream, output)
            else:
                raise BuildError(f"Documentation must not contain links or special files: {relative}")
    if not (destination / "index.html").is_file():
        raise BuildError(f"{source} must contain index.html in the successful build commit")


def render_index(projects):
    cards = []
    for project in projects:
        name, description, repository = (
            html.escape(project[key], quote=True) for key in ("name", "description", "repository")
        )
        cards.append(
            f'<li class="card"><h2><a href="./{project["slug"]}/">{name}</a></h2>'
            f'<p>{description}</p><a class="source" href="https://github.com/{repository}">'
            f'{repository} <span aria-hidden="true">&rarr;</span></a></li>'
        )
    content = '<ul class="projects">' + "".join(cards) + "</ul>" if cards else (
        '<p class="empty">Projects are on their way. Check back soon.</p>'
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Explore Max's apps, experiments, and project documentation.">
  <title>Max's App Archive</title>
  <link rel="stylesheet" href="./style.css">
</head>
<body>
  <main>
    <header>
      <p class="eyebrow">APPS / EXPERIMENTS / DOCUMENTATION</p>
      <h1>Max's App Archive<span aria-hidden="true">.</span></h1>
      <p class="intro">A collection of things I've built. Pick a project to explore its app and documentation.</p>
    </header>
    {content}
    <footer><a href="https://github.com/maxbilbow">Find more on GitHub &rarr;</a></footer>
  </main>
</body>
</html>
"""


def build(github, projects, output, assets):
    if output.exists():
        raise BuildError(f"Output already exists: {output}; choose a fresh output directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".archive-", dir=output.parent) as temporary:
        stage = Path(temporary) / "site"
        stage.mkdir()
        provenance = []
        for project in projects:
            run = latest_success(github, project)
            sha = run["head_sha"]
            archive = github.get(f"/repos/{project['repository']}/tarball/{sha}", binary=True)
            copy_site(archive, project["source"], stage / project["slug"])
            provenance.append({
                "slug": project["slug"], "repository": project["repository"],
                "commit": sha, "run_id": run["id"],
            })
            print(f"Imported {project['repository']}@{sha} -> /{project['slug']}/")
        (stage / "index.html").write_text(render_index(projects), encoding="utf-8")
        shutil.copyfile(assets / "style.css", stage / "style.css")
        (stage / ".nojekyll").touch()
        (stage / "projects.json").write_text(
            json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
        )
        stage.rename(output)


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=root / "projects.json")
    parser.add_argument("--output", type=Path, default=root / "_site")
    args = parser.parse_args()
    try:
        projects = load_projects(args.config)
        github = GitHub(os.environ.get("ARCHIVE_READ_TOKEN"))
        if os.environ.get("GITHUB_EVENT_NAME") == "repository_dispatch":
            event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
            validate_dispatch(github, projects, event)
        build(github, projects, args.output, root / "site")
    except (BuildError, KeyError, TypeError, ValueError, OSError, tarfile.TarError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
