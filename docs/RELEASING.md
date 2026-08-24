# Releasing docfix

Publishing is automated by `.github/workflows/release.yml` and triggered by
pushing a `v*` tag. A release is a decision, not a side effect of merging, which
is why nothing publishes from `main`.

## One-time setup on PyPI

**This is the only step that cannot be automated from the repository**, because
it requires being signed in to PyPI as the project owner.

docfix authenticates with **Trusted Publishing** (OIDC), not an API token. There
is no token in this repository and none should ever be added: a token is a
long-lived secret that any workflow able to read secrets can exfiltrate, while
an OIDC identity is minted per run and scoped to one repository, workflow and
environment. It also cannot leak, because it does not persist.

Do this once, before the first release:

1. Sign in to <https://pypi.org> and open
   **Your projects → Publishing** (or, for a name not yet registered,
   <https://pypi.org/manage/account/publishing/>).
2. Add a **pending publisher** with exactly these values:

   | Field | Value |
   | --- | --- |
   | PyPI project name | `docfix` |
   | Owner | `NikifyArt` |
   | Repository name | `API-lib-for-fixing-common-format-issues-` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

   The repository name ends with a **trailing hyphen**. It is part of the real
   name and is easy to drop — copy it, do not retype it.

3. Optionally repeat on <https://test.pypi.org> with environment `testpypi`, so
   the dry run below works.

A GitHub *environment* named `pypi` is created automatically the first time the
job runs. Adding a required reviewer to it (Settings → Environments) makes every
publish need a human click, which is worth doing once the package has users.

## Rehearse first (recommended)

A version number can never be re-uploaded to PyPI. If the first real upload is
broken, the only remedy is burning `0.1.1` on a packaging fix. So rehearse:

**Actions → Release → Run workflow → target: `testpypi`**

That runs the full suite on three Pythons, builds, checks the wheel contents and
`twine check --strict`, and uploads to TestPyPI. Then confirm the artifact is
actually usable:

```bash
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ docfix
docfix --version
```

The extra index is needed because docfix's dependencies live on real PyPI.

## Release

```bash
# 1. Everything green on main.
git checkout main && git pull

# 2. The tag must match the version in pyproject.toml -- the workflow refuses
#    the release otherwise, rather than letting the two disagree on PyPI.
grep '^version' pyproject.toml

# 3. Tag and push. The push is what publishes.
git tag -a v0.1.0 -m "docfix 0.1.0"
git push origin v0.1.0
```

The workflow then, in order:

1. runs the full suite and lint on Python 3.10, 3.11 and 3.12;
2. builds the wheel and sdist;
3. refuses if the tag disagrees with the packaged version;
4. asserts the runtime YAML and `py.typed` are inside the wheel;
5. runs `twine check --strict`, so the README is known to render;
6. publishes to PyPI;
7. creates the GitHub release with the artifacts attached.

Every gate runs *before* the upload, because an upload cannot be undone.

## Cutting the next version

1. Move the `Unreleased` entries into a new `## X.Y.Z — YYYY-MM-DD` section in
   `CHANGELOG.md`, and leave `Unreleased` empty.
2. Bump `version` in `pyproject.toml`. Semantic versioning: a breaking change to
   the public API in `docfix/__init__.py` is a major bump once past 1.0, and
   before that gets a **Changed** entry with a migration note.
3. Merge, then tag as above.

## If something goes wrong

- **The tag was wrong.** Delete it (`git push --delete origin vX.Y.Z`) and tag
  again. Safe as long as the `pypi` job has not run.
- **A broken version reached PyPI.** It cannot be replaced. You may *yank* it
  (PyPI → Manage → Yank), which hides it from new resolutions while leaving
  existing pins working, then release a fix. Yank rather than delete: deleting
  breaks anyone who already pinned it.
- **The publish step failed with an OIDC error.** The pending publisher does not
  match. Check the four values in the table above, especially the trailing
  hyphen in the repository name and the environment name.
