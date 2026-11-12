"""Sample the known local read-only observer projection; no private config access."""
import json
import time
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import build_opener,ProxyHandler


def main():
    opener=build_opener(ProxyHandler({}));samples=[]
    allowed={'read_only','environment','health','connected','view_authorized','heartbeat_fresh',
        'snapshot_fresh','quote_fresh','history_complete','quote','read_operations','last_error',
        'account','symbol','pending_count','position_count','order_history_count',
        'deal_history_count','margin_information_available','free_margin_authoritative'}
    for index in range(4):
        if index:time.sleep(15)
        try:
            with opener.open('http://127.0.0.1:18769/status',timeout=3) as response:value=json.load(response)
            status={key:value[key] for key in allowed if key in value}
        except Exception:status={'health':'LOCAL_STATUS_UNAVAILABLE'}
        samples.append({'checked_at_utc':datetime.now(timezone.utc).isoformat(),'status':status})
    output=Path(__file__).resolve().parents[1]/'docs/ctrader_observer_after_subscription_fix.json'
    output.write_text(json.dumps({'read_only':True,'samples':samples,
        'limitations':['Volume range anomaly unresolved; execution disabled',
        'History covers requested last 24 hours only','Free margin is not authoritative']},indent=2)+'\n')
    quotes=[x['status'].get('quote') for x in samples]
    print(json.dumps({'samples':len(samples),'health':[x['status'].get('health') for x in samples],
        'all_quotes_fresh':all(x['status'].get('quote_fresh') for x in samples),
        'all_snapshots_fresh':all(x['status'].get('snapshot_fresh') for x in samples),
        'all_heartbeats_fresh':all(x['status'].get('heartbeat_fresh') for x in samples),
        'quote_timestamps_advance':all(quotes) and all(quotes[i]['timestamp_utc']<quotes[i+1]['timestamp_utc'] for i in range(3)),
        'quote_age_seconds':[round(q['server_age_seconds'],3) if q else None for q in quotes],
        'last_errors':[x['status'].get('last_error') for x in samples]}))

if __name__=='__main__':main()
