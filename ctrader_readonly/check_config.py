"""Sanitized private configuration preflight. Never displays configured values."""
import argparse
import json
from urllib.parse import urlsplit
from .config import Config,SafetyError,private_read


def check(path):
    result={'private_file_checks':'NOT_CHECKED','fields':{},'ready_for_oauth':False,
        'broker_identity':'CONFIGURED_NOT_VERIFIED','demo_environment':'NOT_CONNECTED'}
    try:data=private_read(path)
    except Exception as exc:
        result['blockers']=[str(exc) if isinstance(exc,SafetyError) else 'PRIVATE_FILE_UNAVAILABLE']
        return result
    result['private_file_checks']='PASS'
    missing=[]
    for name in ('client_id','client_secret','expected_broker','redirect_uri','token_path'):
        value=data.get(name)
        valid=isinstance(value,str) and bool(value.strip()) and not any(marker in value.upper() for marker in ('ENTER_','REPLACE_','YOUR_','PLACEHOLDER','CHANGEME'))
        result['fields'][name]='PRESENT_NOT_DISPLAYED' if valid else 'MISSING_OR_INVALID'
        if not valid:missing.append('MISSING_'+name.upper())
    account=data.get('demo_account_id')
    valid=type(account) is int and account>0
    result['fields']['demo_account_id']='PRESENT_NOT_DISPLAYED' if valid else 'MISSING_OR_INVALID'
    if not valid:missing.append('DEMO_ACCOUNT_ALLOWLIST_REQUIRED')
    result['application_approved']=data.get('application_approved') is True
    if not result['application_approved']:missing.append('APPLICATION_APPROVAL_REQUIRED')
    result['scope_view_only']=data.get('scope','accounts')=='accounts'
    if not result['scope_view_only']:missing.append('VIEW_SCOPE_REQUIRED')
    try:
        url=urlsplit(data.get('redirect_uri',''))
        redirect_ok=url.scheme=='http' and url.hostname=='127.0.0.1' and bool(url.port) and url.path=='/oauth/callback' and not(url.query or url.fragment or url.username or url.password)
    except Exception:redirect_ok=False
    result['redirect_uri_local_shape_valid']=bool(redirect_ok)
    result['redirect_registration']='REQUIRES_PROVIDER_CONFIRMATION'
    if not redirect_ok:missing.append('LOOPBACK_CALLBACK_REQUIRED')
    if not missing:
        try:config=Config.load(path);config.require_approved()
        except Exception as exc:missing.append(str(exc) if isinstance(exc,SafetyError) else 'INVALID_CONFIGURATION')
    result['blockers']=sorted(set(missing));result['ready_for_oauth']=not missing
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True)
    print(json.dumps(check(parser.parse_args().config),indent=2))

if __name__=='__main__':main()
