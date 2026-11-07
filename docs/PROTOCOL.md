English | [中文 (简体中文)](PROTOCOL.zh-CN.md)

# open289 protocol reference

This document describes how open289 talks to a Fluke 287/289 multimeter through a Fluke IR3000 FC (an infrared-to-Bluetooth-LE adapter that clips onto the meter). It is written for people who want to implement or audit a client.

## Conventions and evidence levels

Every statement is tagged with how it is known.

| Tag | Meaning |
|---|---|
| VERIFIED | Observed on real hardware on 2026-11-06: IR3000 FC radio firmware 01.00.01, MSP firmware 01.00.15, Fluke 289 firmware V1.71, Windows PC with built-in Bluetooth (bleak over WinRT). Only a 289 was tested. |
| DERIVED | Taken from the Fluke Connect app, from public sources about the 287/289 serial protocol, or from naming and analogy, but not exercised against the hardware. Treat it as a hypothesis. |

Example values used in this document: adapter address `FA:28:90:00:00:01`, meter serial `12345678`. They are placeholders, not real devices.

All multi-byte integers are little-endian unless stated otherwise. Hex dumps are space-separated bytes.

## 1. Overview

```
Fluke 289  <--- infrared serial, 115200 8N1 --->  IR3000 FC  <--- Bluetooth LE --->  PC (open289)
```

| Part | Role |
|---|---|
| Fluke 287/289 | Digital multimeter. Speaks a line-oriented serial protocol (ASCII commands ending in CR, ASCII or binary replies) over its infrared port. |
| Fluke IR3000 FC | Battery-powered adapter with a BLE radio and a small microcontroller ("MSP"). It exposes two independent paths over one BLE connection. |
| open289 | The PC client. It uses both paths. |

The adapter offers two paths over a single connection (it accepts only one BLE central at a time, so a phone running Fluke Connect must not be connected):

1. A live stream. The adapter polls the meter itself and re-encodes the displayed readings into compact binary records, sent as notifications about 4 times per second (section 4). This path is fast but carries only what is on the display.
2. A pass-through. Commands written to an adapter control point are forwarded to the meter's serial port, and the meter's raw reply is buffered in the adapter and read back in chunks (sections 5 and 6). This path gives access to the full meter protocol: display state, saved data, recordings, key presses. It is slow (about 1.3 s per command, section 7).

VERIFIED: both paths work at the same time on one connection. Live notifications keep arriving while pass-through commands run.

## 2. BLE advertisement and GATT table

### 2.1 Advertisement

VERIFIED. The adapter advertises the local name `IR 3000 FC` and the primary service `B6981800-7562-11E2-B50D-00163E46F8FE`. No pairing or bonding is needed. A client should scan by name or service and then connect by address. The adapter must be awake: press its button for more than one second until the LED flashes green.

All Fluke-specific UUIDs share the base `xxxxxxxx-7562-11E2-B50D-00163E46F8FE`. This document abbreviates them to the first eight hex digits, for example `B698290F`.

### 2.2 GATT table

VERIFIED (full dump taken from the adapter; ATT MTU 23 after connecting on Windows). "Never write" entries are listed because other writes there can damage the adapter.

| Service | Characteristic | Properties | Purpose | Client use |
|---|---|---|---|---|
| 1800 Generic Access | 2A00 Device Name | read, write, write-no-response | `IR 3000 FC` | Read only. |
| 1801 Generic Attribute | 2A05 Service Changed | indicate | Standard | Ignored. |
| 180A Device Information | 2A24 Model Number | read | Model of the attached meter: `FLUKE 289`. DERIVED: `FLUKE PC3000` when the adapter is not clipped to a meter (string seen in the app, not observed). | Read to identify the meter. |
| | 2A25 Serial Number | read | Serial number string (NUL padded). Example `12345678`. | Read. |
| | 2A26 Firmware Revision | read | Radio firmware, `01.00.01` (space and NUL padded). | Read. |
| | 2A28 Software Revision | read | `V1.71`, the meter firmware. | Read. |
| | 2A29 Manufacturer Name | read | `Fluke Mfg Co.` | Read. |
| 180F Battery | 2A19 Battery Level | read, notify | Adapter battery in percent. | Read. |
| `B6981800` Reading | `B6982901` | read, notify | Declared ASCII reading characteristic. Read value was a fixed 17-byte pattern (`01 02 03 04 05` followed by zeros). DERIVED: the official app never uses it. | Not used. |
| | `B698290F` | read, notify | Live readings, 16 bytes per notification (section 4). | Subscribe. |
| `B6981801` CNX | `B6982902` | read, write | One-byte CNX ID. Writing `01` arms the adapter control point (section 3). | Write `01` once per connection. |
| | `B6982903` | read, write | Contains the device name text `IR 3000 FC`. Purpose not established. | Never written. |
| | `B6982904`, `B6982905` | write | Purpose not established. | Never written. |
| | `B698290E` | write, notify | Purpose not established. | Never written. |
| | `B6982911` | read, write, write-no-response, notify | DERIVED: control point of the microcontroller firmware updater. It reuses the same opcode byte values as the adapter control point with destructive meanings (value `80` erases flash memory). | NEVER WRITE. |
| | `B6982912` | write, write-no-response | DERIVED: firmware image data for the updater above. | NEVER WRITE. |
| `B6981804` Radio image | `B6982913`, `B6982914` | write, write-no-response, notify | DERIVED: BLE radio firmware image transfer. | NEVER WRITE. |
| `B6981805` Adapter logging | `B6982918` control point | read, write, write-no-response, notify | Command channel and the path to the meter (section 5). | Write commands, subscribe to replies. |
| | `B6982919` buffer | notify | Bulk data: the meter's buffered reply, in notification-sized pieces. | Subscribe. |

Why the "never write" list matters. The updater control point `B6982911` and the adapter control point `B6982918` accept the same single-byte opcodes, but `80` means "read buffer size" on the adapter and "erase flash" on the updater. A wrong UUID could brick the adapter. open289 hard-codes the UUIDs of the adapter service and refuses to write anywhere else except `B6982902`.

## 3. Connection sequence

VERIFIED unless marked.

1. Scan for `IR 3000 FC` (or service `B6981800`). Connect by address. No bonding.
2. Discover services. The ATT MTU stays at 23 on the tested Windows stack, so a notification or a write carries at most 20 bytes of payload; independently of the MTU, the adapter rejects control-point writes longer than 18 bytes (see 5.4). A client must accept notifications of any length up to the negotiated MTU.
3. Optionally read the Device Information characteristics (model, serial, firmware) and battery level.
4. Write the single byte `01` to the CNX ID characteristic `B6982902`, with a write-with-response.
5. Enable notifications (CCCD `01 00`) on the control point `B6982918` and the buffer `B6982919`. For live data also enable notifications on `B698290F`.
6. Send commands to the control point and read replies.

Arming. Before the CNX ID is written, the control point accepts writes but never answers; reading it back returns only the byte that was written. After `01` is written to `B6982902`, the control point answers every command by notification. DERIVED: the official app writes its "#n" tool number to the CNX ID on every connect (1 for the first tool, 2 for the second, and so on). Only the value `01` was tried; other values are untested.

Write types. The control point advertises both write and write-without-response. VERIFIED: both produced identical replies. open289 uses write-with-response.

Notifications. All notifications are plain (not indications). The adapter keeps sending live notifications while the control point is in use. A client should drain its reply queue before each command, because a reply to a timed-out command can arrive late and must not be mistaken for the next reply. open289 waits 0.5 s after a timeout and then drops stale replies.

Single connection. When another central (such as a phone) holds the connection, the adapter does not accept a second one. Close Fluke Connect on all phones.

## 4. Live readings

### 4.1 Notification format

VERIFIED. Every notification on `B698290F` has 16 bytes and arrives about 4 times per second (roughly every 250 ms).

| Offset | Size | Content |
|---|---|---|
| 0 | 8 | Primary record: what the meter shows on its main display. |
| 8 | 8 | Secondary record: what the meter shows as a secondary value. All zero when the meter shows no secondary value. |

DERIVED: other lengths exist for other meters (8 bytes for a single reading, 18 bytes with two extra mode bytes for some other Fluke models). The 289 sends 16. A client should still decode only the first 8 bytes of any shorter or longer notification and ignore the rest.

Reading a characteristic returns the most recent notification, so the characteristic can also be polled.

### 4.2 The 8-byte record

The record is one unsigned 64-bit little-endian integer W. Fields are taken from the least significant bit upward: `(W >> offset) & ((1 << width) - 1)`.

| Field | Bit offset | Width | Meaning |
|---|---|---|---|
| counts | 0 | 21 | Absolute display counts, 0 to 2,097,151. |
| state | 21 | 4 | Reading state (4.4). |
| decimals | 25 | 3 | Digits after the decimal point, 0 to 7. |
| magnitude | 28 | 3 | SI prefix code (4.4). |
| sign | 31 | 1 | 1 means negative. |
| unit | 32 | 8 | Unit code (4.5). Byte 4. |
| function | 40 | 8 | Meter function code (4.6). Byte 5. |
| valueRange | 48 | 7 | Range mantissa. |
| rangeDecade | 55 | 3 | Range exponent code (4.4). |
| attribute | 58 | 5 | Attribute code (4.7). |
| capture | 63 | 1 | Capture flag. DERIVED: set when the adapter button requests a save; never seen set during the survey. |

Byte shortcuts: with `lo` = bytes 0..3 as a 32-bit integer, `counts = lo & 0x1FFFFF`, `state = (lo >> 21) & 0xF`, `decimals = (lo >> 25) & 7`, `magnitude = (lo >> 28) & 7`, `sign = lo >> 31`. Byte 4 is the unit and byte 5 is the function. With `hi` = bytes 6..7 as a 16-bit integer, `valueRange = hi & 0x7F`, `rangeDecade = (hi >> 7) & 7`, `attribute = (hi >> 10) & 0x1F`, `capture = hi >> 15`.

### 4.3 Value and display formulas

| Quantity | Formula |
|---|---|
| Value in SI base units | `counts * 10^(magnitudeExponent - decimals)`, negated when sign is 1. |
| Number as shown on the meter | `counts * 10^(-decimals)`, negated when sign is 1, printed with exactly `decimals` digits (keep trailing zeros). |
| Unit text | SI prefix of the magnitude code followed by the unit text. |
| Range | `valueRange * 10^(rangeDecadeExponent + magnitudeExponent)`. The decoder uses this rule; VERIFIED: with a 289 the adapter leaves valueRange and rangeDecade at 0 (all 4,018 packets of the survey), so the live stream carries no range. Get the range from QDDB. |

Display text by state: NORMAL, GREATER_THAN and LESS_THAN print the number (the last two with a leading `> ` or `< `). OL_RANGE and OL_A2D print `OL`, or `-OL` when sign is set. BLANK and INVALID print `----`. EMPTY prints nothing.

Worked example, VERIFIED (a real capture, meter on V DC): primary record `d9 c5 00 08 02 0c 00 00`.

```
W low word  = 0x0800C5D9
counts      = 0xC5D9 = 50649
state       = 0 (NORMAL)
decimals    = 4
magnitude   = 0 (no prefix)       sign = 0
unit        = 0x02 (V DC)         function = 0x0C = 12 (VDC)
range = 0, attribute = 0, capture = 0
value       = 50649 * 10^(0-4) = 5.0649   ->  "5.0649 V DC"
```

Worked example, DERIVED (synthetic, built from the same rules): `00 00 80 28 0b 28 05 00` has state 4 (OL_RANGE), decimals 4, magnitude 2 (mega), unit 11 (ohm), function 40 (resistance), so it displays `OL MOhm`.

### 4.4 State, magnitude and range-decade tables

State (4 bits). VERIFIED codes are marked; the rest are DERIVED.

| Code | Name | Display | Seen on 289 |
|---|---|---|---|
| 0 | NORMAL | number | yes |
| 1 | BLANK | `----` | yes (auto-range blanking) |
| 2 | INACTIVE | | |
| 3 | INVALID | `----` | yes (during dial changes, bytes `ff ff 7f 00 00 <function> ...`) |
| 4 | OL_RANGE | `OL` | yes |
| 5 | OL_A2D | `OL` | |
| 6 | OPEN_TC | `Open TC` | |
| 7 | DISCHARGE | | |
| 8 | LEADS | | |
| 9 | GREATER_THAN | `> number` | |
| 10 | MISSING_PHASE | | |
| 11 | ERROR | | |
| 12 | LESS_THAN | `< number` | |
| 13 | EMPTY | empty | |
| 14, 15 | undefined | | |

A robust client flags 14 and 15 instead of treating them as NORMAL.

Magnitude (3 bits, SI prefix). The unit text in the overall display uses the prefix; the 3-bit field cannot encode tera or larger.

| Code | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| Prefix | none | G | M | k | m | u (micro) | n | p |
| Exponent | 0 | 9 | 6 | 3 | -3 | -6 | -9 | -12 |

Range decade (3 bits). DERIVED.

| Code | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| Exponent | 0 | 1 | 2 | 3 | undefined (treat as 0) | -3 | -2 | -1 |

### 4.5 Unit table (byte 4)

Codes seen on the 289 are marked "seen". All others are DERIVED from the app and appear on other Fluke models.

| Code | Unit | Code | Unit | Code | Unit |
|---|---|---|---|---|---|
| 0 | none | 16 | dB (seen) | 32 | crest factor (seen) |
| 1 | V AC (seen) | 17 | dBm (seen) | 33 | V AC+DC (seen) |
| 2 | V DC (seen) | 18 | W | 34 | A AC+DC |
| 3 | A AC | 19 | J | 35 | percent |
| 4 | A DC (seen) | 20 | H | 36 | V AC / Hz |
| 5 | Hz (seen) | 21 | PSI | 37 | g (acceleration) |
| 6 | percent RH | 22 | m Hg | 38 | m/s^2 |
| 7 | degrees C (seen) | 23 | in Hg | 39 | in/s |
| 8 | degrees F (seen) | 24 | ft H2O | 40 | mm/s |
| 9 | degrees Rankine | 25 | m H2O | 41 | mils |
| 10 | kelvin | 26 | in H2O | 42 | micrometre |
| 11 | ohm (seen) | 27 | in H2O (60 F) | 43 | unknown |
| 12 | siemens (seen) | 28 | bar | 44 | tera-ohm |
| 13 | percent, "duty" (seen) | 29 | pascal | 45 | VA DC |
| 14 | seconds | 30 | g/cm^2 | 46 | undefined |
| 15 | farad (seen) | 31 | dBV (seen) | 47 | VA AC |

Two things are not obvious. Unit 13 is named "duty percent" but it also carries relative-percent (REL %) readings. When a reading is in hertz (unit 5) the unit text is the SI prefix plus `Hz`; the function code says what is being measured (see the Hz rows in 4.8).

### 4.6 Function table (byte 5)

The function says which meter function produced the reading. Codes seen on the 289 are marked; the others are DERIVED and belong to other Fluke tools.

| Code | Name | Code | Name | Code | Name |
|---|---|---|---|---|---|
| 0 | none | 17 | A AC average | 34 | temperature (seen) |
| 1 | mV AC (seen) | 18 | A AC average + DC | 35 | degrees F |
| 2 | V AC (seen) | 19 | uA DC (seen) | 36 | degrees C |
| 3 | V AC+DC (seen) | 20 | mA DC (seen) | 37 | degrees Rankine |
| 4 | mV AC average | 21 | A DC | 38 | kelvin |
| 5 | V AC average | 22 | Hz mV AC | 39 | continuity (seen) |
| 6 | V AC average + DC | 23 | Hz V AC | 40 | resistance (seen) |
| 7 | V AC LoZ (seen) | 24 | Hz mV AC low-pass | 41 | conductance (seen) |
| 8 | mV AC low-pass | 25 | Hz V AC low-pass | 42 | low ohms (seen) |
| 9 | V AC low-pass | 26 | Hz mV DC | 43 | phase |
| 10 | uV DC | 27 | Hz V DC | 44 | A AC inrush |
| 11 | mV DC (seen) | 28 | Hz uA AC | 45 | capacitance (seen) |
| 12 | V DC (seen) | 29 | Hz mA AC | 46 | diode (seen) |
| 13 | mA AC | 30 | Hz A AC | 47 | V AC / Hz |
| 14 | A AC | 31 | Hz uA DC | 48 to 51 | 48 mV AC+DC, 49 mA AC+DC, 50 uA AC, 51 uA AC+DC |
| 15 | A AC+DC | 32 | Hz mA DC | 52 to 85 | insulation and installation tester functions |
| 16 | mA AC average | 33 | Hz A DC | 86 to 95 | pressure (86), field sense (87, 88), V ADC (89), self check (95); 90 to 94 undefined |

On the 289 the Hz display states were observed with the voltage function code and unit 5 (4.8), not with the Hz function codes above.

There is no function code for 289-specific modes such as dBm, dBV, duty cycle, pulse width, min/max, peak, hold or relative. These appear only as units, or not at all (4.9).

### 4.7 Attribute table (5 bits)

| Code | Meaning | Code | Meaning |
|---|---|---|---|
| 0 | none | 9 | low ohms |
| 1 | open circuit (seen, continuity) | 10 | open glitch |
| 2 | short circuit (seen, continuity) | 11 | short glitch |
| 3 | glitch (seen, continuity) | 12 | peak |
| 4 | good diode (seen) | 13 | sourced |
| 5 | negative edge | 14 | simulated |
| 6 | positive edge (seen, Hz) | 15 | noise |
| 7 | high current | 16 | breakdown |
| 8 | hazardous voltage indicator | 17 to 31 | undefined |

### 4.8 Observed encodings per 289 function

VERIFIED by a 20-minute survey in which the meter was stepped through its functions (4,018 live packets compared with 659 QDDB display snapshots, section 6). "Prim/sec" is the display state reported by the meter; the table gives the live function and unit codes. Across 3,076 packets taken while the meter state was stable, unit, SI prefix and decimals matched the QDDB snapshot in all but 12 packets, all at range-change instants. About 88 percent of values fell between the neighbouring QDDB snapshots; the rest coincided with an input that changed faster than the roughly 1.8 s snapshot interval.

| 289 display state | Primary: function, unit | Secondary | Notes |
|---|---|---|---|
| V DC | 12, V DC (2) | none | |
| V AC | 2, V AC (1) | none | |
| V AC with Hz | 2, Hz (5), attribute 6 (positive edge) | 2, V AC (1) | Hz becomes the primary, as on the LCD. |
| V DC with Hz | 12, Hz (5), attribute 6 | 12, V DC (2) | |
| V AC with dBm and Hz | 2, dBm (17) | 2, Hz (5) | |
| V AC with dBV | 2, dBV (31) | 2, V AC (1) | With REL the primary unit is dB (16). |
| V AC with crest factor | 2, CF (32) | 2, V AC (1) | |
| V AC with REL percent | 2, unit 13 | none | Unit 13 carries REL percent. |
| mV AC / mV DC | 1 with unit 1, or 11 with unit 2; magnitude milli | none | |
| V AC+DC | 3, unit 33 | 3, unit 0 (INVALID) | |
| V AC over DC / V DC over AC | 3, V AC (1) or V DC (2) | 3, the other one | |
| V AC LoZ | 7, V AC (1) | 7, V DC (2) | |
| Ohms / low ohms | 40 or 42, ohm (11) | none | OL is state 4. |
| Continuity | 39, ohm (11), attribute 1 open, 2 short, 3 glitch | none | |
| Conductance | 41, siemens (12), nano prefix | none | |
| Capacitance | 45, farad (15) | none | |
| Diode test | 46, V DC (2), attribute 4 good diode | none | |
| Temperature | 34, degrees C (7) or degrees F (8) | none | Follows the meter's unit setting. |
| mA DC / uA DC | 20 or 19, A DC (4) | none | |

### 4.9 What live packets do not carry

VERIFIED.

| Missing | Consequence |
|---|---|
| Mode flags (HOLD, AUTO HOLD, REL, MIN MAX, RECORD, low-pass filter) | A client cannot tell that a mode is active. |
| MIN, MAX and AVERAGE values | Not transmitted at all. |
| Range as meter setting, auto or manual range | Not present: valueRange and rangeDecade are always 0 for a 289. |
| Timestamp | None. The client stamps each packet on arrival. |
| Low battery | None. Battery level 2A19 is the adapter, not the meter. |

HOLD and REL appear only as whatever the LCD main reading shows: the held value during HOLD, the relative value during REL. To get modes and min/max/average, query the display (QDDB, section 6).

## 5. Adapter pass-through

### 5.1 Control point framing

Requests are written to the control point `B6982918` as `[opcode][parameters]`. There is no length byte, checksum or terminator. Integers inside parameters are 32-bit little-endian.

Replies arrive as notifications on the same characteristic as `[status][echoed opcode][payload]`. Bulk data arrives separately on `B6982919` as raw bytes with no header, in the order sent. All buffer notifications for a read arrive before the completion reply on the control point.

Because other replies may be in flight, match a reply by its echoed opcode (byte 1) and ignore replies for other opcodes.

### 5.2 Status codes

| Status | Name | Meaning |
|---|---|---|
| 0 | IDLE | DERIVED (name from the app). Not seen in a reply. |
| 1 | IN_PROCESS | VERIFIED: sent as an interim reply to `8E` (send) and `82` (read). Keep waiting for the final reply. |
| 2 | INCORRECT_MODEL | DERIVED. Probably returned for model-specific opcodes when the attached meter is the wrong family. Never observed. |
| 3 | COMPLETE | VERIFIED. Success. |
| 4 | CANCELLED | DERIVED. Treated as a transient failure. Not observed. |
| 5 | FAILED | DERIVED. Treated as a transient failure. Not observed. |

### 5.3 Opcode table

| Opcode | Name | Parameters | Reply payload | Status |
|---|---|---|---|---|
| `80` | GET_BUFFER_SIZE | none | 32-bit size of the reply buffer | VERIFIED. Returns size 0 when the buffer is empty. |
| `81` | CLEAR_BUFFER | none | none | VERIFIED. First step of every transaction. Whether it also empties the outgoing buffer is open (5.6). |
| `82` | GET_BUFFER_DATA | offset (u32), length (u32) | none; data comes on `B6982919` | VERIFIED. |
| `83` | CANCEL_COMMAND | none | none | DERIVED. Allowed by the client but never sent. |
| `84`, `85` | GET_189_LOG_DATA, GET_189_SAVED_DATA | unknown | unknown | DERIVED. Defined, never used by the official app or open289. |
| `86` | GET_1550_LOG_DATA | none | record count (u16) | DERIVED. Used by the official app for 1550/1555 insulation testers. Not used by open289. |
| `87` to `8C` | GET_289_SESSION_SUMMARY, RECORDED_SESSION_INFO, RECORDED_SESSION_RECORDS, SAVE_MINMAX, SAVE_PEAK, SAVE_MEASUREMENTS | unknown | unknown | DERIVED. Named for the 289 but never sent by the official app. open289 does not use them; downloads go through pass-through queries. |
| `8D` | APPEND_DATA_TO_OUT_BUFFER | raw ASCII bytes of a meter command, no CR | none | VERIFIED. At most 17 command bytes per write. |
| `8E` | SEND_BUFFER_TO_INSTRUMENT | none | none | VERIFIED. |
| `8F` | GET_ADAPTER_VERSION | none | ASCII `01.00.15,` (microcontroller version, comma separated) | VERIFIED. |
| `90` | START_TEST | none | status byte | DERIVED. Starts a high-voltage insulation test on a 1550/1555. NEVER SEND. |
| `91` to `94` | STOP_TEST, SET_TEST_VOLTAGE, SET_TEST_FUNCTION, SET_TIME_LIMIT | various | various | DERIVED. 1550/1555 test control. NEVER SEND. |
| `95` | GET_MODEL_NAME | none | ASCII model name | VERIFIED. With a 289 attached the payload is empty (reply `03 95`). |

open289 allows only `80` to `83`, `8D`, `8E`, `8F` and `95` by default (`83` is allowed but never sent). `84` to `8C` need an explicit opt-in, and `90` to `94` are refused.

### 5.4 Write size limit

VERIFIED. A control-point write longer than 18 bytes is rejected. A pass-through command is therefore split into `8D` writes carrying at most 17 command bytes each (opcode plus 17 bytes is 18). Several `8D` writes accumulate in the adapter's outgoing buffer, in order.

### 5.5 The complete transaction

One meter command takes at least five round trips: `81`, `8D` (one or more), `8E`, `80`, `82` (one or more).

1. `81` clears the buffer. Reply `03 81`.
2. `8D <text>` appends the command text. Repeat for each 17-byte piece. Reply `03 8D` each time. Do not include a CR: the adapter appends it. VERIFIED: sending `ID` returns the identity, while sending `ID` followed by CR yields an extra reply `1` (syntax error) because the adapter's own CR is read as an empty second command.
3. `8E` sends the buffer to the meter. The adapter answers `01 8E` (in process), then `03 8E` only when the meter's complete reply has been captured. This means no polling of the buffer size is required.
4. `80` returns `03 80 <size:u32>`, the length of the meter's reply.
5. `82 <offset:u32> <length:u32>` reads data. The adapter answers `01 82`, pushes the bytes as notifications on `B6982919` (20 bytes each at MTU 23), then sends `03 82`. For replies longer than one read, repeat with increasing offsets. open289 reads at most 400 bytes per request.

Annotated example, VERIFIED (the command `QM`, primary measurement; `>` is written to the control point, `<` is a notification):

```
>CP  81                                     CLEAR
<CP  03 81                                  complete
>CP  8d 51 4d                               APPEND "QM"
<CP  03 8d                                  complete
>CP  8e                                     SEND to meter
<CP  01 8e                                  in process (meter is answering)
<CP  03 8e                                  complete: full reply is buffered
>CP  82 00 00 00 00 1b 00 00 00             READ offset 0, length 0x1b = 27
<CP  01 82                                  in process
<BUF 30 0d 35 2e 30 36 32 32 45 30 2c 56 44 43 2c 4e 4f 52 4d 41   "0\r5.0622E0,VDC,NORMA"
<BUF 4c 2c 4e 4f 4e 45 0d                                         "L,NONE\r"
<CP  03 82                                  complete
```

The buffer holds the meter's native reply `0\r5.0622E0,VDC,NORMAL,NONE\r`, 27 bytes. In practice the client asks for the size first (`80`), here `03 80 1b 00 00 00`, and uses it as the length.

Failure handling. If a reply times out, mark the link as out of step, wait 0.5 s, and discard stale control-point replies before the next command. The adapter also polls the meter itself for the live stream, and a pass-through command sometimes collides with that: the meter then answers with a syntax error or the adapter returns nothing. open289 retries read-only queries up to 2 times after a syntax error, a missing reply or adapter status 4/5. Collisions with the adapter's polling are noted in the implementation, but no stored capture contains one, so the retry path is not covered by the captures.

### 5.6 Buffer limits and batching

| Item | Result | Status |
|---|---|---|
| Single control-point write | at most 18 bytes | VERIFIED |
| Outgoing (to-meter) buffer | 87 bytes in total per send worked | VERIFIED |
| 181 bytes outgoing | no reply | VERIFIED |
| 373 bytes or more outgoing | the adapter hung until it was power-cycled | VERIFIED |
| Reply size | a 1,208-byte reply came back intact | VERIFIED |
| Several queries in one send | works; replies come back concatenated in order | VERIFIED |

open289 refuses any send larger than 87 bytes, because larger sends can hang the adapter.

Batching. Queries separated by a CR share one send; the adapter sends them to the meter in order and the replies are concatenated. In the verified recording (reading index 772), the first batch, `QSRR 772,0` to `QSRR 772,7`, was 87 bytes out and 1,208 bytes back. A batch holds as many commands as fit in 87 bytes, at most 8. Once sample numbers reach two digits, only 7 fit (83 bytes). Because every `QSRR` reply has a fixed length (151 bytes: `0` CR `#0`, 146 payload bytes, CR), the client splits the batch reply on that frame size and checks that each piece starts with `0\r#0`.

The open CLEAR question. It is not known whether `81` empties the outgoing buffer when bytes were appended but never sent. open289 therefore counts the bytes appended since the last successful send. After a send fails part-way, it refuses any new command that, added to those possibly leftover bytes, could exceed 87 bytes. The next successful send resets the count. The repository's probe script `tools/probe_clear.py` is designed to settle this on hardware (not yet run).

## 6. Fluke 287/289 serial protocol over the pass-through

The pass-through carries the meter's own serial protocol. On the meter side the link is 115200 baud, 8 data bits, no parity, 1 stop bit, no flow control (DERIVED from public sources; the adapter handles it, so a client never configures it).

### 6.1 Framing and acknowledgements

A command is a mnemonic with optional arguments, ending in CR (added by the adapter). Mnemonics are case-insensitive. The meter does not echo the command name.

The reply is `<ack> CR [payload CR]`. VERIFIED: the ack is one ASCII digit.

| Ack | Name | Meaning |
|---|---|---|
| `0` | OK | Success. A payload may follow. |
| `1` | SYNTAX_ERROR | The command was not understood. open289 retries read-only queries up to 2 times. Collisions with the adapter's polling are noted in the implementation, but no stored capture contains one, so the retry path is not covered by the captures. |
| `2` | EXECUTION_ERROR | The meter cannot do this now (6.9). VERIFIED. |
| `5` | NO_DATA | DERIVED. No data for the request (for example a saved item index that does not exist). |

ASCII payloads are comma-separated; strings may be quoted. Binary payloads start with the two bytes `#0` and end with CR. There is no length field. Because byte `0D` can occur inside binary data, a parser should use the count fields or fixed lengths of each layout instead of searching for CR.

### 6.2 ASCII commands

VERIFIED marks commands whose replies are in the hardware captures.

| Command | Example reply (after the ack line) | Meaning | Status |
|---|---|---|---|
| `ID` | `FLUKE 289,V1.71,12345678` | Model, firmware, serial | VERIFIED |
| `IM` | model, version, serial, and more | Long identify | DERIVED |
| `QM` | `5.0622E0,VDC,NORMAL,NONE` | Primary reading: value in base units, unit, state, attribute. Overload or invalid is `9.99999999E+37`. | VERIFIED |
| `QDDA` | `V_DC,NONE,AUTO,VDC,5,0,OFF,0.000,0,2,LIVE,5.0625,VDC,0,4,5,NORMAL,NONE,1793997982.873,PRIMARY,5.0625,VDC,0,4,5,NORMAL,NONE,1793997982.873` | Display state as text: primary function, secondary function, range mode, base unit, range, multiplier, lightning bolt, min/max start time, mode count and modes, number of readings, then readings (9 fields each: id, value, unit, multiplier, decimals, digits, state, attribute, timestamp) | VERIFIED |
| `QDDB` | binary, 34 + 30n bytes (6.5) | Same as QDDA in binary | VERIFIED |
| `QSLS` | `1,0,1,2` | Saved-item counts: recordings, min/max, peak, measurements | VERIFIED |
| `QSMR i` | binary (6.7) | Saved measurement i | VERIFIED |
| `QMMSI i` | binary (6.7) | Saved min/max session i | VERIFIED |
| `QPSI i` | binary (6.7) | Saved peak session i | VERIFIED |
| `QRSI i` | binary (6.7) | Recording session i | VERIFIED |
| `QSRR r,s` | binary, 146 bytes (6.8) | One record of a recording; r is the recording's reading index from QRSI, s the sample number | VERIFIED |
| `QSAVNAME n` | `Save` | Name of memory slot n (0 to 7) | VERIFIED |
| `QBL` | `FULL` | Meter battery level | VERIFIED |
| `QMEMLEVEL` | `OK` | Memory level (EXHAUSTED, SEVERE, WARNING, OK) | VERIFIED |
| `QMP clock` | `1793999685` | Meter clock, in seconds. Local wall-clock time encoded as an epoch, no timezone. | VERIFIED |
| `QMP prop`, `MP prop,val` | | Read or set a meter property (auto power-off, beeper, language, and so on) | DERIVED. Setting is not a query. |
| `QEMAP name` | `49,0,LIMBO,1,V_AC,...` | Enumeration table: count, then key,value pairs | DERIVED. Implemented but no capture is stored in the repository logs. |
| `QSN`, `QCCV`, `QCVN`, `QMPQ` | | Serial number, calibration counter and version, quoted-string properties | DERIVED |
| `QLCDBM offset` | gzip-compressed BMP screenshot in chunks | Screenshot | DERIVED. Untested. |
| `PRESS key[,HELD_DOWN]` | ack only | Simulate a key (6.10) | VERIFIED |
| `OFF` | ack only | Power the meter off immediately (6.11) | VERIFIED |
| `LEDT ON\|OFF` | ack only | Power LED | DERIVED |
| `CSD ALL\|MEASUREMENT\|MIN_MAX\|PEAK\|RECORDED` | ack | Clear saved data. Destructive. | DERIVED. Never sent. |
| `RMP`, `RI`, `DS` | ack | Reset properties, reset instrument, default setup. Destructive. | DERIVED. Never sent. |

### 6.3 Binary primitives

| Type | Encoding |
|---|---|
| u16, s16, u32 | Little-endian. |
| double | Eight bytes: two little-endian 32-bit words, high word first ("word-swapped"). For bytes b0 to b7, the big-endian IEEE-754 image is `b3 b2 b1 b0 b7 b6 b5 b4`. VERIFIED. |
| Time | POSIX seconds as a double, meter local time, no timezone. |
| Overload | Value `9.99999999e+37`. |
| String | ASCII after the last record, up to the final CR (saved names). |

Check value: bytes `00 00 14 40 00 00 00 00` decode to 5.0. A plain little-endian read gives about 5.3e-315, which is wrong.

### 6.4 Reading record (30 bytes)

VERIFIED.

| Offset | Size | Field |
|---|---|---|
| 0 | u16 | reading id (6.12) |
| 2 | double | value in base units |
| 10 | u16 | unit (6.12) |
| 12 | s16 | unit multiplier (power of ten of the SI prefix) |
| 14 | s16 | decimal places |
| 16 | s16 | display digits |
| 18 | u16 | state (6.12) |
| 20 | u16 | attribute (6.12) |
| 22 | double | timestamp |

Display value: `value / 10^multiplier`, printed with `decimals` digits. The reading `02 00 | cb 3f 14 40 c8 29 3a 92 | 01 00 | 00 00 | 04 00 | 05 00 | 02 00 | 00 00 | ...` is id 2 (PRIMARY), about 5.0623, unit VDC, multiplier 0, 4 decimals, 5 digits, state NORMAL, attribute NONE (a real QDDB capture).

### 6.5 QDDB layout (display state)

VERIFIED. Payload after `#0` is exactly `34 + 30n` bytes.

| Offset | Size | Field |
|---|---|---|
| 0 | u16 | primary function (qemap `primfunction`) |
| 2 | u16 | secondary function (qemap `secfunction`) |
| 4 | u16 | range mode (0 manual, 1 auto) |
| 6 | u16 | base unit (qemap `unit`) |
| 8 | double | maximum of the range, for example 5.0 or 500.0 |
| 16 | s16 | unit multiplier |
| 18 | u16 | lightning bolt (0 or 1) |
| 20 | double | min/max start time, 0 when not in min/max |
| 28 | u16 | mode bit mask (6.12) |
| 30 | u16 | unknown, observed 0 |
| 32 | u16 | number of readings n |
| 34 | 30 x n | reading records (6.4) |

Example, VERIFIED (shortened): `03 00 | 00 00 | 00 00 | 01 00 | 00 00 14 40 00 00 00 00 ...` is V DC (function 3), no secondary, manual range, unit VDC, range 5.0 (word-swapped double). With no mode set and no secondary value, n is 2 (LIVE, PRIMARY). A secondary display adds SECONDARY (V AC or V DC with Hz, crest factor, V AC over DC, V DC over AC, V AC LoZ, V AC+DC: n = 3). dB functions add DB_REF (V AC with dBV, V AC with dBm and Hz: n = 4). Temperature adds TEMP_OFFSET (n = 3). HOLD and AUTO HOLD only set the mask bit (n stays 2). REL adds REL_LIVE and REL_REFERENCE. MIN MAX adds MINIMUM, MAXIMUM and AVERAGE.

Modes such as HOLD, REL and MIN MAX appear as mask bits here and as extra readings (MINIMUM, MAXIMUM, AVERAGE, REL_LIVE, REL_REFERENCE) in the record list. This is the only way to see them (4.9).

### 6.6 Measurement header

QSMR, QMMSI, QPSI and QRSI contain the same 34-byte block as QDDB offsets 0 to 33 (function, secondary, range mode, unit, range, multiplier, bolt, time, modes, spare, count), followed by n readings and a name.

### 6.7 Saved sessions

All VERIFIED unless marked.

| Command | Header | Contents |
|---|---|---|
| `QSMR i` | 38 bytes: u32 sequence number, then the 34-byte block | n readings, then name |
| `QMMSI i`, `QPSI i` | 54 bytes: u32 sequence number, double start time (offset 4), double end time (offset 12), then the 34-byte block at offset 20 | n readings (observed order: PRIMARY, MINIMUM, MAXIMUM, AVERAGE), then name. For peak sessions the min and max use unit V or A (peak). |
| `QRSI i` | 78 bytes: u32 sequence number, double start (4), double end (12), double sample interval in seconds (20), double event threshold as a fraction (28), u32 reading index (36), u32 number of samples (40), then the 34-byte block at offset 44 | n readings, then name |

The reading index in QRSI is the first argument of QSRR; samples run from 0 to the sample count minus 1. The name is plain ASCII to the final CR. Saved items are numbered from 0 in the order reported by QSLS.

### 6.8 QSRR, one trend record

VERIFIED. Fixed 146-byte payload; on the wire the reply is `0` CR `#0` + 146 bytes + CR, 151 bytes in total.

| Offset | Size | Field |
|---|---|---|
| 0 | double | start time of the interval |
| 8 | double | end time |
| 16 | 30 | MAXIMUM reading (id 8) |
| 46 | 30 | MINIMUM reading (id 7) |
| 76 | 30 | AVERAGE reading (id 9). The value is a sum, not a mean. |
| 106 | u32 | number of samples n accumulated in that sum |
| 110 | 30 | PRIMARY reading (id 2), the value at the start of the record |
| 140 | u16 | record type (0 input, 1 interval) |
| 142 | u16 | stable flag (0 unstable, 1 stable) |
| 144 | u16 | transient state (0 none, 1 range up, 2 range down, 3 overload, 4 open thermocouple) |

The average-is-a-sum rule. The meter stores the sum of n samples in the AVERAGE reading. The true mean is `value / n` when n is not zero. open289 applies this division and validated it on a 72-record recording against the min and max values.

### 6.9 What returns EXECUTION_ERROR

VERIFIED. While a menu, result page or save dialog is open on the meter, all measurement queries (QM, QDDA, QDDB) and also QSLS return ack `2`. It also happens briefly (a few polls) during dial changes. After closing the page on the meter, or with a key press (6.10), queries work again. DERIVED: saved-data queries (QSMR, QMMSI, QPSI, QRSI, QSRR) were not sent while such a page was open (the listing stops at QSLS); expect ack 2 for them as well. DERIVED: public sources report `2` while the meter is in setup mode.

### 6.10 PRESS keys and the screen state machine

`PRESS <key>` simulates a press; `PRESS <key>,HELD_DOWN` a long press. The reply is only the ack (`0`). Keys (from the meter's buttonName table): `F1 F2 F3 F4 UP DOWN LEFT RIGHT HOLD MINMAX RANGE INFO BACKLIGHT ONOFF`. VERIFIED on hardware: HOLD, MINMAX, RANGE (also held), BACKLIGHT, F2, F4, and ONOFF followed by F1 (power-off). F3, UP, DOWN, LEFT, RIGHT and INFO were not exercised. DERIVED from public sources: the other press types in the meter's table are RELEASED and REPEATED. A press returns before the screen changes, so a client should wait about a second and then check the display state. open289 waits 1.0 s by default.

Effects verified on the 289 (V DC range 5, auto):

| Key | Effect |
|---|---|
| `HOLD` | Toggles the HOLD mode bit. |
| `RANGE` | The first press leaves auto-range and keeps the current range (AUTO 5 to MANUAL 5); each further press steps the range (MANUAL 5 to MANUAL 50). |
| `RANGE` held | Returns to auto-range. |
| `BACKLIGHT` | Cycles the backlight: off, bright, dim. No display-state change visible through queries. |
| `MINMAX` | Enters MIN MAX (mode bit MIN_MAX_AVG, min/max start time set). Pressing it again, held or not, stops it and opens the result page. Held is the same as pressed. |
| `ONOFF` | Shows a power-off confirmation (6.11). |

Screen state machine for min/max, VERIFIED. "Queries" means measurement queries.

| State | Queries | Keys | Next state |
|---|---|---|---|
| Measurement screen | OK | `MINMAX` | MIN MAX running |
| MIN MAX running | OK, mode MIN_MAX_AVG | `MINMAX` (or held) | Result page |
| Result page | EXECUTION_ERROR | `F1` restart; `F2` save; `MINMAX` or `F4` close | Running again; save page; measurement screen |
| Save page (opened from the result page with F2) | EXECUTION_ERROR | `F1` +name (not exercised); `F2` Save; `F4` Close | name entry (untested); saves and stays on the save page; measurement screen |

After `F2` (Save) and then `F4` on the save page, QSLS shows one more min/max entry (the min/max count went from 0 to 1 in the test), and queries work again. A backlight press on the save page changes nothing visible.

### 6.11 Power off

VERIFIED. Pressing `ONOFF` shows the message that turning the meter off will break communication; `F1` confirms (F1 OK, F4 cancel). The raw command `OFF` powers the meter off immediately, with no confirmation. The IR3000 then switches itself off. Nothing can switch either device back on remotely: use the meter dial and the adapter button.

### 6.12 Enumeration tables (qemap)

DERIVED. These tables come from public captures of 287 and 289 meters of several firmware generations, and match the layouts decoded from the hardware (for example function 3 = V_DC, unit 1 = VDC). A robust client can fetch them at runtime with `QEMAP <name>`.

| Table | Values |
|---|---|
| primfunction | 0 LIMBO, 1 V_AC, 2 MV_AC, 3 V_DC, 4 MV_DC, 5 V_AC_OVER_DC, 6 V_DC_OVER_AC, 7 V_AC_PLUS_DC, 8 MV_AC_OVER_DC, 9 MV_DC_OVER_AC, 10 MV_AC_PLUS_DC, 11 A_AC, 12 MA_AC, 13 UA_AC, 14 A_DC, 15 MA_DC, 16 UA_DC, 17 A_AC_OVER_DC, 18 A_DC_OVER_AC, 19 A_AC_PLUS_DC, 20 MA_AC_OVER_DC, 21 MA_DC_OVER_AC, 22 MA_AC_PLUS_DC, 23 UA_AC_OVER_DC, 24 UA_DC_OVER_AC, 25 UA_AC_PLUS_DC, 26 TEMPERATURE, 27 OHMS, 28 CONDUCTANCE, 29 CONTINUITY, 30 CAPACITANCE, 31 DIODE_TEST, 32 V_AC_LOZ, 33 OHMS_LOW, 34 to 48 calibration functions (CAL_*) |
| secfunction | 0 NONE, 1 HERTZ, 2 DUTY_CYCLE, 3 PULSE_WIDTH, 4 DBM, 5 DBV, 6 DBM_HERTZ, 7 DBV_HERTZ, 8 CREST_FACTOR, 9 PEAK_MIN_MAX |
| autorange | 0 MANUAL, 1 AUTO |
| unit | 0 NONE, 1 VDC, 2 VAC, 3 VAC_PLUS_DC, 4 V (peak), 5 ADC, 6 AAC, 7 AAC_PLUS_DC, 8 A (peak), 9 OHM, 10 SIE, 11 Hz, 12 S, 13 F, 14 CEL, 15 FAR, 16 PCT, 17 dB, 18 dBV, 19 dBm, 20 CREST_FACTOR |
| bolt | 0 OFF, 1 ON |
| mode (bit mask) | 1 AUTO_HOLD, 2 AUTO_SAVE, 4 HOLD, 8 LOW_PASS_FILTER, 16 MIN_MAX_AVG, 32 RECORD, 64 REL, 128 REL_PERCENT, 256 CALIBRATION |
| readingid | 1 LIVE, 2 PRIMARY, 3 SECONDARY, 4 REL_LIVE, 5 BARGRAPH, 7 MINIMUM, 8 MAXIMUM, 9 AVERAGE, 11 REL_REFERENCE, 12 DB_REF, 13 TEMP_OFFSET |
| state | 0 INACTIVE, 1 INVALID, 2 NORMAL, 3 BLANK, 4 DISCHARGE, 5 OL, 6 OL_MINUS, 7 OPEN_TC |
| attribute | 0 NONE, 1 OPEN_CIRCUIT, 2 SHORT_CIRCUIT, 3 GLITCH_CIRCUIT, 4 GOOD_DIODE, 5 LO_OHMS, 6 NEGATIVE_EDGE, 7 POSITIVE_EDGE, 8 HIGH_CURRENT |
| recordtype | 0 INPUT, 1 INTERVAL |
| isstableflag | 0 UNSTABLE, 1 STABLE |
| transientstate | 0 NON_T, 1 RANGE_UP, 2 RANGE_DOWN, 3 OVERLOAD, 4 OPEN_TC |
| buttonName | 606 UP, 607 DOWN, 608 LEFT, 609 RIGHT, 650 F1, 651 F2, 652 F3, 653 F4, 654 HOLD, 655 MINMAX, 656 RANGE, 657 INFO, 658 BACKLIGHT, 660 ONOFF |
| presstype | 0 PRESSED, 1 HELD_DOWN, 2 RELEASED, 3 REPEATED |
| sessionType (used by CSD) | 0 ALL, 1 RECORDED, 2 MIN_MAX, 3 PEAK, 4 MEASUREMENT |

Note that the live record (section 4) and the serial protocol use different numbering: the live unit code 2 is V DC, while the serial unit code 1 is VDC. Do not mix the tables.

## 7. Timing

| Operation | Time | Status |
|---|---|---|
| Live notifications | about 4 per second | VERIFIED |
| One pass-through command (clear, append, send, size, read) | about 1.3 s | VERIFIED: 72 unbatched QSRR (one pass-through each) took 98.1 s, 1.36 s each; QDDB snapshots in the survey were at least 1.17 s apart |
| One `QSRR` per round trip (recording download) | 1.36 s per record on a 72-record download | VERIFIED |
| Up to 8 `QSRR` per send (as many as fit in 87 bytes) | 0.44 s per record on the same recording | VERIFIED |

A full display snapshot (QDDB) is one pass-through, so display polling realistically runs at about once every 1.5 to 2 seconds (median 1.8 s between snapshots in the survey). Live readings are the only way to get faster updates, which is why open289 offers both: the live stream for real-time acquisition and logging, and pass-through for display state, saved data, recordings and key presses.

## 8. Safety summary

| Rule | Reason |
|---|---|
| Never write `B6982911`, `B6982912`, or anything in service `B6981804`. | Firmware updater and radio image paths. The updater reuses the opcode values of the adapter; `80` erases flash. A mistake can brick the adapter. |
| Never send adapter opcodes `90` to `94`. | They control a high-voltage insulation test on 1550/1555 testers. |
| Never send more than 87 bytes to the meter in one send. | 181 bytes got no reply; 373 bytes or more hung the adapter until power-cycled. |
| Pass only query commands by default (`ID`, `IM`, `Q...`). | `MP`, `CSD` (clears memory) and `RI` (reset) change or erase meter state. The exception is `PRESS`, restricted to the 14 listed keys. |
| Block `ONOFF` unless explicitly allowed. | Powering off breaks communication and cannot be undone remotely. The meter asks for confirmation. |
| Never accept non-ASCII command text. | The adapter forwards bytes as they are and the meter's reaction is undefined. |
| Treat `OFF` as destructive. | It powers the meter off at once, without asking. |
| Use one connection only. | The adapter accepts a single central. |

## 9. Open questions

| Question | Notes |
|---|---|
| Does CLEAR (`81`) empty the outgoing buffer? | Not verified. open289 counts possibly leftover bytes after a failed send. A probe script exists. |
| What exactly is the outgoing buffer capacity? | 87 bytes works, 181 gives no reply; the limit lies in between (not measured). |
| Maximum reply size and read-chunk size | 1,208 bytes verified; larger replies and larger single reads were not measured. |
| Parameters and reply formats of opcodes `84` to `8C` | Never used by the official app; may be model-specific macros. Status 2 or 5 is expected for wrong guesses. |
| Effect of `83` CANCEL and what `00` IDLE and `02` INCORRECT_MODEL look like | Never observed. |
| Do values other than `01` in the CNX ID work, and what do `B6982903`, `B6982904`, `B6982905` and `B698290E` do? | Untested. Not written. |
| Do live notifications need the CNX ID? | Live notifications were seen while the control point was in use; this was not separated out. |
| What does a read of the control point return? | VERIFIED: `00` on a fresh connection, and before arming, the last opcode byte written (`8f`, `95`, `80`). A read after arming was not examined. |
| Behaviour of the detached adapter (`FLUKE PC3000` model string) | From the app only. |
| Firmware-update characteristics | Intentionally never exercised. |
| 287 behaviour | Only a 289 was tested. The 287 uses the same protocol per public sources. |
| `QEMAP`, `IM`, `MP`, `QLCDBM`, `LEDT` against this hardware | Implemented or listed from public sources; not covered by the stored captures. |
| Framed link layer (DLE/STX with CRC-16/X-25) described for other Fluke software | Not used by the adapter in any test. |
