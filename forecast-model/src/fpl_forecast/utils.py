"""Small local artifact helpers; paths are resolved against an explicit root."""
import hashlib
import json
from pathlib import Path
import pandas as pd

POSITIONS = {1:'GKP',2:'DEF',3:'MID',4:'FWD'}


def sha256(path):
    digest=hashlib.sha256()
    with open(path,'rb') as stream:
        for block in iter(lambda:stream.read(8*1024**2),b''):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    temporary.replace(path)


def utc(value):
    stamp=pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError('Explicit timezone-aware timestamp required')
    return stamp.tz_convert('UTC')


def bound_file(root,relative,expected):
    root=Path(root).resolve();path=(root/relative).resolve()
    if root not in path.parents:
        raise ValueError('Artifact path must stay inside its package or capture')
    if sha256(path)!=expected:
        raise ValueError('Bound artifact hash changed: '+relative)
    return path

