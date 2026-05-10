---
name: publish
description: Cut a new release of cz-mtg-compare-mcp to PyPI. Bumps the version in pyproject.toml, commits, tags, and pushes — the "Publish to PyPI" GitHub Actions workflow handles building, smoke-testing, and uploading via Trusted Publishing once the user approves the `pypi` environment in the Actions UI. Use when the user says "publish", "release", "cut a release", or similar.
disable-model-invocation: true
---

# Publish a new version

This skill drives a release of `cz-mtg-compare-mcp` to PyPI. The actual publishing is done by the `.github/workflows/publish.yml` workflow on tag push — it builds an sdist + wheel, smoke-tests the wheel install, and uploads via PyPI Trusted Publishing (OIDC, no stored token). The workflow pauses at a manual approval gate on the `pypi` environment.

The skill itself only takes the local steps that lead up to the tag push. It does NOT run `twine upload` or any direct publish command.

## Steps

### 1. Pre-flight checks (fail fast)

Run these and stop if any fail. Surface the failure clearly to the user — don't try to "fix" by stashing or force-pushing.

- Working tree must be clean: `git status --porcelain` returns nothing.
- Branch must be `main`: `git rev-parse --abbrev-ref HEAD` == `main`.
- Local `main` must be up to date with `origin/main`: `git fetch origin` then check `git rev-list --count main..origin/main` == 0. If behind, ask the user to pull/rebase first.
- All tests pass: `.venv/bin/pytest -q` (NOT `-m live` — those hit real shops). The default suite must be green.

### 2. Pick the bump type

Read the current version from `pyproject.toml` (the `version = "X.Y.Z"` line under `[project]`).

Ask the user **which semantic-versioning bump** to apply (use AskUserQuestion):

- **patch** (e.g. `0.1.0 → 0.1.1`) — bug fixes only, no behavioural changes.
- **minor** (e.g. `0.1.0 → 0.2.0`) — new features, backwards compatible.
- **major** (e.g. `0.1.0 → 1.0.0`) — breaking changes (tool signatures change, removed fields, env-var renames, etc.).

Compute the new version string from the chosen bump.

### 3. Draft the release notes

Get the commits since the last tag:

```bash
git describe --tags --abbrev=0 2>/dev/null
# if a previous tag exists:
git log <last-tag>..HEAD --pretty=format:"- %s"
# if no tags yet:
git log --pretty=format:"- %s"
```

Draft a short release note:

- One-sentence summary of the headline change.
- Bullet list of the user-facing items only (skip internal refactors, doc-only commits, test-only commits unless they're regression-coverage worth flagging).
- Group as `Added` / `Changed` / `Fixed` / `Removed` if there are enough entries to warrant grouping.

Show the draft to the user and confirm before continuing.

### 4. Bump the version

Edit `pyproject.toml` and replace the `version = "<old>"` line with the new version. Use the Edit tool with a unique `old_string` that includes the surrounding lines so it doesn't match `requires-python` or any other version-like field.

### 5. Commit the bump

```bash
git add pyproject.toml
git commit -m "Release vX.Y.Z"
```

Use a plain commit message — the release notes go on the tag, not the commit. Include the standard `Co-Authored-By` footer.

### 6. Tag the release

Create an **annotated** tag with the release notes from step 3 as the message. Use a HEREDOC for proper formatting:

```bash
git tag -a vX.Y.Z -m "$(cat <<'EOF'
Release vX.Y.Z - <one-line summary>

<bulleted release notes>
EOF
)"
```

### 7. Push main + tag

```bash
git push origin main vX.Y.Z
```

This triggers `.github/workflows/publish.yml`. The build job runs unattended (~30 seconds: sdist + wheel + install smoke test); the publish job then pauses on the `pypi` environment approval gate.

### 8. Surface the approval URL

Print the URL the user needs to open and explicitly tell them what to do:

```
https://github.com/xvyslo05/czech-mtg-price-comparator/actions
```

> Open the latest run, click **Review deployments** → tick `pypi` → **Approve and deploy**.

Wait for the user to confirm they've approved. Don't poll the workflow yourself — let them drive.

### 9. Verify the publish landed

After the user confirms approval, poll PyPI until the new version shows up (timeout ~120 seconds, 5-second intervals):

```bash
.venv/bin/python -c "import httpx; print(httpx.get('https://pypi.org/pypi/cz-mtg-compare-mcp/json', timeout=10).json()['info']['version'])"
```

When it matches the new version, surface:

- The PyPI URL: `https://pypi.org/project/cz-mtg-compare-mcp/<NEW_VERSION>/`
- A reminder that anyone using uvx will pick it up automatically on next `--refresh`, but cached installs need `uvx --refresh-package cz-mtg-compare-mcp cz-mtg-compare-mcp` to upgrade.

## Hard rules — DO NOT

- **Do NOT** run `twine upload`, `python -m twine upload`, or any other direct publish command. The GitHub Actions workflow has the only Trusted Publisher binding.
- **Do NOT** push the tag with `--force` or `--force-with-lease`. If a tag of the same name already exists, surface the conflict and stop — don't rewrite history.
- **Do NOT** push with `--no-verify` to skip hooks.
- **Do NOT** delete a tag that's already been pushed without explicit user direction. Yanking a release is a destructive act and PyPI doesn't allow re-uploading the same version.
- **Do NOT** skip the test run. A broken release that publishes 5 minutes later costs more than the 1 second the test suite takes.
- **Do NOT** invent commit messages or release notes. Always derive them from `git log` and confirm with the user.
