"""Recompute the manuscript counts from saved per-action results using only Python."""

import argparse
from collections import Counter
from pathlib import Path
import json


def verify(directory):
    def rows(name):
        return [
            json.loads(line)
            for line in (directory / (name + ".jsonl")).read_text().splitlines()
        ]

    def keyed(data):
        result = {(r["model"], r["task"], r["step"]): r for r in data}
        assert len(result) == len(data)
        return result

    runs, events = rows("runs"), keyed(rows("checked-outboxes"))
    original, controls = keyed(rows("corrected-exports")), keyed(
        rows("export-controls")
    )
    paired = rows("matched-pairs")
    assert len(runs) == 1200 and Counter(r["recipient_truth"] for r in runs) == {
        "none": 56,
        "permitted": 1020,
        "different": 124,
    }
    assert (
        len(events) == 1409 and len(original) == 163 and set(original) == set(controls)
    )
    assert all((key in original) != event["permitted"] for key, event in events.items())
    flags, one, corrections = Counter(), Counter(), 0
    for key, rec in original.items():
        assert rec["original_native_matches"] and rec["orig_error"] is None
        pair = []
        for a, b in zip(rec["corrections"], controls[key]["corrections"], strict=True):
            assert (
                a["recipient"] == b["recipient"]
                and a["native_error"] is None
                and a["downstream_equal"]
            )
            v, w = a["equality"], b["equality"]
            assert (
                v["canonical"]
                == v["tokens"]
                == w["grafana_canonical"]
                == w["langsmith_hmac_equality"]
            )
            assert v["mask"] == w["grafana_mask"]
            assert (
                not v["raw"]
                and not w["langsmith_membership"]
                and not w["langsmith_hmac_bytes"]
            )
            pair.append(v)
        corrections += len(pair)
        for mode in ("canonical", "mask"):
            flags[mode, "every"] += all(c[mode] for c in pair)
            flags[mode, "any"] += any(c[mode] for c in pair)
        if rec["one_outbound"]:
            one["single"] += 1
            one["same"] += all(c["canonical"] for c in pair)
    assert corrections == 223 and one == {"single": 83, "same": 56}
    assert flags == {
        ("canonical", "every"): 94,
        ("canonical", "any"): 101,
        ("mask", "every"): 161,
        ("mask", "any"): 161,
    }
    same = [
        r
        for r in original.values()
        if all(c["equality"]["canonical"] for c in r["corrections"])
    ]
    assert (
        len({r["model"] for r in same}),
        len({r["task"] for r in same}),
        len({(r["model"], r["task"]) for r in same}),
    ) == (11, 40, 75)
    outcomes = Counter((a["permitted"], a["natural_verdict"]) for a in events.values())
    assert outcomes == {
        (False, "different"): 4,
        (False, "insufficient"): 159,
        (True, "permitted"): 1230,
        (True, "insufficient"): 16,
    }
    assert all(a["natural_verdict"] == a["raw_verdict"] for a in events.values())
    wrong = [a for a in events.values() if not a["permitted"]]
    for field, expected in [
        ("company_domain", 123),
        ("directory_format", 157),
        ("in_directory", 163),
    ]:
        assert sum(not a[field] for a in wrong) == expected
        assert all(a[field] for a in events.values() if a["permitted"])
    unresolved = [
        a["permission_sources"] for a in wrong if a["natural_verdict"] == "insufficient"
    ]
    assert Counter(a["queries"] for a in unresolved) == {0: 138, 1: 21}
    assert all(a["intended_returned"] == 0 for a in unresolved)
    matched = [r for r in paired if r["donor_positions"]]
    assert (
        len(matched) == 158
        and sum(r["natural_verdict"] == "different" for r in matched) == 4
    )
    assert all(set(r["donor_verdicts"]) == {"different"} for r in matched)
    assert (
        len({(r["task"], i, r["dest"]) for r in matched for i in r["donor_positions"]})
        == 109
    )
    report = json.loads((directory / "summary.json").read_text())
    assert (
        report["within_directory_wrong_controls"]
        == report["within_directory_wrong_all_listed"]
        == 50
    )
    assert all(r["agree"] for r in rows("permission-resolution"))
    print(
        "Verified 1,409 outbox actions, 223 corrections for 163 wrong sends, both native exports, both tables and the matched comparison."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    verify(parser.parse_args().directory)
