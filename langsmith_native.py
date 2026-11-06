"""Capture native LangSmith HTTP payloads in memory; never contact a server."""

import copy
import json
import re
from collections import defaultdict
import requests
from langsmith import Client
from langsmith.anonymizer import create_anonymizer
from audit import P, EMAIL, DOMAIN, FIXED, canonical, map_strings


class CaptureSession(requests.Session):
    def __init__(self):
        super().__init__()
        self.captured = []
        self.trust_env = False

    def send(self, request, **kwargs):
        assert request.url == "http://capture.invalid/runs", request.url
        self.captured.append(json.loads(request.body))
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        response.request = request
        return response


def anonymizer(mode):
    if mode == "raw":
        return None
    if mode == "mask":
        return create_anonymizer(
            [{"pattern": EMAIL.pattern, "replace": "<email-address>"}]
        )
    maps = defaultdict(dict)

    def tok(kind, value):
        if value not in maps[kind]:
            maps[kind][value] = kind + "_" + str(len(maps[kind]) + 1)
        return maps[kind][value]

    def leaf(s):
        if mode == "tokens":
            return EMAIL.sub(lambda m: tok("EMAIL", m.group()), s)
        s = DOMAIN.sub(lambda m: tok("DOMAIN", m.group().lower()) + ".invalid", s)
        return re.sub(
            r"([a-zA-Z0-9._%+-]+)@(DOMAIN_\d+\.invalid)",
            lambda m: tok("LOCAL", m.group(1)) + "@" + m.group(2),
            s,
        )

    return lambda obj: map_strings(obj, leaf)
