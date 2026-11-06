"""Privacy transformations and in-memory adapters for the tracing libraries."""

from pathlib import Path
from dataclasses import asdict, fields, is_dataclass
from datetime import datetime, timezone
import copy
import hashlib
import importlib.metadata
import json
import re
import statistics
import time
from collections import defaultdict
from agento11y import (
    Client,
    ClientConfig,
    Generation,
    GenerationStart,
    ModelRef,
    Message,
    Part,
    ToolCall,
    ToolResult,
    MessageRole,
    PartKind,
    ContentCaptureMode,
    SecretRedactionOptions,
    create_secret_redaction_sanitizer,
    ToolExecutionStart,
    ToolExecutionEnd,
)
from agento11y.exporters.noop import NoopGenerationExporter
from langsmith.anonymizer import create_anonymizer
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import RecognizerResult, OperatorConfig
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.id_generator import IdGenerator

P = Path(__file__).resolve().parent

EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

DOMAIN = re.compile(r"\b(?:[a-zA-Z0-9-]+\.)+(?:example|com|org|net)\b")

FIXED = datetime(2026, 11, 4, tzinfo=timezone.utc)


def serial(x):
    if isinstance(x, bytes):
        return x.decode()
    if isinstance(x, datetime):
        return x.isoformat()
    if hasattr(x, "value"):
        return x.value
    if hasattr(x, "__dataclass_fields__"):
        return asdict(x)
    raise TypeError(type(x))


def canonical(x):
    """Serialize deterministically so export comparisons ignore dictionary ordering."""
    return json.dumps(x, default=serial, sort_keys=True, separators=(",", ":"))


def normalized(x):
    return json.loads(canonical(x))


def map_strings(x, fn):
    """Apply a transformation to string values while preserving the record structure."""
    if isinstance(x, str):
        return fn(x)
    if isinstance(x, list):
        return [map_strings(y, fn) for y in x]
    if isinstance(x, dict):
        return {k: map_strings(v, fn) for k, v in x.items()}
    return x


def token_transform(value, scope="trace", structural=False):
    """Assign aliases in encounter order, with a fresh mapping for each call."""
    emails = {}
    locals_ = {}
    domains = {}

    def token(table, value, prefix):
        if value not in table:
            table[value] = f"{prefix}_{len(table)+1}"
        return table[value]

    def transform(s):
        if scope == "field":
            emails.clear()
            locals_.clear()
            domains.clear()
        if not structural:
            return EMAIL.sub(lambda m: token(emails, m.group(), "EMAIL"), s)
        # Domain matching precedes email matching so policy-only domains and
        # domains in addresses share the same per-trace namespace.
        s = DOMAIN.sub(
            lambda m: token(domains, m.group().lower(), "DOMAIN") + ".invalid", s
        )
        return re.sub(
            r"([a-zA-Z0-9._%+-]+)@(DOMAIN_\d+\.invalid)",
            lambda m: token(locals_, m.group(1), "LOCAL") + "@" + m.group(2),
            s,
        )

    return map_strings(value, transform)


class FixedIds(IdGenerator):
    def __init__(self):
        self.n = 1

    def generate_span_id(self):
        self.n += 1
        return self.n

    def generate_trace_id(self):
        return 100


class CaptureExporter(NoopGenerationExporter):
    def __init__(self):
        self.generations = []
        self.steps = []

    def export_generations(self, request):
        self.generations.extend(copy.deepcopy(request.generations))
        return super().export_generations(request)

    def export_workflow_steps(self, request):
        self.steps.extend(copy.deepcopy(request.workflow_steps))
        return super().export_workflow_steps(request)


class ScopedSanitizer:
    """Established consistent pseudonymization; mappings stay local to a session."""

    def __init__(self, structural=False):
        self.sessions = {}
        self.structural = structural

    def __call__(self, g):
        tables = self.sessions.setdefault(
            g.conversation_id, {"email": {}, "domain": {}, "local": {}}
        )

        def tok(kind, val):
            table = tables[kind]
            if val not in table:
                table[val] = kind.upper() + "_" + str(len(table) + 1)
            return table[val]

        def text(s):
            if not self.structural:
                return EMAIL.sub(lambda m: tok("email", m.group()), s)
            s = DOMAIN.sub(lambda m: tok("domain", m.group().lower()) + ".invalid", s)
            return re.sub(
                r"([a-zA-Z0-9._%+-]+)@(DOMAIN_\d+\.invalid)",
                lambda m: tok("local", m.group(1)) + "@" + m.group(2),
                s,
            )

        def walk(v):
            if isinstance(v, bytes):
                return text(v.decode()).encode()
            if isinstance(v, str):
                return text(v)
            if is_dataclass(v):
                for f in fields(v):
                    setattr(v, f.name, walk(getattr(v, f.name)))
                return v
            if isinstance(v, list):
                return [walk(x) for x in v]
            if isinstance(v, dict):
                return {k: walk(x) for k, x in v.items()}
            return v

        return walk(g)
