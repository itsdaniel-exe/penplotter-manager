# Pen Plotter Manager

Fully-automated pipeline for the writing machine: give it text, a document,
or an existing SVG, and it formats it to the page and writes it — no
Inkscape, no Universal G-code Sender.

## Console (start here)

One local web app centralizes everything. It replaces Inkscape + the 4xiDraw
extension + UGS + the raw CLI commands below with a single interface.

```bash
.venv\Scripts\python -m plotter.server
```

Open **http://127.0.0.1:8765**. The layout is three columns:

- **Left — the job.** Content (typed text, a document, or an SVG), page size,
  font and size. Margins, alignment and page placement live in a collapsible
  *Margins & placement* group so they stay out of the way until wanted.
- **Middle — the stage.** *Preview* draws the formatted page; *Simulate*
  replays it through a fake GRBL board with a scrubbable animation and
  time/distance stats. Neither touches hardware.
- **Right — the machine.** Live bed diagram, jog pad, pen up/down, zero, and
  the pause/resume/cancel controls, plus the log.

Anything that describes *this particular machine* rather than the current job
lives behind the **gear icon** in the header — work area, orientation, pen
servo values, and speeds. Those were set by calibration and shouldn't need
touching day to day. Every setting is saved in the browser, so a refresh keeps
your calibration.

The port dropdown always includes "Simulator (no hardware)" — pick that to do
everything (preview, simulate, jog, run) with nothing attached. Pick the real
port instead and every button does the same thing against the real board.

### Work area

Defaults to 195×300mm, measured on the real machine by jogging to opposite
corners and marking them. That agrees with the `bounndrycreation1_*.gcode`
boundary tests in the old gcode archive (194.5×294.5mm), found by hand before
this project existed — two independent measurements, so it's trustworthy.

To re-measure (if the machine's rebuilt or re-tensioned): open Settings, jog
to one corner, click **Mark corner A**, jog to the diagonally opposite corner,
click **Mark corner B** — width/height fill in automatically. Stop jogging at
the first sign of resistance: there are no limit switches, and if the belt
slips while the motor keeps turning, the software counts millimetres that
never happened and you get a work area larger than reality.

### Homing

There isn't any — this machine reports `$22=0` and has no limit switches, so
GRBL's `$H` can only ever error. The Home button disables itself when the
firmware says homing is unavailable. Use **Zero here** to set a reference and
**Go to zero** to return to it.

For the same reason the machine cannot know it has been moved by hand: the
steppers are open-loop with no encoders, so the position readout counts
commanded moves, not measured ones. After pushing the gantry by hand, click
**Zero here** to re-establish the reference.

The CLI commands below still work standalone and are what the console
calls under the hood.

Replaces the manual workflow from `Creativity Buzz Writing Machine Software
Download.pdf`:

- **Old**: Inkscape (Hershey Text extension, page setup) → 4xiDraw gcode
  extension → Universal G-code Sender → machine.
- **New**: one command. Text/PDF/DOCX is word-wrapped and paginated to your
  page size using the same single-stroke fonts the Hershey Text extension
  used, converted straight to G-code in this machine's exact dialect, and
  streamed over serial directly to the GRBL board.

G-code dialect (servo commands, feed rates) was reverse-engineered from the
real files in `RUN PEN PLOTTER/Gcode/*.gcode` and the `4xiDraw_servo`
Inkscape extension's defaults, so it matches your machine's calibration out
of the box.

## Setup

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Fonts are already copied into `fonts/` from your `4xiDraw & km laser`
folder — no extra setup needed.

## Usage

Always preview first — renders an SVG you can open in a browser, no
machine or serial port needed:

```bash
.venv\Scripts\python -m plotter.cli preview --text "Dear Alex, ..." --page a4 --font HersheyScriptMed
```

```bash
.venv\Scripts\python -m plotter.cli preview --file order_letter.pdf --page custom-card
```

Check available fonts and serial ports:

```bash
.venv\Scripts\python -m plotter.cli fonts
.venv\Scripts\python -m plotter.cli ports
```

Run a job end-to-end (formats, generates gcode, streams to the machine,
page by page — pauses between pages if the job spans more than one sheet):

```bash
.venv\Scripts\python -m plotter.cli run --text "Dear Alex, ..." --page a4 --font HersheyScriptMed --port COM3
.venv\Scripts\python -m plotter.cli run --file order_letter.pdf --page custom-card --port COM3
.venv\Scripts\python -m plotter.cli run --svg my_design.svg --page a4 --port COM3
```

Add `--dry-run` to any `run` to generate the SVG previews without touching
the serial port.

Generate `.gcode` files only (e.g. to double check, or to run later with
`send`):

```bash
.venv\Scripts\python -m plotter.cli gcode --text "..." --page a4
.venv\Scripts\python -m plotter.cli send jobs\job_01.gcode --port COM3
```

Run a job through a simulated GRBL board — no hardware needed, exercises
the real streaming code (connect/stream/pause/resume/cancel), reports
estimated plot time and travel distance, and flags anything that would
exceed a bed size you give it:

```bash
.venv\Scripts\python -m plotter.cli simulate --text "..." --page a4 --bed-width-mm 200 --bed-height-mm 280
```

## Page sizes

`a4`, `a5`, `a6`, `letter`, `custom-card` (195×295mm, matches your existing
`wriiting test testtest.svg`). Or override with `--width-mm`/`--height-mm`.

## Inputs

- `--text "..."` — typed/pasted text, wrapped and paginated automatically.
- `--file document.pdf` / `.docx` / `.txt` — extracts the text, then same
  pipeline as `--text`.
- `--svg design.svg` — an existing hand-made SVG (paths/lines/polylines/
  rects), scaled and centered to fit the page as-is, no text layout.

## Calibration (done — these are the real values)

Established against the physical machine, not guessed. The defaults in
`plotter/config.py` already encode all of it, and `tests/test_calibration.py`
guards it.

| What | Value | How it was found |
| --- | --- | --- |
| Work area | 195 × 300mm | Marked opposite corners; agrees with the old `bounndrycreation1_*.gcode` boundary tests (194.5 × 294.5) |
| `invert_x` | `True` | Test print — X+ runs physically **left** |
| `invert_y` | `False` | Test print — Y+ runs **toward the operator** |
| `swap_pen` | `True` | `M03 S50` lifts, `M05 S10` lowers on this build |
| Steps/mm | 81.800 both axes | GRBL `$100`/`$101`, deliberately calibrated already |
| Servo up / down | 10 / 50 | Inherited from the real gcode archive |
| Feeds | 10000 / 2500 mm/min | Same source |

Two things worth knowing, because they look like bugs and aren't:

- **`swap_pen`** is needed because this build's servo rest state (`M05`, which
  ignores its `S` value) is physically *down*, the reverse of what the
  original reverse-engineered files implied. The real historical gcode
  confirms it: those files send `M03` before travel and `M05` before drawing.
- **`$130`/`$131`** read GRBL's stock 200.000 default and are unenforced
  (`$20=0`, soft limits off). They say nothing about the real work area —
  ignore them.

If you re-print and it comes out wrong, the two checkboxes under
Settings → Orientation are the fix; the rest of the pipeline is verified.

Run the checks with:

```bash
.venv\Scripts\python -m tests.test_calibration
```

## Project layout

```
plotter/
  fonts.py      parse SVG stroke fonts into glyph outlines
  layout.py     word-wrap + paginate text into placed strokes
  extract.py    pull text out of .pdf / .docx / .txt
  svgin.py      load an existing SVG's strokes, fit to page
  gcode.py      strokes -> gcode in this machine's dialect (applies invert_x/y, swap_pen)
  stream.py     GRBL serial streaming + jog/zero/go-to-zero/pen (replaces UGS)
  simulator.py  fake GRBL board for testing/rehearsal without hardware
  jobs.py       shared pipeline (input -> pages -> gcode -> sim), used by both cli.py and server.py
  preview.py    strokes -> SVG for visual QA
  cli.py        the `plot` commands (fonts/ports/preview/gcode/simulate/send/run)
  server.py     the web console (FastAPI + WebSocket live session, serial port ownership)
web/            the console's frontend (index.html + app.js), served by server.py
tests/          regression tests for the calibrated machine facts
fonts/          the Hershey/EMS SVG stroke fonts (copied from your existing setup)
jobs/           generated previews, gcode, and uploads land here (gitignored)
```

## Next step: hooking up to HandScript

This runs standalone for now. Once it's dialed in on real jobs, the natural
next step is wiring it into the HandScript admin panel (`penplotter app/`)
as an "IN_PRODUCTION" action — pull the order's uploaded file and page/paper
settings, call this same pipeline, and log the result on the order timeline.
Not built yet; ask when you're ready for that piece.
