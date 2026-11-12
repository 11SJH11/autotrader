"""Private, account-fenced mutation journal. Consumed sends are never retried."""
import fcntl
import hashlib
import json
import os
import stat
from pathlib import Path
import sqlite3
import time
from uuid import uuid4
from ctrader_readonly.config import private_path,SafetyError


def encode(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)

def digest(value):return hashlib.sha256(encode(value).encode()).hexdigest()


class Journal:
    def __init__(self,path,profile,approval_id,*,clock=time.time):
        self.clock=clock;self.path=private_path(path);self.owner=None;self.db=None
        info=self.path.parent.stat()
        if info.st_uid!=os.getuid() or info.st_mode&0o777!=0o700:raise SafetyError('PRIVATE_DIRECTORY_MODE_REQUIRED')
        owner_path=private_path(str(self.path)+'.owner')
        descriptor=os.open(owner_path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        info=os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o777!=0o600:
            os.close(descriptor);raise SafetyError('PRIVATE_FILE_MODE_REQUIRED')
        self.owner=os.fdopen(descriptor,'a+b')
        try:
            fcntl.flock(self.owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
            fd=os.open(self.path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
            try:
                mode=os.fstat(fd)
                if not stat.S_ISREG(mode.st_mode) or mode.st_uid!=os.getuid() or mode.st_mode&0o777!=0o600:raise SafetyError('PRIVATE_FILE_MODE_REQUIRED')
            finally:os.close(fd)
            self.db=sqlite3.connect(self.path,timeout=2,isolation_level=None)
            self.db.row_factory=sqlite3.Row
            # DELETE journal avoids persistent broadly-readable WAL sidecars.
            self.db.execute('PRAGMA journal_mode=DELETE');self.db.execute('PRAGMA synchronous=FULL')
            self.db.executescript('''CREATE TABLE IF NOT EXISTS binding(id INTEGER PRIMARY KEY,profile TEXT,approval TEXT);
                CREATE TABLE IF NOT EXISTS mutations(id TEXT PRIMARY KEY,key TEXT UNIQUE,kind INTEGER,entry_slot INTEGER UNIQUE,payload TEXT,digest TEXT,state TEXT,sent INTEGER,at REAL);
                CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY,at REAL,command TEXT,code TEXT);
                CREATE TABLE IF NOT EXISTS observations(seq INTEGER PRIMARY KEY,at REAL,data TEXT);''')
            row=self.db.execute('SELECT profile,approval FROM binding').fetchone()
            if row and tuple(row)!=(profile,approval_id):raise SafetyError('FIRST_TEST_JOURNAL_ALREADY_BOUND')
            self.db.execute('INSERT OR IGNORE INTO binding VALUES(1,?,?)',(profile,approval_id))
            self.db.execute("UPDATE mutations SET state='UNKNOWN' WHERE state='SUBMITTING'")
            directory=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(directory)
            finally:os.close(directory)
        except Exception:
            if self.db:self.db.close()
            if self.owner:self.owner.close()
            raise

    def close(self):
        if self.db:self.db.close();self.db=None
        if self.owner:self.owner.close();self.owner=None

    def audit(self,cid,code):
        self.db.execute('INSERT INTO audit(at,command,code) VALUES(?,?,?)',(self.clock(),cid,code))

    def claim(self,key,kind,payload,*,cid=None):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            prior=self.db.execute('SELECT * FROM mutations WHERE key=?',(key,)).fetchone()
            if prior:
                if prior['kind']!=kind or prior['digest']!=digest(payload):raise SafetyError('IDEMPOTENCY_PAYLOAD_CONFLICT')
                self.db.rollback();return dict(prior),False
            if kind==2106 and self.db.execute('SELECT 1 FROM mutations WHERE entry_slot=1').fetchone():raise SafetyError('FIRST_ENTRY_ALREADY_CLAIMED')
            if self.db.execute("SELECT 1 FROM mutations WHERE state IN ('SUBMITTING','UNKNOWN')").fetchone():
                # Emergency management is explicitly allowed on positively owned
                # exposure despite an unknown entry; the transport rechecks target.
                if kind==2106:raise SafetyError('UNKNOWN_OUTCOME_RECONCILIATION_REQUIRED')
            cid=cid or uuid4().hex
            slot=1 if kind==2106 else None
            self.db.execute('INSERT INTO mutations VALUES(?,?,?,?,?,?,?,?,?)',
                (cid,key,kind,slot,encode(payload),digest(payload),'SUBMITTING',0,self.clock()))
            self.audit(cid,'DURABLE_SUBMISSION_CLAIM');self.db.commit()
            return dict(self.db.execute('SELECT * FROM mutations WHERE id=?',(cid,)).fetchone()),True
        except BaseException:self.db.rollback();raise

    def consume_send(self,cid,kind,payload):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT * FROM mutations WHERE id=?',(cid,)).fetchone()
            if not row or row['kind']!=kind or row['digest']!=digest(payload) or row['state']!='SUBMITTING' or row['sent']:
                raise SafetyError('MUTATION_ALREADY_SENT_OR_NOT_CLAIMED')
            self.db.execute('UPDATE mutations SET sent=1 WHERE id=?',(cid,))
            self.audit(cid,'SEND_FENCE_CONSUMED');self.db.commit()
        except BaseException:self.db.rollback();raise

    def outcome(self,cid,state,code):
        if state not in ('UNKNOWN','ACCEPTED','REJECTED','RECONCILED','CLOSED'):raise SafetyError('INVALID_JOURNAL_OUTCOME')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('UPDATE mutations SET state=? WHERE id=?',(state,cid));self.audit(cid,code);self.db.commit()
        except BaseException:self.db.rollback();raise

    def record(self,book):
        # Raw selected trading facts contain IDs and stay in this private store.
        self.db.execute('INSERT INTO observations(at,data) VALUES(?,?)',(self.clock(),encode(book)))

    def entry(self):
        row=self.db.execute('SELECT * FROM mutations WHERE entry_slot=1').fetchone()
        return dict(row) if row else None

    def summary(self):
        return {'entry_claims':self.db.execute('SELECT count(*) FROM mutations WHERE entry_slot=1').fetchone()[0],
            'mutations':[{'kind':r[0],'state':r[1],'sent':bool(r[2])} for r in self.db.execute('SELECT kind,state,sent FROM mutations')],
            'audit_codes':[r[0] for r in self.db.execute('SELECT code FROM audit ORDER BY seq')],
            'execution_enabled':False}
