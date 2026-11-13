"""Approval/preflight diagnostics, with synthetic private state and no network."""
import io
import json
import time
from dataclasses import replace
from unittest.mock import patch

import pytest
from test_phase31 import native,private_dir,ready
from ctrader_execution import harness
from ctrader_execution.approvals import paths,profile
from ctrader_readonly.config import SafetyError,private_write,private_read


def setup(native,private_dir,monkeypatch):
    c,clock,a,b,w,client,token,j=native
    ready(native);j.close();j.path.unlink()
    cp=private_dir/'config.json';p=paths(cp)
    private_write(p['baseline'],b)
    monkeypatch.setattr(harness.Config,'load',lambda *args,**kwargs:c)
    def open_view(*args):
        client.authorize(token)
        return client
    monkeypatch.setattr(harness,'view_reader',open_view)
    monkeypatch.setattr(harness,'stopped_controls',lambda *args:None)
    monkeypatch.setattr(harness.time,'time',clock.wall)
    return cp,p


def answer(monkeypatch,text=None,exc=None):
    import getpass
    def get(*args,**kwargs):
        if exc:raise exc
        return text
    monkeypatch.setattr(getpass,'getpass',get)
    # Only the prompt stream is patched, not private file operations.
    return patch('builtins.open',lambda *args,**kwargs:io.StringIO())


@pytest.mark.parametrize('text,code',[('', 'UI_INPUT_EMPTY'),('  ','UI_INPUT_EMPTY'),('0.01,50','UI_INPUT_FIELD_COUNT'),('.01,50,,100,7,0','UI_INPUT_MISSING_VALUE'),('.01,50,.01,100,unknown,0','UI_INPUT_INVALID_NUMBER'),('.01,50,.01,100,NaN,0','UI_INPUT_INVALID_NUMBER'),('.01,50,.01,100,7,-1','UI_INPUT_INVALID_NUMBER')])
def test_input_errors_are_specific_and_redacted(monkeypatch,text,code):
    with answer(monkeypatch,text),pytest.raises(SafetyError,match=code):harness.read_ui_facts()


def test_eof_and_valid_input(monkeypatch):
    with answer(monkeypatch,exc=EOFError()),pytest.raises(SafetyError,match='UI_INPUT_EOF'):harness.read_ui_facts()
    with answer(monkeypatch,'0.01, 50, 0.01, 100, 7, 0'):
        ui,floor=harness.read_ui_facts()
    assert ui['commission_roundtrip_per_lot']=='7' and floor=='0'
    assert 'commission_basis_confirmed' not in ui


def facts():return {'minimum_lots':'.01','maximum_lots':'50','step_lots':'.01','units_per_lot':'100','commission_roundtrip_per_lot':'7'},'0'


@pytest.mark.parametrize('idle',[12,35])
def test_human_delay_refreshes_instead_of_expiring_and_saves_only_after_consent(native,private_dir,monkeypatch,capsys,idle):
    cp,p=setup(native,private_dir,monkeypatch);seen=[]
    def delayed():
        assert not native[5]._connected
        native[1].sleep(idle)
        # Old socket traffic must never be reused for the new session.
        native[4].inbox.append(native[4].mock.quote(timestamp=int((native[1].wall()-idle)*1000)))
        return facts()
    monkeypatch.setattr(harness,'read_ui_facts',delayed)
    original=harness.market_state
    def market(client):seen.append(native[1].wall());return original(client)
    monkeypatch.setattr(harness,'market_state',market)
    def consent(message,expected):
        assert not p['test'].exists();seen.append('consent')
    monkeypatch.setattr(harness,'local_confirmation',consent)
    assert harness.main(['approve-test','--config',str(cp)])==0
    assert len([x for x in seen if isinstance(x,float)])==2
    assert seen.count('consent')==2
    saved=private_read(p['test']);assert saved['approved'] is True
    assert not p['journal'].exists() and native[4].peer.calls==0
    assert p['test'].stat().st_mode&0o777==0o600
    assert saved['ui']['actual_rounding_posting_observed'] is False


@pytest.mark.parametrize('problem,code,stage',[('empty','UI_INPUT_EMPTY','UI_INPUT'),('eof','UI_INPUT_EOF','UI_INPUT'),('mismatch','API_UI_SPEC_MISMATCH','UI_API_RISK_VALIDATION'),('costs_unknown','LOCAL_CONFIRMATION_NOT_ACCEPTED','UI_EVIDENCE_CONFIRMATION'),('decline','LOCAL_CONFIRMATION_NOT_ACCEPTED','TEST_LOCAL_CONFIRMATION'),('permission','PRIVATE_FILE_ACCESS_DENIED','TEST_APPROVAL_PERSISTENCE')])
def test_approval_failure_never_persists_or_hints_at_unknown_trade(native,private_dir,monkeypatch,capsys,problem,code,stage):
    cp,p=setup(native,private_dir,monkeypatch)
    def fail(reason):raise SafetyError(reason)
    def input_facts():
        if problem in ('empty','eof'):fail(code)
        ui,floor=facts()
        if problem=='mismatch':ui['step_lots']='.02'
        return ui,floor
    monkeypatch.setattr(harness,'read_ui_facts',input_facts)
    calls=[]
    def consent(*args):
        calls.append(1)
        if problem=='costs_unknown' or (problem=='decline' and len(calls)==2):fail('LOCAL_CONFIRMATION_NOT_ACCEPTED')
    monkeypatch.setattr(harness,'local_confirmation',consent)
    if problem=='permission':
        def denied(*args):raise PermissionError('PRIVATE_UNPRINTABLE_DETAIL')
        monkeypatch.setattr(harness,'private_write',denied)
    assert harness.main(['approve-test','--config',str(cp)])==1
    out=capsys.readouterr().out;value=json.loads(out)
    assert value['code']==code and value['stage']==stage and value['no_order_attempted']
    assert all(x not in out for x in ('7654321','PRIVATE_UNPRINTABLE_DETAIL','resend','UNKNOWN'))
    assert not p['test'].exists() and not p['journal'].exists()


def test_preflight_absent_approval_has_no_network_and_no_writes(native,private_dir,monkeypatch,capsys):
    cp,p=setup(native,private_dir,monkeypatch)
    def prohibited(*args):pytest.fail('Missing approval should not contact broker')
    monkeypatch.setattr(harness,'view_reader',prohibited)
    assert harness.main(['preflight','--config',str(cp)])==1
    value=json.loads(capsys.readouterr().out)
    assert value['state']=='AWAITING_TEST_APPROVAL' and value['code']=='TEST_APPROVAL_MISSING'
    assert not p['test'].exists() and not p['journal'].exists()


@pytest.mark.parametrize('problem,code',[('expired','OWNER_APPROVAL_EXPIRED'),('permissions','PRIVATE_FILE_MODE_REQUIRED')])
def test_preflight_approval_failures(native,private_dir,monkeypatch,capsys,problem,code):
    cp,p=setup(native,private_dir,monkeypatch);a=native[2]
    if problem=='expired':a['expires_at']=native[1].wall()-1
    private_write(p['test'],a)
    if problem=='permissions':p['test'].chmod(0o644)
    assert harness.main(['preflight','--config',str(cp)])==1
    value=json.loads(capsys.readouterr().out);assert value['code']==code
    assert not p['journal'].exists()


def test_missing_approval_does_not_hide_or_change_uncertain_history(native,private_dir,monkeypatch,capsys):
    from ctrader_execution.ledger import Journal
    cp,p=setup(native,private_dir,monkeypatch)
    j=Journal(p['journal'],profile(native[0]),native[2]['id'])
    try:
        j.claim('synthetic-entry',2106,{})
        before=p['journal'].read_bytes()
        assert harness.main(['preflight','--config',str(cp)])==1
        value=json.loads(capsys.readouterr().out)
        assert value['state']=='UNKNOWN' and value['code']=='TEST_APPROVAL_MISSING'
        assert p['journal'].read_bytes()==before and j.entry()['state']=='SUBMITTING'
    finally:j.close()


@pytest.mark.parametrize('reason',['STALE_QUOTE','HEARTBEAT_TIMEOUT','SNAPSHOT_CHANGED_RETRY','RESPONSE_ACCOUNT_MISMATCH'])
def test_post_input_refresh_reports_exact_safe_reason(native,private_dir,monkeypatch,capsys,reason):
    cp,p=setup(native,private_dir,monkeypatch);original=harness.market_state;calls=[]
    def market(client):
        calls.append(1)
        if len(calls)==2:raise SafetyError(reason)
        return original(client)
    monkeypatch.setattr(harness,'market_state',market)
    monkeypatch.setattr(harness,'read_ui_facts',facts)
    messages=[]
    monkeypatch.setattr(harness,'local_confirmation',lambda message,expected:messages.append(message))
    assert harness.main(['approve-test','--config',str(cp)])==1
    value=json.loads(capsys.readouterr().out)
    assert value['stage']=='READ_ONLY_MARKET_REFRESH' and value['code']==reason
    assert 'Actual broker rounding/posting are UNOBSERVED' in messages[0]
    assert not p['test'].exists() and not p['journal'].exists()


def test_reader_keeps_rejecting_old_queued_quotes(native):
    from ctrader_execution.harness import market_state
    client=ready(native);market_state(client)
    quote=native[4].mock.quote()
    native[1].sleep(12);native[4].inbox.insert(0,quote)
    with pytest.raises(SafetyError,match='STALE_QUOTE'):market_state(client)
    assert not client._connected
