"""What may enter a Docker build context, checked without a Docker daemon.

The image is built from this repository root, so anything the daemon sees in
that directory is available to ``COPY``. That includes ``.env`` (the
ListenBrainz token and the Lidarr API key), the virtualenv, ``.git``, the music
database, logs and cookies. Three independent guards keep them out:

* ``.dockerignore`` excludes each secret, each heavy local artefact and the
  build residues an editable install leaves behind (``src/lyra.egg-info``,
  ``build/``, ``dist/``, ``__pycache__``/``*.pyc`` at any depth), so the daemon
  never sends them;
* that file may not un-ignore anything with ``!``, and no
  ``Dockerfile.dockerignore`` may exist, because either one would silently
  override the first guard;
* the Dockerfile names only what the image needs (``pyproject.toml``,
  ``README.md``, ``src/``) via an explicit allow-list, so even a drifting
  ``.dockerignore`` cannot pull a secret into a layer.

These tests read those files as text. They need no network, no daemon and no
build, so CI can enforce the promise cheaply. ``_matches`` models the matching
rules Docker documents (``*`` never crosses ``/``, a pattern with no interior
``/`` matches a root entry only, ``**`` spans directories, excluding a
directory excludes its contents); it is a model, not the daemon, and a real
``docker build`` remains the only proof of the final image.
"""

import fnmatch
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERIGNORE = ROOT / ".dockerignore"
DOCKERFILE = ROOT / "Dockerfile"
#: Docker gives this file precedence over .dockerignore.
DOCKERFILE_IGNORE = ROOT / "Dockerfile.dockerignore"

#: The only context entries the image needs, so the only ones it may copy.
ALLOWED_COPY_SOURCES = frozenset({"pyproject.toml", "README.md", "src"})

#: Instructions that copy context entries into the image.
COPY_LIKE = frozenset({"COPY", "ADD"})

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
    "**/__pycache__/",
    "**/*.pyc",
    "build/",
    "dist/",
    "**/*.egg-info/",
    ".review/",
    ".superpowers/",
    "docs/superpowers/",
)

#: One real context path per hazard that must never reach the daemon. These are
#: matched with Docker's rules rather than compared against the pattern list, so
#: a root-anchored ``*.pyc`` fails here even though its spelling is present: it
#: matches nothing, and ``COPY src/ ./src/`` would bake the host's nested
#: ``.pyc`` files (~260 KB of them) into the image.
REQUIRED_IGNORED_PATHS = (
    ".env",
    ".env.local",
    ".venv/bin/python3",
    ".git/config",
    "data/music_automation.db",
    "music_automation.db",
    "music_automation.log",
    "cookies.txt",
    ".pytest_cache/v/cache/lastfailed",
    "src/lyra/api/__pycache__/lidarr.cpython-312.pyc",
    "src/lyra/api/__pycache__/__init__.cpython-312.pyc",
    "src/lyra.egg-info/PKG-INFO",
    "src/lyra.egg-info/SOURCES.txt",
    "build/lib/lyra/__init__.py",
    "dist/lyra-1.0.0-py3-none-any.whl",
    ".review/state.json",
    ".superpowers/sdd/task-report.md",
    "docs/superpowers/plans/plan.md",
)


def _clean(path):
    """A context-relative path without a leading ``./`` or a trailing ``/``."""
    path = path.strip()
    while path.startswith("./"):
        path = path[2:]
    return path.rstrip("/")


def _match_segments(pattern_parts, path_parts):
    if not pattern_parts:
        return not path_parts
    if pattern_parts[0] == "**":
        # Any number of directories, including none.
        return any(
            _match_segments(pattern_parts[1:], path_parts[skip:])
            for skip in range(len(path_parts) + 1)
        )
    if not path_parts:
        return False
    if not fnmatch.fnmatchcase(path_parts[0], pattern_parts[0]):
        return False
    return _match_segments(pattern_parts[1:], path_parts[1:])


def _matches(pattern, path):
    """True when Docker would exclude ``path`` because of ``pattern``."""
    pattern_parts = [part for part in _clean(pattern).split("/") if part]
    path_parts = [part for part in _clean(path).split("/") if part]
    # Excluding a directory excludes everything below it, so a match on any
    # ancestor of the path is a match for the path.
    return any(
        _match_segments(pattern_parts, path_parts[:end])
        for end in range(len(path_parts), 0, -1)
    )


def _dockerignore_patterns():
    assert DOCKERIGNORE.is_file(), f"{DOCKERIGNORE.name} is missing from {ROOT}"
    patterns = []
    for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            patterns.append(entry)
    return patterns


def _is_ignored(path):
    """True when the effective .dockerignore excludes ``path``."""
    return any(
        _matches(pattern, path)
        for pattern in _dockerignore_patterns()
        if not pattern.startswith("!")
    )


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
    """Every source operand of every ``COPY``-like instruction in the file."""
    sources = []
    for instruction in _logical_lines(_dockerfile_lines()):
        parts = instruction.split()
        if not parts or parts[0].upper() not in COPY_LIKE:
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


def test_no_dockerfile_specific_ignore_file():
    assert not DOCKERFILE_IGNORE.exists(), (
        f"{DOCKERFILE_IGNORE.name} exists and Docker gives it precedence over "
        ".dockerignore, so it can un-ignore .env while the other guard passes. "
        "Keep one ignore file."
    )


def test_dockerignore_has_no_negation_lines():
    negations = [
        pattern for pattern in _dockerignore_patterns() if pattern.startswith("!")
    ]
    assert not negations, (
        f".dockerignore un-ignores with {', '.join(negations)}: a later "
        "'!.env' cancels the '.env' exclusion and the secret reaches the daemon"
    )


def test_dockerignore_lists_each_required_pattern():
    listed = {_clean(pattern) for pattern in _dockerignore_patterns()}
    missing = [
        pattern for pattern in REQUIRED_IGNORES if _clean(pattern) not in listed
    ]
    assert not missing, f".dockerignore does not exclude: {', '.join(missing)}"


def test_dockerignore_excludes_each_required_path():
    leaving = [path for path in REQUIRED_IGNORED_PATHS if not _is_ignored(path)]
    assert not leaving, (
        ".dockerignore leaves these context entries for the daemon: "
        + ", ".join(leaving)
        + ". A pattern with no interior '/' is anchored at the context root, so "
        "a nested cache needs the '**/' form."
    )


def test_dockerignore_matching_model_is_root_anchored():
    # Pins the semantics the test above relies on, so a matcher that silently
    # became "match anything anywhere" cannot hide a root-anchored pattern.
    assert _matches("*.pyc", "module.pyc")
    assert not _matches("*.pyc", "src/lyra/module.pyc")
    assert _matches("**/*.pyc", "src/lyra/module.pyc")
    assert _matches("**/*.pyc", "module.pyc")
    assert _matches("data/", "data/music_automation.db")


def test_dockerfile_copies_only_allowed_sources():
    sources = _copy_sources()
    assert sources, "no COPY/ADD source parsed: the Dockerfile would ship nothing"
    offenders = sorted(
        {source for source in sources if _clean(source) not in ALLOWED_COPY_SOURCES}
    )
    assert not offenders, (
        f"Dockerfile copies context entries outside the allow-list: "
        f"{', '.join(offenders)}. Only "
        f"{', '.join(sorted(ALLOWED_COPY_SOURCES))} may be copied, so a secret "
        "cannot enter a layer even if .dockerignore drifts"
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
