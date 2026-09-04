# Pen Plotter Manager

Fully-automated pipeline for the writing machine: give it text, a document,
or an existing SVG, and it formats it to the page and writes it — no
Inkscape, no Universal G-code Sender.

## Console (start here)

One local web app centralizes everything — bed/page setup with a live
diagram, text/document/SVG input, preview, simulate-with-animation, jog
controls, and connect+run. It replaces Inkscape + the 4xiDraw extension +
UGS + the raw CLI commands below with a single interface.

```bash
.venv\Scripts\python -m plotter.server
```

Open **http://127.0.0.1:8765**. Port dropdown always includes "Simulator
(no hardware)" — pick that to do everything (preview, simulate, jog, run)
against the GRBL simulator with zero hardware attached. When the machine's
plugged in tomorrow, refresh the page so it appears in the port dropdown,
pick it instead, and every button does the exact same thing against the
real board — nothing else changes.

The **Bed & page** panel's bed size defaults to 195×295mm, taken from the
`bounndrycreation1_*.gcode` boundary tests in the old real gcode archive
(the machine's actual printable area, already found by hand before this
project existed). If the machine's changed since, jog to one corner and
click **Mark corner A**, jog to the opposite corner and click **Mark corner
B** — width/height fill in automatically from the two marked points.

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

## First-time calibration

The very first run on a fresh setup, do a `--dry-run` and a real run on
scrap paper before anything customer-facing:

- **Mirrored / upside-down text** → your machine's Y axis or origin corner
  differs from the default assumption. Toggle `invert_y` in
  `plotter/config.py`'s `MachineConfig`, or pass `--travel-feed`/servo flags
  as needed once you're editing that file.
- **Pen presses too hard / doesn't touch paper** → tune `--servo-down` /
  `--servo-up` (defaults 50/10, taken from your existing calibrated gcode).
- **Too fast/slow, skipping steps** → tune `--travel-feed` / `--draw-feed`
  (defaults 10000/2500 mm/min, also taken from your existing files).

## Project layout

```
plotter/
  fonts.py      parse SVG stroke fonts into glyph outlines
  layout.py     word-wrap + paginate text into placed strokes
  extract.py    pull text out of .pdf / .docx / .txt
  svgin.py      load an existing SVG's strokes, fit to page
  gcode.py      strokes -> gcode in this machine's dialect
  stream.py     GRBL serial streaming + jog/home/zero (replaces UGS)
  simulator.py  fake GRBL board for testing/rehearsal without hardware
  jobs.py       shared pipeline (input -> pages -> gcode -> sim), used by both cli.py and server.py
  preview.py    strokes -> SVG for visual QA
  cli.py        the `plot` commands (fonts/ports/preview/gcode/simulate/send/run)
  server.py     the web console (FastAPI + WebSocket live session)
web/            the console's frontend (index.html + app.js), served by server.py
fonts/          the Hershey/EMS SVG stroke fonts (copied from your existing setup)
jobs/           generated previews, gcode, and uploads land here (gitignored)
```

## Next step: hooking up to HandScript

This runs standalone for now. Once it's dialed in on real jobs, the natural
next step is wiring it into the HandScript admin panel (`penplotter app/`)
as an "IN_PRODUCTION" action — pull the order's uploaded file and page/paper
settings, call this same pipeline, and log the result on the order timeline.
Not built yet; ask when you're ready for that piece.
