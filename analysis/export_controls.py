"""Cross-check the observed/corrected export comparisons with alternate log policies."""

from collections import defaultdict
from copy import deepcopy
import hashlib
import hmac
import json
import os
import re
import socket

from audit_archive import EMAIL, HERE, WB, archived_steps, decode, map_strings, read_csv
from matched_checks import lines, save
from workbench_replay import FIXED, grafana
from langsmith import Client
from langsmith_native import CaptureSession, anonymizer

TOKEN = re.compile(r"EMAIL_HMAC_[0-9a-f]{64}")
KEY = b"authorization-evidence-synthetic-fixed-hmac-control-v1"


def keyed_or_member(record, mode, index, member):
    session = CaptureSession()

    def replace(match):
        return (
            "EMAIL_HMAC_"
            + hmac.new(KEY, match[0].lower().encode(), hashlib.sha256).hexdigest()
        )

    fn = (
        anonymizer("tokens")
        if mode == "member"
        else lambda obj: map_strings(obj, lambda s: EMAIL.sub(replace, s))
    )
    client = Client(
        api_url="http://capture.invalid",
        api_key="synthetic-not-a-key",
        session=session,
        auto_batch_tracing=False,
        anonymizer=fn,
        omit_traced_runtime_info=True,
        tracing_sampling_rate=1.0,
    )
    outputs = {"steps": record["steps"], "final_text": record["final_text"]}
    if mode == "member":
        outputs["monitor"] = {"step": index, "committed_address_in_directory": member}
    try:
        client.create_run(
            name="workbench-replay",
            run_type="chain",
            project_name="offline-audit",
            id="00000000-0000-0000-0000-000000000001",
            start_time=FIXED,
            end_time=FIXED,
            inputs={"instruction": record["task"]},
            outputs=outputs,
            extra={"metadata": {}},
        )
        assert len(session.captured) == 1
        return session.captured[0]
    finally:
        client.close()


def equality_structure(export):
    aliases = {}

    def relabel(match):
        val = match[0]
        if val not in aliases:
            aliases[val] = f"RENAMED_ADDRESS_{len(aliases)+1}"
        return aliases[val]

    return map_strings(export, lambda x: TOKEN.sub(relabel, x))


def variants(record, index, member):
    folded = map_strings(record, lambda s: EMAIL.sub(lambda m: m[0].lower(), s))
    keyed = keyed_or_member(record, "keyed", index, member)
    return {
        "grafana_mask": grafana(record, "mask")[1],
        "grafana_canonical": grafana(folded, "tokens")[1],
        "langsmith_hmac_bytes": keyed,
        "langsmith_hmac_equality": equality_structure(keyed),
        "langsmith_membership": keyed_or_member(folded, "member", index, member),
    }


def main():
    os.chdir(WB)

    def offline(*args, **kwargs):
        raise RuntimeError("Network disabled")

    socket.socket.connect = offline
    roster = set((WB / "data/raw/email_addresses.csv").read_text().splitlines())
    pairs = lines("corrected-exports")
    lookup = defaultdict(list)
    for r in pairs:
        assert r["original_native_matches"] and all(
            c["native_error"] is None and c["downstream_equal"]
            for c in r["corrections"]
        )
        lookup[r["model"], r["task"]].append(r)
    results = []
    for archive in sorted((HERE / "archive").glob("*.gz")):
        m = archive.name.split("_all_")[0]
        rows = read_csv(archive)
        for (model, tid), cases in lookup.items():
            if model != m:
                continue
            row = rows[tid]
            parsed = decode(row["full_response"])
            record = {
                "source_model": model,
                "task": row["task"],
                "steps": archived_steps(parsed),
                "final_text": parsed["output"],
            }
            for r in cases:
                i = r["step"]
                old = variants(record, i, r["dest"] in roster)
                tested = []
                for c in r["corrections"]:
                    changed = deepcopy(record)
                    changed["steps"][i]["arguments"]["recipient"] = c["recipient"]
                    new = variants(changed, i, c["recipient"] in roster)
                    equal = {mode: v == new[mode] for mode, v in old.items()}
                    tested.append(dict(recipient=c["recipient"], equality=equal))
                results.append(dict(model=m, task=tid, step=i, corrections=tested))
    save("export-controls", results)
    modes = results[0]["corrections"][0]["equality"]
    summary = {
        k: {
            "every": sum(
                all(c["equality"][k] for c in r["corrections"]) for r in results
            ),
            "any": sum(
                any(c["equality"][k] for c in r["corrections"]) for r in results
            ),
        }
        for k in modes
    }
    (HERE / "export-controls-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
