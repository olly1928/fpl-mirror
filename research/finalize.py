"""Fit historically selected finalists and assess retrospective/current decisions."""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import time

import joblib
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from threadpoolctl import threadpool_limits

from research.estimators import candidates,minutes_candidates,augment,fit_candidate,predict_candidate,MinutesModel,select_features
from research.experiment import point_metrics,minute_metrics,dump,safe
from research.selection import evaluate,select

ROOT=Path(__file__).resolve().parents[1]
POINT_MODELS=['rolling12','empirical_h8_p900','selected_single','nested_ensemble','calibrated_ensemble','equal_top3']


def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj)
    return obj


def grouped(df,models):
    cols={'y_total_points':'sum','y_minutes':'sum','active':'max','cold':'max','dgw':'max',
          'pos':'first','name':'first','code':'first',**{m:'sum' for m in models}}
    return df.groupby(['season','gw','player_id'],sort=False).agg(cols).reset_index()


def scalar_scores(y,p):
    e=p-y;variance=float(np.var(p))
    slope=float(np.cov(p,y,ddof=0)[0,1]/variance) if variance>1e-12 else None
    return {'n':len(y),'rmse':float(np.sqrt(np.mean(e**2))),'mae':float(np.mean(np.abs(e))),
            'bias_forecast_minus_actual':float(e.mean()),'mean_prediction':float(p.mean()),
            'mean_actual':float(y.mean()),'calibration_slope':slope,
            'calibration_intercept':float(y.mean()-slope*p.mean()) if slope is not None else None}


def paired_blocks(df,base,candidate,draws=2000,seed=1729,block_length=4):
    """Four-consecutive-GW circular blocks, stratified by historical season."""
    rng=np.random.default_rng(seed)
    by_season=[]
    for season,g in df.groupby('season',sort=True):
        g=g.copy()
        g['base_sq']=(g[base]-g.y_total_points)**2
        g['model_sq']=(g[candidate]-g.y_total_points)**2
        g['count']=1
        a=g.groupby('gw',sort=True)[['base_sq','model_sq','count']].sum().to_numpy()
        n=len(a);length=min(block_length,n)
        starts=rng.integers(0,n,size=(draws,int(np.ceil(n/length))))
        ix=((starts[:,:,None]+np.arange(length))%n).reshape(draws,-1)[:,:n]
        by_season.append(a[ix].sum(axis=1))
    samples=np.sum(by_season,axis=0)
    gain=(np.sqrt(samples[:,0]/samples[:,2])-np.sqrt(samples[:,1]/samples[:,2]))/np.sqrt(samples[:,0]/samples[:,2])*100
    base_rmse=np.sqrt(np.mean((df[base]-df.y_total_points)**2))
    model_rmse=np.sqrt(np.mean((df[candidate]-df.y_total_points)**2))
    return {'rows':len(df),'seasons':int(df.season.nunique()),'gameweeks':int(df.groupby(['season','gw']).ngroups),
            'rmse_improvement_percent':float((base_rmse-model_rmse)/base_rmse*100),
            'interval_95_percent':np.quantile(gain,[.025,.975]).tolist(),
            'draws':draws,'block_length':block_length,'caveat':'Conditional on the retrospective protocol and available archive population; not a guarantee against future regime or selection effects.'}


def calibration_bins(df,models,pool):
    out=[]
    for model in models:
        g=df.copy();g['bin']=pd.cut(g[model],[-np.inf,0,1,2,3,4,5,6,8,np.inf],right=False).astype(str)
        for band,a in g.groupby('bin',sort=False):
            out.append({'pool':pool,'model':model,'bin':band,'n':len(a),
                        'mean_prediction':float(a[model].mean()),'mean_actual':float(a.y_total_points.mean())})
    return out


def ranking(df,models):
    out=[]
    for (season,gw),a in df[df.active==1].groupby(['season','gw'],sort=True):
        for model in models:
            best=a.sort_values([model,'player_id'],ascending=[False,True]).iloc[0]
            rho=spearmanr(a[model],a.y_total_points).statistic
            top=a.nlargest(min(10,len(a)),model)
            out.append({'season':season,'gw':int(gw),'model':model,'players':len(a),
                        'spearman':float(rho),'captain':best['name'],'captain_id':int(best.player_id),
                        'captain_actual':float(best.y_total_points),'top10_actual_mean':float(top.y_total_points.mean())})
    return pd.DataFrame(out)


def historical_assessment(df,output,baselines):
    g=grouped(df,POINT_MODELS)
    slices=[]
    cohorts={'active':g.active==1,'all':np.ones(len(g),dtype=bool),'cold':g.cold==1,
             'double_gameweek':g.dgw==1,**{p:g.pos==p for p in ['GKP','DEF','MID','FWD']}}
    for cohort,use in cohorts.items():
        a=g[use]
        for model in POINT_MODELS:
            slices.append({'pool':cohort,'model':model,**scalar_scores(a.y_total_points.to_numpy(),a[model].to_numpy())})
    # Exact common cases with the old component test; no changing its candidate pool.
    old=pd.read_csv(baselines/'historical.csv')
    old=old.pivot(index=['season','gw','player_id'],columns='model',values='xp').reset_index()
    common=g.merge(old,on=['season','gw','player_id'],how='inner',validate='one_to_one')
    comparisons=[]
    for (season,a) in list(common.groupby('season'))+[('all_modern',common)]:
        for model in POINT_MODELS+['v1','learned_minutes','learned_plus_points_calibration','rolling10']:
            comparisons.append({'season':season,'model':model,**scalar_scores(a.y_total_points.to_numpy(),a[model].to_numpy())})
    uncertainty=[]
    active=g[g.active==1]
    for model in POINT_MODELS[1:]:
        uncertainty.append({'pool':'active','baseline':'rolling12','model':model,**paired_blocks(active,'rolling12',model)})
    for model in ['selected_single','nested_ensemble','calibrated_ensemble']:
        uncertainty.append({'pool':'v1_matched_modern','baseline':'v1','model':model,**paired_blocks(common,'v1',model)})
    # A data-driven v1/ML blend is selected using earlier modern seasons only.
    blends=[]
    for season,a in common.groupby('season',sort=True):
        past=common[common.season<season]
        weight=.5
        if len(past):
            difference=past.nested_ensemble-past.v1
            weight=float(np.clip(np.sum(difference*(past.y_total_points-past.v1))/(np.sum(difference**2)+10),0,1))
        xp=(1-weight)*a.v1+weight*a.nested_ensemble
        blends.append({'season':season,'ml_weight':weight,'selection_rows':len(past),
                       **scalar_scores(a.y_total_points.to_numpy(),xp.to_numpy())})
    difference=common.nested_ensemble-common.v1
    current_weight=float(np.clip(np.sum(difference*(common.y_total_points-common.v1))/(np.sum(difference**2)+10),0,1))
    dump(output/'historical-assessment.json',safe({'slices':slices,'matched_v1':comparisons,'uncertainty':uncertainty,
          'v1_ml_blends':blends,'v1_ml_current_weight':current_weight,'matched_case_count':len(common)}))
    rank=ranking(g,POINT_MODELS);rank.to_csv(output/'ranking.csv',index=False)
    pd.DataFrame(calibration_bins(active,POINT_MODELS,'historical_active')).to_csv(output/'points-calibration-bins.csv',index=False)
    g.to_csv(output/'historical-selected-predictions.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    common.to_csv(output/'modern-matched-predictions.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    return current_weight


def fit_current(args,chosen,blend_weight):
    historical=augment(pd.read_pickle(args.features/'historical.pkl'))
    test=augment(pd.read_pickle(args.features/'current.pkl'))
    choices={c.name:c for c in candidates()}
    names=list(dict.fromkeys(chosen['ensemble_names']+[chosen['single'],'rolling_03','empirical_05']))
    fitted={};predictions={};artifacts=[]
    args.artifacts.mkdir(parents=True,exist_ok=True)
    for name in names:
        c=choices[name];train=historical[historical.year>=2026-c.train_years]
        t=time.time();model,cols=fit_candidate(c,train)
        pred,minute=predict_candidate(c,model,cols,test)
        fitted[name]=(c,model,cols)
        predictions[name]=pred*test.availability.to_numpy()
        if model is not None:
            path=args.artifacts/f'{name}.joblib'
            joblib.dump({'candidate':asdict(c),'columns':cols,'model':model},path,compress=3)
            artifacts.append({'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size})
        print(json.dumps({'current_finalist_fit':name,'rows':len(train),'seconds':round(time.time()-t,2)}),flush=True)
    mc=next(c for c in minutes_candidates() if c.name==chosen['minutes'])
    minute_model=MinutesModel(mc.params).fit(historical,select_features(historical,'full'))
    minute=minute_model.predict(test)
    availability=test.availability.to_numpy()
    minute[:,:3]*=availability[:,None]
    minute[:,3]=1-minute[:,0];minute[:,4]=minute[:,0]-minute[:,1];minute[:,5]=minute[:,1]
    path=args.artifacts/'minutes.joblib'
    joblib.dump(minute_model,path,compress=3)
    artifacts.append({'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size})
    ensemble=sum(predictions[n]*w for n,w in zip(chosen['ensemble_names'],chosen['ensemble_weights']))
    test['rolling12']=predictions['rolling_03']
    test['empirical_h8_p900']=predictions['empirical_05']
    test['selected_single']=predictions[chosen['single']]
    test['nested_ensemble']=ensemble
    # Availability zero also gates an intercept in a calibrated projection.
    test['calibrated_ensemble']=chosen['calibration']['intercept']*availability+chosen['calibration']['slope']*ensemble
    test['equal_top3']=sum(predictions[n] for n in chosen['ensemble_names'][:3])/3
    for n,p in predictions.items():test['candidate_'+n]=p
    for j,k in enumerate(['p_appear','p60','expected_minutes','p0','p_short','p_long']):test['selected_'+k]=minute[:,j]
    old=pd.read_csv(args.baselines/'current-v1.csv')
    old=old.rename(columns={'xp':'v1','p_appear':'v1_p_appear','p60':'v1_p60','expected_minutes':'v1_minutes'})
    keep=['gw','player_id','v1','v1_p_appear','v1_p60','v1_minutes','likely_player','actual_points','actual_minutes']
    test=test.merge(old[keep],on=['gw','player_id'],how='inner',validate='one_to_one')
    if not np.array_equal(test.y_total_points.to_numpy(),test.actual_points.to_numpy()):
        raise AssertionError('Current baseline outcomes do not agree')
    v2=pd.read_csv(args.baselines/'current-v2.csv').rename(columns={'xp':'learned_minutes'})
    test=test.merge(v2[['gw','player_id','learned_minutes']],on=['gw','player_id'],how='inner',validate='one_to_one')
    test['v1_ml_blend']=(1-blend_weight)*test.v1+blend_weight*test.nested_ensemble
    models=POINT_MODELS+['v1','learned_minutes','v1_ml_blend']
    metrics=[];bins=[];minute_scores={}
    for pool,use in [('v1_fixed_candidates',test.likely_player==1),('owned15',test.owned==1),
                     ('all',np.ones(len(test),dtype=bool)),('thin_prior',test.f_prior_minutes<450)]:
        a=test[use]
        for model in models:
            metrics.append({'pool':pool,'model':model,**scalar_scores(a.y_total_points.to_numpy(),a[model].to_numpy())})
        bins+=calibration_bins(a,models,pool)
        p=a[['selected_p_appear','selected_p60','selected_expected_minutes','selected_p0','selected_p_short','selected_p_long']].to_numpy()
        # The pool is already chosen: mark every row active for these scoped metrics.
        minute_scores[pool]=minute_metrics(a.assign(active=1),p)['active']
        baseapp=a.v1_p_appear.to_numpy();baselong=a.v1_p60.to_numpy();basem=a.v1_minutes.to_numpy()
        baseline=np.column_stack([baseapp,baselong,basem,1-baseapp,baseapp-baselong,baselong])
        minute_scores[pool+'_v1']=minute_metrics(a.assign(active=1),baseline)['active']
    uncertainty=[]
    for pool,use in [('v1_fixed_candidates',test.likely_player==1),('owned15',test.owned==1)]:
        for model in ['selected_single','nested_ensemble','v1_ml_blend']:
            uncertainty.append({'pool':pool,'model':model,'baseline':'v1',**paired_blocks(test[use],'v1',model)})
    test[[c for c in test if not c.startswith('f_')]].to_csv(args.output/'current-selected-predictions.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    pd.DataFrame(bins).to_csv(args.output/'current-calibration-bins.csv',index=False)
    dump(args.output/'current-assessment.json',safe({'metrics':metrics,'minutes':minute_scores,'uncertainty':uncertainty,
          'v1_ml_weight':blend_weight,'known_outcomes_used_for_fitting':False}))
    dump(args.artifacts/'manifest.json',safe({'research_only':True,'live_model_changed':False,'selection':chosen,
         'feature_manifest_sha256':hashlib.sha256((args.features/'feature-manifest.json').read_bytes()).hexdigest(),
         'artifacts':artifacts,'trusted_artifact_only':'joblib artifacts are executable serialisations; load only these hash-verified files from this repository.',
         'training_seasons':sorted(historical.season.unique().tolist()),'current_prior_vintage_caveat':True,
         'production_override_interface_implemented':False}))
    lineup_assessment(test,models,args)
    return test,fitted,minute_model


def lineup_assessment(test,models,args):
    advisor=load_module('lineup_advisor',ROOT/'backtests/baseline-v1/fpl_advisor.py')
    replay=load_module('lineup_replay',ROOT/'backtests/replay_current.py')
    forecasts=json.loads((args.baselines/'current-v1-forecasts.json').read_text())
    outcomes={(int(r.gw),int(r.player_id)):{'minutes':float(r.y_minutes),'points':float(r.y_total_points)} for r in test.itertuples()}
    weeks=[]
    for model in models:
        for gw,a in test.groupby('gw',sort=True):
            forecast=copy.deepcopy(forecasts[str(gw)])
            for r in a.to_dict('records'):
                p=forecast['players'][str(r['player_id'])]['weeks'][str(gw)]
                p['xp']=float(r[model])
                # Same selected minutes model for all new point/ranking policies;
                # old baselines retain their historical assumptions for context.
                if model not in ('v1','learned_minutes'):
                    p.update(p_appear=float(r['selected_p_appear']),p60=float(r['selected_p60']),expected_minutes=float(r['selected_expected_minutes']))
            squad=json.loads((ROOT/f'backtests/fixtures/squad_gw{gw}.json').read_text())
            ids=[p['element'] for p in squad['picks']]
            team=advisor.best_lineup(ids,forecast,int(gw))
            score=replay.team_score(advisor,team,forecast['players'],{pid:outcomes[int(gw),pid] for pid in ids})
            weeks.append({'model':model,'gw':int(gw),'points':float(score),
                          'captain':forecast['players'][str(team['captain'])]['name'],
                          'xi':team['xi'],'bench':team['bench'],'vice_captain':team['vice_captain']})
    actual=json.loads((args.baselines/'current-v1-summary.json').read_text())['actual_score']
    totals={model:sum(r['points'] for r in weeks if r['model']==model) for model in models}
    dump(args.output/'lineup-assessment.json',{'totals':totals,'weeks':weeks,'actual_points':actual,
         'scope':'Conditional on actual owned15, legal formations, captain/vice and actual autosubs. No transfer-policy, wildcard or full-season rank claim. No missing historical Claude overrides invented.'})


def main(args):
    threadpool_limits(limits=1,user_api='blas')
    args.output.mkdir(parents=True,exist_ok=True)
    df,selections,scores=evaluate(args.experiments,args.output)
    weight=historical_assessment(df,args.output,args.baselines)
    fit_current(args,selections['2026'],weight)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--features',type=Path,default=ROOT/'research/work/features')
    p.add_argument('--experiments',type=Path,default=ROOT/'research/work/experiments')
    p.add_argument('--output',type=Path,default=ROOT/'research/results')
    p.add_argument('--baselines',type=Path,default=ROOT/'research/work/baselines')
    p.add_argument('--artifacts',type=Path,default=ROOT/'research/artifacts')
    main(p.parse_args())
