from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class BusinessError(Exception):
    error_code: str
    message: str
    retry_after_seconds: Optional[int] = None
