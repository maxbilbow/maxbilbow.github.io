# Max's App Archive

A static project showcase at <https://maxbilbow.github.io>. Each listed project
keeps ready-to-publish web files in its own repository. After a successful
`main`-branch build, it notifies this repository, which rebuilds and deploys the
complete archive with GitHub Pages.

## How it works

1. A source repository's build workflow completes successfully on `main`.
2. Its notification workflow sends a `project-built` repository dispatch with
   `client_payload.repository` and `client_payload.run_id`.
3. This repository verifies the run against the configured repository, workflow,
   branch, event, and successful conclusion.
4. For **every** listed project, the builder finds the latest successful `main`
   push or manual workflow run and downloads its exact commit. Only the
   configured documentation folder is copied to `/<slug>/`.
5. A generated homepage links to every project. One cumulative Pages artifact is
   deployed, so updating one project does not remove the others.

Pushes to this repository's `main` branch and manual workflow runs also rebuild
all projects. Pull requests run offline tests only, without private-source
credentials. Failed imports stop deployment and leave the currently published
site untouched. Removing a project from the list removes it on the next
successful deployment.

No frontend framework, package installation, database, or runtime service is
required. The builder uses Python's standard library.

## Initial setup

### 1. Configure GitHub Pages

In this repository's **Settings > Pages > Build and deployment**, select
**GitHub Actions** as the source. Keep `main` as the default branch: repository
dispatch events only run workflows present on the default branch. Ensure the
`github-pages` deployment environment permits `main`.

### 2. Grant read access to source repositories

Create the Actions secret `ARCHIVE_READ_TOKEN` in **this repository**. Use a
fine-grained personal access token scoped to the listed source repositories,
with:

- **Contents: read** (download the files at a commit).
- **Actions: read** (verify successful workflow runs).

Public-only imports can run without this secret, subject to GitHub's lower
unauthenticated API rate limit. The built-in `GITHUB_TOKEN` cannot read other
private repositories. Prefer a narrowly scoped, expiring token; rotate it before
expiry. Never put tokens in configuration files.

### 3. Configure each source repository

Prepper is initially configured as:

| Setting | Value |
| --- | --- |
| Repository | `maxbilbow/prepper` |
| Build workflow | `.github/workflows/ci.yml` |
| Branch | `main` |
| Static folder | `github.io/prepper` |
| Published URL | <https://maxbilbow.github.io/prepper/> |

The Prepper checkout includes a `CI` build/test workflow and a separate
[`publish-docs.yml`](https://github.com/maxbilbow/prepper/blob/main/.github/workflows/publish-docs.yml)
notification workflow. The notification uses `workflow_run` after `CI`
**completes**, rather than a step within CI (which would dispatch before the
build is recorded as successful). It excludes failed builds, pull requests,
and forks. Commit both source workflows to its default branch.

In **each source repository**, create `ARCHIVE_DISPATCH_TOKEN`: a fine-grained
personal access token scoped to **only `maxbilbow/maxbilbow.github.io`**, with
**Contents: read and write** permission, required by GitHub's repository-dispatch
API. This is separate from the archive's source-read credential.

For another project, copy Prepper's notification workflow, update
`workflows: [CI]` to its build workflow's display name, and configure that
workflow's filename in this repository's project list. The dispatch format is:

```json
{
  "event_type": "project-built",
  "client_payload": {
    "repository": "maxbilbow/prepper",
    "run_id": 123456789
  }
}
```

`run_id` is the completed **build** run ID, not the notification run ID. The
receiver verifies it through the API and rebuilds from the latest successful
runs, so delayed or repeated notifications cannot roll back a project.

### 4. Publish

Push the archive workflow and configuration to `main`, and commit the source
docs and workflows to Prepper's `main`. Once secrets are configured, push a
source change or manually run its CI workflow to trigger publication.
Alternatively, manually run **Build and deploy archive** after a successful
source build exists.

The first archive build will fail explicitly until **every configured project**
has a successful build containing its documentation. No placeholder import or
stale branch-tip fallback is used.

## Adding projects

Add an entry to [`projects.json`](projects.json):

```json
{
  "slug": "another-app",
  "name": "Another App",
  "description": "A short description for the homepage.",
  "repository": "maxbilbow/another-app",
  "branch": "main",
  "workflow": "ci.yml",
  "source": "github.io/another-app"
}
```

Slugs must be unique lowercase words/numbers separated by hyphens. `source` is
relative to the source repository root; it must contain `index.html`. The
workflow must be a YAML filename in `.github/workflows/`.

### Static-site contract

- Files must exist in the **successful build commit**. Files generated only in
  a runner or uploaded as workflow artifacts are not imported. Generate and
  commit them before the source build, if needed.
- Use relative links such as `./assets/app.css`, or links prefixed with the
  published project path (e.g. `/prepper/assets/app.css`). Root-relative links
  like `/assets/app.css` point to the archive root and will not be rewritten.
- Use plain static files. Markdown must be rendered to HTML before committing;
  no Jekyll, server-side code, or automatic SPA route fallback is provided.
- Symlinks, hard links, special files, and traversal paths are rejected.
- Only explicitly configured folders are published, but **everything inside
  those folders is public**, even for a private source repository. Never include
  secrets, personal data, or confidential files.
- Imported HTML and JavaScript share the site's origin. Only list repositories
  whose documentation you trust to run on `maxbilbow.github.io`.

The deployed [`projects.json`](https://maxbilbow.github.io/projects.json)
records each project's imported commit SHA and build run ID. Repository names
and those commit identifiers are public too.

## Local development and checks

Requires Python 3.10 or later:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/build.py
python3 -m http.server 8000 --directory _site
```

For private imports, provide `ARCHIVE_READ_TOKEN` through your environment
without committing it or writing it into shell history. Builds use the GitHub
API, not local source checkouts. Open <http://localhost:8000>.

The builder refuses to overwrite an existing output directory. Choose a fresh
one for subsequent builds:

```sh
python3 scripts/build.py --output /tmp/app-archive-preview
```

The offline test suite covers commit selection, dispatch verification,
cumulative builds, binary assets, publication boundaries, invalid paths,
redirect credential stripping, and all-or-nothing output.
