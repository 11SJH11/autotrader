"""JSON schemas are a narrow outbound subset of the official Open API messages.

No generic raw-send interface: the transport validates this allowlist again.
https://help.ctrader.com/open-api/messages/
https://help.ctrader.com/open-api/model-messages/#protooapayloadtype
"""
from collections import deque
import json
import time
from types import MappingProxyType
from .config import SafetyError

DEMO_URL = 'wss://demo.ctraderapi.com:5036'
# name: (request type, response type, required keys, optional keys)
SCHEMAS = MappingProxyType({
    'heartbeat': (51,51,(),()),
    'application': (2100,2101,('clientId','clientSecret'),()),
    'accounts': (2149,2150,('accessToken',),()),
    'account': (2102,2103,('ctidTraderAccountId','accessToken'),()),
    'trader': (2121,2122,('ctidTraderAccountId',),()),
    'assets': (2112,2113,('ctidTraderAccountId',),()),
    'symbols': (2114,2115,('ctidTraderAccountId',),('includeArchivedSymbols',)),
    'symbol': (2116,2117,('ctidTraderAccountId','symbolId'),()),
    'reconcile': (2124,2125,('ctidTraderAccountId',),()),
    'orders': (2175,2176,('ctidTraderAccountId','fromTimestamp','toTimestamp'),()),
    'deals': (2133,2134,('ctidTraderAccountId','fromTimestamp','toTimestamp'),('maxRows',)),
    'margin': (2139,2140,('ctidTraderAccountId','symbolId','volume'),()),
    'pnl': (2187,2188,('ctidTraderAccountId',),()),
    'spots': (2127,2128,('ctidTraderAccountId','symbolId','subscribeToSpotTimestamp'),()),
    'leverage': (2177,2178,('ctidTraderAccountId','leverageId'),()),
})


def integer(value, minimum=0):
    # JSON int64 may be encoded as a decimal string; reject floats/bools.
    if isinstance(value,str) and value.isascii() and (value.isdigit() or (value.startswith('-') and value[1:].isdigit())):
        value = int(value)
    if type(value) is not int or not minimum <= value <= 2**63-1:
        raise SafetyError('INVALID_SCHEMA_INTEGER')
    return value


def enum(value, names):
    if isinstance(value,str) and value in names:
        return names[value]
    return integer(value)


def validate_frame(frame):
    if not isinstance(frame,dict) or set(frame) != {'clientMsgId','payloadType','payload'}:
        raise SafetyError('OUTBOUND_NOT_ALLOWED')
    schema = next((s for s in SCHEMAS.values() if s[0]==frame['payloadType']),None)
    if schema is None or type(frame['payloadType']) is not int:
        raise SafetyError('OUTBOUND_NOT_ALLOWED')
    _,_,required,optional = schema
    payload = frame['payload']
    if not isinstance(payload,dict) or set(required)-payload.keys() or payload.keys()-set(required+optional):
        raise SafetyError('OUTBOUND_SCHEMA_REJECTED')
    if not isinstance(frame['clientMsgId'],str) or not frame['clientMsgId']:
        raise SafetyError('CORRELATION_REQUIRED')
    for key,value in payload.items():
        if key in ('ctidTraderAccountId','fromTimestamp','toTimestamp','maxRows','leverageId'):
            integer(value, 0 if key.endswith('Timestamp') else 1)
        elif key in ('symbolId','volume'):
            repeated = key=='volume' or frame['payloadType'] in (2116,2127)
            if repeated != isinstance(value,list):
                raise SafetyError('OUTBOUND_SCHEMA_REJECTED')
            values = value if repeated else [value]
            if not values:
                raise SafetyError('OUTBOUND_SCHEMA_REJECTED')
            for item in values:
                integer(item,1)
        elif key in ('includeArchivedSymbols','subscribeToSpotTimestamp'):
            if type(value) is not bool:
                raise SafetyError('OUTBOUND_SCHEMA_REJECTED')
        elif not isinstance(value,str) or not value:
            raise SafetyError('OUTBOUND_SCHEMA_REJECTED')

    if 'fromTimestamp' in payload and integer(payload['fromTimestamp'])>integer(payload['toTimestamp']):
        raise SafetyError('OUTBOUND_SCHEMA_REJECTED')


class RateLimiter:
    """Sliding windows: <=10 total requests/s and <=2 historical requests/s."""
    def __init__(self,clock=time.monotonic,sleep=time.sleep):
        self.clock,self.sleep = clock,sleep
        self.all,self.history = deque(),deque()
        self.wait_count=0
        self.wait_seconds=0.0
        self.peak_requests_per_second=0
        self.peak_history_per_second=0

    def acquire(self,historical=False):
        while True:
            now=self.clock()
            for queue in (self.all,self.history):
                while queue and now-queue[0]>=1:
                    queue.popleft()
            delays=[]
            if len(self.all)>=10:
                delays.append(1-(now-self.all[0]))
            if historical and len(self.history)>=2:
                delays.append(1-(now-self.history[0]))
            if not delays:
                self.all.append(now)
                if historical:
                    self.history.append(now)
                self.peak_requests_per_second=max(self.peak_requests_per_second,len(self.all))
                self.peak_history_per_second=max(self.peak_history_per_second,len(self.history))
                return
            delay=max(0.001,max(delays))
            self.wait_count+=1;self.wait_seconds+=delay
            self.sleep(delay)


class DemoWebSocket:
    """TLS verified, no redirects or proxy inheritance, and fixed demo endpoint."""
    def __init__(self):
        self.socket=None

    def connect(self):
        # Lazy optional import: importing the observer never connects or loads tokens.
        import websocket
        websocket.enableTrace(False)
        try:
            self.socket=websocket.create_connection(DEMO_URL,timeout=5,
                redirect_limit=0,http_no_proxy=['*'],enable_multithread=False)
            if self.socket.getstatus()!=101:
                self.close()
                raise SafetyError('WEBSOCKET_UPGRADE_REQUIRED')
        except Exception:
            raise SafetyError('NETWORK_CONNECT_FAILED') from None

    def send(self,frame):
        validate_frame(frame)
        try:
            self.socket.send(json.dumps(frame,separators=(',',':')))
        except Exception:
            raise SafetyError('NETWORK_SEND_FAILED') from None

    def receive(self,timeout):
        import websocket
        try:
            self.socket.settimeout(timeout)
            data=self.socket.recv()
            if not isinstance(data,str) or not data or len(data)>4*1024*1024:
                raise SafetyError('INVALID_NETWORK_FRAME')
            value=json.loads(data)
            if not isinstance(value,dict):
                raise SafetyError('INVALID_NETWORK_FRAME')
            return value
        except websocket.WebSocketTimeoutException:
            raise TimeoutError() from None
        except Exception:
            raise SafetyError('NETWORK_RECEIVE_FAILED') from None

    def close(self):
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
        self.socket=None
