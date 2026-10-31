"""Copy a tiny already-computed lab origin fixture into a finalized package.

No fitted model is changed, no model is fitted, and no new forecast is generated.
The source is qualification_current_replay's bound saved at-origin outputs.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def write(p,v):Path(p).write_text(json.dumps(v,indent=2,sort_keys=True,allow_nan=False)+'\n')


def add(package_root,source,gameweek=7):
    root,source=Path(package_root).resolve(),Path(source).resolve();package=read(root/'manifest.json')
    if package.get('offline_fixture'):raise ValueError('Do not replace a frozen actual-model fixture')
    summary=read(source/'summary.json');protocol=read(source/'protocol.json')
    if summary.get('complete')is not True or summary['protocol_sha256']!=sha(source/'protocol.json'):raise ValueError('Completed bound lab replay required')
    if protocol['selection_sha256']!=package['selection_sha256']:raise ValueError('Lab replay selection differs')
    if {r['configuration_id']:r['model_sha256']for r in protocol['models']}!={r['configuration_id']:r['model_sha256']for r in package['models']}:
        raise ValueError('Lab replay fitted models differ from portable models')
    def frame(suffix):
        name=f'GW{gameweek:02d}-{suffix}.parquet';path=source/name
        if sha(path)!=summary['files'][name]:raise ValueError('Source lab replay artifact changed')
        return pd.read_parquet(path),sha(path)
    cases,ch=frame('origins');expected,eh=frame('components');defense,dh=frame('defensive-prior')
    if cases.observation_id.tolist()!=expected.observation_id.tolist()or len(cases)!=len(defense):raise ValueError('Lab fixture populations differ')
    selected=[]
    for position in ['GKP','DEF','MID','FWD']:
        selected.extend(cases.index[cases.fpl_position.eq(position)][:8].tolist())
    selected=sorted(set(selected))
    if not selected:raise ValueError('No at-origin rows for offline fixture')
    allow=['observation_id','player_id','fpl_element_id','fpl_code','fpl_position','origin','gameweek','season',
        'was_home','is_home','club_id','opponent_id','context_missing','canonical_fixture_id','fpl_fixture_id']
    slim=cases.loc[selected,[c for c in allow if c in cases]].reset_index(drop=True)
    out=root/'verification/at-origin-v1'
    if out.exists():raise ValueError('Offline fixture destination must be new')
    out.mkdir(parents=True);files={}
    def save(name,value):
        path=out/(name+'.parquet');value.to_parquet(path,index=False);files[name]={'path':path.name,'sha256':sha(path)}
    save('cases',slim);save('expected',expected.iloc[selected].reset_index(drop=True));save('defensive_prior',defense.iloc[selected].reset_index(drop=True))
    total=expected.prediction.to_numpy()+2*defense.defensive_hit_prior.to_numpy()
    save('approximate_total',pd.DataFrame({'approximate_current_rule_expected_total':total[selected]}))
    sources={'origins':ch,'components':eh,'defensive-prior':dh};sets=['base','opponent_enhanced','rolling_exact']
    for name in sets:
        values,h=frame(name)
        if len(values)!=len(cases):raise ValueError('Lab feature population differs')
        save(name,values.iloc[selected].reset_index(drop=True));sources[name]=h
    fixture={'version':'selected-procedure-offline-v1','physical_fits':0,'new_forecasts':0,'rows':len(selected),
        'selection_sha256':package['selection_sha256'],'primary_seed':package['primary_seed'],
        'model_hashes':{r['configuration_id']:r['model_sha256']for r in package['models']},
        'feature_sets':sets,'files':files,'tolerance':{'absolute':1e-10,'relative':1e-10},
        'source':{'logical_artifact':'qualification-current-replay','gameweek':gameweek,'summary_sha256':sha(source/'summary.json'),
            'protocol_sha256':sha(source/'protocol.json'),'file_sha256':sources},
        'selection_rule':'First up to8 original-order rows in each FPL position, then original global row order; no outcome/score filtering.',
        'contains_outcome_labels':False,'contains_owned_squad':False}
    write(out/'manifest.json',fixture);package['offline_fixture']={'path':str((out/'manifest.json').relative_to(root)),'sha256':sha(out/'manifest.json')}
    write(root/'manifest.json',package);return fixture


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--package-root',required=True);p.add_argument('--source',required=True);p.add_argument('--gameweek',type=int,default=7);a=p.parse_args()
    value=add(a.package_root,a.source,a.gameweek);print(json.dumps({'complete':True,'rows':value['rows'],'new_forecasts':0,'physical_fits':0}))
