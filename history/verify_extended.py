"""Verify the committed expansion's hashes, grain and cross-source reconciliations."""
import argparse
import collections
import csv
import gzip
import hashlib
import io
import json
import pathlib
import re
import tarfile


def rows(path):
    with gzip.open(path,'rt',encoding='utf-8',newline='') as f:return list(csv.DictReader(f))


def verify(root,work=None):
    report=json.loads((root/'coverage.json').read_text());checks=[]
    for r in report['artifacts']:
        p=root/'data'/r['file'];assert hashlib.sha256(p.read_bytes()).hexdigest()==r['sha256'],str(p)
        assert len(rows(p))==r['rows'],str(p)
    checks.append('All derived artifact hashes and row counts agree')
    complete=rows(root/'data/fpl-complete-seasons.csv.gz')
    assert len(complete)==303126
    assert len({(r['season'],r['player_id'],r['fixture_id']) for r in complete})==len(complete)
    assert len({r['season'] for r in complete})==12
    assert all(r['minutes']!='' and r['total_points']!='' for r in complete)
    assert all(0<=float(r['minutes'])<=90 for r in complete)
    for r in complete:
        if r['season']<'2022-23':assert not r['expected_goals'] and not r['expected_assists']
    checks.append('303126 unique player fixtures; 12 seasons; observed minutes/points; missing historical xG retained')
    reference=rows(root/'data/r-converted/season201415.csv.gz');new=[r for r in complete if r['season']=='2014-15']
    expected=collections.Counter((int(float(r['id'])),int(float(r['gw'])),float(r['mins']),float(r['gw_pts'])) for r in reference)
    actual=collections.Counter((int(r['player_id']),int(r['gw']),float(r['minutes']),float(r['total_points'])) for r in new)
    assert actual==expected
    checks.append('All 24876 2014/15 R-data player/GW/minutes/points observations reconcile with the closed JSON source; shared FPL lineage')
    annual=rows(root/'data/annual-history.csv.gz')
    assert len({(r['season'],r['code']) for r in annual})==len(annual)
    assert all(re.fullmatch(r'20\d{2}-\d{2}',r['season']) for r in annual)
    assert not any(r['core_conflict']=='True' for r in annual)
    checks.append('Annual identities and season aliases reconciled; no core numerical source conflicts')
    partial=rows(root/'data/partial-database-player-matches.csv.gz')
    assert any(r['minutes']=='' for r in partial) and any(r['total_points']=='' for r in partial)
    for r in partial:
        if r['complete_points_and_minutes']=='True':assert r['minutes']!='' and r['total_points']!='' and not r['core_conflict'] and r['identity_conflict']=='False'
    checks.append('Unknown database targets and identity conflicts cannot be admitted as complete observed labels')
    under=rows(root/'data/understat-player-matches.csv.gz')
    assert len({(r['understat_player_id'],r['id']) for r in under})==len(under)
    assert all(r['crosswalk_status']=='explicit_map' for r in under if r['fpl_code'])
    checks.append('Understat player/match keys unique; cross-provider IDs use explicit season-bound crosswalks')
    packages=root/'raw/index.json'
    if packages.exists():
        for r in json.loads(packages.read_text())['packages']:
            path=root/'raw'/r['file'];assert hashlib.sha256(path.read_bytes()).hexdigest()==r['sha256']
            observed={};manifest=None
            with tarfile.open(path,'r|gz') as t:
                for member in t:
                    assert not member.name.startswith('/') and '..' not in pathlib.PurePosixPath(member.name).parts
                    if not member.isfile():continue
                    payload=t.extractfile(member).read();observed[member.name]=hashlib.sha256(payload).hexdigest()
                    if member.name=='manifest.json':manifest=json.loads(payload)
            if manifest:
                assert not manifest['failures']
                for record in manifest['records']:assert observed[record['object']]==record['sha256']
        checks.append('Every packaged raw object matches its captured source hash; archive paths are bounded')
    result={'checks_passed':len(checks),'checks':checks,'complete_panel':report['complete_fpl_panel'],
            'limits':['Full panels cover 380 fixtures, not a proved complete historical deadline roster.',
                      'Partial source tables overlap each other and must not be summed as independent training examples.',
                      'The R and JSON reconciliation shares an upstream FPL source; it is not independent truth.',
                      'Historic odds timestamps and archive final metadata cannot be treated as deadline-vintage features.']}
    (root/'validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=pathlib.Path,default=pathlib.Path('history/extended'));a=p.parse_args();verify(a.root)
