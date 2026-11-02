# Export fonts

FFmpeg/libass is pointed at this folder (`fontsdir=`), so whatever a caption
style names must exist here. Anything else depends on the host's installed
fonts, which is how a caption renders correctly on a developer's Mac and as
empty boxes on a Linux server.

| File | Covers | Used by |
|---|---|---|
| `BebasNeue-Regular.ttf` | Latin | Bold preset, Latin text |
| `Poppins-Bold.ttf` | Latin + Devanagari | Clean and Karaoke presets; any preset when the caption contains Devanagari |
| `NotoSansDevanagari-Bold.ttf` | Devanagari (full block) | Deeper fallback for Devanagari characters Poppins does not cover |

## Why Poppins for Devanagari

Poppins was drawn as a Devanagari-and-Latin family, so it renders Hindi and
English in one typeface. That matters here because Indian creators code-mix
constantly — "बहुत good है" is one caption line, and a Devanagari-only font
would render the English half as boxes.

## Adding a language

Drop a font covering the script into this folder and add one entry to
`SCRIPT_FONTS` in `services/caption_burner.py`. Nothing else needs to change.

## Licences

All three are licensed under the SIL Open Font License 1.1. `OFL.txt` carries
the licence text; it applies to every font in this folder.
