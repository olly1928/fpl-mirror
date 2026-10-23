"""Select whole forecast procedures using earlier chronological OOF results."""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from research.estimators import candidates,minutes_candidates
from research.experiment import point_metrics,minute_metrics,dump,safe


def load_points(folder,year,name):
    return np.load(folder/str(year)/f'{name}-seed1729.npz')['points'].astype(float)


def prior_years(year):
    return list(range(max(2016,year-3),year))


def rank_candidates(folder,year,minutes=False):
    choices=minutes_candidates() if minutes else candidates()
    rows=[]
    for candidate in choices:
        records=[]
        for past in prior_years(year):
            path=folder/str(past)/f'{candidate.name}-seed1729.json'
            if not path.exists():break
            record=json.loads(path.read_text())
            if record['status']!='ok':break
            records.append(record)
        if len(records)!=len(prior_years(year)) or not records:
            continue
        key='log_loss' if minutes else 'mse'
        score=float(np.mean([r['metrics']['active'][key] for r in records]))
        rows.append({'name':candidate.name,'family':candidate.family,'score':score,'years':prior_years(year)})
    return sorted(rows,key=lambda r:(r['score'],r['name']))


def fit_weights(matrix,y,years,penalty=.02):
    """Simplex-constrained stacking, equal season influence, no outer labels."""
    unique,counts=np.unique(years,return_counts=True)
    count=dict(zip(unique,counts))
    weight=np.array([1/(len(unique)*count[v]) for v in years])
    k=matrix.shape[1];uniform=np.full(k,1/k)
    def loss(w):
        error=matrix@w-y
        return np.sum(weight*error**2)+penalty*np.sum((w-uniform)**2)
    def gradient(w):
        return 2*matrix.T@(weight*(matrix@w-y))+2*penalty*(w-uniform)
    result=minimize(loss,uniform,jac=gradient,bounds=[(0,1)]*k,
                    constraints=[{'type':'eq','fun':lambda w:w.sum()-1,'jac':lambda w:np.ones(k)}],
                    method='SLSQP',options={'ftol':1e-10,'maxiter':500})
    if not result.success:raise RuntimeError(result.message)
    return np.maximum(0,result.x)/np.maximum(0,result.x).sum()


def select(folder,year):
    ranked=rank_candidates(folder,year)
    if not ranked:raise ValueError(f'No complete prior OOF trials for {year}')
    families={}
    for r in ranked:
        families.setdefault(r['family'],r)
    top=list(families.values())[:5]
    names=[r['name'] for r in top]
    xs=[];ys=[];seasons=[]
    for past in prior_years(year):
        target=pd.read_pickle(folder/str(past)/'targets.pkl')
        use=target.active.to_numpy()==1
        # Row-level stacking does not overweight DGWs: collapse to GW before fit.
        frame=target[['season','gw','player_id','y_total_points','active']].copy()
        for name in names:
            frame[name]=load_points(folder,past,name)
        grouped=frame.groupby(['season','gw','player_id'],sort=False).agg({
            'y_total_points':'sum','active':'max',**{name:'sum' for name in names}})
        grouped=grouped[grouped.active==1]
        xs.append(grouped[names].to_numpy());ys.append(grouped.y_total_points.to_numpy())
        seasons.extend([past]*len(grouped))
    x=np.vstack(xs);y=np.concatenate(ys);season=np.array(seasons)
    weights=fit_weights(x,y,season)
    xp=x@weights
    # OOF-only linear calibration; bounded to avoid unstable slope extrapolation.
    n_by_year={v:int(np.sum(season==v)) for v in np.unique(season)}
    w=np.array([1/n_by_year[v] for v in season])
    design=np.column_stack([np.ones(len(xp)),xp])
    coef=np.linalg.solve(design.T@(w[:,None]*design)+np.eye(2)*10,
                         design.T@(w*y)+np.array([0,10]))
    intercept=float(np.clip(coef[0],-1,1));slope=float(np.clip(coef[1],.5,1.5))
    minute=rank_candidates(folder,year,minutes=True)
    return {'year':year,'selected_using_years':prior_years(year),'single':ranked[0]['name'],
            'family_champions':families,'ensemble_names':names,'ensemble_weights':weights.tolist(),
            'calibration':{'intercept':intercept,'slope':slope,'training_rows':len(y)},
            'minutes':minute[0]['name'] if minute else None,'ranking':ranked,'minute_ranking':minute}


def evaluate(folder,output,first=2018,last=2025):
    output.mkdir(parents=True,exist_ok=True)
    all_rows=[];selection={};scores=[];family_scores=[]
    for year in range(first,last+1):
        chosen=select(folder,year);selection[str(year)]=chosen
        target=pd.read_pickle(folder/str(year)/'targets.pkl')
        names=chosen['ensemble_names'];weights=np.array(chosen['ensemble_weights'])
        matrix=np.stack([load_points(folder,year,name) for name in names],axis=1)
        ensemble=matrix@weights
        selected={
            'rolling12':load_points(folder,year,'rolling_03'),
            'empirical_h8_p900':load_points(folder,year,'empirical_05'),
            'selected_single':load_points(folder,year,chosen['single']),
            'nested_ensemble':ensemble,
            'calibrated_ensemble':chosen['calibration']['intercept']+chosen['calibration']['slope']*ensemble,
            'equal_top3':np.mean(matrix[:,:3],axis=1)}
        frame=target.copy()
        for name,p in selected.items():
            frame[name]=p
            scores.append({'year':year,'model':name,**point_metrics(target,p)})
        for family,info in chosen['family_champions'].items():
            p=load_points(folder,year,info['name'])
            family_scores.append({'year':year,'family':family,'selected':info['name'],**point_metrics(target,p)})
        if chosen['minutes']:
            minutes=np.load(folder/str(year)/f'{chosen["minutes"]}-seed1729.npz')['minutes']
            for j,k in enumerate(['p_appear','p60','expected_minutes','p0','p_short','p_long']):
                frame['selected_'+k]=minutes[:,j]
        all_rows.append(frame)
    selection['2026']=select(folder,2026)
    df=pd.concat(all_rows,ignore_index=True)
    df.to_pickle(output/'nested-predictions.pkl')
    dump(output/'nested-selection.json',safe(selection))
    dump(output/'nested-scores.json',safe(scores))
    dump(output/'family-scores.json',safe(family_scores))
    return df,selection,scores


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--experiments',type=Path,default=Path('research/work/experiments'))
    p.add_argument('--output',type=Path,default=Path('research/work/evaluation'))
    a=p.parse_args();evaluate(a.experiments,a.output)
