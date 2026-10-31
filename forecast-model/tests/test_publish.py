import copy
import json
from pathlib import Path

import pytest

from fpl_forecast.publish import prepare_publication,FILES
from fpl_forecast.utils import sha256


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))


@pytest.fixture
def published(tmp_path):
    root=tmp_path/'model';out=tmp_path/'output';root.mkdir();out.mkdir()
    package={'schema':'portable-fpl-qualification-v2','complete':True,'release_validation_complete':True,
        'supported_season':'2026-27','selection_sha256':'a'*64,'default_variant':'direct_supported',
        'default_points_column':'approximate_current_rule_expected_total'}
    write(root/'manifest.json',package)
    replay={'complete':True,'checks':[{'column':'prediction','exactly_equal':True}],'physical_fits':0,'replay_pid':123}
    result={'complete':True,'physical_fits':0,'model_updates':0,'package_manifest_sha256':sha256(root/'manifest.json'),
        'selection_sha256':'a'*64,'default_variant':'direct_supported','origin_utc':'2026-11-05T12:00:00Z',
        'target_deadline_utc':'2026-11-06T18:30:00Z','target_gameweek':10,'gameweeks':[10,11,12,13,14,15],
        'fresh_process_replay':replay,'capture_manifest_sha256':'b'*64,'limitations':['Approximate current rules.']}
    write(out/'fresh-process-replay.json',replay)
    write(out/'forecasts.json',{'metadata':result,'gameweeks':[],'six_gameweeks':[]})
    for name in ['gameweek-forecasts.csv','six-gameweek-forecasts.csv']:(out/name).write_text('player_id,prediction\n1,4.2\n')
    result['files']={name:sha256(out/name)for name in FILES};write(out/'manifest.json',result)
    return root,out,result,package


def test_publication_is_bounded_generated_subset_with_commit_consumable_receipt(published):
    root,out,result,package=published
    receipt=prepare_publication(root,out,root/'latest',now='2026-11-05T12:01:00Z')
    assert receipt['complete']and receipt['physical_fits']==0 and receipt['published_bytes']<16*1024*1024
    assert set(p.name for p in (root/'latest').iterdir())==set(FILES)|{'README.md','publication.json'}
    assert receipt['package_manifest_sha256']==result['package_manifest_sha256']
    for file,h in receipt['files'].items():assert sha256(root/'latest'/file)==h


@pytest.mark.parametrize('change,match',[
    ('unreviewed','release'),('stale','freshness'),('deadline','deadline'),('seed','package/selection'),
    ('incomplete','successful'),('replay','replay'),('changed_file','hash'),('unexpected_file','non-generated')])
def test_publication_fails_closed_before_replacing_existing_results(published,change,match):
    root,out,result,package=published;now='2026-11-05T12:01:00Z'
    if change=='unreviewed':package['release_validation_complete']=False;write(root/'manifest.json',package)
    elif change=='stale':now='2026-11-05T15:00:00Z'
    elif change=='deadline':result['target_deadline_utc']='2026-11-05T12:05:00Z'
    elif change=='seed':result['selection_sha256']='c'*64
    elif change=='incomplete':result['complete']=False
    elif change=='replay':result['fresh_process_replay']['checks'][0]['exactly_equal']=False
    elif change=='changed_file':(out/'gameweek-forecasts.csv').write_text('changed')
    else:(root/'latest').mkdir();(root/'latest'/'personal.txt').write_text('keep')
    write(out/'manifest.json',result)
    with pytest.raises(ValueError,match=match):prepare_publication(root,out,root/'latest',now=now)
    assert not(root/'latest'/'publication.json').exists()
    if change=='unexpected_file':assert(root/'latest'/'personal.txt').read_text()=='keep'


def test_manual_workflow_has_no_schedule_or_force_push():
    text=(Path(__file__).resolve().parents[1]/'examples/github-workflow.yml').read_text()
    assert 'workflow_dispatch:'in text and '\n  schedule:'not in text and 'cron:'not in text
    assert 'scripts/commit_latest.py'in text and '--force'not in text
    code=(Path(__file__).resolve().parents[1]/'scripts/commit_latest.py').read_text()
    assert "'push','origin','HEAD:refs/heads/'+branch"in code and "'add','--',model_directory+'/latest'"in code
