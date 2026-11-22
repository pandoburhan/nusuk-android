"""
Unit tests and scenario simulations for Nusuk Automation Hub v2.0 resilience enhancements.
Tests:
1. Transport and Socket Exhaustion Error Classification (WinError 10055, 10048, AdbConnectionError, etc.)
2. Forensic Service (XML & Screenshot dump, throttling, rotation)
3. Universal System Warning & ANR Dialog Classifier (Nusuk, MEmu Launcher2, Process system, Indonesian phrases, bounds)
4. Persistent Task Intent (storage, DTO synchronization, clearing on STOP/target reached)
5. Auto-Resume Behavior (on reconnect, watchdog come_online, force-restart-app)
6. Stop vs Restart Semantics (STOP clears intent; RESTART preserves intent & auto-resumes)
"""
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from backend.adb_manager import is_transport_or_socket_error, adb_manager
from backend.forensic_service import ForensicService
from backend.screen_classifier import screen_classifier, ScreenState
from backend.instance_manager import instance_manager, TaskIntent, Runtime
from backend.models import Instance, ClaimStatus, LogSeverity


class TestSocketAndTransportClassification(unittest.TestCase):
    def test_winerror_10055_socket_buffer_exhaustion(self):
        err = OSError(10055, "An operation on a socket could not be performed because the system lacked sufficient buffer space or because a queue was full")
        err.winerror = 10055
        self.assertTrue(is_transport_or_socket_error(err))

    def test_winerror_10048_socket_address_in_use(self):
        err = OSError(10048, "Only one usage of each socket address (protocol/network address/port) is normally permitted")
        err.winerror = 10048
        self.assertTrue(is_transport_or_socket_error(err))

    def test_other_winerrors(self):
        for we in (10053, 10054, 10060, 10061):
            err = OSError(we, "Connection dropped")
            err.winerror = we
            self.assertTrue(is_transport_or_socket_error(err))

    def test_standard_socket_exceptions(self):
        self.assertTrue(is_transport_or_socket_error(ConnectionError("Connection failed")))
        self.assertTrue(is_transport_or_socket_error(ConnectionResetError("Reset by peer")))
        self.assertTrue(is_transport_or_socket_error(ConnectionRefusedError("Refused")))
        self.assertTrue(is_transport_or_socket_error(BrokenPipeError("Broken pipe")))
        self.assertTrue(is_transport_or_socket_error(TimeoutError("Socket timeout")))

    def test_adbutils_and_u2_errors(self):
        class MockAdbConnectionError(Exception):
            __module__ = "adbutils.errors"
        self.assertTrue(is_transport_or_socket_error(MockAdbConnectionError("connect to adb server failed: [WinError 10055]")))

        class MockJsonRpcError(Exception):
            __module__ = "uiautomator2.exceptions"
        self.assertTrue(is_transport_or_socket_error(MockJsonRpcError("HTTP connection failed")))

    def test_non_transport_exceptions(self):
        self.assertFalse(is_transport_or_socket_error(ValueError("Invalid mode")))
        self.assertFalse(is_transport_or_socket_error(KeyError("missing_key")))
        self.assertFalse(is_transport_or_socket_error(RuntimeError("Unknown app error")))
        self.assertFalse(is_transport_or_socket_error(None))


class TestForensicService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="nusuk_forensics_test_")
        self.service = ForensicService(directory=self.temp_dir, max_files=4)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_dump_xml_and_png(self):
        xml_data = "<hierarchy><node text='ANR Dialog'/></hierarchy>"
        png_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        res = self.service.dump(
            device_uuid="test-uuid-1234",
            device_name="Test-Device-01",
            category="system_anr",
            reason="Nusuk isn't responding",
            xml_content=xml_data,
            screenshot_bytes=png_data,
            force=True,
        )
        self.assertIsNotNone(res)
        xml_path, png_path = res
        self.assertTrue(os.path.exists(xml_path))
        self.assertTrue(os.path.exists(png_path))
        with open(xml_path, "r", encoding="utf-8") as f:
            self.assertIn("ANR Dialog", f.read())
        with open(png_path, "rb") as f:
            self.assertEqual(f.read(), png_data)

    def test_dump_throttling(self):
        # First dump succeeds
        r1 = self.service.dump(
            device_uuid="dev-throttle",
            device_name="Dev-1",
            category="anr",
            reason="dialog1",
            xml_content="<xml1/>",
            throttle_sec=10.0,
        )
        self.assertIsNotNone(r1)

        # Second immediate dump without force is throttled
        r2 = self.service.dump(
            device_uuid="dev-throttle",
            device_name="Dev-1",
            category="anr",
            reason="dialog1",
            xml_content="<xml2/>",
            throttle_sec=10.0,
        )
        self.assertIsNone(r2)

        # Forced dump bypasses throttle
        r3 = self.service.dump(
            device_uuid="dev-throttle",
            device_name="Dev-1",
            category="anr",
            reason="dialog1",
            xml_content="<xml3/>",
            force=True,
        )
        self.assertIsNotNone(r3)

    def test_file_rotation(self):
        # Create 6 dumps when max_files=4
        for i in range(6):
            time.sleep(0.01)
            self.service.dump(
                device_uuid=f"dev-{i}",
                device_name=f"Dev-{i}",
                category="test",
                reason=f"step_{i}",
                xml_content=f"<xml>{i}</xml>",
                force=True,
            )
        files = os.listdir(self.temp_dir)
        self.assertLessEqual(len(files), 4)


class TestScreenClassifierDialogs(unittest.TestCase):
    def test_nusuk_anr_dialog(self):
        xml = """
        <hierarchy rotation="0">
          <node class="android.widget.FrameLayout" bounds="[0,0][720,1280]">
            <node resource-id="android:id/parentPanel" bounds="[60,400][660,880]">
              <node resource-id="android:id/alertTitle" text="Nusuk isn't responding" bounds="[100,450][620,520]" />
              <node resource-id="android:id/aerr_wait" text="Wait" clickable="true" bounds="[100,750][280,830]" />
              <node resource-id="android:id/aerr_close" text="Close app" clickable="true" bounds="[400,750][600,830]" />
            </node>
          </node>
        </hierarchy>
        """
        info = screen_classifier.parse_hierarchy(xml, screen_w=720, screen_h=1280)
        self.assertEqual(info["state"], ScreenState.SCREEN_SYSTEM_DIALOG)
        self.assertTrue(info["has_system_crash_dialog"])
        diag = info["system_dialog_data"]
        self.assertIsNotNone(diag)
        self.assertEqual(diag["dialog_target"], "nusuk")
        self.assertTrue(diag["is_anr"])
        self.assertEqual(diag["wait_bounds"], (100, 750, 280, 830))
        self.assertEqual(diag["close_bounds"], (400, 750, 600, 830))

    def test_memu_launcher_anr_dialog(self):
        xml = """
        <hierarchy rotation="0">
          <node class="android.widget.FrameLayout" bounds="[0,0][720,1280]">
            <node resource-id="android:id/parentPanel" bounds="[50,350][670,850]">
              <node resource-id="android:id/alertTitle" text="MEmu Launcher2 isn't responding" bounds="[80,380][640,460]" />
              <node resource-id="android:id/button2" text="Wait" clickable="true" bounds="[120,720][290,800]" />
              <node resource-id="android:id/button1" text="Close app" clickable="true" bounds="[420,720][600,800]" />
            </node>
          </node>
        </hierarchy>
        """
        info = screen_classifier.parse_hierarchy(xml, screen_w=720, screen_h=1280)
        self.assertEqual(info["state"], ScreenState.SCREEN_SYSTEM_DIALOG)
        diag = info["system_dialog_data"]
        self.assertEqual(diag["dialog_target"], "system")
        self.assertTrue(diag["is_anr"])
        self.assertEqual(diag["wait_bounds"], (120, 720, 290, 800))
        self.assertEqual(diag["close_bounds"], (420, 720, 600, 800))

    def test_process_system_anr_dialog(self):
        xml = """
        <hierarchy rotation="0">
          <node class="android.widget.FrameLayout" bounds="[0,0][720,1280]">
            <node resource-id="android:id/alertTitle" text="Process system isn't responding" bounds="[100,400][620,480]" />
            <node text="Tunggu" clickable="true" bounds="[120,700][300,780]" />
            <node text="Tutup aplikasi" clickable="true" bounds="[400,700][620,780]" />
          </node>
        </hierarchy>
        """
        info = screen_classifier.parse_hierarchy(xml, screen_w=720, screen_h=1280)
        self.assertEqual(info["state"], ScreenState.SCREEN_SYSTEM_DIALOG)
        diag = info["system_dialog_data"]
        self.assertEqual(diag["dialog_target"], "system")
        self.assertTrue(diag["is_anr"])
        self.assertEqual(diag["wait_bounds"], (120, 700, 300, 780))
        self.assertEqual(diag["close_bounds"], (400, 700, 620, 780))

    def test_indonesian_nusuk_crash_dialog(self):
        xml = """
        <hierarchy rotation="0">
          <node class="android.widget.FrameLayout" bounds="[0,0][720,1280]">
            <node resource-id="android:id/alertTitle" text="Nusuk telah berhenti" bounds="[100,400][620,480]" />
            <node text="Buka ulang" clickable="true" bounds="[120,700][300,780]" />
            <node text="Tutup" clickable="true" bounds="[400,700][620,780]" />
          </node>
        </hierarchy>
        """
        info = screen_classifier.parse_hierarchy(xml, screen_w=720, screen_h=1280)
        self.assertEqual(info["state"], ScreenState.SCREEN_SYSTEM_DIALOG)
        diag = info["system_dialog_data"]
        self.assertEqual(diag["dialog_target"], "nusuk")
        self.assertTrue(diag["is_crash"])
        self.assertEqual(diag["restart_bounds"], (120, 700, 300, 780))
        self.assertEqual(diag["close_bounds"], (400, 700, 620, 780))


class TestPersistentTaskIntentAndAutoResume(unittest.TestCase):
    def setUp(self):
        # Prevent database timeouts during unit testing
        self.p1 = patch.object(instance_manager, "_persist_task_intent")
        self.p2 = patch.object(instance_manager, "_delete_task_intent")
        self.p3 = patch("backend.instance_manager.log_event")
        self.p1.start()
        self.p2.start()
        self.p3.start()

    def tearDown(self):
        self.p1.stop()
        self.p2.stop()
        self.p3.stop()

    def test_task_intent_lifecycle(self):
        dev_uuid = "test-intent-uuid-1"
        # Set task intent
        intent = instance_manager.set_task_intent(
            device_uuid=dev_uuid,
            mode="auto",
            stop_mode="limit",
            stop_limit=50,
            completed_count=0,
        )
        self.assertEqual(intent.mode, "auto")
        self.assertEqual(intent.stop_limit, 50)
        self.assertEqual(intent.completed_count, 0)

        # Status payload reflection
        payload = instance_manager.status_payload(dev_uuid)
        self.assertTrue(payload["has_pending_task"])
        self.assertEqual(payload["pending_task_mode"], "auto")
        self.assertEqual(payload["pending_task_stop_limit"], 50)
        self.assertEqual(payload["pending_task_completed"], 0)

        # Progress updates
        instance_manager.update_task_progress(dev_uuid, 15)
        payload2 = instance_manager.status_payload(dev_uuid)
        self.assertEqual(payload2["pending_task_completed"], 15)
        self.assertEqual(payload2["session_completed_count"], 15)

        # Mark offline retains task intent
        instance_manager.mark_offline(dev_uuid, "test disconnect")
        payload3 = instance_manager.status_payload(dev_uuid)
        self.assertEqual(payload3["status"], "OFFLINE")
        self.assertTrue(payload3["has_pending_task"])
        self.assertIn("akan otomatis dilanjutkan", payload3["current_step"])

        # Explicit clear task intent
        instance_manager.clear_task_intent(dev_uuid, "Operator clicked STOP")
        payload4 = instance_manager.status_payload(dev_uuid)
        self.assertFalse(payload4["has_pending_task"])
        self.assertIsNone(payload4["pending_task_mode"])

    def test_auto_resume_trigger(self):
        from backend.orchestrator import orchestrator
        dev_uuid = "test-resume-uuid-2"
        instance_manager.set_task_intent(
            device_uuid=dev_uuid,
            mode="auto",
            stop_mode="limit",
            stop_limit=100,
            completed_count=20,
        )

        mock_inst = MagicMock()
        mock_inst.uuid = dev_uuid
        mock_inst.name = "Device-02"
        mock_inst.address = "127.0.0.1:5555"
        mock_inst.nusuk_installed = True
        instance_manager.set_runtime(dev_uuid, status="IDLE")

        with patch.object(instance_manager, "get_instance", return_value=mock_inst), \
             patch("backend.orchestrator.InstanceWorker") as mock_worker_cls, \
             patch("backend.orchestrator.settings") as mock_settings:
            mock_settings.ai_api_key = "test-key"
            mock_worker = MagicMock()
            mock_worker.is_running = False
            mock_worker.start.return_value = True
            mock_worker_cls.return_value = mock_worker

            res = orchestrator.trigger_auto_resume(dev_uuid)
            self.assertTrue(res)
            # Verify worker started with preserved mode, stop condition, and progress count!
            mock_worker.start.assert_called_with(
                "auto",
                stop_mode="limit",
                stop_limit=100,
                initial_completed=20,
            )


class TestStopVsRestartSemantics(unittest.TestCase):
    def setUp(self):
        self.p1 = patch.object(instance_manager, "_persist_task_intent")
        self.p2 = patch.object(instance_manager, "_delete_task_intent")
        self.p3 = patch("backend.instance_manager.log_event")
        self.p4 = patch("backend.orchestrator.log_event")
        self.p1.start()
        self.p2.start()
        self.p3.start()
        self.p4.start()

    def tearDown(self):
        self.p1.stop()
        self.p2.stop()
        self.p3.stop()
        self.p4.stop()

    def test_stop_clears_task_intent(self):
        from backend.orchestrator import orchestrator
        dev_uuid = "test-stop-uuid-3"
        instance_manager.set_task_intent(
            device_uuid=dev_uuid,
            mode="answer_only",
            stop_mode="limit",
            stop_limit=30,
            completed_count=5,
        )
        self.assertTrue(instance_manager.status_payload(dev_uuid)["has_pending_task"])

        mock_inst = MagicMock()
        mock_inst.uuid = dev_uuid
        mock_inst.name = "Device-03"

        with patch.object(instance_manager, "get_instance", return_value=mock_inst):
            orchestrator.stop_many([dev_uuid], reason="Operator clicked STOP in dashboard")
            self.assertFalse(instance_manager.status_payload(dev_uuid)["has_pending_task"])

    def test_force_restart_app_preserves_task_intent_and_resumes(self):
        from backend.orchestrator import orchestrator
        dev_uuid = "test-restart-uuid-4"
        instance_manager.set_task_intent(
            device_uuid=dev_uuid,
            mode="redeem_only",
            stop_mode="limit",
            stop_limit=40,
            completed_count=12,
        )
        instance_manager.set_runtime(dev_uuid, status="RUNNING")

        mock_inst = MagicMock()
        mock_inst.uuid = dev_uuid
        mock_inst.name = "Device-04"
        mock_inst.address = "127.0.0.1:5556"

        with patch.object(instance_manager, "get_instance", return_value=mock_inst), \
             patch.object(adb_manager, "is_ready", return_value=True), \
             patch.object(adb_manager, "clear_ram"), \
             patch.object(adb_manager, "shell"), \
             patch.object(adb_manager, "launch_app"), \
             patch.object(orchestrator, "_stop_and_wait", return_value=True), \
             patch.object(orchestrator, "trigger_auto_resume") as mock_resume:
            ok, msg = orchestrator.force_restart_app(dev_uuid)
            self.assertTrue(ok)
            self.assertIn("otomatis dilanjutkan", msg)
            # Desired task was NOT wiped
            self.assertTrue(instance_manager.status_payload(dev_uuid)["has_pending_task"])
            self.assertEqual(instance_manager.status_payload(dev_uuid)["pending_task_completed"], 12)
            mock_resume.assert_called_with(dev_uuid)


class TestWorkerSocketErrorRecovery(unittest.TestCase):
    def setUp(self):
        self.p1 = patch.object(instance_manager, "_persist_task_intent")
        self.p2 = patch.object(instance_manager, "_delete_task_intent")
        self.p3 = patch("backend.instance_manager.log_event")
        self.p4 = patch("backend.instance_worker.log_event")
        self.p1.start()
        self.p2.start()
        self.p3.start()
        self.p4.start()

    def tearDown(self):
        self.p1.stop()
        self.p2.stop()
        self.p3.stop()
        self.p4.stop()

    def test_worker_socket_error_tier2_fallback_to_offline_retains_intent(self):
        from backend.instance_worker import InstanceWorker
        dev_uuid = "test-socket-err-uuid-5"
        instance_manager.set_task_intent(
            device_uuid=dev_uuid,
            mode="auto",
            stop_mode="limit",
            stop_limit=50,
            completed_count=8,
        )

        mock_inst = MagicMock()
        mock_inst.uuid = dev_uuid
        mock_inst.name = "Device-05"
        mock_inst.address = "127.0.0.1:5557"
        mock_inst.connection_type = "tcp"
        mock_inst.is_legacy_primary = False

        worker = InstanceWorker(mock_inst)
        worker.active_mode = "auto"
        worker.stop_limit = 50
        worker.session_completed_count = 8
        worker.current_claim_id = 999  # simulate an in_progress key claim
        worker.current_key_code = "NUSUK-TEST-KEY"

        # Mock sleep so tests run instantaneously
        worker._sleep = MagicMock(return_value=True)

        # Simulate WinError 10055 on inspect screen
        win_err_10055 = OSError(10055, "An operation on a socket could not be performed because the system lacked sufficient buffer space or because a queue was full")
        win_err_10055.winerror = 10055

        with patch.object(worker, "connect_device", return_value=True), \
             patch.object(worker, "_inspect_screen", side_effect=win_err_10055), \
             patch.object(adb_manager, "ensure_connected", return_value=False), \
             patch("backend.key_service.rollback_claim", return_value="NUSUK-TEST-KEY") as mock_rollback:
            # Run the worker loop
            worker._run_loop()

            # Verify Safe Rollback was executed
            mock_rollback.assert_called_with(999)

            # Verify final instance status is OFFLINE, NOT ERROR!
            payload = instance_manager.status_payload(dev_uuid)
            self.assertEqual(payload["status"], "OFFLINE")
            self.assertNotEqual(payload["status"], "ERROR")

            # Verify TaskIntent was retained!
            self.assertTrue(payload["has_pending_task"])
            self.assertEqual(payload["pending_task_mode"], "auto")
            self.assertEqual(payload["pending_task_completed"], 8)
            self.assertEqual(payload["pending_task_stop_limit"], 50)


if __name__ == "__main__":
    unittest.main()
