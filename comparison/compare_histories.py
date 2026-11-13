"""Run the matched-recipient comparison from the pinned public WorkBench archives."""

import ast
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import socket
import sys

OUT = Path(__file__).resolve().parent
PILOT = OUT.parent / "analysis"
sys.path.insert(0, str(PILOT))
from audit_archive import (
    EMAIL,
    SUCCESS,
    WB,
    archived_steps,
    decode,
    digest,
    langsmith,
    map_strings,
    permitted_by_record,
    read_csv,
    requested_names,
)
from corrected_exports import native_trace
from matched_checks import lines, lookup_evidence
from src.evals.evaluation import is_correct
from workbench_replay import TOOLS

BASELINE = "gpt-4.1"


def write_rows(name, rows):
    target = OUT / name
    temp = target.with_suffix(".tmp")
    with temp.open("w") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
    temp.replace(target)


def action_id(row):
    return (row["model"], row["task"], row["step"])


def scope(row):
    """Match histories on the task, outbound tool and source email."""
    return (row["task"], row["tool"], row["source"])


def exports(instruction, steps, final):
    run = {"task": instruction, "steps": steps, "final_text": final}
    folded = map_strings(run, lambda text: EMAIL.sub(lambda m: m[0].lower(), text))
    return langsmith(folded, "tokens")[1], langsmith(run, "raw")[1]


def score(views, position):
    return tuple(
        permitted_by_record(view, mode, position, False)
        for view, mode in zip(views, ("canonical", "raw"), strict=True)
    )


def scenario(instruction, steps, final, position, wrong_dest, original_ok):
    """Insert one recorded wrong address while preserving the donor history."""
    if not original_ok:
        return {"valid": False, "reason": "original trajectory did not validate"}
    changed = deepcopy(steps)
    changed[position]["arguments"]["recipient"] = wrong_dest
    receipts, error = native_trace(changed, position)
    if error is not None:
        return {"valid": False, "reason": error}
    # native_trace checks that the target added exactly one outbox row and
    # that its stored destination equals the requested address after case folding.
    assert receipts[position] == SUCCESS[changed[position]["name"]]
    assert receipts[:position] == [s["receipt"] for s in steps[:position]]
    downstream = receipts[position + 1 :] != [
        s["receipt"] for s in steps[position + 1 :]
    ]
    for step, receipt in zip(changed, receipts, strict=True):
        step["receipt"] = receipt
    views = exports(instruction, changed, final)
    verdict, raw = score(views, position)
    return {
        "valid": True,
        "canonical": verdict,
        "raw": raw,
        "later_receipt_changed": downstream,
        "export_digest": digest(views[0]),
    }


def main():
    os.chdir(WB)

    def offline(*args, **kwargs):
        raise RuntimeError("Network disabled during the experiment")

    socket.socket.connect = offline
    socket.socket.connect_ex = offline

    tasks = read_csv(
        WB / "data/processed/tasks_and_outcomes/email_tasks_and_outcomes.csv"
    )
    permissions = {
        r["task"]: {action for _, action in r["independent"]}
        for r in lines("permission-resolution")
        if r["agree"]
    }
    assert len(permissions) == 50
    runs = {(r["model"], r["task"]): r for r in lines("runs")}
    events = defaultdict(list)
    errors = defaultdict(list)
    for action in lines("checked-outboxes"):
        events[action["model"], action["task"]].append(action)
        assert action["permitted"] == (action["dest"] in permissions[action["task"]])
        if not action["permitted"]:
            errors[scope(action)].append(action)

    natural, matches = [], []
    manifest = json.loads((PILOT.parent / "archive-manifest.json").read_text())
    for item in manifest:
        file = PILOT / "archive" / Path(item["path"]).name
        assert hashlib.sha256(file.read_bytes()).hexdigest() == item["sha256"]
        model = file.name.split("_all_")[0]
        for task, row in enumerate(read_csv(file)):
            actions = events.get((model, task), [])
            relevant = [action for action in actions if scope(action) in errors]
            if not relevant:
                continue
            assert row["task"] == tasks[task]["task"]
            run = runs[model, task]
            parsed = decode(row["full_response"])
            steps = archived_steps(parsed)
            final = parsed["output"]
            assert all(s["name"] in TOOLS for s in steps)
            views = exports(row["task"], steps, final)
            assert digest(views[0]) == run["view_digests"]["canonical"]
            assert digest(views[1]) == run["view_digests"]["raw"]

            if run["recipient_truth"] == "different":
                for action in actions:
                    if action["permitted"]:
                        continue
                    receipts, error = native_trace(steps, action["step"])
                    assert error is None and receipts == [s["receipt"] for s in steps]
                    canonical, raw = score(views, action["step"])
                    assert (canonical, raw) == (
                        action["natural_verdict"],
                        action["raw_verdict"],
                    )
                    natural.append(
                        dict(
                            model=model,
                            task=task,
                            step=action["step"],
                            scope=action["tool"],
                            source=action["source"],
                            dest=action["dest"],
                            canonical=canonical,
                            raw=raw,
                        )
                    )
                continue

            assert (
                actions
                and run["recipient_truth"] == "permitted"
                and all(action["permitted"] for action in actions)
            )
            operations = ast.literal_eval(row["function_calls"])
            reference = ast.literal_eval(tasks[task]["outcome"])
            assert all(op.split(".func(")[0] in TOOLS for op in operations + reference)
            full_task_correct = bool(is_correct(operations, reference, row["error"]))
            seen = set()
            for action in sorted(relevant, key=lambda action: action["step"]):
                position = action["step"]
                bucket = scope(action)
                primary = bucket not in seen
                seen.add(bucket)
                canonical, raw = score(views, position)
                assert (canonical, raw) == (
                    action["natural_verdict"],
                    action["raw_verdict"],
                )
                original_receipts, error = native_trace(steps, position)
                original_ok = error is None and original_receipts == [
                    s["receipt"] for s in steps
                ]
                names = requested_names(row["task"])
                evidence = lookup_evidence(steps[:position], names, permissions[task])
                cache = {}
                for wrong in errors[bucket]:
                    if wrong["model"] == model:
                        continue
                    assert (
                        wrong["dest"] not in permissions[task]
                        and wrong["dest"] != action["dest"]
                    )
                    wrong_destination = wrong["dest"]
                    if wrong_destination not in cache:
                        cache[wrong_destination] = scenario(
                            row["task"],
                            steps,
                            final,
                            position,
                            wrong_destination,
                            original_ok,
                        )
                    matches.append(
                        dict(
                            error_model=wrong["model"],
                            error_task=task,
                            error_step=wrong["step"],
                            wrong_destination=wrong_destination,
                            source=wrong["source"],
                            tool=wrong["tool"],
                            donor_model=model,
                            donor_step=position,
                            first_position=primary,
                            donor_original_destination=action["dest"],
                            donor_original_canonical=canonical,
                            donor_full_task_correct=full_task_correct,
                            donor_original_valid=original_ok,
                            prior_lookup=evidence,
                            **cache[wrong_destination],
                        )
                    )
        print(f"Completed {model}", flush=True)
    assert len(natural) == 163 and len({action_id(action) for action in natural}) == 163
    write_rows("natural-actions.jsonl", natural)
    write_rows("matched-replays.jsonl", matches)
    print(
        f"{len(natural)} original errors and {len(matches)} error / comparison position combinations."
    )


if __name__ == "__main__":
    main()
