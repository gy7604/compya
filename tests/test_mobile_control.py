import json
import os
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock,patch
import cv2
import numpy as np
from app import App
from mobile_control import MobileStore,CommandJournal,MobileAgent,endpoint,new_credentials,protected_bytes


class MobileTests(unittest.TestCase):
    def command(self,boot='boot',action='start'):
        return dict(id=str(uuid.uuid4()),action=action,boot_id=boot,expires=time.time()+25)

    def test_only_designated_https_host_is_accepted(self):
        self.assertEqual(endpoint('https://compya-mobile-control.example.workers.dev/'),'https://compya-mobile-control.example.workers.dev')
        for url in ('http://compya-mobile-control.example.workers.dev','https://example.com','https://compya-mobile-control.example.workers.dev@evil.com',
                    'https://compya-mobile-control.example.workers.dev:443','https://compya-mobile-control.example.workers.dev/?token=x',
                    'https://compya-mobile-control.example.workers.dev/path'):
            with self.assertRaises(ValueError):endpoint(url)

    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_credentials_are_dpapi_protected_and_round_trip_only_locally(self):
        with tempfile.TemporaryDirectory() as root:
            keys=new_credentials();config=dict(url='https://compya-mobile-control.example.workers.dev',enabled=True,**keys)
            store=MobileStore(root);store.save(config)
            for value in keys.values():self.assertNotIn(value,store.path.read_text())
            self.assertEqual(store.load(),config)
            with self.assertRaises(OSError):protected_bytes(b'not-a-DPAPI-blob',True)

    def test_commands_are_claimed_once_and_interrupted_execution_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as root:
            journal=CommandJournal(root);cmd=self.command();self.assertTrue(journal.claim(cmd,'boot',time.time()))
            self.assertFalse(journal.claim(cmd,'boot',time.time()))
            resumed=CommandJournal(root);self.assertFalse(resumed.claim(cmd,'boot',time.time()))
            self.assertEqual(resumed.receipts()[0]['state'],'unknown')

    def test_stale_wrong_boot_and_arbitrary_commands_do_not_enter_journal(self):
        with tempfile.TemporaryDirectory() as root:
            journal=CommandJournal(root)
            for change in (dict(expires=time.time()-1),dict(expires=time.time()+3600),dict(boot_id='old'),dict(action='shell'),dict(id='../config')):
                cmd=self.command();cmd.update(change);self.assertFalse(journal.claim(cmd,'boot',time.time()))
            self.assertEqual(journal.data,{})

    def test_failed_durable_claim_never_dispatches(self):
        with tempfile.TemporaryDirectory() as root:
            journal=CommandJournal(root)
            with patch.object(journal,'save',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):journal.claim(self.command(),'boot',time.time())
            self.assertEqual(journal.data,{})

    def agent(self,root):
        return MobileAgent(root,dict(url='https://compya-mobile-control.example.workers.dev',**new_credentials()),Mock(),Mock(),opener=Mock())

    def test_screen_is_game_only_scaled_and_is_not_uploaded_without_viewer(self):
        with tempfile.TemporaryDirectory() as root:
            agent=self.agent(root);agent.publish(dict(running=True));agent.game_frame(cv2.imencode('.png',np.zeros((1280,720,3),np.uint8))[1].tobytes())
            self.assertNotIn('screen',agent.payload(False));payload=agent.payload(True)
            import base64
            image=cv2.imdecode(np.frombuffer(base64.b64decode(payload['screen']),np.uint8),cv2.IMREAD_COLOR)
            self.assertEqual(image.shape[:2],(640,360));agent.capture.assert_not_called()
            self.assertNotIn('screen',agent.payload(True))

    def test_stale_or_other_size_frames_are_not_presented_as_current_game(self):
        with tempfile.TemporaryDirectory() as root:
            agent=self.agent(root);agent.publish(dict(running=True));agent.game_frame(cv2.imencode('.png',np.zeros((300,400,3),np.uint8))[1].tobytes())
            self.assertNotIn('screen',agent.payload(True))
            agent.last_screen=0;agent.screen_at=time.time()-60
            self.assertNotIn('screen',agent.payload(True))

    def test_https_client_identifies_application_and_bounds_server_response(self):
        with tempfile.TemporaryDirectory() as root:
            agent=self.agent(root);response=Mock();response.read.return_value=b'{"viewing":false,"command":null}'
            agent.opener.return_value.__enter__=Mock(return_value=response);agent.opener.return_value.__exit__=Mock(return_value=False)
            self.assertFalse(agent.exchange({})['viewing'])
            request=agent.opener.call_args.args[0]
            self.assertTrue(request.get_header('User-agent').startswith('CompyaMobile/'))
            self.assertEqual(request.full_url,agent.config['url']+'/api/agent/sync')
            response.read.return_value=b'x'*4097
            with self.assertRaises(ValueError):agent.exchange({})

    def test_stopped_macro_can_supply_a_viewer_snapshot_without_running_the_game(self):
        with tempfile.TemporaryDirectory() as root:
            agent=self.agent(root);agent.publish(dict(running=False));agent.capture.return_value=cv2.imencode('.png',np.zeros((1280,720,3),np.uint8))[1].tobytes()
            self.assertIn('screen',agent.payload(True));agent.capture.assert_called_once_with()

    def test_main_thread_accepts_only_claimed_current_commands_and_stop_does_not_close_player(self):
        with tempfile.TemporaryDirectory() as root:
            app=App.__new__(App);app.mobile=self.agent(root);app.stop=threading.Event();app.closing=False
            app.start=Mock(return_value=(True,'started'));app.mobile_snapshot=Mock();app.fields={'serial':Mock()};app.fields['serial'].get.return_value='127.0.0.1:5555'
            cmd=self.command(app.mobile.boot_id,'stop');app.mobile.journal.claim(cmd,app.mobile.boot_id,time.time());app.mobile_command(cmd)
            self.assertTrue(app.stop.is_set());app.start.assert_not_called();self.assertEqual(app.mobile.journal.receipts()[0]['state'],'done')
            old=self.command(app.mobile.boot_id);app.mobile.journal.claim(old,app.mobile.boot_id,time.time());old['expires']=time.time()-1
            app.mobile_command(old);app.start.assert_not_called()
            start=self.command(app.mobile.boot_id);app.mobile.journal.claim(start,app.mobile.boot_id,time.time());app.mobile_command(start)
            app.start.assert_called_once_with(remote=True)
            app.mobile_command(start);app.start.assert_called_once()

    def test_capture_cannot_target_another_emulator_or_run_during_live_loop(self):
        app=App.__new__(App)
        with patch('app.ADB') as adb:
            for cfg in (dict(running=True,serial='127.0.0.1:5555'),dict(running=False,serial='emulator-5580')):
                app.mobile_capture_config=cfg;self.assertIsNone(app.mobile_capture())
            adb.assert_not_called()
