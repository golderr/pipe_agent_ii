from __future__ import annotations

from fastapi import HTTPException


def raise_not_implemented(feature: str, *, phase: str = "C.a") -> None:
    raise HTTPException(
        status_code=501,
        detail={
            "message": f"{feature} is scaffolded but not implemented yet.",
            "phase": phase,
            "feature": feature,
        },
    )
