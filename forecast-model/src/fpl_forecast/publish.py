"""Prepare bounded plaintext results for a manually triggered repository commit.

This module performs local file writes only. The separately reviewed GitHub
workflow owns its explicit commit; the forecast runtime never publishes itself.
"""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil

from .utils import atomic_json,bound_file,read_json,sha256,utc

FILES={'forecasts.json':'forecasts.json','gameweek-forecasts.csv':'gameweek-forecasts.csv',
       'six-gameweek-forecasts.csv':'six-gameweek-forecasts.csv',
       'fresh-process-replay.json':'fresh-process-replay.json'}
MAX_BYTES=16*1024*1024


def prepare_publication(package_root,forecast_dir,destination,now=None):
    root,source,dest=map(lambda p:Path(p).resolve(),(package_root,forecast_dir,destination))
    if dest!=root/'latest':raise ValueError('Publication destination must be package_root/latest')
    if dest.is_symlink()or source==dest or source in dest.parents or dest in source.parents:raise ValueError('Publication cannot overlap the run output')
    package=read_json(root/'manifest.json');result=read_json(source/'manifest.json')
    if package.get('schema')!='portable-fpl-qualification-v2'or package.get('complete')is not True:
        raise ValueError('Finalized package required')
    if package.get('release_validation_complete')is not True:raise ValueError('Package release validation is not complete')
    if package.get('supported_season')!='2026-27':raise ValueError('Unsupported publication season')
    if result.get('complete')is not True or result.get('physical_fits')!=0 or result.get('model_updates')!=0:
        raise ValueError('Only successful fixed-model forecasts can be published')
    if result['package_manifest_sha256']!=sha256(root/'manifest.json')or result['selection_sha256']!=package['selection_sha256']:
        raise ValueError('Forecast belongs to a different package/selection')
    if result['default_variant']!=package['default_variant']:raise ValueError('Forecast recipe changed')
    clock=utc(now or datetime.now(timezone.utc));origin=utc(result['origin_utc']);deadline=utc(result['target_deadline_utc'])
    age=(clock-origin).total_seconds()
    if age<0 or age>7200:raise ValueError('Forecast origin is outside the two-hour publication freshness limit')
    if origin>=deadline or (deadline-clock).total_seconds()<300:raise ValueError('Forecast deadline needs at least five minutes of publication margin')
    weeks=result['gameweeks']
    if not 1<=len(weeks)<=6 or weeks!=list(range(int(result['target_gameweek']),int(result['target_gameweek'])+len(weeks))):
        raise ValueError('Invalid consecutive forecast gameweeks')
    replay=result.get('fresh_process_replay',{})
    if replay.get('complete')is not True or not replay.get('checks')or not all(c.get('exactly_equal')is True for c in replay['checks']):
        raise ValueError('Exact fresh-process replay is incomplete')
    paths={name:bound_file(source,name,result['files'][name])for name in FILES}
    if read_json(paths['fresh-process-replay.json'])!=replay:raise ValueError('Replay receipt differs from result manifest')
    human=read_json(paths['forecasts.json'])
    for key in ['complete','origin_utc','target_gameweek','target_deadline_utc','package_manifest_sha256','selection_sha256']:
        if human['metadata'][key]!=result[key]:raise ValueError('Plaintext forecast metadata differs: '+key)
    if sum(p.stat().st_size for p in paths.values())>MAX_BYTES:raise ValueError('Published plaintext exceeds16MiB bound')
    # Only this generated subdirectory can be replaced. Refuse surprising files.
    allowed=set(FILES.values())|{'publication.json','README.md'}
    if dest.exists()and any(p.name not in allowed or not p.is_file()or p.is_symlink()for p in dest.iterdir()):
        raise ValueError('Latest directory has non-generated content; will not overwrite it')
    dest.mkdir(parents=True,exist_ok=True)
    for name,path in paths.items():shutil.copyfile(path,dest/FILES[name])
    receipt={'complete':True,'season':package['supported_season'],'origin_utc':result['origin_utc'],
        'published_at_utc':clock.isoformat(),'target_gameweek':result['target_gameweek'],'target_deadline_utc':result['target_deadline_utc'],
        'gameweeks':weeks,'default_variant':package['default_variant'],'default_points_column':package['default_points_column'],
        'package_manifest_sha256':sha256(root/'manifest.json'),'selection_sha256':package['selection_sha256'],
        'source_forecast_manifest_sha256':sha256(source/'manifest.json'),'physical_fits':0,'model_updates':0,
        'capture_manifest_sha256':result['capture_manifest_sha256'],
        'files':{name:sha256(dest/name)for name in FILES.values()},
        'published_bytes':sum(p.stat().st_size for p in paths.values()),'maximum_age_seconds_at_publish':7200,'minimum_deadline_margin_seconds':300,
        'limitations':result['limitations']}
    atomic_json(dest/'publication.json',receipt)
    (dest/'README.md').write_text(
        '# Latest on-demand forecast\n\n'
        f'Origin: {receipt["origin_utc"]}. Target: GW{receipt["target_gameweek"]}; deadline: {receipt["target_deadline_utc"]}.\n\n'
        'Generated once from fixed model weights. No background schedule or retraining. '
        'These files become stale after their target deadline; check publication.json before use.\n\n'
        'Read forecasts.json or gameweek-forecasts.csv for weekly approximate point forecasts; '
        'six-gameweek-forecasts.csv contains horizon sums. Exact current-rule totals are unavailable. '
        'Pin the repository commit when citing or loading these files.\n')
    return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--package-root',required=True);p.add_argument('--forecast-dir',required=True);p.add_argument('--destination',required=True)
    a=p.parse_args();r=prepare_publication(a.package_root,a.forecast_dir,a.destination)
    print(json.dumps({'complete':True,'published_bytes':r['published_bytes'],'target_gameweek':r['target_gameweek'],'physical_fits':0}))
