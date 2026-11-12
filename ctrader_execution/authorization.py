"""Separate trading-authorization preparation; no URL, tokens or network flow."""
from dataclasses import dataclass
from pathlib import Path
from ctrader_readonly.config import private_path,SafetyError


@dataclass(frozen=True)
class TradingPreparation:
    private_config:Path
    private_token:Path
    view_token:Path

    def manifest(self):
        if private_path(self.private_token)==private_path(self.view_token):raise SafetyError('SEPARATE_TRADING_TOKEN_STORE_REQUIRED')
        private_path(self.private_config)
        return {'environment':'DEMO_ONLY','requested_scope_if_later_approved':'trading',
            'authorization_enabled':False,'execution_enabled':False,'separate_view_token_preserved':True,
            'required_checks':['EXPLICIT_OWNER_APPROVAL','BROKER_CAPABILITY_ACCEPTANCE',
                'ONE_USE_OAUTH_STATE','EXACT_LOOPBACK_CALLBACK','REMOTE_SCOPE_TRADE',
                'PINNED_DEMO_ACCOUNT_AND_DISPLAY_LOGIN','BROKER_CURRENCY_AND_ACCOUNT_MODE',
                'OWNER_ONLY_PRIVATE_STORAGE','SEPARATE_EXECUTION_OWNER']}

    def authorize(self,*args,**kwargs):
        raise SafetyError('EXPLICIT_TRADING_AUTHORIZATION_APPROVAL_REQUIRED')
