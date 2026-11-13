"""Attended Razor DEMO capability tests. No order without explicit --execute.

allocate asks local consent and performs view-only checks; it does not trade.
Original first-test state is never reset, moved, opened for recovery or rewritten.
"""
import argparse
import copy
import json
import time
from uuid import uuid4

from ctrader_readonly.config import Config,SafetyError,private_read
from .approvals import paths,profile,require_baseline,validate_test,local_confirmation
from .ledger import Journal,inspect_private_file
from .allocations import Registry,allocation_paths,status,closed_evidence,read_report
from .renewal import check_current_costs
from .harness import OneTest,view_reader,market_state,prepare,stopped_controls,execution_failure
from .oauth import TradingToken
from .transport import TradeClient,audit_projection


def template(config,baseline,tid,clock=time.time):
    root=config.token_path.parent
    old=private_read(paths(root/'config.json')['test'])
    validate_test(old,config,old['issued_at'])
    # Keep the first tested plan immutable; later tests cannot raise its bounds.
    required={'raw_volume':100,'entry_limit':1,'side':'BUY','order_type':'MARKET',
        'stop_distance':'2.00','target_distance':'4.00','risk_cap':'5.00','margin_cap':'250.00',
        'slippage_buffer':'0.25','fee_buffer':'0.50','max_spread':'0.50'}
    from decimal import Decimal
    for key,value in required.items():
        actual=old.get(key)
        if (Decimal(str(actual))!=Decimal(str(value)) if key not in ('side','order_type') else actual!=value):raise SafetyError('CAPABILITY_FIXED_POLICY_REQUIRED')
    new=copy.deepcopy(old);new.pop('renewal_receipt',None)
    now=clock();new.update(id=tid,issued_at=now,expires_at=now+600,management_until=now+3600,
        allowed_ops=[2106,2108,2111],confirmed_balance=baseline['balance'],intended_balance=baseline['intended_balance'])
    return new


def allocation_preview(config,baseline,plan,reader):
    stopped_controls(plan['controls_database'])
    market=market_state(reader);check_current_costs(reader,plan)
    if reader.status()['symbol']['trading_mode']!=0:raise SafetyError('CAPABILITY_SYMBOL_NOT_ENABLED')
    return prepare(config,plan,baseline,market,time.time())[2]


def check_registered_history(reg,tid):
    # All older tests must remain terminal and byte-preserved; this test alone
    # can be unresolved. A missing/changed historical file blocks operation.
    from .allocations import file_digest
    from .ledger import digest
    rows=reg.db.execute('SELECT * FROM allocations').fetchall()
    if not any(row['id']==tid for row in rows):raise SafetyError('CAPABILITY_ID_NOT_REGISTERED')
    for row in rows:
        if row['id']==tid:continue
        if row['state'] not in ('CLOSED_SUCCESS','LEGACY_CLOSED'):raise SafetyError('CAPABILITY_PREVIOUS_SESSION_UNRESOLVED')
        p=paths(reg.root/'config.json') if row['id']=='legacy-first' else allocation_paths(reg.root,row['id'])
        if file_digest(p['journal'])!=row['journal_digest'] or digest(private_read(p['test']))!=row['approval_digest']:raise SafetyError('CAPABILITY_HISTORY_CHANGED')
    known={row['id'] for row in rows}
    for path in reg.root.glob('capability-*'):
        if path.name=='capability-tests.sqlite' or path.suffix not in ('.sqlite','.json'):continue
        if path.stem[len('capability-'):] not in known:raise SafetyError('CAPABILITY_ORPHAN_JOURNAL')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('allocate','status','preflight','run','resume','resolve','report'))
    parser.add_argument('--config',required=True);parser.add_argument('--test-id')
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args(argv)
    reg=None;client=None;journal=None;runner=None;stage='CONFIGURATION';tid=args.test_id
    try:
        config=Config.load(args.config);profile(config);p=paths(args.config)
        if config.token_path.parent!=p['journal'].parent:raise SafetyError('CAPABILITY_CANONICAL_ROOT_REQUIRED')
        if args.command=='status':print(json.dumps(status(config),indent=2));return 0
        if args.command=='report':
            stage='READ_ONLY_PRIVATE_REPORT'
            value=read_report(config,tid)
            with open('/dev/tty','w') as terminal:terminal.write(json.dumps(value,indent=2)+'\n');terminal.flush()
            print('PRIVATE_CAPABILITY_REPORT_DISPLAYED');return 0
        if args.command=='allocate':
            if tid is not None or args.execute:raise SafetyError('CAPABILITY_ALLOCATION_ARGUMENTS_REJECTED')
            stage='BASELINE_VALIDATE';baseline=require_baseline(private_read(p['baseline']),config)
            stage='ACCOUNT_OWNER';reg=Registry(config,create=True)
            stage='HISTORICAL_TEST_GUARD';reg.require_history()
            tid=uuid4().hex;plan=template(config,baseline,tid)
            stage='VIEW_ONLY_LEGACY_RECONCILIATION';client=view_reader(config)
            legacy=reg.legacy_proof(client)
            proposal=allocation_preview(config,baseline,plan,client)
            client.disconnect('AWAITING_ALLOCATION_CONSENT');client=None
            stage='ALLOCATION_LOCAL_CONSENT'
            local_confirmation('Allocate ONE new Razor DEMO capability test '+tid+'. Original spent journal stays immutable. This test must verify native SL/TP and one automatic full close. Includes residual cancellation and emergency close. Review fixed risk/cost bounds; stops and cost allowances are not guarantees. No strategy activation.\n'+json.dumps(proposal,indent=2)+'\nType ALLOCATE ONE DEMO '+str(config.expected_demo_login),'ALLOCATE ONE DEMO '+str(config.expected_demo_login))
            stage='VIEW_ONLY_FINAL_CHECK';client=view_reader(config)
            legacy=reg.legacy_proof(client)
            now=time.time();plan.update(issued_at=now,expires_at=now+600,management_until=now+3600)
            allocation_preview(config,baseline,plan,client)
            plan['allocation_id']=tid;plan['journal_path']=str(allocation_paths(reg.root,tid)['journal'])
            stage='DURABLE_ALLOCATION';reg.allocate(plan,legacy)
            print(json.dumps({'test_id':tid,'state':'PREPARED','execution_enabled':False,'entry_limit':1}));return 0
        if tid is None:raise SafetyError('CAPABILITY_ID_REQUIRED')
        stage='ACCOUNT_OWNER';reg=Registry(config)
        stage='HISTORICAL_TEST_GUARD';check_registered_history(reg,tid)
        if args.command=='resolve':
            # View-only settlement record; never makes a failed/unknown test
            # eligible for another allocation, even after manual closure.
            existing=reg.db.execute('SELECT state FROM allocations WHERE id=?',(tid,)).fetchone()
            if existing and existing[0]=='CLOSED_SUCCESS':
                print('ALREADY_CLOSED_SUCCESS; NO_STATE_CHANGE');return 0
            stage='VIEW_ONLY_RESOLUTION';client=view_reader(config)
            book,_=closed_evidence(client,allocation_paths(reg.root,tid)['journal'],allow_unresolved=True)
            reg.mark(tid,'FAILED_FLAT',{'account_flat':True,'history_complete':True,'automatic_close_verified':False,'evidence':audit_projection(book)})
            print('FLAT_EVIDENCE_RECORDED; FURTHER_ALLOCATION_BLOCKED_PENDING_REVIEW');return 0
        stage='APPROVAL_VALIDATE';approval,tp=reg.approval(tid,management=args.command=='resume')
        stage='BASELINE_VALIDATE';baseline=require_baseline(private_read(p['baseline']),config)
        stage='EXISTING_JOURNAL_VALIDATE'
        if not inspect_private_file(tp['journal']):raise SafetyError('CAPABILITY_JOURNAL_MISSING')
        if args.command=='preflight' or (args.command=='run' and not args.execute):
            client=view_reader(config);proposal=allocation_preview(config,baseline,approval,client)
            from .ledger import read_status
            if read_status(tp['journal'],profile(config))['entry_claims']:raise SafetyError('CAPABILITY_ENTRY_ALREADY_CLAIMED')
            print(json.dumps({'test_id':tid,'preflight':'PASS','execution_enabled':False,'proposal':proposal}));return 0
        if not args.execute:raise SafetyError('CAPABILITY_EXECUTE_FLAG_REQUIRED')
        if args.command not in ('run','resume'):raise SafetyError('CAPABILITY_OPERATION_REJECTED')
        stage='JOURNAL_OWNER';journal=Journal(tp['journal'],profile(config),tid,account_owner=reg.owner)
        stage='EXISTING_TRADING_TOKEN';token=TradingToken.load(p['token'],config.token_path,config.demo_account_id)
        reg.mark(tid,'ACTIVE')
        client=TradeClient(config,approval);runner=OneTest(config,approval,baseline,client,journal)
        result=runner.run(token,resume=args.command=='resume')
        client.disconnect('VIEW_ONLY_COMPLETION_CHECK');client=None
        stage='VIEW_ONLY_COMPLETION_CHECK';client=view_reader(config)
        report=reg.complete(tid,client,journal,result)
        print(json.dumps({'test_id':tid,'automatic_close_verified':report['automatic_close_verified'],
            'account_flat':True,'cost_report_available':True,'entry_slot_spent':True}));return 0 if report['automatic_close_verified'] else 2
    except (Exception,KeyboardInterrupt) as exc:
        if reg and tid and args.command in ('run','resume') and args.execute:
            try:reg.mark(tid,'FAILED')
            except Exception:pass
        if args.command in ('run','resume') and args.execute:
            try:
                failure=execution_failure(config,{'journal':allocation_paths(config.token_path.parent,tid)['journal']},
                    stage if stage=='VIEW_ONLY_COMPLETION_CHECK' or runner is None else runner.stage,
                    exc,runner.exposure if runner else None)
            except Exception:
                failure={'state':'UNKNOWN','stage':stage,'code':'LOCAL_EVIDENCE_UNAVAILABLE','execution_enabled':False,
                    'operator_action':'Inspect pinned demo UI immediately; do not retry entry'}
        else:
            # Only local fixed codes; never print provider, SQLite or file errors.
            from .diagnostics import safe_capability_code
            failure={'state':'BLOCKED','stage':stage,'code':safe_capability_code(exc),'execution_enabled':False}
        print(json.dumps(failure));return 1
    finally:
        if client:client.disconnect('CAPABILITY_CLI_FINISHED')
        if journal:journal.close()
        if reg:reg.close()

if __name__=='__main__':raise SystemExit(main())
