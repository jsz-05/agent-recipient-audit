# AppWorld recipient checks

This study checks recorded email recipients against the relationship requested
by the user. It compares personal-contact membership with a small resolver that
reads the request and initial contact book. The analysis uses thirty supported
tasks and the fourteen original AppWorld configurations with data version 0.1.0.
It does not run agents or make model requests.

## Check the reported numbers

From this directory, run `python3 check_saved.py`. This checks aggregate
accounting only. The original-record reconstruction is below.

## Reconstruct the study

Use Python 3.12 with SQLite load-extension controls and install `cryptography==50.0.2`.
Download and unpack the pinned public inputs, then run the analysis:

```sh
python3 prepare_sources.py
python3 run_study.py --data inputs/task-data/data --runs inputs/runs --output results
```

The source manifest pins every bundle by SHA-256. The downloader uses the public
AppWorld distribution constants and does not execute downloaded code. Original
data and archived outputs are downloaded from AppWorld and its leaderboard.
Keep the unpacked inputs and detailed outputs private: AppWorld requires
encrypted redistribution of its protected benchmark content. They are not
included in this package.

If you already have the version-matched unpacked inputs, pass their paths to
`run_study.py` instead. The program checks all 66 conservatively eligible task
instructions to recover the frozen thirty-task cohort. It then reconstructs
all 420 selected executions, including those that never sent a message. Results
go to `results/summary.json`; the detailed private records are written alongside
it. The aggregate counts should match `saved-summary.json`.

## What the comparison means

The resolver never receives the benchmark's scoring answers or the agent's
chosen destination. It supports explicit role-based requests, not general
permission extraction. Unsupported tasks do not count as successful checks.
The reported checks are offline judgments on committed messages. They did not
prevent the original sends or deploy a receipt service in AppWorld.

The protocol records the post-hoc design and repeated-send controls. The
reconstruction was tested on the archived inputs without GPU or model access.
