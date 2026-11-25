"""Run the checkout CLI, or open the standalone explorer with --browser."""

from hashlib import sha256
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import webbrowser


ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".planner-runtime"
INSPECTION = {"-h", "--help", "--version", "--schema", "--profile-schema", "--portfolio-schema",
              "--result-schema", "--advice-schema", "--brief-schema", "--show-profile"}
PROBE = ("import sys; print(sys.executable); sys.exit(0 if sys.implementation.name == 'cpython' "
         "and (3, 11) <= sys.version_info[:2] < (3, 15) else 1)")


def _local_python():
    override = os.environ.get("SCHEDULING_PLANNER_PYTHON")
    if not override and sys.implementation.name == "cpython" and (3, 11) <= sys.version_info[:2] < (3, 15):
        return sys.executable
    versions = ["3.12", "3.13", "3.11", "3.14"]
    commands = [[override]] if override else [["python" + v] for v in versions] + [["python3"], ["python"]]
    if os.name == "nt" and not override:
        commands.extend(["py", "-" + v] for v in versions)
    for command in commands:
        try:
            probe = subprocess.run([*command, "-I", "-X", "utf8", "-c", PROBE],
                                   capture_output=True, encoding="utf-8")
        except OSError:
            continue
        if probe.returncode == 0:
            return probe.stdout.strip()
    if override:
        raise RuntimeError("SCHEDULING_PLANNER_PYTHON must point to CPython 3.11 through 3.14.")
    return None


def _pip_environment(base):
    # Publish a marker only after pip and the native import succeed. Each attempt
    # has its own final path because venv scripts contain absolute paths.
    CACHE.mkdir(exist_ok=True)
    digest = sha256()
    for part in (str(ROOT).encode(), str(base).encode(), (ROOT / "pyproject.toml").read_bytes(),
                 (ROOT / "src/scheduling_planner/_version.py").read_bytes()):
        digest.update(part + b"\0")
    prefix = "pip-" + digest.hexdigest()[:16] + "-"
    executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    for ready in sorted(CACHE.glob(prefix + "*/.ready")):
        python = ready.parent / executable
        if python.is_file():
            return python
    env = Path(tempfile.mkdtemp(prefix=prefix, dir=CACHE))
    python = env / executable
    try:
        print("Preparing the planner environment with pip ...", file=sys.stderr)
        for command in ([base, "-m", "venv", env],
                        [python, "-I", "-m", "pip", "install", "-e", ROOT],
                        [python, "-I", "-m", "pip", "check"],
                        [python, "-I", "-c", "from scheduling_planner.engine import plan"]):
            subprocess.run(command, stdout=sys.stderr, check=True)
        (env / ".ready").touch()
    except BaseException:
        shutil.rmtree(env, ignore_errors=True)
        raise
    return python


def _bootstrap_uv():
    # A machine with only an older Python and pip can acquire the project runner.
    CACHE.mkdir(exist_ok=True)
    executable = "Scripts/uv.exe" if os.name == "nt" else "bin/uv"
    for ready in sorted(CACHE.glob("uv-*/.ready")):
        candidate = ready.parent / executable
        if candidate.is_file():
            return candidate
    dest = Path(tempfile.mkdtemp(prefix="uv-", dir=CACHE))
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "--target", dest, "uv==0.9.18"],
                       check=True, stdout=sys.stderr)
        candidate = dest / executable
        subprocess.run([candidate, "--version"], check=True, stdout=sys.stderr)
        (dest / ".ready").touch()
        return candidate
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise


def main(argv):
    if "--browser" in argv:
        if argv != ["--browser"]:
            print("Use --browser on its own to open the offline explorer.", file=sys.stderr)
            return 2
        page = ROOT / "docs/demo.html"
        if not page.is_file() or not webbrowser.open(page.as_uri()):
            print(f"Open the explorer from the checkout: {page}", file=sys.stderr)
            return 1
        return 0
    try:
        base = _local_python()
        options = argv[:argv.index("--")] if "--" in argv else argv
        if base and INSPECTION.intersection(options):
            return subprocess.call([base, ROOT / "planner.py", *argv])
        args = argv or ["--demo"]
        uv = shutil.which("uv")
        # pip's wheelhouse options describe an explicitly selected package source.
        if base and (not uv or os.environ.get("PIP_NO_INDEX") or os.environ.get("PIP_FIND_LINKS")):
            return subprocess.call([_pip_environment(base), "-m", "scheduling_planner", *args])
        if not base and os.environ.get("PIP_NO_INDEX"):
            raise RuntimeError("Offline setup needs a local CPython 3.11-3.14 matching the wheelhouse.")
        if not uv:
            uv = _bootstrap_uv()
        return subprocess.call([uv, "run", "--project", ROOT, "--locked", "--python", base or "3.12",
                                "--", "schedule-plan", *args])
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Planner setup failed: {error}\nSee the README platform table. First setup needs package-index "
              "access or a matching wheelhouse. The offline explorer is docs/demo.html.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
