"""Audit-only clock oracles; no production normalization policy is changed."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

UK=ZoneInfo('Europe/London')
UTC=timezone.utc


def london_wall_to_utc(value,fold=None):
    """Interpret group wall time with explicit overlap choice and roundtrip checks."""
    wall=datetime.fromisoformat(value)
    if wall.tzinfo is not None:
        raise ValueError('Expected naive UK wall time')
    candidates=[]
    for choice in (0,1):
        local=wall.replace(tzinfo=UK,fold=choice)
        utc=local.astimezone(UTC)
        if utc.astimezone(UK).replace(tzinfo=None)==wall and utc not in candidates:
            candidates.append(utc)
    if not candidates:
        raise ValueError('Nonexistent UK local time')
    if len(candidates)>1 and fold not in (0,1):
        raise ValueError('Ambiguous UK local time')
    return wall.replace(tzinfo=UK,fold=fold or 0).astimezone(UTC)
