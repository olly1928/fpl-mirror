"""Structural research checks: probability calibration and a v1/minutes hybrid."""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import joblib
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import softmax
from threadpoolctl import threadpool_limits

from research.estimators import augment,candidates,fit_candidate,predict_candidate
from research.experiment import dump,safe,minute_metrics,point_metrics
from research.finalize import load_module,scalar_scores,paired_blocks
from research.selection import select,prior_years

ROOT=Path(__file__).resolve().parents[1]


def conditional_means(minute):
    app,long,mean=minute[:,:3].T;short=app-long
    # Recover feasible conditional means while exactly retaining predicted E[min].
    lm=np.clip((mean-short*20)/np.maximum(long,1e-12),60,90)
    sm=np.clip((mean-long*lm)/np.maximum(short,1e-12),1,59)
    return sm,lm


def calibrate(minute,parameters):
    logits=np.log(np.maximum(minute[:,3:],1e-9))
    temperature=np.exp(parameters[0]);bias=np.array([parameters[1],parameters[2],0.])
    p=softmax(logits/temperature+bias,axis=1)
    sm,lm=conditional_means(minute)
    return np.column_stack([1-p[:,0],p[:,2],p[:,1]*sm+p[:,2]*lm,p])


def fit_probability_calibration(folder,year,name):
    arrays=[];targets=[];weights=[];years=prior_years(year)
    for past in years:
        df=pd.read_pickle(folder/str(past)/'targets.pkl');use=df.active.to_numpy()==1
        m=np.load(folder/str(past)/f'{name}-seed1729.npz')['minutes'][use]
        y=df.loc[use,'y_minutes'].to_numpy();state=np.where(y<=0,0,np.where(y<60,1,2))
        arrays.append(m);targets.append(state);weights.extend([1/(len(years)*len(m))]*len(m))
    minute=np.vstack(arrays);target=np.concatenate(targets);w=np.array(weights)
    def loss(params):
        p=calibrate(minute,params)[:,3:]
        nll=-np.sum(w*np.log(np.maximum(1e-9,p[np.arange(len(p)),target])))
        return float(nll+.01*np.sum(np.array(params)**2))
    opt=minimize(loss,[0.,0.,0.],method='L-BFGS-B',bounds=[(np.log(.5),np.log(2)),(-.5,.5),(-.5,.5)])
    if not opt.success:raise RuntimeError(opt.message)
    return {'parameters':opt.x.tolist(),'training_years':years,'rows':len(target),'OOF_only':True}


def probability_research(args):
    selections=json.loads((args.output/'nested-selection.json').read_text())
    records=[];calibrators={};prediction={}
    for year in range(2018,2026):
        chosen=selections[str(year)];name=chosen['minutes']
        target=pd.read_pickle(args.experiments/str(year)/'targets.pkl')
        raw=np.load(args.experiments/str(year)/f'{name}-seed1729.npz')['minutes'].astype(float)
        model=fit_probability_calibration(args.experiments,year,name);calibrators[str(year)]=model
        corrected=calibrate(raw,model['parameters'])
        prediction[year]={'raw':raw,'calibrated':corrected}
        for label,p in [('raw',raw),('calibrated',corrected)]:
            records.append({'year':year,'variant':label,**minute_metrics(target,p)})
    chosen=selections['2026'];name=chosen['minutes']
    calibration=fit_probability_calibration(args.experiments,2026,name);calibrators['2026']=calibration
    native=joblib.load(args.artifacts/'minutes.joblib')
    current=augment(pd.read_pickle(args.features/'current.pkl'))
    raw=native.predict(current);corrected=calibrate(raw,calibration['parameters'])
    current_prediction={}
    for label,m in [('raw',raw),('calibrated',corrected)]:
        m=m.copy();m[:,:3]*=current.availability.to_numpy()[:,None]
        m[:,3]=1-m[:,0];m[:,4]=m[:,0]-m[:,1];m[:,5]=m[:,1]
        current_prediction[label]=m
        baseline=pd.read_csv(args.baselines/'current-v1.csv')
        index=pd.MultiIndex.from_frame(current[['gw','player_id']])
        baseline=baseline.set_index(['gw','player_id']).reindex(index)
        for pool,use in [('fixed_candidates',baseline.likely_player.to_numpy()==1),('owned15',current.owned.to_numpy()==1)]:
            records.append({'year':2026,'variant':label,'pool':pool,
                            **minute_metrics(current.loc[use].assign(active=1),m[use])})
    dump(args.output/'probability-calibration.json',safe({'calibrators':calibrators,'scores':records}))
    return prediction,current_prediction


def historical_hybrid(args,prediction):
    old=load_module('hybrid_original',ROOT/'backtests/historical_predictive.py')
    model=old.load_model(ROOT/'backtests/baseline-v1/fpl_advisor.py')
    config=json.loads((ROOT/'backtests/baseline-v1/model/config.json').read_text())
    panels=defaultdict(list)
    with gzip.open(ROOT/'history/extended/data/fpl-complete-seasons.csv.gz','rt') as f:
        for r in csv.DictReader(f):panels[r['season']].append(r)
    original=model.minute_states;rows=[]
    for year in (2023,2024,2025):
        season=f'{year}-{str(year+1)[-2:]}'
        target=pd.read_pickle(args.experiments/str(year)/'targets.pkl')
        indexes={(int(r.gw),int(r.player_id)):i for i,r in enumerate(target.itertuples()) if not r.dgw}
        cases,_=old.cases_for_season(season,panels,args.source/'archive',model,config)
        for case in cases:
            if not case['eligible']:continue
            ix=indexes[(int(case['gw']),int(case['player_id']))]
            shared={k:case[k] for k in ['season','gw','player_id','pos','name','actual_points','actual_minutes']}
            shared['player_id']=int(shared['player_id'])
            for label in ('raw','calibrated'):
                minute=prediction[year][label][ix:ix+1];sm,lm=conditional_means(minute)
                app,long=minute[0,:2]
                def learned(*unused):
                    return [(long,float(lm[0])),(app-long,float(sm[0])),(0.,15.)],{'minutes_60':float(lm[0])}
                model.minute_states=learned
                try:p=old.model_prediction(case,config,model)
                finally:model.minute_states=original
                rows.append({**shared,'variant':label,**p})
    result=pd.DataFrame(rows)
    result.to_csv(args.output/'v1-new-minutes-historical.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    scores=[]
    for (season,label),a in result.groupby(['season','variant']):
        scores.append({'season':season,'variant':label,**scalar_scores(a.actual_points.to_numpy(),a.xp.to_numpy())})
    dump(args.output/'v1-new-minutes-historical.json',safe(scores))


def current_hybrid(args,probability):
    replay=load_module('hybrid_replay',ROOT/'backtests/replay_current.py')
    source=augment(pd.read_pickle(args.features/'current.pkl'))
    results={};original_loader=replay.load_model
    for label,minute in probability.items():
        values={(int(r.gw),int(r.player_id)):minute[i] for i,r in enumerate(source.itertuples())}
        native=replay.load_model(ROOT/'backtests/baseline-v1/fpl_advisor.py')
        original_forecast=native.forecast;active={}
        def minute_states(player,rows,prior,config,override):
            if override:raise ValueError('Research hybrid has not implemented manual news overrides')
            m=values[active['gw'],int(player['id'])][None,:]
            sm,lm=conditional_means(m);app,long=m[0,:2]
            return [(long,float(lm[0])),(app-long,float(sm[0])),(0.,15.)],{
                'method':'chronologically_learned_three_state_minutes','p60':long,'p_short':app-long,
                'minutes_60':float(lm[0]),'minutes_short':float(sm[0])}
        native.minute_states=minute_states
        def forecast(data,*a,**kw):
            active['gw']=json.loads((data/'meta.json').read_text())['next_event']
            r=original_forecast(data,*a,**kw);r['research_only']=True;return r
        native.forecast=forecast;replay.load_model=lambda unused:native
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t)
            config=json.loads((ROOT/'backtests/baseline-v1/model/config.json').read_text())
            config.update(research_variant='v1_new_minutes_'+label,
                          research_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                          calibration_sha256=hashlib.sha256((args.output/'probability-calibration.json').read_bytes()).hexdigest(),
                          feature_manifest_sha256=hashlib.sha256((args.features/'feature-manifest.json').read_bytes()).hexdigest(),
                          minute_artifact_sha256=hashlib.sha256((args.artifacts/'minutes.joblib').read_bytes()).hexdigest())
            dump(folder/'config.json',config)
            shutil.copy2(ROOT/'backtests/baseline-v1/model'/config['prior_file'],folder/config['prior_file'])
            output=args.work/('v1_new_minutes_'+label)
            try:
                result=replay.run(args.current,json.loads((ROOT/'backtests/sources/current-core-refs.json').read_text()),
                    args.actuals,ROOT/'backtests/fixtures',ROOT/'backtests/baseline-v1/fpl_advisor.py',folder,output,
                    args.baselines/'current-v1.csv')
            finally:replay.load_model=original_loader
        results[label]=result
        shutil.copy2(output/'player_predictions.csv',args.output/f'v1-new-minutes-current-{label}.csv')
    dump(args.output/'v1-new-minutes-current.json',safe(results))


def poisson_blocks(mean,size):
    mean=np.asarray(mean,dtype=float)
    if size==2:return mean/2-(1-np.exp(-2*mean))/4
    if size==3:
        a=np.sqrt(3)*mean/2
        remainder=1+(2/3)*np.exp(-1.5*mean)*(np.cos(a-2*np.pi/3)+2*np.cos(a-4*np.pi/3))
        return np.maximum(0,(mean-remainder)/3)
    raise ValueError(size)


def exact_component_prediction(model,df):
    score,minute=model.predict(df)
    x=df[model.cols].to_numpy(dtype=np.float32)
    short=np.clip(model.minutes.models[1].predict(x),1,59)
    long=np.clip(model.minutes.models[2].predict(x),60,90)
    ps=minute[:,4];pl=minute[:,5];expected=minute[:,2]
    saves=model.models['saves']
    rate=saves.predict(x) if hasattr(saves,'predict') else np.zeros(len(df))
    keeper=df.f_pos_GKP.to_numpy();defender=df.f_pos_DEF.to_numpy()
    old_saves=keeper*rate*expected/270
    new_saves=keeper*(ps*poisson_blocks(rate*short/90,3)+pl*poisson_blocks(rate*long/90,3))
    conceded=df.f_opp_goals_proxy.to_numpy()
    old_conceded=(keeper+defender)*conceded*expected/180
    new_conceded=(keeper+defender)*(ps*poisson_blocks(conceded*short/90,2)+pl*poisson_blocks(conceded*long/90,2))
    return score+(new_saves-old_saves)+(old_conceded-new_conceded),minute


def component_research(args):
    historical=augment(pd.read_pickle(args.features/'historical.pkl'))
    current=augment(pd.read_pickle(args.features/'current.pkl'))
    choices=[c for c in candidates() if c.family=='poisson_components']
    records=[];frames=[]
    for year in (2023,2024,2025,2026):
        train=historical[historical.year<year]
        target=current if year==2026 else historical[historical.year==year]
        frame=target[[c for c in target if not c.startswith('f_')]].copy()
        for candidate in choices:
            path=args.work/f'exact-components-{year}-{candidate.name}.npz'
            if path.exists():
                pred=np.load(path)['points']
            else:
                model,cols=fit_candidate(candidate,train)
                pred,_=exact_component_prediction(model,target)
                np.savez_compressed(path,points=pred.astype(np.float32))
            if year==2026:pred=pred*target.availability.to_numpy()
            frame[candidate.name]=pred
            records.append({'year':year,'model':candidate.name,**point_metrics(target,pred)})
            print('exact components',year,candidate.name,flush=True)
        frames.append(frame)
    result=pd.concat(frames,ignore_index=True)
    result.to_csv(args.output/'exact-component-predictions.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    dump(args.output/'exact-component-scores.json',safe(records))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--features',type=Path,default=ROOT/'research/work/canonical-features')
    p.add_argument('--experiments',type=Path,default=ROOT/'research/work/remote/experiments')
    p.add_argument('--source',type=Path,default=ROOT/'research/work/source')
    p.add_argument('--baselines',type=Path,default=ROOT/'research/work/baselines')
    p.add_argument('--artifacts',type=Path,default=ROOT/'research/artifacts')
    p.add_argument('--output',type=Path,default=ROOT/'research/results')
    p.add_argument('--work',type=Path,default=ROOT/'research/work/refinements')
    p.add_argument('--current',type=Path,default=ROOT/'research/work/replay-inputs/raw/current-core')
    p.add_argument('--actuals',type=Path,default=ROOT/'research/work/replay-inputs/outcomes/player_history.csv')
    p.add_argument('--components-only',action='store_true')
    p.add_argument('--skip-components',action='store_true')
    a=p.parse_args();a.work.mkdir(parents=True,exist_ok=True);a.output.mkdir(parents=True,exist_ok=True)
    threadpool_limits(limits=1,user_api='blas')
    if not a.skip_components:component_research(a)
    if not a.components_only:
        past,current=probability_research(a)
        historical_hybrid(a,past)
        current_hybrid(a,current)
