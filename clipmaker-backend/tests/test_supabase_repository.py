import threading
import time
import unittest
from unittest.mock import patch

import httpx

from services.job_store import SupabaseJobRepository


class _ClosableSession:
    def close(self):
        return None


class _FakeClient:
    def __init__(self, name):
        self.name = name
        self.postgrest = type("Postgrest", (), {"session": _ClosableSession()})()


class SupabaseRepositoryTests(unittest.TestCase):
    def make_repository(self):
        repository = SupabaseJobRepository.__new__(SupabaseJobRepository)
        repository._url = "https://example.supabase.co"
        repository._service_role_key = "test-key"
        repository._client = None
        repository._client_lock = threading.RLock()
        return repository

    @patch("services.job_store.time.sleep")
    def test_transport_error_recreates_client_and_retries(self, sleep):
        repository = self.make_repository()
        clients = iter([_FakeClient("first"), _FakeClient("second")])
        repository._create_client = lambda *_args: next(clients)
        attempts = []

        def operation(client):
            attempts.append(client.name)
            if client.name == "first":
                raise httpx.RemoteProtocolError("connection terminated")
            return "ok"

        self.assertEqual(repository._execute(operation, "test request"), "ok")
        self.assertEqual(attempts, ["first", "second"])
        sleep.assert_called_once()

    def test_worker_threads_serialize_client_operations(self):
        repository = self.make_repository()
        repository._create_client = lambda *_args: _FakeClient("shared")
        state_lock = threading.Lock()
        active = 0
        maximum_active = 0

        def operation(_client):
            nonlocal active, maximum_active
            with state_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.02)
            with state_lock:
                active -= 1

        def execute_operation():
            repository._execute(operation, "concurrent test")

        threads = [threading.Thread(target=execute_operation) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(maximum_active, 1)


if __name__ == "__main__":
    unittest.main()
