"""Synthetic final-confirmation/dispatch diagnostics. Never calls execution CLI."""
import json
import pytest
from test_phase31 import native,private_dir
from ctrader_execution.harness import OneTest,execution_failure
from ctrader_execution.approvals import paths
from ctrader_readonly.config import SafetyError


@pytest.mark.parametrize('idle',[12,35])
def test_idle_final_confirmation_uses_new_authenticated_session(native,idle):
    c,clock,a,b,w,client,token,j=native
    opens=[]
    def confirm(*args):
        assert not client._connected
        opens.append(w.opens)
        old=w.mock.quote();clock.sleep(idle);w.inbox.insert(0,old)
    result=OneTest(c,a,b,client,j,confirm=confirm,controls=lambda:None).run(token)
    assert result=='CLOSED_RECONCILED'
    assert w.opens>opens[0] and j.entry()['state']=='CLOSED'
    assert sum(f['payloadType']==2106 for f in w.calls)==1


@pytest.mark.parametrize('problem,stage,code',[
    ('decline','LOCAL_CONFIRMATION','LOCAL_CONFIRMATION_NOT_ACCEPTED'),
    ('approval_expiry','POST_CONFIRMATION_RISK_VALIDATION','OWNER_APPROVAL_EXPIRED'),
    ('token_expiry','POST_CONFIRMATION_BROKER_AUTHORIZATION','TRADING_TOKEN_EXPIRED_REAUTHORIZE'),
    ('account','POST_CONFIRMATION_BROKER_AUTHORIZATION','DISPLAY_LOGIN_MISMATCH')])
def test_post_confirmation_failures_stay_preclaim_and_precise(native,problem,stage,code,private_dir):
    c,clock,a,b,w,client,token,j=native
    def confirm(*args):
        if problem=='decline':raise SafetyError(code)
        if problem=='approval_expiry':clock.sleep(601)
        if problem=='token_expiry':token.expires_at=clock.wall()+29
        if problem=='account':
            def change(name,result):
                if name=='accounts':result['ctidTraderAccount'][0]['traderLogin']=999
                return result
            w.change=change
    runner=OneTest(c,a,b,client,j,confirm=confirm,controls=lambda:None)
    with pytest.raises(SafetyError,match=code) as error:runner.run(token)
    assert runner.stage==stage and j.entry() is None and w.peer.calls==0
    before=j.path.read_bytes()
    value=execution_failure(c,paths(private_dir/'config.json'),runner.stage,error.value)
    assert value['state']=='LOCAL_FAILURE_BEFORE_DISPATCH'
    assert value['dispatch']=='NOT_CLAIMED' and value['code']==code
    assert value['no_order_attempted'] and 'no_blind_resend' not in value
    assert j.path.read_bytes()==before


@pytest.mark.parametrize('dispatch',['claimed','consumed','accepted','rejected','closed'])
def test_dispatch_diagnostics_follow_durable_evidence_only(native,private_dir,dispatch):
    c,clock,a,b,w,client,token,j=native
    claim,_=j.claim('entry',2106,{'synthetic':True})
    if dispatch!='claimed':j.consume_send(claim['id'],2106,{'synthetic':True})
    if dispatch in ('accepted','rejected','closed'):
        j.outcome(claim['id'],dispatch.upper() if dispatch!='closed' else 'CLOSED','HISTORY_CONFIRMED_FLAT' if dispatch=='closed' else 'CORRELATED_EXECUTION_EVENT')
    before=j.path.read_bytes()
    result=execution_failure(c,paths(private_dir/'config.json'),'SUBMISSION',SafetyError('UNKNOWN_BROKER_OUTCOME'))
    assert result['entry_claims']==1 and result['no_blind_resend']
    assert result['dispatch']==('CLAIMED_NOT_PROVEN_SENT' if dispatch=='claimed' else 'CONSUMED_NOT_PROOF_OF_TRANSMISSION')
    assert 'no_order_attempted' not in result
    assert j.path.read_bytes()==before
    if dispatch in ('claimed','consumed'):assert result['state']=='UNKNOWN'


def test_diagnostics_unavailable_and_exception_values_do_not_leak(native,private_dir):
    c,clock,a,b,w,client,token,j=native;j.close();j.path.chmod(0o644)
    result=execution_failure(c,paths(private_dir/'config.json'),'JOURNAL_OWNERSHIP',ValueError('PRIVATE_ACCOUNT_AND_TOKEN'))
    assert result['state']=='UNKNOWN' and result['dispatch']=='EVIDENCE_UNAVAILABLE'
    assert 'PRIVATE_ACCOUNT_AND_TOKEN' not in json.dumps(result)
