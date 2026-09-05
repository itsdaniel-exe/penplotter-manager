# Pen Plotter Console — Handoff

Paste this whole file into a new chat to pick up with full context.

## Who/what this is

Daniel runs a business (**HandScript**, in `C:\Users\dabbe\Desktop\pen ploter business\penplotter app`
— a separate Next.js/Prisma SaaS where customers pay to have documents "handwritten"
by a pen plotter) and physically owns a DIY GRBL pen plotter (Arduino Uno + GRBL,
CoreXY-style gantry, servo pen lift — a "4xiDraw" style build sold as a "Writing
Machine" by the reseller Creativity Buzz). This project (`penplotter manager/`) is a
**standalone local tool** that automates the machine's whole workflow, replacing the
manual chain of Inkscape (Hershey Text + 4xiDraw gcode export) → Universal G-code
Sender. It is **not** wired into the HandScript business app — that's explicitly
deferred (see the bottom of `README.md`). Don't start it unless asked.

## Status: calibrated and working

The machine connects on **COM8** and has been driven for real — jogged, pen tested,
and test-printed. Calibration is **complete**; the values below are established fact,
not guesses, and `tests/test_calibration.py` guards them. Run it after touching
anything in `config.py`, `gcode.py`, `stream.py` or `server.py`:

```bash
.venv\Scripts\python -m tests.test_calibration
```

### The calibrated facts

| What | Value | Evidence |
| --- | --- | --- |
| Work area | 195 × 300mm | User marked opposite corners; matches the old `bounndrycreation1_*.gcode` boundary tests (194.5 × 294.5) |
| `invert_x` | `True` | Test print — X+ runs physically **left** |
| `invert_y` | `False` | Test print — Y+ runs **toward the operator** |
| `swap_pen` | `True` | `M03 S50` lifts, `M05 S10` lowers on this build |
| Steps/mm | 81.800 both axes | GRBL `$100`/`$101` — non-default, already calibrated by someone |
| Homing | none | `$21=0`, `$22=0` — no limit switches exist |
| Soft limits | off | `$20=0` — nothing stops an overtravel but the operator's eyes |

Machine zero (`Zero here`) is the **top-right corner of the page**; a job runs from
there in +X (physically left) and +Y (physically toward the operator). So zero belongs
at the paper's far-right corner. The machine writes relative to zero, never relative to
the paper — moving the paper after zeroing misplaces the text.

## Things that look like bugs but aren't — don't re-litigate

- **`swap_pen` exists because this build's servo rest state is physically *down*.**
  `M05` ignores its `S` value (it's GRBL "spindle off"), so swapping the *numbers*
  does nothing; the *command* is what matters. The real historical gcode confirms the
  mapping: `M03` before travel, `M05` before drawing.
- **`$130`/`$131` mean nothing here.** They read GRBL's stock 200.000 default and are
  unenforced with `$20=0`. Not the work area. The UI says so explicitly now.
- **Home is disabled on purpose.** `$H` seeks a limit switch; with `$22=0` it can only
  error. The UI auto-disables it when the banner reports `$22 != 1` (and re-enables it
  if that ever changes). **Go to zero** covers the practical need.
- **The position readout cannot track the gantry being pushed by hand.** Open-loop
  steppers, no encoders — nothing in GRBL or this software can sense uncommanded
  movement. The user asked for this; it is not implementable in software. Workaround is
  `Zero here` after any manual move; the real fix is hardware (limit switches ~£5,
  which would also make `$H` work). **Do not attempt a software version of this.**

## Bugs found and fixed (all verified)

- **"Could not connect to COM8: Access is denied"** — Windows allows one handle per COM
  port, but every browser tab got its own `Session`/`GrblStreamer`, and `connect()`
  replaced `state.ws` without closing the old socket. Stale tabs and repeat Connect
  clicks each stranded an open handle; this locked the user out of their own machine
  twice (four live WS sessions were found open at once). Fixed with
  `_take_port_ownership()`/`_release_port_ownership()` in `server.py` — newest
  connection wins and the previous holder is closed (reported to the UI as `tookOver`).
- **Pen up/down buttons ignored the Servo up/down fields** — always sent hardcoded
  defaults, so pen height couldn't be tuned at all.
- **Resume was permanently disabled** — nothing ever enabled it, so a paused job could
  never be resumed. Pause/Resume/Cancel now track job state properly.
- **No way to send `$X`** to clear a GRBL alarm from the UI. Added an Unlock button.
- **Browser cached `app.js`**, so fixes looked like they silently did nothing — cost
  real debugging time against live hardware. `index()` now stamps
  `/static/app.js?v=<mtime>`. If a frontend change ever *seems* not to apply, check
  what's actually served before assuming the logic is wrong.
- **Jog arrows moved opposite to their labels.** They now derive direction from
  `invert_x`/`invert_y`, so they move the way they point and stay correct if the
  machine is ever rewired.
- **Canvas sized itself from a container measured before layout settled**, and `hidden`
  lost to CSS `display: flex/grid`. Both fixed (ResizeObserver; `[hidden]` override).

## UI structure (rebuilt)

Three columns: **job** (content, page, font; margins/placement collapsed), **stage**
(preview + scrubbable simulation), **machine** (bed diagram, jog, pen, job transport,
log). Everything describing *the machine rather than the job* — work area, orientation,
pen servo, speeds — lives behind the header gear in a Settings modal, because it's
set-once. All settings persist to `localStorage` (`penplotter.console.v1`), so a
refresh keeps the calibration. "Reset to defaults" in Settings restores only the
machine settings, not the current job.

## Working style notes

Daniel is hands-on with the hardware but not deep in the code, and got visibly
frustrated when explanations ran long or jargon-heavy ("ur too confusinfg", "are you
dumb"). What worked: **short numbered steps, one action at a time, plain words, no
G-code jargon**, and doing the thinking rather than handing him options. He pushed back
correctly on a slow manual process (jog-one-click-and-report) and asked for a proper
calibration mode — the corner-marking tool exists because of that. When something can't
be done (hand-drag tracking), say so plainly rather than building a lookalike.

Also: **no AI attribution in commits or repo files** — he is sole author.

## Reference material (outside this repo)

- `C:\Users\dabbe\Desktop\pen ploter business\RUN PEN PLOTTER\Gcode\*.gcode` — real
  calibrated output from the old workflow. `bounndrycreation1_0001..0009` is the
  original by-hand work-area search; the last three agree on 194.5 × 294.5mm.
- `C:\Users\dabbe\Desktop\pen ploter business\4xiDraw & km laser\` — the original
  Inkscape extension source the font parsing and gcode dialect came from.
- `Creativity Buzz Writing Machine Software Download.pdf` — the reseller's setup PDF
  (software only, no hardware detail).
