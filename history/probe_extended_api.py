"""Bounded public endpoint probes; preserve responses without assuming old IDs work."""
import datetime
import hashlib
import json
import pathlib
import time
import urllib.error
import urllib.request


def run(root):
    root.mkdir(parents=True,exist_ok=True);rows=[]
    endpoints={'cech_code':'https://fantasy.premierleague.com/api/element-summary/11334/',
        'player1_previous_season':'https://fantasy.premierleague.com/api/element-summary/1/?season=2014-15',
        'player1_regular':'https://fantasy.premierleague.com/api/element-summary/1/',
        'legacy_player':'https://fantasy.premierleague.com/web/api/elements/11334/',
        'understat_epl':'https://understat.com/getLeagueData/EPL/2014',
        'understat_api_epl':'https://understat.com/api/getLeagueData/EPL/2014'}
    for name,url in endpoints.items():
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'FPL-historical-research/3.0'}),timeout=30) as response:
                body=response.read();status=response.status
        except urllib.error.HTTPError as exc:body=exc.read();status=exc.code
        except Exception as exc:rows.append({'name':name,'url':url,'error':str(exc)});continue
        (root/(name+'.body')).write_bytes(body)
        row={'name':name,'url':url,'status':status,'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest(),
             'captured_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
        try:
            value=json.loads(body)
            if isinstance(value,dict):row.update(keys=list(value),history_past_rows=len(value.get('history_past',[])),first_history=value.get('history',[])[:1])
        except (ValueError,UnicodeDecodeError):pass
        rows.append(row);time.sleep(.35)
    (root/'probes.json').write_text(json.dumps(rows,indent=2)+'\n')


if __name__=='__main__':run(pathlib.Path('history/extended/work/api-probes'))
