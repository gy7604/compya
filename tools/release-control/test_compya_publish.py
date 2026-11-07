import copy,hashlib,io,json,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import Mock,patch
import compya_publish as cp
from compya_publish import validate_event,validate_url,version,inspect_bundle,ASSET
class PublicationTests(unittest.TestCase):
    def event(self):
        return dict(action='created',repository=dict(full_name='gy7604/compya'),sender=dict(login='gy7604'),issue=dict(number=3,title='COMPYA-PUBLICATION-CONTROL-v1',user=dict(login='gy7604')),comment=dict(id=1,user=dict(login='gy7604'),created_at='2026-11-03T00:00:00Z',updated_at='2026-11-03T00:00:00Z',body='/compya-publish approve test-1 '+'a'*40+' 2.0.11 '+'b'*64+' 431744817 sealed-v1'))
    def test_only_fresh_native_owner_approval_is_a_publication_request(self):
        e=self.event();self.assertEqual(validate_event(e,1793664000)['versionName'],'2.0.11')
        for change in [lambda e:e['sender'].update(login='other'),lambda e:e['issue'].update(number=105),lambda e:e['comment'].update(updated_at='edited'),lambda e:e['comment'].update(body=e['comment']['body']+'\n')]:
            a=copy.deepcopy(e);change(a)
            with self.assertRaises(ValueError):validate_event(a,1793664000)
        with self.assertRaises(ValueError):validate_event(e,1793664901)
    def test_handoff_is_only_an_approved_first_party_file_url(self):
        for url in ['http://a.oaiusercontent.com/files/a','https://evil.invalid/files/a','https://a.oaiusercontent.com.evil.invalid/files/a','https://user:password@a.oaiusercontent.com/files/a','https://a.oaiusercontent.com/files/a#fragment']:
            with self.assertRaises(ValueError):validate_url(url)
        self.assertEqual(version('2.0.11'),(2,0,11))
    def bundle(self,root,*,private=False,traversal=False,wrong_source=False):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            for name in ['V26Macro.exe','UpdateHelper.exe','PriceOCR.exe']:z.writestr('V26Macro/'+name,b'MZsynthetic-not-an-executable')
            z.writestr('V26Macro/_internal/lib.dll',b'synthetic-library')
            if private:z.writestr('V26Macro/_internal/last_settings.json','private')
            if traversal:z.writestr('V26Macro/../outside','bad')
        data=stream.getvalue()
        receipt=dict(schema=1,appId='compya',sourceRepository='gy7604/compya',sourceSha=('c' if wrong_source else 'a')*40,requestId='test-1',mode='build',versionName='2.0.11',buildExecuted=True,installed=False,publication='NOT_APPROVED',unitTests=256,zipBytes=len(data),zipSha256=hashlib.sha256(data).hexdigest(),authenticodeVerified=False,realGameVerified=False)
        bundle=root/'candidate.zip'
        with zipfile.ZipFile(bundle,'w') as z:z.writestr(ASSET,data);z.writestr('receipt.json',json.dumps(receipt))
        return bundle,dict(requestId='test-1',sourceSha='a'*40,versionName='2.0.11',outerSha256=hashlib.sha256(bundle.read_bytes()).hexdigest())
    def test_existing_bytes_are_verified_without_running_or_rebuilding_them(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);bundle,r=self.bundle(root);out=root/'out';out.mkdir()
            receipt=inspect_bundle(bundle,r,out)
            self.assertEqual(receipt['publication'],'OWNER_APPROVED');self.assertFalse(receipt['installed'])
            self.assertEqual({p.name for p in out.iterdir()},{ASSET,'build-receipt.json','V26Macro_Windows.sha256'})
    def test_private_user_files_unsafe_paths_wrong_source_and_tampering_are_rejected(self):
        for kwargs in [dict(private=True),dict(traversal=True),dict(wrong_source=True),{}]:
            with tempfile.TemporaryDirectory() as folder:
                root=Path(folder);bundle,r=self.bundle(root,**kwargs);out=root/'out';out.mkdir()
                if not kwargs:r['outerSha256']='0'*64
                with self.assertRaises(ValueError):inspect_bundle(bundle,r,out)
    def test_public_approval_cannot_contain_a_plaintext_private_file_address(self):
        e=self.event()
        e['comment']['body']=e['comment']['body'].replace('sealed-v1','https://synthetic.oaiusercontent.com/files/fake')
        with self.assertRaises(ValueError):validate_event(e,1793664000)
    def test_exact_candidate_approval_carries_only_the_transport_marker(self):
        r=validate_event(self.event(),1793664000)
        self.assertEqual(r['delivery'],'sealed-v1')
        self.assertNotIn('url',r)
    def test_encrypted_transport_captures_the_reference_without_command_line_exposure(self):
        fake='https://synthetic.oaiusercontent.com/files/fake'
        with patch.object(cp.subprocess,'run',return_value=Mock(returncode=0,stdout=json.dumps({'url':fake}))) as child:
            self.assertEqual(cp.receive_file_url(self.event(),{'requestId':'test-1'}),fake)
            args,kwargs=child.call_args
            self.assertNotIn(fake,str(args))
            self.assertTrue(kwargs['capture_output'])
            self.assertEqual(kwargs['timeout'],205)
            self.assertEqual(json.loads(kwargs['input'])['appId'],'compya')
    def test_transport_failure_does_not_echo_any_private_reference(self):
        fake='https://synthetic.oaiusercontent.com/files/fake'
        with patch.object(cp.subprocess,'run',return_value=Mock(returncode=1,stdout=fake,stderr=fake)):
            with self.assertRaises(ValueError) as error:
                cp.receive_file_url(self.event(),{'requestId':'test-1'})
            self.assertNotIn(fake,str(error.exception))
    def test_transport_rejects_unsafe_urls_extra_fields_and_oversized_results(self):
        for value in [{'url':'https://evil.invalid/files/fake'},
            {'url':'https://synthetic.oaiusercontent.com/files/fake','extra':True},
            {'url':'https://synthetic.oaiusercontent.com/files/'+'x'*13000},[]]:
            with patch.object(cp.subprocess,'run',return_value=Mock(returncode=0,stdout=json.dumps(value))):
                with self.assertRaises(ValueError):cp.receive_file_url(self.event(),{'requestId':'test-1'})
    def test_replayed_wrong_target_and_non_owner_approvals_are_rejected(self):
        for change in [lambda e:e.update(action='edited'),lambda e:e['repository'].update(full_name='gy7604/HikingAndroid-Updates'),
            lambda e:e['issue'].update(pull_request={}),lambda e:e['comment']['user'].update(login='stranger')]:
            e=self.event();change(e)
            with self.assertRaises(ValueError):validate_event(e,1793664000)

if __name__=='__main__':unittest.main()
