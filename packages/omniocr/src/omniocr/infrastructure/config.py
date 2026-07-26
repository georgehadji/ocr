from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = "omniocr"
    desktop_mode: bool = True
    enable_vlm: bool = False
    enable_calamari: bool = False
