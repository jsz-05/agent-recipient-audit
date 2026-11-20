"""Reconstruct the older AppWorld recipient study without running agent code.

Python 3.12; standard library only. --data points to the original data/ tree;
--runs contains the unpacked official leaderboard folders. Outputs containing
benchmark content stay local. Only aggregate results are release artifacts.
"""

import argparse
import ast
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from resolver import resolve

HERE = Path(__file__).resolve().parent
ID_KEYS = {
    "36705ae": "partner_gmail_user_id",
    "4441ee9": "manager_gmail_user_id",
    "a1d3dfd": "partner_gmail_user_id",
    "f6f2e67": "parents_gmail_user_ids",
    "fddb6b6": "manager_gmail_user_id",
}
EMAIL_KEYS = {
    "98d2608": "recipient_email_address",
    "b71318e": "coworker_email_addresses",
    "baeb104": "relative_emails",
    "bcb9696": "manager_email",
}


def read(path):
    return json.loads(path.read_text())


def database(data, app, records_path):
    """Rebuild an in-memory database from the read-only base and archived data writes."""
    source = sqlite3.connect(f'file:{data/"base_dbs"/(app+".db")}?mode=ro', uri=True)
    db = sqlite3.connect(":memory:")
    source.backup(db)
    source.close()
    db.row_factory = sqlite3.Row
    db.enable_load_extension(False)
    for line in records_path.read_text().splitlines():
        statement, params, many = json.loads(line)
        if not re.match(
            r"^\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+", statement, re.I
        ):
            raise ValueError("Non-data SQL in archive")
        (db.executemany if many else db.execute)(statement, params)
    return db


def scoring_set(data, task, spec, initial_users):
    # Ground truth is used only after resolve() has returned its result.
    """Recover the benchmark answer key separately from the request-based resolver."""
    family = task.split("_")[0]
    private = read(data / "tasks" / task / "ground_truth/private_data.json")
    if family in ID_KEYS:
        ids = private[ID_KEYS[family]]
        ids = [ids] if isinstance(ids, int) else ids
        return {initial_users[i]["email"].casefold() for i in ids}
    if family in EMAIL_KEYS:
        values = private[EMAIL_KEYS[family]]
        values = [values] if isinstance(values, str) else values
        return {x.casefold() for x in values}
    if family == "fdc4b74":
        db = database(data, "admin", data / "tasks" / task / "dbs/admin.jsonl")
        result = {
            r["email"].casefold()
            for r in db.execute(
                "SELECT u.email FROM main_users u JOIN company_employees m ON m.main_user_id=u.id "
                "JOIN company_employees x ON x.company_id=m.company_id JOIN main_users o ON o.id=x.main_user_id "
                "WHERE o.email=? AND m.is_manager=1",
                (spec["supervisor"]["email"],),
            )
        }
        db.close()
        assert len(result) == 1
        return result
    raise ValueError("No independently reviewed scoring rule: " + task)


def receipt(allowed, actual):
    """Assign per-action aliases to the permission set and recorded destinations."""
    mapping = {a: i + 1 for i, a in enumerate(sorted(allowed))}
    for a in sorted(actual):
        mapping.setdefault(a, len(mapping) + 1)
    return {
        "permitted_slots": [mapping[a] for a in sorted(allowed)],
        "committed_destinations": [mapping[a] for a in sorted(actual)],
    }


def aggregates(rows):
    """Count errors and flags without deduplicating repeated sends."""
    errors = [r for r in rows if r["is_error"]]
    correct = [r for r in rows if not r["is_error"]]
    extra = [r for r in errors if not r["membership_flags"] and r["permission_flags"]]
    return {
        "messages": len(rows),
        "errors": len(errors),
        "recipient_correct": len(correct),
        "error_executions": len({(r["run"], r["task"]) for r in errors}),
        "error_tasks": len({r["task"] for r in errors}),
        "membership_error_flags": sum(r["membership_flags"] for r in errors),
        "permission_error_flags": sum(r["permission_flags"] for r in errors),
        "membership_false_flags": sum(r["membership_flags"] for r in correct),
        "permission_false_flags": sum(r["permission_flags"] for r in correct),
        "additional_error_flags": len(extra),
        "additional_error_executions": len({(r["run"], r["task"]) for r in extra}),
        "additional_error_tasks": len({r["task"] for r in extra}),
        "additional_error_families": len({r["task"].split("_")[0] for r in extra}),
        "additional_error_configurations": len(
            {re.sub(r"_test_(?:normal|challenge)$", "", r["run"]) for r in extra}
        ),
        "additional_nonowner_errors": sum(r["wrong_nonowner"] for r in extra),
    }


def verify_resolver(manifest):
    """Preserve the original selection rule while allowing readable formatting."""
    original = HERE / "provenance/original-resolver.py"
    current = HERE / "resolver.py"
    assert (
        hashlib.sha256(original.read_bytes()).hexdigest() == manifest["resolver_sha256"]
    )
    # The manifest pins the original experiment. Formatting must leave its
    # executable syntax unchanged, rather than silently redefining the cohort.
    assert ast.dump(ast.parse(current.read_text())) == ast.dump(
        ast.parse(original.read_text())
    )


def initial_contacts(data, task_dir, spec):
    """Read the user's initial contact book, before the agent changes any state."""
    phone = database(data, "phone", task_dir / "dbs/phone.jsonl")
    contacts = [
        dict(row)
        for row in phone.execute(
            "SELECT c.first_name,c.last_name,c.email,c.relationships FROM contacts c "
            "JOIN users u ON u.id=c.user_id WHERE u.phone_number=?",
            (spec["supervisor"]["phone_number"],),
        )
    ]
    phone.close()
    return contacts


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", required=True, type=Path)
    p.add_argument("--runs", required=True, type=Path)
    p.add_argument("--output", type=Path, default=HERE / "results")
    p.add_argument("--pilot-records", type=Path)
    p.add_argument("--selection-only", action="store_true")
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = read(HERE / "source-manifest.json")
    tasks = manifest["task_ids"]
    verify_resolver(manifest)
    # Select from instructions and contact books before reading scoring records.
    supported = []
    for task in manifest["screened_task_ids"]:
        task_dir = args.data / "tasks" / task
        spec = read(task_dir / "specs.json")
        contacts = initial_contacts(args.data, task_dir, spec)
        if resolve(spec["instruction"], contacts)["status"] == "resolved":
            supported.append(task)
    assert sorted(supported) == tasks, (supported, tasks)
    selection = {
        "screened": len(manifest["screened_task_ids"]),
        "supported": len(supported),
        "cohort_agrees": True,
        "resolver_sha256": manifest["resolver_sha256"],
    }
    (args.output / "selection-check.json").write_text(
        json.dumps(selection, indent=2) + "\n"
    )
    if args.selection_only:
        print(json.dumps(selection))
        return
    rows = []
    episodes = []
    task_records = []
    prefix_checks = 0
    hashes = {}
    for task in tasks:
        task_dir = args.data / "tasks" / task
        spec = read(task_dir / "specs.json")
        assert spec["db_version"] == "0.1.0"
        contacts = initial_contacts(args.data, task_dir, spec)
        decision = resolve(spec["instruction"], contacts)
        assert decision["status"] == "resolved", (task, decision)
        allowed = set(decision["allowed"])
        members = {c["email"].casefold() for c in contacts}
        initial = database(args.data, "gmail", task_dir / "dbs/gmail.jsonl")
        initial_users = {
            r["id"]: dict(r) for r in initial.execute("SELECT * FROM users")
        }
        initial_email_ids = {r["id"] for r in initial.execute("SELECT id FROM emails")}
        owner = next(
            i
            for i, u in initial_users.items()
            if u["email"].casefold() == spec["supervisor"]["email"].casefold()
        )
        # Only scoring reads the benchmark answer key.
        expected = scoring_set(args.data, task, spec, initial_users)
        task_records.append(
            {
                "task": task,
                "role": decision["role"],
                "permission_size": len(allowed),
                "agrees_with_reference": allowed == expected,
            }
        )
        initial.close()
        for source in manifest["baseline_bundles"]:
            run = source["name"]
            run_dir = args.runs / run / "tasks" / task
            if not run_dir.exists():
                continue  # Task belongs to the other benchmark split.
            assert (run_dir / "version/data.txt").read_text().strip() == "0.1.0"
            for f in (task_dir / "dbs").glob("*.jsonl"):
                init = f.read_text().splitlines()
                final = (run_dir / "dbs" / f.name).read_text().splitlines()
                assert [json.loads(s) for s in final[: len(init)]] == [
                    json.loads(s) for s in init
                ]
                prefix_checks += 1
            mailbox_record_path = run_dir / "dbs/gmail.jsonl"
            hashes[run + "/tasks/" + task + "/dbs/gmail.jsonl"] = hashlib.sha256(
                mailbox_record_path.read_bytes()
            ).hexdigest()
            # Rebuild directly from the full final SQL sequence, independently
            # of the earlier pilot's initial-copy-plus-suffix reconstruction.
            db = database(args.data, "gmail", mailbox_record_path)
            final_users = {r["id"]: dict(r) for r in db.execute("SELECT * FROM users")}
            messages = [
                dict(r)
                for r in db.execute("SELECT * FROM emails")
                if r["id"] not in initial_email_ids and r["sender_id"] == owner
            ]
            failed_test_count = int(
                re.search(
                    r"Num Failed Tests\s*:\s*(\d+)",
                    (run_dir / "evaluation/report.md").read_text(),
                ).group(1)
            )
            episodes.append(
                {
                    "run": run,
                    "task": task,
                    "messages": len(messages),
                    "native_failed_tests": failed_test_count,
                }
            )
            for message in sorted(messages, key=lambda x: x["id"]):
                actual = {
                    final_users[i]["email"].casefold()
                    for i in json.loads(message["recipient_ids"])
                }
                wrong = actual - expected
                recipient_receipt = receipt(allowed, actual)
                permission_flag = not actual <= allowed
                assert permission_flag == (
                    not set(recipient_receipt["committed_destinations"])
                    <= set(recipient_receipt["permitted_slots"])
                )
                rows.append(
                    {
                        "run": run,
                        "task": task,
                        "message_id": message["id"],
                        "role": decision["role"],
                        "actual": sorted(actual),
                        "reference": sorted(expected),
                        "permission": sorted(allowed),
                        "is_error": bool(wrong),
                        "wrong_nonowner": bool(
                            wrong - {spec["supervisor"]["email"].casefold()}
                        ),
                        "membership_flags": not actual <= members,
                        "permission_flags": permission_flag,
                        "receipt": recipient_receipt,
                        "native_failed_tests": failed_test_count,
                    }
                )
            db.close()
    assert len(episodes) == len(tasks) * 14
    pilot_agreement = None
    if args.pilot_records:
        pilot = {
            (r["run"], r["task"], r["email_id"]): r for r in read(args.pilot_records)
        }
        assert len(rows) == len(pilot)
        for r in rows:
            pilot_row = pilot[(r["run"], r["task"], r["message_id"])]
            assert (
                r["actual"],
                r["is_error"],
                r["membership_flags"],
                r["permission_flags"],
            ) == (
                pilot_row["actual"],
                pilot_row["is_error"],
                pilot_row["membership_rejects"],
                pilot_row["relationship_rejects"],
            )
        pilot_agreement = len(rows)
    first_errors = {}
    for r in rows:
        if r["is_error"]:
            first_errors.setdefault((r["run"], r["task"]), r)
    summary = {
        **aggregates(rows),
        "data_version": "0.1.0",
        "task_count": len(tasks),
        "task_families": len({t.split("_")[0] for t in tasks}),
        "configurations": 14,
        "executions": len(episodes),
        "executions_with_sends": sum(e["messages"] > 0 for e in episodes),
        "executions_without_sends": sum(e["messages"] == 0 for e in episodes),
        "reference_set_agreements": sum(
            t["agrees_with_reference"] for t in task_records
        ),
        "reference_set_disagreements": [
            t for t in task_records if not t["agrees_with_reference"]
        ],
        "native_pass_recipient_contradictions": sum(
            r["is_error"] and r["native_failed_tests"] == 0 for r in rows
        ),
        "initial_database_prefix_checks": prefix_checks,
        "first_error_per_execution": aggregates(list(first_errors.values())),
        "excluding_parent_photo_family": aggregates(
            [r for r in rows if not r["task"].startswith("f6f2e67_")]
        ),
        "by_role": {
            role: aggregates([r for r in rows if r["role"] == role])
            for role in sorted({r["role"] for r in rows})
        },
        "api_cost_usd": 0,
        "local_model_inference": False,
    }
    assert not summary["reference_set_disagreements"]
    assert not summary["native_pass_recipient_contradictions"]
    if args.pilot_records:
        (args.output / "pilot-agreement.json").write_text(
            json.dumps({"rows_reproduced": pilot_agreement}) + "\n"
        )
    for name, value in [
        ("summary.json", summary),
        ("records-private.json", rows),
        ("executions-private.json", episodes),
        ("task-checks-private.json", task_records),
        ("input-hashes-private.json", hashes),
    ]:
        (args.output / name).write_text(json.dumps(value, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: v
                for k, v in summary.items()
                if k
                not in [
                    "by_role",
                    "first_error_per_execution",
                    "excluding_parent_photo_family",
                ]
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
