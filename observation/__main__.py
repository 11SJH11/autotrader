"""Run a foreground deterministic replay. No systemd, broker or private sources."""
import argparse
from dataclasses import asdict
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import signal
import threading

from audit_phase26.compare import frames
from signal_adapters.t02 import Candle
from .worker import ReplayEvent,Worker,encode


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True)
    parser.add_argument('--start',default='2026-03-02')
    parser.add_argument('--end',default='2026-03-04')
    parser.add_argument('--delay',type=float,default=.1,help='Real seconds per replay candle')
    parser.add_argument('--max-events',type=int)
    parser.add_argument('--once',action='store_true',help='Exit at replay EOF instead of idling with heartbeat')
    parser.add_argument('--controls-database',help='Explicit observation control store; defaults stopped/suspended')
    args=parser.parse_args(argv)
    if args.delay<0 or (args.max_events is not None and args.max_events<=0):parser.error('Invalid pacing or event limit')
    stop=threading.Event()
    for kind in (signal.SIGINT,signal.SIGTERM):signal.signal(kind,lambda *_:stop.set())
    worker=None
    try:
        frame=frames();subset=frame[(frame.timestamp>=args.start)&(frame.timestamp<args.end)]
        if subset.empty:raise ValueError('EMPTY_REPLAY')
        source_hash=hashlib.sha256(subset.to_csv(index=False).encode()).hexdigest()
        worker=Worker(args.database,source_hash,controls_path=args.controls_database)
        processed=0
        for index,row in enumerate(subset.itertuples(index=False)):
            if index<=worker.meta['cursor']:continue
            if stop.is_set() or (args.max_events and processed>=args.max_events):break
            candle=Candle(row.timestamp.to_pydatetime(),*(Decimal(str(v)) for v in (row.open,row.high,row.low,row.close)))
            event=ReplayEvent(index,candle,datetime.now(timezone.utc))
            worker.process(event);processed+=1
            stop.wait(args.delay)
        else:
            worker.meta['fresh_input']=False;worker.heartbeat('REPLAY_COMPLETE')
            while not args.once and not stop.wait(1):worker.heartbeat('REPLAY_COMPLETE')
        worker.stop()
        print('OFFLINE_REPLAY_STOPPED; no broker execution path')
        return 0
    except Exception:
        if worker:
            try:worker.error('WORKER_FAILED')
            except Exception:pass
        print('OFFLINE_REPLAY_FAILED; inspect sanitized diagnostic status')
        return 1
    finally:
        if worker:worker.close()

if __name__=='__main__':raise SystemExit(main())
