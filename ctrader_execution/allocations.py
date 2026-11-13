"""Private account-scoped capability registry. No broker mutation methods."""
from contextlib import closing
from decimal import Decimal as D
import hashlib
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from ctrader_readonly.config import SafetyError,private_path,private_read,private_write
from ctrader_readonly.protocol import integer
from .approvals import profile,paths
from .ledger import AccountOwner,Journal,inspect_private_file,digest,encode,read_status
from .transport import read_book,owned_state,audit_projection


def allocation_paths(root,test_id):
    if not isinstance(test_id,str) or len(test_id)!=32 or any(c not in '0123456789abcdef' for c in test_id):raise SafetyError('INVALID_CAPABILITY_ID')
    return {'test':root/('capability-'+test_id+'.json'),'journal':root/('capability-'+test_id+'.sqlite')}


def file_digest(path):
    if not inspect_private_file(path):raise SafetyError('CAPABILITY_EVIDENCE_MISSING')
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_allocation(config,approval):
    root=private_path(config.token_path).parent;tid=approval.get('allocation_id')
    p=allocation_paths(root,tid)
    if approval['id']!=tid or approval.get('journal_path')!=str(p['journal']):raise SafetyError('CAPABILITY_PATH_MISMATCH')
    registry=root/'capability-tests.sqlite'
    if not inspect_private_file(registry):raise SafetyError('CAPABILITY_REGISTRY_REQUIRED')
    with closing(sqlite3.connect(registry.as_uri()+'?mode=ro',uri=True,timeout=.25)) as db:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        binding=db.execute("SELECT value FROM metadata WHERE key='profile'").fetchone()
        row=db.execute('SELECT approval_digest,state FROM allocations WHERE id=?',(tid,)).fetchone()
        if not binding or binding[0]!=profile(config) or not row or row[0]!=digest(approval):raise SafetyError('CAPABILITY_APPROVAL_MISMATCH')
        if row[1] not in ('PREPARED','ACTIVE','FAILED','FAILED_FLAT'):raise SafetyError('CAPABILITY_SESSION_NOT_ACTIVE')
    return p


def journal_facts(path):
    if not inspect_private_file(path):raise SafetyError('CAPABILITY_JOURNAL_MISSING')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.25)) as db:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        binding=db.execute('SELECT profile,approval FROM binding').fetchall()
        mutations=[dict(zip(('id','kind','state','sent','at'),r)) for r in db.execute('SELECT id,kind,state,sent,at FROM mutations ORDER BY at')]
        return binding,mutations


def require_flat(book):
    if book.get('history_complete') is not True:raise SafetyError('HISTORY_INCOMPLETE')
    if book['positions'] or any(integer(o['orderStatus'],1)==1 for o in book['orders']):raise SafetyError('CAPABILITY_ACCOUNT_EXPOSURE_PRESENT')


def closed_evidence(reader,path,*,allow_unresolved=False):
    binding,mutations=journal_facts(path)
    entries=[m for m in mutations if m['kind']==2106]
    if len(entries)!=1:raise SafetyError('CAPABILITY_ENTRY_EVIDENCE_REQUIRED')
    entry=entries[0]
    if not entry['sent'] or (not allow_unresolved and any(m['state'] in ('UNKNOWN','SUBMITTING','REJECTED') for m in mutations)):raise SafetyError('CAPABILITY_UNRESOLVED_MUTATION')
    book=read_book(reader,max(0,int(entry['at']*1000)-5000))
    require_flat(book)
    order,positions,opened,closed=owned_state(book,entry['id'])
    if not order or positions or opened!=100 or closed!=100:raise SafetyError('CAPABILITY_POSITIVE_CLOSURE_REQUIRED')
    return book,entry


def execution_costs(book,cid,journal):
    """USD XAUUSD costs only. No missing-field currency/rounding defaults."""
    order,_,opened,closed=owned_state(book,cid);oid=integer(order['orderId'],1)
    opening=[d for d in book['deals'] if integer(d['orderId'],1)==oid and 'closePositionDetail' not in d and integer(d['filledVolume'])>0]
    pids={integer(d['positionId'],1) for d in opening}
    closing=[d for d in book['deals'] if integer(d['positionId'],1) in pids and 'closePositionDetail' in d and integer(d['filledVolume'])>0]
    if opened!=closed or not opened or not opening or not closing:raise SafetyError('CAPABILITY_COSTS_INCOMPLETE')
    def amount(value,key):
        if key not in value or 'moneyDigits' not in value:raise SafetyError('CAPABILITY_COSTS_INCOMPLETE')
        digits=integer(value['moneyDigits'])
        if digits>12:raise SafetyError('CAPABILITY_COSTS_INCOMPLETE')
        raw=value[key]
        if type(raw) is not int and not (isinstance(raw,str) and raw.lstrip('-').isdigit()):raise SafetyError('CAPABILITY_COSTS_INCOMPLETE')
        return D(raw)/(D(10)**digits)
    fees=sum((amount(d,'commission') for d in opening+closing),D(0))
    gross=sum((amount(d['closePositionDetail'],'grossProfit') for d in closing),D(0))
    swap=sum((amount(d['closePositionDetail'],'swap') for d in closing),D(0))
    vwap=lambda ds:sum(D(str(d['executionPrice']))*integer(d['filledVolume'],1) for d in ds)/sum(integer(d['filledVolume'],1) for d in ds)
    entry_price=vwap(opening);close_price=vwap(closing)
    reference=journal.db.execute('SELECT bid,ask FROM references_at_send WHERE command=?',(cid,)).fetchone()
    delta=None if reference is None else (entry_price-D(reference[1]) if integer(order['tradeData']['tradeSide'],1)==1 else D(reference[0])-entry_price)
    return {'opening_price':str(entry_price),'closing_price':str(close_price),'commission_signed_usd':str(fees),
        'commission_cost_usd':str(-fees),'gross_pnl_usd':str(gross),'swap_signed_usd':str(swap),
        'net_realized_pnl_usd':str(gross+fees+swap),'entry_price_vs_last_observed_quote':str(delta) if delta is not None else None,
        'reference_is_not_guaranteed_execution_slippage':True}


class Registry:
    """One pinned private root; failures stay blocking until separate review."""
    def __init__(self,config,*,create=False):
        self.config=config;self.root=private_path(config.token_path).parent;self.profile=profile(config)
        self.owner=AccountOwner(self.root,self.profile);self.db=None;self.legacy_lock=None
        try:
            legacy_owner=self.root/'first-test.sqlite.owner'
            if not inspect_private_file(legacy_owner):raise SafetyError('CAPABILITY_LEGACY_OWNER_REQUIRED')
            self.legacy_lock=os.fdopen(os.open(private_path(legacy_owner),os.O_RDWR|os.O_NOFOLLOW),'a+b')
            fcntl.flock(self.legacy_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            path=self.root/'capability-tests.sqlite'
            exists=inspect_private_file(path)
            if not exists and not create:raise SafetyError('CAPABILITY_REGISTRY_REQUIRED')
            fd=os.open(private_path(path),os.O_RDWR|os.O_NOFOLLOW|(os.O_CREAT if create else 0),0o600);os.close(fd)
            self.db=sqlite3.connect(path,timeout=.25,isolation_level=None)
            self.db.row_factory=sqlite3.Row
            self.db.execute('PRAGMA synchronous=FULL')
            if not exists:
                self.db.execute('PRAGMA journal_mode=DELETE')
                self.db.executescript('''CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
                    CREATE TABLE allocations(id TEXT PRIMARY KEY,approval_digest TEXT,state TEXT,created REAL,result TEXT,journal_digest TEXT);
                    CREATE UNIQUE INDEX one_unresolved_test ON allocations((1)) WHERE state NOT IN ('CLOSED_SUCCESS','LEGACY_CLOSED');
                    CREATE TABLE events(seq INTEGER PRIMARY KEY,at REAL,test_id TEXT,code TEXT,detail TEXT);''')
                self.db.execute("INSERT INTO metadata VALUES('profile',?)",(self.profile,))
            row=self.db.execute("SELECT value FROM metadata WHERE key='profile'").fetchone()
            if not row or row[0]!=self.profile:raise SafetyError('CAPABILITY_PROFILE_MISMATCH')
        except BaseException:self.close();raise

    def close(self):
        if self.db:self.db.close();self.db=None
        if self.legacy_lock:self.legacy_lock.close();self.legacy_lock=None
        if self.owner:self.owner.close()

    def event(self,tid,code,detail=None):
        self.db.execute('INSERT INTO events(at,test_id,code,detail) VALUES(?,?,?,?)',(time.time(),tid,code,encode(detail) if detail is not None else None))

    def require_history(self):
        rows_=self.db.execute('SELECT * FROM allocations').fetchall()
        for row in rows_:
            if row['state'] not in ('CLOSED_SUCCESS','LEGACY_CLOSED'):raise SafetyError('CAPABILITY_PREVIOUS_SESSION_UNRESOLVED')
            p=paths(self.root/'config.json') if row['id']=='legacy-first' else allocation_paths(self.root,row['id'])
            if file_digest(p['journal'])!=row['journal_digest'] or digest(private_read(p['test']))!=row['approval_digest']:raise SafetyError('CAPABILITY_HISTORY_CHANGED')
        known={row['id'] for row in rows_}
        for path in self.root.glob('capability-*.sqlite'):
            if path.name=='capability-tests.sqlite':continue
            tid=path.name[len('capability-'):-len('.sqlite')]
            if tid not in known:raise SafetyError('CAPABILITY_ORPHAN_JOURNAL')
        for path in self.root.glob('capability-*.json'):
            if path.name[len('capability-'):-len('.json')] not in known:raise SafetyError('CAPABILITY_ORPHAN_APPROVAL')
        return rows_

    def legacy_proof(self,reader):
        if self.db.execute("SELECT 1 FROM allocations WHERE id='legacy-first'").fetchone():return None
        p=paths(self.root/'config.json')
        book,entry=closed_evidence(reader,p['journal'])
        binding,_=journal_facts(p['journal']);approval=private_read(p['test'])
        if binding!=[(self.profile,approval['id'])]:raise SafetyError('CAPABILITY_LEGACY_BINDING_MISMATCH')
        return {'approval_digest':digest(approval),'journal_digest':file_digest(p['journal']),
            'book':audit_projection(book),'entry':entry['id']}

    def allocate(self,approval,legacy):
        tid=approval['id'];p=allocation_paths(self.root,tid)
        self.require_history()
        if inspect_private_file(p['test']) or inspect_private_file(p['journal']):raise SafetyError('CAPABILITY_ID_ALREADY_EXISTS')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if legacy is not None:
                lp=paths(self.root/'config.json')
                if file_digest(lp['journal'])!=legacy['journal_digest'] or digest(private_read(lp['test']))!=legacy['approval_digest']:raise SafetyError('CAPABILITY_HISTORY_CHANGED')
                self.db.execute('INSERT INTO allocations VALUES(?,?,?,?,?,?)',('legacy-first',legacy['approval_digest'],'LEGACY_CLOSED',time.time(),encode({'classification':'MANUALLY_CLOSED_LEGACY','evidence':legacy['book']}),legacy['journal_digest']))
                self.event('legacy-first','POSITIVE_LEGACY_CLOSURE_REVIEWED')
            if not self.db.execute("SELECT 1 FROM allocations WHERE id='legacy-first'").fetchone():raise SafetyError('CAPABILITY_LEGACY_REVIEW_REQUIRED')
            self.db.execute('INSERT INTO allocations VALUES(?,?,?,?,?,?)',(tid,digest(approval),'PREPARING',time.time(),None,None))
            self.event(tid,'ALLOCATION_CONSENT_COMMITTED');self.db.commit()
        except BaseException:self.db.rollback();raise
        # Crash in publication leaves PREPARING, blocks all further allocations.
        private_write(p['test'],approval)
        j=Journal(p['journal'],self.profile,tid,account_owner=self.owner)
        j.close()
        self.db.execute("UPDATE allocations SET state='PREPARED' WHERE id=? AND state='PREPARING'",(tid,))
        self.event(tid,'IMMUTABLE_APPROVAL_AND_JOURNAL_PUBLISHED')
        return tid

    def approval(self,tid,*,management=False):
        p=allocation_paths(self.root,tid);value=private_read(p['test'])
        from .approvals import validate_test
        validate_test(value,self.config,management=management)
        return value,p

    def mark(self,tid,state,result=None):
        if state not in ('ACTIVE','FAILED','FAILED_FLAT','CLOSED_SUCCESS'):raise SafetyError('INVALID_CAPABILITY_STATE')
        self.db.execute('UPDATE allocations SET state=?,result=COALESCE(?,result) WHERE id=?',(state,encode(result) if result is not None else None,tid))
        self.event(tid,state,result)

    def complete(self,tid,reader,journal,result):
        book,entry=closed_evidence(reader,journal.path,allow_unresolved=True)
        journal.record(audit_projection(book))
        costs=execution_costs(book,entry['id'],journal)
        approval=private_read(allocation_paths(self.root,tid)['test'])
        close_acks=journal.db.execute('SELECT facts FROM acknowledgements WHERE kind=2111').fetchall()
        origin=next(o for o in book['orders'] if o.get('clientOrderId')==entry['id'] and integer(o['orderType'],1)==1 and integer(o['tradeData']['tradeSide'],1)==1)
        pids={integer(d['positionId'],1) for d in book['deals'] if integer(d['orderId'],1)==integer(origin['orderId'],1) and 'closePositionDetail' not in d}
        close_deals=[d for d in book['deals'] if integer(d['positionId'],1) in pids and 'closePositionDetail' in d and integer(d['filledVolume'])>0]
        closing_ids=set()
        for row in close_acks:
            facts=json.loads(row[0]);oid=facts.get('order',{}).get('orderId',facts.get('deal',{}).get('orderId'));did=facts.get('deal',{}).get('dealId')
            for d in close_deals:
                if (oid is not None and integer(d['orderId'],1)==integer(oid,1)) or (did is not None and integer(d['dealId'],1)==integer(did,1)):
                    closing_ids.add(integer(d['orderId'],1))
        attributed=sum(integer(d['filledVolume'],1) for d in close_deals if integer(d['orderId'],1) in closing_ids)
        commands=journal.db.execute('SELECT kind,state,sent FROM mutations').fetchall()
        verified=journal.db.execute("SELECT 1 FROM audit WHERE code='NATIVE_POSITION_PROTECTION_VERIFIED'").fetchone()
        uncertainty=journal.db.execute("SELECT 1 FROM audit WHERE code IN ('UNKNOWN_BROKER_OUTCOME','ENTRY_UNKNOWN_RECONCILING_WITHOUT_RESEND','CRITICAL_NATIVE_PROTECTION_MISSING_OR_MISMATCHED','CRITICAL_CLOSE_OUTCOME_UNKNOWN','CRITICAL_CANCEL_REMAINDER_UNKNOWN')").fetchone()
        closes=[r for r in commands if r[0]==2111]
        expected_close=len(closes)==1 and closes[0][1]=='ACCEPTED' and closes[0][2]
        passed=(result=='CLOSED_RECONCILED' and verified and attributed==100 and expected_close and not uncertainty
            and D(costs['net_realized_pnl_usd'])>=-D(approval['risk_cap'])
            and D(costs['commission_cost_usd'])<=D('.08')+D(approval['fee_buffer']))
        state='CLOSED_SUCCESS' if passed else 'FAILED_FLAT'
        payload={'automatic_close_verified':bool(passed),'costs':costs,'history_complete':True,'account_flat':True}
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.mark(tid,state,payload)
            self.db.execute('UPDATE allocations SET journal_digest=? WHERE id=?',(file_digest(journal.path),tid))
            self.db.commit()
        except BaseException:self.db.rollback();raise
        return payload


def status(config):
    root=private_path(config.token_path).parent;path=root/'capability-tests.sqlite'
    if not inspect_private_file(path):return {'state':'NO_ALLOCATIONS','execution_enabled':False,'tests':[]}
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.25)) as db:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        binding=db.execute("SELECT value FROM metadata WHERE key='profile'").fetchone()
        if not binding or binding[0]!=profile(config):raise SafetyError('CAPABILITY_PROFILE_MISMATCH')
        return {'execution_enabled':False,'tests':[{'test_id':r[0],'state':r[1],'result_available':r[2] is not None} for r in db.execute('SELECT id,state,result FROM allocations ORDER BY created')]}


def read_report(config,tid):
    """Historical reports remain readable during another test's ownership."""
    root=private_path(config.token_path).parent
    if tid!='legacy-first':allocation_paths(root,tid)
    path=root/'capability-tests.sqlite'
    if not inspect_private_file(path):raise SafetyError('CAPABILITY_REGISTRY_REQUIRED')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.25)) as db:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        binding=db.execute("SELECT value FROM metadata WHERE key='profile'").fetchone()
        if not binding or binding[0]!=profile(config):raise SafetyError('CAPABILITY_PROFILE_MISMATCH')
        row=db.execute('SELECT state,result FROM allocations WHERE id=?',(tid,)).fetchone()
        if not row or row[1] is None:raise SafetyError('CAPABILITY_REPORT_NOT_AVAILABLE')
        if tid=='legacy-first':return {'state':row[0],'classification':'MANUALLY_CLOSED_LEGACY','selected_evidence_stored_privately':True}
        history=[json.loads(r[0]) for r in db.execute('SELECT detail FROM events WHERE test_id=? AND detail IS NOT NULL ORDER BY seq',(tid,))]
        return {'state':row[0],'result':json.loads(row[1]),'historical_results':history}
