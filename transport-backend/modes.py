"""Mode constants and helpers for transport types.

Use integer codes internally for compact representation, but provide
helpers to convert to/from the original string names so external
interfaces remain stable.
"""
WALKING = 0
BUS = 1
TRAIN = 2

_INT_TO_NAME = {
    WALKING: "walking",
    BUS: "bus",
    TRAIN: "train",
}

_NAME_TO_INT = {v: k for k, v in _INT_TO_NAME.items()}


def int_to_name(code: int | None) -> str | None:
    if code is None:
        return None
    return _INT_TO_NAME.get(code, None)


def name_to_int(name: str | None) -> int | None:
    if name is None:
        return None
    return _NAME_TO_INT.get(name, None)


def all_transit_modes() -> set:
    """Return a set of integer codes for all transit (non-walking) modes."""
    return {BUS, TRAIN}
