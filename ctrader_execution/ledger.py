"""Private, account-fenced mutation journal. Consumed sends are never retried."""
import fcntl
from contextlib import closing
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


def inspect_private_file(path):
    """Metadata only: never read credential/token contents or create a file."""
    path=private_path(path)
    parent=path.parent.stat()
    if parent.st_uid!=os.getuid() or stat.S_IMODE(parent.st_mode)!=0o700:raise SafetyError('PRIVATE_DIRECTORY_MODE_REQUIRED')
    try:info=path.lstat()
    except FileNotFoundError:return False
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600:raise SafetyError('PRIVATE_FILE_MODE_REQUIRED')
    return True


def read_status(path,expected_profile):
    """Read a consistent projection without Journal's creation/recovery writes.

    SUBMITTING is *reported* as uncertain; stored rows are never recovered here.
    Missing, uninitialized and unreadable databases are distinct from an UNKNOWN
    broker outcome. No approval/token/baseline validity is needed for inspection.
    """
    result={'journal':'ABSENT','entry_claims':0,'mutations':[],'state':'NOT_STARTED',
        'execution_enabled':False}
    if not inspect_private_file(path):return result
    result['journal']='PRESENT'
    try:
        with closing(sqlite3.connect(private_path(path).as_uri()+'?mode=ro',uri=True,timeout=.25)) as db:
            db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not tables:return dict(result,journal='UNINITIALIZED')
            if not {'binding','mutations','audit','observations'}<=tables:raise SafetyError('STATUS_JOURNAL_SCHEMA_INVALID')
            binding=db.execute('SELECT profile FROM binding').fetchall()
            if len(binding)!=1 or binding[0][0]!=expected_profile:raise SafetyError('STATUS_JOURNAL_PROFILE_MISMATCH')
            records=db.execute('SELECT kind,state,sent,entry_slot FROM mutations').fetchall()
            entries=[r for r in records if r[3]==1]
            if len(entries)>1 or any(r[0] not in (2106,2108,2109,2110,2111) or r[1] not in ('SUBMITTING','UNKNOWN','ACCEPTED','REJECTED','RECONCILED','CLOSED') or r[2] not in (0,1) or r[3] not in (None,1) or (r[0]==2106)!=(r[3]==1) for r in records):raise SafetyError('STATUS_JOURNAL_SCHEMA_INVALID')
            if records and not entries:raise SafetyError('STATUS_JOURNAL_SCHEMA_INVALID')
            result['entry_claims']=len(entries)
            result['mutations']=[{'kind':r[0],'state':r[1],'sent':bool(r[2])} for r in records]
            if any(r[1] in ('SUBMITTING','UNKNOWN') for r in records):result['state']='UNKNOWN'
            elif entries:
                state=entries[0][1]
                if state=='CLOSED':
                    proof=db.execute("SELECT 1 FROM audit WHERE code='HISTORY_CONFIRMED_FLAT' LIMIT 1").fetchone()
                    result['state']='EXECUTED' if proof else 'UNCONFIRMED'
                else:result['state']={'ACCEPTED':'SUBMITTED','REJECTED':'REJECTED','RECONCILED':'RECONCILED'}[state]
            return result
    except sqlite3.OperationalError as exc:
        # No provider/SQLite exception text in public output, including paths.
        code=getattr(exc,'sqlite_errorcode',None)
        raise SafetyError('STATUS_JOURNAL_BUSY' if code in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED) else 'STATUS_JOURNAL_UNREADABLE') from None
    except sqlite3.DatabaseError:raise SafetyError('STATUS_JOURNAL_UNREADABLE') from None


class AccountOwner:
    """One execution owner across every journal in the pinned private profile.

    All managed allocations must use this canonical directory. Copying private
    configuration/state to bypass it is outside the supported owner boundary.
    """
    def __init__(self,root,profile):
        self.root=private_path(root/'execution.owner').parent;self.profile=profile;self.file=None;self.global_file=None
        info=self.root.stat()
        if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:raise SafetyError('PRIVATE_DIRECTORY_MODE_REQUIRED')
        # Also serialize copies of the same pinned approved profile across private
        # directories. This is a lifetime OS fence; durable outcomes remain in the
        # canonical registry, never in /tmp. No environment-dependent lock root.
        global_root=private_path(Path('/tmp')/('autotrader-owner-'+str(os.getuid()))/'probe').parent
        try:os.mkdir(global_root,0o700)
        except FileExistsError:pass
        info=global_root.stat()
        if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:raise SafetyError('PRIVATE_DIRECTORY_MODE_REQUIRED')
        if not isinstance(profile,str) or len(profile)!=64 or any(c not in '0123456789abcdef' for c in profile):raise SafetyError('INVALID_OWNER_PROFILE')
        global_path=private_path(global_root/(profile+'.owner'))
        global_fd=os.open(global_path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:
            info=os.fstat(global_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600:raise SafetyError('PRIVATE_FILE_MODE_REQUIRED')
            fcntl.flock(global_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.global_file=os.fdopen(global_fd,'a+b');global_fd=None
        finally:
            if global_fd is not None:os.close(global_fd)
        try:fd=os.open(private_path(self.root/'execution.owner'),os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        except BaseException:self.close();raise
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600:raise SafetyError('PRIVATE_FILE_MODE_REQUIRED')
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.file=os.fdopen(fd,'a+b');fd=None
        except BaseException:self.close();raise
        finally:
            if fd is not None:os.close(fd)

    def require(self,root,profile):
        if self.file is None or self.file.closed or self.global_file is None or self.global_file.closed or self.root!=root or self.profile!=profile:raise SafetyError('ACCOUNT_EXECUTION_OWNER_REQUIRED')

    def close(self):
        if self.file:self.file.close();self.file=None
        if self.global_file:self.global_file.close();self.global_file=None


class Journal:
    def __init__(self,path,profile,approval_id,*,clock=time.time,account_owner=None):
        self.clock=clock;self.path=private_path(path);self.owner=None;self.db=None
        self.account_owner=None;self.owns_account=account_owner is None
        info=self.path.parent.stat()
        if info.st_uid!=os.getuid() or info.st_mode&0o777!=0o700:raise SafetyError('PRIVATE_DIRECTORY_MODE_REQUIRED')
        try:
            self.account_owner=account_owner or AccountOwner(self.path.parent,profile)
            self.account_owner.require(self.path.parent,profile)
            owner_path=private_path(str(self.path)+'.owner')
            descriptor=os.open(owner_path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
            info=os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o777!=0o600:
                os.close(descriptor);raise SafetyError('PRIVATE_FILE_MODE_REQUIRED')
            self.owner=os.fdopen(descriptor,'a+b')
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
                CREATE TABLE IF NOT EXISTS observations(seq INTEGER PRIMARY KEY,at REAL,data TEXT);
                CREATE TABLE IF NOT EXISTS acknowledgements(seq INTEGER PRIMARY KEY,command TEXT,kind INTEGER,at REAL,facts TEXT);
                CREATE TABLE IF NOT EXISTS references_at_send(command TEXT PRIMARY KEY,bid TEXT,ask TEXT,at REAL);''')
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
            if self.owns_account and self.account_owner:self.account_owner.close()
            raise

    def close(self):
        if self.db:self.db.close();self.db=None
        if self.owner:self.owner.close();self.owner=None
        if self.owns_account and self.account_owner:self.account_owner.close()

    def audit(self,cid,code):
        self.db.execute('INSERT INTO audit(at,command,code) VALUES(?,?,?)',(self.clock(),cid,code))

    def claim(self,key,kind,payload,*,cid=None):
        self.account_owner.require(self.path.parent,self.account_owner.profile)
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
        self.account_owner.require(self.path.parent,self.account_owner.profile)
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

    def acknowledge(self,cid,kind,body):
        # Selected correlation facts only, no arbitrary broker descriptions.
        facts={'executionType':body['executionType']}
        for name,keys in (('order',('orderId','positionId','clientOrderId')),('position',('positionId',)),('deal',('dealId','orderId','positionId','filledVolume'))):
            if name in body:facts[name]={k:body[name][k] for k in keys if k in body[name]}
        self.db.execute('INSERT INTO acknowledgements(command,kind,at,facts) VALUES(?,?,?,?)',(cid,kind,self.clock(),encode(facts)))

    def reference(self,cid,quote):
        self.db.execute('INSERT OR IGNORE INTO references_at_send VALUES(?,?,?,?)',(cid,str(quote.bid),str(quote.ask),quote.at.timestamp()))

    def entry(self):
        row=self.db.execute('SELECT * FROM mutations WHERE entry_slot=1').fetchone()
        return dict(row) if row else None

    def summary(self):
        return {'entry_claims':self.db.execute('SELECT count(*) FROM mutations WHERE entry_slot=1').fetchone()[0],
            'mutations':[{'kind':r[0],'state':r[1],'sent':bool(r[2])} for r in self.db.execute('SELECT kind,state,sent FROM mutations')],
            'audit_codes':[r[0] for r in self.db.execute('SELECT code FROM audit ORDER BY seq')],
            'execution_enabled':False}
