# Releasing ProbeOwl

How a release is made by hand, as v0.1.0 is. Each step names what to check before the next. The
automated route, for later releases, is at the end.

A release is one wheel, `probeowl-<version>-py3-none-any.whl`. It carries the web app, its licences
(`THIRD_PARTY_NOTICES.txt` and the IBM Plex OFL files) and no mocks, and the guide, built to be
served by `owl serve` at `/guide/` with no call outside the machine, with Material for MkDocs'
licence. It is published to PyPI, so
`uv tool install probeowl` installs it, and attached to the GitHub release. There is no sdist: the
web app is built from `web/`, which an sdist of `backend/` would not carry.

## 1. Pre-flight

On `develop`, with everything for the release merged:

1. **Version.** `version` in `backend/pyproject.toml` is the release's, such as `0.1.0`.
   `cd backend && uv version --short` prints it. The web app's sidebar reads the same value.
2. **CHANGELOG.** Its section is complete, and its heading has the date:
   `## [0.1.0] - 2026-11-15`. `[Unreleased]` stays above it, empty.
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

From a clean checkout of `main` (a fresh worktree, so no stray `web/dist` or `node_modules`),
with Node.js installed:

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
uv tool install ../dist/probeowl-0.1.0-py3-none-any.whl
"$UV_TOOL_BIN_DIR/owl" --version                     # owl 0.1.0
mkdir -p /tmp/owl-empty
PROBEOWL_STORE_URL="sqlite:///$UV_TOOL_DIR/store.db" "$UV_TOOL_BIN_DIR/owl" store init
PROBEOWL_STORE_URL="sqlite:///$UV_TOOL_DIR/store.db" "$UV_TOOL_BIN_DIR/owl" serve --config /tmp/owl-empty --port 7999
```

Open <http://127.0.0.1:7999>. You should see "No applications yet", the sidebar reading
`v0.1.0 · local`, and no errors in the browser's console. The notices are at
<http://127.0.0.1:7999/licenses/THIRD_PARTY_NOTICES.txt>. Open the empty state's Getting started
link: the guide opens at <http://127.0.0.1:7999/guide/getting-started/>, in IBM Plex, with no
errors in the console, and its search finds "replica". Stop it, then remove both folders.

Then the release check, on the same commit: `bash dev/release/wheel_check.sh` installs the wheel
with `uv tool install` and a fresh resolution, runs `owl init` for a personal and a Production
setup, `owl --version`, `owl check` and `owl serve`, and passes. CI doesn't run it, so run it too
on any change to packaging or the CLI, and name the commit in the pull request.

**Every release, v0.1 to v0.5, gets the same three before it goes out:** this wheel check, the
native runs on each affected dialect (step 1), and the maintainer's newcomer retest on the release
wheel. From v0.2 each release carries one phase of No YAML required; see the roadmap's Releases.

## 4. Publish to PyPI

The first upload uses a token you make on PyPI for your account, because the project doesn't exist
yet. It's never written to a file, a script or your shell history.

1. On <https://pypi.org/manage/account/token/>, create a token scoped to your entire account.
2. Upload, entering the token at uv's hidden prompt (username `__token__`):

   ```sh
   uv publish ../dist/probeowl-0.1.0-py3-none-any.whl
   ```

   Or set it only in that terminal, without echoing it, and clear it afterwards:

   ```sh
   read -rs UV_PUBLISH_TOKEN && export UV_PUBLISH_TOKEN
   uv publish ../dist/probeowl-0.1.0-py3-none-any.whl
   unset UV_PUBLISH_TOKEN
   ```

   In Windows PowerShell 5.1, which has no `-MaskInput`:
   `$env:UV_PUBLISH_TOKEN = [Net.NetworkCredential]::new('', (Read-Host -AsSecureString)).Password`,
   then `Remove-Item Env:UV_PUBLISH_TOKEN` when done.
3. Check <https://pypi.org/project/probeowl/> shows 0.1.0. Then install it from PyPI in a fresh
   tool environment, as in step 3, with `uv tool install probeowl`.
4. **Replace the token.** Delete the account-wide token, and create one scoped to the `probeowl`
   project for any later manual upload. Keep it in your password manager only.

## 5. Tag, and create the GitHub release

On `main`'s merge commit:

```sh
git tag -a v0.1.0 -m "ProbeOwl 0.1.0"
git push origin v0.1.0
gh release create v0.1.0 ../dist/probeowl-0.1.0-py3-none-any.whl \
  --title "ProbeOwl 0.1.0" --notes-file notes.md --verify-tag --prerelease
```

`notes.md` is the CHANGELOG's 0.1.0 section. `--prerelease` matches "v0.1 pre-release" on the
landing page; leave it off once a release is meant for general use.

## 6. Deploy the site, once the repository is public

GitHub Pages serves the landing page (`site/index.html`) at the root,
<https://saadzarook.github.io/probeowl/>, and the guide under `/guide/`. The guide's address comes
from `GUIDE_URL` in `backend/probeowl/guide.py`, which `guide/hooks.py` gives MkDocs.

Plain `mkdocs gh-deploy` must not be used: it would replace the landing page with the guide. Build
both into one staging folder instead, and push that folder to the `gh-pages` branch:

```sh
stage="$(mktemp -d)"
(cd guide && uvx --with-requirements requirements.txt --from mkdocs \
   mkdocs build --strict -d "$stage/guide")
cp site/index.html "$stage/index.html"
touch "$stage/.nojekyll"           # serve the files as they are
cd "$stage"
git init -q -b gh-pages
git add -A
git commit -q -m "Site for ProbeOwl 0.1.0"
git push --force https://github.com/saadzarook/probeowl.git gh-pages
```

The `gh-pages` branch holds only the built site, so replacing it each time is intended. In the
repository's **Settings → Pages**, set the source to **Deploy from a branch**, `gh-pages`, `/ (root)`.
Then check that <https://saadzarook.github.io/probeowl/> shows the landing page, that its Guide link
opens <https://saadzarook.github.io/probeowl/guide/>, and that the guide's search works.

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
