"""Call the ledger's own validator and scope check.

`validate_report.py` resolves `prompt_file` from the ledger checkout, so the
report itself can live in a scratch directory. Scope validation reads the
report from `root` and learns the hash rule from the ledger script.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import sys
from pathlib import Path

FOCUS_FILES = {
    "sentiment": "agents/investigation-sentiment.md",
    "news": "agents/investigation-news.md",
    "social": "agents/investigation-social.md",
    "trading": "agents/investigation-trading.md",
    "full": "agents/investigation.md",
}

# The validator currently on main accepts only this shape. The stricter
# parser on the open trust PR accepts any number in [-1, 1]. Matching the
# older expression keeps a report valid before and after that PR merges.
MAIN_SCORE_RE = __import__("re").compile(
    r"^sentiment_score:\s*(-?(?:0(?:\.\d+)?|1(?:\.0+)?))\s*$",
    __import__("re").MULTILINE,
)


def prompt_hash(ledger_root: Path, focus: str) -> tuple[str, str]:
    relative = FOCUS_FILES.get(focus) or FOCUS_FILES["full"]
    path = ledger_root / relative
    if not path.is_file():
        raise FileNotFoundError(f"ledger prompt missing: {relative}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return relative, digest


def _load(ledger_root: Path, module: str):
    scripts = ledger_root / "scripts"
    script_path = str(scripts)
    if script_path not in sys.path:
        sys.path.insert(0, script_path)
    if module in sys.modules:
        loaded = sys.modules[module]
        origin = Path(getattr(loaded, "__file__", ""))
        if scripts in origin.parents:
            return loaded
        del sys.modules[module]
    return importlib.import_module(module)


def validate_report_dir(ledger_root: Path, report_dir: Path) -> list[str]:
    module = _load(ledger_root, "validate_report")
    errors = list(module.validate_report_dir(report_dir))
    return errors


def validate_scope(ledger_root: Path, root: Path, branch: str, files: list[str]) -> list[str]:
    """Run the ledger scope check.

    The trust-study revision takes ``root=`` so a scratch tree can be checked.
    The copy on main always reads ``REPO_ROOT``. Point that global at the
    scratch tree for the call, then put it back.
    """
    module = _load(ledger_root, "validate_contributor_scope")
    signature = inspect.signature(module.validate_scope)
    kwargs = {"require_report": True}
    if "root" in signature.parameters:
        kwargs["root"] = root
    if "block_simulated" in signature.parameters:
        kwargs["block_simulated"] = True
    previous = getattr(module, "REPO_ROOT", None)
    if "root" not in signature.parameters:
        module.REPO_ROOT = root
    try:
        return list(module.validate_scope(branch, files, **kwargs))
    finally:
        if "root" not in signature.parameters and previous is not None:
            module.REPO_ROOT = previous


def branch_name(session: str, ticker: str, hash8: str) -> str:
    safe = ticker.upper().replace(".", "-")
    return f"report/{session}-{safe}-{hash8}"


def simulated_block_reason(username: str, report_text: str) -> str:
    if username.lower().startswith("sim-"):
        return f"github_username {username!r} uses the sim- prefix blocked on main"
    for line in report_text.splitlines():
        if line.lower().startswith("simulated:"):
            value = line.split(":", 1)[1].strip().strip("'\"").lower()
            if value in {"true", "yes", "1"}:
                return "report sets simulated true, which the ledger blocks on main"
    return ""
