"""One-off public historical source acquisition; independent of the live feeds.

An explicit inventory pins URL, Git blob identity and expected byte size. Raw
bytes are content addressed and retained; failed requests remain in the ledger.
Only standard-library modules are needed for collection and verification.
"""
import argparse
import concurrent.futures
import datetime
import hashlib
import json
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request


def sha256(body):
    return hashlib.sha256(body).hexdigest()


def git_blob(body):
    return hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest()


def acquire(record, output):
    key = record.get('git_blob_sha') or hashlib.sha256(record['url'].encode()).hexdigest()
    path = output/'objects'/key
    meta = output/'metadata'/(key+'.json')
    if path.exists() and meta.exists():
        body = path.read_bytes(); result = json.loads(meta.read_text())
        if sha256(body) != result['sha256']:
            raise ValueError('Cached checksum mismatch: '+key)
    else:
        for attempt in range(4):
            try:
                request = urllib.request.Request(record['url'], headers={'User-Agent':'FPL-public-history-research/3.0'})
                with urllib.request.urlopen(request, timeout=40) as response:
                    body = response.read(); status = response.status
                break
            except (urllib.error.URLError, TimeoutError):
                if attempt == 3:
                    raise
                time.sleep(1+attempt)
        result = {'url':record['url'], 'status':status, 'bytes':len(body), 'sha256':sha256(body),
                  'captured_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
        path.parent.mkdir(parents=True,exist_ok=True); meta.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(body); meta.write_text(json.dumps(result,sort_keys=True)+'\n')
    if record.get('git_blob_sha') and git_blob(body) != record['git_blob_sha']:
        raise ValueError('Git blob identity mismatch: '+record['url'])
    if record.get('bytes') is not None and len(body) != record['bytes']:
        raise ValueError('Source size mismatch: '+record['url'])
    return dict(record, **{k:v for k,v in result.items() if k!='url'},
                transport_url=result['url'], object='objects/'+key)


def run(inventory, output, workers=8):
    output.mkdir(parents=True,exist_ok=True)
    records = json.loads(inventory.read_text())['files']
    unique = {}
    for row in records:
        if 'url' not in row:
            row['url'] = 'https://raw.githubusercontent.com/'+row['repository']+'/'+row['commit']+'/'+urllib.parse.quote(row['path'],safe='/')
        unique.setdefault(row.get('git_blob_sha',row['url']),row)
    outcomes = {}; failures=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(acquire,r,output):key for key,r in unique.items()}
        for i, future in enumerate(concurrent.futures.as_completed(futures),1):
            key=futures[future]
            try: outcomes[key]=future.result()
            except Exception as exc: failures.append({'record':unique[key], 'error':str(exc)})
            if i%250==0 or i==len(futures):
                print(json.dumps({'completed':i,'unique_sources':len(futures),'failures':len(failures)}),flush=True)
    expanded=[]
    for row in records:
        result=outcomes.get(row.get('git_blob_sha',row['url']))
        if result:
            fields=('status','bytes','sha256','captured_at','object','transport_url','transport','archive_member')
            expanded.append(dict(row,**{k:result[k] for k in fields if k in result}))
    manifest={'schema':'fpl-extended-raw-v1','inventory_sha256':sha256(inventory.read_bytes()),
              'requested_paths':len(records),'unique_requested':len(unique),'unique_captured':len(outcomes),
              'records':expanded,'failures':failures}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n')
    if failures: raise ValueError('Some sources failed; see manifest.json and rerun the same cache')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory',type=pathlib.Path,required=True)
    parser.add_argument('--output',type=pathlib.Path,required=True)
    parser.add_argument('--workers',type=int,default=8)
    args=parser.parse_args()
    if not 1<=args.workers<=12: parser.error('workers must be 1..12')
    run(args.inventory,args.output,args.workers)
