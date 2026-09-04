# Pen Plotter Console — Handoff

Paste this whole file into a new chat to continue exactly where this session left off.

## Who/what this is

Daniel runs a business (**HandScript**, in `../pen ploter business/penplotter app` — a
separate Next.js/Prisma SaaS where customers pay to have documents "handwritten" by a
pen plotter) and physically owns a DIY GRBL pen plotter (Arduino Uno + GRBL firmware,
CoreXY-style gantry, servo-controlled pen lift — the "4xiDraw" style build, marketed as
a "Writing Machine" by the reseller Creativity Buzz). This project (`penplotter manager/`)
is a **standalone local tool** that fully automates the machine's workflow, replacing
the manual chain of Inkscape (Hershey Text extension + 4xiDraw gcode export) → Universal
G-code Sender. It is not yet wired into the HandScript business app — that's a known,
explicitly deferred future step (see bottom of `README.md`).

## Current physical state — READ THIS FIRST

**The machine is physically connected right now** to the PC via USB, on **COM8**
(confirmed via `pyserial`'s port list). Earlier in this session we connected the web
console to it for real and GRBL responded to a live `$$` settings query
(`$0=10 (step pulse, usec)` was the first line seen) — so the serial link, driver, and
GRBL firmware are all confirmed working end-to-end against real hardware for the first
time this session.

**We were mid-way through a first-time physical calibration checklist when this chat
got interrupted**, about to hand off to the user for the physically-observed steps
(nothing past "connect" has touched real hardware yet — no jog, no pen test, no real
print). Next chat should resume exactly here. The full planned checklist (not yet
executed):

1. ~~Connect to COM8, confirm GRBL responds~~ ✅ done
2. **Tiny jog test** — ✅ X+ confirmed working, moves ~1mm, no grinding. Y+ direction
   and "which edge of paper is the top" still need confirming (see below).
3. ~~**Pen servo test**~~ ✅ done and fixed — pen up/down was backwards (clicking
   "Pen up" lowered the pen). Root cause: the servo's rest state (M05, ignores the S
   value) turned out to be physically "down" on this build, not "up" as the original
   reverse-engineered files assumed. Fixed with a new `swap_pen` config flag
   (`plotter/config.py` — `MachineConfig.pen_up_cmd_value`/`pen_down_cmd_value`
   pick which literal command+value pair means "up" vs "down") and a "swap pen
   up/down" checkbox in the Machine panel. **User has it ticked and confirmed
   correct.** This flag flows into real job gcode too (`gcode.py`), not just the
   jog buttons, so it's safe for real prints.
4. **Get orientation right (X and Y direction, paper top edge)** — in progress. Two
   new checkboxes exist for this now: **flip X** and **flip Y** in the Machine panel
   (`plotter/config.py` — `MachineConfig.invert_x`/`invert_y`, applied in `gcode.py`'s
   `to_machine()`). Need from the user, in plain terms (not gcode jargon, they asked
   for this to stay simple):
   - Does the **right arrow** move the pen left or right?
   - Does the **up arrow** move the pen away from them or toward them?
   - When paper is loaded, which edge is the top of the writing — the far edge or the
     near edge?
   Then just tick the boxes for them based on the answers — don't make them reason
   about "invert" semantics.
5. ~~**Find the real bed size**~~ ✅ done, without touching hardware — found in the
   user's own old real gcode archive instead of remeasuring. `RUN PEN PLOTTER\Gcode\
   bounndrycreation1_0001.gcode` through `_0009.gcode` (outside this project, at
   `C:\Users\dabbe\Desktop\pen ploter business\RUN PEN PLOTTER\Gcode\`) is a real
   historical series where the user iteratively pushed a test rectangle bigger
   (170mm wide → 199 [too far] → 184 → settled at **194.5 × 294.5mm** for the last
   three attempts in a row) — a real physical calibration already done on this exact
   machine before this project existed. Console default is now **195×295mm**
   (matches the already-existing `custom-card` page size). Also confirms the
   `swap_pen` fix from this session was correct: those old files send `M03 S50`
   before travel (pen up) and `M05 S10` before drawing (pen down) — the same mapping
   the user just confirmed works live. No limit switches or soft limits exist on this
   machine (`$20=0`, `$21=0`, confirmed from real GRBL settings this session), so
   nothing stops an overtravel but the user's own eyes — worth keeping in mind if
   this 195×295mm number is ever in doubt.
   A proper **re-calibration tool now exists** in the console too, in case the
   machine changes: **Mark corner A** / **Mark corner B** buttons in the Bed & page
   panel (`web/index.html`, `web/app.js`) — jog to one corner, click Mark corner A,
   jog to the *diagonally opposite* corner, click Mark corner B, width/height fill
   in automatically from the two marked live positions. No manual arithmetic, no
   relaying numbers through chat. (First attempt at this was asking the user to
   jog one click at a time and report the reading verbally — user rightly called
   this out as slow and asked for an actual calibration mode instead.)
   **The user then ran this tool for real and got 195 × 300mm**, independently
   agreeing with the historical 194.5 × 294.5mm from the old gcode. That is now the
   default bed size. Since there are no limit switches, keep real pages a few mm
   inside it; and note a mis-measurement is possible in one specific way — if the
   belt slips at an end stop while the steppers keep turning, the software keeps
   counting mm that never happened and the bed reads larger than reality.
6. **Tiny real test print** — scrap paper on the bed, zero at a safe start position,
   type a couple of words, click **Simulate** first (offline, zero risk), then **Run
   on machine**. Watch closely, ready to hit **Pause**/**Cancel job**.
7. Only after all of the above check out clean → real jobs are safe to run.

**Also fixed this session, not on the original checklist:** the live console had two
real bugs — Pen up/down buttons ignored the Servo up/down number fields entirely
(always sent the hardcoded defaults), and there was no way to send GRBL's `$X`
unlock from the UI at all. Both fixed (`server.py`, `web/app.js`) and verified
against the simulator before touching hardware. The connect banner is also now fully
parsed and logged (soft/hard limits, homing, steps/mm, max travel) instead of just
its first line — this session confirmed `$100`/`$101` (steps/mm) = 81.800 on both
axes, deliberately calibrated and consistent, a good sign.

**Orientation — RESOLVED by real test print.** `invert_x=True`, `invert_y=False`
(and `swap_pen=True`) are now the defaults in `config.py`, `server.py`'s `MachineIn`,
`web/index.html` and `web/app.js`. Physical facts behind them, confirmed live: the
UI's right arrow moves the carriage physically **left**, the up arrow moves it
**toward the operator**, and with these settings a test print came out the right way
up. Machine zero (`Zero here`) = the **top-right corner of the page**, and the job
runs from there in +X (physically left) and +Y (physically toward the operator), so
the paper's far-right corner is where zero belongs. The machine writes relative to
zero, never relative to the paper — moving the paper after zeroing misplaces the
text, which caused a "it's not in the corner" scare before it was understood.

**"Could not connect to COM8: Access is denied" — FIXED, root-caused.** Windows
allows only one open handle per COM port, but every browser tab got its own
`Session` with its own `GrblStreamer`, and `connect()` in `app.js` replaced
`state.ws` without closing the old socket — so repeated Connect clicks and stale
tabs each stranded an open handle, locking the user out of their own machine (hit
twice this session; four live WS sessions were found open at once the second time).
Now `_take_port_ownership()`/`_release_port_ownership()` in `server.py` make the
newest connection the sole owner and close the previous holder (surfaced to the UI
as `tookOver`), the connect handler drops any stale streamer the session already
had, the `finally` block releases ownership, and `connect()` closes the previous
socket first. Verified against handover, same-session reconnect, release, and a
non-owner attempting to release.

**Homing & position tracking — settled, don't re-litigate.** `$21=0`/`$22=0`: this
machine has no limit switches, so `$H` can only ever error. The Home button is now
auto-disabled when the connect banner reports `$22 != 1` (and auto-enabled if it ever
reports `$22=1`), with the reason logged. A **Go to zero** button replaces it for the
practical use case (`GrblStreamer.go_to_zero()` — `G90` then `G1 X0 Y0 F3000`; it is
explicitly *not* homing, just a return to the last `Zero here`).

The user also asked for the position readout to track the gantry being **pushed by
hand**. This is not possible: the machine is open-loop with no encoders, so neither
GRBL nor this software has any way to sense uncommanded movement — the position is a
count of commanded moves, not a measurement. Told the user plainly rather than
building something that silently lies. Workaround is `Zero here` after any manual
move. The real fix is hardware: limit switches on each axis (~£5, would also make
`$H` genuinely work with `$21=1`/`$22=1`) or encoders. **If asked about this again,
don't try to implement software tracking — it cannot work.**

**Browser cache gotcha — fixed.** `web/app.js` was being served from browser cache
after edits, so a working fix looked like it silently did nothing (cost real
debugging time against live hardware). `index()` in `server.py` now stamps
`/static/app.js?v=<mtime>` into the HTML, so a normal refresh always picks up
changes. If a frontend change ever *seems* not to apply, verify what's actually
served (`Invoke-WebRequest http://127.0.0.1:8765/static/app.js`) before assuming the
logic is wrong.

**Safety note:** real hardware actions (jog, run) are irreversible/physically
consequential in a way Preview/Simulate are not. Don't auto-pilot through the
checklist above — confirm what actually happened on the machine at each step.

## How to get back to the console

```bash
cd "C:\Users\dabbe\Desktop\Daniel all projects\penplotter manager"
# check if the server's already running (it may still be, from this session):
netstat -ano | grep 8765 | grep LISTENING
# if nothing listening, start it:
.venv\Scripts\python -m plotter.server
```

Open **http://127.0.0.1:8765**. Port dropdown lists `SIMULATOR` and (when plugged in)
`COM8`. Pick `COM8`, click Connect, proceed with the checklist above.

If the browser tab from the old session is still open, it may already show
"connected: COM8" in the status pill — check before reconnecting (reconnecting resets
the Arduino again, which is safe but unnecessary if already connected).

## What's built (full picture)

```
penplotter manager/
  plotter/
    fonts.py      parse SVG stroke fonts (Hershey/EMS) into glyph outlines
    layout.py     word-wrap + paginate text into placed strokes (page-space mm, y-down)
    extract.py    pull text out of .pdf / .docx / .txt
    svgin.py      load an existing SVG's strokes, fit to page
    gcode.py      strokes -> gcode in this machine's exact dialect (see below)
    stream.py     GRBL serial streaming: connect/stream/pause/resume/cancel/jog/home/
                  zero/pen up/down. Accepts an injectable `transport` so tests/sim can
                  swap in a fake serial device. Tracks live position/pen state itself
                  (parses its own sent lines) so the UI has something to show.
    simulator.py  FakeGrblPort: a fake GRBL controller (pyserial-compatible interface)
                  for testing without hardware. Two modes: instant (bulk stats/CLI
                  simulate) and speed_factor=15x-real-time (used by the live web
                  console's "Run" against Simulator, so pause/resume/cancel have an
                  actual window and the live position visibly moves).
    jobs.py       shared pipeline (input -> pages -> gcode -> sim/run), used by both
                  cli.py and server.py so they never drift apart.
    preview.py    strokes -> static SVG for quick visual QA
    cli.py        `plot` command group: fonts/ports/preview/gcode/simulate/send/run
    server.py     FastAPI app: REST (/api/fonts, /api/ports, /api/preview,
                  /api/simulate, /api/upload) + one WebSocket (/ws/session) for the
                  live console (connect/jog/home/zero/pen/run/pause/resume/cancel).
  web/
    index.html    the console's single-page UI (bed/page setup with live diagram,
                   content input tabs, style/machine settings, preview/simulate stage
                   with animated playback, jog pad, log panel)
    app.js        all frontend logic/state for the above
  fonts/          Hershey/EMS SVG stroke fonts, copied from the user's existing
                  `4xiDraw & km laser/svg_fonts/` folder
  jobs/           generated previews/gcode/uploads land here (gitignored, currently
                  should be empty/cleaned up)
  requirements.txt, README.md, .gitignore
```

**G-code dialect** (matters if touching `gcode.py`/`config.py`): `G90`/`G21` absolute
mm, `M03 S<val>`/`M05 S<val>` for servo pen down/up, `G4 P<sec>` dwell after each servo
move, `F10000` travel feed / `F2500` draw feed. This was reverse-engineered from the
user's real, already-calibrated `.gcode` files (in
`../pen ploter business/RUN PEN PLOTTER/Gcode/*.gcode`) and the `4xiDraw_servo`
Inkscape extension's defaults — **not guessed**. Fonts are real SVG-font files (mostly
straight-line stroke data), not filled outline fonts — the machine physically can only
trace continuous strokes, confirmed both by the extension code and by the setup video's
own narration ("the writing machine only understands this font").

## What's verified vs still assumed

Verified for real in this session (clicked/scripted, not just claimed):
- Font parsing/rendering, word-wrap + pagination (visually confirmed via screenshot)
- PDF/DOCX/TXT extraction, SVG pass-through (via the actual UI upload flow)
- G-code output matches the real machine's command dialect
- Full GRBL streaming protocol (connect handshake, line-by-line ack, pause, resume,
  cancel) — proven via a deterministic WebSocket test script against the simulator,
  including that a cancelled job is now correctly distinguished from a completed one
  (`cancelled: true/false` in the `jobComplete`/`pageComplete` WS messages — this was a
  real bug found and fixed this session)
- Jog, Home, Zero, Pen Up/Down — via the simulator
- Bed/page diagram math (origin, margins, center/corner quick actions)
- **NEW this session, against real hardware**: serial connection + GRBL handshake on
  COM8 actually works

Still open / just placeholders until physically confirmed (see checklist above):
- Real bed size (`bedW`/`bedH` in the console default to a guessed 200×280mm)
- `invert_y` (Y-axis direction / origin corner) — a guess, needs one real print to
  confirm or flip
- Servo up/down values (10/50) — inherited from old files, not verified against this
  specific machine's servo/pen mount
- Everything about physical jog/run has only been tested against the simulator, never
  real hardware, until the checklist above is completed

## Deferred / not built

Wiring this into the HandScript admin app (`../pen ploter business/penplotter app/`) so
an approved order auto-feeds this pipeline — explicitly deferred, mentioned in
`README.md`'s last section. Don't start this unless asked.

## Reference source material (for context, not needed day-to-day)

- `Creativity Buzz Writing Machine Software Download.pdf` (in Documents) — the
  original software-setup PDF from the machine's reseller. Software-only, no hardware
  assembly info.
- A YouTube setup video (Creativity Buzz channel) — also software-only.
- `../pen ploter business/4xiDraw & km laser/` — the original Inkscape extension
  source (Python) this project's font parsing and gcode dialect were derived from,
  including `svg_fonts/` (copied into this project's `fonts/`).
- `../pen ploter business/RUN PEN PLOTTER/Gcode/*.gcode` — real calibrated gcode
  output samples used to reverse-engineer the exact command dialect.
