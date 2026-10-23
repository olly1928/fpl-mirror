"""Secondary six-GW labels with frozen origin features and purged year splits."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from research.estimators import Candidate,augment,fit_candidate,predict_candidate
from research.experiment import dump,safe
from research.finalize import scalar_scores,paired_blocks

ROOT=Path(__file__).resolve().parents[1]


def build_labels(df):
    output=[];audit=[]
    for season,a in df.groupby('season',sort=True):
        # GW targets retain DGW sums; first fixture supplies only origin features.
        a=a.sort_values(['gw','player_id','fixture_id'])
        first=a.drop_duplicates(['gw','player_id']).set_index(['gw','player_id'])
        threshold=np.where(a.pos.to_numpy()=='DEF',10,12)
        a=a.copy()
        a['dc_award']=2*((np.nan_to_num(a.y_defensive_contribution)>=threshold)&(a.pos!='GKP')&(a.year>=2025))
        points=a.groupby(['gw','player_id']).y_total_points.sum().to_dict()
        minutes=a.groupby(['gw','player_id']).y_minutes.sum().to_dict()
        dc=a.groupby(['gw','player_id']).dc_award.sum().to_dict()
        # Team calendar is reconstructed independently of any one player's DNPs.
        calendar=a.groupby(['team','gw']).fixture_id.nunique().to_dict()
        maxgw=int(a.gw.max());excluded=0;admitted=0
        for (gw,pid),r in first.iterrows():
            if gw+5>maxgw:continue
            score=mins=awards=0.;fixtures=0;known=True
            for future in range(gw,gw+6):
                key=(future,pid);count=calendar.get((r['team'],future),0)
                fixtures+=count
                if key in points:
                    score+=points[key];mins+=minutes[key];awards+=dc[key]
                elif count:
                    known=False;break
            if not known:
                excluded+=1;continue
            record=r.to_dict();record.update(gw=int(gw),player_id=int(pid),y_total_points=score,y_minutes=mins,
                 y_dc_award=awards,window_end_gw=int(gw+5),future_fixture_count=fixtures)
            # Expected rule adjustment scales with the future scheduled exposure.
            record['f_base_dc']*=fixtures
            record['f_six_fixture_count']=float(fixtures)
            record['f_six_rolling']=record.get('f_points_12',2.)*fixtures
            output.append(record);admitted+=1
        audit.append({'season':season,'admitted':admitted,'unknown_future_coverage_excluded':excluded,
                      'last_origin_gw':maxgw-5})
    return pd.DataFrame(output),audit


def choices():
    result=[Candidate('six_ridge_01','ridge',{'alpha':10}),Candidate('six_ridge_02','ridge',{'alpha':1000})]
    for i,(leaves,years) in enumerate([(7,99),(15,99),(7,3),(15,3)],1):
        result.append(Candidate(f'six_lgbm_{i:02d}','lightgbm',{'num_leaves':leaves,'min_child_samples':200,
            'n_estimators':400,'learning_rate':.035,'reg_lambda':50},train_years=years))
    for i,depth in enumerate([4,6],1):
        result.append(Candidate(f'six_catboost_{i:02d}','catboost',{'depth':depth,'iterations':450,'l2_leaf_reg':50,'learning_rate':.04}))
    return result


def fit_six(c,df,year,folder,identity):
    path=folder/str(year)/c.name;path.parent.mkdir(parents=True,exist_ok=True)
    if path.with_suffix('.json').exists():
        old=json.loads(path.with_suffix('.json').read_text())
        if old.get('identity')==identity and old.get('status')=='ok':return old
    train=df[(df.year<year)&(df.year>=year-c.train_years)].copy()
    test=df[df.year==year]
    if not len(test):return None
    train.y_total_points-=train.y_dc_award
    # The generic learner removes DC itself for one-fixture labels; six-week
    # actual awards were already removed exactly above.
    train.y_defensive_contribution=np.nan
    t=time.time();model,cols=fit_candidate(c,train)
    p,_=predict_candidate(c,model,cols,test)
    rec={'identity':identity,'candidate':asdict(c),'year':year,'training_rows':len(train),'test_rows':len(test),
         'max_training_season':int(train.year.max()),'max_training_window_end_gw':int(train.window_end_gw.max()),
         'status':'ok','seconds':time.time()-t,'scores':{}}
    for name,use in [('active',test.active==1),('all',np.ones(len(test),dtype=bool))]:
        rec['scores'][name]=scalar_scores(test.loc[use,'y_total_points'].to_numpy(),p[np.asarray(use)])
    np.savez_compressed(path.with_suffix('.npz'),points=p.astype(np.float32))
    dump(path.with_suffix('.json'),safe(rec))
    print(json.dumps({'six_year':year,'model':c.name,'seconds':round(rec['seconds'],2)}),flush=True)
    return rec


def main(args):
    threadpool_limits(limits=1,user_api='blas')
    historical=augment(pd.read_pickle(args.features/'historical.pkl'))
    current=augment(pd.read_pickle(args.features/'current.pkl'))
    all_data=pd.concat([historical,current],ignore_index=True)
    args.work.mkdir(parents=True,exist_ok=True)
    if args.reuse_labels and (args.work/'labels.pkl').exists():
        df=pd.read_pickle(args.work/'labels.pkl')
        audit=json.loads((args.work/'coverage.json').read_text())
    else:
        df,audit=build_labels(all_data)
        df.to_pickle(args.work/'labels.pkl')
        dump(args.work/'coverage.json',audit)
    identity=hashlib.sha256(Path(__file__).read_bytes()+(ROOT/'research/protocol-six.json').read_bytes()+(args.features/'feature-manifest.json').read_bytes()).hexdigest()
    dump(args.work/'experiment.json',{'identity':identity,'candidates':[asdict(c) for c in choices()],'coverage':audit})
    for year in range(args.first_year,args.last_year+1):
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for f in as_completed([pool.submit(fit_six,c,df,year,args.work,identity) for c in choices()]):
                f.result()
    if args.fit_only:return
    output=[];scores=[];selection={}
    for year in range(2018,2027):
        past=list(range(max(2016,year-3),year));ranks=[]
        for c in choices():
            records=[json.loads((args.work/str(y)/(c.name+'.json')).read_text()) for y in past]
            mse=np.mean([r['scores']['active']['rmse']**2 for r in records])
            ranks.append((float(mse),c.name))
        ranks.sort();name=ranks[0][1]
        selection[str(year)]={'selected':name,'selection_years':past,'ranking':ranks}
        test=df[df.year==year].copy()
        test['selected_six']=np.load(args.work/str(year)/(name+'.npz'))['points']
        test['rolling_six']=test.f_six_rolling
        for model in ['rolling_six','selected_six']:
            a=test[test.active==1]
            scores.append({'year':year,'model':model,**scalar_scores(a.y_total_points.to_numpy(),a[model].to_numpy())})
        output.append(test[[c for c in test if not c.startswith('f_')]])
    result=pd.concat(output,ignore_index=True)
    historical_test=result[(result.year<=2025)&(result.active==1)]
    uncertainty=paired_blocks(historical_test,'rolling_six','selected_six',block_length=8)
    args.output.mkdir(parents=True,exist_ok=True)
    result.to_csv(args.output/'six-week-predictions.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    dump(args.output/'six-week-assessment.json',safe({'coverage':audit,'scores':scores,'selection':selection,
           'uncertainty':uncertainty,'protocol':'research/protocol-six.json',
           'caveat':'Six-GW labels overlap, realised schedule counts are used, and windows with unknown future registration are excluded. Current has two overlapping windows only.'}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--features',type=Path,default=ROOT/'research/work/features')
    p.add_argument('--work',type=Path,default=ROOT/'research/work/six-week')
    p.add_argument('--output',type=Path,default=ROOT/'research/results')
    p.add_argument('--jobs',type=int,default=2)
    p.add_argument('--first-year',type=int,default=2016)
    p.add_argument('--last-year',type=int,default=2026)
    p.add_argument('--fit-only',action='store_true')
    p.add_argument('--reuse-labels',action='store_true')
    main(p.parse_args())
