import ast
import copy
import importlib.util
from importlib.metadata import version
import os
import subprocess
import sys
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest

from fpl_forecast import current_inputs as inputs
from fpl_forecast import context_features, qualification_models as models
from fpl_forecast import inference, model
from fpl_forecast.history import read_history
from fpl_forecast.utils import sha256

STAGE=Path(__file__).resolve().parents[1]


def clubs():
    teams=[{'id':i,'code':100+i,'name':f'Club {i}','short_name':f'T{i}',
            'canonical_club_id':f'club-{i}'}for i in range(1,21)]
    return {'season':'2026-27','teams':teams},{'teams':teams}


def fixture(fid,event,kickoff,goals=(2,0),finished=True):
    return {'id':fid,'event':event,'kickoff_time':kickoff,'team_h':1,'team_a':2,
            'team_h_score':goals[0],'team_a_score':goals[1],'finished':finished,'started':finished}


def raw_history():
    raw=pd.DataFrame({'observation_id':['h1','h2','h3'],'player_id':['fpl-code:1001']*3,
        'season':['2026-27']*3,'gameweek':[1,2,3],'fpl_position':['GKP']*3,
        'event_time':pd.to_datetime(['2026-08-01','2026-08-05','2026-08-09'],utc=True),
        'minutes':[0,90,59],'goals_scored':[0,0,0],'assists':[0,0,1],'bonus':[0,2,0],
        'clean_sheets':[0,1,0],'saves':[2,4,5],'goals_conceded':[0,0,3],
        'penalties_saved':[0,0,0],'red_cards':[0,0,0],'yellow_cards':[1,0,0],
        'own_goals':[0,0,0],'penalties_missed':[0,0,0],'total_points':[-1,9,4]})
    for c in model.BASE_LAG_VALUES:
        if c not in raw:raw[c]=0.
    return model.masks_and_targets(raw)


def cases():
    return pd.DataFrame({'observation_id':['future'],'player_id':['fpl-code:1001'],
        'fpl_position':['GKP'],'season':['2026-27'],'gameweek':[3],
        'origin':[pd.Timestamp('2026-08-07T00:00:00Z')],'was_home':[True],
        'is_home':[1.],'club_id':['club-1'],'opponent_id':['club-2'],
        'context_missing':[False],'canonical_fixture_id':['current-fpl:2026-27:3']})


def test_fixed_season_club_map_fails_closed_on_id_code_and_alias_drift():
    mapping,bootstrap=clubs();assert len(inputs.validate_clubs(bootstrap,mapping,'2026-27'))==20
    altered=copy.deepcopy(bootstrap);altered['teams'][0]['code']=999
    with pytest.raises(ValueError,match='drift'):inputs.validate_clubs(altered,mapping,'2026-27')
    altered=copy.deepcopy(mapping);altered['teams'][1]['canonical_club_id']='club-1'
    with pytest.raises(ValueError,match='unique'):inputs.validate_clubs(bootstrap,altered,'2026-27')
    with pytest.raises(ValueError,match='only'):inputs.validate_clubs(bootstrap,mapping,'2027-28')


def test_fresh_team_results_have_strict_lag_and_target_gameweek_guards():
    mapping,b=clubs();c=inputs.validate_clubs(b,mapping,'2026-27')
    f=[fixture(1,1,'2026-08-01T00:00:00Z'),fixture(2,2,'2026-08-05T00:00:00Z'),
       fixture(3,3,'2026-08-01T00:00:00Z'),fixture(4,2,None,finished=False)]
    a,excluded=inputs.current_team_history(f,c,'2026-27','2026-08-07T00:00:00Z',3)
    assert len(a)==2 and a.goals.tolist()==[2,0] and a.conceded.tolist()==[0,2]
    assert excluded=={'unfinished':1,'target_or_future_gameweek':1,'availability_lag':1}
    later,_=inputs.current_team_history(f,c,'2026-27','2026-08-07T00:00:00.000001Z',3)
    assert len(later)==4
    with pytest.raises(ValueError,match='Duplicate'):inputs.current_team_history(f+f[:1],c,'2026-27','2026-08-07T00:00:00Z',3)
    bad=copy.deepcopy(f);bad[0]['team_h_score']=None
    with pytest.raises(ValueError,match='scores'):inputs.current_team_history(bad,c,'2026-27','2026-08-07T00:00:00Z',3)


def test_future_calendar_preserves_double_gameweek_and_player_identity():
    mapping,b=clubs();c=inputs.validate_clubs(b,mapping,'2026-27')
    b['elements']=[{'id':7,'code':1001,'team':1,'web_name':'Player','element_type':1,'now_cost':45,'status':'a'}]
    f=[fixture(10,10,'2026-11-07T15:00:00Z',finished=False),fixture(11,10,'2026-11-10T15:00:00Z',finished=False)]
    rows,players=inputs.future_cases(b,f,c,'2026-27','2026-11-05T12:00:00Z',[10,11])
    assert rows.fpl_fixture_id.tolist()==[10,11] and len(players)==1
    assert rows.observation_id.nunique()==2 and rows.player_id.nunique()==1
    f[0]['started']=True
    with pytest.raises(ValueError,match='started'):inputs.future_cases(b,f,c,'2026-27','2026-11-05T12:00:00Z',[10])


def test_context_and_exact_rolling_ignore_boundary_and_future_outcomes():
    history=raw_history();origin=cases();mapping,b=clubs();c=inputs.validate_clubs(b,mapping,'2026-27')
    team,_=inputs.current_team_history([fixture(1,1,'2026-08-01T00:00:00Z'),fixture(2,2,'2026-08-05T00:00:00Z')],c,'2026-27','2026-08-08T00:00:00Z',3)
    context,proof=context_features.build_context_features(team,origin)
    rolling,latest=models.build_rolling_features(history,origin)
    assert context.loc[0,'team_history_n']==1 and rolling.loc[0,'q_save_blocks_observed20']==1
    assert rolling.loc[0,'q_save_blocks_mean20']==0
    altered=history.copy();altered.loc[1:,'saves']=999
    same,_=models.build_rolling_features(altered,origin);pd.testing.assert_frame_equal(rolling,same)
    changed=team.copy();changed.loc[changed.fixture_id.str.endswith(':2'),['goals','conceded']]=999
    same,proof2=context_features.build_context_features(changed,origin);pd.testing.assert_frame_equal(context,same)
    assert (proof.to_numpy()<origin.origin.iloc[0].value).all()and(latest<origin.origin.iloc[0].value).all()
    pd.testing.assert_frame_equal(proof,proof2)


def test_staged_feature_builders_match_frozen_lab_on_synthetic_rows():
    # The research path is a test dependency only, never a runtime dependency.
    pytest.importorskip('fpl_lab')
    from fpl_lab import qualification_models as old_roll,qualification_data as old_context,modern_components as old_base
    h=raw_history();o=cases();mapping,b=clubs();c=inputs.validate_clubs(b,mapping,'2026-27')
    team,_=inputs.current_team_history([fixture(1,1,'2026-08-01T00:00:00Z')],c,'2026-27','2026-08-07T00:00:00Z',3)
    pd.testing.assert_frame_equal(model.build_features(h,o),old_base.build_features(h,o))
    a,ap=models.build_rolling_features(h,o);e,ep=old_roll.build_rolling_features(h,o)
    pd.testing.assert_frame_equal(a,e);np.testing.assert_array_equal(ap,ep)
    a,ap=context_features.build_context_features(team,o);e,ep=old_context.build_context_features(team,o)
    pd.testing.assert_frame_equal(a,e);pd.testing.assert_frame_equal(ap,ep)
    assert model.build_features(h,o).shape[1]==83 and a.shape[1]==46 and models.build_rolling_features(h,o)[0].shape[1]==56


def test_runtime_contains_no_training_search_database_or_lab_imports():
    for path in (STAGE/'src/fpl_forecast').glob('*.py'):
        tree=ast.parse(path.read_text())
        imports=[x.module or ''for x in ast.walk(tree)if isinstance(x,ast.ImportFrom)]
        imports += [a.name for x in ast.walk(tree)if isinstance(x,ast.Import)for a in x.names]
        assert not any(x.startswith(('fpl_lab','duckdb'))for x in imports)
        assert not any(isinstance(x,ast.FunctionDef)and x.name.startswith(('fit_model','fit_predict','prepare_rolling','recipes'))for x in ast.walk(tree))


def export_module():
    spec=importlib.util.spec_from_file_location('stage_export',STAGE/'scripts/export_selected.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def script_module(name):
    spec=importlib.util.spec_from_file_location(name,STAGE/'scripts'/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_season_partitions_preserve_original_interleaved_rows_and_reject_changed_ordinals(tmp_path):
    original=raw_history();original.loc[1,'season']='2025-26';data=tmp_path/'data';data.mkdir()
    index=script_module('prepare_stage').partition_history(original,data)
    path=data/'player-history-index.json'
    pd.testing.assert_frame_equal(read_history(tmp_path,'data/player-history-index.json',sha256(path)),original)
    assert len(index['parts'])==2 and max(p['bytes']for p in index['parts'])<=2*1024*1024
    part=index['parts'][0];file=tmp_path/part['path'];changed=pd.read_parquet(file)
    changed['_package_row_index']=0;changed.to_parquet(file,index=False)
    part.update(sha256=sha256(file),bytes=file.stat().st_size);path.write_text(json.dumps(index))
    with pytest.raises(ValueError,match='alignment'):read_history(tmp_path,'data/player-history-index.json',sha256(path))


@pytest.fixture
def export_fixture(tmp_path):
    recipes=[{'configuration_id':'direct','component':'supported_score','family':'prior','feature_set':'opponent_enhanced','parameters':{},'reference_role':None},
             {'configuration_id':'state','component':'minute_state','family':'rolling','feature_set':'rolling_exact','parameters':{},'reference_role':'rolling20'},
             {'configuration_id':'minutes','component':'minutes','family':'rolling','feature_set':'rolling_exact','parameters':{},'reference_role':'rolling20'}]
    dump=lambda p,v:p.write_text(json.dumps(v))
    protocol=tmp_path/'protocol.json';dump(protocol,{'primary_seed':20261028,'recipes':recipes})
    dump(tmp_path/'runtime-versions.json',{n:{'available':True,'version':version(n)}for n in inference.TRAINING_DEPENDENCIES})
    selected={'seed':20261028,'components':{'supported_score':'direct'},'procedure':{'arm':'direct_supported'},
        'protocol_sha256':sha256(protocol),'private_local_path':'/not-for-package','required_configuration_ids':['direct','state','minutes']}
    upstream=tmp_path/'selected.json';dump(upstream,selected)
    selection=tmp_path/'selection.json';dump(selection,{**selected,'frozen_at':'2026-07-10T00:00:00Z','configuration_ids':['direct','state','minutes'],
        'selection_file':str(upstream),'selection_sha256':sha256(upstream)})
    records=[]
    for r in recipes:
        value=np.array([.2,.1,.7])if r['component']=='minute_state'else 60. if r['component']=='minutes'else 4.
        wrapper={'recipe':r,'component':r['component'],'family':'prior','feature_count':1,'feature_columns':['x'],
                 'seed':20261028,'global_prior':value,'by_position':{p:value for p in models.POSITIONS}}
        path=tmp_path/(r['configuration_id']+'.joblib');joblib.dump(wrapper,path)
        records.append({'complete':True,'status':'ok','purpose':'latest-export','training_cutoff_season':'2026-27','fit_origin_utc':'2026-07-11T00:00:00Z',
            'configuration_id':r['configuration_id'],'component':r['component'],
            'seed':20261028,'recipe':r,'feature_columns':['x'],'model_file':str(path),'model_sha256':sha256(path),
            'training_index_sha256':'a'*64,'training_max_available_at':'2026-07-01T00:00:00Z',
            'selection_sha256':sha256(selection),'protocol_sha256':sha256(protocol),
            'indices_file':str(path),'indices_sha256':sha256(path),'specification_file':str(protocol),'specification_sha256':sha256(protocol),
            'worker_file':str(protocol),'worker_sha256':sha256(protocol)})
    record_file=tmp_path/'records.json';dump(record_file,records)
    return protocol,selection,record_file,records


def test_export_reuses_only_bound_models_and_relocated_inference_has_exact_values(export_fixture,tmp_path):
    protocol,selection,records,raw=export_fixture;out=tmp_path/'relocated-package'
    manifest=export_module().export(STAGE,protocol,selection,records,out)
    assert len(manifest['models'])==3 and manifest['physical_fits']==0 and not manifest['release_validation_complete']
    assert 'private_local_path'not in manifest['selection']
    assert all(not Path(r['model_file']).is_absolute()for r in manifest['models'])
    with pytest.raises(ValueError,match='outside supplied'):inference.verify_package(out)
    o=cases();o.to_parquet(tmp_path/'cases.parquet',index=False)
    code="from pathlib import Path;import pandas as pd;from fpl_forecast.inference import verify_package,apply_saved;root=Path(__import__('sys').argv[1]);m=verify_package(root);o=pd.read_parquet(root.parent/'cases.parquet');r=apply_saved(root,m,o,{n:pd.DataFrame({'x':[0.]})for n in ['opponent_enhanced','rolling_exact']});assert r.prediction.tolist()==[4.] and r.expected_minutes.tolist()==[60.] and r.p_appear.tolist()==[.8]"
    child=package_child(out,['-c',code,str(out)]);assert child.returncode==0,child.stderr
    # Binding is enforced before model loading, including after relocation.
    selected=out/manifest['models'][0]['model_file'];selected.write_bytes(selected.read_bytes()+b'changed')
    child=verify_child(out);assert child.returncode!=0 and 'hash'in child.stderr


def test_export_rejects_missing_models_selection_and_seed_drift(export_fixture,tmp_path):
    protocol,selection,records,raw=export_fixture;export=export_module().export
    for mutation,match in [('missing','Missing'),('seed','primary'),('selection','different')]:
        changed=copy.deepcopy(raw)
        if mutation=='missing':changed.pop()
        elif mutation=='seed':changed[0]['seed']+=1
        else:changed[0]['selection_sha256']='b'*64
        records.write_text(json.dumps(changed))
        with pytest.raises(ValueError,match=match):export(STAGE,protocol,selection,records,tmp_path/mutation)


def test_export_validates_underlying_selection_even_when_final_lock_contains_components(export_fixture,tmp_path):
    protocol,selection,records,raw=export_fixture;lock=json.loads(selection.read_text())
    lock['components']={'supported_score':'state'};selection.write_text(json.dumps(lock))
    with pytest.raises(ValueError,match='underlying'):export_module().export(STAGE,protocol,selection,records,tmp_path/'wrong-lock')


def test_bound_actual_model_offline_fixture_checks_full_output_and_refuses_tampering(export_fixture,tmp_path):
    protocol,selection,records,raw=export_fixture;root=tmp_path/'model'
    package=export_module().export(STAGE,protocol,selection,records,root)
    o=pd.concat([cases()]*4,ignore_index=True);o['observation_id']=['a','b','c','d'];o['fpl_position']=['GKP','DEF','MID','FWD']
    features={name:pd.DataFrame({'x':[0.]*4})for name in ['base','opponent_enhanced','rolling_exact']}
    registry=inference.load_models(package['models'],root,package['primary_seed'])
    expected=inference.make_predictors(registry,package['selection'],package['recipes'],[package['default_variant']],False)[package['default_variant']](o,features);source=tmp_path/'lab-replay';source.mkdir()
    lab_protocol={'selection_sha256':package['selection_sha256'],'models':package['models']}
    (source/'protocol.json').write_text(json.dumps(lab_protocol))
    for name,frame in {'origins':o,'components':expected,'defensive-prior':pd.DataFrame({'defensive_hit_prior':[0.,.2,.3,.1]}),**features}.items():
        frame.to_parquet(source/('GW07-'+name+'.parquet'),index=False)
    summary={'complete':True,'protocol_sha256':sha256(source/'protocol.json'),'files':{p.name:sha256(p)for p in source.glob('*.parquet')}}
    (source/'summary.json').write_text(json.dumps(summary))
    fixture=script_module('add_offline_fixture').add(root,source)
    assert fixture['rows']==4 and not fixture['contains_outcome_labels']and not fixture['contains_owned_squad']
    child=package_child(root,['-m','fpl_forecast.offline_check','--package-root',str(root)])
    assert child.returncode==0,child.stderr
    receipt=json.loads(child.stdout);assert receipt['complete']and receipt['network_requests']==0 and receipt['physical_fits']==0
    assert all(r['maximum_absolute_difference']==0 for r in receipt['checks'])
    file=root/'verification/at-origin-v1/expected.parquet';file.write_bytes(file.read_bytes()+b'changed')
    child=package_child(root,['-m','fpl_forecast.offline_check','--package-root',str(root)]);assert child.returncode!=0 and 'hash'in child.stderr


def test_aggregation_keeps_double_gameweek_counts_blanks_and_unavailable_exact_total():
    players=pd.DataFrame([{'fpl_element_id':1,'player_id':'p','fpl_code':'1','name':'P','team':'A','fpl_position':'MID',
                          'price_million_gbp':5.,'snapshot_status':'a','chance_of_playing_next_round':None,'can_select':True}])
    rows=pd.DataFrame({'fpl_element_id':[1,1],'gameweek':[10,10],'supported_score':[2.,3.],
        'approximate_current_rule_expected_total':[2.2,3.2],'current_rule_expected_total':[np.nan,np.nan],
        'approximate_current_rule_expected_total_status_diagnostic':[1.1,1.6],
        'expected_minutes':[60.,70.],'p_appear':[.8,.9],'p60':[.6,.7],'defensive_hit_prior':[.1,.1]})
    gw,horizon=inference.aggregate(rows,players,[10,11]);assert gw.fixture_count.tolist()==[2,0]
    assert gw.supported_score.tolist()==[5.,0.]
    np.testing.assert_allclose(gw.expected_appearances,[1.7,0.])
    assert gw.current_rule_expected_total.isna().all()and horizon.current_rule_expected_total.isna().all()


def package_child(root,args):
    env=os.environ.copy();env['PYTHONPATH']=str(root/'src');env.pop('LAB_RESULT_PATH',None)
    return subprocess.run([sys.executable,*args],cwd=root,env=env,capture_output=True,text=True,timeout=30)


def verify_child(root):
    return package_child(root,['-c',"from fpl_forecast.inference import verify_package;import sys;verify_package(sys.argv[1])",str(root)])


@pytest.mark.parametrize('change,match',[('code_removed','inventory'),('code_extra','inventory'),('dependency_removed','inventory'),('provenance','hash'),('training','hash')])
def test_runtime_fail_closed_inventories_and_provenance(export_fixture,tmp_path,change,match):
    protocol,selection,records,_=export_fixture;out=tmp_path/'checked-package'
    manifest=export_module().export(STAGE,protocol,selection,records,out)
    if change=='code_removed':manifest['code_files'].pop('src/fpl_forecast/fetch.py')
    elif change=='code_extra':(out/'src/fpl_forecast/unbound.py').write_text('VALUE=1\n')
    elif change=='dependency_removed':manifest['runtime_versions'].pop('numpy')
    elif change=='provenance':(out/'stage-provenance.json').write_text('{}')
    else:(out/'training-runtime-versions.json').write_text('{}')
    (out/'manifest.json').write_text(json.dumps(manifest))
    child=verify_child(out);assert child.returncode!=0 and match in child.stderr,child.stderr


def test_export_rejects_training_runtime_drift_before_copy(export_fixture,tmp_path):
    protocol,selection,records,_=export_fixture;path=protocol.parent/'runtime-versions.json';r=json.loads(path.read_text());r['numpy']['version']='0.0';path.write_text(json.dumps(r))
    with pytest.raises(ValueError,match='frozen training'):export_module().export(STAGE,protocol,selection,records,tmp_path/'bad-runtime')
    assert not(tmp_path/'bad-runtime').exists()
