"""Audit unmodified WorkBench runs. No generation, network tools, or model use."""

import ast
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.insert(0, str(PROJECT))
from canonical_identity.checker import decide, requested_names
from audit import EMAIL, canonical, map_strings
from workbench_replay import WB, decode, langsmith

SUCCESS = {
    "email.send_email": "Email sent successfully.",
    "email.forward_email": "Email forwarded successfully.",
}
FORWARD = "email.forward_email"
DIRECTORY = "company_directory.find_email_address"
csv.field_size_limit(2**28)


def read_csv(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", newline="") as stream:
        return list(csv.DictReader(stream))


def reference(row):
    """Read the benchmark answer key for scoring; never pass it to the checker."""
    calls = []
    for value in ast.literal_eval(row["outcome"]):
        call = ast.parse(value, mode="eval").body
        name = ast.unparse(call.func).removesuffix(".func")
        calls.append(
            (name, {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords})
        )
    return calls


def resolve(instruction, mailbox, directory):
    """Bounded parser for this benchmark's text, independent of its answer keys."""
    names = requested_names(instruction)
    if not names:
        raise ValueError("Unknown recipient instruction")
    dest = {directory[name] for name in names}
    if "send an email to " in instruction.lower():
        return dest, None
    subject = re.search(r"about '([^']+)'", instruction)
    sender = re.search(r"\bfrom ([a-z]+)", instruction.lower())
    relevant = [
        r
        for r in mailbox
        if r["inbox/outbox"] == "inbox"
        and (not subject or r["subject"] == subject[1])
        and (not sender or r["sender/recipient"] == directory[sender[1]])
    ]
    if "last week" in instruction:
        relevant = [
            r
            for r in relevant
            if "2023-11-20" <= r["sent_datetime"][:10] <= "2023-11-26"
        ]
    elif relevant:
        latest = max(r["sent_datetime"] for r in relevant)
        relevant = [r for r in relevant if r["sent_datetime"] == latest]
    return dest, {r["email_id"] for r in relevant}


def archived_steps(parsed):
    """Recover tool calls and their surrounding assistant text from one saved run."""
    steps = []
    for s in parsed["trace"]:
        if s["action"] in (None, "Final Answer"):
            continue
        try:
            message = json.loads(s["llm_output"])
            narrative = (
                (message.get("content", "") or "")
                if isinstance(message, dict)
                else s["llm_output"]
            )
        except (TypeError, ValueError):
            narrative = s["llm_output"]
        steps.append(
            dict(
                name=s["action"],
                arguments=s["action_input"],
                receipt=s["observation"],
                assistant_text=narrative,
            )
        )
    assert [(s["name"], s["arguments"]) for s in steps] == [
        tuple(s) for s in parsed["intermediate_steps"]
    ]
    return steps


def full_views(task, steps, final):
    """Build the raw, masked, literal-alias and canonical-alias versions of a run."""
    r = dict(task=task, steps=steps, final_text=final)
    folded = map_strings(r, lambda value: EMAIL.sub(lambda m: m[0].lower(), value))
    views = {m: langsmith(r, m)[1] for m in ("raw", "mask", "tokens")}
    views["canonical"] = langsmith(folded, "tokens")[1]
    return views


def permitted_by_record(view, mode, step_index, use_all_lookups):
    """Inspect only the supplied export; later lookup evidence is an explicit control."""
    names = requested_names(view["inputs"]["instruction"])
    steps = view["outputs"]["steps"]
    known = {}
    for step in steps if use_all_lookups else steps[:step_index]:
        if step["name"] == DIRECTORY:
            arg = step["arguments"]
            if not isinstance(arg, dict) or not isinstance(arg.get("name"), str):
                continue
            name = arg["name"].lower()
            if name not in names:
                continue
            try:
                found = json.loads(step["receipt"])
            except (TypeError, ValueError):
                continue
            if (
                isinstance(found, list)
                and len(found) == 1
                and isinstance(found[0], str)
            ):
                known[name] = found[0]
    dest = steps[step_index]["arguments"]["recipient"]
    if mode == "mask" or not known:
        return "insufficient"
    if mode == "raw":
        dest = dest.lower()
        known = {k: v.lower() for k, v in known.items()}
    if dest in known.values():
        return "permitted"
    if mode == "tokens" or names != set(known):
        return "insufficient"
    return "different"


def digest(data):
    return hashlib.sha256(canonical(data).encode()).hexdigest()


def main():
    os.chdir(WB)

    def offline(*args, **kwargs):
        raise RuntimeError("Offline analysis")

    socket.socket.connect = offline
    tasks = read_csv(
        WB / "data/processed/tasks_and_outcomes/email_tasks_and_outcomes.csv"
    )
    mailbox = read_csv(WB / "data/processed/emails.csv")
    by_id = {r["email_id"]: r for r in mailbox}
    addresses = (WB / "data/raw/email_addresses.csv").read_text().splitlines()
    directory = {s.split(".")[0]: s for s in addresses}
    assert len(directory) == len(addresses)
    chosen = {}
    resolver_report = []
    for task_id, task in enumerate(tasks):
        reference_data = reference(task)
        if not reference_data or not all(name in SUCCESS for name, _ in reference_data):
            continue
        scope = (
            "forward" if all(name == FORWARD for name, _ in reference_data) else "send"
        )
        expected = {
            (args.get("email_id"), args["recipient"].lower())
            for _, args in reference_data
        }
        destinations, sources = resolve(task["task"], mailbox, directory)
        reconstructed = {
            (sid, addr)
            for sid in (sources if sources is not None else [None])
            for addr in destinations
        }
        resolver_report.append(
            {
                "task": task_id,
                "scope": scope,
                "agree": reconstructed == expected,
                "independent": sorted(reconstructed),
                "official": sorted(expected),
            }
        )
        chosen[task_id] = (scope, expected)
    records, actions = [], []
    for archive in sorted((HERE / "archive").glob("*.gz")):
        model = archive.name.split("_all_")[0]
        rows = read_csv(archive)
        assert len(rows) == len(tasks)
        for task_id, (scope, reference_data) in chosen.items():
            row, task = rows[task_id], tasks[task_id]
            assert row["task"] == task["task"]
            run_record = dict(
                model=model, task=task_id, scope=scope, archive_error=bool(row["error"])
            )
            records.append(run_record)
            try:
                if not row["full_response"]:
                    raise ValueError("Empty full_response")
                parsed = decode(row["full_response"])
                steps = archived_steps(parsed)
            except (
                ValueError,
                SyntaxError,
                TypeError,
                AssertionError,
                KeyError,
            ) as error:
                run_record.update(
                    parse_error=type(error).__name__ + ": " + str(error)[:100]
                )
                continue
            run_record["other_tools"] = sorted(
                {
                    s["name"]
                    for s in steps
                    if (s["name"] or "").split(".")[0]
                    not in ("email", "company_directory")
                }
            )
            run_record["failed_outbound"] = sum(
                s["name"] in SUCCESS and s["receipt"] != SUCCESS[s["name"]]
                for s in steps
            )
            run_record["completed_other_email_writes"] = [
                s["name"]
                for s in steps
                if s["name"] in ("email.delete_email", "email.reply_email")
                and isinstance(s["receipt"], str)
                and "success" in s["receipt"].lower()
            ]
            views = full_views(task["task"], steps, parsed["output"])
            run_record["view_digests"] = {m: digest(v) for m, v in views.items()}
            run_record["completed_direct_sends"] = 0
            run_record["completed_forwards"] = 0
            trace_actions = []
            for step_index, step in enumerate(steps):
                tool = step["name"]
                if tool not in SUCCESS or step["receipt"] != SUCCESS[tool]:
                    continue
                recipient = step["arguments"]["recipient"]
                origin = step["arguments"].get("email_id") if tool == FORWARD else None
                run_record[
                    (
                        "completed_forwards"
                        if tool == FORWARD
                        else "completed_direct_sends"
                    )
                ] += 1
                source_record = by_id.get(origin)
                measured = dict(
                    model=model,
                    task=task_id,
                    scope=scope,
                    step=step_index,
                    tool=tool,
                    recipient=recipient,
                    source=origin,
                    permitted_recipient=recipient.lower()
                    in {d for _, d in reference_data},
                    natural_case_variant=recipient != recipient.lower(),
                )
                if scope == "forward" and tool == FORWARD:
                    measured["reference_pair"] = (
                        origin,
                        recipient.lower(),
                    ) in reference_data
                    measured["source_matches"] = origin in {
                        s for s, _ in reference_data
                    }
                    measured["payload_matches"] = (
                        None
                        if source_record is None
                        else any(
                            (source_record["subject"], source_record["body"])
                            == (by_id[s]["subject"], by_id[s]["body"])
                            for s, _ in reference_data
                        )
                    )
                    measured["body_matches"] = (
                        None
                        if source_record is None
                        else any(
                            source_record["body"] == by_id[s]["body"]
                            for s, _ in reference_data
                        )
                    )
                    measured["actual_subject"] = (
                        source_record["subject"] if source_record else None
                    )
                measured["checker"] = {
                    m: permitted_by_record(v, m, step_index, False)
                    for m, v in views.items()
                }
                measured["retrospective_checker"] = {
                    m: permitted_by_record(v, m, step_index, True)
                    for m, v in views.items()
                }
                trace_actions.append(measured)
                actions.append(measured)
            try:
                run_record["original_workflow_checker"] = {
                    m: decide(v, "canonical_tokens" if m == "canonical" else m)
                    for m, v in views.items()
                }
            except (TypeError, KeyError, ValueError, AttributeError) as error:
                run_record["workflow_checker_error"] = type(error).__name__
            run_record["recipient_truth"] = (
                "none"
                if not trace_actions
                else (
                    "permitted"
                    if all(a["permitted_recipient"] for a in trace_actions)
                    else "different"
                )
            )
            forward = [a for a in trace_actions if "reference_pair" in a]
            run_record["forward_truth"] = (
                "none"
                if not forward
                else (
                    "permitted"
                    if all(a["reference_pair"] for a in forward)
                    else "different"
                )
            )
        print(f"Inspected {model}", flush=True)
    for name, values in [
        ("runs", records),
        ("actions", actions),
        ("permission-resolution", resolver_report),
    ]:
        (HERE / (name + ".jsonl")).write_text(
            "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in values)
        )
    print(
        f"{len(records)} eligible episodes, {len(actions)} completed outbound calls; {len(resolver_report)} request resolutions"
    )


if __name__ == "__main__":
    main()
