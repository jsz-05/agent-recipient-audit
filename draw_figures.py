"""Draw the worked comparison and request-type results from archived records."""

import argparse
import json
from math import atan2, cos, sin
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from figure_fonts import register_fonts


ROMAN, CODE = "Times-Roman", "Courier"
INK, RULE = map(HexColor, ("#222628", "#30373b"))
BLUE, TEAL, OCHRE = map(HexColor, ("#315e83", "#266e66", "#985c1f"))
PALE_BLUE, GRID = map(HexColor, ("#f5f7f9", "#e2e5e7"))


def figure(path, height, title):
    c = Canvas(
        str(path),
        pagesize=(500, height),
        initialFontName=ROMAN,
        invariant=1,
        pageCompression=1,
    )
    c.setTitle(title)
    c.setAuthor("")
    return c


def text(c, x, y, value, size=10, font=ROMAN, color=INK, align="left", limit=None):
    width = stringWidth(value, font, size)
    left = x if align == "left" else x - width if align == "right" else x - width / 2
    if left < 3 or left + width > 497 or y < 4 or y + size > c._pagesize[1] - 2:
        raise ValueError(f"Text outside figure: {value}")
    if limit is not None and width > limit:
        raise ValueError(f"Text exceeds its panel: {value}")
    c.setFillColor(color)
    c.setFont(font, size)
    {"left": c.drawString, "right": c.drawRightString, "center": c.drawCentredString}[
        align
    ](x, y, value)


def line(c, points, color=RULE, width=0.65):
    path = c.beginPath()
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    c.setStrokeColor(color)
    c.setLineWidth(width)
    c.drawPath(path)


def arrow(c, x1, y1, x2, y2):
    line(c, [(x1, y1), (x2, y2)])
    angle = atan2(y2 - y1, x2 - x1)
    ends = [(x2 - 4 * cos(angle + a), y2 - 4 * sin(angle + a)) for a in (-0.45, 0.45)]
    line(c, [ends[0], (x2, y2), ends[1]])


def box(c, x, y, w, h, fill=None):
    c.setStrokeColor(RULE)
    c.setLineWidth(0.55)
    if fill is not None:
        c.setFillColor(fill)
    c.roundRect(x, y, w, h, 2.5, stroke=1, fill=int(fill is not None))


def address(c, x, y, address, color):
    given, rest = address.split(".", 1)
    surname, domain = rest.split("@", 1)
    for segment, tint in [(given + ".", INK), (surname, color), ("@" + domain, INK)]:
        text(c, x, y, segment, 10, CODE, tint)
        x += stringWidth(segment, CODE, 10)


def recipient_cell(c, y, label, record):
    box(c, 143, y, 175, 28, HexColor("#ffffff"))
    text(c, 151, y + 17, label, 9.2)
    color = TEAL if record["destination"].startswith("kofi.mensah@") else OCHRE
    address(c, 151, y + 5, record["destination"], color)


def alias_cell(c, y, alias):
    box(c, 352, y, 140, 24, PALE_BLUE)
    text(c, 361, y + 8, "Recipient", 9.7)
    text(c, 408, y + 7.5, alias, 10.5, CODE, BLUE)


def icon(c, x, y, kind, color=INK):
    """Small line symbols for directory records, an email, and a log."""
    c.saveState()
    c.setStrokeColor(color)
    c.setLineWidth(0.7)
    if kind == "email":
        c.rect(x, y, 10, 7, stroke=1, fill=0)
        line(c, [(x, y + 7), (x + 5, y + 3), (x + 10, y + 7)], color, 0.7)
    elif kind == "directory":
        c.roundRect(x, y - 1, 10, 10, 1, stroke=1, fill=0)
        c.circle(x + 3, y + 5, 1.2, stroke=1, fill=0)
        line(
            c,
            [
                (x + 1.5, y + 1),
                (x + 1.5, y + 2.5),
                (x + 4.5, y + 2.5),
                (x + 4.5, y + 1),
            ],
            color,
            0.6,
        )
        line(c, [(x + 6, y + 5), (x + 8.5, y + 5)], color, 0.6)
        line(c, [(x + 6, y + 2), (x + 8.5, y + 2)], color, 0.6)
    else:
        c.rect(x + 1, y - 1, 8, 10, stroke=1, fill=0)
        for d in (1, 4, 7):
            line(c, [(x + 3, y + d), (x + 7, y + d)], color, 0.6)
    c.restoreState()


def kofi_figure(directory, cases):
    c = figure(
        directory / "kofi-comparison.pdf",
        218,
        "Directory evidence in two histories of the same request",
    )
    # A bounded header belongs visibly to the figure, not the surrounding prose.
    box(c, 6, 191, 488, 21, HexColor("#edf0f3"))
    for x, kind, label in [
        (13, "directory", "Directory evidence"),
        (149, "email", "Destination in outbox"),
        (358, "log", "Address in exported log"),
    ]:
        icon(c, x, 197, kind)
        text(c, x + 15, 198, label, 9.3, "Times-Bold", limit=130)
    line(c, [(134, 192), (134, 211)], RULE, 0.5)
    line(c, [(341, 192), (341, 211)], RULE, 0.5)

    box(c, 6, 100, 488, 84, HexColor("#e7eff8"))
    text(c, 14, 171, "(a) Original error and corrected replay", 10, "Times-Bold")
    box(c, 14, 125, 106, 28, HexColor("#ffffff"))
    text(c, 22, 136, "No lookup of Kofi", 9.7)
    recipient_cell(c, 136, "Archived error", cases["observed"])
    recipient_cell(c, 104, "Corrected replay", cases["corrected"])
    line(c, [(120, 139), (130, 139), (130, 150)])
    line(c, [(130, 139), (130, 118)])
    arrow(c, 130, 150, 140, 150)
    arrow(c, 130, 118, 140, 118)
    line(c, [(318, 150), (334, 150), (334, 118), (318, 118)])
    arrow(c, 334, 134, 349, 134)
    assert cases["observed"]["alias"] == cases["corrected"]["alias"]
    alias_cell(c, 122, cases["observed"]["alias"])

    box(c, 6, 5, 488, 88, HexColor("#e6f1ec"))
    text(
        c,
        14,
        80,
        "(b) Previously successful history with the same error inserted",
        10,
        "Times-Bold",
    )
    box(c, 14, 32, 106, 28, HexColor("#ffffff"))
    text(c, 22, 48, "Lookup of Kofi", 9.7)
    (lookup,) = cases["successful"]["lookups"]
    text(c, 22, 36, lookup, 10, CODE, BLUE)
    recipient_cell(c, 49, "Archived correct send", cases["successful"])
    recipient_cell(c, 15, "Replay with wrong address", cases["inserted"])
    line(c, [(120, 46), (130, 46), (130, 63)])
    line(c, [(130, 46), (130, 29)])
    arrow(c, 130, 63, 140, 63)
    arrow(c, 130, 29, 140, 29)
    arrow(c, 318, 63, 349, 63)
    arrow(c, 318, 29, 349, 29)
    alias_cell(c, 51, cases["successful"]["alias"])
    alias_cell(c, 17, cases["inserted"]["alias"])
    c.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("figures/figure-data.json"))
    parser.add_argument("--output", type=Path, default=Path("figures"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    data = json.loads(args.data.read_text())
    register_fonts()
    kofi_figure(args.output, data["kofi"])


if __name__ == "__main__":
    main()
