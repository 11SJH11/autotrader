"""Account-scoped capability allocations and native-child close lifecycles.

Invented identities and fake sockets only. No actual credentials or broker I/O.
"""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from test_phase31 import native,private_dir
from ctrader_execution import allocations,capabilities
from ctrader_execution.allocations import Registry,allocation_paths,closed_evidence
from ctrader_execution.approvals import paths,profile,validate_test
from ctrader_execution.ledger import Journal,AccountOwner,read_status
from ctrader_execution.harness import OneTest,market_state,prepare
from ctrader_readonly.config import SafetyError,private_write,private_read


@pytest.fixture
def capability(native,private_dir,monkeypatch):
    c,clock,a,b,w,client,token,j=native
    a['controls_database']='SYNTHETIC_CONTROLS'
    p=paths(private_dir/'config.json');private_write(p['test'],a);private_write(p['baseline'],b)
    source=json.loads(Path('tests/fixtures/razor_first_fill.json').read_text())
    legacy=source['closed'];cid=source['client_order_id']
    claim,_=j.claim('legacy-entry',2106,{},cid=cid);j.consume_send(cid,2106,{})
    j.outcome(cid,'ACCEPTED','CORRELATED_EXECUTION_EVENT');j.close()
    for order in legacy['orders']:order['utcLastUpdateTimestamp']=int(clock.wall()*1000)
    for deal in legacy['deals']:deal['executionTimestamp']=int(clock.wall()*1000)
    w.peer.orders={o['orderId']:copy.deepcopy(o) for o in legacy['orders']}
    w.peer.deals={d['dealId']:copy.deepcopy(d) for d in legacy['deals']}
    w.peer.positions={}
    def metadata(name,result):
        if name=='symbol':result['symbol'][0].update(commissionType=2,preciseTradingCommissionRate=350000000,preciseMinCommission=0,minCommissionType=1,minCommissionAsset='USD')
        return result
    w.change=metadata
    rawsend=w.peer.send
    def send(frame):
        result=rawsend(frame)
        if frame['payloadType']==2106:
            origin=next(o for o in w.peer.orders.values() if o.get('clientOrderId')==frame['clientMsgId'])
            if 'positionId' in origin:
                pid=origin['positionId'];childid=w.peer.next_id()
                w.peer.orders[childid]={'orderId':childid,'positionId':pid,'orderType':4,'orderStatus':1,
                    'clientOrderId':frame['clientMsgId'],'closingOrder':True,'executedVolume':0,
                    'tradeData':dict(origin['tradeData'],tradeSide=2)}
        if frame['payloadType']==2111:
            for o in w.peer.orders.values():
                if o.get('positionId')==frame['payload']['positionId'] and o['orderType']==4:o['orderStatus']=5
        for d in w.peer.deals.values():
            d.update(commission=-4,moneyDigits=2)
            if 'closePositionDetail' in d:d['closePositionDetail'].update(grossProfit=0,swap=0,moneyDigits=2,commission=-4)
        return result
    w.peer.send=send
    monkeypatch.setattr(capabilities,'stopped_controls',lambda *args:None)
    monkeypatch.setattr(capabilities.time,'time',clock.wall)
    monkeypatch.setattr(capabilities,'local_confirmation',lambda *args:None)
    monkeypatch.setattr(capabilities.Config,'load',lambda *args,**kwargs:c)
    # TradeClient fake authenticates only the invented synthetic trade grant;
    # production capabilities.view_reader remains the immutable VIEW-only Reader.
    def view(*args):
        client.authority=a
        client.authorize(token)
        return client
    monkeypatch.setattr(capabilities,'view_reader',view)
    return native,private_dir/'config.json',p


def allocate(capability,capsys):
    n,cp,p=capability
    rc=capabilities.main(['allocate','--config',str(cp)])
    output=capsys.readouterr().out
    assert rc==0,output
    return json.loads(output)['test_id']


def approve_for(capability,tid):
    n,cp,p=capability
    return private_read(allocation_paths(cp.parent,tid)['test'])


def test_allocate_preserves_original_and_blocks_overlap(capability,capsys):
    n,cp,p=capability;before=p['journal'].read_bytes();inode=p['journal'].stat().st_ino
    tid=allocate(capability,capsys)
    assert p['journal'].read_bytes()==before and p['journal'].stat().st_ino==inode
    approval=approve_for(capability,tid);validate_test(approval,n[0],n[1].wall())
    assert approval['allocation_id']==approval['id']==tid
    assert read_status(allocation_paths(cp.parent,tid)['journal'],profile(n[0]))['entry_claims']==0
    assert capabilities.main(['allocate','--config',str(cp)])==1
    assert json.loads(capsys.readouterr().out)['code']=='CAPABILITY_PREVIOUS_SESSION_UNRESOLVED'
    assert n[4].peer.calls==0


def test_allocation_requires_local_consent(capability,monkeypatch,capsys):
    def decline(*args):raise SafetyError('LOCAL_CONFIRMATION_NOT_ACCEPTED')
    monkeypatch.setattr(capabilities,'local_confirmation',decline)
    n,cp,p=capability;before=p['journal'].read_bytes()
    assert capabilities.main(['allocate','--config',str(cp)])==1
    assert json.loads(capsys.readouterr().out)['code']=='LOCAL_CONFIRMATION_NOT_ACCEPTED'
    assert list(cp.parent.glob('capability-????????????????????????????????.sqlite'))==[]
    assert p['journal'].read_bytes()==before


def test_account_owner_excludes_other_journals_and_registry(capability,capsys):
    n,cp,p=capability
    reg=Registry(n[0],create=True)
    try:
        with pytest.raises(BlockingIOError):Registry(n[0])
        with pytest.raises(BlockingIOError):Journal(cp.parent/'another.sqlite',profile(n[0]),'b'*32)
    finally:reg.close()


def test_full_native_child_roundtrip_and_automatic_ack_cost_proof(capability,capsys):
    n,cp,p=capability;c,clock,a,b,w,client,token,j=n
    tid=allocate(capability,capsys);approval=approve_for(capability,tid)
    reg=Registry(c);newjournal=Journal(allocation_paths(cp.parent,tid)['journal'],profile(c),tid,account_owner=reg.owner,clock=clock.wall)
    try:
        client.authority=approval
        result=OneTest(c,approval,b,client,newjournal,confirm=lambda *args:None,controls=lambda:None).run(token)
        assert result=='CLOSED_RECONCILED'
        report=reg.complete(tid,client,newjournal,result)
        assert report['automatic_close_verified'] and report['account_flat']
        assert report['costs']['commission_cost_usd']=='0.08'
        assert report['costs']['net_realized_pnl_usd']=='-0.08'
        assert sum(f['payloadType']==2106 for f in w.calls)==1
        assert sum(f['payloadType']==2111 for f in w.calls)==1
        reg.require_history()
    finally:newjournal.close();reg.close()
    # A new allocation requires new consent and is allowed only after success.
    next_id=allocate(capability,capsys);assert next_id!=tid
    assert capabilities.main(['preflight','--config',str(cp),'--test-id',next_id])==0
    assert json.loads(capsys.readouterr().out)['preflight']=='PASS'


@pytest.mark.parametrize('mode',['timeout_before','timeout_after','reject'])
def test_unknown_or_failed_entry_cannot_be_retried_as_new_allocation(capability,capsys,mode):
    n,cp,p=capability;c,clock,a,b,w,client,token,j=n
    tid=allocate(capability,capsys);approval=approve_for(capability,tid)
    reg=Registry(c);nj=Journal(allocation_paths(cp.parent,tid)['journal'],profile(c),tid,account_owner=reg.owner,clock=clock.wall)
    try:
        client.authority=approval;original=w.peer.send
        def send(request):
            w.peer.mode=mode if request['payloadType']==2106 else 'normal'
            return original(request)
        w.peer.send=send
        runner=OneTest(c,approval,b,client,nj,confirm=lambda *args:None,controls=lambda:None)
        try:
            result=runner.run(token)
            try:reg.complete(tid,client,nj,result)
            except SafetyError:reg.mark(tid,'FAILED')
        except SafetyError:reg.mark(tid,'FAILED')
        with pytest.raises(SafetyError,match='CAPABILITY_PREVIOUS_SESSION_UNRESOLVED'):reg.require_history()
        assert sum(f['payloadType']==2106 for f in w.calls)==1
    finally:nj.close();reg.close()
    assert capabilities.main(['allocate','--config',str(cp)])==1
    capsys.readouterr()


def test_immutable_approval_and_missing_history_block(capability,capsys):
    n,cp,p=capability;tid=allocate(capability,capsys)
    path=allocation_paths(cp.parent,tid)['test'];value=private_read(path);value['risk_cap']='500';private_write(path,value)
    with pytest.raises(SafetyError,match='CAPABILITY_APPROVAL_MISMATCH'):validate_test(value,n[0],n[1].wall())
    assert capabilities.main(['preflight','--config',str(cp),'--test-id',tid])==1
    assert json.loads(capsys.readouterr().out)['code']=='CAPABILITY_APPROVAL_MISMATCH'


def test_legacy_unknown_or_unspent_never_imported(capability,capsys):
    n,cp,p=capability
    with sqlite3.connect(p['journal']) as db:db.execute("UPDATE mutations SET state='UNKNOWN'")
    assert capabilities.main(['allocate','--config',str(cp)])==1
    assert json.loads(capsys.readouterr().out)['code']=='CAPABILITY_UNRESOLVED_MUTATION'


def test_preparing_crash_keeps_allocation_blocked(capability,capsys,monkeypatch):
    n,cp,p=capability
    def fail(*args):raise OSError('SYNTHETIC_CRASH')
    monkeypatch.setattr(allocations,'private_write',fail)
    assert capabilities.main(['allocate','--config',str(cp)])==1
    capsys.readouterr()
    reg=Registry(n[0])
    try:
        with pytest.raises(SafetyError,match='CAPABILITY_PREVIOUS_SESSION_UNRESOLVED'):reg.require_history()
    finally:reg.close()


def execute_fake(capability,tid):
    n,cp,p=capability;c,clock,a,b,w,client,token,j=n
    approval=approve_for(capability,tid);client.authority=approval
    reg=Registry(c);nj=Journal(allocation_paths(cp.parent,tid)['journal'],profile(c),tid,account_owner=reg.owner,clock=clock.wall)
    runner=OneTest(c,approval,b,client,nj,confirm=lambda *args:None,controls=lambda:None)
    return reg,nj,runner


def test_accepted_close_waits_for_positive_fill_and_child_cancellation(capability,capsys):
    n,cp,p=capability;c,clock,a,b,w,client,token,j=n
    tid=allocate(capability,capsys);reg,nj,runner=execute_fake(capability,tid)
    original=w.peer.send;respond=w.response;pending=[];polls=[]
    def send(request):
        if request['payloadType']==2111:
            pending.append(copy.deepcopy(request))
            pid=request['payload']['positionId'];oid=w.peer.sequence+1
            return w.peer.event(request['clientMsgId'],2,order={'orderId':oid,'positionId':pid})
        return original(request)
    def response(name,payload):
        if name=='reconcile' and pending:
            polls.append(1)
            if len(polls)>=6:
                request=pending.pop();original(request)
                for o in w.peer.orders.values():o['utcLastUpdateTimestamp']=int(clock.wall()*1000)
                for d in w.peer.deals.values():d['executionTimestamp']=int(clock.wall()*1000)
        return respond(name,payload)
    w.peer.send=send;w.response=response
    try:
        result=runner.run(token)
        report=reg.complete(tid,client,nj,result)
        assert report['automatic_close_verified'] and len(polls)>=6
        assert sum(f['payloadType']==2111 for f in w.calls)==1
    finally:nj.close();reg.close()


@pytest.mark.parametrize('which',['before','after'])
def test_lost_close_ack_never_resends_or_allows_next_test(capability,capsys,which):
    n,cp,p=capability;c,clock,a,b,w,client,token,j=n
    tid=allocate(capability,capsys);reg,nj,runner=execute_fake(capability,tid)
    send=w.peer.send
    def mutated(request):
        if request['payloadType']==2111 and which=='before':raise TimeoutError()
        result=send(request)
        if request['payloadType']==2111:
            for o in w.peer.orders.values():o['utcLastUpdateTimestamp']=int(clock.wall()*1000)
            for d in w.peer.deals.values():d['executionTimestamp']=int(clock.wall()*1000)
            raise TimeoutError()
        return result
    w.peer.send=mutated
    try:
        if which=='before':
            with pytest.raises(SafetyError):runner.run(token)
            reg.mark(tid,'FAILED')
        else:
            result=runner.run(token)
            report=reg.complete(tid,client,nj,result)
            assert report['account_flat'] and not report['automatic_close_verified']
        assert sum(f['payloadType']==2111 for f in w.calls)==1
        with pytest.raises(SafetyError,match='CAPABILITY_PREVIOUS_SESSION_UNRESOLVED'):reg.require_history()
    finally:nj.close();reg.close()


def test_empty_ack_not_proof_automatic_close_and_missing_costs_fail_closed(capability,capsys):
    n,cp,p=capability;c,clock,a,b,w,client,token,j=n
    tid=allocate(capability,capsys);reg,nj,runner=execute_fake(capability,tid)
    try:
        result=runner.run(token)
        nj.db.execute('DELETE FROM acknowledgements WHERE kind=2111')
        report=reg.complete(tid,client,nj,result)
        assert not report['automatic_close_verified'] and report['account_flat']
        for d in w.peer.deals.values():
            if 'closePositionDetail' in d:d['closePositionDetail'].pop('moneyDigits',None)
        with pytest.raises(SafetyError,match='CAPABILITY_COSTS_INCOMPLETE'):reg.complete(tid,client,nj,result)
    finally:nj.close();reg.close()


def test_crash_after_durable_claim_stays_same_allocation_recovery_only(capability,capsys):
    n,cp,p=capability;c,clock,a,b,w,client,token,j=n
    tid=allocate(capability,capsys);reg,nj,runner=execute_fake(capability,tid)
    nj.claim('entry',2106,{},cid='c'*32);nj.close();reg.close()
    reg=Registry(c);nj=Journal(allocation_paths(cp.parent,tid)['journal'],profile(c),tid,account_owner=reg.owner)
    try:
        assert nj.entry()['state']=='UNKNOWN'
        with pytest.raises(SafetyError):nj.claim('another',2106,{})
        with pytest.raises(SafetyError,match='CAPABILITY_PREVIOUS_SESSION_UNRESOLVED'):reg.require_history()
    finally:nj.close();reg.close()


def test_process_death_releases_account_owner_without_resetting_state(capability):
    import os,signal
    n,cp,p=capability;readfd,writefd=os.pipe();pid=os.fork()
    if pid==0:
        os.close(readfd)
        owner=AccountOwner(cp.parent,profile(n[0]));os.write(writefd,b'owned')
        signal.pause();os._exit(0)
    os.close(writefd)
    try:
        assert os.read(readfd,5)==b'owned'
        with pytest.raises(BlockingIOError):Registry(n[0],create=True)
    finally:
        os.close(readfd);os.kill(pid,signal.SIGKILL);os.waitpid(pid,0)
    reg=Registry(n[0],create=True);reg.close()
    assert read_status(p['journal'],profile(n[0]))['entry_claims']==1


def test_legacy_per_journal_owner_also_excludes_new_registry(capability):
    import fcntl
    n,cp,p=capability
    with open(str(p['journal'])+'.owner','a+b') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):Registry(n[0],create=True)


def test_failed_local_confirmation_preserves_allocated_identity(capability,capsys):
    n,cp,p=capability;c,clock,a,b,w,client,token,j=n
    tid=allocate(capability,capsys);reg,nj,runner=execute_fake(capability,tid)
    def decline(*args):raise SafetyError('LOCAL_CONFIRMATION_NOT_ACCEPTED')
    runner.confirm=decline
    try:
        with pytest.raises(SafetyError,match='LOCAL_CONFIRMATION_NOT_ACCEPTED'):runner.run(token)
        assert nj.entry() is None
        with pytest.raises(SafetyError,match='CAPABILITY_PREVIOUS_SESSION_UNRESOLVED'):reg.require_history()
    finally:nj.close();reg.close()


def test_same_profile_owner_is_global_across_private_directories(capability):
    n,cp,p=capability
    other=cp.parent/'another-private-root';other.mkdir(mode=0o700)
    owner=AccountOwner(cp.parent,profile(n[0]))
    try:
        with pytest.raises(BlockingIOError):AccountOwner(other,profile(n[0]))
    finally:owner.close()
    next_owner=AccountOwner(other,profile(n[0]));next_owner.close()


def test_deleted_registry_cannot_bypass_historical_journal(capability,capsys):
    n,cp,p=capability;tid=allocate(capability,capsys)
    (cp.parent/'capability-tests.sqlite').unlink()  # Synthetic fault, never real state.
    assert capabilities.main(['allocate','--config',str(cp)])==1
    assert json.loads(capsys.readouterr().out)['code']=='CAPABILITY_ORPHAN_JOURNAL'


def test_preflight_and_status_are_nonexecuting_and_preserve_all_journals(capability,capsys):
    n,cp,p=capability;tid=allocate(capability,capsys)
    files=[p['journal'],allocation_paths(cp.parent,tid)['journal'],allocation_paths(cp.parent,tid)['test']]
    before=[f.read_bytes() for f in files]
    assert capabilities.main(['status','--config',str(cp)])==0
    result=json.loads(capsys.readouterr().out)
    assert result['execution_enabled'] is False and len(result['tests'])==2
    assert capabilities.main(['preflight','--config',str(cp),'--test-id',tid])==0
    capsys.readouterr()
    assert [f.read_bytes() for f in files]==before and n[4].peer.calls==0


def test_expired_allocation_blocks_new_test_without_replacement(capability,capsys):
    n,cp,p=capability;tid=allocate(capability,capsys);n[1].sleep(601)
    permit=allocation_paths(cp.parent,tid)['test'];before=permit.read_bytes()
    assert capabilities.main(['preflight','--config',str(cp),'--test-id',tid])==1
    assert json.loads(capsys.readouterr().out)['code']=='OWNER_APPROVAL_EXPIRED'
    assert capabilities.main(['allocate','--config',str(cp)])==1
    assert json.loads(capsys.readouterr().out)['code']=='CAPABILITY_PREVIOUS_SESSION_UNRESOLVED'
    assert permit.read_bytes()==before


def test_historical_result_detail_is_retained_across_recovery_states(capability,capsys):
    n,cp,p=capability;tid=allocate(capability,capsys);reg=Registry(n[0])
    try:
        original={'account_flat':True,'automatic_close_verified':False,'costs':{'commission_cost_usd':'0.08'}}
        reg.mark(tid,'FAILED_FLAT',original);reg.mark(tid,'ACTIVE')
        assert json.loads(reg.db.execute('SELECT result FROM allocations WHERE id=?',(tid,)).fetchone()[0])==original
        details=[json.loads(row[0]) for row in reg.db.execute('SELECT detail FROM events WHERE test_id=? AND detail IS NOT NULL',(tid,))]
        assert original in details
    finally:reg.close()


def test_historical_report_readable_without_execution_owner(capability,capsys):
    from ctrader_execution.allocations import read_report
    n,cp,p=capability;tid=allocate(capability,capsys);reg=Registry(n[0])
    try:
        result={'account_flat':True,'automatic_close_verified':False,'costs':{'commission_cost_usd':'0.08'}}
        reg.mark(tid,'FAILED_FLAT',result)
        report=read_report(n[0],tid)
        assert report['result']==result and result in report['historical_results']
        legacy=read_report(n[0],'legacy-first')
        assert 'evidence' not in legacy and legacy['classification']=='MANUALLY_CLOSED_LEGACY'
    finally:reg.close()
