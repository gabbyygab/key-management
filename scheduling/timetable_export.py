"""Section timetable as one landscape image (PNG) or one landscape PDF page.

Both outputs come from the same layout, so they match: a titled header, a
Monday-to-Saturday grid trimmed to the hours the section actually uses, each
class as a coloured card (code, title, time, room, instructor), and a legend
under the grid that spells out every subject in full, because a narrow card
has to truncate its title.

``layout()`` works in abstract units on a 1200-unit-wide sheet and returns
draw operations; ``render_png`` / ``render_pdf`` only paint them. Text is
measured with Bitstream Vera (shipped inside reportlab) in both, so a label
that fits in one fits in the other. No browser, no node, no extra packages:
Pillow and reportlab are already dependencies.
"""
import io
import math
import os
import re

from django.http import HttpResponse
from django.utils import timezone

from reportlab import rl_config
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas

from PIL import Image, ImageDraw, ImageFont

from .planner import get_section_timetable
from .services import PALETTES

# --- fonts -----------------------------------------------------------------

_FONT_DIR = os.path.join(os.path.dirname(rl_config.__file__), "fonts")
_FONT_FILES = {False: os.path.join(_FONT_DIR, "Vera.ttf"),
               True: os.path.join(_FONT_DIR, "VeraBd.ttf")}
_PDF_FONT = {False: "KTVera", True: "KTVera-Bold"}
_METRIC_PX = 200  # reference size the widths are measured at

_metric_fonts = {}
_pdf_fonts_ready = False


def _metric_font(bold):
    if bold not in _metric_fonts:
        _metric_fonts[bold] = ImageFont.truetype(_FONT_FILES[bold], _METRIC_PX)
    return _metric_fonts[bold]


def text_width(text, size, bold=False):
    """Width of ``text`` in layout units at ``size`` units."""
    return _metric_font(bold).getlength(text) * size / _METRIC_PX


def _ensure_pdf_fonts():
    global _pdf_fonts_ready
    if _pdf_fonts_ready:
        return
    for bold, name in _PDF_FONT.items():
        try:
            pdfmetrics.getFont(name)
        except KeyError:
            pdfmetrics.registerFont(TTFont(name, _FONT_FILES[bold]))
    _pdf_fonts_ready = True


# --- colours ---------------------------------------------------------------

# The same eight hues the on-screen grid uses (scheduling.services.PALETTES):
# card fill, card edge, left accent bar, text.
_HUES = {
    "emerald": ("#ECFDF5", "#6EE7B7", "#059669", "#022C22"),
    "blue": ("#EFF6FF", "#93C5FD", "#2563EB", "#172554"),
    "violet": ("#F5F3FF", "#C4B5FD", "#7C3AED", "#2E1065"),
    "amber": ("#FFFBEB", "#FCD34D", "#D97706", "#451A03"),
    "rose": ("#FFF1F2", "#FDA4AF", "#E11D48", "#4C0519"),
    "sky": ("#F0F9FF", "#7DD3FC", "#0284C7", "#082F49"),
    "indigo": ("#EEF2FF", "#A5B4FC", "#4F46E5", "#1E1B4B"),
    "teal": ("#F0FDFA", "#5EEAD4", "#0D9488", "#042F2E"),
}
INK = "#1C1917"
MUTED = "#78716C"
BRAND_DARK = "#14342A"
BRAND_GREEN = "#1E6B4A"
GRID_HOUR = "#D6D3D1"
GRID_HALF = "#EEECEA"


def _hue_for(palette):
    m = re.match(r"bg-(\w+)-", palette.get("bg", ""))
    return _HUES.get(m.group(1) if m else "", _HUES["emerald"])


# --- model -----------------------------------------------------------------

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _hm(minute):
    """540 -> '9:00 AM'."""
    h, m = divmod(minute, 60)
    suffix = "AM" if h < 12 else "PM"
    return f"{(h % 12) or 12}:{m:02d} {suffix}"


def build_model(section, term, include_groups=True):
    """Everything a renderer needs, from the same data the page shows."""
    tt = get_section_timetable(section, term, include_groups=include_groups)

    blocks = []
    for p in tt["blocks"]:
        b = p["block"]
        start = b.start_time.hour * 60 + b.start_time.minute
        end = b.end_time.hour * 60 + b.end_time.minute
        code = p["subject_code"] or p["title"]
        tag = ""
        if p["shared_groups"]:
            tag = p["shared_groups"]  # "G1 + G2": every group attends together
        elif p["is_group_block"]:
            m = re.search(r"G\d+$", p["section_code"] or "")
            tag = m.group(0) if m else ""
        blocks.append({
            "day": p["day_of_week"], "start": start, "end": end,
            "code": code, "title": p["subject_title"] or p["title"], "tag": tag,
            "room": p["room_label"], "instructor": p["instructor_name"],
            "lane": p["lane"], "lanes": p["lane_count"],
            "hue": _hue_for(p["palette"]),
            "time": f"{_hm(start)} – {_hm(end)}",
        })

    used_days = {b["day"] for b in blocks}
    days = [d for d in range(6)] + ([6] if 6 in used_days else [])

    legend = {}
    for b in blocks:
        entry = legend.setdefault(b["code"], {
            "code": b["code"], "title": b["title"], "hue": b["hue"],
            "instructors": [], "rooms": [],
        })
        if b["instructor"] and b["instructor"] not in entry["instructors"]:
            entry["instructors"].append(b["instructor"])
        if b["room"] and b["room"] not in entry["rooms"]:
            entry["rooms"].append(b["room"])

    scope = ""
    if section is not None and section.is_whole_block:
        scope = "Block and group classes" if include_groups else "Block-wide classes only"
    return {
        "heading": f"{section.code} Class Timetable" if section else "Class Timetable",
        "term": str(term) if term else "",
        "scope": scope,
        "department": (section.department.name if section is not None
                       and getattr(section, "department_id", None) else ""),
        "classes": tt["total_blocks"],
        "hours": tt["total_hours"],
        "days": [(d, DAY_NAMES[d]) for d in days],
        "blocks": blocks,
        "legend": sorted(legend.values(), key=lambda e: e["code"]),
        "generated": timezone.localtime().strftime("%Y-%m-%d %I:%M %p"),
    }


# --- layout ----------------------------------------------------------------

SHEET_W = 1200.0
MARGIN = 28.0


def fit(text, size, bold, max_w):
    """``text`` cut with an ellipsis so it is at most ``max_w`` units wide."""
    if max_w <= 0:
        return ""
    if text_width(text, size, bold) <= max_w:
        return text
    ell = "…"
    while text and text_width(text + ell, size, bold) > max_w:
        text = text[:-1]
    return (text.rstrip() + ell) if text else ""


def wrap(text, size, bold, max_w, max_lines):
    """Greedy word wrap into at most ``max_lines`` lines; the last one is cut
    with an ellipsis if text is left over."""
    words, lines, cur = text.split(), [], ""
    for i, w in enumerate(words):
        trial = f"{cur} {w}".strip()
        if text_width(trial, size, bold) <= max_w or not cur:
            cur = trial
            continue
        lines.append(cur)
        cur = w
        if len(lines) == max_lines - 1:
            cur = " ".join(words[i:])
            break
    if cur:
        lines.append(cur)
    lines = lines[:max_lines]
    if lines and " ".join(lines).split() != words:
        lines[-1] += "…"  # text was dropped: say so
    return [fit(ln, size, bold, max_w) for ln in lines]


def layout(model, *, target_h, max_hour_h, fill_sheet=False):
    """Draw operations for the sheet.

    Returns ``(ops, sheet_h)``. Ops are tuples whose y is top-down:
    ``("rect", x, y, w, h, fill, stroke, radius, stroke_w)``,
    ``("line", x1, y1, x2, y2, color, width)`` and
    ``("text", x, baseline_y, string, size, bold, color, anchor)`` with anchor
    ``"l"``, ``"r"`` or ``"c"``. With ``fill_sheet`` the sheet is padded to
    ``target_h`` (a fixed PDF page); otherwise it is only as tall as it needs.
    """
    ops = []
    W = SHEET_W
    M = MARGIN
    blocks = model["blocks"]

    # Hours shown: what the classes use, padded to whole hours, at least five.
    if blocks:
        first = min(b["start"] for b in blocks) // 60 * 60
        last = math.ceil(max(b["end"] for b in blocks) / 60) * 60
    else:
        first, last = 7 * 60, 17 * 60
    first = max(first, 7 * 60)
    last = min(max(last, first + 5 * 60), 21 * 60)
    first = min(first, last - 5 * 60)
    hours = (last - first) // 60

    # Legend geometry (two columns, one line per subject).
    legend = model["legend"]
    legend_rows = math.ceil(len(legend) / 2)
    legend_line = 19.0
    legend_h = (24 + legend_rows * legend_line) if legend else 0

    head_h = 74.0
    day_head_h = 28.0
    footer_h = 26.0
    top_of_grid = M + head_h - 8 + day_head_h
    below_grid = (14 + legend_h) if legend else 6
    hour_h = max(30.0, min(max_hour_h, (target_h - top_of_grid - below_grid - footer_h) / hours))
    grid_h = hours * hour_h
    sheet_h = top_of_grid + grid_h + below_grid + footer_h
    if fill_sheet:
        sheet_h = max(sheet_h, target_h)

    # --- header
    ops.append(("rect", 0, 0, W, sheet_h, "#FFFFFF", None, 0, 0))
    ops.append(("text", M, M + 22, model["heading"], 24, True, BRAND_DARK, "l"))
    right = model["term"]
    ops.append(("text", W - M, M + 22, right, 15, True, BRAND_GREEN, "r"))
    bits = [x for x in (model["department"], model["scope"],
                        f"{model['classes']} classes", f"{model['hours']:g} weekly hours") if x]
    ops.append(("text", M, M + 44, "  ·  ".join(bits), 12, False, MUTED, "l"))
    ops.append(("line", M, M + 56, W - M, M + 56, BRAND_GREEN, 2))

    # --- grid frame
    time_w = 70.0
    days = model["days"]
    col_w = (W - 2 * M - time_w) / len(days)
    gx0 = M + time_w
    gx1 = W - M
    ops.append(("rect", M, M + head_h - 8, W - 2 * M, day_head_h, BRAND_GREEN, None, 0, 0))
    ops.append(("text", M + time_w / 2, M + head_h - 8 + 19, "TIME", 11, True, "#D1FAE5", "c"))
    for i, (_, name) in enumerate(days):
        ops.append(("text", gx0 + i * col_w + col_w / 2, M + head_h - 8 + 19,
                    name, 14, True, "#FFFFFF", "c"))

    grid_top = top_of_grid
    ops.append(("rect", M, grid_top, W - 2 * M, grid_h, "#FFFFFF", GRID_HOUR, 0, 1))
    for h in range(hours + 1):
        y = grid_top + h * hour_h
        if h < hours:
            ops.append(("line", gx0, y + hour_h / 2, gx1, y + hour_h / 2, GRID_HALF, 1))
            ops.append(("text", gx0 - 8, y + 12, _hm(first + h * 60), 11, True, INK, "r"))
        if 0 < h < hours:
            ops.append(("line", M, y, gx1, y, GRID_HOUR, 1))
    for i in range(len(days) + 1):
        x = gx0 + i * col_w
        ops.append(("line", x, grid_top, x, grid_top + grid_h, GRID_HOUR, 1))

    # --- classes
    day_col = {d: i for i, (d, _) in enumerate(days)}
    pad = 5.0
    for b in sorted(blocks, key=lambda b: (b["day"], b["start"], b["lane"])):
        if b["day"] not in day_col:
            continue
        lane_w = col_w / b["lanes"]
        x = gx0 + day_col[b["day"]] * col_w + b["lane"] * lane_w + 2
        w = lane_w - 4
        y = grid_top + (max(b["start"], first) - first) / 60 * hour_h + 1.5
        h = (min(b["end"], last) - max(b["start"], first)) / 60 * hour_h - 3
        if h <= 4 or w <= 8:
            continue
        fill, edge, accent, ink = b["hue"]
        ops.append(("rect", x, y, w, h, fill, edge, 5, 1))
        ops.append(("rect", x, y + 1, 5, h - 2, accent, None, 2, 0))

        inner_x = x + 5 + pad
        inner_w = w - 5 - 2 * pad
        size = 12.0 if w >= 120 else 10.5
        line_h = size * 1.28
        room_lines = max(1, int((h - 2 * 4) // line_h))
        code = b["code"] + (f"  {b['tag']}" if b["tag"] else "")
        wanted = [("code", [fit(code, size + 1, True, inner_w)], True, size + 1)]
        # Priority when the card is short: code, time, room, title, instructor.
        extras = {
            "time": ([fit(b["time"], size - 1, False, inner_w)], False, size - 1),
            "room": ([fit(b["room"], size - 1, True, inner_w)], True, size - 1) if b["room"] else None,
            "instructor": ([fit(b["instructor"], size - 1, False, inner_w)], False, size - 1),
        }
        budget = room_lines - 1
        shown = {}
        for key in ("time", "room", "title", "instructor"):
            if budget <= 0:
                break
            if key == "title":
                t_lines = wrap(b["title"], size - 0.5, False, inner_w, 2 if budget >= 3 else 1)
                t_lines = t_lines[:budget]
                shown[key] = (t_lines, False, size - 0.5)
                budget -= len(t_lines)
            elif extras.get(key):
                shown[key] = extras[key]
                budget -= 1
        order = ["title", "time", "room", "instructor"]
        cursor = y + 5
        for key, lines, bold, sz in [wanted[0]] + [(k, *shown[k]) for k in order if k in shown]:
            for ln in lines:
                ops.append(("text", inner_x, cursor + sz * 0.8, ln, sz, bold,
                            ink if key in ("code", "title") else "#44403C", "l"))
                cursor += sz * 1.28

    if not blocks:
        ops.append(("text", (gx0 + gx1) / 2, grid_top + grid_h / 2,
                    "No classes are scheduled for this section in this term.",
                    15, False, MUTED, "c"))

    # --- legend
    y = grid_top + grid_h + 14
    if legend:
        ops.append(("text", M, y + 12, "SUBJECTS", 11, True, BRAND_GREEN, "l"))
        y += 24
        col_x = [M, M + (W - 2 * M) / 2 + 8]
        col_w2 = (W - 2 * M) / 2 - 8
        for i, e in enumerate(legend):
            cx = col_x[i % 2]
            cy = y + (i // 2) * legend_line
            fill, edge, accent, ink = e["hue"]
            ops.append(("rect", cx, cy + 2, 11, 11, fill, accent, 2, 1.2))
            code_w = text_width(e["code"], 11.5, True)
            ops.append(("text", cx + 18, cy + 11.5, e["code"], 11.5, True, INK, "l"))
            detail = e["title"]
            who = " / ".join(e["instructors"])
            where = ", ".join(e["rooms"])
            tail = "  ·  ".join(x for x in (who, where) if x)
            room_for = col_w2 - 18 - code_w - 10
            title_part = fit(detail, 11, False, room_for * 0.55 if tail else room_for)
            used = text_width(title_part, 11, False)
            ops.append(("text", cx + 18 + code_w + 8, cy + 11.5, title_part, 11, False, INK, "l"))
            if tail:
                ops.append(("text", cx + 18 + code_w + 8 + used + 8, cy + 11.5,
                            fit("— " + tail, 10.5, False, room_for - used - 8), 10.5, False, MUTED, "l"))

    # --- footer
    foot_y = sheet_h - 11
    ops.append(("text", M, foot_y, "KEYTRACK — University Key Management System", 9.5, False, MUTED, "l"))
    ops.append(("text", W - M, foot_y, f"Generated {model['generated']}", 9.5, False, MUTED, "r"))
    return ops, sheet_h


# --- painters --------------------------------------------------------------

def render_png(model, scale=2.5):
    """One landscape PNG, ``1200 * scale`` px wide."""
    ops, sheet_h = layout(model, target_h=SHEET_W * 0.68, max_hour_h=96.0)
    img = Image.new("RGB", (round(SHEET_W * scale), round(sheet_h * scale)), "#FFFFFF")
    d = ImageDraw.Draw(img)
    fonts = {}

    def font(size, bold):
        key = (round(size * scale * 4), bold)
        if key not in fonts:
            fonts[key] = ImageFont.truetype(_FONT_FILES[bold], size * scale)
        return fonts[key]

    for op in ops:
        kind = op[0]
        if kind == "rect":
            _, x, y, w, h, fill, stroke, radius, sw = op
            box = [x * scale, y * scale, (x + w) * scale - 1, (y + h) * scale - 1]
            kw = {"fill": fill}
            if stroke:
                kw.update(outline=stroke, width=max(1, round(sw * scale)))
            if radius:
                d.rounded_rectangle(box, radius=radius * scale, **kw)
            else:
                d.rectangle(box, **kw)
        elif kind == "line":
            _, x1, y1, x2, y2, color, width = op
            d.line([x1 * scale, y1 * scale, x2 * scale, y2 * scale], fill=color,
                   width=max(1, round(width * scale)))
        else:
            _, x, y, s, size, bold, color, anchor = op
            d.text((x * scale, y * scale), s, font=font(size, bold), fill=color,
                   anchor={"l": "ls", "r": "rs", "c": "ms"}[anchor])
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True, dpi=(250, 250))
    return buf.getvalue()


def render_pdf(model):
    """One landscape US-letter page."""
    _ensure_pdf_fonts()
    page_w, page_h = landscape(letter)
    k = page_w / SHEET_W
    ops, sheet_h = layout(model, target_h=page_h / k, max_hour_h=110.0, fill_sheet=True)
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(page_w, page_h))
    c.setTitle(model["heading"])
    c.setAuthor("KEYTRACK")

    def Y(y):
        return page_h - y * k

    for op in ops:
        kind = op[0]
        if kind == "rect":
            _, x, y, w, h, fill, stroke, radius, sw = op
            c.setFillColor(fill)
            if stroke:
                c.setStrokeColor(stroke)
                c.setLineWidth(sw * k)
            if radius:
                c.roundRect(x * k, Y(y + h), w * k, h * k, radius * k, stroke=1 if stroke else 0, fill=1)
            else:
                c.rect(x * k, Y(y + h), w * k, h * k, stroke=1 if stroke else 0, fill=1)
        elif kind == "line":
            _, x1, y1, x2, y2, color, width = op
            c.setStrokeColor(color)
            c.setLineWidth(width * k)
            c.line(x1 * k, Y(y1), x2 * k, Y(y2))
        else:
            _, x, y, s, size, bold, color, anchor = op
            c.setFillColor(color)
            c.setFont(_PDF_FONT[bold], size * k)
            if anchor == "l":
                c.drawString(x * k, Y(y), s)
            elif anchor == "r":
                c.drawRightString(x * k, Y(y), s)
            else:
                c.drawCentredString(x * k, Y(y), s)
    c.showPage()
    c.save()
    return buf.getvalue()


def export_section_timetable(section, term, fmt, *, include_groups=True):
    """HttpResponse for ``fmt`` in ``png`` or ``pdf``."""
    model = build_model(section, term, include_groups=include_groups)
    label = re.sub(r"[^A-Za-z0-9_-]+", "_", f"{section.code}_{term}").strip("_")
    if fmt == "png":
        resp = HttpResponse(render_png(model), content_type="image/png")
        resp["Content-Disposition"] = f'attachment; filename="timetable_{label}.png"'
    else:
        resp = HttpResponse(render_pdf(model), content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="timetable_{label}.pdf"'
    return resp
