"""Renew an expired, demonstrably unused test permit; never initialize a journal."""
from contextlib import closing
import copy
import fcntl
import json
import os
import sqlite3
import time
from uuid import uuid4

from ctrader_readonly.config import SafetyError,private_read,private_write,private_path
from ctrader_readonly.client import rows,one
from ctrader_readonly.protocol import integer
from .approvals import profile,paths,validate_test,require_baseline,number,local_confirmation
from .ledger import inspect_private_file,digest,encode,AccountOwner

SAFE_AUDITS=frozenset(('CLI_ATTENTION_REQUIRED','OWNER_INTERRUPT_REQUIRES_RESUME_NO_RESEND','ENTRY_APPROVAL_RENEWED'))


def check_current_costs(client,old):
    symbol=one(rows(client._read('symbol',symbolId=[client._symbol_id]),'symbol'))
    required={'symbolId','commissionType','preciseTradingCommissionRate',
        'preciseMinCommission','minCommissionType','minCommissionAsset'}
    if not required<=symbol.keys():raise SafetyError('RENEWAL_COMMISSION_EVIDENCE_MISSING')
    if integer(symbol['symbolId'],1)!=client._symbol_id:raise SafetyError('RENEWAL_COMMISSION_EVIDENCE_CHANGED')
    from decimal import Decimal
    if (integer(symbol['commissionType'])!=2 or integer(symbol['preciseMinCommission'])!=0
        or integer(symbol['minCommissionType'])!=1 or symbol['minCommissionAsset']!='USD'
        or Decimal(integer(symbol['preciseTradingCommissionRate']))/Decimal(100000000)*2!=number(old['ui']['commission_roundtrip_per_lot'],zero=True)):
        raise SafetyError('RENEWAL_COMMISSION_EVIDENCE_CHANGED')


def require_receipt(value,config):
    """A renewed file alone is insufficient: its exact payload needs a receipt."""
    receipt=value.get('renewal_receipt')
    if receipt is None:return
    if not isinstance(receipt,str) or len(receipt)!=32:raise SafetyError('RENEWAL_RECEIPT_INVALID')
    path=private_path(value['journal_path'])
    if not inspect_private_file(path):raise SafetyError('RENEWAL_RECEIPT_MISSING')
    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.25)) as db:
            db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            binding=db.execute('SELECT profile,approval FROM binding').fetchall()
            row=db.execute('SELECT new_digest FROM approval_renewals WHERE receipt=?',(receipt,)).fetchone()
            if binding!=[(profile(config),value['id'])] or not row or row[0]!=digest(value):raise SafetyError('RENEWAL_RECEIPT_MISMATCH')
    except sqlite3.DatabaseError:raise SafetyError('RENEWAL_RECEIPT_UNAVAILABLE') from None


class RenewalGuard:
    """Same account journal owner lock; rw-existing DB; no execution recovery.

    A committed receipt precedes atomic private-file replacement. Crash before
    file replacement leaves the expired file; crash after leaves an already
    receipted permit. No crash can mint an unreceipted valid renewal.
    """
    def __init__(self,path,expected_profile,approval_id):
        self.path=private_path(path);self.profile=expected_profile;self.approval_id=approval_id
        self.owner=None;self.db=None;self.account_owner=None
        try:
            try:self.account_owner=AccountOwner(self.path.parent,expected_profile)
            except BlockingIOError:raise SafetyError('RENEWAL_OWNER_BUSY') from None
            if not inspect_private_file(self.path):raise SafetyError('RENEWAL_JOURNAL_REQUIRED')
            owner=private_path(str(self.path)+'.owner')
            if not inspect_private_file(owner):raise SafetyError('RENEWAL_OWNER_FILE_REQUIRED')
            self.owner=os.fdopen(os.open(owner,os.O_RDWR|os.O_NOFOLLOW),'r+b',buffering=0)
            try:fcntl.flock(self.owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise SafetyError('RENEWAL_OWNER_BUSY') from None
            self.db=sqlite3.connect(self.path.as_uri()+'?mode=rw',uri=True,timeout=.25,isolation_level=None)
            self.db.execute('PRAGMA synchronous=FULL');self.db.execute('BEGIN IMMEDIATE')
            self.check_unused()
        except BaseException:self.close();raise

    def check_unused(self):
        bindings=self.db.execute('SELECT profile,approval FROM binding').fetchall()
        if bindings!=[(self.profile,self.approval_id)]:raise SafetyError('RENEWAL_BINDING_MISMATCH')
        if self.db.execute('SELECT count(*) FROM mutations').fetchone()[0]:raise SafetyError('RENEWAL_MUTATION_EVIDENCE_PRESENT')
        if self.db.execute('SELECT count(*) FROM observations').fetchone()[0]:raise SafetyError('RENEWAL_EXECUTION_EVIDENCE_PRESENT')
        codes={r[0] for r in self.db.execute('SELECT code FROM audit')}
        if not codes<=SAFE_AUDITS:raise SafetyError('RENEWAL_UNEXPLAINED_AUDIT_EVIDENCE')

    def save(self,path,old,new):
        self.check_unused()
        if private_read(path)!=old:raise SafetyError('RENEWAL_APPROVAL_CHANGED')
        if old['id']!=new['id'] or old['profile']!=new['profile']:raise SafetyError('RENEWAL_BINDING_MISMATCH')
        changed={k for k in set(old)|set(new) if old.get(k)!=new.get(k)}
        if not changed<= {'issued_at','expires_at','management_until','renewal_receipt'}:raise SafetyError('RENEWAL_POLICY_CHANGE_FORBIDDEN')
        self.db.execute('''CREATE TABLE IF NOT EXISTS approval_renewals(
            receipt TEXT PRIMARY KEY,at REAL NOT NULL,old_digest TEXT NOT NULL,
            new_digest TEXT NOT NULL,old_approval TEXT NOT NULL,new_approval TEXT NOT NULL)''')
        self.db.execute('INSERT INTO approval_renewals VALUES(?,?,?,?,?,?)',
            (new['renewal_receipt'],time.time(),digest(old),digest(new),encode(old),encode(new)))
        self.db.execute('INSERT INTO audit(at,command,code) VALUES(?,?,?)',(time.time(),None,'ENTRY_APPROVAL_RENEWED'))
        self.db.commit()  # Durable owner consent/evidence BEFORE publishing permit.
        private_write(path,new)
        if private_read(path)!=new:raise SafetyError('RENEWAL_PRIVATE_READBACK_FAILED')

    def close(self):
        if self.db:self.db.close();self.db=None
        if self.owner:self.owner.close();self.owner=None
        if self.account_owner:self.account_owner.close();self.account_owner=None

    def __enter__(self):return self
    def __exit__(self,*args):self.close()


def renew_test(config_path,config,baseline,*,view_reader,market_state,prepare,stopped_controls,confirm=local_confirmation,clock=time.time,stage=lambda value:None):
    p=paths(config_path)
    stage('RENEWAL_APPROVAL_READ');old=private_read(p['test'])
    # Validate expired schema at its original issue time, never bypassing entry
    # expiry for actual use. Renewal only replaces expiry after fresh consent.
    issued=old.get('issued_at')
    if type(issued) not in (int,float) or issued>clock():raise SafetyError('RENEWAL_APPROVAL_INVALID')
    validate_test(old,config,issued)
    if clock()<old['expires_at']:raise SafetyError('RENEWAL_NOT_REQUIRED')
    require_baseline(baseline,config,clock())
    if number(old['confirmed_balance'],zero=True)!=number(baseline['balance'],zero=True) or number(old['intended_balance'])!=number(baseline['intended_balance']):raise SafetyError('RENEWAL_BASELINE_CHANGE_FORBIDDEN')
    if old['journal_path']!=str(p['journal']):raise SafetyError('RENEWAL_BINDING_MISMATCH')
    client=None
    try:
        stage('RENEWAL_JOURNAL_GUARD')
        with RenewalGuard(p['journal'],profile(config),old['id']) as guard:
            stage('RENEWAL_CONTROLS');stopped_controls(old['controls_database'])
            new=copy.deepcopy(old)
            now=clock();new.update(issued_at=now,expires_at=now+600,management_until=now+3600)
            # Receipt is attached only after planning; do not pretend an
            # uncommitted renewed file is authorized during preflight.
            new.pop('renewal_receipt',None)
            stage('RENEWAL_READ_ONLY_PREFLIGHT');client=view_reader(config)
            market=market_state(client)
            check_current_costs(client,old)
            if client.status()['order_history_count'] or client.status()['deal_history_count']:raise SafetyError('RENEWAL_BROKER_HISTORY_REVIEW_REQUIRED')
            _,_,proposal=prepare(config,new,baseline,market,clock())
            client.disconnect('AWAITING_RENEWAL_CONSENT');client=None
            stage('RENEWAL_LOCAL_CONFIRMATION')
            confirm('Renew the SAME unused Razor DEMO test and bound journal. Identity, quantity, risk/cost/stop policy remain identical. Actual rounding/posting remain unobserved; review the existing conservative allowance. Includes residual cancellation and full normal/emergency close.\n'+json.dumps(proposal,indent=2)+'\nType RENEW ONE DEMO '+str(config.expected_demo_login),'RENEW ONE DEMO '+str(config.expected_demo_login))
            stage('RENEWAL_READ_ONLY_RECHECK');stopped_controls(old['controls_database'])
            client=view_reader(config);market=market_state(client)
            check_current_costs(client,old)
            if client.status()['order_history_count'] or client.status()['deal_history_count']:raise SafetyError('RENEWAL_BROKER_HISTORY_REVIEW_REQUIRED')
            now=clock();new.update(issued_at=now,expires_at=now+600,management_until=now+3600)
            prepare(config,new,baseline,market,now)
            stage('RENEWAL_PRIVATE_PERSISTENCE');new['renewal_receipt']=uuid4().hex
            guard.save(p['test'],old,new)
            validate_test(private_read(p['test']),config,clock())
    finally:
        if client:client.disconnect('RENEWAL_FINISHED')
