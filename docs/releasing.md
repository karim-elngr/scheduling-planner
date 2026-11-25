# Preparing a release

Ship the checkout for agent hosts and the wheel for Python integrations. A host needs the skill and its reference files. An installed Python client needs the command, library, schemas and bundled snapshot. Verify both paths against the same revision.

## Build the release candidate

Use a clean checkout, a fresh CPython 3.11–3.14 virtual environment and an empty `dist/` output directory. Check the version in [`_version.py`](../src/scheduling_planner/_version.py) against the [changelog](../CHANGELOG.md) and any migration guidance. Run from the checkout root.

```sh
uv sync --locked --extra dev
uv run --locked --extra dev python -m ruff check .
uv run --locked --extra dev python -m unittest discover -s tests -v
uv run --locked --extra dev python tools/eval_agent.py
uv run --locked --extra dev python tools/refresh_demo.py --check
uv run --locked --extra dev python -m build
uv run --locked --extra dev python -m twine check --strict dist/*
uv run --locked --extra dev python tools/check_release.py dist
```

| Check | What a pass establishes |
|---|---|
| Unit tests and reference cases | The Python interfaces, CLI and synthetic host workflow meet their assertions. For skill changes, also run the prompts in a real host and review its response using the [host evaluation guide](workflow-evaluations.md). |
| Demo freshness | New native solves reproduce the committed reference data. Open [the offline demo](demo.html) at narrow and wide widths. Select 09:30, preview the review moving to noon, then compare 09:00. Lock the review to block both morning times. Unlock it and remove Eli's noon coverage to see the conditional proposal. Check that the downloaded snapshot requests only the chosen start. Run `python tools/check_browser_demo.py` with Chrome on your path, or supply its executable via `--chrome`. |
| Build and strict metadata check | The source archive builds a wheel and the distribution description passes Twine's checks. The [package description](package-description.md) must make sense outside a checkout. |
| Archive check | Exactly one sdist and one wheel are present. Package files match byte-for-byte, metadata and entry points agree, and the license, examples, schemas and required source-only assets are present. |

## Refresh the documentation images

The **Offline browser demo** workflow captures the three figures from Chrome and publishes the `demo-images` artifact. After changing the demo, download the artifact for the tested revision, replace the three PNGs in `docs/images/`, and check each caption against its figure. To reproduce the captures locally, install `playwright==1.65.2` in a separate Python environment with Google Chrome installed and run `python tools/capture_demo.py docs/images` from the checkout.

## Exercise the installed wheel

Run this POSIX smoke test from the same checkout. The explicit directory change ensures the installed package supplies the modules and the packaged demo.

```sh
repo=$(pwd)
smoke=$(mktemp -d)
python -m venv "$smoke/env"
. "$smoke/env/bin/activate"
python -m pip install "$repo"/dist/*.whl
cd "$smoke"
python -m pip check
schedule-plan --demo
python -m scheduling_planner --demo
schedule-plan --schema
schedule-plan --brief-schema
schedule-plan --show-profile --profile "$repo/examples/team-profile.json"
schedule-plan --advise "$repo/sample.json"
schedule-plan --rules-file "$repo/examples/team_rules.py" "$repo/examples/release-review.json"
schedule-plan --advise --profile "$repo/examples/team-profile.json" --heuristics-file "$repo/examples/team_heuristics.py" "$repo/sample.json"
```

**Bundled demo.** Expect optimal verified starts of 15:00, 09:30 and 09:00 UTC for caps zero, one and two. The marginal improvements are 330 and 30 minutes.

**Team policy.** With default advice the sample's primary option keeps every meeting in place. The deadline-sensitive profile makes the earlier verified slot primary. Check the affected attendees and the missing evidence for the conditional alternative. In the release-review fixture, the selected plan must honor both the decision deadline and on-call coverage.

## Prepare a checkout for offline use

Build a wheelhouse on a connected machine with the same checkout revision, Python minor version, operating system and architecture as the destination. Linux also requires compatible libc and platform wheels. Use an empty directory outside the repository. The development extra covers the release and test tools, and the explicit build dependencies support pip's isolated build.

```sh
wheelhouse=$(mktemp -d)
python3 -m pip wheel --wheel-dir "$wheelhouse" ".[dev]" "setuptools>=77" wheel
echo "$wheelhouse"
```

Copy the entire wheelhouse and checkout to the destination. From the checkout, use a supported local Python interpreter with `venv` available. Set the path to the copied wheels and run the native demo.

```sh
export PIP_NO_INDEX=1
export PIP_FIND_LINKS=/absolute/path/to/wheelhouse
python3 run.py --demo
```

In PowerShell, set `$env:PIP_NO_INDEX = "1"` and `$env:PIP_FIND_LINKS = "C:\path\to\wheelhouse"`. Run `py -3.12 run.py --demo` for a Python 3.12 wheelhouse. Keep the variables set when installing `.[dev]` for an offline development environment. The CI distribution job exercises a fresh clone with the index and pip cache disabled after it prepares matching wheels.

## Publish under the maintainer's account

Before publishing, confirm the canonical repository, license, package name and publisher account. Set the project URLs in package metadata and configure private reporting or a monitored contact in [SECURITY.md](../SECURITY.md). Review the hosted checks for the exact revision to be tagged.

For hosts that want the skill and all examples, distribute the sdist or make a source ZIP from the tested tag:

```sh
git archive --format=zip --prefix=scheduling-planner/ --output=scheduling-planner.zip TAG
```

For a package index, verify ownership of the distribution name and configure publishing credentials through the hosting organization. Upload the sdist and wheel from the verified build. The repository's verification workflow runs independently of publishing credentials.
