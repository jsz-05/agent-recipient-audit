"""Check aggregate accounting; full reconstruction needs the original bundles."""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
s = json.loads((HERE / "saved-summary.json").read_text())
assert s["messages"] == s["errors"] + s["recipient_correct"] == 352
assert (
    s["membership_error_flags"] + s["additional_error_flags"]
    == s["permission_error_flags"]
    == 150
)
assert s["additional_error_flags"] == 23 and s["additional_nonowner_errors"] == 23
assert s["membership_false_flags"] == s["permission_false_flags"] == 0
assert s["reference_set_agreements"] == s["task_count"] == 30
assert (
    s["executions_with_sends"] + s["executions_without_sends"] == s["executions"] == 420
)
assert s["first_error_per_execution"]["additional_error_flags"] == 12
assert s["excluding_parent_photo_family"]["additional_error_flags"] == 11
assert (
    not s["reference_set_disagreements"]
    and s["native_pass_recipient_contradictions"] == 0
)
print(
    "AppWorld aggregate checks passed. Use run_study.py to reconstruct original records."
)
