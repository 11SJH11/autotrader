"""Broker-native sizing preview; never promotes observation proposals to execution."""
from dataclasses import replace
from decimal import Decimal
from control_plane.contracts import TradeIntent
from control_plane.operations import entry_reason
from ctrader_readonly.config import SafetyError


def preview(proposal:TradeIntent,spec,controls):
    if not proposal.observation_only:raise SafetyError('OBSERVATION_PROPOSAL_REQUIRED')
    reason=entry_reason(controls,proposal.strategy_id)
    if reason:raise SafetyError(reason)
    if not spec.independently_verified:raise SafetyError('NATIVE_SPEC_NOT_VERIFIED')
    rows=[s for s in controls['strategies'] if s['strategy']==proposal.strategy_id]
    lots=Decimal(rows[0]['lots'])
    units=spec.units_for_lots(lots)
    # Original source ID, UTC publication time, age/TTL, setup/leg and policy
    # version survive. This is a proposal, never an entry approval.
    return replace(proposal,account_scope='RAZOR-DEMO',instrument='XAUUSD',quantity=units,
        campaign_id=proposal.campaign_id or proposal.setup_id,observation_only=True)
