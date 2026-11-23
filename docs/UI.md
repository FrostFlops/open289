English | [简体中文](UI.zh-CN.md)

# Fluke 289 front-panel map

This document maps the front panel of the Fluke 289 as seen through `open289 press` (and the MCP `press` tool): what each key does on each dial position, how to reach a function, how to get back to the measurement screen, and what the protocol cannot show. It is written for AI agents and developers who drive the meter without seeing its screen.

Everything here was observed on a Fluke 289, firmware V1.71, by pressing keys over the IR3000 FC pass-through and checking the LCD and a `QDDB` display query after each press. Evidence level: VERIFIED (observed on hardware) unless a statement says otherwise. The 287 was not tested. Key names, `--held` and the result format are described in the [README](../README.md) (section "Front-panel keys"); the wire protocol is in [PROTOCOL.md](PROTOCOL.md).

Notation: F1 to F4 are the soft keys from left to right; a dash means no label (the key does nothing). "Primary / secondary" names the QDDB `function` and `secondary_function` the choice produces.

## 1. Driving the meter with `press`

Keys: `F1 F2 F3 F4 UP DOWN LEFT RIGHT HOLD MINMAX RANGE INFO BACKLIGHT ONOFF`. `--held` sends a long press for every key. After each key `press` returns the display state (a QDDB snapshot, or a text when queries are blocked, see section 2) together with what changed, a note and warnings (see "What `press` reports" below).

`ONOFF` was not pressed during this walk. `press` refuses it unless `--allow-off` is given (`allow_off` in the MCP tool). The meter then shows a power-off confirmation with F1 OK and F4 Cancel; F1 switches the meter off and the adapter goes to sleep with it, which cannot be undone from the PC (see the README). An ONOFF followed by any F1 therefore switches the meter off.

Golden rules:

1. **Track the screen yourself.** Soft keys change meaning with the screen and, inside a menu, with the selected item, but QDDB does not show whether a menu, panel, Setup or help is open, where any cursor is, or which soft-key labels are drawn (section 2). The state confirms only effects that change the function, secondary function, autorange, range, unit, modes, the MIN MAX start time, the dBm reference or the temperature offset, and the blocked state. Press one key at a time, predict the screen from this document and use the state wherever the effect is visible; for cursor moves and for opening and closing overlays you depend on your own key history (`press` helps with `changed`, `note` and `warnings`, but it only infers; see "What `press` reports"). When that history is uncertain, ask someone at the meter to read the screen before the next soft key. A blind sequence that goes wrong lands in another menu.
2. **Menus remember their cursor.** The Save panel, the Menu and the Setup panel reopen where they were left, so the same soft key can open a different item next time, and the cursor cannot be read over the protocol. The Setup cursor was seen to stay on its item across a dial change, and also to be back on Reset after one. Never press F1, F2 or F3 in Setup unless someone at the meter has read the soft-key labels: F1 names the selected item (on Reset F1 is a dash and F2 and F3 are the reset choices, section 3.7).
3. **Refusals are silent.** A key the current mode does not accept is acknowledged (`OK`) and nothing changes; `press` shows this only as `changed: []` with a `note` or a warning. A refused menu choice leaves the menu open. A following F4 (meant as Stop or Close) then only closes the menu, a second F4 opens Setup, and a soft key after that acts in Setup: F1 opens the selected Setup item (once Instrument, where the next F1 enters edit mode), and with the cursor on Reset (where it was found after a dial change) F2 or F3 opens a factory-reset confirmation whose F1 is OK. After any soft key whose effect the state did not confirm, do not press F1, F2 or F3 until you know which screen is shown.
4. **UP and DOWN change the LCD contrast on the measurement screen and in the Measurement saved-item viewer** (not recorded for the other viewers; in the recording Trend view they zoom). Each DOWN lightens the digits, each UP darkens them one step. Never press them there except in matched pairs. In menus, lists and editors they move the cursor or change the value; on the Meter Info page they scroll; in the INFO help DOWN scrolls (UP was not recorded there).
5. **Editors apply at once in some places and not in others.** The Setup editors use OK and Cancel (Cancel restores the old value). The dBm Reference preset list and the Temperature Offset editor apply every UP and DOWN immediately and only offer Close.
6. **A held press** acted like a short press for F2, F4, HOLD, MINMAX and BACKLIGHT; HOLD held does not start AutoHOLD. Exceptions: `RANGE` held returns to autorange, and in the Trend view of a recording LEFT or RIGHT held moves the cursor two samples instead of one. INFO held opened the help at the scroll position it was left at. Held F1, F3, UP, DOWN and ONOFF, and held LEFT or RIGHT outside the Trend view, were not tried. LEFT and RIGHT do nothing on the measurement screen.
7. **BACKLIGHT** cycles off, bright, dim, off. Remote key presses did not seem to restart the meter's auto backlight timeout (5 minutes in the setup that was tested).
8. **Keys that redraw a heavy view answered `NO_REPLY`**: F3 Trend from the recording summary, and UP or DOWN (zoom) in the Trend view. In every case that was checked the key had taken effect (the redraw delays the meter's answer); in one earlier case the effect was not checked. `press` reports the missing answer as a `METER_ERROR` and does not send the remaining keys. These views block queries before and after the key, so the state cannot show whether it acted. Do not repeat the key blindly (a second F3 goes back to the summary, a second UP zooms in again): assume it took effect, and ask someone at the meter if it matters.

### What `press` reports

Before the first key `press` reads the display state once (QDDB) as a baseline, and after each key (after `--settle`, default 1 s) it reads it again. Each press entry carries these additive fields (the existing fields are unchanged):

- **`changed`**: the display-state fields that differ from the state before the key, out of `function`, `secondary_function`, `autorange`, `range`, `unit`, `modes` and `minmax_start`, plus `db_ref` and `temp_offset` when both states carry those readings. `[]` means nothing visible changed. `["view"]` means a blocking page (a result page or a saved-item viewer) opened or closed. `null` means it cannot be told: both states blocked, or one of them unknown (an error, a closed link, or the key after ONOFF, which has no baseline); it is always `null` on ONOFF, power-off and `LINK_CLOSED` entries. In autorange a range move caused by the input is not counted: `range` is ignored only when both states are in autorange.
- **`note`** (only when needed): a hint for the cases the state cannot settle. A soft key with no visible change (a menu or panel may have opened, moved or closed, or a choice was refused; look before the next soft key, because a blind F4 can open Setup). A soft key that switched HOLD on (F2 Save opened the Save panel, which holds the reading and remembers its cursor). UP or DOWN with no change (a cursor or value moved, or the LCD contrast changed on a screen without a cursor: the measurement screen and the Measurement saved-item viewer; not recorded for the other viewers, and in the Trend view UP and DOWN zoom). LEFT and RIGHT. INFO (the help is invisible; F4 closes it). HOLD in AutoHOLD (takes a new value and stays in AutoHOLD). HOLD, MINMAX or RANGE that changed nothing (ignored: not available in this function or mode, or a page that blocks keys such as the lead alert). RANGE held in autorange is not flagged.
- **`warnings`** (top level): a list of `{code, message}`. `KEY_IGNORED`: a HOLD, MINMAX or RANGE had no effect. `LEAD_ALERT_POSSIBLE`: such an ignored key in a current function (`A_*`, `MA_*`, `UA_*`), unless REL explains an ignored RANGE or a running recording explains an ignored MINMAX. `MENU_STATE_UNKNOWN`: the last key that can close a menu (ignoring UP, DOWN, LEFT, RIGHT, INFO and BACKLIGHT after it) was a soft key that changed nothing or opened the Save panel.

`--text` prints a `changed:` line (`nothing` or `unknown` where applicable), a `note:` line and `WARNING CODE: message` lines. If the last key is a soft key and nothing changed, `press` reads the state once more after another settle period (at least 1 s) before deciding, because some choices (low-pass, for one) take more than 1 s. If the link drops mid-sequence, `press` stops and keeps the presses made so far. A drop noticed while the key is being sent gives that entry `ack` `LINK_CLOSED` and the state "meter powering off"; a drop noticed while the state is read back after the key gives the state "link closed (meter switched off, or the adapter went out of range)". In both cases `changed` is `null`.

How to use them with the golden rules:

- `changed` is evidence of an effect, not of a screen. A non-empty `changed` confirms what the key did; `[]` after a soft key means the screen is now unknown (a menu or panel may be open, or the choice was refused). Treat that as rule 1 and rule 3 do: ask someone at the meter to read the screen before the next soft key, and do not press F4 blind.
- `null` means no check was possible: fall back on your own key history.
- `MENU_STATE_UNKNOWN` means a menu, the Save panel or a refused choice may still be open. Do not press F1, F2 or F3 until you know which screen is shown.
- `KEY_IGNORED` on HOLD, MINMAX or RANGE means the function or mode does not take the key, or a page blocks keys. Do not repeat the key. On any dial position the page that blocks keys may be the lead alert (a lead left in the mA or A jack, section 6). Outside the current functions `press` gives only `KEY_IGNORED` for it, not `LEAD_ALERT_POSSIBLE`: when section 5 says the key works there, ask the user to check the leads. If the function is a current one, see the next point.
- `LEAD_ALERT_POSSIBLE` means the lead alert may be showing (section 6). The warning itself is invisible over the protocol and the reading looks like a normal near-zero current, so this is a suspicion drawn from ignored keys. Stop measuring, ask the user to check which jacks hold the leads and to read the meter screen, and take no reading as valid until they confirm.
- `note` texts are reminders of what the key may have done where `changed` cannot say: read them before the next key.

## 2. What is visible over the protocol

| Situation | QDDB / queries |
|---|---|
| Measurement screen | works |
| HOLD, AutoHOLD, MIN MAX session, Peak session, REL, low-pass, running recording | works; `modes` shows `HOLD`, `AUTO_HOLD`, `MIN_MAX_AVG` (MIN MAX and Peak; a Peak session also has secondary function `PEAK_MIN_MAX`), `REL`, `REL_PERCENT`, `LOW_PASS_FILTER`, `RECORD` |
| Menu, Save panel (opened from the measurement screen), Delete Memory, View Memory list, Setup panel and its pages, INFO help | works (they overlay the reading; QDDB answers as on the measurement screen, except that the meter is in HOLD while the Save panel is open) |
| Saved-item viewers (View Mem) | blocked (`EXECUTION_ERROR`) |
| MIN MAX and recording result pages | blocked (the Peak result page was not checked) |
| Save panel opened from a MIN MAX or recording result page | blocked |
| Power-off confirmation (after ONOFF) | blocked |
| Dial change | blocked briefly; read again after a moment |
| Lead-alert warning | invisible, see below |

A blocked query shows up in `press` results as the state text "menu / result page open (measurement queries blocked)", and in `read` and the other reading commands as the error `METER_BUSY` (exit 5). Despite that wording, menus and panels do not block queries: a blocked state means that a saved-item viewer, a result page, the Save panel opened from a result page or the power-off confirmation is open, or that the dial was just turned.

The lead-alert page ("WARNING / Leads connected incorrectly.", all soft-key labels blank, backlight forced to full brightness) appears on the A position with no lead in the mA or A jack (or leads in both), and on uA with the lead in the A jack (only the no-lead case was observed over the protocol, see section 6). Nothing of it is visible over the protocol: the live notification, QDDB, QM and QDDA all report a normal near-zero current (0.001 mA DC in the live packet and QDDB, 0.002 mA in QM, 1e-06 A in QDDA; state `NORMAL`; the live packet has no secondary record), indistinguishable from a real zero reading. While it is shown the meter acknowledges keys but ignores them (F1 and HOLD changed nothing). The cure is physical: fix the jacks (section 6). When the leads are corrected the warning disappears and the backlight goes back to the state it had before (off in the walk).

Other invisible things:

- whether the Menu, the Save panel, View Memory, Delete Memory, the Setup panel, a Setup page or the help is open at all (QDDB answers as on the measurement screen; the name list was not checked);
- the cursor position in every menu, panel and list, and the soft-key labels;
- which blocked page is shown: a saved-item viewer, its Trend view, a MIN MAX or recording result page (probably also the Peak result page), the Save panel opened from a result page and the power-off confirmation all give the same blocked state, although F4 Close discards only on a result page, F3 deletes only in some viewers and F1 on the power-off confirmation switches the meter off (F4 there is Cancel);
- the Setup values (beeper, auto backlight timeout, auto power off, smoothing, event thresholds), the continuity beeper mute and Beep on SHORT/OPEN (none of them is in QDDB);
- the screen contrast and whether the backlight is on.

Visible: the dBm reference (`DB_REF`) in dBm mode and the temperature offset (`TEMP_OFFSET`) in temperature mode. Both are QDDB readings: the MCP `press` state leaves the readings out, but `changed` lists `db_ref` or `temp_offset` when they change (in the CLI and in MCP), and `read` shows their values.

## 3. Screens common to all positions

### 3.1 Measurement screen

Status bar: battery, clock, a mini reading, date, beeper icon (speaker with waves = beeper on, plain speaker = off). Home soft keys are F1 Menu, F2 Save, F3 mode key or dash, F4 Setup. LoZ V~ has no Menu (F1 is a dash). Positions with a menu are listed in section 5.

### 3.2 HOLD and AutoHOLD

HOLD shows a "HOLD" banner (`modes` = `HOLD`); HOLD again releases. While held the soft keys become F1 AutoHOLD, F2 Save, F4 Setup. F1 AutoHOLD gives an "AutoHOLD" banner (`modes` = `AUTO_HOLD`) with F1 Cancel (back to normal), F2 Save, F4 Setup; HOLD inside AutoHOLD keeps AutoHOLD and takes a new value. Opening the Save panel from the measurement screen also puts the meter into HOLD.

### 3.3 MIN MAX session and result page

MINMAX (short or held) starts a session: "Min Max" banner, Maximum, Average and Minimum with elapsed times and the start date and time; `modes` = `MIN_MAX_AVG`; F1 Restart (clears the values and restarts the start time), F4 Stop. Inside the session:

- HOLD adds a HOLD banner (`modes` = `HOLD,MIN_MAX_AVG`): values freeze, timers keep running. HOLD again releases.
- RANGE locks the range (auto to manual) without leaving MIN MAX.
- F4 Stop or MINMAX opens the **result page** ("Stopped" + "Min Max" banners): F1 Restart, F2 Save, F4 Close. QDDB is blocked here. F2 Save opens a Save panel that has only "Save" (F1 +Name, F2 Save, F4 Close back to the result page). F4 Close or MINMAX returns to the measurement screen; the range stays as it was.

MIN MAX is ignored on LoZ V~ and in diode test (`modes` stays empty).

### 3.4 Peak session

Chosen from the Menu (Peak item). Same shape as MIN MAX with a "Peak" banner and Peak Max, Average, Peak Min; secondary function `PEAK_MIN_MAX`, `modes` = `MIN_MAX_AVG`; F1 Restart, F4 Stop leads to a result page (Restart, Save, Close; whether it blocks QDDB was not checked). Peak is refused in temperature mode and in the "DC,AC" combination mode (section 5).

### 3.5 REL and REL %

Menu item REL (F1 REL, F2 REL %). REL shows the delta reading with "Reference" and the live value below, a "REL" banner, `modes` = `REL`, and locks the range (RANGE and RANGE held are then ignored). On the home screen F3 becomes "REL %" and toggles percent mode (+/-10 % bar, `modes` = `REL_PERCENT`) and back. Choosing REL again in the Menu ends REL; from REL %, choosing REL switches back to REL. REL % is refused in dBV mode (observed on mV~; dBm was not tried); REL there gives a relative dB reading (`REL_REFERENCE` in QDDB). REL works on an OL reading (reference OL). REL is refused in the "DC,AC" combination mode (observed on uA), and Peak is refused in "DC,AC" mode and in temperature (observed on mV DC). Both work from the plain DC function.

### 3.6 Save panel

F2 Save on the home screen. Title "Save", overlays the lower half of the screen; the meter is in HOLD while it is open. Items: Save, View Memory and Delete (seen on LoZ V~ and V~); Record is added on the positions where a recording makes sense (seen on V~; LoZ V~ has none). The panel has two columns; the cursor moves with LEFT and RIGHT in row order and with UP and DOWN in column order, both wrapping. The cursor is remembered between openings and cannot be read over the protocol.

- LoZ V~ layout: column 1 Save, Delete; column 2 View Memory. RIGHT cycles Save, View Memory, Delete; DOWN cycles Save, Delete, View Memory.
- Layout with Record (V~): column 1 Save, Record; column 2 View Memory, Delete. By the same rule RIGHT cycles Save, View Memory, Record, Delete and DOWN cycles Save, Record, View Memory, Delete (DERIVED, not stepped through).

| Cursor on | F1 | F2 | F3 | F4 |
|---|---|---|---|---|
| Save | +Name | Save | - | Close |
| Record | soft keys not recorded (the Recording page is entered from this item, see below) | | | not recorded |
| View Memory | View | - | - | Close |
| Delete | Open | - | - | Close |

- **F2 Save** stores the reading and returns to the measurement screen at once (HOLD released). Each save adds one measurement item.
- **+Name** opens a name list (Save n, MinMax n, Recording n, Motor n, Panel n, Circuit n, Control n, Battery n; n is the next sequence number). F1 Save, F3 Reset-# (never pressed: resets the counter), F4 Close, which returns straight to the measurement screen and releases HOLD. DOWN wraps.
- **F4 Close** on the panel: back to the measurement screen, HOLD released.
- **Record** opens the "Recording" page (the key that opens it from the Record item was not recorded): Set Duration (days hrs min, default 00 00 01), Set Sample Interval (min sec, default 00 01), Memory Available. F1 Edit (digit editor, OK / Cancel), F2 Start, F4 Cancel. A running recording shows a "Recording" banner, Start Time, Remaining Time, Interval Samples, Events; `modes` = `RECORD`; F4 Stop. During a recording, HOLD shows a HOLD banner, freezes the screen and blanks all soft-key labels, so F4 Stop is not offered until HOLD is released (the recording continues; HOLD again releases). RANGE works (auto to manual); MINMAX and F1 are ignored; INFO opens help. At the end of the duration or on Stop a "Stopped" + "Recording" page appears (Duration, Interval Samples, Events; F2 Save, F3 Trend, F4 Close; QDDB blocked). From Trend: F2 Save, F3 Summary, F4 Close. Save opens the Save panel (+Name, Save, Close). **Close without Save discards the recording.**
- **View Memory, View** opens the "View Memory" panel with Measurement, Min Max, Peak (column 1) and Recording (column 2); F1 View, F4 Close. Entering it releases HOLD. Viewers (QDDB blocked in all of them):
  - Measurement: "View Mem" banner, the saved reading full-screen, its time and date in the status bar. F1 Prev (older), F2 Next (newer, stops at the newest), F3 Delete (never pressed), F4 Close (straight to the measurement screen). UP and DOWN change contrast.
  - Recording: summary ("View Mem" + "Recording" banners, last value, bar graph, Start Time, Duration, Interval Samples, Events). F1 Prev, F2 Next, F3 Trend, F4 Close. No delete key. Trend: graph of the whole recording, header start time and zoom "x1", a cursor with its value and time under the graph. LEFT and RIGHT move the cursor one sample (held: two), UP zooms in (x2 around the cursor, y axis rescales), DOWN zooms out; F3 Summary, F4 Close.
  - Min Max: "View Mem" + "Min Max" banners, last value, Maximum, Average, Minimum with elapsed times, start date and time. F1 Prev, F2 Next, F3 Delete (never pressed), F4 Close.
  - Peak: "View Mem" + "Peak" banners, Peak Max, Average, Peak Min with times. Same soft keys.
- **Delete, Open** opens the "Delete Memory" panel (Measurement, Min Max, Peak, Recording): F1 View (not explored; the page it opens was never seen and may offer delete keys, so stay out), F3 Delete All (never pressed), F4 Close (back to the measurement screen).

**Destructive keys that were never pressed and must not be pressed by an agent without an explicit request:** Delete (in the viewers), Delete All (Delete Memory), Reset-# (name list). Closing a recording result page without saving also loses the recording.

These keys can be hit blind. The Save panel reopens on the item last selected (the View Memory panel was not checked), and neither the cursor nor the panels show over the protocol. F2 then F1, meant for +Name, opens Delete Memory when the Save cursor was left on Delete, and F3 there is Delete All (for example an F3 meant as a home mode key such as the edge toggle, REL % or continuity, or as a Menu choice such as ms or DC,AC). On the name list F3 is Reset-#. All viewers give the same blocked state, so an F3 meant as Trend in the Recording viewer is Delete when a Measurement, Min Max or Peak viewer is open. After opening the Save panel or View Memory, never press F3 unless your own key history shows that the Recording viewer or a recording result page is open.

### 3.7 Setup panel

F4 Setup on the measurement screen, also in HOLD and AutoHOLD. The MIN MAX, Peak and recording result pages have no Setup key; F4 there is Close. Title "Setup", two columns: Reset, Meter Info, Instrument, Recording; Display, Calibration, Contrast, Secure Erase. F1 shows the name of the selected item and opens it; F4 Close returns to the measurement screen. The cursor is remembered and cannot be read over the protocol (section 1, rule 2). QDDB keeps working with Setup and its pages open. INFO inside Setup shows context help for the setup items; its Close returns to the Setup panel (not to the measurement screen). The help also lists a "Software Update" item that is not visible in the panel.

**Not explored on purpose, stay out:** Display, Calibration, Contrast, Secure Erase, and the hidden Software Update item. Secure Erase and Software Update were deliberately not opened; judging by their names they can erase data or change the firmware.

- **Reset** selected: F1 dash, F2 Setup, F3 Meter, F4 Close. F2 and F3 each open an "ATTENTION" confirmation (F1 OK, F4 Cancel), then return to the measurement screen. Setup: "Reset Setup settings, Temperature Offset, and dBm reference selection to factory defaults?". Meter: "Reset all meter settings to factory defaults?". Only Cancel was ever pressed. Never press OK. Because the Setup panel is invisible over the protocol, a stray F4 followed by F2 or F3 and then F1 (meant as Menu, +Name, REL or Edit) would confirm a factory reset. F4 on the confirmation is Cancel.
- **Meter Info**: read-only list (Model, Serial, Firmware Version, calibration fields, Operator / Company / Site / Contact Info filled in with FlukeView Forms, recording memory available, board ID, bootloader versions, calibration version). UP and DOWN scroll one line (no contrast change) and stop at the ends. F4 Close (straight to the measurement screen).
- **Instrument**: Beeper, dBm Reference, Auto Backlight Timeout, Auto Power Off, Smoothing (AC). UP and DOWN move the cursor, no wrap at either end. F1 Edit, F4 Close (straight to the measurement screen). In edit mode the value is highlighted with an up/down marker; F1 OK, F4 Cancel (restores the old value; checked for every item).

  | Item | Values |
  |---|---|
  | Beeper | On, Off |
  | dBm Reference | 4-digit editor (0600 ohm); LEFT and RIGHT select the digit (stop at the ends), UP and DOWN change it |
  | Auto Backlight Timeout | 5, 10, 15, 20, 25, 30 min, Off (wraps) |
  | Auto Power Off | Off, 15, 25, 35, 45, 60 min (wraps) |
  | Smoothing (AC) | On, Off |
- **Recording** (page title "Format"): Event Threshold for AutoHOLD (1 % steps; 4 to 12 seen) and Event Threshold for Recording (1, 4, 5, 10, 15, 20, 25 %, Off; wraps). F1 Edit, F4 Close (where it returns to was not recorded); edit as above.

### 3.8 INFO help

INFO opens a help viewer titled with the function name (for example "Low Impedance Input", "Measure Volts AC", "Measure Volts DC"): a description, then one paragraph per icon or key on the current screen. F1 Prev (previous paragraph), F2 Next, F3 More... (page down), F4 Close (back to the measurement screen; help opened inside Setup closes to the Setup panel). DOWN scrolls too; it stops at the end without wrapping. INFO held reopens the help at the scroll position it was left at. The help text appears only on the LCD; QDDB keeps answering with the measurement while it is open.

### 3.9 Backlight

BACKLIGHT (short or held, one step each): off, bright, dim, off. It also works on the MIN MAX result and save pages (an earlier observation recorded in the README, not repeated in this walk). The lead-alert warning forces the backlight bright.

## 4. Menu mechanics (all positions with a Menu)

F1 Menu opens an overlay titled "Menu" with two columns of items (some items hold two or three choices, shown by the soft keys). LEFT and RIGHT step through the items in row order, UP and DOWN in column order, both wrapping. The soft-key labels follow the selected item: press the soft key of the choice you want. The cursor is remembered and cannot be read over the protocol. F4 Close closes the menu. A refused choice leaves the menu open. A choice can take time to apply: low-pass needs more than 1 s, so read the state again before deciding that it failed.

## 5. Dial positions

In the order they were walked. "Home" lists the soft keys F1 / F2 / F3 / F4 on the measurement screen. All positions share HOLD, Save, Setup, INFO and BACKLIGHT as in section 3; only differences are listed. HOLD then F1 AutoHOLD was seen on V~, V DC and Ohm (mV~ behaved as V~). On LoZ V~ the soft keys under HOLD were not recorded, on the other positions only HOLD itself was checked, and during a recording HOLD blanks the soft keys.

### 5.1 LoZ V~ (`V_AC_LOZ`)

Home: "0.0 VAC LoZ", "Manual Range 1000 V", secondary reading VDC. Soft keys: - / Save / - / Setup. No Menu. RANGE and RANGE held do nothing (fixed 1000 V). F1, F3, LEFT, RIGHT do nothing. MIN MAX does nothing. INFO opens "Low Impedance Input". Save panel has no Record. Other keys as in section 3.

### 5.2 V~ (`V_AC`)

Home: "Auto Range 5 VAC", bar graph 0 to 5. Soft keys Menu / Save / - / Setup (F3 becomes a mode key in Hz/%/ms, dBm and REL). RANGE: AUTO 5, MAN 5, MAN 50, MAN 500, MAN 1000, MAN 5, ...; RANGE held = auto. INFO "Measure Volts AC". HOLD with AutoHOLD, MIN MAX as in section 3.

Menu: column 1 VAC, REL, Peak/CF, low-pass (icon); column 2 Hz/%/ms, dBm, dBV.

| Item | F1 | F2 | F3 | Result |
|---|---|---|---|---|
| VAC | VAC | | | plain V AC, auto range |
| REL | REL | REL % | | section 3.5 (locks the range in use) |
| Peak, CF | Peak | CF | | Peak: section 3.4. CF: crest factor big, V AC below (secondary `CREST_FACTOR`) |
| low-pass | low-pass | low-pass + Hz | | low-pass banner, forces MAN 1000 V (`modes` = `LOW_PASS_FILTER`); slow to apply. + Hz: frequency big, V AC below |
| Hz, %, ms | Hz | % | ms | frequency big with V AC below (`HERTZ`), duty cycle with Hz below (`DUTY_CYCLE`), pulse width with Hz below (`PULSE_WIDTH`) |
| dBm | dBm | dBm, Hz | | dBm with V AC (or Hz) below; `DB_REF` in QDDB (secondary `DBM`, `DBM_HERTZ`) |
| dBV | dBV | dBV, Hz | | as dBm without reference (`DBV`, `DBV_HERTZ`) |

Home F3 in the Hz, duty and pulse-width modes toggles the trigger edge (`POSITIVE_EDGE` / `NEGATIVE_EDGE`). In dBm it is "Ref": opens "dBm Reference" with the value highlighted; UP and DOWN cycle the presets 4, 8, 16, 25, 32, 50, 75, 600, 1000 ohm, applied immediately (no OK or Cancel), F4 Close. Restore 600 if you change it. Home F3 in REL is "REL %".

### 5.3 mV~ (`MV_AC`)

Home: "Auto Range 500 mVAC". Soft keys Menu / Save / - / Setup. RANGE: AUTO 500, MAN 500, MAN 50 (OL on a larger input), MAN 500, ...; held = auto. Menu identical to V~ with "mVAC" as first item; every item was checked (secondary functions as in V~). DOWN in the menu wraps from dBV to mVAC. REL % works straight from the Menu (`REL_PERCENT`); choosing it again ends it. In dBV mode REL % is refused and REL gives a relative dB reading. Low-pass applies slowly.

### 5.4 V DC (`V_DC`)

Home: zero-centred bar -5 to 5, "Auto Range 5 VDC", soft keys Menu / Save / - / Setup (F3 becomes the edge toggle in Hz/%/ms and REL % in REL). RANGE: AUTO 5, MAN 5, 50, 500, 1000, 5, ...; held = auto. INFO "Measure Volts DC". Menu (no dBm, dBV or low-pass): column 1 VDC, REL, Peak; column 2 AC+DC, Hz/%/ms. LEFT and RIGHT row order: [VDC, AC+DC], [REL, Hz], [Peak].

| Item | F1 | F2 | F3 | Result (primary / secondary) |
|---|---|---|---|---|
| VDC | VDC | | | `V_DC` |
| AC+DC | AC+DC | AC, DC | DC, AC | `V_AC_PLUS_DC` (true rms AC+DC, unit "V AC+DC", no bar graph); `V_AC_OVER_DC` (AC big, DC below); `V_DC_OVER_AC` (DC big, AC below). These change the primary function, not a secondary one |
| REL | REL | REL % | | section 3.5 (locks MAN 5) |
| Peak | Peak | | | Peak session (`V_DC` / `PEAK_MIN_MAX`) |
| Hz, %, ms | Hz | % | ms | `V_DC` / `HERTZ`, `DUTY_CYCLE`, `PULSE_WIDTH`, VDC below; home F3 = edge toggle |

### 5.5 mV DC (`MV_DC`)

Home: zero-centred bar -50 to 50, "Auto Range 50 mVDC". RANGE: AUTO 50, MAN 50, MAN 500, MAN 50, ...; held = auto. Menu: column 1 mVDC, REL, Peak, AC+DC; column 2 Temp, Hz/%/ms. UP from the first item wraps to the last.

| Item | F1 | F2 | F3 | Result |
|---|---|---|---|---|
| mVDC | mVDC | | | `MV_DC` |
| REL | REL | REL % | | as V DC (locks MAN 50) |
| Peak | Peak | | | `MV_DC` / `PEAK_MIN_MAX`, auto range kept. Refused (menu stays open) in temperature mode and in DC,AC mode: only works from plain mVDC |
| AC+DC | AC+DC | AC, DC | DC, AC | `MV_AC_PLUS_DC`, `MV_AC_OVER_DC`, `MV_DC_OVER_AC` |
| Temp | F | C | | primary `TEMPERATURE` (thermocouple), fixed manual range 1350 C / 2462 F, no bar graph, QDDB carries `TEMP_OFFSET`. Home soft keys Menu / Save / Offset / Setup. Without a probe: "ATTENTION Open Thermocouple." (reading `OPEN_TC`). RANGE ignored. MIN MAX and REL work (REL in C, `REL_REFERENCE` in C) |
| Hz, %, ms | Hz | % | ms | `HERTZ` / `DUTY_CYCLE` / `PULSE_WIDTH` with mV DC below |

Temperature F3 Offset opens "Temperature Offset": editor +000.0 (sign, 3 digits, 1 decimal), cursor on the last digit, LEFT and RIGHT move (the sign is reachable), UP and DOWN change and apply at once (`TEMP_OFFSET` follows, and `press` lists `temp_offset` in `changed`); only F4 Close. Leave it at 0.0.

### 5.6 Ohm (`OHMS`)

Home: bar 0 to 500, "Auto Range 500 ohm". Soft keys Menu / Save / continuity (beeper symbol) / Setup. RANGE: AUTO 500 ohm, MAN 500 ohm, 5 k, 50 k, 500 k, 5 M, 30 M, 50 M, 500 M, 500 ohm, ...; held = auto. The 30 Mohm step was seen only in the QDDB range value, not checked on the LCD. HOLD with AutoHOLD, MIN MAX (auto range kept) and REL / REL % (REL locks MAN 500) as elsewhere.

- Home F3 continuity: primary `CONTINUITY`, "Continuity" with a beeper icon, big ohm reading, fixed MAN 500 ohm, a bar with the threshold and "Beep on SHORT"; QDDB state `SHORT_CIRCUIT` for a short (4.7 ohm seen). F3 then reads "ohm" and returns to `OHMS` with auto range. The beeper was silent with the instrument beeper off; not checked with it on.
- Menu: column 1 "Ohms, nS, continuity", REL; column 2 "continuity Beeper".
  - Ohms item: F1 Ohms, F2 nS, F3 continuity. nS: `CONDUCTANCE`, MAN 50 nS (OL for a short), soft keys Menu / Save / - / Setup.
  - Beeper item: F1 Beeper toggles the continuity beeper (icon crossed out = muted; also switches to continuity), F2 Short/Open toggles "Beep on SHORT" / "Beep on OPEN". Both were toggled once and restored.

### 5.7 Diode / capacitance (`DIODE_TEST`, `CAPACITANCE`)

One dial position; the meter remembers which of the two was last used. Home soft keys Menu / Save / - / Setup. Menu: "Diode, Cap" (F1 Diode, F2 Cap), REL (F1 REL, F2 REL %).

- Cap: "OL mF" with a capacitor symbol, "Auto Range 100 mF". RANGE: AUTO 100 mF, MAN 100 mF, 1 nF, 10 nF, 100 nF, 1 uF, 10 uF, 100 uF, 1000 uF, 10 mF, 100 mF, ...; held = auto. REL (locks the range), MIN MAX and HOLD work.
- Diode: "V DC" with a diode symbol, fixed MAN 5 V DC; RANGE and RANGE held ignored; state `SHORT_CIRCUIT` at 4.7 mV. MIN MAX ignored. HOLD works.

### 5.8 A with the lead in the mA jack (`MA_*`)

The A dial position serves both current jacks. With the lead in the mA jack: home "0.00x mADC", zero-centred bar -50 to 50, "Auto Range 50 mADC", soft keys Menu / Save / - / Setup. RANGE: AUTO 50 mA, MAN 50, MAN 400, MAN 50, ...; held = auto. Menu: column 1 "AC, DC" (F1 AC, F2 DC), AC+DC (F1 AC+DC, F2 AC,DC, F3 DC,AC), REL, Peak/CF; column 2 Hz/%/ms.

| Item | Result |
|---|---|
| AC | `MA_AC` ("mAAC", bar 0 to 50) |
| DC | `MA_DC` |
| AC+DC / AC,DC / DC,AC | `MA_AC_PLUS_DC` / `MA_AC_OVER_DC` / `MA_DC_OVER_AC` |
| Peak (from DC) | `MA_DC` / `PEAK_MIN_MAX` session |
| CF | `MA_AC` / `CREST_FACTOR` (switches to AC) |
| Hz, %, ms | `MA_AC` / `HERTZ`, `DUTY_CYCLE`, `PULSE_WIDTH` (AC side) |
| REL, REL % | as elsewhere (locks MAN 50) |

### 5.9 A with the lead in the A jack (`A_*`)

Home "0.0000 ADC", bar -5 to 5, "Auto Range 5 ADC". RANGE: AUTO 5 A, MAN 5, MAN 10, MAN 5, ...; held = auto. Menu identical to the mA jack (the cursor carries over), with the same results under `A_AC`, `A_DC`, `A_AC_PLUS_DC`, `A_AC_OVER_DC`, `A_DC_OVER_AC`, `A_DC` / `PEAK_MIN_MAX`, `A_AC` / `CREST_FACTOR`, `A_AC` / `HERTZ`, `DUTY_CYCLE`, `PULSE_WIDTH`; REL locks MAN 5.

### 5.10 uA (`UA_*`), lead in the mA/uA jack

Home "0.01 uADC", bar -500 to 500, "Auto Range 500 uADC". RANGE: AUTO 500 uA, MAN 500, MAN 5000, MAN 500, ...; held = auto. The Menu layout differs from mA and A: column 1 "AC, DC", AC+DC, REL; column 2 "Peak, CF", Hz/%/ms. LEFT and RIGHT go row by row and wrap (REL, RIGHT, "AC, DC"; "AC, DC", RIGHT, Peak). Results: `UA_AC`, `UA_DC`, `UA_AC_PLUS_DC`, `UA_AC_OVER_DC`, `UA_DC_OVER_AC`, `UA_DC` / `PEAK_MIN_MAX` (takes MAN 500), `UA_AC` / `CREST_FACTOR`, `UA_AC` / `HERTZ`, `DUTY_CYCLE`, `PULSE_WIDTH`, REL (locks MAN 500). REL is refused in "DC, AC" mode like Peak; it works from plain DC.

### 5.11 LoZ Ohm (`OHMS_LOW`)

Home "OL ohm Lo Ohms" with nothing connected, bar 0 to 50, fixed "Manual Range 50 ohm"; live state `OL_RANGE`. Soft keys Menu / Save / - / Setup (F3 does nothing). RANGE and RANGE held ignored. Menu has only REL (F1 REL, F2 REL %); REL works even on OL (reference OL). MIN MAX works (Average shows dashes on OL). HOLD works.

## 6. Jack rules

- The A dial position needs the lead in the matching jack: mA jack gives the mA ranges, A jack gives the A ranges. Leads in both jacks, or in neither, raise the lead-alert warning. COM does not matter.
- uA needs the lead in the mA jack; a lead in the A jack raises the warning.
- Every other position expects the mA and A jacks to be empty, otherwise the warning appears.
- The warning is invisible over the protocol (section 2). On the A and uA positions, if keys stop having an effect and the reading is a near-zero current (about 0.001 to 0.002 mA, state `NORMAL`), suspect a wrong jack and ask for the leads to be checked. `press` raises `LEAD_ALERT_POSSIBLE` for an ignored HOLD, MINMAX or RANGE in a current function, which is the same suspicion (section 1, "What `press` reports").
- On every other position a lead left in the mA or A jack raises the same warning, but `press` gives only `KEY_IGNORED` for an ignored HOLD, MINMAX or RANGE there, not `LEAD_ALERT_POSSIBLE` (what the readings show in that case was not observed over the protocol). When such a key should work on that position (section 5), suspect the leads and ask the user to check them.

Evidence: during the walk only the A position with no lead in either current jack was observed over the protocol. The other rules were confirmed on the meter itself, not recorded key by key.

## 7. Recipes

Sequences observed on the meter except where marked: Beeper off was run end to end, the others are put together from individually observed steps. Read the state between steps, and remember that menus, panels and cursors do not show in it (section 2).

- **Back to the measurement screen**: no key always leads there. QDDB answers the same on the measurement screen as with the Menu, the Save panel, View Memory, Delete Memory, the Setup panel, a Setup page or the help open, so pressing F4 until the state looks right does not work blind: F4 on the measurement screen, in HOLD and in AutoHOLD opens Setup, and a second F4 closes it again. Track the screen from the keys you pressed since the last known state and press F4 once for each overlay you opened. One F4 closes each of these: the Menu; the Save panel (HOLD released); the name list (straight to the measurement screen, HOLD released); Delete Memory (to the measurement screen); the Measurement and Recording saved-item viewers (straight to the measurement screen); the Setup panel; the Meter Info and Instrument pages (straight to the measurement screen, not to the Setup panel); the help (to the measurement screen, or to the Setup panel when it was opened inside Setup). Where F4 Close leads from the View Memory panel, the Min Max and Peak viewers, the Trend view and the Recording ("Format") setup page was not recorded. In a Setup editor the first F4 is Cancel and a second F4 closes the page. Use the state where it helps:
  - `MIN_MAX_AVG` in `modes`: a MIN MAX or Peak session is running, and F4 is Stop; F4 Stop followed by F4 Close drops the unsaved result. End a MIN MAX session with MINMAX instead (MINMAX in a Peak session was not tried).
  - `RECORD` in `modes`: a recording is running, and F4 is Stop. It ends the recording, and the next F4 Close discards it unless it is saved first. If HOLD is also on, the soft keys are blank: release HOLD first.
  - `HOLD` without `MIN_MAX_AVG`: either the HOLD screen (release it with HOLD; F4 there opens Setup) or an open Save panel (F4 Close). The name list opened from the Save panel also holds the reading (F4 Close), but whether QDDB answers while it is open was not checked.
  - `AUTO_HOLD`: end it with F1 Cancel.
  - A blocked state (section 2): a saved-item viewer, a MIN MAX or recording result page (probably also the Peak result page), the Save panel opened from a result page (F4 Close returns to the result page) or the power-off confirmation (F4 Cancel; never F1, which switches the meter off) is open, or the dial was just turned (read the state again after a moment before pressing anything). F4 Close on a result page discards an unsaved result.

  If you do not know the key history, ask someone at the meter what the screen shows before pressing F4.
- **Beeper off** (needs someone looking at the meter: the Setup cursor and the beeper setting are invisible over the protocol, and if the panel follows the column order of the other two-column panels (DERIVED), DOWN from Recording moves into column 2 with Display, Calibration, Contrast and Secure Erase): F4 Setup. Have the F1 label read and press DOWN until it reads Instrument (two DOWN presses from Reset). F1 Instrument (the cursor was on Beeper when the page opened; the list does not wrap, so UP presses can only bring the cursor up to Beeper). F1 Edit. If the highlighted value is On, press UP ("Off") and F1 OK; otherwise press F4 Cancel. F4 Close (straight back to the measurement screen). The status-bar speaker icon loses its waves; this cannot be checked over the protocol.
- **Beeper on**: the same path; in edit mode press UP or DOWN until "On" is highlighted, then F1 OK (not tried on hardware; only UP from On to Off was observed).
- **Start, stop and save MIN MAX**: MINMAX, then check that `modes` shows `MIN_MAX_AVG`. If it does not, MIN MAX is ignored here (LoZ V~, diode): stop and do not press F4. Press MINMAX again to stop: the state turns blocked, which means the result page is shown. Prefer MINMAX to F4 Stop, because F4 opens Setup when no session is running. Then F2 Save (a Save panel with only Save) and F2 Save. Where the meter goes after this second F2 was not recorded: if the state is still blocked, press F4 Close once and read the state again (repeat while it stays blocked); if QDDB answers, you are on the measurement screen and should press nothing more. To drop the values instead, press MINMAX or F4 Close on the result page. F1 Restart clears the values and keeps the session running.
- **Set REL**: F1 Menu, then move the cursor to REL. The Menu reopens on the item last used and the cursor is invisible, so count from a known position or have someone read the soft keys. Press F1 REL (or F2 REL % where offered), then read the state. If `modes` shows `REL` or `REL_PERCENT`, you are done and the Menu is closed. If the function, range and modes are all unchanged, the choice was refused (for example in DC,AC mode) and the Menu is still open: press F4 exactly once. If something else changed, another item was chosen and the Menu is closed: do not press F4 (it would open Setup); undo that choice instead. In the Ohm Menu, F1 on the "continuity Beeper" item mutes or unmutes the continuity beeper (and switches to `CONTINUITY`), and F2 toggles Beep on SHORT/OPEN; neither setting is visible over the protocol, and choosing it again undoes it. To end REL, choose REL again in the Menu (or REL % again to leave percent mode).
- **Change the dBm reference**: only when the state shows secondary function `DBM` (V~ or mV~; `DBM_HERTZ` was not checked). Home F3 Ref opens the "dBm Reference" page; each UP or DOWN moves one preset (4, 8, 16, 25, 32, 50, 75, 600, 1000 ohm) and applies at once. After the first press, check that `changed` lists `db_ref` (this works in the CLI and in MCP; the MCP `press` state carries no readings, so `DB_REF` itself shows only in the CLI state or in a separate `read`). If it does not, the page did not open and the press was a contrast step: press the opposite key once, and do not press F4 (on the measurement screen it opens Setup). F4 Close when done; restore 600 afterwards.
- **Back to autorange**: RANGE held. RANGE and RANGE held were ignored in REL, diode, LoZ V~ and LoZ Ohm, and RANGE in temperature. Continuity (fixed MAN 500 ohm), low-pass (MAN 1000 V) and nS (MAN 50 nS) also use a fixed range; RANGE was not tried there.
- **Save one reading** (from the plain measurement screen): F2 Save opens the Save panel and puts the meter in HOLD; then press F2 Save. This saves only when the Save panel cursor is on Save, because the panel reopens on the item last used and the cursor is invisible over the protocol (on View Memory and Delete F2 is a dash; on Record it was not recorded). After the second F2, check that `modes` no longer shows `HOLD` (saved, back on the measurement screen at once). If `HOLD` is still shown, the panel is still open with the cursor on another item: press F4 Close once (releases HOLD) and do not press F1 or F3 there (the cursor may be on Delete). If the first F2 did not bring `HOLD`, the Save panel did not open because another overlay, possibly Setup, was open: stop and find out where the meter is before the next soft key.
- **Record** (not verified end to end: the key that opens the Recording page from the Record item was not recorded, and the Save cursor is invisible over the protocol): F2 Save; move the cursor to Record with someone reading the panel; open it; adjust Duration and Interval with F1 Edit if needed; F2 Start. `modes` shows `RECORD` while the recording runs. When the state turns blocked, the recording has ended: press F2 Save, then F2 Save on the Save panel (where the meter goes afterwards was not recorded). Close without Save discards the recording.
- **INFO help**: INFO opens a help page for the current screen, but its text appears only on the LCD and nothing of it comes back over the protocol, so it does not help an agent that cannot see the screen. F4 Close returns (to the Setup panel when the help was opened inside Setup).

## 8. Summary across dial positions

| Dial | Primary functions (QDDB) | Menu items | Home F3 | Manual range steps |
|---|---|---|---|---|
| LoZ V~ | `V_AC_LOZ` (V DC secondary) | none | - | fixed 1000 V |
| V~ | `V_AC` | VAC, REL, Peak/CF, low-pass (+Hz), Hz/%/ms, dBm (+Hz), dBV (+Hz) | mode key (edge, Ref, REL %) | 5, 50, 500, 1000 V |
| mV~ | `MV_AC` | as V~ with mVAC | as V~ | 500, 50 mV |
| V DC | `V_DC`, `V_AC_PLUS_DC`, `V_AC_OVER_DC`, `V_DC_OVER_AC` | VDC, REL, Peak, AC+DC (3 choices), Hz/%/ms | mode key | 5, 50, 500, 1000 V |
| mV DC | `MV_DC` (+ AC/DC combinations), `TEMPERATURE` | mVDC, REL, Peak, AC+DC (3), Temp (F/C), Hz/%/ms | Offset in Temp | 50, 500 mV (Temp fixed) |
| Ohm | `OHMS`, `CONTINUITY`, `CONDUCTANCE` | Ohms/nS/continuity, REL, continuity Beeper (mute, Short/Open) | continuity and ohm | 500 ohm to 500 Mohm (8 manual ranges, 30 M seen in QDDB only) |
| Diode / Cap | `DIODE_TEST`, `CAPACITANCE` | Diode/Cap, REL | - | Cap 1 nF to 100 mF; diode fixed 5 V |
| A (mA jack) | `MA_AC` / `MA_DC` (+ combinations) | AC/DC, AC+DC (3), REL, Peak/CF, Hz/%/ms | - | 50, 400 mA |
| A (A jack) | `A_AC` / `A_DC` (+ combinations) | as mA | - | 5, 10 A |
| uA (mA jack) | `UA_AC` / `UA_DC` (+ combinations) | same items, Peak/CF in column 2 | - | 500, 5000 uA |
| LoZ Ohm | `OHMS_LOW` | REL | - | fixed 50 ohm |

Common to all: HOLD (and HOLD then F1 AutoHOLD where offered), MIN MAX session (except LoZ V~ and diode), Save panel (with Record where a recording makes sense), Setup panel, INFO help, BACKLIGHT cycle.

Refusals that look like "nothing happened": Peak and REL in the DC,AC combination mode, Peak in temperature, REL % in dBV mode, MIN MAX in diode and LoZ V~, RANGE where the range is fixed, every key under the lead-alert warning. A refused soft key leaves a menu open, so a following blind F4 lands somewhere else (it once opened Setup). Always know which screen is shown before the next soft key; the state does not show menus (section 2).
