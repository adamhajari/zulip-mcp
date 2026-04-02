import os
from pathlib import Path

import yaml


DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config.yaml"


def _resolve_path(config_path: Path | None = None) -> Path:
    return config_path or Path(os.environ.get("ZULIP_MCP_CONFIG", DEFAULT_CONFIG_PATH))


def save_interests(interests: list[str], config_path: Path | None = None) -> None:
    path = _resolve_path(config_path)
    with open(path) as f:
        raw = yaml.safe_load(f)
    raw["interests"] = interests
    with open(path, "w") as f:
        yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)


def load_config(config_path: Path | None = None) -> dict:
    path = _resolve_path(config_path)
    with open(path) as f:
        config = yaml.safe_load(f)

    # Resolve zuliprc path relative to the config file
    zuliprc = config.get("zuliprc", ".zuliprc")
    if not Path(zuliprc).is_absolute():
        config["zuliprc"] = str(path.parent / zuliprc)

    config.setdefault("defaults", {})
    config["defaults"].setdefault("hours_back", 24)
    config["defaults"].setdefault("channels", [])
    config["defaults"].setdefault("truncation_length", 500)

    return config
