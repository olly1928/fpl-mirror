"""One bounded public FPL capture; no accounts, schedules, training or writes upstream."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError,URLError
from urllib.request import Request,urlopen

API='https://fantasy.premierleague.com/api/'


def utc(value):
    result=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('Timestamp requires timezone')
    return result.astimezone(timezone.utc)


def now():return datetime.now(timezone.utc)


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp');temp.write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+'\n')
    temp.replace(path)


class Client:
    """At most four requests/second; retries count toward the common time limit."""
    def __init__(self,max_seconds=600,timeout=20):
        self.until=time.monotonic()+max_seconds;self.timeout=timeout
        self.lock=threading.Lock();self.next_request=0.;self.bytes=0;self.requests=0

    def check_time(self):
        if time.monotonic()>=self.until:raise TimeoutError('Public data capture exceeded its time limit')

    def get(self,endpoint):
        if not re.fullmatch(r'(bootstrap-static/|fixtures/|element-summary/[0-9]+/)',endpoint):
            raise ValueError('Endpoint outside the public forecasting data allowlist')
        for attempt in range(3):
            self.check_time()
            with self.lock:
                delay=max(0.,self.next_request-time.monotonic())
                self.next_request=max(self.next_request,time.monotonic())+.25
                self.requests+=1
                if self.requests>5000:raise ValueError('Capture request limit exceeded')
            if delay:time.sleep(min(delay,max(0.,self.until-time.monotonic())))
            self.check_time()
            request=Request(API+endpoint,headers={'Accept':'application/json','User-Agent':'FPL-forecast-model/1.0 (read-only public data)'})
            try:
                with urlopen(request,timeout=min(self.timeout,max(.1,self.until-time.monotonic()))) as response:
                    if not response.geturl().startswith(API):raise ValueError('Unexpected redirect outside official FPL API')
                    body=response.read(16*1024*1024+1)
                    if len(body)>16*1024*1024:raise ValueError('Oversize official API response')
                    server_date=response.headers.get('Date')
                    if not server_date or abs((parsedate_to_datetime(server_date)-now()).total_seconds())>900:
                        raise ValueError('Official response timestamp is missing or outside fifteen-minute freshness window')
                    json.loads(body)
                    with self.lock:
                        self.bytes+=len(body)
                        if self.bytes>128*1024*1024:raise ValueError('Capture exceeds128MiB byte limit')
                    return body,server_date
            except HTTPError as exc:
                if exc.code not in {429,500,502,503,504} or attempt==2:raise
                delay=min(10.,float(exc.headers.get('Retry-After','0'))) if str(exc.headers.get('Retry-After','0')).isdigit() else 0.
            except (URLError,TimeoutError):
                if attempt==2:raise
                delay=0.
            self.check_time();time.sleep(min(max(delay,2**attempt),max(0.,self.until-time.monotonic())))
        raise RuntimeError('Unreachable capture retry state')


def _fetch(client,root,endpoint,path,role,**identity):
    started=now().isoformat();body,server_date=client.get(endpoint);retrieved=now().isoformat()
    destination=root/path;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(body)
    return {'role':role,'path':path,'sha256':hashlib.sha256(body).hexdigest(),'url':API+endpoint,
            'request_started_at':started,'retrieved_at':retrieved,'server_date':server_date,**identity},json.loads(body)


def _population(bootstrap):
    fields=('id','code','team','element_type','now_cost','status','chance_of_playing_next_round')
    return sorted(tuple(e.get(k) for k in fields) for e in bootstrap['elements'])


def _schedule(fixtures,weeks):
    return sorted((f['id'],f.get('event'),f.get('kickoff_time'),f['team_h'],f['team_a'])
                  for f in fixtures if f.get('event') in weeks)


def capture(output_dir,season,*,workers=4,max_seconds=600):
    """Write a single immutable snapshot and return its manifest; never forecast."""
    if not re.fullmatch(r'[0-9]{4}[-/][0-9]{2}',season):raise ValueError('Expected model season YYYY-YY')
    if not 1<=workers<=4 or not 30<=max_seconds<=900:raise ValueError('Use1–4 workers and30–900 capture seconds')
    root=Path(output_dir).resolve()
    if root.exists() and any(root.iterdir()):raise ValueError('Capture output must be empty; choose a new output directory')
    root.mkdir(parents=True,exist_ok=True);client=Client(max_seconds=max_seconds);records=[]
    try:
        record,bootstrap=_fetch(client,root,'bootstrap-static/','bootstrap.json','bootstrap');records.append(record)
        record,fixtures=_fetch(client,root,'fixtures/','fixtures.json','fixtures');records.append(record)
        events=bootstrap.get('events',[]);players=bootstrap.get('elements',[])
        if not events or not players:raise ValueError('Official calendar or roster is empty')
        if utc(min(events,key=lambda e:int(e['id']))['deadline_time']).year!=int(season[:4]):
            raise ValueError('Official season differs from frozen model; do not reuse season-specific identities')
        future=[e for e in events if utc(e['deadline_time'])>now()]
        if not future:raise ValueError('No future official deadline remains in the supported season')
        target=min(future,key=lambda e:int(e['id']));weeks=[int(e['id']) for e in events if int(target['id'])<=int(e['id'])<int(target['id'])+6]
        ids=[int(e['id']) for e in players]
        if len(ids)!=len(set(ids)) or len(ids)>1500:raise ValueError('Invalid or unexpectedly large official roster')
        pool=ThreadPoolExecutor(max_workers=workers)
        try:
            futures=[pool.submit(_fetch,client,root,f'element-summary/{pid}/',f'element-summary/{pid}.json','element_summary',player_id=pid) for pid in ids]
            for future in as_completed(futures):
                record,_=future.result();records.append(record);client.check_time()
        finally:pool.shutdown(wait=True,cancel_futures=True)
        record,final_bootstrap=_fetch(client,root,'bootstrap-static/','bootstrap-final.json','bootstrap_final');records.append(record)
        record,final_fixtures=_fetch(client,root,'fixtures/','fixtures-final.json','fixtures_final');records.append(record)
        if _population(bootstrap)!=_population(final_bootstrap):raise ValueError('Roster, price or availability changed during capture; run again into a new directory')
        deadlines=lambda b:{int(e['id']):e['deadline_time'] for e in b['events'] if int(e['id']) in weeks}
        if deadlines(bootstrap)!=deadlines(final_bootstrap) or _schedule(fixtures,weeks)!=_schedule(final_fixtures,weeks):
            raise ValueError('Future calendar changed during capture; run again into a new directory')
        origin=now();client.check_time()
        if origin>=utc(target['deadline_time']):raise ValueError('Capture crossed the target deadline; no prospective forecast can be issued')
        manifest={'version':'portable-fpl-capture-v1','season':season.replace('/','-'),'origin_utc':origin.isoformat(),
            'target_gameweek':int(target['id']),'target_deadline_utc':target['deadline_time'],
            'files':sorted(records,key=lambda r:r['path']),'players':len(ids),'requested_gameweeks':weeks,
            'source_observation_policy':'Public official data retrieved before the frozen origin; models use their declared historical label lag.',
            'account_data_requested':False,'request_count':client.requests,'response_bytes':client.bytes}
        write_json(root/'manifest.json',manifest);return manifest
    except BaseException as exc:
        write_json(root/'capture-failure.json',{'failed_at':now().isoformat(),'error_type':type(exc).__name__,
            'error':str(exc),'complete':False,'account_data_requested':False})
        raise
