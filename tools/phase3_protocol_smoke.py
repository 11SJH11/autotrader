"""Fictional native-unit protocol smoke; never reads credentials or uses sockets."""
import argparse
from datetime import datetime,timedelta,timezone
from decimal import Decimal as D
import json
from pathlib import Path
import tempfile

from control_plane.contracts import TradeIntent,Quote
from control_plane.native import NativeEngine,MarginEvidence
from ctrader_execution.schema import SymbolSpec,RiskLimits
from ctrader_execution.offline import OfflinePeer,OfflineAdapter


def run(directory):
    directory.mkdir(parents=True,exist_ok=True)
    path=Path(tempfile.mkdtemp(prefix='protocol-',dir=directory))/'native.sqlite'
    spec=SymbolSpec(456,10000,100,500000,100,2,0,0,True)
    limits=RiskLimits(D(100),D(200),D(150),D(20),D(10),D(500),D(7),D('.5'),D('.5'),D('.5'))
    peer=OfflinePeer(account_id=321)
    engine=NativeEngine(path,OfflineAdapter(peer),spec,limits)
    now=datetime.now(timezone.utc);quote=Quote(D('1999.9'),D(2000),now)
    evidence=MarginEvidence(D(2),D(100),D(1000),now,True,321,456)
    try:
        engine.reconcile(now);engine.control('t02_reference','RUNNING',now);engine.suspend(False,now)
        def submit(key,order_type,entry):
            intent=TradeIntent(key,key,'t02_reference',key,'1','RAZOR-DEMO','XAUUSD','BUY',D(2),entry,D(1990),now,now+timedelta(seconds=60),order_type=order_type,take_profit=D(2010))
            decision,command=engine.enqueue(intent,quote,evidence,now)
            assert decision.accepted,decision.reason_code
            engine.dispatch(command.command_id,quote,evidence,now)
        submit('fictional-market','MARKET',D(2000));pid=next(iter(peer.positions))
        engine.manage('fictional-be','MODIFY_POSITION',pid,Quote(D(2005),D('2005.1'),now),now,stop=D(2000))
        engine.manage('fictional-partial-close','CLOSE',pid,quote,now,units=D(1))
        engine.manage('fictional-full-close','CLOSE',pid,quote,now)
        submit('fictional-limit','LIMIT',D(1998))
        oid=max(peer.orders)
        engine.manage('fictional-amend','AMEND_PENDING',oid,quote,now,entry=D(1997),margin=evidence)
        engine.manage('fictional-cancel','CANCEL',oid,quote,now)
        status=engine.status()
        assert all(D(c['quantity'])==0 and D(c['margin'])==0 for c in status['commands'])
        return {'mode':'FICTIONAL_OFFLINE_CTRADER_PROTOCOL_SMOKE','network_calls':0,
            'execution_enabled':False,'offline_peer_calls':peer.calls,'commands':len(status['commands']),
            'management_actions':len(status['actions']),'all_reservations_released':True,
            'stages':['MARKET_WITH_NATIVE_RELATIVE_PROTECTION','PROTECTION_AMEND','PARTIAL_CLOSE','FULL_CLOSE','LIMIT_WITH_NATIVE_ABSOLUTE_PROTECTION','PENDING_AMEND','CANCEL'],
            'limitations':['Invented prices, IDs and margin evidence; not real broker execution','No production trade transport or trading authorization invoked']}
    finally:engine.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',default='.phase3/smoke');parser.add_argument('--output',default='docs/phase3_protocol_smoke.json')
    args=parser.parse_args();result=run(Path(args.directory))
    Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
    print('OFFLINE_PROTOCOL_SMOKE_PASS; NETWORK_CALLS_ZERO; REAL_EXECUTION_DISABLED')

if __name__=='__main__':main()
