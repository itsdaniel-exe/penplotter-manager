# Pen Plotter Console — Handoff

Paste this whole file into a new chat to pick up with full context.

## Who/what this is

This drives a DIY GRBL pen plotter: Arduino Uno running GRBL, CoreXY-style gantry,
servo pen lift — a "4xiDraw" style build, sold in some places as a "Writing Machine".

It is a **standalone local tool** that automates the machine's whole workflow,
replacing the manual chain of Inkscape (Hershey Text + 4xiDraw gcode export) →
Universal G-code Sender.

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
- **Console froze after "Zero here"** — `_send_and_wait()` looped forever if the board
  never sent `ok`, so the button hung with no message. Reloading and reconnecting then
  closed the stuck handle mid-read, crashing that session (pyserial's `hEvent`
  AttributeError). Replies now time out after 30s with a readable error, a closed port
  says so, Zero/Pen up/Pen down errors reach the log instead of killing the socket, and
  closed tabs no longer leak a server thread. *Why* the board didn't answer that `G92`
  is still unknown.
- **Cancel didn't stop, Disconnect looked dead.** Cancel while paused spun forever in the
  pause loop (and blocked server shutdown). Cancel otherwise sent `!` and walked away,
  leaving GRBL frozen in a feed hold with moves queued and the pen down. Disconnect
  worked server-side, but the page only logged it — button and status never changed.
  Cancel now stops sending, releases any hold, waits on `G4 P0` (acked only once the
  buffer has drained) and lifts the pen. Disconnect, reconnect and closing the tab
  do the same first. **Don't switch Cancel to a soft reset (`0x18`)**: GRBL's
  `gc_init()` zeroes the G92 offset on reset, so it would wipe "Zero here". Trade-off:
  Cancel isn't an e-stop — the machine finishes the few short moves it already accepted.
  Verified on the simulator only; not yet on the real machine.
- **Pen up/down was tracked backwards on this machine.** `stream.py` and the simulator
  both hardcoded `M03 = pen down`, but with `swap_pen` the job gcode LIFTS with M03. So
  the live pen badge was inverted for a whole run, the simulator swapped its drawn and
  travelled distances (168mm of ink reported as 434mm), and the Simulate animation traced
  the travel moves instead of the letters. Both now take the mapping from `MachineConfig`
  (`set_pen_mapping`), and the streamer reports `pen ?` until something has actually
  driven the servo, because nothing reads it back.
- **A page that failed mid-stream left the pen on the paper** and the console stuck on
  "Running" with live Pause/Cancel. The error path now lifts the pen and ends the job.
- **Jog / Pen / Zero / Go to zero stayed live during a run**, so a click mid-page wrote
  into the same serial port the job was streaming to — a `G92` there re-zeros the rest of
  the page. Blocked server-side and disabled in the UI while a job runs.
- **A multi-page job drew every page on the same sheet.** It now stops after each page
  and waits for the operator to confirm a fresh one.
- **Nothing bounds-checked a real run** — only the simulator did. Jobs are now checked
  against the measured work area before the pen moves, with an explicit override.
- **A second tab taking the port killed a running job's handle mid-stroke.** The takeover
  now cancels the job, waits for the pen to lift, and tells the losing tab (which used to
  sit there showing "connected" with every button silently doing nothing).
- **Pausing for more than 30s failed the job** with a bogus "check the USB cable": a feed
  hold stops GRBL acking, and that counted against the no-reply timeout. A pause no longer
  counts. A pause left set by an earlier job also used to hold the next one silently.
- **"Go to zero" travelled with the pen down**, ruling a line across the sheet.
- **Word's punctuation vanished from the page.** A stroke font has no curly apostrophe and
  `Font.glyph()` falls back to the space glyph, so "don't" plotted as "don t". Typographic
  characters are substituted; anything still undrawable is reported after a Preview.
- **A word too long for the line ran off the sheet** (and off the rails) instead of being
  broken.
- **SVG import**: arc commands were never tokenised, so an `A` dropped its letter and fed
  seven numbers to the previous command; a `Z` swallowed the next subpath's moveto (same
  bug in the glyph parser); `transform` attributes were ignored entirely; `<defs>` and
  hidden elements were plotted; `width="100%"` failed the whole upload. All fixed, and
  `<text>` is now reported rather than silently dropped.
- **docPath/svgPath were unvalidated server-side paths**, so a crafted request could have
  the console read and plot any file the account could open. Confined to `jobs/uploads`,
  which is also pruned now.
- **Bad input surfaced as "Internal Server Error"** (a zero font size raised
  ZeroDivisionError). Preview/Simulate now return the actual reason.
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

Write for someone standing at the machine, not at a terminal: **short numbered steps,
one action at a time, plain words, no G-code jargon**. Prefer doing the thinking to
handing over a list of options.

The corner-marking calibration exists because reporting jog positions by hand was too
slow to be useful — that is the standard to hold new features to. When something is not
possible (tracking the gantry being pushed by hand, for instance), say so plainly
instead of building something that looks like it works.

## Reference material (kept outside this repo)

- The original `*.gcode` output from the old Inkscape + UGS workflow. The
  `bounndrycreation1_0001..0009` files are the original by-hand work-area search; the
  last three agree on 194.5 × 294.5mm.
- The 4xiDraw Inkscape extension source, which the font parsing and gcode dialect came
  from.
- The reseller's setup PDF (software only, no hardware detail).
