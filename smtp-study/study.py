"""Exercise recipient receipts over loopback SMTP. No model or external mail service.

Install aiosmtpd==1.4.6; --prepare freezes fixtures and --run uses only 127.0.0.1.
"""

from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import default
from email.utils import getaddresses
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import smtplib
import socket
import sys

HERE = Path(__file__).resolve().parent
PORTABLE = (HERE.parent / "analysis").is_dir()
ROOT = HERE.parent if PORTABLE else HERE.parents[1]
ANALYSIS = (
    ROOT / "analysis" if PORTABLE else ROOT / "revisions/permission-evidence-pilot"
)
RECEIPTS = ROOT / "receipts" if PORTABLE else ROOT / "revisions/authorization-receipt"
sys.path[:0] = [str(ANALYSIS), str(RECEIPTS), str(ROOT)]
from audit_archive import WB, read_csv, reference
from workbench_replay import TOOLS, reset_state, get_state
from receipt import resolve_grant, review

CONDITIONS = [
    "permitted_to",
    "wrong_to",
    "wrong_cc",
    "wrong_bcc",
    "envelope_override",
    "reject_extra",
    "reject_permitted",
    "reject_all",
    "reject_data",
    "lost_reply",
]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def prepare():
    os.chdir(WB)
    reset_state()
    roster = sorted(get_state().directory_emails.email_address)
    tasks = read_csv(
        WB / "data/processed/tasks_and_outcomes/email_tasks_and_outcomes.csv"
    )
    fixtures = []
    for tid, task in enumerate(tasks):
        calls = reference(task)
        if not calls or not all(
            name in ["email.send_email", "email.forward_email"] for name, args in calls
        ):
            continue
        permitted = sorted({args["recipient"].lower() for name, args in calls})
        other = next(a for a in roster if a not in permitted)
        fixtures.append(
            dict(
                task=tid,
                instruction=task["task"],
                permitted_for_scoring=permitted,
                other=other,
            )
        )
    assert len(fixtures) == 50
    protocol = dict(
        created_utc=datetime.now(timezone.utc).isoformat(),
        fixtures=fixtures,
        conditions=CONDITIONS,
        question="Does the private recipient receipt describe the SMTP-accepted envelope, including hidden destinations and partial or uncertain submission?",
        alternative="A receipt built only from headers, requested destinations or successful RCPT replies can misdescribe the committed submission.",
        scope="Ten constructed protocol conditions repeated with 50 existing request grants; not 500 independent tasks, natural agent errors, production delivery or a novel SMTP vulnerability.",
        server="aiosmtpd==1.4.6",
        client="Python standard-library smtplib.SMTP.send_message",
        measurement="Receipt uses client-observed RCPT and final DATA replies. Separate server records are used only for scoring. Unknown final replies produce abstention.",
        canonicalization="Exact lower-case addresses in the pinned directory; no claim that SMTP local parts are generally case-insensitive.",
        local_models=False,
        api_cost_usd=0,
        external_network=False,
        baseline="All SMTP commands and replies plus the grant permit the same comparison; the receipt is a restricted representation of this evidence, not a stronger enforcement mechanism.",
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )
    file = HERE / "protocol.json"
    if file.exists():
        old = json.loads(file.read_text())
        assert old["fixtures"] == fixtures and old["conditions"] == CONDITIONS
        return old
    write(file, protocol)
    return protocol


class Sink:
    """The server applies transport outcomes, never authorization labels."""

    def __init__(self):
        self.condition = None
        self.reject = set()
        self.records = []

    async def handle_RCPT(self, server, session, envelope, address, options):
        if address in self.reject:
            return "550 Mailbox unavailable"
        envelope.rcpt_tos.append(address)
        return "250 Recipient accepted"

    async def handle_DATA(self, server, session, envelope):
        if self.condition == "reject_data":
            return "554 Message rejected"
        if self.condition == "lost_before_store":
            server.transport.close()
            return "250 Message accepted"
        self.records.append(
            dict(
                recipients=list(envelope.rcpt_tos),
                content=envelope.original_content.decode("utf-8"),
            )
        )
        if self.condition == "lost_reply":
            server.transport.close()
            return "250 Message accepted"
        return "250 Message accepted"


class ObservedSMTP(smtplib.SMTP):
    def __init__(self, *a, **k):
        self.rcpt_replies = []
        self.data_reply = None
        self.data_started = False
        self.data_bytes = None
        super().__init__(*a, **k)

    def rcpt(self, recipient, options=()):
        code, text = super().rcpt(recipient, options)
        self.rcpt_replies.append(dict(recipient=recipient, code=code))
        return code, text

    def data(self, msg):
        self.data_started = True
        self.data_bytes = msg.encode("ascii") if isinstance(msg, str) else msg
        try:
            code, text = super().data(msg)
        except smtplib.SMTPDataError as e:
            self.data_reply = e.smtp_code
            raise
        self.data_reply = code
        return code, text


def receipt(grant, client):
    """Use client replies to record accepted, rejected or uncertain submission."""
    if not client.data_started or (
        client.data_reply is not None and client.data_reply != 250
    ):
        status = "not_accepted"
    elif client.data_reply is None:
        status = "unknown"
    else:
        status = "accepted"
    addresses = (
        [r["recipient"] for r in client.rcpt_replies if r["code"] in [250, 251]]
        if status == "accepted"
        else []
    )
    aliases = {}

    def alias(value):
        if value is None:
            return None
        if value not in aliases:
            aliases[value] = len(aliases) + 1
        return aliases[value]

    return dict(
        status=status,
        permitted=[alias(v) for v in grant],
        accepted=[alias(v) for v in addresses],
    )


def decide(r):
    """Check every accepted recipient; uncertain submission must remain unresolved."""
    if r["status"] == "not_accepted":
        return "no_send"
    if r["status"] != "accepted" or not r["accepted"]:
        return "insufficient"
    decisions = [
        review(dict(permitted=r["permitted"], committed=v)) for v in r["accepted"]
    ]
    if "different" in decisions:
        return "different"
    if "insufficient" in decisions:
        return "insufficient"
    return "permitted"


def envelope_for_condition(condition, permitted, other):
    """Construct one transport fixture without using the receipt's judgment."""
    to_recipients = list(permitted)
    cc_recipients = []
    bcc_recipients = []
    envelope_override = None
    reject = set()
    if condition == "wrong_to":
        to_recipients = [other]
    elif condition == "wrong_cc":
        cc_recipients = [other]
    elif condition in [
        "wrong_bcc",
        "reject_extra",
        "reject_permitted",
        "reject_all",
        "reject_data",
        "lost_reply",
        "lost_before_store",
    ]:
        bcc_recipients = [other]
    elif condition == "envelope_override":
        envelope_override = [other]
    requested = (
        envelope_override
        if envelope_override is not None
        else to_recipients + cc_recipients + bcc_recipients
    )
    if condition == "reject_extra":
        reject = {other}
    elif condition == "reject_permitted":
        reject = set(permitted)
    elif condition == "reject_all":
        reject = set(requested)
    return (
        to_recipients,
        cc_recipients,
        bcc_recipients,
        envelope_override,
        requested,
        reject,
    )


def run(output, unknown_control=False):
    """Exercise each transport condition over loopback and keep server truth separate."""
    from aiosmtpd.controller import Controller

    protocol = prepare()
    conditions = ["lost_before_store"] if unknown_control else CONDITIONS
    assert importlib.metadata.version("aiosmtpd") == "1.4.6"
    # Choose a free loopback port; no external relay is configured.
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    sink = Sink()
    controller = Controller(sink, hostname="127.0.0.1", port=port)
    controller.start()
    rows = []
    try:
        for fixture in protocol["fixtures"]:
            reset_state()
            grant = resolve_grant(
                fixture["instruction"], TOOLS["company_directory.find_email_address"]
            )
            assert None not in grant
            for condition in conditions:
                permitted = fixture["permitted_for_scoring"]
                other = fixture["other"]
                (
                    to_recipients,
                    cc_recipients,
                    bcc_recipients,
                    envelope_override,
                    requested,
                    reject,
                ) = envelope_for_condition(condition, permitted, other)
                sink.condition = condition
                sink.reject = reject
                previous_record_count = len(sink.records)
                message = EmailMessage()
                message["From"] = "assistant@example.test"
                message["To"] = ", ".join(to_recipients)
                if cc_recipients:
                    message["Cc"] = ", ".join(cc_recipients)
                if bcc_recipients:
                    message["Bcc"] = ", ".join(bcc_recipients)
                message["Subject"] = "Local recipient receipt test"
                message["Message-ID"] = (
                    f'<task-{fixture["task"]}-{condition}@example.test>'
                )
                message.set_content(
                    "Synthetic integration fixture. Nothing leaves the loopback server."
                )
                client = ObservedSMTP(
                    "127.0.0.1", port, local_hostname="localhost", timeout=5
                )
                error = None
                refused = {}
                try:
                    refused = client.send_message(message, to_addrs=envelope_override)
                except (
                    smtplib.SMTPRecipientsRefused,
                    smtplib.SMTPDataError,
                    smtplib.SMTPServerDisconnected,
                ) as e:
                    error = type(e).__name__
                finally:
                    client.close()
                committed = sink.records[previous_record_count:]
                assert len(committed) <= 1
                actual = committed[0]["recipients"] if committed else []
                truth = (
                    "no_send"
                    if not committed
                    else "permitted" if set(actual) <= set(permitted) else "different"
                )
                recipient_receipt = receipt(grant, client)
                verdict = decide(recipient_receipt)
                # The private adapter never receives the server's observation.
                assert (
                    verdict == truth
                    if condition not in ["lost_reply", "lost_before_store"]
                    else verdict == "insufficient"
                )
                headers = None
                if client.data_bytes is not None:
                    parsed = BytesParser(policy=default).parsebytes(client.data_bytes)
                    assert "Bcc" not in parsed
                    headers = [
                        a
                        for name, a in getaddresses(
                            parsed.get_all("To", []) + parsed.get_all("Cc", [])
                        )
                    ]
                rows.append(
                    dict(
                        task=fixture["task"],
                        condition=condition,
                        grant_for_scoring=list(grant),
                        requested_envelope=requested,
                        visible_headers=headers,
                        rcpt_replies=client.rcpt_replies,
                        data_started=client.data_started,
                        data_reply=client.data_reply,
                        error=error,
                        refused={a: code for a, (code, text) in refused.items()},
                        server_records=committed,
                        server_truth=truth,
                        receipt=recipient_receipt,
                        verdict=verdict,
                    )
                )
    finally:
        controller.stop()
    output.mkdir(parents=True, exist_ok=True)
    (output / "transactions.jsonl").write_text(
        "".join(
            json.dumps(recipient_receipt, ensure_ascii=False) + "\n"
            for recipient_receipt in rows
        )
    )
    summary = summarize(rows)
    write(output / "summary.json", summary)
    print(json.dumps(summary))


def summarize(rows):
    by_condition = {}
    for condition in dict.fromkeys(r["condition"] for r in rows):
        group = [r for r in rows if r["condition"] == condition]
        by_condition[condition] = dict(
            transactions=len(group),
            server_truth=dict(Counter(r["server_truth"] for r in group)),
            receipt_decisions=dict(Counter(r["verdict"] for r in group)),
            headers_omit_wrong=sum(
                r["server_truth"] == "different"
                and r["visible_headers"] is not None
                and set(r["visible_headers"]) <= set(r["grant_for_scoring"])
                for r in group
            ),
            requested_includes_rejected_wrong=sum(
                any(
                    v["code"] == 550 and v["recipient"] not in r["grant_for_scoring"]
                    for v in r["rcpt_replies"]
                )
                for r in group
            ),
        )
    return dict(
        transactions=len(rows),
        tasks=len({r["task"] for r in rows}),
        conditions=by_condition,
        definitive=sum(r["verdict"] != "insufficient" for r in rows),
        incorrect_definitive=sum(
            r["verdict"] not in ["insufficient", r["server_truth"]] for r in rows
        ),
        api_cost_usd=0,
        python=sys.version.split()[0],
        aiosmtpd=importlib.metadata.version("aiosmtpd"),
    )


def verify(path):
    rows = [
        json.loads(s) for s in (path / "transactions.jsonl").read_text().splitlines()
    ]
    expected_count = (
        50 if {r["condition"] for r in rows} == {"lost_before_store"} else 500
    )
    assert (
        len(rows) == expected_count
        and len({(r["task"], r["condition"]) for r in rows}) == expected_count
    )
    for r in rows:
        assert decide(r["receipt"]) == r["verdict"]
        destinations = [a for s in r["server_records"] for a in s["recipients"]]
        expected = (
            "no_send"
            if not r["server_records"]
            else (
                "permitted"
                if set(destinations) <= set(r["grant_for_scoring"])
                else "different"
            )
        )
        assert expected == r["server_truth"]
        if r["data_reply"] == 250:
            assert destinations == [
                v["recipient"] for v in r["rcpt_replies"] if v["code"] in [250, 251]
            ]
        else:
            assert r["receipt"]["accepted"] == []
        if r["condition"] in ["lost_reply", "lost_before_store"]:
            assert r["verdict"] == "insufficient"
        else:
            assert r["verdict"] == r["server_truth"]
    assert summarize(rows) == json.loads((path / "summary.json").read_text())
    print(f"Verified {len(rows)} SMTP transactions against separate server records.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--prepare", action="store_true")
    p.add_argument("--run", action="store_true")
    p.add_argument("--verify", action="store_true")
    p.add_argument("--output", type=Path, default=HERE / "saved")
    p.add_argument("--unknown-control", action="store_true")
    a = p.parse_args()
    a.output = a.output.resolve()
    if a.prepare:
        print(
            json.dumps(
                dict(tasks=len(prepare()["fixtures"]), conditions=len(CONDITIONS))
            )
        )
    if a.run:
        run(a.output, a.unknown_control)
    if a.verify:
        verify(a.output)
