import importlib.util
from pathlib import Path
import subprocess

import pytest

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/commit_latest.py'

def module():
    spec=importlib.util.spec_from_file_location('commit_latest',SCRIPT);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def git(root,*args):
    return subprocess.run(['git',*args],cwd=root,text=True,capture_output=True,check=True).stdout.strip()

@pytest.fixture
def repositories(tmp_path):
    bare=tmp_path/'remote.git';git(tmp_path,'init','--bare',str(bare));work=tmp_path/'work';work.mkdir();git(work,'init','-b','main')
    git(work,'config','user.name','Synthetic test');git(work,'config','user.email','synthetic@example.invalid')
    (work/'forecast-model').mkdir();(work/'forecast-model/manifest.json').write_text('{"bound":true}')
    (work/'data.txt').write_text('initial');git(work,'add','.');git(work,'commit','-m','base');git(work,'remote','add','origin',str(bare));git(work,'push','-u','origin','main')
    other=tmp_path/'collector';git(tmp_path,'clone','-b','main',str(bare),str(other));git(other,'config','user.name','Synthetic collector');git(other,'config','user.email','synthetic@example.invalid')
    latest=work/'forecast-model/latest';latest.mkdir();(latest/'publication.json').write_text('{"new":true}')
    return work,other,bare

def test_unrelated_collector_update_is_rebased_before_normal_push(repositories,monkeypatch):
    work,other,bare=repositories;m=module();calls=[]
    monkeypatch.setattr(m,'validate_publication',lambda root:calls.append(root))
    (other/'data.txt').write_text('collector update');git(other,'add','data.txt');git(other,'commit','-m','hourly collector');git(other,'push','origin','main')
    receipt=m.commit_latest(work,'forecast-model','main')
    assert receipt['complete']and receipt['push_attempts']==1 and len(calls)==2
    assert(work/'data.txt').read_text()=='collector update'
    assert git(bare,'show','main:forecast-model/latest/publication.json')=='{"new":true}'
    assert git(work,'show','--format=','--name-only','HEAD')=='forecast-model/latest/publication.json'

def test_changed_remote_package_stops_without_overwriting_remote(repositories,monkeypatch):
    work,other,bare=repositories;m=module();monkeypatch.setattr(m,'validate_publication',lambda root:None)
    (other/'forecast-model/manifest.json').write_text('{"changed":true}');git(other,'add','.');git(other,'commit','-m','changed model');git(other,'push','origin','main')
    expected=git(bare,'rev-parse','main')
    with pytest.raises(ValueError,match='Remote model package changed'):m.commit_latest(work,'forecast-model','main')
    assert git(bare,'rev-parse','main')==expected
    assert git(bare,'ls-tree','--name-only','main','forecast-model/latest')==''

def test_non_generated_staged_change_is_never_committed(repositories,monkeypatch):
    work,other,bare=repositories;m=module();monkeypatch.setattr(m,'validate_publication',lambda root:None)
    expected=git(work,'rev-parse','HEAD');(work/'data.txt').write_text('unrelated');git(work,'add','data.txt')
    with pytest.raises(ValueError,match='Only generated'):m.commit_latest(work,'forecast-model','main')
    assert git(work,'rev-parse','HEAD')==expected
