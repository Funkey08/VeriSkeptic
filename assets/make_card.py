"""Render a 1280x640 GitHub social-preview card for VeriSkeptic."""
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

W, H, DPI = 1280, 640, 100
PX = 72 / DPI  # points per pixel, so font sizes can be given in px

BG = "#0d1117"
FG = "#e6edf3"
MUTED = "#8b949e"
FAINT = "#21262d"
LINE = "#30363d"
ACCENT = "#3fb950"  # signal green
WARN = "#d29922"

SANS = "Avenir Next"
MONO = "Menlo"

fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, W)
ax.set_ylim(H, 0)
ax.axis("off")
fig.patch.set_facecolor(BG)


def text(x, y, s, size, color=FG, font=SANS, weight="normal", **kw):
    ax.text(x, y, s, fontsize=size * PX, color=color, family=font, weight=weight, va="center", **kw)


# --- decorative waveform along the bottom -------------------------------------------------
def wave(y, bits, color, amp=14, x0=0, step=40, lw=2.0, alpha=1.0):
    xs, ys = [], []
    for i, b in enumerate(bits):
        lvl = y - amp if b else y + amp
        xs += [x0 + i * step, x0 + (i + 1) * step]
        ys += [lvl, lvl]
    ax.plot(xs, ys, color=color, lw=lw, alpha=alpha, solid_joinstyle="miter")


n = W // 40 + 1
wave(548, [i % 2 for i in range(n)], LINE, lw=1.6, x0=64)
data = [0, 0, 1, 1, 1, 0, 1, 0, 0, 1, 1, 0, 1, 1, 1, 0, 0, 0, 1, 0, 1, 1, 0, 1, 0, 0, 1, 1, 0, 1, 0, 1, 1]
wave(596, data[:n], ACCENT, lw=2.0, alpha=0.55, x0=64)
text(18, 548, "clk", 14, MUTED, MONO)
text(18, 596, "dut", 14, MUTED, MONO)

# --- header ------------------------------------------------------------------------------
text(72, 70, "Funkey08 / VeriSkeptic", 22, MUTED, MONO)
text(68, 150, "VeriSkeptic", 92, FG, SANS, weight="bold")
text(72, 232, "A self-verifying LLM agent for RTL design.", 30, FG)
text(72, 274, "How much of the gain survives without the answer key?", 24, MUTED)

# --- agent loop --------------------------------------------------------------------------
steps = ["spec", "Verilog", "compile", "own testbench", "simulate"]
widths = [96, 128, 134, 210, 148]
gap, y, h = 34, 352, 54
x = 72
boxes = []
for s, w in zip(steps, widths):
    hl = s == "own testbench"
    ax.add_patch(FancyBboxPatch((x, y - h / 2), w, h, boxstyle="round,pad=0,rounding_size=10",
                                fc="#0f2a1a" if hl else FAINT, ec=ACCENT if hl else LINE, lw=1.6))
    text(x + w / 2, y, s, 21, ACCENT if hl else FG, MONO, ha="center")
    boxes.append((x, w))
    x += w + gap

for (x1, w1), (x2, _) in zip(boxes, boxes[1:]):
    ax.add_patch(FancyArrowPatch((x1 + w1 + 4, y), (x2 - 4, y), arrowstyle="-|>",
                                 mutation_scale=14, color=MUTED, lw=1.5))

# revise loop: from simulate back to Verilog, under the row
sx = boxes[-1][0] + boxes[-1][1] / 2
vx = boxes[1][0] + boxes[1][1] / 2
ax.add_patch(FancyArrowPatch((sx, y + h / 2 + 2), (vx, y + h / 2 + 2),
                             connectionstyle="bar,fraction=-0.06", arrowstyle="-|>",
                             mutation_scale=14, color=WARN, lw=1.6))
text((sx + vx) / 2, y + h / 2 + 60, "revise  ·  up to 5 design versions", 17, WARN, MONO, ha="center")

# --- experimental modes panel -----------------------------------------------------------
px, pw, py0, rh = 994, 226, 118, 72
text(px, py0 - 26, "FEEDBACK THE AGENT SEES", 13, MUTED, MONO)
modes = [("A", "baseline", "none · one call", False),
         ("B", "compile", "compiler errors", False),
         ("C", "self-verified", "its own tests", True),
         ("D", "oracle", "hidden tests", False)]
for i, (k, name, fb, hl) in enumerate(modes):
    ty = py0 + i * rh
    ax.add_patch(FancyBboxPatch((px, ty), pw, rh - 12, boxstyle="round,pad=0,rounding_size=10",
                                fc="#0f2a1a" if hl else BG, ec=ACCENT if hl else LINE, lw=1.6 if hl else 1.2))
    text(px + 18, ty + 30, k, 24, ACCENT if hl else MUTED, MONO, weight="bold")
    text(px + 52, ty + 19, name, 19, FG, weight="bold" if hl else "normal")
    text(px + 52, ty + 42, fb, 14, ACCENT if hl else MUTED, MONO)
text(px, py0 + 4 * rh + 6, "D = upper bound only", 13, MUTED, MONO)

# --- footer ------------------------------------------------------------------------------
text(72, 488, "VerilogEval v2  ·  152 problems  ·  Icarus Verilog  ·  Gemini + Claude", 19, MUTED)

out = sys.argv[1] if len(sys.argv) > 1 else "social-preview.png"
fig.savefig(out, dpi=DPI, facecolor=BG)
print(out)
