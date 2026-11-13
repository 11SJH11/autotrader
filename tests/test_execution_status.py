"""Local status projections never authorize, connect or recover/write history."""
import hashlib
import json
import sqlite3

import pytest
from test_phase31 import native,private_dir
from ctrader_execution import harness
from ctrader_execution.approvals import paths,profile
from ctrader_execution.ledger import read_status
from ctrader_readonly.config import private_write


def setup_status(native,private_dir,monkeypatch):
    cp=private_dir/'config.json';p=paths(cp)
    monkeypatch.setattr(harness.Config,'load',lambda *args,**kwargs:native[0])
    def prohibited(*args,**kwargs):pytest.fail('Status must not connect or initialize an execution journal')
    monkeypatch.setattr(harness,'Journal',prohibited)
    monkeypatch.setattr(harness,'view_reader',prohibited)
    monkeypatch.setattr(harness,'TradeClient',prohibited)
    monkeypatch.setattr(harness.TradingToken,'load',prohibited)
    return cp,p


def status(cp,capsys):
    rc=harness.main(['status','--config',str(cp)])
    output=capsys.readouterr().out
    assert all(x not in output for x in ('7654321','SYNTHETIC','no blind resend','inspect private journal'))
    return rc,json.loads(output)


@pytest.mark.parametrize('oauth,approval,expected',[(False,False,'NOT_STARTED'),(True,False,'AWAITING_TEST_APPROVAL'),(True,True,'NOT_STARTED')])
def test_status_without_journal_or_baseline(native,private_dir,monkeypatch,capsys,oauth,approval,expected):
    cp,p=setup_status(native,private_dir,monkeypatch)
    native[-1].close();p['journal'].unlink()
    if oauth:private_write(p['token'],{'synthetic':'Contents must not be read or treated as remote authorization'})
    if approval:private_write(p['test'],{'expired':'Status does not need an active permit'})
    before={f.name:f.read_bytes() for f in private_dir.iterdir() if f.is_file()}
    rc,value=status(cp,capsys)
    assert rc==0 and value['state']==expected and value['journal']=='ABSENT'
    assert value['entry_claims']==0 and not value['execution_enabled']
    assert value['trading_token_store_present']==oauth
    assert before=={f.name:f.read_bytes() for f in private_dir.iterdir() if f.is_file()}
    assert not p['journal'].exists() and not p['baseline'].exists()


@pytest.mark.parametrize('stored,expected,proof',[('SUBMITTING','UNKNOWN',False),('UNKNOWN','UNKNOWN',False),('ACCEPTED','SUBMITTED',False),('REJECTED','REJECTED',False),('CLOSED','UNCONFIRMED',False),('CLOSED','EXECUTED',True)])
def test_status_preserves_durable_outcomes_without_approval(native,private_dir,monkeypatch,capsys,stored,expected,proof):
    cp,p=setup_status(native,private_dir,monkeypatch);j=native[-1]
    claim,_=j.claim('synthetic-entry',2106,{'synthetic':'payload'})
    if stored!='SUBMITTING':j.outcome(claim['id'],stored,'HISTORY_CONFIRMED_FLAT' if proof else 'SYNTHETIC_OUTCOME')
    # Missing or expired permits must never hide uncertain historical execution.
    if stored=='UNKNOWN':private_write(p['test'],{'expired':True})
    before=p['journal'].read_bytes()
    rc,value=status(cp,capsys)
    assert rc==0 and value['state']==expected and value['entry_claims']==1
    assert value['mutations'][0]['state']==stored
    assert p['journal'].read_bytes()==before
    assert j.entry()['state']==stored
    assert native[4].peer.calls==0


def test_uncertain_management_dominates_closed_entry(native,private_dir,monkeypatch,capsys):
    cp,p=setup_status(native,private_dir,monkeypatch);j=native[-1]
    entry,_=j.claim('entry',2106,{});j.outcome(entry['id'],'CLOSED','HISTORY_CONFIRMED_FLAT')
    j.claim('close',2111,{'positionId':123,'volume':100})
    assert status(cp,capsys)[1]['state']=='UNKNOWN'


@pytest.mark.parametrize('problem,code',[('corrupt','STATUS_JOURNAL_UNREADABLE'),('schema','STATUS_JOURNAL_SCHEMA_INVALID'),('profile','STATUS_JOURNAL_PROFILE_MISMATCH'),('permissions','PRIVATE_FILE_MODE_REQUIRED')])
def test_unreadable_history_never_reports_not_started_or_broker_unknown(native,private_dir,monkeypatch,capsys,problem,code):
    cp,p=setup_status(native,private_dir,monkeypatch);j=native[-1]
    if problem=='profile':j.db.execute("UPDATE binding SET profile='other'")
    j.close()
    if problem=='corrupt':p['journal'].write_bytes(b'SYNTHETIC_PRIVATE_CORRUPTION')
    elif problem=='schema':
        with sqlite3.connect(p['journal']) as db:db.execute('DROP TABLE mutations')
    elif problem=='permissions':p['journal'].chmod(0o644)
    before=p['journal'].read_bytes()
    rc,value=status(cp,capsys)
    assert rc==1 and value['state']=='UNAVAILABLE' and value['code']==code
    assert p['journal'].read_bytes()==before


def test_empty_database_distinct_from_absence(native,private_dir,monkeypatch,capsys):
    cp,p=setup_status(native,private_dir,monkeypatch);native[-1].close()
    p['journal'].write_bytes(b'')
    assert status(cp,capsys)[1]['journal']=='UNINITIALIZED'
    assert p['journal'].read_bytes()==b''


def test_database_lock_is_safe_unavailability(native,private_dir,monkeypatch,capsys):
    cp,p=setup_status(native,private_dir,monkeypatch);native[-1].db.execute('BEGIN EXCLUSIVE')
    try:
        rc,value=status(cp,capsys)
        assert rc==1 and value=={'state':'UNAVAILABLE','code':'STATUS_JOURNAL_BUSY','execution_enabled':False}
    finally:native[-1].db.rollback()
