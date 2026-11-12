"""Explicit source conventions. No broker calendar, local OS timezone or I/O."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from control_plane.contracts import utc

UTC=timezone.utc
LONDON=ZoneInfo('Europe/London')


def timestamp(value: str | datetime,convention: str,*,fold: int | None=None):
    value=datetime.fromisoformat(value) if isinstance(value,str) else value
    if not isinstance(value,datetime):
        raise ValueError('INVALID_TIMESTAMP')
    if fold is not None and (type(fold) is not int or fold not in (0,1)):
        raise ValueError('INVALID_FOLD')
    if convention in ('telegram','utc'):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('AWARE_TIMESTAMP_REQUIRED')
        return value.astimezone(UTC)
    if convention!='uk_local':
        raise ValueError('EXPLICIT_TIME_CONVENTION_REQUIRED')
    wall=value.replace(tzinfo=None)
    candidates={}
    for choice in (0,1):
        local=wall.replace(tzinfo=LONDON,fold=choice)
        instant=local.astimezone(UTC)
        if instant.astimezone(LONDON).replace(tzinfo=None)==wall:
            candidates[choice]=instant
    if not candidates:
        raise ValueError('NONEXISTENT_UK_TIME')
    # A supplied UTC offset can establish the overlap's meaning, but must be
    # consistent with London on that date. It is never used as a broker offset.
    if value.tzinfo is not None and value.utcoffset() is not None:
        instant=value.astimezone(UTC)
        if instant not in candidates.values() or (fold is not None and candidates[fold]!=instant):
            raise ValueError('UK_OFFSET_MISMATCH')
        return instant
    if len(set(candidates.values()))>1 and fold is None:
        raise ValueError('AMBIGUOUS_UK_TIME')
    return candidates[fold or 0]


@dataclass(frozen=True)
class RawBrokerEpoch:
    seconds: Decimal

    def __post_init__(self):
        if not isinstance(self.seconds,Decimal) or not self.seconds.is_finite() or self.seconds<=0:
            raise ValueError('INVALID_RAW_EPOCH')


@dataclass(frozen=True)
class OffsetEvidence:
    """Externally established, bounded interval; no default seasonal mapping."""
    seconds: int
    start_utc: datetime
    end_utc: datetime

    def __post_init__(self):
        utc(self.start_utc);utc(self.end_utc)
        if type(self.seconds) is not int or abs(self.seconds)>14*3600 or self.seconds%3600 or self.end_utc<=self.start_utc:
            raise ValueError('INVALID_OFFSET_EVIDENCE')


def broker_epoch_to_utc(raw: RawBrokerEpoch,evidence: OffsetEvidence):
    # The normalized output is a datetime, which this boundary refuses as input.
    if type(raw) is not RawBrokerEpoch or type(evidence) is not OffsetEvidence:
        raise ValueError('RAW_EPOCH_AND_VERIFIED_INTERVAL_REQUIRED')
    instant=datetime.fromtimestamp(float(raw.seconds-evidence.seconds),UTC)
    if not evidence.start_utc<=instant<evidence.end_utc:
        raise ValueError('UNVERIFIED_BROKER_INTERVAL')
    return instant
