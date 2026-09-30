# X806 / A804F Replacement Configurator

An independent, open-source Windows configurator for the 8-key X806/A804F
macropad. It replaces the device's vendor utility for key bindings, macros,
lighting, report rate, and its three onboard profiles.

> [!WARNING]
> This is experimental software based on USB captures rather than vendor
> documentation. It writes directly to the device's configuration memory. Keep
> the original vendor configurator available for recovery and start by testing a
> simple binding. You use this software at your own risk.

This project is not affiliated with or endorsed by the device manufacturer.

## Download and run

The easiest option is the `X806-Configurator.exe` attached to a GitHub release.
It is a standalone Windows application and does not require Python.

To run from source instead:

```powershell
git clone <repository-url>
cd X806_Configurator_MVP
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install .
x806-configurator
```

You can also run `run_x806_mvp.bat`, which installs `pywinusb` if needed and
starts the application.

## Hardware identity

- VID `0x30FA`
- PID `0x1340`
- Product string `INSTANT USB GAMING MOUSE`
- Vendor collection `MI_01 / Col04`
- Feature Report ID `7`
- Feature report length `8`

## What works

- Three capture-verified onboard profiles: Game I, Game II, Game III
- Capture-matching complete configuration upload across all three profiles
- Eight physical buttons
- Standard USB HID keyboard keys
- Macro editor with:
  - live keyboard recording with captured press/release timing
  - key down
  - key up
  - tap
  - explicit delay
  - multi-select move/delete and Clear all
  - Ctrl+C / Ctrl+V / Ctrl+Shift+S / Win+R helpers
- Built-in shortcut presets: Copy, Paste, Cut, Undo, Redo, Select All, Find,
  Save, Close Window, Show Desktop, Lock PC, My Computer, Run dialog, and Calculator
- Left/right Ctrl, Shift, Alt, and Win modifier usages
- Grouped, double-click action assignment for keyboard, mouse, media, profile,
  DPI, lighting, and macro actions
- Direct keyboard capture: focus the Keyboard tab's capture area and press the
  desired physical key to assign it immediately
- The complete macro recorder and event editor lives directly in the Macro tab
- Launch app builder: paste a Windows Run command, URI, or executable path and
  the configurator generates Win+R, typing, and Enter automatically
- Type text builder: paste a string or multiple lines to type directly without
  opening the Windows Run dialog
- Launch app and Type text default to a memory-efficient 0 ms inter-key delay;
  longer delays consume one additional firmware token between every character
- Previous/next profile arrows (also Ctrl+Page Up / Ctrl+Page Down)
- Confirmed direct `Switch → Game II` action
- Capture-verified direct functions: left/middle/right mouse, double click, fire,
  DPI loop/+/−, switch to Game I/II/III, Previous/Next, Play/Pause,
  Volume+/Volume−, Mute, LED Loop, Media Player, Mail, Calculator, Browser,
  and Show Desktop
- Capture-verified global lighting modes: Cycle breath and Button response
- Per-color cycle toggles for Blue, Pink, Red, and Purple
- Six lighting cycle-time levels and 125/250/500/1000 Hz USB report rates
- JSON save/load
- Automatic migration of original MVP project files, validation, and save-time backups
- Profile copy/reset tools and complete-project validation
- Right-click any key to copy or paste its assignment, or swap it with another
  key; copied assignments can also be pasted into a different profile
- Automatic migration of edited legacy `Office` mappings into the real Game I bank
- US-layout text-to-macro helper (including shifted punctuation)
- Persistent last-upload snapshot and timestamped successful-upload history
- Confirmation before device writes and packet preview in the activity log
- Shared object-region capacity checking

## Verified memory map used

### Game I

- Primary function table: `0x0008` (9 action bytes)
- Descriptor base: `0x00A0`

### Game II

- Primary function table: `0x0050`
- Descriptor base: `0x01B8`

### Game III

- Primary function table: `0x0060` (9 action bytes)
- Descriptor base: `0x0244`

All three profiles use one globally packed extended-object region beginning at
`0x0400`. The supplied captures positively exercise the region through `0x0547`.
The configurator therefore compiles and uploads all three hardware profiles as a
single image, matching the vendor allocator.

Direct consumer-control descriptors also store the allocator's current address,
even though they do not consume object space. This pointer is generated dynamically
so application and media actions remain valid when they follow macros.

The global lighting mode is stored at `0x001C`. Controlled captures confirmed
`0x93` for Cycle breath and `0x9B` for Button response. The app writes only that
byte, preserving the adjacent vendor settings in the same eight-byte region.

The four cycle-color selectors are stored at `0x001D` as four two-bit fields.
The enabled state is `01`: Blue uses bits 1–0, Pink bits 3–2, Red bits 5–4,
and Purple bits 7–6. All four colors were isolated in controlled captures.

Lighting cycle time shares `0x001C`: the low three bits store the zero-based
level. Cycle breath uses `0x90–0x95`; Button response uses `0x98–0x9D`.
Levels 1 and 4 are capture-confirmed, with the intervening values extrapolated
from the contiguous field.

USB report interval is stored as `(interval_ms - 1)`: 125 Hz=`0x07`,
250 Hz=`0x03`, 500 Hz=`0x01`, and 1000 Hz=`0x00`. The 125 Hz and 500 Hz values
are capture-confirmed. The vendor duplicates this value in active descriptor
headers and six fixed auxiliary descriptor locations, which the configurator
updates together with the global byte at `0x001E`.

Every memory transaction is split into at most 8 bytes. After all blocks, the app
sends the vendor-captured `0x10` final-apply command and reset packet; omitting that
sequence leaves newly written mappings inactive.

The app refuses to overflow these observed regions.

## Physical-to-internal switch map

    physical 1 -> internal 7
    physical 2 -> internal 3
    physical 3 -> internal 2
    physical 4 -> internal 1
    physical 5 -> internal 9
    physical 6 -> internal 6
    physical 7 -> internal 5
    physical 8 -> internal 4

## Macro bytecode used

Controlled captures confirmed:

    81 04 = A down
    01 04 = A up
    81 05 = B down
    01 05 = B up

Timing uses 2 ms units. A delay-only token is:

    TT 00

with `TT <= 0x7F`. Long delays are broken into multiple delay tokens. The controlled 1000 ms sample compiled to:

    77 00
    7F 00
    7F 00
    7F 00

because `(0x77 + 0x7F + 0x7F + 0x7F) * 2 ms = 1000 ms`.

Macros are stored as:

    01 00
    <2-byte tokens...>
    00 00
    <zero padding to 8-byte boundary>

## Captured input reports

The configured application buttons produce ordinary HID input reports rather
than a vendor-specific runtime protocol. A controlled key-press capture confirmed:

- Media Player: Consumer usage `0x0183`
- Calculator: Consumer usage `0x0192`
- Browser/Home: Consumer usage `0x0223`
- Lock PC: Left Win + L
- Close Window: Left Alt + F4
- My Computer: Left Win + E
- Show Desktop: Left Win + D (the device's dedicated `0x27` action)

Mail's configuration descriptor contains Consumer usage `0x018A`; its press was
not present in the supplied input capture, but the binding itself is confirmed by
the vendor configuration write.

## Protocol research tools

Protocol research controls are intentionally kept out of the everyday GUI. A
console companion performs read-only device operations and offline analysis:

    py x806_probe.py --read
    py x806_probe.py --read --count 20 --interval 0.25
    py x806_probe.py --analyze capture.txt
    py x806_probe.py --compare baseline.pcapng changed.pcapng

To capture a controlled vendor Apply operation on this computer:

    py x806_probe.py --capture-vendor 30 --output captures/game_i_a.pcapng

During the 30-second window, select one vendor profile, change one key, and click
Apply. Repeat with only that key changed again; comparing the reconstructed blocks
reveals the profile bank without guessing.

For unresolved direct functions, capture a baseline Apply and a second Apply with
only one binding or lighting option changed. `--compare` reports the exact changed
controller addresses and byte values. The priority capture pairs are Game I switch,
Game III switch, Play/Pause, and one lighting-mode change.

The probe intentionally has no arbitrary-send command. Unknown packets should first
be learned from controlled vendor captures; configuration writes remain protected by
Dry Run and confirmation in the GUI.

## Upload history and recovery

After the device accepts the complete upload and final-apply command, the app saves
the normalized project and exact raw write plan under:

    %LOCALAPPDATA%\X806 Configurator\upload_history

`last_uploaded_configuration.json` always contains the most recent successful
upload. Timestamped JSON snapshots retain earlier uploads, and
`upload_history.jsonl` provides a compact index. Failed uploads and dry runs are not
recorded as successful configurations. Use **Tools → Open upload history folder**
or **Tools → Load last uploaded snapshot** to access the records.

Run the regression suite with:

    py -m unittest -v test_x806_configurator.py

## Intentional limitations

### Reading current mappings

The device does not expose a straightforward general flash read through the HID feature interface we have decoded, so the app uses its saved project as the source of truth.

The profile selector chooses which bank is edited; it does not switch the device's
currently running bank. Uploading rewrites all three banks but leaves the active
hardware profile unchanged. Use a mapped Switch → Game I/II/III action to activate
the desired profile. The vendor `config.ini` only stores the vendor UI's last
selection and is not treated as a live profile read from the device.

## Recommended first test

1. Start with the profile currently active in the vendor configuration.
2. Change one key to an ordinary letter.
3. Click **Apply all profiles** and review the confirmation.
4. Upload the complete hardware configuration.
5. Verify that key.
6. Then test a small macro such as Ctrl+C.

## Development

Run the regression suite:

```powershell
py -m unittest -v
```

Build a standalone Windows executable in `dist`:

```powershell
.\build_windows.ps1
```

The GitHub Actions workflow runs the tests and creates the same Windows artifact
on every push. Pushing a tag such as `v0.1.0` creates a GitHub release and attaches
the executable automatically.

## License

MIT. See [LICENSE](LICENSE).
