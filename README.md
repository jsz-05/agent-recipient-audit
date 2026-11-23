# Did my agent email the right person?

Replication code for **Did My Agent Email the Right Person? Auditing Recipients
in Redacted Agent Logs**, by Jeffrey Zhou and K. Mani Chandy.

The study compares what a recipient checker can establish from actual agent
mistakes and from the same wrong addresses inserted into successful histories.
It also tests permission receipts, saved-contact mistakes in AppWorld, and
SMTP acceptance. The saved inputs and results reproduce the paper without
model access or API fees.

## Quick check

Use Python 3.12. These checks need only the standard library:

```sh
python3 check_results.py
python3 history-robustness/analyze.py --data . --output outputs/history-robustness
python3 appworld/check_saved.py
```

The checks cover 1,409 WorkBench sends, the 94/163 export collisions, and
4/158 original-error flags versus 2,845/2,865 placements in successful
histories. AppWorld adds 352 recorded sends, including 23 wrong-recipient
sends that contact membership misses.

## Rerun the WorkBench study

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python reproduce.py
```

This replays the tools and both tracing libraries, then compares the generated
records and summaries **byte for byte** with `saved/`, `saved-comparison/`, and
`saved-receipts/`. It writes to `analysis/`, `comparison/`, and `receipts/`.
No email leaves the simulator and no model is called.

`reproduce.py` is the starting point for reading the code. `comparison/` holds
the matched-history experiment, `receipts/receipt.py` defines the permission
receipt, and `appworld/resolver.py` resolves the relationships named in requests.
The small comments in each runner explain the order of operations.

## Other experiments

- [AppWorld](appworld/README.md): reconstruct the contact study from the official
  archived runs. Its protected inputs must be downloaded separately and kept
  local. `appworld/check_saved.py` checks the published aggregate results.
- [SMTP](smtp-study/README.md): rerun the 550 transactions against a local
  loopback mail server, including uncertain outcomes after a lost acknowledgment.
- [Saved live pilot](live-study/README.md): inspect or replay the existing model
  responses. Its optional `--run` mode makes paid requests; it is not needed to
  reproduce the paper.

## Figures

With the dependencies installed and Poppler's `pdftoppm` on your command path:

```sh
python figure_data.py
python draw_figures.py
python draw_study_figures.py
```

The four vector PDFs and their plotting data appear in `figures/`. They show
the Kofi example, the two replay comparisons, the history contrast by request
type, and the AppWorld results by requested relationship. Manuscript files and
generated figures are not included in this repository.

## Inputs and checks

`archive-manifest.json` identifies the 24 WorkBench archives.
`input-sha256.json` pins the benchmark inputs. The upstream code and its MIT
license are preserved in `external/WorkBench/`; our readability changes do not
touch those files. The original AppWorld resolver is kept in
`appworld/provenance/` so its recorded checksum can still be verified. The
readable version must have an identical syntax tree before the study will run.

`REFACTOR_CHECKS.txt` records the comparisons made before this release.
The experimental protocols and selection limits remain in `protocols/` and
the individual study folders. Repeated tasks and sends are retained in the
reported counts; they are not independent deployment samples.

## Repository history

This history was reconstructed from the verified release on November 22, 2026.
Commit timestamps were assigned for organization; they are not records of when
the code was written or experiments were run. Original experiment dates remain
in the protocols and saved records.
