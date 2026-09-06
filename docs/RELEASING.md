# Releasing

Releases are published to [PyPI](https://pypi.org/project/ertftm070/) by
GitHub Actions **trusted publishing** — no tokens, no secrets, the CI
workflow is authorized directly.

## One-time setup (needs a PyPI account)

1. Create an account at <https://pypi.org> if you don't have one.
2. On GitHub, create the **release environment**: repo → Settings →
   Environments → **New environment** → `release` (no secrets needed;
   it just gates the publish job).
3. On PyPI, go to **Publishing → Trusted Publishers → Add a pending publisher**:
   - Project name: `ertftm070`
   - Owner: `DorneichI`
   - Repository name: `er-tftm070-4-driver`
   - Workflow name: `ci.yml`
   - Environment name: `release`
4. Confirm. Publishing is now unlocked for this repository's tags.

## Cutting a release

1. Make sure `main` is green (CI badge in the README).
2. Update `CHANGELOG.md`: move the `[Unreleased]` entries under the new
   version heading, dated.
3. Commit, open a pull request, and merge it — `main` is
   branch-protected.  Then tag **with a `v` prefix** — the version on
   PyPI comes from the tag (setuptools_scm):
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```
4. CI runs lint, tests, and builds on the tag, then publishes the
   sdist + wheel to PyPI via the trusted publisher. Watch the
   Actions run; the `release` environment gates the publish step.

   The published wheel is pure Python (`py3-none-any`) — the optional
   C accelerator is deliberately not compiled into it (a compiled wheel
   would be platform-tagged and PyPI would reject it).  For the fast
   path on a Pi, install from the sdist:
   `pip install ertftm070 --no-binary ertftm070` (falls back to the
   pure-Python backend if no compiler is available).

## After publishing

Verify from a clean machine / venv:

```bash
pip install ertftm070[Pillow]
python -c "import ertftm070; print(ertftm070.__version__, ertftm070.BACKEND)"
```

## If you need to yank a release

Yanking hides the release from new installs but keeps the filename
reserved.  There is no CLI for it — twine can only upload, never remove
files — so use the PyPI web UI:

project → **Manage → Releases → (…)** → **Yank** (pick a reason, confirm)

Anyone already on the yanked version keeps it; new installs get the
newest available one instead.
