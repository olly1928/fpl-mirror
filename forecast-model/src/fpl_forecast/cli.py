"""Explicit one-shot commands. Nothing is scheduled or installed in the background."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from .fetch import capture,write_json


def package_root(value=None):
    root=Path(value).expanduser().resolve() if value else Path(__file__).resolve().parents[2]
    manifest=root/'manifest.json'
    if not manifest.is_file():raise ValueError('Package manifest not found; pass --package-root PATH to the extracted model folder')
    config=json.loads(manifest.read_text())
    if not config.get('supported_season'):raise ValueError('Package manifest is missing supported_season')
    return root,config


def main(argv=None):
    parser=argparse.ArgumentParser(description='Run the fixed FPL model once with fresh public data, or reuse a saved capture. No training or background service.')
    sub=parser.add_subparsers(dest='command',required=True)
    for name,help_text in [('run','Fetch fresh public inputs, forecast once, and exit'),('fetch','Save one public input capture and exit'),('forecast','Forecast offline from an existing input capture')]:
        p=sub.add_parser(name,help=help_text);p.add_argument('--package-root',help='Extracted package root; defaults to this editable installation')
        p.add_argument('--output-dir',required=True,help='New output directory; existing evidence is not overwritten')
        if name in {'run','fetch'}:
            p.add_argument('--workers',type=int,default=4,help='Concurrent public GET requests (1–4; rate remains at most4/second)')
            p.add_argument('--max-capture-seconds',type=int,default=600,help='Bound public input collection (30–900 seconds)')
        else:p.add_argument('--capture-dir',required=True,help='Saved capture directory containing manifest.json')
    args=parser.parse_args(argv)
    try:
        root,config=package_root(args.package_root);out=Path(args.output_dir).expanduser().resolve()
        if out==root or out in root.parents:raise ValueError('Output cannot replace the model package or one of its parent directories')
        if out.exists() and any(out.iterdir()):raise ValueError('Output directory is not empty; choose a new run name')
        if args.command in {'run','fetch'}:
            cap=out/'capture' if args.command=='run' else out
            captured=capture(cap,config['supported_season'],workers=args.workers,max_seconds=args.max_capture_seconds)
            if args.command=='fetch':
                print(json.dumps({'complete':True,'capture_dir':str(cap),'origin_utc':captured['origin_utc'],
                    'target_gameweek':captured['target_gameweek'],'players':captured['players'],'models_run':False},indent=2));return 0
        else:cap=Path(args.capture_dir).expanduser().resolve()
        # Set native limits before importing scientific runtimes. Models stay fixed.
        for key in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS']:os.environ[key]='1'
        from .inference import forecast
        destination=out/'forecast' if args.command=='run' else out
        result=forecast(root,cap,destination)
        if not result.get('complete'):raise RuntimeError('Forecast did not produce a complete result')
        if args.command=='run':
            write_json(out/'run.json',{'complete':True,'command':'run','default_recipe':config['default_variant'],
                'package_manifest_sha256':hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest(),
                'capture_manifest':'capture/manifest.json','forecast_directory':'forecast',
                'target_gameweek':captured['target_gameweek'],'origin_utc':captured['origin_utc'],
                'new_model_fits':0,'account_data_requested':False,'background_service_installed':False})
        print(json.dumps({'complete':True,'forecast_directory':str(destination),'recipe':config['default_variant'],
            'new_model_fits':0,'background_service_installed':False},indent=2));return 0
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}',file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
