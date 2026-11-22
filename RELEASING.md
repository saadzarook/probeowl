# Releasing ProbeOwl

How a release is made by hand, as v0.1.0 and v0.2.0 were. Each step names what to check before
the next. The automated route, for later releases, is at the end.

The steps, in order:

| Step | What | Section |
| --- | --- | --- |
| 1 | Pre-flight on `develop`, then merge to `main` | [1. Pre-flight](#1-pre-flight) |
| 2 | Build the wheel, and check what's in it | [2. Build the wheel](#2-build-the-wheel-and-check-whats-in-it) |
| 3 | Install it as a newcomer would; the wheel check | [3. Install it](#3-install-it-as-a-newcomer-would) |
| 4 | Upload the wheel from step 2 to PyPI | [4. Publish to PyPI](#4-publish-to-pypi) |
| 5 | Tag `main`, and the GitHub release with the wheel attached | [5. Tag](#5-tag-and-create-the-github-release) |
| 6 | Build, deploy and check the site | [6. Deploy the site](#6-deploy-the-site-once-the-repository-is-public) |

Every command below uses `$VERSION`, the release's version as `backend/pyproject.toml` says it.
Set it once, in the shell you release from, at the repository's root:

```sh
VERSION="$(cd backend && uv version --short)"     # such as 0.2.1
```

A release is one wheel, `probeowl-<version>-py3-none-any.whl`. It carries the web app, its licences
(`THIRD_PARTY_NOTICES.txt` and the IBM Plex OFL files) and no mocks, and the guide, built to be
served by `owl serve` at `/guide/` with no call outside the machine, with Material for MkDocs'
licence. It is published to PyPI, so
`uv tool install probeowl` installs it, and attached to the GitHub release. There is no sdist: the
web app is built from `web/`, which an sdist of `backend/` would not carry.

## 1. Pre-flight

On `develop`, with everything for the release merged:

1. **Version.** `version` in `backend/pyproject.toml` is the release's. `$VERSION` (above) is
   read from it, and the web app's sidebar reads the same value.
2. **CHANGELOG.** Its section is complete, and its heading has the date:
   `## [$VERSION] - <date>`, such as `## [0.2.1] - 2026-11-20`. `[Unreleased]` stays above it,
   empty.
3. **A full native run.** Stop your own `owl serve` first: the live step uses 7878 and 4173, and
   stops whatever holds them. Then run every step on the release commit:

   ```sh
   bash dev/native/verify.sh --tests --exit --wsl --live
   ```

   Every step passes, and full CI is green on the release commit.
4. **A secrets scan** over every commit on every ref, findings redacted
   ([dev/README.md](dev/README.md) explains the ignore file):

   ```sh
   gitleaks git --log-opts="--all" --redact --gitleaks-ignore-path dev/security/.gitleaksignore .
   ```

   It finds nothing. Anything it finds is a leak to fix before going further.
5. **Merge to `main`.** Open a pull request from `develop` to `main`. It merges once its required
   checks pass. `main` holds
   releases: the tag goes on its merge commit.

## 2. Build the wheel, and check what's in it

From a fresh clone of `main`'s merge commit (so no stray `web/dist` or `node_modules`), with
Node.js installed. Two builds from the same commit and the same `core.autocrlf` give the same
file, byte for byte: on Windows, clone with `-c core.autocrlf=true`, as the release builds so far.

```sh
cd backend
uv build --wheel -o ../dist
```

`hatch_build.py` runs `npm ci` and the production build, and adds the web app and its licences.
Then `guide_build.py` builds the guide with the MkDocs pinned in `guide/requirements.txt`, through
the uv running the build. Then check the wheel:

```sh
python - <<'EOF'
import glob, zipfile
[wheel] = glob.glob("../dist/probeowl-*.whl")
names = zipfile.ZipFile(wheel).namelist()
for needed in ("probeowl/webapp/index.html",
               "probeowl/webapp/licenses/THIRD_PARTY_NOTICES.txt",
               "probeowl/webapp/licenses/ibm-plex-sans-OFL.txt",
               "probeowl/webapp/licenses/ibm-plex-mono-OFL.txt",
               "probeowl/guide_site/index.html",
               "probeowl/guide_site/several-systems/index.html",
               "probeowl/guide_site/assets/plex.css",
               "probeowl/guide_site/licenses/MKDOCS_MATERIAL_LICENSE.txt"):
    assert needed in names, f"missing {needed}"
pages = [n for n in names if n.startswith("probeowl/guide_site/") and n.endswith(".html")]
for page in pages:
    text = zipfile.ZipFile(wheel).read(page).decode()
    assert "fonts.googleapis" not in text and "api.github.com" not in text, f"{page} calls out"
assert not any(n.endswith(".map") for n in names), "a source map shipped"
assert not any("mockServiceWorker" in n for n in names), "the mock service worker shipped"
assert not any("/assets/browser-" in n for n in names), "the mocks chunk shipped"
print(wheel, "OK:", len(names), "files")
EOF
```

## 3. Install it as a newcomer would

In a fresh tool environment, away from your own:

```sh
export UV_TOOL_DIR="$(mktemp -d)" UV_TOOL_BIN_DIR="$(mktemp -d)"
uv tool install "../dist/probeowl-$VERSION-py3-none-any.whl"
"$UV_TOOL_BIN_DIR/owl" --version                     # owl $VERSION
mkdir -p /tmp/owl-empty
PROBEOWL_STORE_URL="sqlite:///$UV_TOOL_DIR/store.db" "$UV_TOOL_BIN_DIR/owl" store init
PROBEOWL_STORE_URL="sqlite:///$UV_TOOL_DIR/store.db" "$UV_TOOL_BIN_DIR/owl" serve --config /tmp/owl-empty --port 7999
```

Open <http://127.0.0.1:7999>. You should see "No applications yet", the sidebar reading
`v$VERSION · local`, and no errors in the browser's console. The notices are at
<http://127.0.0.1:7999/licenses/THIRD_PARTY_NOTICES.txt>. Open the empty state's Getting started
link: the guide opens at <http://127.0.0.1:7999/guide/getting-started/>, in IBM Plex, with no
errors in the console, and its search finds "replica". Stop it, then remove both folders.

Then the release check, on the same commit: `bash dev/release/wheel_check.sh` installs the wheel
with `uv tool install` and a fresh resolution, runs `owl init` for a personal and a Production
setup, `owl --version`, `owl check` and `owl serve`, and passes. CI runs the same check (the
`wheel` workflow) on every pull request that changes what goes in the wheel (packaging, the CLI,
the build hooks, the web app's and the guide's dependencies) and on every push to `main`.

**Every release, v0.1 to v0.5, gets the same three before it goes out:** this wheel check, the
native runs on each affected dialect (step 1), and the maintainer's newcomer retest on the release
wheel. From v0.2 each release carries one phase of No YAML required; see the roadmap's Releases.

## 4. Publish to PyPI

Upload the wheel built and checked in [step 2](#2-build-the-wheel-and-check-whats-in-it),
`../dist/probeowl-$VERSION-py3-none-any.whl`, from that clone's `backend/` folder. The token is
never written to a file, a script or your shell history.

**Which token.** The very first upload (0.1.0) used a token scoped to your whole PyPI account,
because the project didn't exist yet, and step 4 below replaced it. Every upload since uses the
token scoped to the `probeowl` project, from your password manager.

1. Have the project-scoped token ready. (First upload only: create one scoped to your entire
   account on <https://pypi.org/manage/account/token/>.)
2. Upload, entering the token at uv's hidden prompt (username `__token__`):

   ```sh
   uv publish "../dist/probeowl-$VERSION-py3-none-any.whl"
   ```

   Or set it only in that terminal, without echoing it, and clear it afterwards:

   ```sh
   read -rs UV_PUBLISH_TOKEN && export UV_PUBLISH_TOKEN
   uv publish "../dist/probeowl-$VERSION-py3-none-any.whl"
   unset UV_PUBLISH_TOKEN
   ```

   In Windows PowerShell 5.1, which has no `-MaskInput`:
   `$env:UV_PUBLISH_TOKEN = [Net.NetworkCredential]::new('', (Read-Host -AsSecureString)).Password`,
   then `Remove-Item Env:UV_PUBLISH_TOKEN` when done.
3. Check <https://pypi.org/project/probeowl/> shows `$VERSION`. Then install it from PyPI in a
   fresh tool environment, as in step 3, with `uv tool install probeowl`.
4. **First upload only: replace the token.** Delete the account-wide token, and create one scoped
   to the `probeowl` project for any later manual upload. Keep it in your password manager only.

## 5. Tag, and create the GitHub release

On `main`'s merge commit, from the clone's `backend/` folder, as in step 4. First the notes: the
CHANGELOG's `$VERSION` section, ending with the wheel's sha256 on a line of its own, so anyone can
compare what they download:

```sh
SHA="$(sha256sum "../dist/probeowl-$VERSION-py3-none-any.whl" | cut -d' ' -f1)"
awk -v head="## [$VERSION]" 'index($0, head) == 1 { on = 1; print; next } on && /^## \[/ { exit } on' \
  ../CHANGELOG.md > notes.md
printf '\nsha256: %s\n' "$SHA" >> notes.md
tail -n 1 notes.md                     # sha256: <the hash PyPI shows for the wheel>
```

Then the tag and the release:

```sh
git tag -a "v$VERSION" -m "ProbeOwl $VERSION"
git push origin "v$VERSION"
gh release create "v$VERSION" "../dist/probeowl-$VERSION-py3-none-any.whl" \
  --title "ProbeOwl $VERSION" --notes-file notes.md --verify-tag --prerelease
gh release view "v$VERSION" --json assets --jq '.assets[].name'   # lists the wheel
gh release view "v$VERSION" --json body --jq .body | tail -n 1    # sha256: $SHA
gh api "repos/saadzarook/probeowl/releases/tags/v$VERSION" --jq '.assets[].digest'   # sha256:$SHA
```

`--prerelease` matches "pre-release" on the landing page; leave it off once a release is meant for
general use.

The last three commands must list `probeowl-$VERSION-py3-none-any.whl`, end the notes with
`sha256: $SHA`, and give GitHub's digest of the attached wheel as the same hash. If the notes lack
the line, add it with `gh release edit "v$VERSION" --notes-file notes.md`, which changes nothing
else. If the release was created without the wheel, attach it:

```sh
gh release upload "v$VERSION" "../dist/probeowl-$VERSION-py3-none-any.whl"
```

## 6. Deploy the site, once the repository is public

GitHub Pages serves the landing page (`site/index.html`) at the root,
<https://saadzarook.github.io/probeowl/>, and the guide under `/guide/`. The guide's address comes
from `GUIDE_URL` in `backend/probeowl/guide.py`, which `guide/hooks.py` gives MkDocs.

Plain `mkdocs gh-deploy` must not be used: it would replace the landing page with the guide, and
build the guide with Google Fonts and the GitHub widget. Build and check what Pages serves, then
publish it. From the repo root, after `npm ci` in `web/` and `npx playwright install chromium`:

```sh
stage="$(mktemp -d)"
bash dev/site/build.sh "$stage"     # landing page, its fonts and the offline guide, checked in a browser
bash dev/site/deploy.sh "$stage"    # force-pushes it to gh-pages
node dev/site/check_stage.mjs https://saadzarook.github.io/probeowl/
```

`build.sh` builds the guide exactly as the wheel does, offline, and only its address differs. It
fails if any page requests something from another site, logs a console error or gets an error
response, or if the guide's search finds nothing. The `gh-pages` branch holds only the built site,
so replacing it each time is intended. `deploy.sh` commits as the repository's git identity, and
refuses an email that isn't a GitHub noreply address, which GitHub would refuse to push for an
account that keeps its email private. In a fresh clone, set it first:
`git config --local user.email <id>+<login>@users.noreply.github.com`. In the repository's **Settings → Pages**, the source is
**Deploy from a branch**, `gh-pages`, `/ (root)`. Pages takes a minute or two to update; then the
last command checks the live site the same way, the landing page and every page of the guide.

## Later: the automated route

Two draft pull requests replace steps 4 to 6 once Actions is on. Each touches `.github/workflows/`,
so each needs an "approved #N" before it merges.

**#213, the release workflow.** It runs on a version tag. It checks the tag matches
`uv version`, builds and checks the wheel as in step 2, publishes it to PyPI by **Trusted
Publishing** (a short-lived token from GitHub's OIDC, so nothing is stored), and creates the GitHub
release with the wheel. To switch:

1. On PyPI, open the `probeowl` project's **Publishing** settings and add a trusted publisher:
   repository `saadzarook/probeowl`, workflow `release.yml`, environment `pypi`.
2. On GitHub, create the `pypi` environment. Requiring a reviewer on it makes every publish wait
   for a person's approval.
3. Merge #213 after "approved #213". Delete the project-scoped token from step 4: nothing needs it
   any more.
4. From then on, a release is steps 1 to 3, then `git tag` and `git push origin v<version>`. The
   workflow does the rest.

**#207, the site workflow.** It builds the site exactly as step 6 does, the guide under `/guide/`
and the landing page at the root, and deploys it with GitHub's Pages actions on every push to `main`
that changes the site. To switch:

1. In **Settings → Pages**, change the source to **GitHub Actions**.
2. Merge #207 after "approved #207". The `gh-pages` branch is no longer used, and can be deleted.
