"""Independent per-event forward search. No production entry module or engine import."""
from decimal import Decimal as D
from reference_alt import require,close_feature
from reference_bb import reference_extra, COST
from verify_mean import check as check_decisions, audit


def reference(block,spec,cost_name,base):
    require(spec in [dict(id=n,low_vol=v,micro=m) for n,v,m in
                    [('BASE',False,False),('VOL',True,False),('MICRO',False,True),('VOL_MICRO',True,True)]],
            'Unknown reference definition')
    bs=block['candles']; fee,half,slip=COST[cost_name]
    result={i:dict(r,direction=0) for i,r in base.items()}; reports=[]; busy_until=-1
    # Scan each already-closed setup into its next 5 completed minutes separately.
    # This algorithm differs from the production pending-state minute loop.
    for i,r in base.items():
        if r['direction']!=-1:continue
        require(i%15==0,'Non15m event')
        raw=r['atr']/(r['close']/D('1.5'))
        report=dict(setup_index=i,setup_end_ms=bs[i-1]['T'],raw_stop_fraction=str(raw),status='DIRECT',end_index=i)
        reports.append(report)
        if spec['low_vol'] and r['atr']*D('1.5') < r['close']*D('.003'):
            report['status']='REJECT_LOW_VOL';continue
        if not spec['micro']:
            result[i]=dict(r);continue
        if i<busy_until:
            report['status']='IGNORED_OVERLAP';continue
        frozen=reference_extra(block,i)
        end=min(i+5,len(bs)-1); found=False
        for target in range(i+1,end+1):
            b=bs[target-1]
            if D(b['c'])>frozen['high']:
                report.update(status='CANCELLED_HIGH',end_index=target);found=True;break
            confirm=D(b['o'])>D(b['c']) and D(bs[target-2]['l'])>D(b['c'])
            if confirm:
                entry=D(bs[target]['o'])*(1-half)*(1-slip)
                ratio=1-frozen['mean']/entry; minimum=6*(fee+half+slip)
                trace=dict(r['trace'],setup_index=str(i),setup_end_ms=str(bs[i-1]['T']),
                    confirmation_end_ms=str(b['T']),waiting_bars=str(target-i),
                    frozen_signal_high=str(frozen['high']),frozen_mean20=str(frozen['mean']),
                    frozen_setup_close=str(r['close']))
                result[target]=dict(base[target],direction=-1 if ratio>=minimum else 0,
                                    atr=r['atr'],close=D(b['c']),trace=trace)
                report.update(status='CONFIRMED' if ratio>=minimum else 'REJECT_NEW_ROOM',
                    end_index=target,confirmation_end_ms=b['T'],entry_fill=str(entry),
                    room_fraction=str(ratio),required_room=str(minimum))
                found=True;break
        if not found:
            report.update(status='EXPIRED' if i+5 < len(bs) else 'END_OF_BLOCK_UNCONFIRMED',
                          end_index=i+5 if i+5 < len(bs) else len(bs))
        busy_until=report['end_index']
    return result,reports


def check(actual,expected,events,reports):
    check_decisions(actual,expected)
    require(len(events)==len(reports),'Entry event count')
    numeric={'raw_stop_fraction','entry_fill','room_fraction','required_room'}
    for a,b in zip(events,reports):
        require(a.keys()==b.keys(),'Event fields')
        for k in a:
            if k in numeric:close_feature(a[k],b[k],'Entry event '+k)
            else:require(a[k]==b[k],'Entry state '+k)
