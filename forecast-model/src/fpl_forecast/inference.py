"""On-demand selected-procedure inference with exact fresh-process verification."""
import argparse
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tomllib

import numpy as np
import pandas as pd

from .current_inputs import prepare
from .qualification_predict import load_models,make_predictors,apply_current_status_diagnostic
from .utils import atomic_json,bound_file,read_json,sha256,utc

RUNTIME_DEPENDENCIES={'numpy','pandas','scipy','scikit-learn','joblib','threadpoolctl','pyarrow','lightgbm','xgboost','catboost'}
TRAINING_DEPENDENCIES={'numpy','pandas','scipy','scikit-learn','lightgbm','xgboost','catboost'}
CODE_NAMES={'__init__.py','assembly.py','cli.py','context_features.py','current_inputs.py','fetch.py','history.py','inference.py','inputs.py','model.py','offline_check.py','publish.py','qualification_models.py','qualification_predict.py','utils.py'}


def assert_runtime_origin(root):
    source=Path(root).resolve()/'src/fpl_forecast'
    for name,module in list(sys.modules.items()):
        spec=getattr(module,'__spec__',None);declared=getattr(spec,'name','')or ''
        if name=='fpl_forecast'or name.startswith('fpl_forecast.')or declared.startswith('fpl_forecast.'):
            file=getattr(module,'__file__',None)
            if not file or not Path(file).resolve().is_relative_to(source):raise ValueError('Loaded fpl_forecast module is outside supplied package: '+name)


def verify_package(root):
    root=Path(root).resolve();m=read_json(root/'manifest.json')
    assert_runtime_origin(root)
    if m.get('schema')!='portable-fpl-qualification-v2'or m.get('complete')is not True:raise ValueError('A finalized selected-model package is required')
    if sys.version_info[:2]!=(3,12):raise ValueError('Use the pinned Python3.12 model runtime')
    if set(m['runtime_versions'])!=RUNTIME_DEPENDENCIES:raise ValueError('Exact runtime dependency inventory required')
    if set(m['runtime_configuration_sha256'])!={'pyproject.toml','requirements.lock.txt'}:raise ValueError('Runtime configuration inventory differs')
    for name,h in m['runtime_configuration_sha256'].items():bound_file(root,name,h)
    config=tomllib.loads((root/'pyproject.toml').read_text());pins=dict(x.split('==')for x in config['project']['dependencies'])
    if pins!=m['runtime_versions']or config['project']['version']!='2.0.0'or m.get('package_version')!='2.0.0':raise ValueError('Runtime pins or package version differ')
    training=read_json(bound_file(root,'training-runtime-versions.json',m['training_runtime_versions_sha256']))
    if set(training)!=TRAINING_DEPENDENCIES or any(r.get('available')is not True or r.get('version')!=m['runtime_versions'][n]for n,r in training.items()):raise ValueError('Frozen training runtime differs')
    for name,expected in m['runtime_versions'].items():
        if version(name)!=expected:raise ValueError('Pinned dependency mismatch: '+name)
    expected_code={'src/fpl_forecast/'+name for name in CODE_NAMES}
    actual_code={str(p.relative_to(root))for p in (root/'src/fpl_forecast').rglob('*.py')}
    if set(m['code_files'])!=expected_code or actual_code!=expected_code:raise ValueError('Exact package source inventory required')
    for name,expected in m['code_files'].items():bound_file(root,name,expected)
    provenance=read_json(bound_file(root,'stage-provenance.json',m['stage_provenance_sha256']))
    if m['assets']!=provenance['assets']:raise ValueError('Historical asset provenance differs')
    if {str(p.relative_to(root))for p in (root/'data').rglob('*')if p.is_file()}!={a['path']for a in m['assets'].values()}:raise ValueError('Exact historical asset inventory required')
    for asset in m['assets'].values():bound_file(root,asset['path'],asset['sha256'])
    for record in m['models']:bound_file(root,record['model_file'],record['model_sha256'])
    if len({r['configuration_id']for r in m['models']})!=len(m['models'])or {str(p.relative_to(root))for p in (root/'models').rglob('*')if p.is_file()}!={r['model_file']for r in m['models']}:raise ValueError('Exact fitted model inventory required')
    if 'offline_fixture'in m:
        ref=m['offline_fixture'];path=bound_file(root,ref['path'],ref['sha256']);fixture=read_json(path)
        for item in fixture['files'].values():bound_file(path.parent,item['path'],item['sha256'])
    return m


def apply_saved(root,manifest,cases,feature_sets):
    assert_runtime_origin(root)
    registry=load_models(manifest['models'],root,manifest['primary_seed']);arm=manifest['default_variant']
    callback=make_predictors(registry,manifest['selection'],manifest['recipes'],[arm],manifest.get('selected_diagnostics',False))[arm]
    return callback(cases,feature_sets)


def replay(root,out):
    started=time.perf_counter();root,out=Path(root).resolve(),Path(out).resolve();manifest=verify_package(root)
    receipt=read_json(out/'replay-input.json')
    if receipt['package_manifest_sha256']!=sha256(root/'manifest.json'):raise ValueError('Replay model package changed')
    cases=pd.read_parquet(bound_file(out,receipt['cases_file'],receipt['cases_sha256']))
    features={name:pd.read_parquet(bound_file(out,info['path'],info['sha256']))for name,info in receipt['feature_sets'].items()}
    expected=pd.read_parquet(bound_file(out,receipt['prediction_file'],receipt['prediction_sha256']))
    actual=apply_saved(root,manifest,cases,features);checks=[]
    if actual.observation_id.tolist()!=expected.observation_id.tolist():raise ValueError('Replay population/order changed')
    for column in expected.select_dtypes(include='number').columns:
        if column not in actual or not np.array_equal(actual[column].to_numpy(),expected[column].to_numpy(),equal_nan=True):raise ValueError('Exact replay failed: '+column)
        checks.append({'column':column,'rows':len(actual),'exactly_equal':True})
    result={'complete':True,'checks':checks,'replay_pid':os.getpid(),'physical_fits':0,'seconds':time.perf_counter()-started}
    atomic_json(out/'fresh-process-replay.json',result);return result


def aggregate(frame,players,weeks):
    keys=['fpl_element_id','gameweek'];identity=['fpl_element_id','player_id','fpl_code','name','team','fpl_position','price_million_gbp','snapshot_status','chance_of_playing_next_round','can_select']
    columns=['supported_score','approximate_current_rule_expected_total','current_rule_expected_total',
        'approximate_current_rule_expected_total_status_diagnostic','expected_minutes','p_appear','p60','defensive_hit_prior']
    grouped=frame.groupby(keys,as_index=False)[columns].sum(min_count=1)
    counts=frame.groupby(keys).size().rename('fixture_count').reset_index()
    grid=players[identity].merge(pd.DataFrame({'gameweek':weeks}),how='cross').merge(grouped,on=keys,how='left',validate='one_to_one').merge(counts,on=keys,how='left',validate='one_to_one')
    grid['fixture_count']=grid.fixture_count.fillna(0).astype(int);blank=grid.fixture_count.eq(0)
    for c in columns:
        if c!='current_rule_expected_total':grid.loc[blank,c]=0.
    grid=grid.rename(columns={'p_appear':'expected_appearances','p60':'expected_sixty_minute_appearances'})
    grid['schedule_status']=np.where(blank,'blank_at_capture','scheduled_at_capture')
    horizon_columns=[c for c in columns if c not in {'p_appear','p60'}]+['expected_appearances','expected_sixty_minute_appearances']
    horizon=grid.groupby(['fpl_element_id','player_id','name','team','fpl_position'],as_index=False)[horizon_columns].sum(min_count=1)
    horizon['gameweeks']=','.join(map(str,weeks));return grid,horizon


def forecast(package_root,capture_dir,output_dir):
    started=time.perf_counter();root,cap,out=map(lambda p:Path(p).resolve(),(package_root,capture_dir,output_dir));manifest=verify_package(root)
    for protected in [root/'src',root/'models',root/'data',cap]:
        if out==protected or out in protected.parents or protected in out.parents:raise ValueError('Output cannot overwrite package inputs or capture')
    if out.exists()and any(out.iterdir()):raise ValueError('Use a new empty forecast output')
    mhash=sha256(root/'manifest.json');prepared=prepare(root,cap,manifest);cases=prepared['cases'];features=prepared['feature_sets']
    prediction=apply_saved(root,manifest,cases,features);out.mkdir(parents=True,exist_ok=True);(out/'replay').mkdir()
    cases.to_parquet(out/'replay/cases.parquet',index=False)
    # Persist exact named features for independent-process replay; no target labels.
    refs={}
    for name,frame in features.items():
        path=out/f'replay/{name}.parquet';frame.to_parquet(path,index=False);refs[name]={'path':str(path.relative_to(out)),'sha256':sha256(path)}
    prediction.to_parquet(out/'replay/prediction.parquet',index=False)
    atomic_json(out/'replay-input.json',{'package_manifest_sha256':mhash,'cases_file':'replay/cases.parquet','cases_sha256':sha256(out/'replay/cases.parquet'),
        'feature_sets':refs,'prediction_file':'replay/prediction.parquet','prediction_sha256':sha256(out/'replay/prediction.parquet')})
    env=os.environ.copy();env.pop('LAB_RESULT_PATH',None)
    check=subprocess.run([sys.executable,'-m','fpl_forecast.inference','--replay','--package-root',str(root),'--output-dir',str(out)],capture_output=True,text=True,env=env,timeout=300)
    (out/'replay.log').write_text(check.stdout+check.stderr)
    if check.returncode:raise RuntimeError('Fresh-process exact replay failed; inspect replay.log')
    verified=read_json(out/'fresh-process-replay.json')
    if not verified['complete']or verified['replay_pid']==os.getpid():raise ValueError('Independent process verification required')
    merged=cases.merge(prediction,on='observation_id',validate='one_to_one',how='left')
    if not np.isfinite(merged.prediction).all():raise ValueError('Missing complete-procedure predictions')
    merged['supported_score']=merged.prediction
    defensive=prepared['defensive_prior'].reset_index(drop=True)
    merged['defensive_hit_prior']=defensive.defensive_hit_prior.to_numpy()
    merged['approximate_current_rule_expected_total']=merged.supported_score+2*merged.defensive_hit_prior
    merged['current_rule_expected_total']=np.nan
    adjusted=apply_current_status_diagnostic(cases,prediction,prepared['weeks'][0])
    merged['current_status_factor']=adjusted.current_status_factor.to_numpy();merged['current_status_adjustment_known']=adjusted.current_status_adjustment_known.to_numpy()
    merged['approximate_current_rule_expected_total_status_diagnostic']=merged.approximate_current_rule_expected_total*merged.current_status_factor
    gameweeks,horizon=aggregate(merged,prepared['players'],prepared['weeks'])
    for name,frame in [('fixture-forecasts',merged),('gameweek-forecasts',gameweeks),('six-gameweek-forecasts',horizon)]:
        frame.to_parquet(out/(name+'.parquet'),index=False);frame.to_csv(out/(name+'.csv'),index=False)
    if sha256(root/'manifest.json')!=mhash:raise ValueError('Model package changed during inference')
    for entry in prepared['capture']['files']:bound_file(cap,entry['path'],entry['sha256'])
    if pd.Timestamp.now(tz='UTC')>=utc(prepared['capture']['target_deadline_utc']):raise ValueError('Inference crossed the next deadline')
    metadata={'schema':'portable-qualification-forecast-v2','complete':True,'inference_pid':os.getpid(),'default_variant':manifest['default_variant'],
        'default_points_column':'approximate_current_rule_expected_total','primary_seed':manifest['primary_seed'],
        'origin_utc':prepared['capture']['origin_utc'],'target_gameweek':prepared['capture']['target_gameweek'],'target_deadline_utc':prepared['capture']['target_deadline_utc'],
        'gameweeks':prepared['weeks'],'current_players':len(prepared['players']),'fixture_rows':len(merged),'gameweek_rows':len(gameweeks),
        'package_manifest_sha256':mhash,'capture_manifest_sha256':sha256(cap/'manifest.json'),'selection_sha256':manifest['selection_sha256'],
        'physical_fits':0,'model_updates':0,'fresh_process_replay':verified,'current_player_history_rows':prepared['current_player_rows'],'current_team_history_rows':prepared['current_team_rows'],
        'player_history_exclusions':prepared['player_exclusions'],'team_history_exclusions':prepared['team_exclusions'],
        'total_seconds':time.perf_counter()-started,'limitations':manifest['limitations']}
    records=lambda f:json.loads(f.to_json(orient='records',double_precision=15,date_format='iso'))
    atomic_json(out/'forecasts.json',{'metadata':metadata,'gameweeks':records(gameweeks),'six_gameweeks':records(horizon)})
    metadata['files']={str(p.relative_to(out)):sha256(p)for p in sorted(out.rglob('*'))if p.is_file()}
    atomic_json(out/'manifest.json',metadata);return metadata


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--replay',action='store_true',required=True);p.add_argument('--package-root',required=True);p.add_argument('--output-dir',required=True);a=p.parse_args();print(json.dumps(replay(a.package_root,a.output_dir)))
