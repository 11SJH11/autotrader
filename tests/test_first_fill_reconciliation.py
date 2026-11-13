"""Actual broker role/field shapes, sanitized identities, no broker connections."""
import copy
import json
from decimal import Decimal as D
from pathlib import Path

import pytest
from test_phase31 import native,private_dir,ready
from ctrader_execution.transport import owned_state,observed_exposure,audit_projection
from ctrader_execution.harness import OneTest,execution_failure,protection_ok
from ctrader_execution.approvals import paths
from ctrader_execution.schema import SymbolSpec
from ctrader_readonly.config import SafetyError

FIXTURE=json.loads(Path('tests/fixtures/razor_first_fill.json').read_text())
CID=FIXTURE['client_order_id']

def book(which='open'):return copy.deepcopy(FIXTURE[which])

def exposure(value):return observed_exposure(value,CID,symbol_id=10001,side=1,raw_volume=100)


def test_native_protection_inherits_cid_without_becoming_duplicate_entry():
    value=book();order,positions,opened,closed=owned_state(value,CID)
    assert order['orderType']==1 and len(positions)==1 and (opened,closed)==(100,0)
    assert positions[0]['price']==3876.83 and positions[0]['stopLoss']==3874.83 and positions[0]['takeProfit']==3880.83
    spec=SymbolSpec(10001,10000,100,500000,100,2,0,0,True,True)
    assert protection_ok(positions[0],{'side':'BUY','order_type':'MARKET','stop_distance':'2','target_distance':'4'},spec)


def test_manual_close_requires_positive_deals_and_balances_to_zero():
    order,positions,opened,closed=owned_state(book('closed'),CID)
    assert order['orderType']==1 and positions==[] and (opened,closed)==(100,100)
    fees=[D(x['commission'])/(D(10)**x['moneyDigits']) for x in book('closed')['deals']]
    assert fees==[D('-.04'),D('-.04')] and sum(fees)==D('-.08')


@pytest.mark.parametrize('change',['duplicate_entry','wrong_position','wrong_symbol','wrong_side','oversize_child','unknown_type','pending_after_close'])
def test_entry_uniqueness_and_child_link_checks_remain_strict(change):
    value=book('closed' if change=='pending_after_close' else 'open')
    child=next(o for o in value['orders'] if o['orderType']==4)
    if change=='duplicate_entry':
        entry=copy.deepcopy(next(o for o in value['orders'] if o.get('clientOrderId')==CID and o['orderType']==1));entry['orderId']+=1000;value['orders'].append(entry)
    elif change=='wrong_position':child['positionId']+=1000
    elif change=='wrong_symbol':child['tradeData']['symbolId']+=1000
    elif change=='wrong_side':child['tradeData']['tradeSide']=1
    elif change=='oversize_child':child['tradeData']['volume']=200
    elif change=='unknown_type':child.update(orderType=99,closingOrder=True)
    else:child['orderStatus']=1
    with pytest.raises(SafetyError):owned_state(value,CID)


def test_missing_history_reports_positive_open_exposure_without_authorizing_reconciliation():
    value=book();value['history_complete']=False;value['deals']=[]
    summary=exposure(value)
    assert summary['ownership_identified'] and summary['owned_open_positions']==1
    assert summary['owned_open_raw_volume']==100 and summary['native_sl_tp_present']
    with pytest.raises(SafetyError,match='HISTORY_INCOMPLETE'):owned_state(value,CID)


def test_missing_entry_does_not_infer_ownership_from_protective_child():
    value=book();value['orders']=[o for o in value['orders'] if o['orderType']==4]
    assert exposure(value)['account_open_positions']==1
    assert exposure(value)['ownership_identified'] is False
    with pytest.raises(SafetyError,match='ENTRY_ORDER_EVIDENCE_MISSING'):owned_state(value,CID)


def test_closed_deal_volume_mismatch_is_still_rejected():
    value=book('closed');closing=next(d for d in value['deals'] if 'closePositionDetail' in d)
    closing['closePositionDetail']['closedVolume']=50
    with pytest.raises(SafetyError,match='EXIT_VOLUME_MISMATCH'):owned_state(value,CID)


def test_harness_reports_open_exposure_when_strict_history_is_incomplete(native,private_dir):
    c,clock,a,b,w,client,token,j=native;ready(native)
    request,_=j.claim('entry',2106,{},cid=CID);j.consume_send(CID,2106,{})
    j.outcome(CID,'ACCEPTED','CORRELATED_EXECUTION_EVENT')
    runner=OneTest(c,a,b,client,j,controls=lambda:None)
    runner.spec=client.native_spec
    value=book();value['history_complete']=False
    client.snapshot=lambda *args:value
    with pytest.raises(SafetyError,match='HISTORY_INCOMPLETE') as error:runner._book()
    failure=execution_failure(c,paths(private_dir/'config.json'),runner.stage,error.value,runner.exposure)
    assert failure['code']=='HISTORY_INCOMPLETE' and failure['state']=='CRITICAL_OBSERVED_EXPOSURE'
    assert failure['journal_state']=='SUBMITTED' and failure['observed_exposure']['owned_open_positions']==1
    assert w.peer.calls==0 and j.entry()['state']=='ACCEPTED'


def test_failed_history_request_still_reports_account_open_position(native,private_dir):
    c,clock,a,b,w,client,token,j=native;ready(native)
    j.claim('entry',2106,{},cid=CID)
    runner=OneTest(c,a,b,client,j,controls=lambda:None);runner.spec=client.native_spec
    def failed(*args):raise SafetyError('HISTORY_INCOMPLETE')
    client.snapshot=failed
    value=book()
    client._read=lambda *args,**kwargs:{'order':[],'position':value['positions']}
    with pytest.raises(SafetyError,match='HISTORY_INCOMPLETE'):runner._book()
    assert runner.exposure['account_open_positions']==1 and not runner.exposure['ownership_identified']
    assert w.peer.calls==0


def test_audit_projection_preserves_documented_closing_role():
    value=book();value['orders'][0]['closingOrder']=True
    assert audit_projection(value)['orders'][0]['closingOrder'] is True


@pytest.mark.parametrize('closing_flag',[False,True])
def test_close_order_can_share_cid_when_role_is_proven(closing_flag):
    value=book('closed')
    close_ids={d['orderId'] for d in value['deals'] if 'closePositionDetail' in d}
    closing=next(o for o in value['orders'] if o['orderId'] in close_ids)
    closing['clientOrderId']=CID
    if closing_flag:closing['closingOrder']=True
    _,positions,opened,closed=owned_state(value,CID)
    assert positions==[] and (opened,closed)==(100,100)
