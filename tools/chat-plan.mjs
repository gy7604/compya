// Read-only checkout identity and instruction plan. Executes no checks/build/deploy.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const profile=JSON.parse(fs.readFileSync(path.join(root,'automation/chat-workflow.json'),'utf8'));
const git=(...args)=>spawnSync('git',['-C',root,...args],{encoding:'utf8',timeout:10000});
const head=git('rev-parse','HEAD');
const ancestry=git('merge-base','--is-ancestor',profile.source.baselineSha,'HEAD');
const remote=git('remote','get-url','origin');
const expected=profile.repository.toLowerCase();
const actual=remote.stdout.trim().replace(/\.git$/,'').replace(/^https:\/\/github\.com\//,'').replace(/^git@github\.com:/,'').toLowerCase();
const status=git('status','--porcelain','--untracked-files=no');
const identityValid=head.status===0 && remote.status===0 && actual===expected;
const canonicalCheckout=identityValid && ancestry.status===0;
const blockers=[...profile.blockers];
if(!canonicalCheckout) blockers.unshift('CHECKOUT_IS_NOT_FROM_VERIFIED_APP_SOURCE');
if(status.status!==0 || status.stdout.trim()) blockers.unshift('TRACKED_CHANGES_REQUIRE_DIFF_REVIEW');
console.log(JSON.stringify({schema:1,appId:profile.appId,mode:'PLAN_ONLY',
 repository:profile.repository,checkoutSha:head.stdout.trim(),canonicalCheckout,
 liveStateRecheckRequired:true,source:profile.source,hosting:profile.hosting,
 suggestedChecks:profile.checks,blockers,executed:{checks:false,build:false,pc:false,publish:false,install:false},
 publication:'NOT_APPROVED',pcSelection:'NOT_SELECTED'},null,2));
if(!identityValid)process.exitCode=2;
