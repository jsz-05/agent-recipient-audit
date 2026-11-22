"""Draw the study design and history comparison from the archived figure data.

Run from any directory with Python and ReportLab installed::

    python draw_study_figures.py

The PDFs contain vector text and shapes with the manuscript's embedded Times
fonts. PNG previews are rendered from those PDFs with Poppler. No experiment,
model request, or independent observation is added by this script.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
from math import atan2, cos, sin
from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from figure_fonts import register_fonts


HERE = Path(__file__).resolve().parent
ROMAN = "Times-Roman"
INK, RULE = map(HexColor, ("#222628", "#879198"))
NAVY, TEAL, OCHRE = map(HexColor, ("#315e83", "#266e66", "#985c1f"))
PALE, GRID = map(HexColor, ("#f5f7f9", "#e2e5e7"))

# The first four templates name one person; the last names two. This is the
# permitted-recipient structure described in the manuscript, not a new label.
REQUESTS = (
    ("Send a new message", ("New email",), 1),
    ("Latest from one sender", ("Latest from", "a sender"), 1),
    ("Latest on a subject", ("Latest on", "a subject"), 1),
    ("Last week, sender and subject", ("Last week,", "sender and subject"), 1),
    ("Latest on a subject, two people", ("Latest subject,", "two people"), 2),
)


def new_figure(path, width, height, title):
    canvas = Canvas(
        str(path),
        pagesize=(width, height),
        initialFontName=ROMAN,
        invariant=1,
        pageCompression=1,
    )
    canvas.setTitle(title)
    canvas.setAuthor("")
    canvas.setSubject("Recorded WorkBench errors and offline replay comparisons")
    return canvas


def text(c, x, y, value, size=10, color=INK, align="left", limit=None, font=ROMAN):
    width = stringWidth(value, font, size)
    left = x if align == "left" else x - width if align == "right" else x - width / 2
    page_width, page_height = c._pagesize
    page_left, page_bottom = c.absolutePosition(left, y)
    page_right, page_top = c.absolutePosition(left + width, y + size)
    if (
        page_left < 1
        or page_right > page_width - 1
        or page_bottom < 2
        or page_top > page_height - 1
    ):
        raise ValueError(f"Text outside figure: {value!r}")
    if limit is not None and width > limit:
        raise ValueError(f"Text exceeds its panel: {value!r}")
    c.setFont(font, size)
    c.setFillColor(color)
    {"left": c.drawString, "right": c.drawRightString, "center": c.drawCentredString}[
        align
    ](x, y, value)


def line(c, points, color=RULE, width=0.65, dashed=False):
    c.saveState()
    c.setStrokeColor(color)
    c.setLineWidth(width)
    if dashed:
        c.setDash(2.4, 2)
    path = c.beginPath()
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    c.drawPath(path)
    c.restoreState()


def arrow(c, points, color=RULE, dashed=False):
    line(c, points, color, dashed=dashed)
    (x1, y1), (x2, y2) = points[-2:]
    angle = atan2(y2 - y1, x2 - x1)
    ends = [
        (x2 - 3.8 * cos(angle + a), y2 - 3.8 * sin(angle + a)) for a in (-0.42, 0.42)
    ]
    line(c, [ends[0], (x2, y2), ends[1]], color)


def box(c, x, y, width, height, color=RULE, fill=None):
    c.saveState()
    c.setStrokeColor(color)
    c.setLineWidth(0.6)
    if fill is not None:
        c.setFillColor(fill)
    c.rect(x, y, width, height, stroke=1, fill=fill is not None)
    c.restoreState()


def load_values(data_path):
    data = json.loads(data_path.read_text())
    correction_rows = {
        row["label"]: row for row in data["corrections"]["request_types"]
    }
    history_rows = {row["label"]: row for row in data["request_types"]}
    if set(correction_rows) != {name for name, _, _ in REQUESTS}:
        raise ValueError(
            "Correction request types differ from the five documented templates"
        )
    if set(history_rows) != {name for name, _, _ in REQUESTS} | {"All matched errors"}:
        raise ValueError(
            "History request types differ from the five documented templates"
        )

    errors = sum(row["errors"] for row in correction_rows.values())
    corrections = sum(
        correction_rows[name]["errors"] * people for name, _, people in REQUESTS
    )
    if errors != data["corrections"]["coverage"]["errors"]:
        raise ValueError("Correction rows do not sum to the reported error cohort")

    rows = []
    for name, display, _ in REQUESTS:
        row = history_rows[name]
        rows.append(
            {
                "request": name,
                "display_label": list(display),
                "original_errors": row["observed"],
                "original_errors_flagged": row["observed_flagged"],
                "replay_placements": row["replays"],
                "replay_placements_flagged": row["replays_flagged"],
                "original_percent": 100 * row["observed_flagged"] / row["observed"],
                "replay_percent": 100 * row["replays_flagged"] / row["replays"],
            }
        )

    total = history_rows["All matched errors"]
    for field in ("observed", "observed_flagged", "replays", "replays_flagged"):
        if sum(history_rows[name][field] for name, _, _ in REQUESTS) != total[field]:
            raise ValueError(f"History rows do not sum to the aggregate {field}")
    counts = {
        "recorded_errors": errors,
        "permitted_corrections": corrections,
        "matched_errors": total["observed"],
        "replay_placements": total["replays"],
        "original_errors_flagged": total["observed_flagged"],
        "replay_placements_flagged": total["replays_flagged"],
    }
    # Catch an unintended cohort change before overwriting publication figures.
    if (errors, corrections, total["observed"], total["replays"]) != (
        163,
        223,
        158,
        2865,
    ):
        raise ValueError(
            "The cohort changed; review the figure and manuscript together"
        )
    return counts, rows


def draw_overview(path, counts, sample):
    """Show recorded inputs and the change in each replay, at readable print size."""
    from draw_figures import icon

    c = new_figure(path, 500, 202, "Constructing the two WorkBench replay comparisons")
    blue, green, pink, gray = map(
        HexColor, ("#e7eff8", "#e6f1ec", "#f4ded8", "#edf0f3")
    )
    code, bold = "Courier", "Times-Bold"
    original, corrected = sample["observed"], sample["corrected"]
    assert original["task"] == 48 and original["source"] == "00000477"
    assert not original["lookups"] and sample["successful"]["lookups"] == ["EMAIL_2"]

    def t(x, y, v, size=9, font=ROMAN, color=INK, limit=None):
        text(c, x, y, v, size, color, limit=limit, font=font)

    def panel(x, y, w, h, title, fill, symbol=None):
        c.setFillColor(fill)
        c.setStrokeColor(INK)
        c.setLineWidth(0.7)
        c.roundRect(x, y, w, h, 2.5, stroke=1, fill=1)
        if symbol:
            icon(c, x + 7, y + h - 14, symbol)
        offset = 22 if symbol else 8
        t(x + offset, y + h - 14, title, 9.6, bold, limit=w - offset - 7)
        line(c, [(x, y + h - 20), (x + w, y + h - 20)], INK, 0.6)

    panel(4, 6, 164, 190, "Recorded workflow: task 48", gray, "log")
    t(12, 165, "Request (abridged)", 9, bold)
    t(12, 153, "Forward Akira's gala emails", 9)
    t(12, 142, "from last week to Kofi.", 9)
    line(c, [(12, 135), (160, 135)], RULE, 0.5)
    icon(c, 12, 122, "email")
    t(27, 123, "Source email", 9, bold)
    t(12, 112, "id: 00000477", 8.5, code)
    t(12, 101, "from: akira.sato@atlas.com", 8.5, code)
    line(c, [(12, 94), (160, 94)], RULE, 0.5)
    t(12, 82, "Recorded tool call", 9, bold)
    t(12, 71, "email.forward_email(", 8.5, code)
    t(12, 60, "  email_id='00000477',", 8.5, code)
    t(12, 49, "  recipient=", 8.5, code)
    c.setFillColor(pink)
    c.rect(10, 35, 150, 12, stroke=0, fill=1)
    t(12, 38, " '" + original["destination"] + "')", 8.5, code)
    t(12, 24, "Directory lookups: none", 8.5, bold)
    t(12, 13, "Tool reports a successful forward.", 8.5)
    arrow(c, [(168, 101), (178, 101), (178, 150), (188, 150)], INK)
    arrow(c, [(178, 101), (178, 53), (188, 53)], INK)
    panel(188, 108, 180, 88, "(a) Correct the recipient", blue)
    t(196, 164, "Same history and other arguments", 8.7)
    c.setFillColor(pink)
    c.rect(195, 140, 165, 14, stroke=0, fill=1)
    t(200, 144, "- " + original["destination"], 8.7, code)
    c.setFillColor(green)
    c.rect(195, 122, 165, 14, stroke=0, fill=1)
    t(200, 126, "+ " + corrected["destination"], 8.7, code)
    t(
        196,
        112,
        f'{counts["permitted_corrections"]} corrections at {counts["recorded_errors"]} error positions',
        8.5,
    )
    panel(188, 6, 180, 94, "(b) Start from a successful run", green)
    t(196, 69, "Same request, tool and source email", 8.7)
    t(196, 57, "Insert the original wrong address:", 8.7)
    t(196, 45, original["destination"], 8.7, code, color=OCHRE)
    c.setFillColor(white)
    c.setStrokeColor(INK)
    c.roundRect(195, 11, 166, 27, 2.5, stroke=1, fill=1)
    t(201, 28, "Kofi lookup already in this history:", 8.5)
    t(201, 16, corrected["destination"], 8.7, code)
    arrow(c, [(368, 150), (386, 150)], INK)
    arrow(c, [(368, 53), (386, 53)], INK)
    panel(386, 108, 110, 88, "Replay and export", blue)
    t(394, 164, "LangSmith + Grafana", 8.7)
    t(394, 152, "Masked or aliased", 8.7)
    t(394, 137, "Compare full logs", 9, bold)
    t(394, 124, "Original / corrected", 8.7)
    t(394, 113, "Identical or different", 8.5)
    panel(386, 6, 110, 94, "Replay and check", green)
    t(394, 69, "Same lookup checker", 8.7)
    t(394, 55, "Kofi example", 9, bold)
    t(394, 40, "Recorded error: abstain", 8.5, limit=94)
    t(394, 28, "Inserted error: flag", 8.5, limit=94)
    t(394, 14, "Flag / verify / abstain", 8.5)
    c.save()


def draw_appworld_plot(path, appworld):
    """Disaggregate the same observed sends by the relationship in the request."""
    c = new_figure(
        path, 243, 238, "AppWorld recipient checks by requested relationship"
    )
    roles = sorted(appworld["by_role"], key=lambda k: -appworld["by_role"][k]["errors"])
    assert sum(appworld["by_role"][r]["errors"] for r in roles) == 150
    assert sum(appworld["by_role"][r]["membership_error_flags"] for r in roles) == 127
    assert sum(appworld["by_role"][r]["permission_error_flags"] for r in roles) == 150
    for y, color, label in [
        (225, NAVY, "Contact membership"),
        (212, TEAL, "Requested relationship"),
    ]:
        c.setFillColor(color)
        c.rect(10, y - 1, 9, 6, stroke=0, fill=1)
        text(c, 24, y, label, 9.3)
    x0, x1 = 67, 223
    for v in (0, 20, 40, 60):
        x = x0 + (x1 - x0) * v / 60
        line(c, [(x, 38), (x, 199)], GRID, 0.5)
        text(c, x, 26, str(v), 9, align="center")
    line(c, [(x0, 38), (x1, 38)], RULE, 0.65)
    for i, r in enumerate(roles):
        row = appworld["by_role"][r]
        y = 190 - i * 19.5
        text(c, 59, y - 2, r.capitalize(), 9, align="right")
        for ybar, key, color in [
            (y + 1, "membership_error_flags", NAVY),
            (y - 6, "permission_error_flags", TEAL),
        ]:
            value = row[key]
            width = (x1 - x0) * value / 60
            c.setFillColor(color)
            c.rect(x0, ybar, width, 5.5, stroke=0, fill=1)
            text(c, x0 + width + 3, ybar - 1, str(value), 8.5, color)
    text(c, 145, 10, "Wrong sends flagged (count)", 9.5, align="center")
    c.save()


def percent_label(value):
    if value in (0, 100):
        return f"{value:.0f}%"
    return f"{value:.1f}%"


def draw_history_plot(path, rows):
    # At 243 pt this is one AAAI column; no downscaling is needed.
    c = new_figure(
        path, 243, 232, "Wrong sends flagged in original and successful histories"
    )
    for y, color, label in (
        (219, NAVY, "Original error histories"),
        (204, TEAL, "Successful histories, error inserted"),
    ):
        c.setFillColor(color)
        c.rect(6, y - 1, 10, 7, fill=1, stroke=0)
        text(c, 22, y - 1, label, 10.2, limit=215)

    x0, x1, axis_y, grid_top = 94, 233, 34, 190
    span = x1 - x0
    for tick in (0, 25, 50, 75, 100):
        x = x0 + span * tick / 100
        line(c, [(x, axis_y), (x, grid_top)], GRID, 0.5)
        line(c, [(x, axis_y), (x, axis_y - 3)], RULE, 0.5)
        text(c, x, axis_y - 12, str(tick), 9, align="center")
    line(c, [(x0, axis_y), (x1, axis_y)], RULE, 0.6)
    text(c, (x0 + x1) / 2, 7, "Errors flagged (%)", 10, align="center")

    for index, row in enumerate(rows):
        center = 174 - 31 * index
        labels = row["display_label"]
        if len(labels) == 1:
            text(c, 86, center - 3.2, labels[0], 9.5, align="right", limit=80)
        else:
            text(c, 86, center + 2.3, labels[0], 9.5, align="right", limit=80)
            text(c, 86, center - 8.7, labels[1], 9.5, align="right", limit=80)

        for value, y, color in (
            (row["original_percent"], center + 5.5, NAVY),
            (row["replay_percent"], center - 5.5, TEAL),
        ):
            width = span * value / 100
            if width:
                c.setFillColor(color)
                c.rect(x0, y - 4.25, width, 8.5, fill=1, stroke=0)
            label = percent_label(value)
            if value >= 80:
                text(c, x0 + width - 4, y - 3.1, label, 9.2, white, "right")
            else:
                text(c, x0 + width + 3, y - 3.1, label, 9.2, color)
    c.save()


def render_preview(pdf, dpi):
    renderer = shutil.which("pdftoppm")
    if not renderer:
        raise RuntimeError(
            "Poppler pdftoppm is required to render and inspect the PDFs"
        )
    subprocess.run(
        [
            renderer,
            "-singlefile",
            "-r",
            str(dpi),
            "-png",
            str(pdf),
            str(pdf.with_suffix("")),
        ],
        check=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=HERE / "figures/figure-data.json")
    parser.add_argument("--output", type=Path, default=HERE / "figures")
    parser.add_argument(
        "--appworld", type=Path, default=HERE / "appworld/saved-summary.json"
    )
    parser.add_argument("--dpi", type=int, default=144, help="PDF preview resolution")
    args = parser.parse_args()
    counts, rows = load_values(args.data)
    appworld = json.loads(args.appworld.read_text())
    if (
        appworld["task_count"],
        appworld["messages"],
        appworld["additional_error_flags"],
    ) != (30, 352, 23):
        raise ValueError(
            "AppWorld cohort changed; review the figure and manuscript together"
        )
    register_fonts()
    args.output.mkdir(parents=True, exist_ok=True)

    overview = args.output / "study-overview.pdf"
    history = args.output / "study-history-contrast.pdf"
    sample = json.loads(args.data.read_text())["kofi"]
    appworld_plot = args.output / "appworld-recipient-checks.pdf"
    draw_overview(overview, counts, sample)
    draw_history_plot(history, rows)
    draw_appworld_plot(appworld_plot, appworld)
    for pdf in (overview, history, appworld_plot):
        render_preview(pdf, args.dpi)

    metadata = {
        "source": args.data.name,
        "source_sha256": hashlib.sha256(args.data.read_bytes()).hexdigest(),
        "counts": counts,
        "appworld_source_sha256": hashlib.sha256(
            args.appworld.read_bytes()
        ).hexdigest(),
        "appworld_counts": {
            k: appworld[k]
            for k in ("task_count", "messages", "errors", "additional_error_flags")
        },
        "correction_count_derivation": "One permitted address for the first four request types; two for the fifth.",
        "history_percent_definition": "100 * flagged / tested within each request type and history condition",
        "history_rows": rows,
        "sampling_note": "The 2865 placements reuse 158 of the 163 recorded errors; they are not new independent errors.",
        "figure_dimensions_pt": {
            overview.name: [500, 202],
            history.name: [243, 232],
            appworld_plot.name: [243, 238],
        },
        "smallest_type_pt": 8.5,
        "preview_dpi": args.dpi,
        "recommended_widths": {
            overview.name: "textwidth",
            history.name: "columnwidth",
            appworld_plot.name: "columnwidth",
        },
        "appworld_by_relationship": appworld["by_role"],
        "appworld_plot": {
            "membership_errors_flagged": appworld["membership_error_flags"],
            "relationship_errors_flagged": appworld["permission_error_flags"],
            "wrong_sends": appworld["errors"],
            "membership_false_flags": appworld["membership_false_flags"],
            "relationship_false_flags": appworld["permission_false_flags"],
            "recipient_correct_sends": appworld["recipient_correct"],
        },
    }
    (args.output / "study-figure-data.json").write_text(
        json.dumps(metadata, indent=2) + "\n"
    )
    print(
        f"Created {overview.name}, {history.name} and {appworld_plot.name}; rendered previews and saved their data."
    )


if __name__ == "__main__":
    main()
