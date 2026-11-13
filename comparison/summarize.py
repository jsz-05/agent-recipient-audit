"""Rebuild the eligible match index and summarize the native replay verdicts."""

from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent
PILOT = ROOT.parent / "analysis"
BASE = "gpt-4.1"


def rows(path):
    return [json.loads(s) for s in path.read_text().splitlines()]


def error_key(r):
    return (r["error_model"], r["error_task"], r["error_step"])


def original_key(r):
    return (r["model"], r["task"], r["step"])


def split_errors(matches):
    groups = defaultdict(list)
    for r in matches:
        groups[error_key(r)].append(r)
    return groups


def counts(values):
    return dict(sorted(Counter(values).items()))


def summarize(matches, natural):
    valid = [r for r in matches if r["valid"]]
    groups = split_errors(valid)
    if not groups:
        return {
            "eligible_placements": len(matches),
            "validated_placements": 0,
            "errors": 0,
        }
    probabilities, by_task = [], defaultdict(list)
    for k, rs in groups.items():
        probability = mean(r["canonical"] == "different" for r in rs)
        natural_hit = natural[k]["canonical"] == "different"
        probabilities.append(probability)
        by_task[k[1]].append((probability, natural_hit))
    return {
        "eligible_placements": len(matches),
        "validated_placements": len(valid),
        "invalid_reasons": counts(r["reason"] for r in matches if not r["valid"]),
        "errors": len(groups),
        "tasks": len(by_task),
        "wrong_source_models": len({k[0] for k in groups}),
        "donor_models": len({r["donor_model"] for r in valid}),
        "donor_episodes": len({(r["donor_model"], r["error_task"]) for r in valid}),
        "distinct_modified_donor_calls": len(
            {
                (
                    r["donor_model"],
                    r["error_task"],
                    r["donor_step"],
                    r["wrong_destination"],
                )
                for r in valid
            }
        ),
        "comparisons_per_error_range": [
            min(map(len, groups.values())),
            max(map(len, groups.values())),
        ],
        "inserted_verdicts": counts(r["canonical"] for r in valid),
        "natural_verdicts_on_same_errors": counts(
            natural[k]["canonical"] for k in groups
        ),
        "errors_detected_in_all_comparisons": sum(p == 1 for p in probabilities),
        "errors_detected_in_some_comparisons": sum(0 < p < 1 for p in probabilities),
        "errors_detected_in_no_comparison": sum(p == 0 for p in probabilities),
        "equal_error_mean_detection": mean(probabilities),
        "natural_detection_on_same_errors": mean(
            natural[k]["canonical"] == "different" for k in groups
        ),
        "equal_task_inserted": mean(
            mean(p for p, _ in task) for task in by_task.values()
        ),
        "equal_task_natural": mean(
            mean(y for _, y in task) for task in by_task.values()
        ),
        "raw_canonical_disagreements": sum(r["raw"] != r["canonical"] for r in valid),
    }


def validate_selection(matches, genuine, old):
    events = rows(PILOT / "checked-outboxes.jsonl")
    runs = {
        (r["model"], r["task"]): r["recipient_truth"]
        for r in rows(PILOT / "runs.jsonl")
    }
    donors = defaultdict(list)
    for a in events:
        if runs[a["model"], a["task"]] == "permitted":
            donors[a["task"], a["tool"], a["source"]].append(a)
    expected = {}
    for a in events:
        if a["permitted"]:
            continue
        k = original_key(a)
        assert (
            genuine[k]["dest"] == a["dest"]
            and genuine[k]["canonical"] == a["natural_verdict"]
        )
        by_model = defaultdict(list)
        for b in donors[a["task"], a["tool"], a["source"]]:
            if b["model"] != a["model"]:
                by_model[b["model"]].append(b["step"])
        for model, positions in by_model.items():
            for i in positions:
                expected[k, model, i] = (i == min(positions), a["dest"])
    observed = {
        (error_key(r), r["donor_model"], r["donor_step"]): (
            r["first_position"],
            r["wrong_destination"],
        )
        for r in matches
    }
    assert len(matches) == len(observed) and observed == expected
    baseline_positions = defaultdict(dict)
    expected_check_disagreements = []
    for r in matches:
        if not r["valid"]:
            continue
        if r["donor_model"] == BASE:
            baseline_positions[error_key(r)][r["donor_step"]] = r["canonical"]
        prior = r["prior_lookup"]
        all_names = (
            prior["unique_exact_names"] == prior["names_required"]
            and prior["intended_returned"] == prior["names_required"]
        )
        if (r["canonical"] == "different") != all_names:
            expected_check_disagreements.append(
                (error_key(r), r["donor_model"], r["donor_step"])
            )
    for r in old:
        compare = baseline_positions[original_key(r)]
        assert sorted(compare) == r["donor_positions"]
        assert [compare[i] for i in sorted(compare)] == r["donor_verdicts"]
    return expected_check_disagreements


def main():
    matches = rows(ROOT / "matched-replays.jsonl")
    natural = {original_key(r): r for r in rows(ROOT / "natural-actions.jsonl")}
    old = rows(PILOT / "matched-pairs.jsonl")
    evidence_disagreements = validate_selection(matches, natural, old)
    old_keys = {original_key(r) for r in old if r["donor_positions"]}
    other = [r for r in matches if r["donor_model"] != BASE]
    primary = [r for r in other if r["first_position"]]
    orig = [r for r in matches if r["donor_model"] == BASE and r["first_position"]]
    groups = split_errors(primary)
    strict = [r for r in primary if r["donor_full_task_correct"]]
    observed_models = sorted({r["donor_model"] for r in primary})
    donor_reports = {
        model: summarize([r for r in primary if r["donor_model"] == model], natural)
        for model in observed_models
    }
    leave_one = [
        summarize([r for r in primary if r["donor_model"] != model], natural)[
            "equal_error_mean_detection"
        ]
        for model in observed_models
    ]
    # Each original donor call contributes once for its unmodified negative control.
    donor_controls = {
        (r["donor_model"], r["error_task"], r["donor_step"]): r[
            "donor_original_canonical"
        ]
        for r in primary
        if r["valid"]
    }
    report = {
        "protocol": "See protocol.md. Primary average gives equal weight to each observed error.",
        "primary": summarize(primary, natural),
        "same_errors_as_previous": summarize(
            [r for r in primary if error_key(r) in old_keys], natural
        ),
        "recomputed_previous_reference": summarize(orig, natural),
        "only_workbench_final_state_correct": summarize(strict, natural),
        "all_eligible_call_positions": summarize(other, natural),
        "original_errors": len(natural),
        "old_errors_without_new_match": len(old_keys - set(groups)),
        "newly_covered_errors": len(set(groups) - old_keys),
        "unmatched_errors": [list(k) for k in sorted(set(natural) - set(groups))],
        "donor_original_unmodified_verdicts": counts(donor_controls.values()),
        "alternate_donor_breakdown": donor_reports,
        "leave_out_one_donor_model_equal_error_rate_range": [
            min(leave_one),
            max(leave_one),
        ],
        "positive_verdict_vs_all_required_bindings_disagreements": len(
            evidence_disagreements
        ),
        "insufficient_prior_lookups": counts(
            r["prior_lookup"]["queries"]
            for r in primary
            if r.get("canonical") == "insufficient"
        ),
        "insufficient_prior_bound_names": counts(
            r["prior_lookup"]["unique_exact_names"]
            for r in primary
            if r.get("canonical") == "insufficient"
        ),
        "later_receipts_changed_in_primary_comparisons": sum(
            r.get("later_receipt_changed", False) for r in primary
        ),
        "new_llm_calls": 0,
        "openrouter_spend_usd": 0,
    }
    (ROOT / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    with (ROOT / "by-error.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "model",
                "task",
                "step",
                "tool",
                "source",
                "observed_destination",
                "observed_verdict",
                "earlier_gpt41_match",
                "other_histories",
                "validated",
                "detected",
                "insufficient",
                "permitted",
                "fully_task_correct_histories",
                "detected_in_fully_correct_histories",
            ]
        )
        for k, a in sorted(natural.items()):
            rs = groups.get(k, [])
            valid = [r for r in rs if r["valid"]]
            fully = [r for r in valid if r["donor_full_task_correct"]]
            c = Counter(r["canonical"] for r in valid)
            writer.writerow(
                [
                    *k,
                    a["scope"],
                    a["source"],
                    a["dest"],
                    a["canonical"],
                    k in old_keys,
                    len(rs),
                    len(valid),
                    c["different"],
                    c["insufficient"],
                    c["permitted"],
                    len(fully),
                    sum(r["canonical"] == "different" for r in fully),
                ]
            )
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "primary",
                    "same_errors_as_previous",
                    "only_workbench_final_state_correct",
                    "all_eligible_call_positions",
                    "recomputed_previous_reference",
                    "leave_out_one_donor_model_equal_error_rate_range",
                    "positive_verdict_vs_all_required_bindings_disagreements",
                    "insufficient_prior_lookups",
                    "insufficient_prior_bound_names",
                    "old_errors_without_new_match",
                    "newly_covered_errors",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
