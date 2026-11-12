"""Deterministic schema peer for tests only. No sockets, tokens or credentials.

Unlike a broker idempotency service, repeated NewOrder calls create extra orders.
The coordinator must prevent repeats; clientOrderId is correlation, not a guarantee.
"""
import copy
from decimal import Decimal as D
from .schema import validate_mutation
from ctrader_readonly.config import SafetyError


class OfflinePeer:
    def __init__(self,account_id=123,symbol_id=456):
        self.account_id=account_id;self.symbol_id=symbol_id
        self.orders={};self.positions={};self.deals={};self.calls=0
        self.mode='normal';self.fill_fraction=D(1);self.market_price=D('2000')
        self.sequence=100;self.history_complete=True;self.connected=True
        self.protection_missing=False;self.last_frame=None

    def next_id(self):self.sequence+=1;return self.sequence

    def event(self,cid,kind,order=None,position=None,deal=None):
        p={'ctidTraderAccountId':self.account_id,'executionType':kind}
        if order:p['order']=copy.deepcopy(order)
        if position:p['position']=copy.deepcopy(position)
        if deal:p['deal']=copy.deepcopy(deal)
        return {'payloadType':2126,'clientMsgId':cid,'payload':p}

    def send(self,frame):
        validate_mutation(frame);self.calls+=1;self.last_frame=copy.deepcopy(frame)
        if not self.connected:raise ConnectionError()
        if self.mode=='timeout_before':raise TimeoutError()
        cid=frame['clientMsgId'];p=frame['payload'];kind=frame['payloadType']
        if p['ctidTraderAccountId']!=self.account_id:raise SafetyError('DEMO_ACCOUNT_MISMATCH')
        if self.mode=='reject':return {'payloadType':2132,'clientMsgId':cid,'payload':{'ctidTraderAccountId':self.account_id,'errorCode':'TRADING_BAD_VOLUME'}}
        if kind==2106:
            if p['symbolId']!=self.symbol_id:raise SafetyError('RESPONSE_SYMBOL_MISMATCH')
            oid=self.next_id()
            td={'symbolId':self.symbol_id,'volume':p['volume'],'tradeSide':p['tradeSide'],'label':p['label']}
            order={'orderId':oid,'tradeData':td,'orderType':p['orderType'],'orderStatus':1,'clientOrderId':p['clientOrderId'],'executedVolume':0}
            order.update({k:p[k] for k in ('limitPrice','stopLoss','takeProfit','relativeStopLoss','relativeTakeProfit') if k in p})
            self.orders[oid]=order
            result=self.event(cid,2,order)
            if p['orderType']==1:result=self.fill(oid,int(D(p['volume'])*self.fill_fraction),self.market_price,cid)
        elif kind==2108:
            order=self.orders[p['orderId']]
            if order['orderStatus']!=1:return {'payloadType':2132,'clientMsgId':cid,'payload':{'ctidTraderAccountId':self.account_id,'errorCode':'ORDER_NOT_FOUND'}}
            order['orderStatus']=5;result=self.event(cid,5,order)
        elif kind==2109:
            order=self.orders[p['orderId']]
            if order['orderStatus']!=1 or order['executedVolume']:raise SafetyError('ORDER_NOT_AMENDABLE')
            order['tradeData']['volume']=p['volume']
            order.update({k:p[k] for k in ('limitPrice','stopLoss','takeProfit')})
            result=self.event(cid,4,order)
        elif kind==2110:
            position=self.positions[p['positionId']]
            if position['positionStatus']!=1:raise SafetyError('POSITION_NOT_OPEN')
            position.update(stopLoss=p['stopLoss'],takeProfit=p['takeProfit'])
            result=self.event(cid,4,position=position)
        elif kind==2111:
            position=self.positions[p['positionId']];volume=p['volume']
            if position['positionStatus']!=1 or volume>position['tradeData']['volume']:raise SafetyError('CLOSE_VOLUME')
            position['tradeData']['volume']-=volume
            if not position['tradeData']['volume']:position['positionStatus']=2
            oid=self.next_id();did=self.next_id()
            deal={'dealId':did,'orderId':oid,'positionId':position['positionId'],'symbolId':self.symbol_id,
                'volume':volume,'filledVolume':volume,'executionPrice':float(self.market_price),
                'tradeSide':2 if position['tradeData']['tradeSide']==1 else 1,'dealStatus':2,
                'closePositionDetail':{'closedVolume':volume}}
            self.deals[did]=deal
            result=self.event(cid,3,position=position,deal=deal)
        else:raise SafetyError('OFFLINE_SCHEMA')
        if self.mode=='timeout_after':raise TimeoutError()
        return result

    def fill(self,oid,volume,price=None,cid=None):
        order=self.orders[oid];price=price or D(str(order.get('limitPrice',self.market_price)))
        if order['orderStatus']!=1 or volume<=0 or order['executedVolume']+volume>order['tradeData']['volume']:raise SafetyError('FILL_VOLUME')
        pid=order.get('positionId')
        if pid is None:
            pid=self.next_id();order['positionId']=pid
            position={'positionId':pid,'tradeData':dict(order['tradeData'],volume=0),'positionStatus':1,'price':float(price)}
            self.positions[pid]=position
        position=self.positions[pid];old=position['tradeData']['volume']
        position['price']=float((D(str(position['price']))*old+price*volume)/(old+volume))
        position['tradeData']['volume']+=volume
        buy=position['tradeData']['tradeSide']==1;entry=D(str(position['price']))
        for absolute,relative,sign in [('stopLoss','relativeStopLoss',-1 if buy else 1),('takeProfit','relativeTakeProfit',1 if buy else -1)]:
            if absolute in order:position[absolute]=order[absolute]
            elif relative in order:position[absolute]=float(entry+sign*D(order[relative])/100000)
        if self.protection_missing:position.pop('stopLoss',None)
        order['executedVolume']+=volume
        complete=order['executedVolume']==order['tradeData']['volume']
        if complete:order['orderStatus']=2
        did=self.next_id()
        deal={'dealId':did,'orderId':oid,'positionId':pid,'symbolId':self.symbol_id,'volume':volume,
            'filledVolume':volume,'executionPrice':float(price),'tradeSide':order['tradeData']['tradeSide'],'dealStatus':2}
        self.deals[did]=deal
        return self.event(cid or order['clientOrderId'],3 if complete else 11,order,position,deal)

    def snapshot(self):
        if not self.connected:raise ConnectionError()
        return copy.deepcopy({'ctidTraderAccountId':self.account_id,'orders':list(self.orders.values()),
            'positions':[p for p in self.positions.values() if p['positionStatus']==1],'deals':list(self.deals.values()),
            'history_complete':self.history_complete})


class OfflineAdapter:
    def __init__(self,peer):
        if type(peer) is not OfflinePeer:raise SafetyError('NETWORK_EXECUTION_NOT_IMPLEMENTED_OR_APPROVED')
        self.peer=peer

    def send(self,frame):return self.peer.send(frame)
    def snapshot(self):return self.peer.snapshot()


def production_execution(*args,**kwargs):
    # Intentionally no flag/env/config value can unlock a network mutation path.
    raise SafetyError('EXECUTION_APPROVAL_AND_BROKER_CAPABILITY_TESTS_REQUIRED')
