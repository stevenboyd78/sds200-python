# Project visual assets

This directory contains the project branding and reproducible Textual TUI
screenshots.

## Branding

The branding set uses a generic neon green scanner display with no agency,
location, talkgroup, frequency, or channel references.

- `sds200-python-logo.svg` — primary horizontal vector logo
- <img src="sds200-python-logo.svg">
- `sds200-python-icon.svg` — square vector icon
- <img src="sds200-python-icon.svg">
- `sds200-python-logo-4k.png` — 4800×1200 transparent PNG
- <img src="sds200-python-logo-4k.png">
- `sds200-python-icon-2048.png` — 2048×2048 transparent PNG
- <img src="sds200-python-icon-2048.png">
- `sds200-python-wallpaper-1080p.png` — 1920×1080 wallpaper
- <img src="sds200-python-wallpaper-1080p.png">
- `sds200-python-wallpaper-4k.png` — 3840×2160 wallpaper
- <img src="sds200-python-wallpaper-4k.png">

## Textual TUI screenshots

The `screenshots/` directory contains native SVG exports from the real Textual
application:

- `screenshots/tui-overview.svg` — wide operational view with an active recording;
- `screenshots/tui-recordings.svg` — recording-library view;
- `screenshots/tui-compact.svg` — compact terminal layout.

All scanner names, departments, sites, channels, frequencies, endpoints, logs,
recordings, and timestamps shown in these images are fictional demonstration
data.

The generator creates temporary WAV files and does not require scanner hardware,
network access, PortAudio, or a display server.

Regenerate the screenshots from the repository root:

    python -m pip install -e ".[dev]"
    python scripts/generate_tui_screenshots.py

Do not edit the generated SVG files manually. Update the generator and regenerate
all screenshots together so the documented interface remains reproducible.
