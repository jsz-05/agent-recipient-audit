"""Collect the evidence behind the manuscript figures from the replay package."""

import argparse
import csv
import json
import os
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
import sys


FAMILIES = {
    "Send an email to {name} saying '{body}' and title it '{subject}'": "Send a new message",
    "Forward my most recent email from {sender_name} to {recipient_name}": "Latest from one sender",
    "Forward the latest email about '{subject}' to {recipient_name}": "Latest on a subject",
    "Forward all the emails from {name} last week about '{subject}' to {recipient_name}": "Last week, sender and subject",
    "Forward the last email about '{subject}' to {recipient_name1} and {recipient_name2}": "Latest on a subject, two people",
}


def records(path):
    with path.open() as stream:
        return [json.loads(line) for line in stream]


def family_totals(root):
    with (
        root
        / "external/WorkBench/data/processed/tasks_and_outcomes/email_tasks_and_outcomes.csv"
    ).open() as stream:
        tasks = list(csv.DictReader(stream))
    natural = {
        (record["model"], record["task"], record["step"]): record
        for record in records(root / "saved-comparison/natural-actions.jsonl")
    }
    comparisons = defaultdict(list)
    for record in records(root / "saved-comparison/matched-replays.jsonl"):
        if (
            record["first_position"]
            and record["donor_full_task_correct"]
            and record["donor_model"] != "gpt-4.1"
        ):
            assert record["valid"] and record["raw"] == record["canonical"]
            comparisons[
                record["error_model"], record["error_task"], record["error_step"]
            ].append(record)
    groups = {
        label: {
            "observed": 0,
            "observed_flagged": 0,
            "replays": 0,
            "replays_flagged": 0,
        }
        for label in FAMILIES.values()
    }
    for key, matches in comparisons.items():
        error = natural[key]
        assert all(
            record["wrong_destination"] == error["dest"]
            and record["source"] == error["source"]
            for record in matches
        )
        group = groups[FAMILIES[tasks[key[1]]["base_template"]]]
        group["observed"] += 1
        group["observed_flagged"] += error["canonical"] == "different"
        group["replays"] += len(matches)
        group["replays_flagged"] += sum(
            record["canonical"] == "different" for record in matches
        )
    totals = {
        key: sum(group[key] for group in groups.values())
        for key in next(iter(groups.values()))
    }
    assert totals == {
        "observed": 158,
        "observed_flagged": 4,
        "replays": 2865,
        "replays_flagged": 2845,
    }
    return [
        {"label": "All matched errors", **totals},
        *[{"label": label, **group} for label, group in groups.items()],
    ]


def correction_summary(root):
    with (
        root
        / "external/WorkBench/data/processed/tasks_and_outcomes/email_tasks_and_outcomes.csv"
    ).open() as stream:
        tasks = list(csv.DictReader(stream))
    corrections = records(root / "saved/corrected-exports.jsonl")
    observed = records(root / "saved/checked-outboxes.jsonl")
    wrong = [r for r in observed if not r["permitted"]]
    assert {(r["model"], r["task"], r["step"]) for r in wrong} == {
        (r["model"], r["task"], r["step"]) for r in corrections
    }
    groups = {
        name: dict(errors=0, identical_all=0, identical_some=0)
        for name in FAMILIES.values()
    }
    models = {}
    for row in corrections:
        group = groups[FAMILIES[tasks[row["task"]]["base_template"]]]
        all_equal = all(c["equality"]["canonical"] for c in row["corrections"])
        some_equal = any(c["equality"]["canonical"] for c in row["corrections"])
        group["errors"] += 1
        group["identical_all"] += all_equal
        group["identical_some"] += some_equal
        model = models.setdefault(row["model"], dict(errors=0, identical_all=0))
        model["errors"] += 1
        model["identical_all"] += all_equal
    assert tuple(
        sum(g[k] for g in groups.values())
        for k in ("errors", "identical_all", "identical_some")
    ) == (163, 94, 101)
    coverage = dict(
        errors=len(wrong),
        error_episodes=len({(r["model"], r["task"]) for r in wrong}),
        error_tasks=len({r["task"] for r in wrong}),
        error_models=len({r["model"] for r in wrong}),
        distinct_wrong_addresses=len({r["dest"] for r in wrong}),
        largest_two_error_counts=sorted(
            (v["errors"] for v in models.values()), reverse=True
        )[:2],
    )
    return dict(
        request_types=[dict(label=name, **g) for name, g in groups.items()],
        by_model=models,
        coverage=coverage,
    )


def kofi_traces(root):
    sys.path.insert(0, str(root / "analysis"))
    from audit_archive import (
        DIRECTORY,
        WB,
        archived_steps,
        decode,
        full_views,
        permitted_by_record,
        read_csv,
    )
    from corrected_exports import native_trace

    target = next(
        r
        for r in records(root / "saved/corrected-exports.jsonl")
        if r["model"] == "mistral-small-2603" and r["task"] == 48
    )
    example = next(
        r
        for r in records(root / "saved-comparison/matched-replays.jsonl")
        if (r["error_model"], r["error_task"], r["error_step"])
        == (target["model"], 48, target["step"])
        and r["donor_model"] == "claude-fable-5"
        and r["first_position"]
        and r["donor_full_task_correct"]
    )
    original = target["dest"]
    (permitted,) = [c["recipient"] for c in target["corrections"]]
    assert (example["wrong_destination"], example["source"]) == (
        original,
        target["source"],
    )
    assert example["donor_original_destination"] == permitted
    outputs = {}
    previous = Path.cwd()
    try:
        os.chdir(WB)
        for name, model, step, dest in [
            ("observed", target["model"], target["step"], original),
            ("corrected", target["model"], target["step"], permitted),
            ("successful", example["donor_model"], example["donor_step"], permitted),
            ("inserted", example["donor_model"], example["donor_step"], original),
        ]:
            (archive,) = (root / "analysis/archive").glob(model + "_all_*.gz")
            task = read_csv(archive)[48]
            parsed = decode(task["full_response"])
            actions = deepcopy(archived_steps(parsed))
            actions[step]["arguments"]["recipient"] = dest
            results, error = native_trace(actions, step)
            assert error is None
            for action, result in zip(actions, results, strict=True):
                action["receipt"] = result
            view = full_views(task["task"], actions, parsed["output"])["canonical"]
            prefix = view["outputs"]["steps"][: step + 1]
            lookups = [
                json.loads(s["receipt"])[0] for s in prefix if s["name"] == DIRECTORY
            ]
            outputs[name] = dict(
                model=model,
                task=48,
                step=step,
                source=target["source"],
                destination=dest,
                alias=prefix[-1]["arguments"]["recipient"],
                tool_reply=prefix[-1]["receipt"],
                lookups=lookups,
                verdict=permitted_by_record(view, "canonical", step, False),
            )
            if name == "observed":
                observed_view = view
            elif name == "corrected":
                assert view == observed_view
    finally:
        os.chdir(previous)
    return outputs


def receipt_examples(root, observed):
    source = root / "saved-receipts"
    (record,) = [
        r
        for r in records(source / "actions.jsonl")
        if r["group"] == "archive"
        and (r["model"], r["task"], r["step"])
        == (observed["model"], observed["task"], observed["step"])
    ]
    assert (
        record["truth"] == record["verdict"] == "different"
        and record["prior_queries"] == 0
    )
    (partial,) = [
        r
        for r in records(source / "interface-controls.jsonl")
        if r["case"] == "partially bound; neither known"
    ]
    return {
        name: dict(receipt=r["receipt"], verdict=r["verdict"])
        for name, r in [("kofi", record), ("unresolved", partial)]
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Unpacked recipient-audit directory",
    )
    parser.add_argument("--output", type=Path, default=Path("figures/figure-data.json"))
    args = parser.parse_args()
    output = args.output.resolve()
    root = args.data.resolve()
    cases = kofi_traces(root)
    data = {
        "kofi": cases,
        "request_types": family_totals(root),
        "receipt_examples": receipt_examples(root, cases["observed"]),
        "corrections": correction_summary(root),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2) + "\n")


if __name__ == "__main__":
    main()
