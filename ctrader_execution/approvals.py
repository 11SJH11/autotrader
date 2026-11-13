"""Owner-only, role-specific approvals. No implicit authorization from config flags."""
from dataclasses import asdict
from decimal import Decimal as D
import hashlib
import json
import time
from pathlib import Path
from ctrader_readonly.config import Config,SafetyError,private_path,private_read,private_write


RISK_KEYS=('stop_distance','target_distance','risk_cap','margin_cap','slippage_buffer','fee_buffer','max_spread','stop_floor')


def profile(config):
    if (config.discovery_only or config.demo_account_id is None or config.expected_demo_login is None
        or config.expected_broker!='Pepperstone - Europe' or config.expected_broker_name!='pepperstoneuk'
        or config.expected_currency!='USD' or config.expected_account_mode!='HEDGED'):
        raise SafetyError('PINNED_RAZOR_USD_PROFILE_REQUIRED')
    value=[config.demo_account_id,config.expected_demo_login,config.expected_broker,
        config.expected_broker_name,config.expected_currency,config.expected_account_mode,config.client_id]
    return hashlib.sha256(json.dumps(value,separators=(',',':')).encode()).hexdigest()


def paths(config_path):
    root=private_path(config_path).parent
    return {name:root/filename for name,filename in {
        'baseline':'baseline-confirmed.json','pending':'baseline-pending.json',
        'oauth':'trading-oauth-approval.json','token':'trading-token.json',
        'test':'first-test-approval.json','journal':'first-test.sqlite'}.items()}


def number(value,*,zero=False):
    try:result=D(str(value))
    except Exception:raise SafetyError('INVALID_APPROVAL_NUMBER') from None
    if not result.is_finite() or result<0 or (not zero and result==0):raise SafetyError('INVALID_APPROVAL_NUMBER')
    return result


def require_role(value,config,role,now=None,*,management=False):
    now=time.time() if now is None else now
    if value.get('role')!=role or value.get('profile')!=profile(config) or value.get('approved') is not True:
        raise SafetyError('EXPLICIT_OWNER_APPROVAL_REQUIRED')
    start=value.get('issued_at');end=value.get('management_until' if management else 'expires_at')
    if type(start) not in (int,float) or type(end) not in (int,float) or not start<=now<end:
        raise SafetyError('OWNER_APPROVAL_EXPIRED')
    if role=='OAUTH_ONLY' and end-start>900:raise SafetyError('APPROVAL_DURATION_TOO_LONG')
    if role=='ONE_TEST' and (value.get('expires_at',0)-start>600 or value.get('management_until',0)-start>3600):
        raise SafetyError('APPROVAL_DURATION_TOO_LONG')
    if not isinstance(value.get('id'),str) or len(value['id'])!=32:raise SafetyError('INVALID_APPROVAL_ID')
    return value


def load_approval(config_path,role,now=None,*,management=False):
    config=Config.load(config_path);p=paths(config_path)
    return require_role(private_read(p['oauth' if role=='OAUTH_ONLY' else 'test']),config,role,now,management=management)


def require_baseline(value,config,now=None):
    now=time.time() if now is None else now
    if value.get('profile')!=profile(config) or value.get('confirmed') is not True:
        raise SafetyError('LOCAL_BASELINE_CONFIRMATION_REQUIRED')
    at=value.get('confirmed_at')
    if type(at) not in (int,float) or not 0<=now-at<=86400:raise SafetyError('BASELINE_CONFIRMATION_STALE')
    number(value.get('balance'),zero=True);number(value.get('intended_balance'))
    return value


def validate_test(value,config,now=None,*,management=False):
    require_role(value,config,'ONE_TEST',now,management=management)
    if value.get('raw_volume')!=100 or value.get('entry_limit')!=1 or value.get('side') not in ('BUY','SELL') or value.get('order_type') not in ('MARKET','LIMIT'):
        raise SafetyError('BOUNDED_MINIMUM_TEST_REQUIRED')
    if value.get('emergency_close') is not True or value.get('cancel_remainder') is not True:
        raise SafetyError('EMERGENCY_RESPONSE_APPROVAL_REQUIRED')
    if value.get('stop_uncertainty_acknowledged') is not True:raise SafetyError('STOP_CONSTRAINT_CONFIRMATION_REQUIRED')
    allowed=value.get('allowed_ops')
    if not isinstance(allowed,list) or any(type(k) is not int or k not in (2106,2108,2109,2110,2111) for k in allowed) or not {2106,2108,2111}<=set(allowed):raise SafetyError('EXPLICIT_OPERATION_APPROVAL_REQUIRED')
    canonical=private_path(config.token_path).parent/'first-test.sqlite'
    if 'allocation_id' in value:
        from .allocations import validate_allocation
        validate_allocation(config,value)
    elif value.get('journal_path')!=str(canonical):raise SafetyError('CANONICAL_FIRST_TEST_JOURNAL_REQUIRED')
    number(value.get('confirmed_balance'),zero=True);number(value.get('intended_balance'))
    for key in RISK_KEYS:number(value.get(key),zero=key in ('slippage_buffer','fee_buffer','stop_floor'))
    ui=value.get('ui',{})
    if ui.get('same_account_confirmed') is not True or ui.get('commission_basis_confirmed') is not True:
        raise SafetyError('MATCHING_UI_SPEC_CONFIRMATION_REQUIRED')
    for key in ('minimum_lots','maximum_lots','step_lots','units_per_lot','commission_roundtrip_per_lot'):number(ui.get(key),zero=key=='commission_roundtrip_per_lot')
    if number(ui['minimum_lots'])!=D('.01') or number(ui['units_per_lot'])!=100:raise SafetyError('RAZOR_MINIMUM_CONTRACT_MISMATCH')
    if 'renewal_receipt' in value:
        from .renewal import require_receipt
        require_receipt(value,config)
    return value


def local_confirmation(message,expected):
    # Explicitly a controlling terminal, never API/stdout logs or a pipeline.
    import getpass
    try:
        # r+ requests a seekable buffered reader/writer; terminals cannot seek.
        # This stream only displays the prompt; getpass opens the TTY for input.
        with open('/dev/tty','w') as terminal:
            terminal.write(message+'\n');terminal.flush()
            answer=getpass.getpass('Type the requested confirmation (input hidden): ',stream=terminal)
    except (OSError,EOFError):raise SafetyError('LOCAL_OWNER_TTY_REQUIRED') from None
    if answer!=expected:raise SafetyError('LOCAL_CONFIRMATION_NOT_ACCEPTED')
