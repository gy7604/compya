// Trusted transport only. No product code, persistent keys, installation or publication.
import {constants,createHash,generateKeyPairSync,randomBytes,publicEncrypt,privateDecrypt,
  createCipheriv,createDecipheriv,createPublicKey,timingSafeEqual} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';

const OWNER='gy7604',WINDOW=180,ALGORITHM='RSA-OAEP-256+A256GCM';
const APPS=Object.freeze({
  sangil:{repo:'gy7604/HikingAndroid-Updates',issue:1,title:'SANGIL-PUBLICATION-CONTROL-v1',
    prefix:'/sangil-publish approve ',flag:'SANGIL_PUBLICATION_ENABLED'},
  compya:{repo:'gy7604/compya',issue:3,title:'COMPYA-PUBLICATION-CONTROL-v1',
    prefix:'/compya-publish approve ',flag:'COMPYA_PUBLICATION_ENABLED'}
});
const fail=()=>{throw Error('sealed_file_transfer_rejected');};
const seconds=()=>Math.floor(Date.now()/1000);
const hash=value=>createHash('sha256').update(value).digest('hex');
const fields=['schema','purpose','appId','repository','issue','requestId','approvalCommentId',
 'approvalBodySha256','publisherRunId','controllerSha','workflowSha','challengeId','keySha256','createdAt','expiresAt'];
function exact(object,names){
  if(!object||typeof object!=='object'||Array.isArray(object)||
    Object.keys(object).sort().join(',')!==[...names].sort().join(','))fail();
}
export function validateFileUrl(value){
  if(typeof value!=='string'||!value.length||value.length>12000||/[\s\x00-\x1f\x7f]/.test(value))fail();
  let url;try{url=new URL(value);}catch{fail();}
  if(url.protocol!=='https:'||!url.hostname.endsWith('.oaiusercontent.com')||
     !url.pathname.startsWith('/files/')||url.username||url.password||url.hash||
     (url.port&&url.port!=='443'))fail();
  return value;
}
function context(challenge,now){
  exact(challenge,[...fields,'publicKey']);
  for(const key of ['purpose','appId','repository','requestId','approvalBodySha256','publisherRunId',
    'controllerSha','workflowSha','challengeId','keySha256'])if(typeof challenge[key]!=='string')fail();
  const app=Object.hasOwn(APPS,challenge.appId)?APPS[challenge.appId]:null;
  if(challenge.schema!==1||challenge.purpose!=='owner-approved-publication-file'||!app||
     challenge.repository!==app.repo||challenge.issue!==app.issue||
     typeof challenge.requestId!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(challenge.requestId)||
     !Number.isSafeInteger(challenge.approvalCommentId)||challenge.approvalCommentId<1||
     !/^[a-f0-9]{64}$/.test(challenge.approvalBodySha256)||
     !/^[1-9][0-9]{0,19}$/.test(challenge.publisherRunId)||
     !/^[a-f0-9]{40}$/.test(challenge.controllerSha)||!/^[a-f0-9]{40}$/.test(challenge.workflowSha)||
     !/^[A-Za-z0-9_-]{43}$/.test(challenge.challengeId)||!/^[a-f0-9]{64}$/.test(challenge.keySha256)||
     !Number.isSafeInteger(now)||!Number.isSafeInteger(challenge.createdAt)||challenge.createdAt>now||
     challenge.expiresAt!==challenge.createdAt+WINDOW||challenge.expiresAt<=now)fail();
  return Object.fromEntries(fields.map(key=>[key,challenge[key]]));
}
function encoded(value,max){
  if(typeof value!=='string'||!value.length||value.length>max||!/^[A-Za-z0-9_-]+$/.test(value))fail();
  const bytes=Buffer.from(value,'base64url');
  if(bytes.toString('base64url')!==value)fail();
  return bytes;
}
function publicPart(challenge){
  exact(challenge.publicKey,['kty','n','e']);
  if(challenge.publicKey.kty!=='RSA'||challenge.publicKey.e!=='AQAB')fail();
  const key=createPublicKey({key:challenge.publicKey,format:'jwk'});
  const bits=key.asymmetricKeyDetails?.modulusLength;
  if(!Number.isInteger(bits)||bits<3072||bits>4096||
     hash(key.export({format:'der',type:'spki'}))!==challenge.keySha256)fail();
  return key;
}
export function sealFileReference(url,challenge,now=seconds()){
  const aad=Buffer.from(JSON.stringify(context(challenge,now))),publicKey=publicPart(challenge);
  validateFileUrl(url);
  const key=randomBytes(32),iv=randomBytes(12),plain=Buffer.from(JSON.stringify({url}));
  try{
    const cipher=createCipheriv('aes-256-gcm',key,iv);cipher.setAAD(aad);
    const ciphertext=Buffer.concat([cipher.update(plain),cipher.final(),cipher.getAuthTag()]);
    const wrappedKey=publicEncrypt({key:publicKey,padding:constants.RSA_PKCS1_OAEP_PADDING,
      oaepHash:'sha256',oaepLabel:Buffer.from('gy7604-owner-publication-v1')},key);
    return {schema:1,algorithm:ALGORITHM,aad:aad.toString('base64url'),wrappedKey:wrappedKey.toString('base64url'),
      iv:iv.toString('base64url'),ciphertext:ciphertext.toString('base64url')};
  }finally{key.fill(0);plain.fill(0);}
}
export function openFileReference(envelope,privateKey,challenge,now=seconds()){
  let key,plain;
  try{
    exact(envelope,['schema','algorithm','aad','wrappedKey','iv','ciphertext']);
    if(envelope.schema!==1||envelope.algorithm!==ALGORITHM)fail();
    const expected=Buffer.from(JSON.stringify(context(challenge,now))),aad=encoded(envelope.aad,4000);
    if(aad.length!==expected.length||!timingSafeEqual(aad,expected))fail();
    const iv=encoded(envelope.iv,16),ciphertext=encoded(envelope.ciphertext,18000);
    if(iv.length!==12||ciphertext.length<17||ciphertext.length>12500)fail();
    key=privateDecrypt({key:privateKey,padding:constants.RSA_PKCS1_OAEP_PADDING,
      oaepHash:'sha256',oaepLabel:Buffer.from('gy7604-owner-publication-v1')},encoded(envelope.wrappedKey,700));
    if(key.length!==32)fail();
    const decipher=createDecipheriv('aes-256-gcm',key,iv);decipher.setAAD(aad);
    decipher.setAuthTag(ciphertext.subarray(-16));
    plain=Buffer.concat([decipher.update(ciphertext.subarray(0,-16)),decipher.final()]);
    const payload=JSON.parse(plain.toString('utf8'));exact(payload,['url']);
    return validateFileUrl(payload.url);
  }catch{fail();}finally{key?.fill(0);plain?.fill(0);}
}
function originalEvent(appId,event,requestId,now){
  const app=APPS[appId],issue=event?.issue,comment=event?.comment;
  const created=Date.parse(comment?.created_at)/1000;
  if(!app||event.action!=='created'||event.repository?.full_name!==app.repo||
     event.sender?.login!==OWNER||issue?.number!==app.issue||issue.title!==app.title||
     issue.user?.login!==OWNER||Object.hasOwn(issue,'pull_request')||comment?.user?.login!==OWNER||
     !Number.isSafeInteger(comment.id)||comment.id<1||comment.created_at!==comment.updated_at||
     !Number.isSafeInteger(created)||now-created<0||now-created>900||
     typeof comment.body!=='string'||comment.body.length>16000||
     !comment.body.startsWith(app.prefix+requestId+' ')||
     !comment.body.split('\n')[0].endsWith(' sealed-v1')||
     typeof requestId!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(requestId))fail();
  return app;
}
function call(executable,args,input){
  const result=spawnSync(executable,args,{input,encoding:'utf8',timeout:30000,maxBuffer:2*1024*1024,windowsHide:true});
  if(result.error||result.status!==0)fail();
  return result.stdout.trim();
}
function nativeApi(app){
  const prefix='repos/'+app.repo+'/issues/';
  return (method,path,body)=>{
    const permitted=(method==='GET'&&path.startsWith(prefix+'comments/'))||
      (method==='GET'&&path.startsWith(prefix+app.issue+'/comments?'))||
      (method==='POST'&&path===prefix+app.issue+'/comments');
    if(!permitted||path.includes('..'))fail();
    return JSON.parse(call('gh',['api','--method',method,path,...(body?['--input','-']:[])],
      body?JSON.stringify(body):undefined));
  };
}
export function validateChallengeComment(comment,expected,now=seconds()){
  if(comment?.user?.login!=='github-actions[bot]'||comment.user.type!=='Bot'||
     comment.created_at!==comment.updated_at||typeof comment.body!=='string')fail();
  const prefix='/publication-file-challenge '+expected.appId+' '+expected.requestId+' ';
  if(!comment.body.startsWith(prefix)||comment.body.length>6000)fail();
  const challenge=JSON.parse(comment.body.slice(prefix.length));context(challenge,now);publicPart(challenge);
  for(const key of ['appId','requestId','repository','issue','approvalCommentId','approvalBodySha256',
    'publisherRunId','controllerSha','workflowSha'])if(challenge[key]!==expected[key])fail();
  if(comment.issue_url!=='https://api.github.com/repos/'+challenge.repository+'/issues/'+challenge.issue)fail();
  return challenge;
}
export async function receiveFileReference(appId,event,requestId,options={}){
  const now=options.now||seconds,env=options.environment||process.env;
  const app=originalEvent(appId,event,requestId,now()),comment=event.comment;
  if(env.GITHUB_REPOSITORY!==app.repo||env.GITHUB_ACTOR!==OWNER||env.GITHUB_EVENT_NAME!=='issue_comment'||
     env.GITHUB_RUN_ATTEMPT!=='1'||env[app.flag]!=='true'||
     !/^[1-9][0-9]{0,19}$/.test(env.GITHUB_RUN_ID||'')||
     !/^[a-f0-9]{40}$/.test(env.PUBLISHER_CONTROLLER_SHA||'')||!/^[a-f0-9]{40}$/.test(env.GITHUB_SHA||''))fail();
  const verifyController=options.verifyController||(()=>call('git',['rev-parse','HEAD'])===env.PUBLISHER_CONTROLLER_SHA);
  if(!verifyController())fail();
  const api=options.api||nativeApi(app),pause=options.pause||delay;
  const issuePath='repos/'+app.repo+'/issues/'+app.issue;
  const guard=()=>{
    originalEvent(appId,event,requestId,now());
    const current=api('GET','repos/'+app.repo+'/issues/comments/'+comment.id);
    if(current.user?.login!==OWNER||current.body!==comment.body||
       current.created_at!==comment.created_at||current.updated_at!==current.created_at||
       current.issue_url!=='https://api.github.com/'+issuePath)fail();
  };
  guard();
  const pair=(options.makeKeys||(()=>generateKeyPairSync('rsa',{modulusLength:3072})))();
  const created=now();
  const challenge={schema:1,purpose:'owner-approved-publication-file',appId,repository:app.repo,issue:app.issue,
    requestId,approvalCommentId:comment.id,approvalBodySha256:hash(comment.body),publisherRunId:env.GITHUB_RUN_ID,
    controllerSha:env.PUBLISHER_CONTROLLER_SHA,workflowSha:env.GITHUB_SHA,
    challengeId:randomBytes(32).toString('base64url'),keySha256:hash(pair.publicKey.export({format:'der',type:'spki'})),
    createdAt:created,expiresAt:created+WINDOW,publicKey:pair.publicKey.export({format:'jwk'})};
  context(challenge,created);publicPart(challenge);
  api('POST',issuePath+'/comments',{body:'/publication-file-challenge '+appId+' '+requestId+' '+JSON.stringify(challenge)});
  for(let count=0;count<36&&now()<challenge.expiresAt;count++){
    guard();
    const rows=api('GET',issuePath+'/comments?per_page=100&since='+encodeURIComponent(new Date((created-1)*1000).toISOString()));
    if(!Array.isArray(rows)||rows.length>=100)fail();
    const prefix='/publication-file-deliver '+appId+' '+requestId+' '+env.GITHUB_RUN_ID+' ';
    const matching=rows.filter(c=>c.user?.login===OWNER&&typeof c.body==='string'&&c.body.startsWith(prefix));
    if(matching.length>1)fail();
    if(matching.length===1){
      const item=matching[0],stamp=Date.parse(item.created_at)/1000;
      if(item.created_at!==item.updated_at||!Number.isSafeInteger(stamp)||stamp<created||stamp>now()||
         stamp>=challenge.expiresAt||item.issue_url!=='https://api.github.com/'+issuePath||item.body.length>24000)fail();
      const url=openFileReference(JSON.parse(item.body.slice(prefix.length)),pair.privateKey,challenge,now());
      guard();return url;
    }
    await pause(5000);
  }
  fail();
}
async function cli(){
  if(process.argv[2]!=='--receive-private-pipe'||process.stdin.isTTY||process.stdout.isTTY)fail();
  const bytes=readFileSync(0);if(bytes.length>128*1024)fail();
  try{
    const input=JSON.parse(bytes.toString('utf8'));exact(input,['appId','event','requestId']);
    const url=await receiveFileReference(input.appId,input.event,input.requestId);
    process.stdout.write(JSON.stringify({url}));
  }finally{bytes.fill(0);}
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  cli().catch(()=>{process.stderr.write('Sealed file transfer stopped; no automatic retry.\n');process.exitCode=1;});
}
