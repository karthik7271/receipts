"""Load config/settings.yaml (flat `key: value` pairs, no dependencies)."""

from pathlib import Path

SETTINGS_FILE = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


def load_settings() -> dict:
    out = {}
    for line in SETTINGS_FILE.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if ":" in line:
            key, value = (p.strip() for p in line.split(":", 1))
            try:
                out[key] = float(value) if "." in value else int(value)
            except ValueError:
                out[key] = value
    return out
