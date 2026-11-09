"""Match naturally selected wrong destinations to lookup-complete comparison runs.

WorkBench is a local, in-memory simulator. This module does not send messages.
Run after audit_archive.py with the research environment's Python interpreter.
"""

from collections import Counter, defaultdict
from copy import deepcopy
import json
import os
import re
import socket

from audit_archive import (
    DIRECTORY,
    EMAIL,
    FORWARD,
    HERE,
    PROJECT,
    SUCCESS,
    WB,
    archived_steps,
    decode,
    langsmith,
    map_strings,
    permitted_by_record,
    read_csv,
    requested_names,
)
from workbench_replay import TOOLS, get_state, load_tasks, replay, reset_state


def lines(name):
    return [
        json.loads(line) for line in (HERE / (name + ".jsonl")).read_text().splitlines()
    ]


def save(name, rows):
    (HERE / (name + ".jsonl")).write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )


def lookup_evidence(steps, names, permitted):
    lookups = [s for s in steps if s["name"] == DIRECTORY]
    any_hits, exact = set(), set()
    for lookup in lookups:
        try:
            reply = json.loads(lookup["receipt"])
        except (TypeError, ValueError):
            continue
        if not isinstance(reply, list):
            continue
        any_hits.update(
            addr.lower()
            for addr in reply
            if isinstance(addr, str) and addr.lower() in permitted
        )
        arg = lookup["arguments"]
        query = arg.get("name", "") if isinstance(arg, dict) else ""
        if isinstance(query, str) and query.lower() in names and len(reply) == 1:
            exact.add(query.lower())
    return {
        "queries": len(lookups),
        "intended_returned": len(any_hits),
        "unique_exact_names": len(exact),
        "names_required": len(names),
    }


def main():
    os.chdir(WB)

    def offline(*args, **kwargs):
        raise RuntimeError("Network disabled for replay")

    socket.socket.connect = offline
    actions, runs = lines("actions"), lines("runs")
    by_episode = defaultdict(list)
    for a in actions:
        by_episode[a["model"], a["task"]].append(a)
    reference = {
        r["task"]: {dest for _, dest in r["independent"]}
        for r in lines("permission-resolution")
    }
    assert all(r["agree"] for r in lines("permission-resolution"))
    tasks = {t["id"]: t for t in load_tasks()}
    roster = set((WB / "data/raw/email_addresses.csv").read_text().splitlines())
    assert len(roster) == 20 and all(
        re.fullmatch(r"[a-z]+\.[a-z]+@atlas\.com", a) for a in roster
    )
    reset_state()
    state = get_state()
    mailbox = state.emails.copy(deep=True)
    diagnostics = []
    for archive in sorted((HERE / "archive").glob("*.gz")):
        model = archive.name.split("_all_")[0]
        rows = read_csv(archive)
        for tid, task in tasks.items():
            observed = by_episode.get((model, tid), [])
            if not observed:
                continue
            rec = decode(rows[tid]["full_response"])
            steps = archived_steps(rec)
            for a in observed:
                index = a["step"]
                s = steps[index]
                # Restore only the mailbox; these native send/forward functions have no other writes.
                state.emails = mailbox.copy(deep=True)
                receipt = TOOLS[s["name"]](**s["arguments"])
                assert receipt == s["receipt"] == SUCCESS[s["name"]]
                assert len(state.emails) == len(mailbox) + 1
                delivered = state.emails.iloc[-1]
                dest = delivered["sender/recipient"]
                assert (
                    delivered["inbox/outbox"] == "outbox"
                    and dest == a["recipient"].lower()
                )
                assert (dest in reference[tid]) == a["permitted_recipient"]
                verdict = a["checker"]["canonical"]
                diag = dict(
                    model=model,
                    task=tid,
                    step=index,
                    tool=a["tool"],
                    source=a["source"],
                    request=task["task"],
                    dest=dest,
                    permitted=a["permitted_recipient"],
                    natural_verdict=verdict,
                    raw_verdict=a["checker"]["raw"],
                    in_directory=dest in roster,
                    company_domain=dest.endswith("@atlas.com"),
                    directory_format=bool(
                        re.fullmatch(r"[a-z]+\.[a-z]+@atlas\.com", dest)
                    ),
                    permission_sources=lookup_evidence(
                        steps[:index], requested_names(task["task"]), reference[tid]
                    ),
                )
                diagnostics.append(diag)
    assert len(diagnostics) == len(actions)
    save("checked-outboxes", diagnostics)
    # Compare every eligible original donor call; one wrong action contributes one row.
    donors = {tid: replay(task) for tid, task in tasks.items()}
    assert all(r["recipient_authorized"] for r in donors.values())
    cache = {}

    def donor_verdict(tid, index, dest):
        key = (tid, index, dest)
        if key in cache:
            return cache[key]
        t = deepcopy(tasks[tid])
        t["steps"][index]["action_input"]["recipient"] = dest
        replayed = replay(t)
        outbound = [
            s
            for s in replayed["steps"][: index + 1]
            if s["name"] in SUCCESS and s["receipt"] == SUCCESS[s["name"]]
        ]
        assert replayed["actual_recipients"][len(outbound) - 1] == dest
        assert (
            replayed["steps"][index]["receipt"] == SUCCESS[t["steps"][index]["action"]]
        )
        folded = map_strings(
            replayed, lambda value: EMAIL.sub(lambda m: m[0].lower(), value)
        )
        view = langsmith(folded, "tokens")[1]
        cache[key] = permitted_by_record(view, "canonical", index, False)
        return cache[key]

    pairs = []
    for a in diagnostics:
        if a["permitted"]:
            continue
        donor = donors[a["task"]]
        positions = [
            i
            for i, s in enumerate(donor["steps"])
            if s["name"] == a["tool"]
            and s["receipt"] == SUCCESS[s["name"]]
            and s["arguments"].get("email_id") == a["source"]
        ]
        verdicts = [donor_verdict(a["task"], i, a["dest"]) for i in positions]
        pairs.append(dict(**a, donor_positions=positions, donor_verdicts=verdicts))
    save("matched-pairs", pairs)
    wrong_neutral = [
        replay(task, mutated=True, replacement=min(roster - reference[tid]))
        for tid, task in tasks.items()
    ]
    assert len(wrong_neutral) == 50 and all(
        not r["recipient_authorized"] for r in wrong_neutral
    )

    def tally(xs):
        return dict(sorted(Counter(xs).items()))

    def metrics(key):
        return {
            label: tally(a[key] for a in diagnostics if a["permitted"] == correct)
            for label, correct in [("permitted", True), ("wrong", False)]
        }

    wrong = [a for a in diagnostics if not a["permitted"]]
    common = [a for a in pairs if a["donor_positions"]]
    insufficient = [a for a in wrong if a["natural_verdict"] == "insufficient"]
    summary = {
        "archives": len({r["model"] for r in runs}),
        "eligible_tasks": len(tasks),
        "episodes": len(runs),
        "episodes_by_truth": tally(r["recipient_truth"] for r in runs),
        "original_workflow_decisions": tally(
            r["recipient_truth"]
            + " / "
            + str(r.get("original_workflow_checker", {}).get("canonical"))
            for r in runs
        ),
        "outboxes_verified": len(diagnostics),
        "wrong": len(wrong),
        "wrong_episodes": len({(a["model"], a["task"]) for a in wrong}),
        "wrong_models": len({a["model"] for a in wrong}),
        "wrong_tasks": len({a["task"] for a in wrong}),
        "natural_canonical": metrics("natural_verdict"),
        "natural_raw": metrics("raw_verdict"),
        "directory_membership": metrics("in_directory"),
        "literal_domain": metrics("company_domain"),
        "directory_format": metrics("directory_format"),
        "insufficient_wrong_prior_lookup_counts": tally(
            a["permission_sources"]["queries"] for a in insufficient
        ),
        "insufficient_wrong_permitted_addresses_returned": tally(
            a["permission_sources"]["intended_returned"] for a in insufficient
        ),
        "matched_actions": len(common),
        "unmatched_actions": len(pairs) - len(common),
        "matched_tasks": len({a["task"] for a in common}),
        "matched_models": len({a["model"] for a in common}),
        "matched_episodes": len({(a["task"], a["model"]) for a in common}),
        "matched_donor": tally(a["donor_verdicts"][0] for a in common),
        "matched_natural": tally(a["natural_verdict"] for a in common),
        "within_action_donor_disagreements": sum(
            len(set(a["donor_verdicts"])) > 1 for a in common
        ),
        "unique_donor_placements": len(cache),
        "within_directory_wrong_controls": len(wrong_neutral),
        "within_directory_wrong_all_listed": sum(
            all(d in roster for d in r["actual_recipients"]) for r in wrong_neutral
        ),
        "natural_literal_case_variants": sum(
            a["natural_case_variant"] for a in actions
        ),
        "body_differs_with_correct_recipient": sum(
            a.get("body_matches") is False and a["permitted_recipient"] for a in actions
        ),
    }
    (HERE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
