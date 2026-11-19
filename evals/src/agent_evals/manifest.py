"""The run manifest (8.6-pre): what an eval result was measured WITH. 0 requests.

A score compares models only if everything else was the same. The gateway's fallback is right
for the dashboard (an answer beats no answer) and wrong for an eval (a run that silently
switches models measures two models at once). So every result records its configuration, and
the report and the gate refuse a run whose rows disagree.

Checked for equality across a run: the routes, the temperature, `agent_hash` (the code the
agent's answers depend on) and `data_hash` (the knowledge base and the plant it reads, 8.6a).
Recorded but NOT checked: the git commit. A README commit between two days of one run changes
the SHA but not a single answer.
"""

from __future__ import annotations

import hashlib
import subprocess
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parents[3]  # evals/src/agent_evals/manifest.py -> the repo
# What the agent's ANSWERS depend on (the subset of evals.yml's path filter that is code the
# agent runs; the scenarios have their own check: `stale`).
AGENT_CODE = ("api/app/agent", "api/app/rag", "mcp/sensor_mcp/src", "mcp/docs_mcp/src")
# What the answers depend on besides code (8.6a): the knowledge base the agent retrieves from and
# the plant it investigates. A changed manual changes answers just like a changed prompt.
AGENT_DATA = ("data/docs", "data/plant.yaml")
CHECKED = ("routes", "temperature", "agent_hash", "data_hash")


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    routes: tuple[str, ...]  # what MAY answer (exactly one in a strict eval)
    temperature: float
    agent_hash: str  # sha256 over AGENT_CODE's .py files, first 12 hex characters
    data_hash: str | None = None  # sha256 over AGENT_DATA (None: recorded before 8.6a)
    git_sha: str | None  # None outside a git checkout
    git_dirty: bool  # uncommitted changes: the SHA alone doesn't describe the code


def agent_hash(root: Path = ROOT) -> str:
    """Path + content of every .py file under AGENT_CODE, in a fixed order: a change to any of
    them (a prompt, a guardrail, a tool) gives another hash.

    Known caveat (8.6a): it hashes the WORKING-TREE bytes, and on Windows Git may check files out
    with CRLF; the same commit can then hash differently on another machine. That errs on the
    safe side (a false "differs", never the same hash for different code), and normalizing now
    would change every value and make the baseline look incomparable, so it stays as it is.
    `data_hash` normalizes from the start."""
    h = hashlib.sha256()
    for top in AGENT_CODE:
        for f in sorted((root / top).rglob("*.py")):
            h.update(f.relative_to(root).as_posix().encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:12]


def data_hash(root: Path = ROOT) -> str:
    """Path + content of every file under AGENT_DATA, in a fixed order, line endings normalized
    (CRLF -> LF): a function of the committed content, the same on every machine."""
    h = hashlib.sha256()
    for top in AGENT_DATA:
        path = root / top
        files = [path] if path.is_file() else sorted(f for f in path.rglob("*") if f.is_file())
        for f in files:
            h.update(f.relative_to(root).as_posix().encode())
            h.update(f.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:12]


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def build(routes: Sequence[str], temperature: float) -> Manifest:
    sha = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain", "--", *AGENT_CODE, *AGENT_DATA)
    return Manifest(
        routes=tuple(routes),
        temperature=temperature,
        agent_hash=agent_hash(),
        data_hash=data_hash(),
        git_sha=sha,
        git_dirty=bool(status),
    )


def problems(manifests: Sequence[Manifest | None], answered: Sequence[set[str]]) -> list[str]:
    """What makes a run's rows incomparable; [] = one configuration, one model.

    `manifests`: one per result row (None: recorded before manifests existed).
    `answered`: one per row, the provider:model strings that gave the row's SUCCESSFUL agent
    calls (results.answered_by): what actually answered, whatever the manifest says.
    """
    issues: list[str] = []

    # 1. Detect multiple models that actually answered.
    model_counts: Counter[str] = Counter()

    for models in answered:
        model_counts.update(models)

    if len(model_counts) > 1:
        details = ", ".join(
            f"{model} ({count} rows)"
            for model, count in sorted(
                model_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        )
        issues.append(f"mixed models: {details}")

    # 2. Check configuration consistency across manifests.
    valid_manifests = [m for m in manifests if m is not None]

    for field in CHECKED:
        # None = not recorded yet (data_hash before 8.6a): unknown, not "different".
        counts = Counter(value for m in valid_manifests if (value := getattr(m, field)) is not None)

        if len(counts) > 1:
            details = ", ".join(
                f"{value} ({count} rows)"
                for value, count in sorted(
                    counts.items(),
                    key=lambda item: (-item[1], str(item[0])),
                )
            )
            issues.append(f"{field} differs: {details}")

    return issues
