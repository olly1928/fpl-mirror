"""Finalize a portable package from an immutable latest-fit selection; no fitting."""
import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import shutil
import sys
import tomllib
import pandas as pd

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def write(p,m):Path(p).write_text(json.dumps(m,indent=2,sort_keys=True,allow_nan=False)+'\n')


def export(stage,protocol_path,selection_path,records_path,output):
    stage,protocol_path,selection_path,records_path,output=map(lambda p:Path(p).resolve(),(stage,protocol_path,selection_path,records_path,output))
    if output.exists()and any(output.iterdir()):raise ValueError('Final package output must be new')
    protocol=read(protocol_path);lock=read(selection_path)
    training_runtime_path=protocol_path.parent/'runtime-versions.json';training_runtime=read(training_runtime_path)
    required_training={'numpy','pandas','scipy','scikit-learn','lightgbm','xgboost','catboost'}
    if set(training_runtime)!=required_training:raise ValueError('Frozen training runtime inventory differs')
    for name,ref in training_runtime.items():
        if ref.get('available')is not True or ref.get('version')!=version(name):raise ValueError('Current environment differs from frozen training runtime: '+name)
    config=tomllib.loads((stage/'pyproject.toml').read_text())
    pins=dict(x.split('==')for x in config['project']['dependencies'])
    for name,pin in pins.items():
        if version(name)!=pin:raise ValueError('Current environment differs from staged dependency pin: '+name)
    if not {'frozen_at','configuration_ids','selection_file','selection_sha256','procedure'}<=set(lock):raise ValueError('A complete frozen final selection lock is required')
    frozen=pd.Timestamp(lock['frozen_at'])
    if pd.isna(frozen)or frozen.tzinfo is None:raise ValueError('Final lock needs timezone-aware freeze time')
    upstream=Path(lock['selection_file']);upstream=upstream if upstream.is_absolute()else protocol_path.parent/upstream
    if sha(upstream)!=lock['selection_sha256']:raise ValueError('Underlying selected procedure changed')
    source=read(upstream)
    if source.get('protocol_sha256')!=sha(protocol_path):raise ValueError('Selection not bound to this frozen protocol')
    if lock['procedure']!=source.get('procedure')or('components'in lock and lock['components']!=source['components']):raise ValueError('Final lock differs from underlying selected procedure/components')
    if not set(lock['configuration_ids'])<=set(source.get('required_configuration_ids',[])):raise ValueError('Final lock expanded selected configurations')
    arm=source['procedure']['arm'];seed=int(protocol['primary_seed'])
    # Selection arithmetic needs only these values. Keep local artifact paths out
    # of the distributable package while binding the complete source by hash.
    public_selection={'components':source['components'],'seed':seed,'procedure':{'arm':arm},'protocol_sha256':sha(protocol_path)}
    sys.path.insert(0,str(stage/'src'))
    from fpl_forecast.qualification_predict import required_configurations,load_models
    needed=set(required_configurations(source,protocol['recipes'],[arm],False).values())
    if int(source.get('seed',seed))!=seed or not needed<=set(lock['configuration_ids']):raise ValueError('Final lock omits deployed recipes or changes primary seed')
    raw=read(records_path);records=raw if isinstance(raw,list)else raw['records'];byid={}
    for r in records:
        if r['configuration_id']not in needed:continue
        if r['configuration_id']in byid:raise ValueError('Duplicate latest-fit recipe')
        if not r.get('complete')or r.get('status')!='ok'or r.get('purpose')!='latest-export' or int(r['seed'])!=seed or r.get('training_cutoff_season')!='2026-27':raise ValueError('Completed fixed-primary latest fits with2026-27 cutoff required')
        origin=pd.Timestamp(r['fit_origin_utc'])
        if pd.isna(origin)or origin.tzinfo is None or origin<frozen:raise ValueError('Fit origin predates final lock or lacks timezone')
        if r['selection_sha256']!=sha(selection_path)or r['protocol_sha256']!=sha(protocol_path):raise ValueError('Fit belongs to a different selection/protocol')
        if sha(r['model_file'])!=r['model_sha256']:raise ValueError('Selected fitted model changed')
        for file_key,hash_key in [('indices_file','indices_sha256'),('specification_file','specification_sha256'),('worker_file','worker_sha256')]:
            source_path=Path(r[file_key]);source_path=source_path if source_path.is_absolute()else protocol_path.parent/source_path
            if sha(source_path)!=r[hash_key]:raise ValueError('Latest fit provenance changed: '+file_key)
        byid[r['configuration_id']]=r
    if set(byid)!=needed:raise ValueError('Missing selected or common rolling availability fits')
    load_models(list(byid.values()),expected_seed=seed)
    shutil.copytree(stage,output,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','*.pyc','.venv','runs','captures','latest','verification','models'))
    (output/'models').mkdir(exist_ok=True);packaged=[]
    for cid,r in sorted(byid.items()):
        dest=output/'models'/(cid+'.joblib');shutil.copyfile(r['model_file'],dest)
        keep=['configuration_id','component','seed','recipe','feature_columns','training_index_sha256','training_max_available_at','training_rows','training_cutoff_season','fit_origin_utc','selection_sha256','protocol_sha256']
        packaged.append({**{k:r[k]for k in keep if k in r},'model_file':str(dest.relative_to(output)),'model_sha256':sha(dest),'existing_fit_reused':True})
    provenance=read(output/'stage-provenance.json')
    for asset in provenance['assets'].values():
        if sha(output/asset['path'])!=asset['sha256']:raise ValueError('Prepared historical asset changed')
    dependencies=['numpy','pandas','scipy','scikit-learn','joblib','threadpoolctl','pyarrow','lightgbm','xgboost','catboost']
    if set(pins)!=set(dependencies)or config['project']['version']!='2.0.0':raise ValueError('Exact portable dependency inventory/version required')
    shutil.copyfile(training_runtime_path,output/'training-runtime-versions.json')
    manifest={'schema':'portable-fpl-qualification-v2','complete':True,'supported_season':'2026-27','default_variant':arm,
        'package_version':'2.0.0','training_runtime_versions_sha256':sha(training_runtime_path),
        'runtime_configuration_sha256':{name:sha(output/name)for name in ['pyproject.toml','requirements.lock.txt']},
        'default_points_column':'approximate_current_rule_expected_total','primary_seed':seed,'selection':public_selection,'recipes':protocol['recipes'],
        'selection_sha256':sha(selection_path),'protocol_sha256':sha(protocol_path),'latest_fit_records_sha256':sha(records_path),'selected_diagnostics':False,'models':packaged,'assets':provenance['assets'],
        'physical_fits':0,'new_model_updates':0,'release_validation_complete':False,'deployment_recommendation':'research_only_pending_full_evidence','runtime_versions':{name:version(name)for name in dependencies},
        'code_files':{str(p.relative_to(output)):sha(p)for p in sorted((output/'src/fpl_forecast').glob('*.py'))},
        'stage_provenance_sha256':sha(output/'stage-provenance.json'),
        'release_validation_pending':['same-capture parity against lab selected procedure','independent relocated inference','manual GitHub hosted execution if promoted'],
        'limitations':['Raw supported score uses historical FPL assist/bonus definitions; exact current-rule total remains unavailable.',
            'Approximate current total adds twice a lag-only defensive-hit prior. This is a separate imperfect current-rule bridge.',
            'Current status/chance adjustment is a next-GW-only diagnostic; raw points retained and later availability unknown.',
            'All six weeks share one origin. Future fixture/transfer changes require another fresh run.',
            'Archived opportunity/position and realised-calendar assumptions remain; no certified joint distribution or guaranteed advantage.',
            'Fixed2026-27 club IDs/codes only; official API access is required in the executing environment.']}
    write(output/'manifest.json',manifest);return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--stage',required=True);p.add_argument('--protocol',required=True);p.add_argument('--selection',required=True);p.add_argument('--records',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    m=export(a.stage,a.protocol,a.selection,a.records,a.output);print(json.dumps({'complete':True,'models':len(m['models']),'physical_fits':0,'default_variant':m['default_variant']}))
