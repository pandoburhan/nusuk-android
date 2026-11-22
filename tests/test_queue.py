"""
Unit tests and scenario verification for the QUEUE (Antrian Tugas Mendatang) system in Nusuk Automation Hub v2.0.
"""
import time
import unittest
from unittest.mock import MagicMock, patch

from backend.instance_manager import instance_manager, Runtime
from backend.models import Instance, LogSeverity
from backend.orchestrator import orchestrator


class TestQueueSystem(unittest.TestCase):
    def setUp(self):
        self.dev_uuid = "queue-test-device-001"
        self.patch_p_intent = patch.object(instance_manager, "_persist_task_intent")
        self.patch_d_intent = patch.object(instance_manager, "_delete_task_intent")
        self.patch_p_queue = patch.object(instance_manager, "_persist_task_queue")
        self.patch_d_queue = patch.object(instance_manager, "_delete_task_queue")
        self.patch_log = patch("backend.instance_manager.log_event")
        self.patch_orch_log = patch("backend.orchestrator.log_event")
        self.patch_key_service = patch("backend.key_service.eligible_count", return_value=100)
        self.patch_adb_states = patch("backend.adb_manager.adb_manager.device_states", return_value={"127.0.0.1:5555": "device", "None": "device", "": "device"})

        self.mock_p_intent = self.patch_p_intent.start()
        self.mock_d_intent = self.patch_d_intent.start()
        self.mock_p_queue = self.patch_p_queue.start()
        self.mock_d_queue = self.patch_d_queue.start()
        self.mock_log = self.patch_log.start()
        self.mock_orch_log = self.patch_orch_log.start()
        self.mock_key_service = self.patch_key_service.start()
        self.mock_adb_states = self.patch_adb_states.start()

        # Reset runtime
        orchestrator.workers.clear()
        with instance_manager._lock:
            instance_manager.runtime[self.dev_uuid] = Runtime()
            instance_manager.runtime[self.dev_uuid].task_queue = []
            instance_manager.runtime[self.dev_uuid].task_intent = None

    def tearDown(self):
        orchestrator.workers.clear()
        self.patch_p_intent.stop()
        self.patch_d_intent.stop()
        self.patch_p_queue.stop()
        self.patch_d_queue.stop()
        self.patch_log.stop()
        self.patch_orch_log.stop()
        self.patch_key_service.stop()
        self.patch_adb_states.stop()
        with instance_manager._lock:
            instance_manager.runtime.pop(self.dev_uuid, None)

    def test_enqueue_fifo_and_status_payload(self):
        """Enqueue multiple tasks, ensure FIFO order and status_payload reflect queue fields."""
        item1 = instance_manager.enqueue_task(self.dev_uuid, "answer_only", "limit", 50)
        item2 = instance_manager.enqueue_task(self.dev_uuid, "redeem_only", "until_exhausted", 100)

        queue = instance_manager.get_queue(self.dev_uuid)
        self.assertEqual(len(queue), 2)
        self.assertEqual(queue[0]["mode"], "answer_only")
        self.assertEqual(queue[0]["stop_limit"], 50)
        self.assertEqual(queue[1]["mode"], "redeem_only")
        self.assertEqual(queue[1]["stop_mode"], "until_exhausted")

        payload = instance_manager.status_payload(self.dev_uuid)
        self.assertTrue(payload["has_queued_tasks"])
        self.assertEqual(payload["queue_length"], 2)
        self.assertEqual(payload["next_queued_mode"], "answer_only")

        # Dequeue FIFO
        popped = instance_manager.dequeue_next_task(self.dev_uuid)
        self.assertEqual(popped["mode"], "answer_only")
        self.assertEqual(len(instance_manager.get_queue(self.dev_uuid)), 1)
        self.assertEqual(instance_manager.status_payload(self.dev_uuid)["next_queued_mode"], "redeem_only")

    def test_cancel_single_queue_item(self):
        """Cancel specific item by queue_id without affecting other items."""
        item1 = instance_manager.enqueue_task(self.dev_uuid, "answer_only", "limit", 10)
        item2 = instance_manager.enqueue_task(self.dev_uuid, "redeem_only", "limit", 20)
        item3 = instance_manager.enqueue_task(self.dev_uuid, "auto", "limit", 30)

        # Cancel item 2
        removed = instance_manager.cancel_queue_item(self.dev_uuid, item2["queue_id"])
        self.assertTrue(removed)

        remaining = instance_manager.get_queue(self.dev_uuid)
        self.assertEqual(len(remaining), 2)
        modes = [it["mode"] for it in remaining]
        self.assertEqual(modes, ["answer_only", "auto"])

    def test_cancel_all_queue(self):
        """Clear all items in queue."""
        instance_manager.enqueue_task(self.dev_uuid, "answer_only", "limit", 10)
        instance_manager.enqueue_task(self.dev_uuid, "redeem_only", "limit", 20)
        instance_manager.clear_queue(self.dev_uuid)

        self.assertEqual(len(instance_manager.get_queue(self.dev_uuid)), 0)
        payload = instance_manager.status_payload(self.dev_uuid)
        self.assertFalse(payload["has_queued_tasks"])
        self.assertEqual(payload["queue_length"], 0)
        self.assertIsNone(payload["next_queued_mode"])

    @patch("backend.instance_manager.instance_manager.get_instance")
    def test_queue_many_validation(self, mock_get_inst):
        mock_inst = Instance(uuid=self.dev_uuid, name="Dev1", nusuk_installed=True)
        mock_get_inst.return_value = mock_inst

        # 1. IDLE device should be skipped
        instance_manager.set_runtime(self.dev_uuid, status="IDLE")
        res = orchestrator.queue_many([self.dev_uuid], "redeem_only")
        self.assertEqual(len(res["queued"]), 0)
        self.assertEqual(len(res["skipped"]), 1)
        self.assertIn("Device belum berjalan", res["skipped"][0]["reason"])

        # 2. RUNNING device in same mode should be skipped
        instance_manager.set_runtime(self.dev_uuid, status="RUNNING", mode="redeem_only")
        res = orchestrator.queue_many([self.dev_uuid], "redeem_only")
        self.assertEqual(len(res["queued"]), 0)
        self.assertEqual(len(res["skipped"]), 1)
        self.assertIn("sudah sama", res["skipped"][0]["reason"])

        # 3. RUNNING device in different mode should be successfully queued
        res = orchestrator.queue_many([self.dev_uuid], "answer_only", stop_limit=25)
        self.assertEqual(len(res["queued"]), 1)
        self.assertEqual(res["queued"][0]["mode"], "answer_only")
        self.assertEqual(len(instance_manager.get_queue(self.dev_uuid)), 1)

    @patch("backend.instance_manager.instance_manager.get_instance")
    def test_stop_many_clears_queue(self, mock_get_inst):
        """Operator STOP must clear both active intent and pending queue."""
        mock_inst = Instance(uuid=self.dev_uuid, name="Dev1")
        mock_get_inst.return_value = mock_inst

        instance_manager.set_runtime(self.dev_uuid, status="RUNNING", mode="auto")
        instance_manager.set_task_intent(self.dev_uuid, "auto", "limit", 100)
        instance_manager.enqueue_task(self.dev_uuid, "redeem_only", "limit", 50)

        orchestrator.stop_many([self.dev_uuid])

        self.assertIsNone(instance_manager.get_task_intent(self.dev_uuid))
        self.assertEqual(len(instance_manager.get_queue(self.dev_uuid)), 0)

    @patch("backend.instance_manager.instance_manager.get_instance")
    def test_promote_next_queued_task(self, mock_get_inst):
        """When task finishes naturally to IDLE, next queued task starts automatically."""
        mock_inst = Instance(uuid=self.dev_uuid, name="Dev1", nusuk_installed=True)
        mock_get_inst.return_value = mock_inst

        instance_manager.set_runtime(self.dev_uuid, status="IDLE")
        instance_manager.enqueue_task(self.dev_uuid, "redeem_only", "limit", 10)

        with patch("backend.orchestrator.InstanceWorker") as mock_worker_cls, \
             patch("backend.key_service.eligible_count", return_value=50):
            mock_worker = MagicMock()
            mock_worker.start.return_value = True
            mock_worker_cls.return_value = mock_worker

            orchestrator._promote_next_queued_task(self.dev_uuid)

            mock_worker.start.assert_called_once_with("redeem_only", stop_mode="limit", stop_limit=10)
            self.assertEqual(len(instance_manager.get_queue(self.dev_uuid)), 0)
            ti = instance_manager.get_task_intent(self.dev_uuid)
            self.assertIsNotNone(ti)
            self.assertEqual(ti.mode, "redeem_only")

    @patch("backend.instance_manager.instance_manager.get_instance")
    def test_daily_limit_skips_quiz_and_promotes_redeem(self, mock_get_inst):
        """When Daily Limit occurs, queued quiz tasks are skipped, and non-quiz task is promoted."""
        mock_inst = Instance(uuid=self.dev_uuid, name="Dev1", nusuk_installed=True)
        mock_get_inst.return_value = mock_inst

        instance_manager.set_runtime(self.dev_uuid, status="IDLE")
        # Enqueue 1 quiz mode, then 1 redeem mode
        instance_manager.enqueue_task(self.dev_uuid, "answer_only", "limit", 10)
        instance_manager.enqueue_task(self.dev_uuid, "redeem_only", "limit", 20)

        with patch("backend.orchestrator.InstanceWorker") as mock_worker_cls, \
             patch("backend.key_service.eligible_count", return_value=50):
            mock_worker = MagicMock()
            mock_worker.start.return_value = True
            mock_worker_cls.return_value = mock_worker

            # Promote with DAILY_LIMIT stopped reason
            orchestrator._promote_next_queued_task(self.dev_uuid, stopped_reason="DAILY_LIMIT")

            # answer_only must have been discarded; redeem_only started
            mock_worker.start.assert_called_once_with("redeem_only", stop_mode="limit", stop_limit=20)
            self.assertEqual(len(instance_manager.get_queue(self.dev_uuid)), 0)
            ti = instance_manager.get_task_intent(self.dev_uuid)
            self.assertEqual(ti.mode, "redeem_only")

    @patch("backend.instance_manager.instance_manager.get_instance")
    def test_switch_clears_queue(self, mock_get_inst):
        """Starting a new mode on a running instance (SWITCH) must clear existing queue."""
        mock_inst = Instance(uuid=self.dev_uuid, name="Dev1", nusuk_installed=True, adb_serial="127.0.0.1:5555")
        mock_get_inst.return_value = mock_inst

        instance_manager.set_runtime(self.dev_uuid, status="RUNNING", mode="answer_only")
        instance_manager.enqueue_task(self.dev_uuid, "redeem_only", "limit", 20)

        with patch.object(orchestrator._pool, "submit"):
            res = orchestrator.start_many([self.dev_uuid], "auto")
            self.assertEqual(len(res["switched"]), 1)
            # Queue should be cleared because operator manually switched
            self.assertEqual(len(instance_manager.get_queue(self.dev_uuid)), 0)


if __name__ == "__main__":
    unittest.main()
