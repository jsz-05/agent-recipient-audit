"""Replay the WorkBench study offline and compare every output with the archive."""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def run(stage):
    """Run a stage in a fresh process so simulator state cannot leak between stages."""
    subprocess.run([sys.executable, str(ROOT / stage)], cwd=ROOT, check=True)


def compare_saved_results(saved_folder, output_folder):
    """Require byte-for-byte agreement, including individual action records."""
    for expected in sorted((ROOT / saved_folder).iterdir()):
        actual = ROOT / output_folder / expected.name
        if expected.read_bytes() != actual.read_bytes():
            raise RuntimeError(f"Replay differs from saved results: {expected.name}")


def main():
    # First reconstruct the recorded errors and their corrected exports.
    run("replay_archive.py")

    # Then insert the same wrong recipients into matched successful histories.
    run("comparison/compare_histories.py")
    run("comparison/summarize.py")

    # Finally test the permission receipts and all alternative coworker sends.
    run("receipts/evaluate.py")
    run("receipts/coworkers.py")

    compare_saved_results("saved-comparison", "comparison")
    compare_saved_results("saved-receipts", "receipts")
    run("check_results.py")
    print("All per-action records and summaries reproduced.")


if __name__ == "__main__":
    main()
