"""Verify the manuscript's counts from the distributed rows with standard Python."""

from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import mean

from verify_findings import verify

ROOT = Path(__file__).resolve().parent


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def main():
    verify(ROOT / "saved")
    history = ROOT / "saved-comparison"
    natural = {
        (r["model"], r["task"], r["step"]): r
        for r in read_rows(history / "natural-actions.jsonl")
    }
    matches = [
        r
        for r in read_rows(history / "matched-replays.jsonl")
        if r["first_position"]
        and r["donor_full_task_correct"]
        and r["donor_model"] != "gpt-4.1"
    ]
    by_error = defaultdict(list)
    for row in matches:
        key = row["error_model"], row["error_task"], row["error_step"]
        assert row["valid"]
        assert row["donor_original_valid"] and row["raw"] == row["canonical"]
        assert (
            row["wrong_destination"] == natural[key]["dest"]
            and row["source"] == natural[key]["source"]
        )
        by_error[key].append(row)
    correct_donors = {(r["error_task"], r["donor_model"]) for r in matches}
    placements = {
        (r["error_task"], r["donor_model"], r["donor_step"], r["wrong_destination"])
        for r in matches
    }
    assert (
        len(natural),
        len(by_error),
        len(matches),
        len(correct_donors),
        len(placements),
    ) == (163, 158, 2865, 801, 1456)
    assert Counter(r["canonical"] for r in matches) == {
        "different": 2845,
        "insufficient": 20,
    }
    assert Counter(natural[key]["canonical"] for key in by_error) == {
        "different": 4,
        "insufficient": 154,
    }
    no_decision = [r for r in matches if r["canonical"] == "insufficient"]
    assert all(r["prior_lookup"]["names_required"] == 2 for r in no_decision)
    assert Counter(r["prior_lookup"]["unique_exact_names"] for r in no_decision) == {
        0: 9,
        1: 11,
    }
    task_rates = defaultdict(list)
    error_rates = []
    for key, group in by_error.items():
        rate = mean(r["canonical"] == "different" for r in group)
        error_rates.append(rate)
        task_rates[key[1]].append((rate, natural[key]["canonical"] == "different"))
    assert len(task_rates) == 43 and all(
        mean(a for a, b in v) > mean(b for a, b in v) for v in task_rates.values()
    )
    assert round(mean(error_rates) * 100, 2) == 99.33
    receipts = ROOT / "saved-receipts"
    checked = read_rows(receipts / "actions.jsonl")
    natural_actions = {
        (r["model"], r["task"], r["step"]): r
        for r in read_rows(ROOT / "saved" / "checked-outboxes.jsonl")
    }
    assert {
        (r["model"], r["task"], r["step"]) for r in checked if r["group"] == "archive"
    } == set(natural_actions)
    assert len(checked) == 1459

    def independent_verdict(rec):
        grant, dest = rec["permitted"], rec["committed"]
        if dest is None or not grant:
            return "insufficient"
        if dest in [x for x in grant if x is not None]:
            return "permitted"
        return "insufficient" if any(x is None for x in grant) else "different"

    for r in checked:
        assert r["truth"] == r["verdict"] == independent_verdict(r["receipt"])
    assert Counter((r["group"], r["truth"]) for r in checked) == {
        ("archive", "permitted"): 1246,
        ("archive", "different"): 163,
        ("coworker swap", "different"): 50,
    }
    swap = [r for r in checked if r["group"] == "coworker swap"]
    assert (
        all(r["roster_member"] for r in swap) and len({r["task"] for r in swap}) == 50
    )
    assert Counter(r["trace_verdict"] for r in swap) == {
        "different": 47,
        "insufficient": 3,
    }
    partial = read_rows(receipts / "partial-grants.jsonl")
    assert Counter((r["truth"], r["verdict"]) for r in partial) == {
        ("permitted", "permitted"): 406,
        ("permitted", "insufficient"): 406,
        ("different", "insufficient"): 120,
    }
    assert all(independent_verdict(r["receipt"]) == r["verdict"] for r in partial)
    controls = read_rows(receipts / "interface-controls.jsonl")
    assert len(controls) == 8 and all(
        independent_verdict(r["receipt"]) == r["expected"] for r in controls
    )
    coworkers = read_rows(receipts / "coworkers.jsonl")
    places = defaultdict(list)
    for r in coworkers:
        key = r["model"], r["task"], r["step"]
        assert key in natural and r["member"] and r["trace"] == r["combined"]
        assert independent_verdict(r["receipt"]) == r["verdict"] == "different"
        places[key].append(r)
    assert set(places) == set(natural) and len(coworkers) == 3037
    assert all(len(v) == len({r["substitute"] for r in v}) for v in places.values())
    assert Counter(r["trace"] for r in coworkers) == {
        "different": 73,
        "insufficient": 2964,
    }
    assert Counter(tuple(sorted({r["trace"] for r in v})) for v in places.values()) == {
        ("different",): 4,
        ("insufficient",): 159,
    }
    print(
        "Verified 2,845/2,865 matched replays versus 4/158 observed errors across 43 tasks."
    )
    print(
        "Verified 1,409 archived receipts, 50 wrong-coworker swaps, 932 partial grants and 8 interface checks."
    )
    print("Verified 3,037 alternative coworkers at the 163 original error positions.")

    live = [
        json.loads(p.read_text()) for p in (ROOT / "live-study/runs").glob("*.json")
    ]
    actions = [a for r in live for a in r["actions"]]
    assert len(live) == 32 and len(actions) == 40
    assert all(r["status"] == "completed" and r["exact_forward_set"] for r in live)
    assert (
        sum(r["exposed"] for r in live) == 24
        and sum(len(r["steps"]) for r in live) == 113
    )
    assert all(a["truth"] == a["verdict"] == "permitted" for a in actions)
    replies = [
        json.loads(p.read_text())
        for p in (ROOT / "live-study/responses").glob("*.json")
    ]
    assert (
        len(replies) == 97
        and abs(sum(r["cost_usd"] for r in replies) - 0.049627225) < 1e-10
    )
    smtp = read_rows(ROOT / "smtp-study/saved/transactions.jsonl")
    control = read_rows(ROOT / "smtp-study/unknown-control/transactions.jsonl")
    assert len(smtp) == 500 and len(control) == 50
    assert Counter(r["verdict"] for r in smtp + control) == {
        "permitted": 100,
        "different": 250,
        "no_send": 100,
        "insufficient": 100,
    }
    for r in smtp + control:
        receipt = r["receipt"]
        assert "@" not in json.dumps(receipt)
        if receipt["status"] == "accepted":
            decisions = [
                independent_verdict({"permitted": receipt["permitted"], "committed": v})
                for v in receipt["accepted"]
            ]
            verdict = (
                "different"
                if "different" in decisions
                else "insufficient" if "insufficient" in decisions else "permitted"
            )
        else:
            verdict = (
                "no_send" if receipt["status"] == "not_accepted" else "insufficient"
            )
        assert verdict == r["verdict"]
        destinations = [a for s in r["server_records"] for a in s["recipients"]]
        truth = (
            "no_send"
            if not r["server_records"]
            else (
                "permitted"
                if set(destinations) <= set(r["grant_for_scoring"])
                else "different"
            )
        )
        assert truth == r["server_truth"]
        assert verdict == "insufficient" or verdict == truth
    lost = {r["task"]: r for r in smtp if r["condition"] == "lost_reply"}
    assert len(lost) == 50
    for r in control:
        other = lost[r["task"]]
        for key in [
            "rcpt_replies",
            "data_started",
            "data_reply",
            "receipt",
            "verdict",
            "requested_envelope",
            "visible_headers",
        ]:
            assert r[key] == other[key]
        assert r["server_truth"] == "no_send" and other["server_truth"] == "different"
    print(
        "Verified 32 live workflows and 40 permitted sends; API charges $0.049627225."
    )
    print(
        "Verified 550 SMTP transactions: 450 correct definitive judgments and 100 unknown outcomes."
    )


if __name__ == "__main__":
    main()
