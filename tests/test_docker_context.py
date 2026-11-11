"""What may enter a Docker build context, checked without a Docker daemon.

The image is built from this repository root, so anything the daemon sees in
that directory is available to ``COPY``. That includes ``.env`` (the
ListenBrainz token and the Lidarr API key), the virtualenv, ``.git``, the music
database, logs and cookies. Two independent guards keep them out:

* ``.dockerignore`` excludes each secret and each heavy local artefact, so the
  daemon never sends them;
* the Dockerfile names only what the image needs (``pyproject.toml``,
  ``README.md``, ``src/``) instead of copying the whole context, so even a
  ``.dockerignore`` that drifts cannot pull a secret into a layer.

These tests read those two files as text. They need no network, no daemon and
no build, so CI can enforce the promise cheaply; a real ``docker build`` is
still the only proof of the final image, and it is not available here.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERIGNORE = ROOT / ".dockerignore"
DOCKERFILE = ROOT / "Dockerfile"

#: Every context entry that must never reach the daemon.
REQUIRED_IGNORES = (
    ".env",
    ".env.*",
    ".venv/",
    ".git/",
    "data/",
    "*.db",
    "*.log",
    "cookies.txt",
    ".pytest_cache/",
    "__pycache__/",
    ".review/",
    ".superpowers/",
    "docs/superpowers/",
)


def _normalise(pattern):
    """Compare patterns without caring about a trailing slash or whitespace."""
    return pattern.strip().rstrip("/")


def _dockerignore_patterns():
    assert DOCKERIGNORE.is_file(), f"{DOCKERIGNORE.name} is missing from {ROOT}"
    patterns = []
    for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            patterns.append(entry)
    return patterns


def _dockerfile_lines():
    assert DOCKERFILE.is_file(), f"{DOCKERFILE.name} is missing from {ROOT}"
    return DOCKERFILE.read_text(encoding="utf-8").splitlines()


def _logical_lines(lines):
    """Join backslash continuations so a wrapped instruction is still seen."""
    pending = ""
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            pending += stripped[:-1] + " "
            continue
        yield (pending + stripped).strip()
        pending = ""
    if pending.strip():
        yield pending.strip()


def _copy_sources():
    """Every source operand of every ``COPY`` instruction in the Dockerfile."""
    sources = []
    for instruction in _logical_lines(_dockerfile_lines()):
        parts = instruction.split()
        if not parts or parts[0].upper() != "COPY":
            continue
        operands = [part for part in parts[1:] if not part.startswith("--")]
        if operands and operands[0].startswith("["):
            # The JSON form: COPY ["src/", "./src/"].
            try:
                operands = json.loads(" ".join(operands))
            except json.JSONDecodeError:  # pragma: no cover - malformed Dockerfile
                pass
        sources.extend(operands[:-1])
    return sources


def test_dockerignore_exists():
    assert DOCKERIGNORE.is_file(), (
        ".dockerignore is missing: the build context would carry .env, .venv, "
        ".git and data/ into the image"
    )


def test_dockerignore_lists_each_required_pattern():
    listed = {_normalise(pattern) for pattern in _dockerignore_patterns()}
    missing = [
        pattern for pattern in REQUIRED_IGNORES if _normalise(pattern) not in listed
    ]
    assert not missing, f".dockerignore does not exclude: {', '.join(missing)}"


def test_dockerfile_never_copies_the_build_context():
    whole_context = {".", "*", "./", "**", "./*"}
    offenders = [
        source
        for source in _copy_sources()
        if source in whole_context
    ]
    assert not offenders, (
        "Dockerfile copies the whole build context with COPY "
        f"{' '.join(offenders)}: the context holds .env and .venv"
    )


def test_dockerfile_does_not_copy_sensitive_paths():
    sensitive = {
        ".env",
        ".venv",
        ".git",
        "data",
        "cookies.txt",
        ".review",
        ".superpowers",
    }
    offenders = [
        source
        for source in _copy_sources()
        if Path(source).parts and Path(source).parts[0] in sensitive
    ]
    assert not offenders, (
        f"Dockerfile copies secret or local-only paths: {', '.join(offenders)}"
    )


def _installs_the_project(instruction):
    """True for a ``RUN`` that installs the local project, flags or not."""
    tokens = instruction.split()
    if not tokens or tokens[0].upper() != "RUN":
        return False
    return any(
        tokens[index].endswith("pip")
        and tokens[index + 1] == "install"
        and "." in tokens[index + 2 :]
        for index in range(len(tokens) - 1)
    )


def test_dockerfile_installs_the_package():
    instructions = list(_logical_lines(_dockerfile_lines()))
    assert any(
        _installs_the_project(instruction) for instruction in instructions
    ), "Dockerfile should install the package itself with 'pip install .'"
    assert not any(
        "requirements.txt" in instruction for instruction in instructions
    ), "Dockerfile still relies on requirements.txt instead of pyproject.toml"


def test_dockerfile_does_not_need_pythonpath():
    instructions = list(_logical_lines(_dockerfile_lines()))
    assert not any(
        instruction.upper().startswith("ENV") and "PYTHONPATH" in instruction
        for instruction in instructions
    ), "PYTHONPATH is unnecessary once the package is installed"
