"""Reconstruct the archive analysis and compare it with the saved results."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

from verify_findings import verify

ROOT = Path(__file__).resolve().parent
ANALYSIS = ROOT / "analysis"


def check_inputs():
    """Verify the pinned benchmark files before using them in a replay."""
    checksums = json.loads((ROOT / "input-sha256.json").read_text())
    for name, expected in checksums.items():
        actual = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"Input checksum mismatch: {name}")


def compare_saved_results():
    for expected in sorted((ROOT / "saved").iterdir()):
        actual = ANALYSIS / expected.name
        if expected.read_bytes() != actual.read_bytes():
            raise RuntimeError(
                f"Regenerated file differs from saved file: {actual.name}"
            )


def main():
    print("Checking pinned inputs.", flush=True)
    check_inputs()
    for stage in (
        "audit_archive",  # Recover each recorded send and its available evidence.
        "matched_checks",  # Compare the checker with the separate scoring labels.
        "corrected_exports",  # Correct recipients and compare complete native exports.
        "export_controls",  # Repeat under the alternative privacy representations.
    ):
        subprocess.run(
            [sys.executable, str(ANALYSIS / (stage + ".py"))], cwd=ROOT, check=True
        )
    verify(ANALYSIS)
    compare_saved_results()
    print(
        "All regenerated summaries and per-action files agree with the saved results."
    )


if __name__ == "__main__":
    main()
