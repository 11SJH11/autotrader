"""Renewal uses only synthetic credentials/journals and read-side fake sockets."""
import copy
import json
import sqlite3

import pytest
from test_phase31 import native,private_dir
from ctrader_execution.approvals import paths,profile,validate_test
from ctrader_execution.ledger import Journal
from ctrader_execution import harness,renewal
from ctrader_readonly.config import SafetyError,private_read,private_write


@pytest.fixture
def expired(native,private_dir,monkeypatch):
    c,clock,a,b,w,client,token,j=native
    a['controls_database']='synthetic-controls'
    # Preserve prior pre-dispatch audit evidence, not a replacement journal.
    j.audit(None,'CLI_ATTENTION_REQUIRED');j.audit(None,'CLI_ATTENTION_REQUIRED')
    j.close();clock.sleep(601)
    b['confirmed_at']=clock.wall()
    cp=private_dir/'config.json';p=paths(cp)
    private_write(p['test'],a);private_write(p['baseline'],b)
    monkeypatch.setattr(harness.Config,'load',lambda *args,**kwargs:c)
    monkeypatch.setattr(harness.time,'time',clock.wall)
    # Synthetic wire returns TRADE for its client; never loads a real token.
    def view(*args):client.authorize(token);return client
    monkeypatch.setattr(harness,'view_reader',view)
    def metadata(name,result):
        if name=='symbol':result['symbol'][0].update(commissionType=2,preciseTradingCommissionRate=350000000,preciseMinCommission=0,minCommissionType=1,minCommissionAsset='USD')
        return result
    w.change=metadata
    monkeypatch.setattr(harness,'stopped_controls',lambda *args:None)
    return native,cp,p


def counts(p):
    with sqlite3.connect(p['journal'].as_uri()+'?mode=ro',uri=True) as db:
        return (db.execute('SELECT * FROM binding').fetchall(),db.execute('SELECT * FROM mutations').fetchall(),db.execute('SELECT * FROM audit').fetchall(),db.execute('SELECT * FROM observations').fetchall())


def invoke(cp,capsys):
    rc=harness.main(['renew-test','--config',str(cp)])
    output=capsys.readouterr().out
    assert all(word not in output for word in ('7654321','SYNTHETIC','RENEW ONE DEMO','PRIVATE_DETAIL'))
    return rc,output


def test_renew_expired_pre_dispatch_preserves_id_state_and_one_entry_lifetime(expired,monkeypatch,capsys):
    n,cp,p=expired;c,clock,a,b,w,client,token,j=n
    before=counts(p);inode=p['journal'].stat().st_ino;consents=[]
    def consent(*args):
        assert private_read(p['test'])==a
        consents.append(1);clock.sleep(35)
        assert not client._connected
    monkeypatch.setattr(harness,'local_confirmation',consent)
    assert invoke(cp,capsys)[0]==0
    new=private_read(p['test']);validate_test(new,c,clock.wall())
    assert new['id']==a['id'] and new['journal_path']==a['journal_path']
    assert new['expires_at']-new['issued_at']==600 and new['management_until']-new['issued_at']==3600
    assert len(consents)==1 and p['journal'].stat().st_ino==inode
    after=counts(p)
    assert after[0]==before[0] and after[1]==before[1]==[] and after[3]==before[3]==[]
    assert after[2][:-1]==before[2] and after[2][-1][-1]=='ENTRY_APPROVAL_RENEWED'
    with sqlite3.connect(p['journal']) as db:
        receipt=db.execute('SELECT old_approval,new_approval FROM approval_renewals').fetchone()
        assert json.loads(receipt[0])==a and json.loads(receipt[1])==new
    # The original binding opens; a different permit still cannot unlock it.
    owner=Journal(p['journal'],profile(c),a['id'],clock=clock.wall)
    try:
        owner.claim('only-entry',2106,{'synthetic':True})
        with pytest.raises(SafetyError,match='FIRST_ENTRY_ALREADY_CLAIMED'):owner.claim('second-entry',2106,{'synthetic':True})
    finally:owner.close()
    assert w.peer.calls==0


@pytest.mark.parametrize('state',['SUBMITTING','UNKNOWN','ACCEPTED','REJECTED','CLOSED'])
def test_any_claim_blocks_renewal_even_unsent_or_completed(expired,monkeypatch,capsys,state):
    n,cp,p=expired;c,clock,a,b,w,client,token,j=n
    with sqlite3.connect(p['journal']) as db:
        db.execute('INSERT INTO mutations VALUES(?,?,?,?,?,?,?,?,?)',('x','entry',2106,1,'{}','digest',state,int(state!='SUBMITTING'),clock.wall()))
    old=p['test'].read_bytes();before=counts(p)
    monkeypatch.setattr(harness,'local_confirmation',lambda *args:pytest.fail('Claimed test cannot ask for renewal consent'))
    rc,out=invoke(cp,capsys)
    assert rc==1 and json.loads(out)['code']=='RENEWAL_MUTATION_EVIDENCE_PRESENT'
    assert p['test'].read_bytes()==old and counts(p)==before and w.peer.calls==0


@pytest.mark.parametrize('problem,code',[
    ('mutation','RENEWAL_MUTATION_EVIDENCE_PRESENT'),('send_audit','RENEWAL_UNEXPLAINED_AUDIT_EVIDENCE'),
    ('profile','RENEWAL_BINDING_MISMATCH'),('approval','RENEWAL_BINDING_MISMATCH'),
    ('observations','RENEWAL_EXECUTION_EVIDENCE_PRESENT'),('missing','RENEWAL_JOURNAL_REQUIRED')])
def test_fail_closed_on_conflicts_and_attempt_evidence(expired,monkeypatch,capsys,problem,code):
    n,cp,p=expired
    with sqlite3.connect(p['journal']) as db:
        if problem=='mutation':db.execute('INSERT INTO mutations VALUES(?,?,?,?,?,?,?,?,?)',('y','cancel',2108,None,'{}','digest','UNKNOWN',1,0))
        elif problem=='send_audit':db.execute("INSERT INTO audit(at,code) VALUES(0,'SEND_FENCE_CONSUMED')")
        elif problem=='profile':db.execute("UPDATE binding SET profile='other'")
        elif problem=='approval':db.execute("UPDATE binding SET approval='other'")
        elif problem=='observations':db.execute("INSERT INTO observations(at,data) VALUES(0,'{}')")
    if problem=='missing':p['journal'].unlink()
    old=p['test'].read_bytes()
    rc,out=invoke(cp,capsys)
    assert rc==1 and json.loads(out)['code']==code and p['test'].read_bytes()==old
    if problem=='missing':assert not p['journal'].exists()


def test_owner_and_db_locks_block_renewal(expired):
    n,cp,p=expired;c=n[0];a=n[2]
    with renewal.RenewalGuard(p['journal'],profile(c),a['id']):
        with pytest.raises(SafetyError,match='RENEWAL_OWNER_BUSY'):renewal.RenewalGuard(p['journal'],profile(c),a['id'])
        with pytest.raises(BlockingIOError):Journal(p['journal'],profile(c),a['id'])
    with sqlite3.connect(p['journal']) as db:
        db.execute('BEGIN IMMEDIATE')
        with pytest.raises(sqlite3.OperationalError):renewal.RenewalGuard(p['journal'],profile(c),a['id'])


@pytest.mark.parametrize('problem,code',[('decline','LOCAL_CONFIRMATION_NOT_ACCEPTED'),('spread','TEST_SPREAD_LIMIT'),('margin','TEST_MARGIN_OR_FUNDS_LIMIT'),('not_expired','RENEWAL_NOT_REQUIRED'),('baseline','RENEWAL_BASELINE_CHANGE_FORBIDDEN')])
def test_new_consent_and_existing_policy_checks_required(expired,monkeypatch,capsys,problem,code):
    n,cp,p=expired;c,clock,a,b,w,client,token,j=n
    def consent(*args):
        if problem=='decline':raise SafetyError('LOCAL_CONFIRMATION_NOT_ACCEPTED')
    monkeypatch.setattr(harness,'local_confirmation',consent)
    if problem=='not_expired':
        clock.sleep(-600);b['confirmed_at']=clock.wall();private_write(p['baseline'],b)
    if problem=='baseline':
        b['intended_balance']='9000';private_write(p['baseline'],b)
    original=harness.market_state
    def market(client):
        value=original(client)
        if problem=='spread':value['quote']=type(value['quote'])(value['quote'].bid,value['quote'].bid+1,value['quote'].at)
        if problem=='margin':value['buy_margin']=1000
        return value
    monkeypatch.setattr(harness,'market_state',market)
    old=p['test'].read_bytes();before=counts(p)
    rc,out=invoke(cp,capsys)
    assert rc==1 and json.loads(out)['code']==code
    assert p['test'].read_bytes()==old and counts(p)==before and w.peer.calls==0


def test_committed_receipt_before_file_write_failure_keeps_expired_permit(expired,monkeypatch,capsys):
    n,cp,p=expired;c,clock,a,b,w,client,token,j=n
    monkeypatch.setattr(harness,'local_confirmation',lambda *args:None)
    def failed(*args):raise PermissionError('PRIVATE_DETAIL')
    monkeypatch.setattr(renewal,'private_write',failed)
    rc,out=invoke(cp,capsys)
    assert rc==1 and json.loads(out)['code']=='PRIVATE_FILE_ACCESS_DENIED'
    assert private_read(p['test'])==a
    with pytest.raises(SafetyError,match='OWNER_APPROVAL_EXPIRED'):validate_test(a,c,clock.wall())
    assert counts(p)[1]==[]


def test_unreceipted_or_modified_renewal_cannot_authorize(expired,monkeypatch,capsys):
    n,cp,p=expired;c,clock,a,b,w,client,token,j=n
    monkeypatch.setattr(harness,'local_confirmation',lambda *args:None)
    assert invoke(cp,capsys)[0]==0
    value=private_read(p['test']);value['risk_cap']='500'
    with pytest.raises(SafetyError,match='RENEWAL_RECEIPT_MISMATCH'):validate_test(value,c,clock.wall())
    value=private_read(p['test']);value['renewal_receipt']='0'*32
    with pytest.raises(SafetyError,match='RENEWAL_RECEIPT_MISMATCH'):validate_test(value,c,clock.wall())


def test_ordinary_approve_test_cannot_overwrite_existing_binding(expired,monkeypatch,capsys):
    n,cp,p=expired
    monkeypatch.setattr(harness,'local_confirmation',lambda *args:pytest.fail('Must use explicit renewal'))
    old=p['test'].read_bytes();before=counts(p)
    assert harness.main(['approve-test','--config',str(cp)])==1
    assert json.loads(capsys.readouterr().out)['code']=='RENEWAL_REQUIRED_FOR_EXISTING_JOURNAL'
    assert p['test'].read_bytes()==old and counts(p)==before


def test_renewed_permit_supports_read_only_preflight_and_cannot_renew_twice_while_current(expired,monkeypatch,capsys):
    n,cp,p=expired
    monkeypatch.setattr(harness,'local_confirmation',lambda *args:None)
    assert invoke(cp,capsys)[0]==0
    before=counts(p);permit=p['test'].read_bytes()
    assert harness.main(['preflight','--config',str(cp)])==0
    assert json.loads(capsys.readouterr().out)['preflight']=='PASS'
    assert counts(p)==before and p['test'].read_bytes()==permit and n[4].peer.calls==0
    rc,out=invoke(cp,capsys)
    assert rc==1 and json.loads(out)['code']=='RENEWAL_NOT_REQUIRED'
    assert counts(p)==before and p['test'].read_bytes()==permit


def test_receipted_permit_remains_valid_if_process_stops_after_atomic_publication(expired,monkeypatch,capsys):
    n,cp,p=expired;c,clock,a,b,w,client,token,j=n
    monkeypatch.setattr(harness,'local_confirmation',lambda *args:None)
    write=renewal.private_write
    def crash_after_replace(path,value):
        write(path,value)
        raise RuntimeError('SIMULATED_CRASH_AFTER_ATOMIC_REPLACE')
    monkeypatch.setattr(renewal,'private_write',crash_after_replace)
    assert invoke(cp,capsys)[0]==1
    new=private_read(p['test'])
    assert new['id']==a['id'] and validate_test(new,c,clock.wall())
    assert counts(p)[1]==[] and w.peer.calls==0


@pytest.mark.parametrize('missing',[True,False])
def test_commission_metadata_missing_or_changed_blocks_renewal(expired,monkeypatch,capsys,missing):
    n,cp,p=expired;w=n[4];original=w.change
    def change(name,result):
        result=original(name,result)
        if name=='symbol':
            if missing:result['symbol'][0].pop('preciseMinCommission')
            else:result['symbol'][0]['preciseMinCommission']=100000000
        return result
    w.change=change
    monkeypatch.setattr(harness,'local_confirmation',lambda *args:pytest.fail('Changed costs cannot renew'))
    before=counts(p);old=p['test'].read_bytes()
    rc,out=invoke(cp,capsys)
    assert rc==1 and json.loads(out)['code']==('RENEWAL_COMMISSION_EVIDENCE_MISSING' if missing else 'RENEWAL_COMMISSION_EVIDENCE_CHANGED')
    assert counts(p)==before and p['test'].read_bytes()==old
