"""Publish only an owner-approved, prebuilt Compya candidate. Never builds/runs it."""
import argparse,hashlib,json,os,re,stat,subprocess,tempfile,time,urllib.parse,urllib.request,zipfile
from datetime import datetime
from pathlib import Path,PurePosixPath
REPO='gy7604/compya';PREFIX='repos/'+REPO;ISSUE=3;TITLE='COMPYA-PUBLICATION-CONTROL-v1'
ASSET='V26Macro_Windows.zip';MAX=310*1024*1024
def version(v):
    if not re.fullmatch(r'\d+\.\d+\.\d+',v):raise ValueError('Semantic version required')
    return tuple(map(int,v.split('.')))
def validate_url(url):
    u=urllib.parse.urlsplit(url)
    if len(url)>12000 or u.scheme!='https' or not u.hostname or not u.hostname.endswith('.oaiusercontent.com') or u.username or u.password or u.port not in (None,443) or u.fragment or not u.path.startswith('/files/') or re.search(r'[\s\x00-\x1f]',url):raise ValueError('Approved file reference required')
    return url
def validate_event(event,now):
    i=event.get('issue',{});c=event.get('comment',{})
    if (event.get('action')!='created' or event.get('repository',{}).get('full_name')!=REPO or event.get('sender',{}).get('login')!='gy7604' or i.get('number')!=ISSUE or i.get('title')!=TITLE or i.get('user',{}).get('login')!='gy7604' or 'pull_request' in i or c.get('user',{}).get('login')!='gy7604' or type(c.get('id')) is not int or c['id']<1 or c.get('created_at')!=c.get('updated_at')):raise ValueError('Dedicated original owner approval required')
    if 'oaiusercontent.com' in c.get('body','').lower():raise ValueError('Private file addresses are never accepted in public approval text')
    stamp=int(datetime.fromisoformat(c['created_at'].replace('Z','+00:00')).timestamp())
    if not 0<=now-stamp<=900:raise ValueError('Approval expired')
    match=re.fullmatch(r'/compya-publish approve ([A-Za-z0-9_-]{1,100}) ([a-f0-9]{40}) (\d+\.\d+\.\d+) ([a-f0-9]{64}) ([1-9][0-9]*) (sealed-v1)',c.get('body',''))
    if not match:raise ValueError('Exact candidate approval required')
    version(match[3])
    return dict(requestId=match[1],sourceSha=match[2],versionName=match[3],outerSha256=match[4],baselineReleaseId=int(match[5]),delivery=match[6],commentId=c['id'])
def inspect_bundle(path,r,destination):
    path=Path(path);out=Path(destination)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX or hashlib.sha256(path.read_bytes()).hexdigest()!=r['outerSha256']:raise ValueError('Exact approved bundle digest required')
    with zipfile.ZipFile(path) as z:
        rows=z.infolist()
        if len(rows)!=2 or {x.filename for x in rows}!={ASSET,'receipt.json'} or sum(x.file_size for x in rows)>MAX or any(stat.S_ISLNK(x.external_attr>>16) or x.flag_bits&1 for x in rows):raise ValueError('Only candidate and non-secret receipt may be handed off')
        if z.getinfo('receipt.json').file_size>30000:raise ValueError('Receipt bounds')
        receipt=json.loads(z.read('receipt.json'))
        expected=dict(schema=1,appId='compya',sourceRepository=REPO,sourceSha=r['sourceSha'],requestId=r['requestId'],mode='build',versionName=r['versionName'],buildExecuted=True,installed=False,publication='NOT_APPROVED')
        if any(receipt.get(k)!=v for k,v in expected.items()) or type(receipt.get('unitTests')) is not int or receipt['unitTests']<1:raise ValueError('Build receipt mismatch')
        target=out/ASSET
        with z.open(ASSET) as source,target.open('xb') as dest:
            import shutil;shutil.copyfileobj(source,dest)
    if target.stat().st_size!=receipt.get('zipBytes') or hashlib.sha256(target.read_bytes()).hexdigest()!=receipt.get('zipSha256'):raise ValueError('Candidate digest mismatch')
    with zipfile.ZipFile(target) as z:
        rows=z.infolist();names=set();total=0
        for item in rows:
            n=item.filename;p=PurePosixPath(n);parts=p.parts
            if '\\' in n or ':' in n or re.search(r'[\x00-\x1f]',n) or p.is_absolute() or '..' in parts or not parts or parts[0]!='V26Macro' or any(x.endswith((' ','.')) for x in parts) or stat.S_ISLNK(item.external_attr>>16) or item.flag_bits&1 or n.lower() in names:raise ValueError('Unsafe runtime archive path')
            names.add(n.lower());total+=item.file_size
            permitted=(len(parts)==2 and parts[1] in ('V26Macro.exe','UpdateHelper.exe','PriceOCR.exe','README.md','TEST_RESULTS.md')) or (len(parts)>=3 and parts[1]=='_internal') or (len(parts)==3 and parts[1]=='assets' and parts[2] in ('catcher.png','app.ico','coach_advice.png','league_next_match.png','league_round_label.png','league_play_hud.png'))
            if item.is_dir():continue
            if not permitted or any(x.startswith('.env') for x in parts) or parts[-1].lower() in {'buttons.json','rules.json','progress.json','last_settings.json','ranking_daily.json'} or parts[-1].lower().endswith(('.keystore','.jks','.sqlite','.sqlite3','.db','.log','.pem','.key')):raise ValueError('Private user data or unsupported runtime file')
        if len(rows)>30000 or total>1024*1024*1024 or not {'v26macro/v26macro.exe','v26macro/updatehelper.exe','v26macro/priceocr.exe'}<=names or z.testzip() is not None:raise ValueError('Incomplete or unbounded runtime')
        for name in ('V26Macro.exe','UpdateHelper.exe','PriceOCR.exe'):
            with z.open('V26Macro/'+name) as f:
                if f.read(2)!=b'MZ':raise ValueError('Windows executable format')
    # Publish a bounded receipt, never the general artifact URL or a private log.
    public={k:receipt[k] for k in ('schema','appId','sourceRepository','sourceSha','requestId','versionName','unitTests','zipBytes','zipSha256','authenticodeVerified','realGameVerified')}
    public.update(publication='OWNER_APPROVED',installed=False)
    (out/'build-receipt.json').write_text(json.dumps(public,indent=2)+'\n')
    (out/'V26Macro_Windows.sha256').write_text(receipt['zipSha256']+'  '+ASSET+'\n')
    return public
def gh(args,data=None):
    p=subprocess.run(['gh',*args],input=json.dumps(data) if data else None,capture_output=True,text=True,timeout=120)
    if p.returncode:raise RuntimeError('Publication step failed; inspect the draft and exact request before retrying')
    return p.stdout
def api(path,method='GET',body=None):
    if not path.startswith(PREFIX+'/'):raise ValueError('Publication repository scope')
    return json.loads(gh(['api','--method',method,path,*(['--input','-'] if body else [])],body))
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None
def receive_file_url(event,r):
    helper=Path(__file__).with_name('sealed_file_transfer.mjs')
    result=subprocess.run(['node',str(helper),'--receive-private-pipe'],
        input=json.dumps(dict(appId='compya',event=event,requestId=r['requestId'])),
        capture_output=True,text=True,encoding='utf-8',errors='strict',timeout=205)
    if result.returncode or len(result.stdout)>13000:
        raise ValueError('Private file transfer stopped; no automatic retry')
    payload=json.loads(result.stdout)
    if not isinstance(payload,dict) or set(payload)!={'url'}:
        raise ValueError('Private file transfer payload rejected')
    return validate_url(payload['url'])
def main():
    p=argparse.ArgumentParser();p.add_argument('--event',required=True);p.add_argument('--inspect-bundle');p.add_argument('--output');a=p.parse_args()
    event=json.loads(Path(a.event).read_text());r=validate_event(event,int(time.time()))
    if a.inspect_bundle:
        print(json.dumps(inspect_bundle(a.inspect_bundle,r,a.output)));return
    if os.environ.get('GITHUB_REPOSITORY')!=REPO or os.environ.get('GITHUB_ACTOR')!='gy7604' or os.environ.get('GITHUB_EVENT_NAME')!='issue_comment' or os.environ.get('GITHUB_RUN_ATTEMPT')!='1' or os.environ.get('COMPYA_PUBLICATION_ENABLED')!='true':raise ValueError('Enabled native first owner request required')
    def guard():
        validate_event(event,int(time.time()))
        c=api(PREFIX+'/issues/comments/'+str(r['commentId']))
        if c.get('user',{}).get('login')!='gy7604' or c.get('created_at')!=c.get('updated_at') or c.get('body')!=event['comment']['body'] or c.get('issue_url')!='https://api.github.com/'+PREFIX+'/issues/'+str(ISSUE):raise ValueError('Approval revoked/edited')
        latest=api(PREFIX+'/releases/latest')
        if latest.get('id')!=r['baselineReleaseId'] or latest.get('draft') or latest.get('prerelease') or version(latest['tag_name'].removeprefix('v'))>=version(r['versionName']):raise ValueError('Public baseline changed or duplicate/downgrade')
    guard();tag='v'+r['versionName']
    # Creating the tag/draft is a one-time claim: an existing tag or draft is never overwritten.
    refs=api(PREFIX+'/git/matching-refs/tags/'+tag)
    releases=[]
    for page in range(1,6):
        batch=api(PREFIX+f'/releases?per_page=100&page={page}');releases+=batch
        if len(batch)<100:break
    else:raise ValueError('Release retention review required')
    if any(x.get('ref')=='refs/tags/'+tag for x in refs) or any(x.get('tag_name')==tag for x in releases):raise ValueError('Version already claimed; manual review required')
    with tempfile.TemporaryDirectory(prefix='approved-compya-publication-') as folder:
        root=Path(folder);bundle=root/'approved.zip'
        private_file_url=receive_file_url(event,r)
        opener=urllib.request.build_opener(NoRedirect)
        with opener.open(urllib.request.Request(private_file_url,headers={'User-Agent':'OwnerApprovedCompyaPublisher/1'}),timeout=60) as response,bundle.open('xb') as out:
            size=0
            while True:
                chunk=response.read(1024*1024)
                if not chunk:break
                size+=len(chunk)
                if size>MAX:raise ValueError('Approved bundle download bounds')
                out.write(chunk)
        receipt=inspect_bundle(bundle,r,root);guard()
        release=api(PREFIX+'/releases','POST',dict(tag_name=tag,target_commitish=r['sourceSha'],name=tag,draft=True,prerelease=False,body=f"Owner-approved request {r['requestId']}\nSource: {r['sourceSha']}\nTests: {receipt['unitTests']}\nSHA256: {receipt['zipSha256']}\nAuthenticode and live game/device results: see build-receipt.json."))
        rid=release['id']
        gh(['release','upload',tag,str(root/ASSET),str(root/'build-receipt.json'),str(root/'V26Macro_Windows.sha256'),'--repo',REPO])
        draft=api(PREFIX+'/releases/'+str(rid));asset=next((x for x in draft.get('assets',[]) if x['name']==ASSET),None)
        if draft.get('draft') is not True or not asset or asset.get('size')!=receipt['zipBytes'] or asset.get('digest')!='sha256:'+receipt['zipSha256']:raise ValueError('Draft uploaded digest mismatch')
        guard();api(PREFIX+'/releases/'+str(rid),'PATCH',dict(draft=False,make_latest='true'))
        latest=api(PREFIX+'/releases/latest');asset=next((x for x in latest.get('assets',[]) if x['name']==ASSET),None)
        if latest.get('id')!=rid or not asset or asset.get('digest')!='sha256:'+receipt['zipSha256']:raise RuntimeError('Publication outcome needs review; never retry automatically')
        print(json.dumps(dict(releaseId=rid,version=tag,requestId=r['requestId'],sourceSha=r['sourceSha'],sha256=receipt['zipSha256'],installed=False,published=True)))
if __name__=='__main__':
    try:main()
    except Exception:
        import sys;sys.stderr.write('Approved Compya publication stopped. Inspect the exact request/draft; no automatic retry.\n');sys.exit(1)
