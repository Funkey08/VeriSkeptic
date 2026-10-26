"""Render a 1280x640 GitHub social-preview card for VeriSkeptic, with the headline results."""
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
    ax.text(x, y, s, fontsize=size * PX, color=color, family=[font, MONO],  # Menlo supplies missing glyphs (→)
            weight=weight, va="center", **kw)


# --- decorative waveform along the bottom -------------------------------------------------
def wave(y, bits, color, amp=12, x0=0, step=40, lw=2.0, alpha=1.0):
    xs, ys = [], []
    for i, b in enumerate(bits):
        lvl = y - amp if b else y + amp
        xs += [x0 + i * step, x0 + (i + 1) * step]
        ys += [lvl, lvl]
    ax.plot(xs, ys, color=color, lw=lw, alpha=alpha, solid_joinstyle="miter")


n = W // 40 + 1
wave(574, [i % 2 for i in range(n)], LINE, lw=1.6, x0=64)
data = [0, 0, 1, 1, 1, 0, 1, 0, 0, 1, 1, 0, 1, 1, 1, 0, 0, 0, 1, 0, 1, 1, 0, 1, 0, 0, 1, 1, 0, 1, 0, 1, 1]
wave(612, data[:n], ACCENT, lw=2.0, alpha=0.55, x0=64)
text(18, 574, "clk", 14, MUTED, MONO)
text(18, 612, "dut", 14, MUTED, MONO)

# --- header ------------------------------------------------------------------------------
text(72, 64, "Funkey08 / VeriSkeptic", 22, MUTED, MONO)
text(68, 140, "VeriSkeptic", 88, FG, SANS, weight="bold")
text(72, 218, "A self-verifying LLM agent for RTL design.", 30, FG)
text(72, 258, "The repair loop works. Its own tests are the bottleneck.", 24, MUTED)

# --- agent loop --------------------------------------------------------------------------
steps = ["spec", "Verilog", "compile", "own testbench", "simulate"]
widths = [96, 128, 134, 210, 148]
gap, y, h = 34, 330, 52
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
text((sx + vx) / 2, y + h / 2 + 56, "revise  ·  80.9% → 87.5%  ·  10 fixed, 0 broken", 17, WARN, MONO, ha="center")

# --- key numbers -------------------------------------------------------------------------
stats = [("p = 0.002", "loop gain, McNemar"), ("56.7 → 70.0%", "FSMs vs. single call"),
         ("12.5%", "false confidence"), ("$0.013", "per design")]
cw, cg, cy, ch = 200, 17, 452, 76
for i, (big, small) in enumerate(stats):
    cx = 72 + i * (cw + cg)
    ax.add_patch(FancyBboxPatch((cx, cy), cw, ch, boxstyle="round,pad=0,rounding_size=10",
                                fc=FAINT, ec=LINE, lw=1.2))
    text(cx + 16, cy + 27, big, 24, FG, weight="bold")
    text(cx + 16, cy + 56, small, 14, MUTED, MONO)

# --- results panel: pass@1 per mode ------------------------------------------------------
px, pw, py0, rh = 994, 226, 104, 112
text(px, py0 - 26, "PASS@1 · 152 PROBLEMS", 13, MUTED, MONO)
modes = [("A", "single call", 86.2, "no feedback", False),
         ("C", "self-verified", 87.5, "its own tests only", True),
         ("D", "oracle", 97.4, "hidden tests · upper bound", False)]
for i, (k, name, val, note, hl) in enumerate(modes):
    ty = py0 + i * rh
    ax.add_patch(FancyBboxPatch((px, ty), pw, rh - 14, boxstyle="round,pad=0,rounding_size=10",
                                fc="#0f2a1a" if hl else BG, ec=ACCENT if hl else LINE, lw=1.6 if hl else 1.2))
    text(px + 16, ty + 24, k, 18, ACCENT if hl else MUTED, MONO, weight="bold")
    text(px + 40, ty + 24, name, 16, FG, weight="bold" if hl else "normal")
    text(px + pw - 16, ty + 24, f"{val:.1f}%", 20, ACCENT if hl else FG, weight="bold", ha="right")
    bx, bw, by = px + 16, pw - 32, ty + 50  # meter on a 0-100 scale
    ax.add_patch(FancyBboxPatch((bx, by), bw, 6, boxstyle="round,pad=0,rounding_size=3", fc=FAINT, ec="none"))
    ax.add_patch(FancyBboxPatch((bx, by), bw * val / 100, 6, boxstyle="round,pad=0,rounding_size=3",
                                fc=ACCENT if hl else MUTED, ec="none"))
    text(px + 16, ty + 76, note, 12, ACCENT if hl else MUTED, MONO)
text(px, py0 + 3 * rh + 2, "model: gemini-3.5-flash-lite", 12, MUTED, MONO)

out = sys.argv[1] if len(sys.argv) > 1 else "social-preview.png"
fig.savefig(out, dpi=DPI, facecolor=BG)
print(out)
