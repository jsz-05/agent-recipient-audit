"""Place each other listed employee into the mistaken agent's own pre-send history."""

from collections import Counter, defaultdict
from copy import deepcopy
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
    langsmith,
    map_strings,
    permitted_by_record,
    read_csv,
    reference,
)
from matched_checks import lines
from workbench_replay import TOOLS, get_state
from evaluate import public_receipt, start_episode
from receipt import committed_recipient, review


def main():
    os.chdir(WB)

    def offline(*args, **kwargs):
        raise RuntimeError("Network disabled during coworker comparison")

    socket.socket.connect = socket.socket.connect_ex = offline
    tasks = read_csv(
        WB / "data/processed/tasks_and_outcomes/email_tasks_and_outcomes.csv"
    )
    targets = defaultdict(list)
    for r in lines("checked-outboxes"):
        if not r["permitted"]:
            targets[r["model"], r["task"]].append(r)
    out = []
    for file in sorted((PILOT / "archive").glob("*.gz")):
        model = file.name.split("_all_")[0]
        for task_id, row in enumerate(read_csv(file)):
            if (model, task_id) not in targets:
                continue
            original = archived_steps(decode(row["full_response"]))
            instruction = row["task"]
            assert instruction == tasks[task_id]["task"]
            authorized = {
                args["recipient"].lower() for _, args in reference(tasks[task_id])
            }
            initial_grant = start_episode(instruction)
            roster = set(get_state().directory_emails.email_address)
            assert len(roster) == 20
            alternatives = sorted(roster - authorized)
            for target in targets[model, task_id]:
                i = target["step"]
                for alternate in alternatives:
                    grant = start_episode(instruction)
                    assert grant == initial_grant
                    state = get_state()
                    prefix = deepcopy(original[: i + 1])
                    for step in prefix[:-1]:
                        assert (
                            TOOLS[step["name"]](**step["arguments"]) == step["receipt"]
                        )
                    last = prefix[-1]
                    assert (
                        last["name"] == target["tool"]
                        and last["arguments"].get("email_id") == target["source"]
                    )
                    last["arguments"]["recipient"] = alternate
                    before = len(state.emails)
                    last["receipt"] = TOOLS[last["name"]](**last["arguments"])
                    destination = committed_recipient(state.emails, before)
                    assert (
                        last["receipt"] == SUCCESS[last["name"]]
                        and destination == alternate
                        and destination not in authorized
                    )
                    run = dict(task=instruction, steps=prefix, final_text="")
                    folded = map_strings(
                        run, lambda text: EMAIL.sub(lambda m: m[0].lower(), text)
                    )
                    view = langsmith(folded, "tokens")[1]
                    trace = permitted_by_record(view, "canonical", i, False)
                    rec = public_receipt(grant, destination)
                    member = destination in roster
                    out.append(
                        dict(
                            model=model,
                            task=task_id,
                            step=i,
                            source=target["source"],
                            substitute=alternate,
                            member=member,
                            trace=trace,
                            combined=trace if member else "different",
                            receipt=rec,
                            verdict=review(rec),
                        )
                    )
    grouped = defaultdict(list)
    for r in out:
        grouped[r["model"], r["task"], r["step"]].append(r)
    assert set(grouped) == {
        (m, t, r["step"]) for (m, t), rows in targets.items() for r in rows
    }
    assert all(len(v) == len({r["substitute"] for r in v}) for v in grouped.values())
    summary = dict(
        placements=len(out),
        observed_errors=len(grouped),
        alternatives_per_error=dict(Counter(map(len, grouped.values()))),
        trace=dict(Counter(r["trace"] for r in out)),
        combined=dict(Counter(r["combined"] for r in out)),
        receipt=dict(Counter(r["verdict"] for r in out)),
        roster_rejections=sum(not r["member"] for r in out),
        trace_per_error=dict(
            Counter("/".join(sorted({r["trace"] for r in v})) for v in grouped.values())
        ),
    )
    (OUT / "coworkers.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in out)
    )
    (OUT / "coworkers-summary.json").write_text(
        json.dumps(summary, sort_keys=True, indent=2) + "\n"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
