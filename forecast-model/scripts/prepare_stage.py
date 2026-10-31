"""Developer-only preparation; copies feature history/source, never fits/predicts."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_text())
def write(path,value):Path(path).write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')


def partition_history(history,data):
    """Small uploadable pieces; persisted monotone ordinal restores exact order."""
    folder=data/'history';folder.mkdir(exist_ok=True);rows=history.copy()
    if '_package_row_index'in rows:raise ValueError('Reserved package row index already exists')
    rows['_package_row_index']=np.arange(len(rows),dtype=np.int64);parts=[];limit=2*1024*1024
    for season,group in rows.groupby('season',sort=True):
        if not isinstance(season,str)or not __import__('re').fullmatch(r'[0-9]{4}-[0-9]{2}',season):raise ValueError('Invalid history season')
        pending=[group];chunks=[]
        while pending:
            g=pending.pop(0);test=folder/(season+'.pending.parquet');g.to_parquet(test,index=False,compression='zstd')
            if test.stat().st_size>limit:
                test.unlink()
                if len(g)<2:raise ValueError('Single history row exceeds package partition limit')
                cut=len(g)//2;pending=[g.iloc[:cut],g.iloc[cut:]]+pending;continue
            chunks.append(g);test.unlink()
        for i,g in enumerate(chunks):
            name=season+(''if len(chunks)==1 else f'-{i+1:02d}')+'.parquet';p=folder/name
            g.to_parquet(p,index=False,compression='zstd')
            parts.append({'path':'data/history/'+name,'sha256':sha(p),'bytes':p.stat().st_size,'rows':len(g),'season':season})
    reconstructed=pd.concat([pd.read_parquet(data.parent/p['path'])for p in parts],ignore_index=True).sort_values('_package_row_index',kind='stable')
    np.testing.assert_array_equal(reconstructed.pop('_package_row_index'),np.arange(len(history)))
    pd.testing.assert_frame_equal(reconstructed.reset_index(drop=True),history.reset_index(drop=True),check_exact=True)
    index={'version':'lossless-history-partitions-v1','rows':len(history),'columns':list(history.columns),
        'dtypes':{c:str(t)for c,t in history.dtypes.items()},'order':'_package_row_index ascending; exactly0 through rows−1',
        'maximum_part_bytes':limit,'parts':parts,'exact_concatenated_parity':True}
    write(data/'player-history-index.json',index)
    old=data/'player-history.parquet'
    if old.exists():old.unlink()
    return index


def extract(path,names,imports):
    tree=ast.parse(Path(path).read_text());nodes=[];seen=set()
    for node in tree.body:
        name=node.name if isinstance(node,(ast.FunctionDef,ast.ClassDef)) else node.targets[0].id if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name) else None
        if name in names:nodes.append(node);seen.add(name)
    if seen!=set(names):raise ValueError('Missing vendor definitions: '+str(set(names)-seen))
    for node in nodes:
        for child in ast.walk(node):
            if isinstance(child,ast.ImportFrom) and child.level==1 and child.module is None:
                for alias in child.names:
                    if alias.name=='modern_components':alias.name='model'
    body='"""Vendored frozen inference arithmetic; no training/search/DB routines."""\n'+imports+'\n\n'+ast.unparse(ast.Module(body=nodes,type_ignores=[]))+'\n'
    return body


def prepare(lab,v1,stage):
    lab,v1,stage=map(lambda p:Path(p).resolve(),(lab,v1,stage));src=stage/'src/fpl_forecast';data=stage/'data'
    src.mkdir(parents=True,exist_ok=True);data.mkdir(parents=True,exist_ok=True)
    if (stage/'manifest.json').exists():raise ValueError('Do not overwrite a finalized model package')
    sources={};copied={}
    for name in ['__init__.py','fetch.py','inputs.py','utils.py','model.py']:
        old=v1/'src/fpl_forecast'/name;shutil.copyfile(old,src/name);copied[name]=sha(old)
    (src/'__init__.py').write_text((src/'__init__.py').read_text().replace("__version__ = '1.0.0'","__version__ = '2.0.0'"))
    cli=(v1/'src/fpl_forecast/cli.py').read_text().replace("'default_recipe':'learned_subtotal'","'default_recipe':config['default_variant']").replace("'recipe':'learned_subtotal'","'recipe':config['default_variant']")
    (src/'cli.py').write_text(cli);copied['cli.py:source']=sha(v1/'src/fpl_forecast/cli.py')
    plan={
      'context_features.py':('qualification_data.py',{'VERSION','VALUES','DEFAULTS','WINDOWS','ns','_side_features','build_context_features'},'import numpy as np\nimport pandas as pd'),
      'qualification_models.py':('qualification_models.py',{'VERSION','SEED','FOCUS','ASSEMBLY','TARGETS','KINDS','POPULATIONS','POSITIONS','_values','target_arrays','rolling_columns','build_rolling_features','_array','_normalize','predict_model'},'import numpy as np\nimport pandas as pd'),
      'assembly.py':('qualification_evaluation.py',{'GOAL_WEIGHTS','probabilities','reference_map','assemble_supported_score'},'import numpy as np'),
    }
    for dest,(name,nodes,imports) in plan.items():
        old=lab/'src/fpl_lab'/name;(src/dest).write_text(extract(old,nodes,imports));sources[dest]={'source_name':name,'source_sha256':sha(old),'definitions':sorted(nodes),'vendored_sha256':sha(src/dest)}
    old=lab/'src/fpl_lab/qualification_predict.py';tree=ast.parse(old.read_text())
    tree.body=[n for n in tree.body if not isinstance(n,ast.FunctionDef) or n.name!='dependency_manifest']
    for n in tree.body:
        if isinstance(n,ast.ImportFrom) and n.module=='qualification_evaluation':n.module='assembly'
        if isinstance(n,ast.ImportFrom) and n.module=='qualification_models':n.names=[a for a in n.names if a.name!='runtime_versions']
    (src/'qualification_predict.py').write_text(ast.unparse(tree)+'\n');sources['qualification_predict.py']={'source_name':'qualification_predict.py','source_sha256':sha(old),'vendored_sha256':sha(src/'qualification_predict.py'),'transform':'Only relative module aliases changed; dependency-report helper removed. Prediction arithmetic unchanged.'}
    history_path=v1/'data/historical-features.parquet';history=pd.read_parquet(history_path)
    if not (history.season<'2026-27').all():raise ValueError('Historical player asset must exclude current season')
    # This projection already contains every raw scoring input needed for exact rolling targets.
    required={'minutes','goals_scored','assists','bonus','saves','goals_conceded','clean_sheets','yellow_cards','red_cards','own_goals','penalties_missed','penalties_saved','total_points','fpl_position'}
    if not required<=set(history):raise ValueError('Raw scoring inputs missing from historical projection')
    history_index=partition_history(history,data)
    teams=pd.read_parquet(lab/'artifacts/core/team-panel.parquet');teams=teams[teams.season<'2026-27'].copy()
    teams.to_parquet(data/'team-history.parquet',index=False,compression='zstd')
    bridge=read(lab/'artifacts/bridge/current-player-bridge.json');snapshot=read(lab/'artifacts/bridge/fpl-current-selected.json')
    goal=pd.read_parquet(lab/'artifacts/core/goal-panel.parquet',columns=['fixture_id','player_id','club_id'])
    function=next(n for n in ast.parse((lab/'src/fpl_lab/current_forecast.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='club_map_from_bridge')
    namespace={};exec(compile(ast.Module(body=[function],type_ignores=[]),'evidenced-club-map','exec'),namespace)
    mapping=namespace['club_map_from_bridge'](bridge,goal,pd.DataFrame(snapshot['teams']))
    old_manifest=read(lab/'artifacts/current-inputs/manifest.json')
    if {str(k):v for k,v in mapping.items()}!=old_manifest['club_map']:raise ValueError('Recomputed corroborated team map differs')
    club_map={'season':'2026-27','teams':[{**t,'canonical_club_id':mapping[int(t['id'])]}for t in snapshot['teams']],
        'method':'Accepted source-player ID with canonical club corroboration; one unique canonical club per official2026team ID, with official stable team code retained.',
        'evidence_sha256':{'bridge':sha(lab/'artifacts/bridge/current-player-bridge.json'),'source_snapshot':sha(lab/'artifacts/bridge/fpl-current-selected.json'),'canonical_goal_panel':sha(lab/'artifacts/core/goal-panel.parquet'),'mapping_function_source':sha(lab/'src/fpl_lab/current_forecast.py')}}
    write(data/'club-map.json',club_map)
    assets={str(p.relative_to(data)):{'path':'data/'+str(p.relative_to(data)),'sha256':sha(p),'bytes':p.stat().st_size}for p in sorted(data.rglob('*'))if p.is_file()}
    provenance={'version':'qualification-portable-stage-v1','complete':False,'final_models_present':False,'supported_season':'2026-27','physical_fits':0,'real_forecasts':0,
        'copied_v1_source_sha256':copied,'vendored_sources':sources,'assets':assets,'player_history_rows':len(history),'team_history_rows':len(teams),
        'source_transforms':{'__init__.py':'Package __version__ updated to2.0.0; original source hash retained.'},
        'history_partition_count':len(history_index['parts']),'history_largest_partition_bytes':max(p['bytes']for p in history_index['parts']),'history_exact_parity':True,
        'frozen_current_team_rows_excluded':int((pd.read_parquet(lab/'artifacts/core/team-panel.parquet',columns=['season']).season=='2026-27').sum()),
        'source_package_manifest_sha256':sha(v1/'manifest.json'),'required_release_gates':['lockedselectedmodel export','same-capture lab/package parity','fresh-process exact replay','relocated independent inference','hosted execution separately checked'],
        'mirror_adapter':'Not implemented: current mirror player_history aggregates DGW columns with pipes and loses per-fixture nonlinear target blocks. Official per-fixture summaries or another lossless per-fixture feed required.'}
    write(stage/'stage-provenance.json',provenance);return provenance


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--lab',required=True);p.add_argument('--v1',required=True);p.add_argument('--stage',required=True);a=p.parse_args()
    m=prepare(a.lab,a.v1,a.stage);print(json.dumps({k:m[k]for k in ['complete','physical_fits','real_forecasts','player_history_rows','team_history_rows']}))
