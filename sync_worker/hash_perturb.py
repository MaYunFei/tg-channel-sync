from __future__ import annotations

import os
import uuid
from dataclasses import dataclass


@dataclass(slots=True)
class PerturbResult:
    path: str
    changed: bool
    reason: str


def perturb_clone_media(path: str, msg_type: str) -> PerturbResult:
    if msg_type in {"photo", "video"}:
        return _append_tail_bytes(path, msg_type)
    return PerturbResult(path=path, changed=False, reason="unsupported_type")


def _append_tail_bytes(path: str, msg_type: str) -> PerturbResult:
    marker = f"\nTGCS_FINGERPRINT_RESET:{msg_type}:{uuid.uuid4().hex}\n".encode("ascii")
    try:
        with open(path, "ab") as file_obj:
            file_obj.write(marker)
    except Exception as exc:
        return PerturbResult(path=path, changed=False, reason=f"append_error:{exc}")

    return PerturbResult(path=path, changed=True, reason="tail_bytes_appended")
