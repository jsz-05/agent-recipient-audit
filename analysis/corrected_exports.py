"""Compare an observed wrong send with each allowable correction in its own history."""

from collections import Counter, defaultdict
from copy import deepcopy
import json
import os
import socket

from audit_archive import (
    HERE,
    SUCCESS,
    WB,
    archived_steps,
    decode,
    digest,
    full_views,
    read_csv,
)
from matched_checks import lines, save
from workbench_replay import TOOLS, get_state, reset_state


def native_trace(steps, target):
    """Replay a tool sequence and check the target action's actual outbox write."""
    reset_state()
    state = get_state()
    observed = []
    for index, step in enumerate(steps):
        args = step["arguments"]
        # The archive can include serialization/agent errors; exclude unexecutable
        # trajectories from the fully replay-validated result and count them.
        if step["name"] not in TOOLS or not isinstance(args, dict):
            return None, "unsupported call"
        try:
            before = len(state.emails)
            value = TOOLS[step["name"]](**args)
        except (TypeError, ValueError, KeyError, IndexError) as e:
            return None, type(e).__name__
        if index == target:
            assert len(state.emails) == before + 1 and value == SUCCESS[step["name"]]
            assert (
                state.emails.iloc[-1]["sender/recipient"] == args["recipient"].lower()
            )
        observed.append(value)
    return observed, None


def main():
    os.chdir(WB)

    def offline(*args, **kwargs):
        raise RuntimeError("Network disabled")

    socket.socket.connect = offline
    reference = {
        r["task"]: {dest for _, dest in r["independent"]}
        for r in lines("permission-resolution")
    }
    observed_runs = {(r["model"], r["task"]): r for r in lines("runs")}
    by_episode = defaultdict(list)
    for a in lines("checked-outboxes"):
        by_episode[a["model"], a["task"]].append(a)
    results = []
    for archive in sorted((HERE / "archive").glob("*.gz")):
        model = archive.name.split("_all_")[0]
        rows = read_csv(archive)
        for (m, tid), actions in by_episode.items():
            if m != model or all(a["permitted"] for a in actions):
                continue
            row = rows[tid]
            parsed = decode(row["full_response"])
            steps = archived_steps(parsed)
            original = full_views(row["task"], steps, parsed["output"])
            assert {
                mode: digest(view) for mode, view in original.items()
            } == observed_runs[m, tid]["view_digests"]
            for a in actions:
                if a["permitted"]:
                    continue
                index = a["step"]
                actual, orig_error = native_trace(steps, index)
                original_matches = actual == [s["receipt"] for s in steps]
                corrections = []
                for addr in sorted(reference[tid]):
                    changed = deepcopy(steps)
                    changed[index]["arguments"]["recipient"] = addr
                    assert addr != a["dest"]
                    new_receipts, error = native_trace(changed, index)
                    # Keep the input/assistant history fixed, but include any changed
                    # later native tool responses in the corrected export when available.
                    if original_matches and new_receipts is not None:
                        for s, receipt in zip(changed, new_receipts):
                            s["receipt"] = receipt
                    v = full_views(row["task"], changed, parsed["output"])
                    same = {
                        mode: export == v[mode] for mode, export in original.items()
                    }
                    assert not same["raw"]
                    corrections.append(
                        dict(
                            recipient=addr,
                            native_error=error,
                            downstream_equal=(
                                (actual == new_receipts)
                                if actual is not None and new_receipts is not None
                                else None
                            ),
                            equality=same,
                        )
                    )
                results.append(
                    dict(
                        model=m,
                        task=tid,
                        step=index,
                        source=a["source"],
                        dest=a["dest"],
                        original_native_matches=original_matches,
                        orig_error=orig_error,
                        one_outbound=len(actions) == 1,
                        last_tool=index == len(steps) - 1,
                        correct_before_after=sum(not x["permitted"] for x in actions)
                        == 1,
                        canonical_checker=a["natural_verdict"],
                        corrections=corrections,
                    )
                )
    save("corrected-exports", results)
    valid = [
        r
        for r in results
        if r["original_native_matches"]
        and all(x["native_error"] is None for x in r["corrections"])
    ]
    groups = dict(
        all=results,
        validated=valid,
        one_name=[r for r in valid if len(r["corrections"]) == 1],
        one_outbound=[r for r in valid if r["one_outbound"]],
        only_wrong_outbound=[r for r in valid if r["correct_before_after"]],
        last_tool=[r for r in valid if r["last_tool"]],
    )
    summary = {}
    for name, group in groups.items():
        summary[name] = dict(
            actions=len(group),
            episodes=len({(r["model"], r["task"]) for r in group}),
            tasks=len({r["task"] for r in group}),
            models=len({r["model"] for r in group}),
            equality={
                mode: {
                    "every": sum(
                        all(c["equality"][mode] for c in r["corrections"])
                        for r in group
                    ),
                    "any": sum(
                        any(c["equality"][mode] for c in r["corrections"])
                        for r in group
                    ),
                }
                for mode in ("raw", "mask", "tokens", "canonical")
            },
        )
    summary["corrected_downstream_changes"] = sum(
        any(x["downstream_equal"] is False for x in r["corrections"]) for r in valid
    )
    summary["unvalidated_original_reasons"] = dict(
        Counter(
            str(r["orig_error"] or "archived receipt differs")
            for r in results
            if not r["original_native_matches"]
        )
    )
    (HERE / "corrected-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
