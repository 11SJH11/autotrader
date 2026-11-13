"""Baseline-only regressions: synthetic account and a real pseudo-terminal."""
import errno
import json
import os
import pty
import select
import time

import pytest
from test_phase31 import native,private_dir,ready
from ctrader_execution import harness
from ctrader_execution.approvals import profile,paths,require_baseline
from ctrader_readonly.config import SafetyError,private_write,private_read


def configured(native,private_dir,monkeypatch):
    config=native[0];client=ready(native);cp=private_dir/'config.json';p=paths(cp)
    # This shared synthetic fixture creates an execution journal; baseline needs none.
    native[-1].close();p['journal'].unlink()
    monkeypatch.setattr(harness.Config,'load',lambda *args,**kwargs:config)
    monkeypatch.setattr(harness,'view_reader',lambda config:client)
    private_write(p['pending'],{'profile':profile(config),'intended_balance':'10000'})
    return cp,p


def test_real_terminal_baseline_confirmation_persists_without_execution(native,private_dir,monkeypatch):
    cp,p=configured(native,private_dir,monkeypatch)
    pid,fd=pty.fork()
    if pid==0:
        import sys
        sys.stdout=os.fdopen(os.dup(1),'w',buffering=1)
        sys.stderr=os.fdopen(os.dup(2),'w',buffering=1)
        result=harness.main(['confirm-baseline','--config',str(cp)])
        # os._exit does not flush pytest's inherited capture buffers.
        import sys
        sys.stdout.flush();sys.stderr.flush();os._exit(result)
    output=b'';sent=False;deadline=time.monotonic()+10
    try:
        while time.monotonic()<deadline:
            if select.select([fd],[],[],.1)[0]:
                try:chunk=os.read(fd,4096)
                except OSError as exc:
                    if exc.errno==errno.EIO:break
                    raise
                if not chunk:break
                output+=chunk
                if b'input hidden' in output and not sent:
                    os.write(fd,b'CONFIRM 7654321 10000 10000\n');sent=True
        else:
            os.kill(pid,9);pytest.fail('Synthetic terminal confirmation timed out')
    finally:
        os.close(fd);_,status=os.waitpid(pid,0)
    assert os.waitstatus_to_exitcode(status)==0 and sent
    assert b'BASELINE_CONFIRMED_PRIVATELY' in output
    # Phrase is displayed once in the instructions; typed answer must not echo.
    assert output.count(b'CONFIRM 7654321 10000 10000')==1
    value=require_baseline(private_read(p['baseline']),native[0])
    assert value['confirmed'] is True and value['balance']=='10000'
    assert p['baseline'].stat().st_mode&0o777==0o600
    assert not p['journal'].exists() and not p['test'].exists() and not p['token'].exists()
    assert native[4].peer.calls==0


@pytest.mark.parametrize('problem,code,stage',[
    ('missing','PRIVATE_FILE_MISSING','BASELINE_PENDING_READ'),
    ('permissions','PRIVATE_FILE_MODE_REQUIRED','BASELINE_PENDING_READ'),
    ('profile','BASELINE_PROFILE_MISMATCH','BASELINE_PENDING_READ'),
    ('tty','LOCAL_OWNER_TTY_REQUIRED','BASELINE_LOCAL_CONFIRMATION'),
    ('declined','LOCAL_CONFIRMATION_NOT_ACCEPTED','BASELINE_LOCAL_CONFIRMATION'),
    ('persist','PRIVATE_FILE_ACCESS_DENIED','BASELINE_PRIVATE_PERSISTENCE'),
    ('network','NETWORK_CONNECT_FAILED','BASELINE_READ_ONLY_VERIFY')])
def test_baseline_failure_codes_never_suggest_unknown_trade(native,private_dir,monkeypatch,capsys,problem,code,stage):
    cp,p=configured(native,private_dir,monkeypatch)
    monkeypatch.setattr(harness,'local_confirmation',lambda *args:None)
    def fail(reason):raise SafetyError(reason)
    if problem=='missing':p['pending'].unlink()
    elif problem=='permissions':p['pending'].chmod(0o644)
    elif problem=='profile':private_write(p['pending'],{'profile':'wrong','intended_balance':'10000'})
    elif problem=='tty':monkeypatch.setattr(harness,'local_confirmation',lambda *args:fail('LOCAL_OWNER_TTY_REQUIRED'))
    elif problem=='declined':monkeypatch.setattr(harness,'local_confirmation',lambda *args:fail('LOCAL_CONFIRMATION_NOT_ACCEPTED'))
    elif problem=='network':monkeypatch.setattr(harness,'view_reader',lambda *args:fail('NETWORK_CONNECT_FAILED'))
    elif problem=='persist':
        def denied(*args):raise PermissionError('SYNTHETIC_PRIVATE_INFORMATION')
        monkeypatch.setattr(harness,'private_write',denied)
    assert harness.main(['confirm-baseline','--config',str(cp)])==1
    output=capsys.readouterr().out
    assert 'stage='+stage in output and 'code='+code in output
    assert 'NO_ORDER_ATTEMPTED' in output
    assert all(word not in output for word in ('journal','resend','UNKNOWN','7654321','SYNTHETIC_PRIVATE_INFORMATION'))
    assert not p['baseline'].exists() and not p['journal'].exists()
    assert native[4].peer.calls==0


def test_confirmation_expiry_is_not_pending_observation_expiry(native,private_dir,monkeypatch,capsys):
    cp,p=configured(native,private_dir,monkeypatch)
    private_write(p['pending'],{'profile':profile(native[0]),'intended_balance':'10000','observed_at':0})
    monkeypatch.setattr(harness,'local_confirmation',lambda *args:None)
    assert harness.main(['confirm-baseline','--config',str(cp)])==0
    value=private_read(p['baseline']);assert require_baseline(value,native[0])
    value['confirmed_at']=time.time()-86401
    with pytest.raises(SafetyError,match='BASELINE_CONFIRMATION_STALE'):require_baseline(value,native[0])
    value['confirmed_at']=time.time()+60
    with pytest.raises(SafetyError,match='BASELINE_CONFIRMATION_STALE'):require_baseline(value,native[0])
