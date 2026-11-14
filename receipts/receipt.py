"""Issue and read a recipient receipt inside a trusted, single-action mail adapter."""

import json

from canonical_identity.checker import requested_names
from src.tools._utils import normalize_email


def resolve_grant(instruction, lookup):
    """Read the original request once, before the agent takes any actions."""
    names = requested_names(instruction)
    if not names:
        return (None,)
    resolved = []
    for name in sorted(names):
        response = lookup(name=name)
        try:
            addresses = json.loads(response)
        except (TypeError, ValueError):
            addresses = None
        if (
            isinstance(addresses, list)
            and len(addresses) == 1
            and isinstance(addresses[0], str)
            and addresses[0]
        ):
            resolved.append(normalize_email(addresses[0]))
        else:
            resolved.append(None)
    return tuple(resolved)


def committed_recipient(mailbox, previous_length):
    """WorkBench appends synchronously. Reject missing or ambiguous observations."""
    new = mailbox.iloc[previous_length:]
    if len(new) != 1 or new.iloc[0]["inbox/outbox"] != "outbox":
        return None
    value = new.iloc[0]["sender/recipient"]
    return value if isinstance(value, str) and value else None


def issue_receipt(grant, stored_recipient):
    """Aliases apply only to this receipt. Treat a stored destination as one value."""
    aliases = {}

    def alias(address):
        if address is None:
            return None
        if address not in aliases:
            aliases[address] = len(aliases) + 1
        return aliases[address]

    return {
        "permitted": [alias(value) for value in grant],
        "committed": alias(stored_recipient),
    }


def review(receipt):
    """The reviewer only gets the serialized receipt, never directory values."""
    destination = receipt["committed"]
    known = receipt["permitted"]
    if destination is None or not known:
        return "insufficient"
    if destination in known:
        return "permitted"
    return "insufficient" if None in known else "different"
