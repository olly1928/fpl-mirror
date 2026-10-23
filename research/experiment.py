"""Run declared chronological candidate fits, saving every trial and prediction."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
import time
import traceback
import warnings

import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning
from threadpoolctl import threadpool_limits

from research.estimators import candidates, minutes_candidates, augment, select_features, fit_candidate, predict_candidate, MinutesModel

ROOT=Path(__file__).resolve().parents[1]
warnings.filterwarnings('ignore',message='X does not have valid feature names')
# Convergence warnings are saved in each trial, not silenced in the ledger.


def dump(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')
    temp.replace(path)


def safe(value):
    if isinstance(value,dict):return {k:safe(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [safe(v) for v in value]
    if isinstance(value,(float,np.floating)):return float(value) if np.isfinite(value) else None
    if isinstance(value,np.integer):return int(value)
    return value


def point_metrics(df,pred):
    keys=['season','gw','player_id']
    a=df[keys+['y_total_points','active']].copy();a['prediction']=pred
    # A DGW is one forecast, with both fixture targets and predictions summed.
    g=a.groupby(keys,sort=False).agg(y=('y_total_points','sum'),p=('prediction','sum'),active=('active','max'))
    out={}
    for name,use in [('active',g.active==1),('all',np.ones(len(g),dtype=bool))]:
        y=g.loc[use,'y'].to_numpy();p=g.loc[use,'p'].to_numpy();e=p-y
        slope=float(np.cov(p,y,ddof=0)[0,1]/np.var(p)) if np.var(p)>1e-12 else None
        out[name]={'n':len(y),'rmse':float(np.sqrt(np.mean(e**2))),'mse':float(np.mean(e**2)),
                   'mae':float(np.mean(np.abs(e))),'bias_forecast_minus_actual':float(e.mean()),
                   'mean_prediction':float(p.mean()),'mean_actual':float(y.mean()),'calibration_slope':slope}
    return out


def minute_metrics(df,pred):
    y=df.y_minutes.to_numpy();state=np.where(y<=0,0,np.where(y<60,1,2))
    out={}
    for name,use in [('active',df.active.to_numpy()==1),('all',np.ones(len(df),dtype=bool))]:
        p=pred[use];z=y[use];s=state[use]
        out[name]={'n':len(z),'appearance_brier':float(np.mean((p[:,0]-(z>0))**2)),
                   'p60_brier':float(np.mean((p[:,1]-(z>=60))**2)),
                   'minutes_rmse':float(np.sqrt(np.mean((p[:,2]-z)**2))),
                   'log_loss':float(-np.log(np.maximum(1e-9,p[:,3:][np.arange(len(z)),s])).mean()),
                   'mean_appearance':float(p[:,0].mean()),'actual_appearance':float((z>0).mean()),
                   'mean_p60':float(p[:,1].mean()),'actual_p60':float((z>=60).mean())}
    return out


def fit_trial(candidate,df,year,output,identity,seed=1729):
    path=output/f'{year}/{candidate.name}-seed{seed}'
    ledger=path.with_suffix('.json'); predictions=path.with_suffix('.npz')
    if ledger.exists() and predictions.exists():
        old=json.loads(ledger.read_text())
        if old.get('identity')==identity and old.get('status')=='ok':return old
    training=df[(df.year<year)&(df.year>=year-candidate.train_years)]
    test=df[df.year==year]
    started=time.time()
    rec={'candidate':asdict(candidate),'year':year,'seed':seed,'identity':identity,
         'training_seasons':sorted(training.season.unique().tolist()),'training_rows':len(training),
         'test_rows':len(test),'latest_training_outcome':float(training.target_time.max()+10800),
         'earliest_test_cutoff':float(test.cutoff.min()),'status':'running'}
    if rec['latest_training_outcome']>=rec['earliest_test_cutoff']:
        raise AssertionError('Season boundary leaks outcomes')
    path.parent.mkdir(parents=True,exist_ok=True)
    try:
        with warnings.catch_warnings(record=True) as caught:
            if candidate.family=='minutes':
                cols=select_features(training,candidate.feature_set)
                model=MinutesModel(candidate.params,seed).fit(training,cols)
                minute=model.predict(test)
                np.savez_compressed(predictions,minutes=minute.astype(np.float32))
                rec['metrics']=minute_metrics(test,minute)
            else:
                model,cols=fit_candidate(candidate,training,seed)
                pred,minute=predict_candidate(candidate,model,cols,test)
                rec['metrics']=point_metrics(test,pred)
                fields={'points':pred.astype(np.float32)}
                if minute is not None:
                    fields['minutes']=minute.astype(np.float32)
                    rec['minute_metrics']=minute_metrics(test,minute)
                np.savez_compressed(predictions,**fields)
            rec['warnings']=list(dict.fromkeys(str(w.message) for w in caught))[:20]
        rec['feature_columns']=cols
        rec['status']='ok'
        rec['prediction_sha256']=hashlib.sha256(predictions.read_bytes()).hexdigest()
    except Exception as e:
        rec.update(status='failed',error=repr(e),traceback=traceback.format_exc())
    rec['runtime_seconds']=time.time()-started
    dump(ledger,safe(rec))
    print(json.dumps({'year':year,'candidate':candidate.name,'status':rec['status'],
                      'seconds':round(rec['runtime_seconds'],2),
                      'active_metric':rec.get('metrics',{}).get('active')}),flush=True)
    return rec


def main(args):
    # No estimator is allowed to silently claim all nine CPU cores.
    threadpool_limits(limits=1,user_api='blas')
    df=augment(pd.read_pickle(args.features/'historical.pkl'))
    if (args.features/'current.pkl').exists():
        df=pd.concat([df,augment(pd.read_pickle(args.features/'current.pkl'))],ignore_index=True)
    # Float32 saves memory; provenance and labels retain their precision.
    cols=[c for c in df if c.startswith('f_')]
    df[cols]=df[cols].astype(np.float32)
    digest=hashlib.sha256()
    for p in [ROOT/'research/protocol.json',ROOT/'research/estimators.py',Path(__file__),args.features/'feature-manifest.json']:
        digest.update(p.read_bytes())
    identity=digest.hexdigest()
    all_candidates=candidates()+minutes_candidates()
    dump(args.output/'experiment.json',{'identity':identity,'protocol':json.loads((ROOT/'research/protocol.json').read_text()),
         'candidates':[asdict(c) for c in all_candidates], 'python':platform.python_version(),
         'source_manifest':json.loads((args.features/'feature-manifest.json').read_text()),
         'started_at':time.time(),'selected_on_current_outcomes':False,'live_model_modified':False})
    if args.names:
        all_candidates=[c for c in all_candidates if c.name in args.names.split(',') or c.family in args.names.split(',')]
    results=[]
    for year in range(args.first_year,args.last_year+1):
        if year==2026:
            raise ValueError('Fit only historically selected finalists for current season with finalize.py')
        target=df[df.year==year].copy()
        path=args.output/str(year)/'targets.pkl'
        path.parent.mkdir(parents=True,exist_ok=True)
        keep=[c for c in target if not c.startswith('f_')]
        target[keep].to_pickle(path)
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            tasks=[executor.submit(fit_trial,c,df,year,args.output,identity,args.seed) for c in all_candidates]
            for f in as_completed(tasks):
                results.append(f.result())
        dump(args.output/'progress.json',{'finished_through_year':year,'trials':len(results),
                                          'failures':sum(r['status']!='ok' for r in results)})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--features',type=Path,default=ROOT/'research/work/features')
    p.add_argument('--output',type=Path,default=ROOT/'research/work/experiments')
    p.add_argument('--first-year',type=int,default=2016)
    p.add_argument('--last-year',type=int,default=2025)
    p.add_argument('--jobs',type=int,default=3)
    p.add_argument('--seed',type=int,default=1729)
    p.add_argument('--names')
    main(p.parse_args())
