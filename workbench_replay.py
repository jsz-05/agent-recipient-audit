"""Replay public archived actions in WorkBench; safe AST decoding, never eval."""

import ast
import copy
import csv
import gzip
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from audit import (
    P,
    EMAIL,
    FIXED,
    canonical,
    normalized,
    ScopedSanitizer,
    CaptureExporter,
    FixedIds,
    Generation,
    Message,
    Part,
    ToolCall,
    ToolResult,
    MessageRole,
    PartKind,
    ModelRef,
    Client,
    ClientConfig,
    GenerationStart,
    ToolExecutionStart,
    ToolExecutionEnd,
    ContentCaptureMode,
    SecretRedactionOptions,
    create_secret_redaction_sanitizer,
    InMemorySpanExporter,
    TracerProvider,
    SimpleSpanProcessor,
)
from langsmith_native import CaptureSession, anonymizer
from langsmith import Client as LSClient

WB = P / "external/WorkBench"
sys.path.insert(0, str(WB))
from src.tools import email, company_directory
from src.tools.state import get_state, reset_state

TOOLS = {
    x.name: x.func
    for module in [email, company_directory]
    for x in vars(module).values()
    if hasattr(x, "func") and hasattr(x, "name")
}
BAD = "unapproved-recipient@outside.example"


def safe_decode(node):
    """Decode the archive's record constructors without executing archived code."""
    if isinstance(node, ast.Expression):
        return safe_decode(node.body)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [safe_decode(n) for n in node.elts]
    if isinstance(node, ast.Dict):
        return {safe_decode(k): safe_decode(v) for k, v in zip(node.keys, node.values)}
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in ["AgentResult", "TraceStep", "AgentAction"]
        and not node.args
    ):
        assert all(k.arg is not None for k in node.keywords)
        return {
            "record_type": node.func.id,
            **{k.arg: safe_decode(k.value) for k in node.keywords},
        }
    raise ValueError("Unsupported archived syntax: " + type(node).__name__)


def decode(s):
    return safe_decode(ast.parse(s, mode="eval"))


def load_tasks():
    """Load the fifty outbound-email tasks from the pinned reference archive."""
    tasks = list(
        csv.DictReader(
            (
                WB / "data/processed/tasks_and_outcomes/email_tasks_and_outcomes.csv"
            ).open()
        )
    )
    rows = list(
        csv.DictReader(
            gzip.open(
                WB / "data/results/email/gpt-4.1_all_2026-06-04_10-10-16.csv.gz", "rt"
            )
        )
    )
    assert len(tasks) == len(rows) == 90
    selected = []
    for task_id, (task, row) in enumerate(zip(tasks, rows)):
        assert row["task"] == task["task"], task_id
        if not any(step in task["outcome"] for step in ["send_email", "forward_email"]):
            continue
        expected = set(re.findall(r'recipient="([^"]+)"', task["outcome"]))
        assert expected
        parsed = decode(row["full_response"])
        steps = [
            step
            for step in parsed["trace"]
            if step["action"] not in [None, "Final Answer"]
        ]
        # AgentResult.intermediate_steps redundantly records actual calls.
        assert [(step["action"], step["action_input"]) for step in steps] == [
            (a, b) for a, b in parsed["intermediate_steps"]
        ]
        selected.append(
            {
                "id": task_id,
                "task": task["task"],
                "family": task["base_template"],
                "expected_recipients": sorted(expected),
                "steps": steps,
                "final_text": parsed["output"],
                "source_error": row["error"],
            }
        )
    assert len(selected) == 50
    return selected


def replay(task, mutated=False, replacement=BAD):
    """Execute the saved tool calls, optionally replacing the first outbound recipient."""
    reset_state()
    state = get_state()
    initial_email_ids = set(state.emails.email_id)
    steps = []
    changed = False
    for step_index, source in enumerate(task["steps"]):
        name = source["action"]
        arguments = copy.deepcopy(source["action_input"])
        assert name in TOOLS, name
        if (
            mutated
            and not changed
            and name in ["email.send_email", "email.forward_email"]
        ):
            arguments["recipient"] = replacement
            changed = True
        receipt = TOOLS[name](**arguments)
        # Keep all textual assistant content. The structured call is represented
        # once with its executed arguments, rather than duplicating old JSON.
        try:
            assistant_text = json.loads(source["llm_output"]).get("content", "") or ""
        except (ValueError, TypeError):
            assistant_text = source["llm_output"]
        steps.append(
            {
                "name": name,
                "arguments": arguments,
                "receipt": receipt,
                "assistant_text": assistant_text,
            }
        )
    outbox_rows = json.loads(
        json.dumps(
            state.emails[~state.emails.email_id.isin(initial_email_ids)].to_dict(
                "records"
            ),
            default=str,
        )
    )
    recipients = [r["sender/recipient"] for r in outbox_rows]
    recipient_authorized = bool(recipients) and set(recipients) <= set(
        task["expected_recipients"]
    )
    if mutated:
        assert changed and replacement in recipients
    return {
        "task_id": task["id"],
        "task": task["task"],
        "steps": steps,
        "final_text": task["final_text"],
        "outbox_delta": outbox_rows,
        "actual_recipients": recipients,
        "recipient_authorized": recipient_authorized,
        "mutated": mutated,
    }


def messages(run):
    """Represent each executed call and its response as tracing-library messages."""
    message_list = []
    for step_index, step in enumerate(run["steps"]):
        parts = []
        if step["assistant_text"]:
            parts.append(Part(kind=PartKind.TEXT, text=step["assistant_text"]))
        parts.append(
            Part(
                kind=PartKind.TOOL_CALL,
                tool_call=ToolCall(
                    name=step["name"],
                    id=f"call-{step_index}",
                    input_json=canonical(step["arguments"]).encode(),
                ),
            )
        )
        message_list.append(Message(role=MessageRole.ASSISTANT, parts=parts))
        message_list.append(
            Message(
                role=MessageRole.TOOL,
                parts=[
                    Part(
                        kind=PartKind.TOOL_RESULT,
                        tool_result=ToolResult(
                            name=step["name"],
                            tool_call_id=f"call-{step_index}",
                            content=step["receipt"],
                        ),
                    )
                ],
            )
        )
    message_list.append(
        Message(
            role=MessageRole.ASSISTANT,
            parts=[Part(kind=PartKind.TEXT, text=run["final_text"])],
        )
    )
    return message_list


def grafana(run, mode):
    """Capture the native Grafana export and a copy with timestamps normalized."""
    sanitizers = {
        "raw": lambda: None,
        "mask": lambda: create_secret_redaction_sanitizer(
            SecretRedactionOptions(redact_input_messages=True)
        ),
        "default_roles": lambda: create_secret_redaction_sanitizer(
            SecretRedactionOptions(redact_input_messages=False)
        ),
        "tokens": lambda: ScopedSanitizer(),
        "structural": lambda: ScopedSanitizer(structural=True),
    }
    exporter = CaptureExporter()
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider(id_generator=FixedIds())
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    client = Client(
        ClientConfig(
            generation_sanitizer=sanitizers[mode](),
            generation_exporter=exporter,
            tracer_provider=tracer_provider,
            content_capture=ContentCaptureMode.FULL,
            now=lambda: FIXED,
            use_experimental_otel=False,
        )
    )
    generation = Generation(
        id="generation-1",
        conversation_id="conversation-1",
        model=ModelRef(
            provider="archived", name=run.get("source_model", "WorkBench-gpt-4.1")
        ),
        system_prompt="Replay of archived WorkBench tool actions.",
        input=[
            Message(
                role=MessageRole.USER,
                parts=[Part(kind=PartKind.TEXT, text=run["task"])],
            )
        ],
        output=messages(run),
        metadata={},
        started_at=FIXED,
        completed_at=FIXED,
    )
    try:
        with client.start_generation(
            GenerationStart(
                id=generation.id,
                conversation_id=generation.conversation_id,
                model=generation.model,
                system_prompt=generation.system_prompt,
                started_at=FIXED,
            )
        ) as record:
            record.set_result(generation)
        for i, step in enumerate(run["steps"]):
            with client.start_tool_execution(
                ToolExecutionStart(
                    tool_name=step["name"],
                    tool_call_id=f"call-{i}",
                    conversation_id=generation.conversation_id,
                    include_content=True,
                    content_capture=ContentCaptureMode.FULL,
                    started_at=FIXED,
                )
            ) as record:
                record.set_result(
                    ToolExecutionEnd(
                        arguments=step["arguments"],
                        result=step["receipt"],
                        completed_at=FIXED,
                    )
                )
        client.flush()
        span_views = []
        for span in span_exporter.get_finished_spans():
            span_data = json.loads(span.to_json())
            instrumentation_scope = span.instrumentation_scope
            span_data["instrumentation_scope"] = {
                "name": instrumentation_scope.name,
                "version": instrumentation_scope.version,
                "schema_url": instrumentation_scope.schema_url,
                "attributes": dict(instrumentation_scope.attributes or {}),
            }
            span_data["dropped_counts"] = {
                key: getattr(span, key, None)
                for key in ["dropped_attributes", "dropped_events", "dropped_links"]
            }
            span_views.append(span_data)
        raw_export = normalized(
            {
                "generations": exporter.generations,
                "workflow_steps": exporter.steps,
                "spans": span_views,
            }
        )
        comparable_export = copy.deepcopy(raw_export)
        for span_view in comparable_export["spans"]:
            for field in ["start_time", "end_time"]:
                span_view[field] = "NORMALIZED_TIMESTAMP"
            for event in span_view["events"]:
                if "timestamp" in event:
                    event["timestamp"] = "NORMALIZED_TIMESTAMP"
        assert (
            len(exporter.generations) == 1 and len(span_views) == len(run["steps"]) + 1
        )
        return raw_export, comparable_export
    finally:
        client.shutdown()
        tracer_provider.shutdown()


def langsmith(run, mode):
    """Capture the native LangSmith export in memory without sending a request."""
    capture_session = CaptureSession()
    client = LSClient(
        api_url="http://capture.invalid",
        api_key="synthetic-not-a-key",
        session=capture_session,
        auto_batch_tracing=False,
        anonymizer=anonymizer(mode),
        omit_traced_runtime_info=True,
        tracing_sampling_rate=1.0,
    )
    try:
        client.create_run(
            name="workbench-replay",
            run_type="chain",
            project_name="offline-audit",
            id="00000000-0000-0000-0000-000000000001",
            start_time=FIXED,
            end_time=FIXED,
            inputs={"instruction": run["task"]},
            outputs={"steps": run["steps"], "final_text": run["final_text"]},
            extra={"metadata": {}},
        )
        assert len(capture_session.captured) == 1
        return capture_session.captured[0], capture_session.captured[0]
    finally:
        client.close()
