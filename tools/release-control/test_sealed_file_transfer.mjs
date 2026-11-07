import test from 'node:test';
import assert from 'node:assert/strict';
import {generateKeyPairSync,createHash} from 'node:crypto';
import {sealFileReference,openFileReference,validateFileUrl,receiveFileReference,validateChallengeComment} from './sealed_file_transfer.mjs';
const pair=generateKeyPairSync('rsa',{modulusLength:3072});
const digest=x=>createHash('sha256').update(x).digest('hex');
const NOW=1793664000,URL='https://synthetic.oaiusercontent.com/files/fake/raw?synthetic=true';
function challenge(overrides={}){
  return {schema:1,purpose:'owner-approved-publication-file',appId:'sangil',repository:'gy7604/HikingAndroid-Updates',
    issue:1,requestId:'fake-build',approvalCommentId:123,approvalBodySha256:'a'.repeat(64),publisherRunId:'200',
    controllerSha:'b'.repeat(40),workflowSha:'c'.repeat(40),challengeId:'d'.repeat(43),
    keySha256:digest(pair.publicKey.export({format:'der',type:'spki'})),createdAt:NOW,expiresAt:NOW+180,
    publicKey:pair.publicKey.export({format:'jwk'}),...overrides};
}
function fixture(appId='sangil'){
  const repo=appId==='sangil'?'gy7604/HikingAndroid-Updates':'gy7604/compya',issue=appId==='sangil'?1:3;
  const prefix=appId==='sangil'?'/sangil-publish approve ':'/compya-publish approve ';
  const event={action:'created',repository:{full_name:repo},sender:{login:'gy7604'},
    issue:{number:issue,title:appId==='sangil'?'SANGIL-PUBLICATION-CONTROL-v1':'COMPYA-PUBLICATION-CONTROL-v1',user:{login:'gy7604'}},
    comment:{id:123,user:{login:'gy7604'},created_at:'2026-11-03T00:00:00Z',updated_at:'2026-11-03T00:00:00Z',
      body:prefix+'fake-build '+'a'.repeat(40)+' fake-candidate sealed-v1'}};
  const env={GITHUB_REPOSITORY:repo,GITHUB_ACTOR:'gy7604',GITHUB_EVENT_NAME:'issue_comment',
    GITHUB_RUN_ATTEMPT:'1',GITHUB_RUN_ID:'200',PUBLISHER_CONTROLLER_SHA:'b'.repeat(40),GITHUB_SHA:'c'.repeat(40),
    SANGIL_PUBLICATION_ENABLED:'true',COMPYA_PUBLICATION_ENABLED:'true'};
  return {event,env,repo,issue};
}
test('round trip returns the exact URL without including plaintext in the public envelope',()=>{
  const c=challenge(),e=sealFileReference(URL,c,NOW);
  assert.equal(openFileReference(e,pair.privateKey,c,NOW),URL);
  assert.ok(!JSON.stringify(e).includes(URL));assert.ok(!JSON.stringify(e).includes('oaiusercontent'));
  assert.deepEqual(Object.keys(c.publicKey).sort(),['e','kty','n']);
});
test('existing maximum private reference length is supported',()=>{
  const url='https://synthetic.oaiusercontent.com/files/fake?data='+'x'.repeat(11940);
  assert.ok(url.length<=12000);const c=challenge(),e=sealFileReference(url,c,NOW);
  assert.equal(openFileReference(e,pair.privateKey,c,NOW),url);
});
test('each envelope has fresh independent encryption material',()=>{
  const c=challenge(),a=sealFileReference(URL,c,NOW),b=sealFileReference(URL,c,NOW);
  assert.notEqual(a.iv,b.iv);assert.notEqual(a.wrappedKey,b.wrappedKey);assert.notEqual(a.ciphertext,b.ciphertext);
});
test('all approval, candidate, run, repository and controller bindings are authenticated',()=>{
  const c=challenge(),e=sealFileReference(URL,c,NOW);
  for(const [key,value] of Object.entries({requestId:'other',approvalCommentId:124,approvalBodySha256:'f'.repeat(64),
    publisherRunId:'201',controllerSha:'e'.repeat(40),workflowSha:'e'.repeat(40),challengeId:'f'.repeat(43),
    keySha256:'e'.repeat(64),createdAt:NOW-1,expiresAt:NOW+179})){
    assert.throws(()=>openFileReference(e,pair.privateKey,{...c,[key]:value},NOW),undefined,key);
  }
  assert.throws(()=>openFileReference(e,pair.privateKey,{...c,appId:'compya',repository:'gy7604/compya',issue:3},NOW));
});
test('expired and future challenges are rejected',()=>{
  const c=challenge(),e=sealFileReference(URL,c,NOW);
  for(const now of [NOW-1,NOW+180,NOW+181])assert.throws(()=>openFileReference(e,pair.privateKey,c,now));
  assert.throws(()=>sealFileReference(URL,c,NOW+180));
});
test('payload, key, nonce, tag, algorithm and shape tampering fail closed',()=>{
  const c=challenge(),e=sealFileReference(URL,c,NOW);
  for(const key of ['aad','wrappedKey','iv','ciphertext']){
    const copy={...e,[key]:(e[key][0]==='A'?'B':'A')+e[key].slice(1)};
    assert.throws(()=>openFileReference(copy,pair.privateKey,c,NOW),undefined,key);
  }
  for(const mutation of [{algorithm:'other'},{schema:2},{extra:true},{wrappedKey:'bad='},{iv:''}]){
    assert.throws(()=>openFileReference({...e,...mutation},pair.privateKey,c,NOW));
  }
});
test('a different receiver cannot open the envelope',()=>{
  const other=generateKeyPairSync('rsa',{modulusLength:3072}),c=challenge();
  assert.throws(()=>openFileReference(sealFileReference(URL,c,NOW),other.privateKey,c,NOW));
});
test('private key fields, weak keys and false public-key fingerprints are rejected',()=>{
  const c=challenge(),weak=generateKeyPairSync('rsa',{modulusLength:2048});
  for(const change of [{publicKey:pair.privateKey.export({format:'jwk'})},{publicKey:weak.publicKey.export({format:'jwk'})},
    {publicKey:{...c.publicKey,e:'Aw'}},{keySha256:'f'.repeat(64)},{extra:true}]){
    assert.throws(()=>sealFileReference(URL,{...c,...change},NOW));
  }
});
test('unsafe origins, user information, fragments, whitespace and oversized values are rejected',()=>{
  for(const url of ['http://synthetic.oaiusercontent.com/files/x','https://evil.test/files/x',
    'https://oaiusercontent.com/files/x','https://synthetic.oaiusercontent.com/not-files/x',
    'https://synthetic.oaiusercontent.com:444/files/x','https://u:p@synthetic.oaiusercontent.com/files/x',
    URL+'#fragment',URL+'\n',URL+' '.repeat(5),'x'.repeat(12001),null,10]){
    assert.throws(()=>validateFileUrl(url));
  }
});
test('challenge comments require the native bot, immutable body and trusted run metadata',()=>{
  const c=challenge(),expected=Object.fromEntries(['appId','requestId','repository','issue','approvalCommentId',
    'approvalBodySha256','publisherRunId','controllerSha','workflowSha'].map(k=>[k,c[k]]));
  const comment={user:{login:'github-actions[bot]',type:'Bot'},created_at:'same',updated_at:'same',
    issue_url:'https://api.github.com/repos/'+c.repository+'/issues/1',
    body:'/publication-file-challenge sangil fake-build '+JSON.stringify(c)};
  assert.deepEqual(validateChallengeComment(comment,expected,NOW),c);
  for(const changed of [{user:{login:'stranger',type:'User'}},{updated_at:'edited'},{issue_url:'wrong'}]){
    assert.throws(()=>validateChallengeComment({...comment,...changed},expected,NOW));
  }
  assert.throws(()=>validateChallengeComment(comment,{...expected,publisherRunId:'201'},NOW));
});
async function simulate(options={}){
  const f=fixture(options.appId),calls=[];let publicChallenge,clock=NOW;
  const original={...f.event.comment,issue_url:'https://api.github.com/repos/'+f.repo+'/issues/'+f.issue};
  const api=(method,path,body)=>{
    calls.push({method,path,body});
    if(method==='GET'&&path.includes('/issues/comments/'))return options.changedApproval?{...original,body:'revoked'}:original;
    if(method==='POST'){
      publicChallenge=JSON.parse(body.body.slice(body.body.indexOf('{')));
      return {id:500};
    }
    if(options.empty)return [];
    const envelope=sealFileReference(URL,publicChallenge,clock);
    const item={id:600,user:{login:'gy7604'},created_at:f.event.comment.created_at,updated_at:f.event.comment.updated_at,
      issue_url:original.issue_url,body:'/publication-file-deliver '+(options.appId||'sangil')+' fake-build 200 '+JSON.stringify(envelope)};
    if(options.mutateDelivery)options.mutateDelivery(item);
    return options.duplicate?[item,{...item,id:601}]:[item];
  };
  const value=await receiveFileReference(options.appId||'sangil',f.event,'fake-build',{
    environment:{...f.env,...options.environment},now:()=>clock,api,verifyController:()=>!options.wrongController,
    makeKeys:()=>pair,pause:async()=>{clock+=5;}
  });
  return {value,calls,publicChallenge};
}
test('both app transports work with injected synthetic APIs only',async()=>{
  for(const appId of ['sangil','compya']){
    const r=await simulate({appId});assert.equal(r.value,URL);
    assert.equal(r.calls.filter(c=>c.method==='POST').length,1);
    assert.ok(!JSON.stringify(r.calls).includes(URL));
    assert.ok(r.calls.every(c=>!c.path.includes('/releases')&&!c.path.includes('/git/')));
  }
});
test('native first owner run and enabled app are required before any challenge',async()=>{
  for(const environment of [{GITHUB_ACTOR:'stranger'},{GITHUB_RUN_ATTEMPT:'2'},
    {GITHUB_REPOSITORY:'other/repo'},{GITHUB_EVENT_NAME:'push'},{SANGIL_PUBLICATION_ENABLED:'false'},
    {PUBLISHER_CONTROLLER_SHA:'bad'},{GITHUB_RUN_ID:'0'}]){
    await assert.rejects(simulate({environment}));
  }
  await assert.rejects(simulate({wrongController:true}));
});
test('revoked approval, duplicate delivery and edited delivery stop the transfer',async()=>{
  await assert.rejects(simulate({changedApproval:true}));
  await assert.rejects(simulate({duplicate:true}));
  await assert.rejects(simulate({mutateDelivery:item=>{item.updated_at='edited';}}));
  await assert.rejects(simulate({mutateDelivery:item=>{item.issue_url='wrong';}}));
});
test('an absent or unrelated delivery expires without retry or publication',async()=>{
  await assert.rejects(simulate({empty:true}));
  await assert.rejects(simulate({mutateDelivery:item=>{item.user.login='stranger';}}));
});

test('approval context fields reject type coercion and invalid clock values',()=>{
  for(const [key,value] of Object.entries({publisherRunId:200,approvalCommentId:'123',createdAt:String(NOW),
    expiresAt:String(NOW+180),requestId:10,appId:['sangil'],issue:'1'})){
    assert.throws(()=>sealFileReference(URL,challenge({[key]:value}),NOW));
  }
  assert.throws(()=>sealFileReference(URL,challenge(),NaN));
});
test('mutated original owner events are rejected before any API operation',async()=>{
  for(const mutate of [e=>{e.sender.login='stranger';},e=>{e.comment.updated_at='edited';},
    e=>{e.issue.pull_request={};},e=>{e.issue.number=42;},e=>{e.comment.body=e.comment.body.replace('sealed-v1',URL);},
    e=>{e.comment.created_at=e.comment.updated_at='2026-11-02T00:00:00Z';}]){
    const f=fixture();mutate(f.event);let calls=0;
    await assert.rejects(receiveFileReference('sangil',f.event,'fake-build',{environment:f.env,now:()=>NOW,
      api:()=>{calls++;throw Error('must_not_call');},verifyController:()=>true}));
    assert.equal(calls,0);
  }
});
