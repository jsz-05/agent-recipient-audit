"""Reaggregate the fixed archive and independently check its recorded evidence.

Python standard library only. Run with --data STUDY_ROOT or --data the extracted
recipient-audit directory. No model, network, SMTP, SDK, or native-tool calls.
"""

import argparse
import ast
from collections import Counter, defaultdict
from copy import deepcopy
import csv
import gzip
from hashlib import sha256
import json
from pathlib import Path
import re
from statistics import mean


EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
SUCCESS = {
    "email.send_email": "Email sent successfully.",
    "email.forward_email": "Email forwarded successfully.",
}
GROUPS = ("task", "template", "source_model", "episode", "destination")


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def read_csv(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", newline="") as stream:
        return list(csv.DictReader(stream))


def keyed(rows):
    result = {(r["model"], r["task"], r["step"]): r for r in rows}
    assert len(result) == len(rows), "Duplicate original action key"
    return result


def error_key(row):
    return row["error_model"], row["error_task"], row["error_step"]


def write_csv(path, rows):
    if not rows:
        return
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class LiteralRecords(ast.NodeTransformer):
    """Convert only the archive's record constructors to literal dictionaries."""

    def visit_Call(self, node):
        assert isinstance(node.func, ast.Name)
        assert node.func.id in ("AgentResult", "TraceStep", "AgentAction")
        assert not node.args and all(k.arg is not None for k in node.keywords)
        return ast.Dict(
            keys=[ast.Constant(k.arg) for k in node.keywords],
            values=[self.visit(k.value) for k in node.keywords],
        )


def decode_record(text):
    return ast.literal_eval(LiteralRecords().visit(ast.parse(text, mode="eval")))


def requested_names(instruction):
    text = instruction.lower().strip()
    if "send an email to " in text:
        return {re.search(r"send an email to ([a-z]+) saying ", text)[1]}
    if re.match(r"^[a-z]+(?: and [a-z]+)? needs? ", text):
        return set(re.split(r" needs? ", text, maxsplit=1)[0].split(" and "))
    match = re.fullmatch(
        r"(?:can you )?forward .+ to ([a-z]+(?: and [a-z]+)?)", text, re.S
    )
    assert match, instruction
    return set(match[1].split(" and "))


def raw_decision(steps, position, names, destination):
    known = {}
    for step in steps[:position]:
        if step["name"] != "company_directory.find_email_address":
            continue
        args = step["arguments"]
        if not isinstance(args, dict) or not isinstance(args.get("name"), str):
            continue
        name = args["name"].lower()
        if name not in names:
            continue
        try:
            result = json.loads(step["receipt"])
        except (TypeError, ValueError):
            continue
        if isinstance(result, list) and len(result) == 1 and isinstance(result[0], str):
            known[name] = result[0].lower()
    if destination.lower() in known.values():
        return "permitted"
    return "different" if set(known) == names else "insufficient"


def aliases(record):
    """Independently reconstruct the published whole-content alias relation."""
    mapping = {}

    def replace(match):
        address = match[0].lower()
        if address not in mapping:
            mapping[address] = "EMAIL_" + str(len(mapping) + 1)
        return mapping[address]

    def walk(value):
        if isinstance(value, str):
            return EMAIL.sub(replace, value)
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value

    return walk(record)


def group_value(key, dimension, events, tasks):
    event = events[key]
    return {
        "task": str(key[1]),
        "template": tasks[key[1]]["base_template"],
        "source_model": key[0],
        "episode": f"{key[0]} / task {key[1]}",
        "destination": event["dest"],
    }[dimension]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data",
        type=Path,
        required=True,
        help="Study root or extracted recipient-audit reviewer directory",
    )
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).resolve().parent / "results"
    )
    args = parser.parse_args()
    root = args.data.resolve()
    output = args.output.resolve()
    if (root / "revisions/permission-evidence-pilot").is_dir():
        pilot = root / "revisions/permission-evidence-pilot"
        history = root / "revisions/successful-history-comparison"
        archive_dir = pilot / "archive"
        manifest_path = pilot / "archive-manifest.json"
    else:
        pilot, history = root / "saved", root / "saved-comparison"
        archive_dir = root / "analysis/archive"
        manifest_path = root / "archive-manifest.json"
    bench = root / "external/WorkBench"
    csv.field_size_limit(2**28)
    inputs = [
        pilot / (name + ".jsonl")
        for name in (
            "actions",
            "checked-outboxes",
            "corrected-exports",
            "export-controls",
            "permission-resolution",
            "runs",
        )
    ]
    inputs += [
        history / (name + ".jsonl") for name in ("natural-actions", "matched-replays")
    ]
    task_path = bench / "data/processed/tasks_and_outcomes/email_tasks_and_outcomes.csv"
    roster_path = bench / "data/raw/email_addresses.csv"
    inputs += [task_path, roster_path, manifest_path]
    actions, events = keyed(read_jsonl(inputs[0])), keyed(read_jsonl(inputs[1]))
    corrections, controls = keyed(read_jsonl(inputs[2])), keyed(read_jsonl(inputs[3]))
    permissions = {r["task"]: r for r in read_jsonl(inputs[4])}
    runs_list = read_jsonl(inputs[5])
    runs = {(r["model"], r["task"]): r for r in runs_list}
    natural = keyed(read_jsonl(inputs[6]))
    replays = read_jsonl(inputs[7])
    tasks, roster = read_csv(task_path), set(roster_path.read_text().splitlines())
    assert len(runs) == len(runs_list)
    assert set(events) == set(actions)
    errors = {k: r for k, r in events.items() if not r["permitted"]}
    assert set(errors) == set(corrections) == set(controls) == set(natural)
    assert all(
        r["agree"] and r["official"] == r["independent"] for r in permissions.values()
    )
    for key, event in events.items():
        action = actions[key]
        permitted = {p[1] for p in permissions[key[1]]["official"]}
        assert event["dest"] == action["recipient"].lower()
        assert (
            event["permitted"]
            == action["permitted_recipient"]
            == (event["dest"] in permitted)
        )
        assert (
            event["natural_verdict"]
            == event["raw_verdict"]
            == action["checker"]["canonical"]
            == action["checker"]["raw"]
        )
        assert event["in_directory"] == (event["dest"] in roster)
        assert requested_names(tasks[key[1]]["task"]) == {
            s.split(".")[0] for s in permitted
        }
    for episode, run in runs.items():
        episode_events = [event for key, event in events.items() if key[:2] == episode]
        truth = (
            "none"
            if not episode_events
            else (
                "permitted"
                if all(event["permitted"] for event in episode_events)
                else "different"
            )
        )
        assert run["recipient_truth"] == truth

    # The committed full exports are retained as per-correction equality records.
    # Cross-check both SDK records and controls without importing their code.
    collision = {}
    equality_counts = defaultdict(Counter)
    total_corrections = 0
    for key, original in corrections.items():
        assert original["dest"] == errors[key]["dest"] == natural[key]["dest"]
        assert (
            original["canonical_checker"]
            == errors[key]["natural_verdict"]
            == natural[key]["canonical"]
            == natural[key]["raw"]
        )
        assert original["original_native_matches"] and original["orig_error"] is None
        left = {r["recipient"]: r for r in original["corrections"]}
        right = {r["recipient"]: r for r in controls[key]["corrections"]}
        assert len(left) == len(original["corrections"])
        assert (
            set(left) == set(right) == {p[1] for p in permissions[key[1]]["official"]}
        )
        vectors = defaultdict(list)
        for dest, a in left.items():
            b = right[dest]
            assert a["native_error"] is None and a["downstream_equal"]
            eq, other = a["equality"], b["equality"]
            assert (
                eq["canonical"]
                == eq["tokens"]
                == other["grafana_canonical"]
                == other["langsmith_hmac_equality"]
            )
            assert eq["mask"] == other["grafana_mask"]
            assert (
                not eq["raw"]
                and not other["langsmith_hmac_bytes"]
                and not other["langsmith_membership"]
            )
            for name, value in {**eq, **other}.items():
                vectors[name].append(value)
            total_corrections += 1
        for name, values in vectors.items():
            equality_counts[name]["every"] += all(values)
            equality_counts[name]["any"] += any(values)
        collision[key] = all(vectors["canonical"])

    # Reconstruct every eligible published history. Do not execute archive text.
    archived = {}
    reconstructed_content_checks = 0
    for item in json.loads(manifest_path.read_text()):
        path = archive_dir / Path(item["path"]).name
        assert sha256(path.read_bytes()).hexdigest() == item["sha256"]
        inputs.append(path)
        model = path.name.split("_all_")[0]
        raw_rows = read_csv(path)
        assert len(raw_rows) == len(tasks)
        for task in permissions:
            row = raw_rows[task]
            assert row["task"] == tasks[task]["task"]
            record = decode_record(row["full_response"])
            steps = []
            for step in record["trace"]:
                if step["action"] in (None, "Final Answer"):
                    continue
                narrative = step["llm_output"]
                try:
                    message = json.loads(narrative)
                    if isinstance(message, dict):
                        narrative = message.get("content", "") or ""
                except (TypeError, ValueError):
                    pass
                steps.append(
                    {
                        "name": step["action"],
                        "arguments": step["action_input"],
                        "receipt": step["observation"],
                        "assistant_text": narrative,
                    }
                )
            assert [(s["name"], s["arguments"]) for s in steps] == [
                tuple(s) for s in record["intermediate_steps"]
            ]
            observed = {
                i
                for i, s in enumerate(steps)
                if s["name"] in SUCCESS and s["receipt"] == SUCCESS[s["name"]]
            }
            assert observed == {k[2] for k in events if k[:2] == (model, task)}
            archived[model, task] = steps
            names = requested_names(row["task"])
            content = {
                "inputs": {"instruction": row["task"]},
                "outputs": {"steps": steps, "final_text": record["output"]},
            }
            original_aliases = None
            for position in observed:
                key = model, task, position
                event, step = events[key], steps[position]
                assert event["tool"] == step["name"]
                assert event["dest"] == step["arguments"]["recipient"].lower()
                assert event["source"] == step["arguments"].get("email_id")
                assert (
                    raw_decision(steps, position, names, event["dest"])
                    == event["natural_verdict"]
                )
                if key in corrections:
                    if original_aliases is None:
                        original_aliases = aliases(content)
                    for correction in corrections[key]["corrections"]:
                        changed = deepcopy(content)
                        changed["outputs"]["steps"][position]["arguments"][
                            "recipient"
                        ] = correction["recipient"]
                        assert (aliases(changed) == original_aliases) == correction[
                            "equality"
                        ]["canonical"]
                        reconstructed_content_checks += 1

    # Validate first-call matching independently from the saved first_position flag.
    expected = set()
    donors_by_scope = defaultdict(lambda: defaultdict(list))
    for key, event in events.items():
        if runs[key[:2]]["recipient_truth"] == "permitted":
            donors_by_scope[key[1], event["tool"], event["source"]][key[0]].append(
                key[2]
            )
    for key, event in errors.items():
        for model, positions in donors_by_scope[
            key[1], event["tool"], event["source"]
        ].items():
            if model != key[0]:
                expected.update((key, model, position) for position in positions)
    observed = set()
    full_correct_labels = defaultdict(set)
    for row in replays:
        key = error_key(row)
        position_key = key, row["donor_model"], row["donor_step"]
        assert position_key not in observed
        observed.add(position_key)
        source = errors[key]
        donor_key = row["donor_model"], row["error_task"], row["donor_step"]
        donor = events[donor_key]
        assert row["wrong_destination"] == source["dest"]
        assert row["source"] == source["source"] == donor["source"]
        assert row["tool"] == source["tool"] == donor["tool"]
        assert row["donor_original_destination"] == donor["dest"]
        assert row["donor_original_canonical"] == donor["natural_verdict"]
        positions = donors_by_scope[key[1], source["tool"], source["source"]][
            row["donor_model"]
        ]
        assert row["first_position"] == (row["donor_step"] == min(positions))
        full_correct_labels[donor_key[:2]].add(row["donor_full_task_correct"])
        assert (
            row["valid"]
            and row["donor_original_valid"]
            and not row["later_receipt_changed"]
        )
        independently_scored = raw_decision(
            archived[donor_key[:2]],
            donor_key[2],
            requested_names(tasks[key[1]]["task"]),
            source["dest"],
        )
        assert independently_scored == row["raw"] == row["canonical"]
    assert expected == observed
    assert all(len(values) == 1 for values in full_correct_labels.values())

    selected = [
        r
        for r in replays
        if r["valid"]
        and r["first_position"]
        and r["donor_full_task_correct"]
        and r["donor_model"] != "gpt-4.1"
    ]
    placements = defaultdict(list)
    for row in selected:
        placements[error_key(row)].append(row)
    error_rows = []
    for key, event in sorted(errors.items()):
        group = placements.get(key, [])
        error_rows.append(
            {
                "model": key[0],
                "task": key[1],
                "step": key[2],
                "template": tasks[key[1]]["base_template"],
                "destination": event["dest"],
                "source_message": event["source"],
                "whole_export_collision_every": collision[key],
                "original_flagged": event["natural_verdict"] == "different",
                "successful_placements": len(group),
                "successful_flagged": sum(r["canonical"] == "different" for r in group),
                "successful_detection": (
                    mean(r["canonical"] == "different" for r in group) if group else ""
                ),
            }
        )

    def describe(keys, replay_groups=placements):
        keys = list(keys)
        matched = [k for k in keys if replay_groups.get(k)]
        all_rows = [r for k in matched for r in replay_groups[k]]
        original = (
            mean(errors[k]["natural_verdict"] == "different" for k in matched)
            if matched
            else None
        )
        inserted = (
            mean(
                mean(r["canonical"] == "different" for r in replay_groups[k])
                for k in matched
            )
            if matched
            else None
        )
        return {
            "wrong_actions": len(keys),
            "matched_actions": len(matched),
            "original_flagged": sum(
                errors[k]["natural_verdict"] == "different" for k in matched
            ),
            "original_detection": original,
            "successful_placements": len(all_rows),
            "successful_flagged": sum(r["canonical"] == "different" for r in all_rows),
            "equal_error_successful_detection": inserted,
            "equal_error_difference": inserted - original if matched else None,
            "collisions_every": sum(collision[k] for k in keys),
            "collision_rate": mean(collision[k] for k in keys) if keys else None,
        }

    group_details, deletions, weighted = [], [], []
    for dimension in GROUPS:
        groups = defaultdict(list)
        for key in errors:
            groups[group_value(key, dimension, events, tasks)].append(key)
        reports = []
        for label, keys in sorted(groups.items()):
            report = describe(keys)
            group_details.append({"grouping": dimension, "group": label, **report})
            reports.append(report)
            if dimension != "episode":
                remaining = set(errors) - set(keys)
                deletions.append(
                    {
                        "grouping": dimension,
                        "removed": label,
                        "removed_wrong_actions": len(keys),
                        **describe(remaining),
                    }
                )
        matched_reports = [r for r in reports if r["matched_actions"]]
        weighted.append(
            {
                "weighting": dimension,
                "groups_all_errors": len(reports),
                "groups_matched_errors": len(matched_reports),
                "original_detection": mean(
                    r["original_detection"] for r in matched_reports
                ),
                "successful_detection": mean(
                    r["equal_error_successful_detection"] for r in matched_reports
                ),
                "difference": mean(
                    r["equal_error_difference"] for r in matched_reports
                ),
                "collision_rate": mean(r["collision_rate"] for r in reports),
            }
        )
    donor_deletions = []
    for model in sorted({r["donor_model"] for r in selected}):
        remaining = {
            k: [r for r in rows if r["donor_model"] != model]
            for k, rows in placements.items()
        }
        donor_deletions.append(
            {
                "removed_donor": model,
                "errors_losing_all_donors": sum(
                    not rows for rows in remaining.values()
                ),
                **describe(errors, remaining),
            }
        )
    duplicate_calls, original_calls = defaultdict(set), defaultdict(set)
    for row in selected:
        key = row["donor_model"], row["error_task"], row["donor_step"]
        duplicate_calls[key + (row["wrong_destination"],)].add(row["canonical"])
        original_calls[key].add(row["donor_original_canonical"])
    assert all(
        len(values) == 1
        for values in [*duplicate_calls.values(), *original_calls.values()]
    )
    unique_outcomes = Counter(next(iter(v)) for v in duplicate_calls.values())
    source_counts = Counter(k[0] for k in errors)
    excluded = {model for model, count in source_counts.most_common(2)}
    baseline = describe(errors)
    assert (
        baseline["wrong_actions"],
        baseline["matched_actions"],
        baseline["original_flagged"],
        baseline["successful_placements"],
        baseline["successful_flagged"],
        baseline["collisions_every"],
    ) == (163, 158, 4, 2865, 2845, 94)

    ranges = {}
    for dimension in ("task", "template", "source_model", "destination"):
        rows = [r for r in deletions if r["grouping"] == dimension]
        ranges[dimension] = {}
        for metric in (
            "equal_error_difference",
            "equal_error_successful_detection",
            "collision_rate",
        ):
            low, high = min(rows, key=lambda r: r[metric]), max(
                rows, key=lambda r: r[metric]
            )
            ranges[dimension][metric] = {
                "minimum": low[metric],
                "minimum_removed": low["removed"],
                "minimum_remaining_errors": low["wrong_actions"],
                "maximum": high[metric],
                "maximum_removed": high["removed"],
                "maximum_remaining_errors": high["wrong_actions"],
            }
    report = {
        "analysis": "Post-hoc descriptive reaggregation of saved records; no independent sample or population interval.",
        "estimand": "Average successful-history detection first over eligible donor models within each original error, then across original errors; compare original decisions on the same errors.",
        "verification_boundary": "Independently reaggregates saved native-export equalities, checks all 223 against reconstructed alias content, and recomputes raw checker decisions from the original archives. Does not rerun native SDK exports, outbox writes, or the WorkBench full-task evaluator. Its saved full-task correctness labels are checked for consistency within episodes.",
        "input_sha256": {
            str(p.relative_to(root)): sha256(p.read_bytes()).hexdigest()
            for p in sorted(set(inputs))
        },
        "protocol_sha256": sha256(
            (Path(__file__).parent / "protocol.md").read_bytes()
        ).hexdigest(),
        "archive": {
            "episodes": len(runs),
            "archives": len({k[0] for k in runs}),
            "completed_actions": len(events),
            "permitted_actions": len(events) - len(errors),
            "errors": len(errors),
            "error_episodes": len({k[:2] for k in errors}),
            "error_tasks": len({k[1] for k in errors}),
            "error_sources": len(source_counts),
            "wrong_destinations": len({r["dest"] for r in errors.values()}),
            "wrong_destinations_in_roster": sum(
                r["dest"] in roster for r in errors.values()
            ),
            "original_decisions": dict(
                Counter(r["natural_verdict"] for r in errors.values())
            ),
        },
        "verification": {
            "reconstructed_archive_episodes": len(archived),
            "raw_checker_recomputed_original_actions": len(events),
            "raw_checker_recomputed_replays": len(replays),
            "reconstructed_content_corrections": reconstructed_content_checks,
            "saved_native_corrections": total_corrections,
            "raw_alias_disagreements": 0,
            "complete_candidate_selection_agreement": True,
            "both_sdk_correction_agreement": True,
        },
        "whole_export_equality": dict(equality_counts),
        "baseline": baseline,
        "matched_structure": {
            "error_tasks": len({k[1] for k in placements}),
            "error_sources": len({k[0] for k in placements}),
            "wrong_destinations": len({errors[k]["dest"] for k in placements}),
            "donor_models": len({r["donor_model"] for r in selected}),
            "donor_episodes": len(
                {(r["donor_model"], r["error_task"]) for r in selected}
            ),
            "donors_per_error_range": [
                min(map(len, placements.values())),
                max(map(len, placements.values())),
            ],
            "distinct_unmodified_donor_calls": len(original_calls),
            "unmodified_donor_verdicts": dict(
                Counter(next(iter(v)) for v in original_calls.values())
            ),
            "distinct_modified_donor_calls": len(duplicate_calls),
            "unique_modified_call_verdicts": dict(unique_outcomes),
            "unique_modified_call_detection": unique_outcomes["different"]
            / len(duplicate_calls),
        },
        "excluded_two_largest_error_sources": {
            "excluded": sorted(excluded),
            **describe(k for k in errors if k[0] not in excluded),
        },
        "balanced_weightings": weighted,
        "leave_one_group_out_ranges": ranges,
        "leave_one_donor_out": {
            "models": len(donor_deletions),
            "detection_minimum": min(
                r["equal_error_successful_detection"] for r in donor_deletions
            ),
            "detection_maximum": max(
                r["equal_error_successful_detection"] for r in donor_deletions
            ),
            "max_errors_losing_all_donors": max(
                r["errors_losing_all_donors"] for r in donor_deletions
            ),
        },
        "all_matched_task_differences_positive": all(
            r["equal_error_difference"] > 0
            for r in group_details
            if r["grouping"] == "task" and r["matched_actions"]
        ),
        "new_model_calls": 0,
        "new_native_replays": 0,
        "openrouter_spend_usd": 0,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    write_csv(output / "by-error.csv", error_rows)
    write_csv(output / "by-group.csv", group_details)
    write_csv(output / "leave-one-group-out.csv", deletions)
    write_csv(output / "leave-one-donor-out.csv", donor_deletions)
    write_csv(output / "balanced-weightings.csv", weighted)
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "verification",
                    "baseline",
                    "matched_structure",
                    "excluded_two_largest_error_sources",
                    "balanced_weightings",
                    "leave_one_donor_out",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
