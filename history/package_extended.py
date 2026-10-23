"""Package verified raw source caches and retain lossless per-file provenance."""
import argparse
import gzip
import hashlib
import io
import json
import pathlib
import tarfile


def pack(root,dest):
    dest.parent.mkdir(parents=True,exist_ok=True)
    with dest.open('wb') as f,gzip.GzipFile(fileobj=f,mode='wb',filename='',mtime=0) as g,tarfile.open(fileobj=g,mode='w|') as t:
        for p in sorted(root.rglob('*')):
            if not p.is_file():continue
            data=p.read_bytes();member=tarfile.TarInfo(str(p.relative_to(root)));member.size=len(data);member.mtime=0;member.mode=0o644
            t.addfile(member,io.BytesIO(data))
    return {'file':dest.name,'bytes':dest.stat().st_size,'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()}


def main(work,output):
    records=[]
    for group in ('player-histories','early-archives','understat','auxiliary','api-probes'):
        records.append(dict(pack(work/group,output/(group+'.tar.gz')),group=group))
    (output/'index.json').write_text(json.dumps({'schema':'fpl-extended-packages-v1','packages':records},indent=2)+'\n')
    print(json.dumps({'packages':len(records),'compressed_bytes':sum(r['bytes'] for r in records)}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--work',type=pathlib.Path,required=True);p.add_argument('--output',type=pathlib.Path,required=True)
    a=p.parse_args();main(a.work,a.output)
