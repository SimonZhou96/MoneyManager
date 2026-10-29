import concurrent.futures
import shutil
import subprocess
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from redis import Redis
from redis.exceptions import ConnectionError

from web import queue
from web.business import BusinessError


class RedisTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        executable = shutil.which('redis-server')
        if not executable:
            raise unittest.SkipTest('redis-server is required for isolated lock tests')
        cls.directory = tempfile.TemporaryDirectory(prefix='screening-lock-tests-')
        cls.socket = str(Path(cls.directory.name) / 'redis.sock')
        cls.server = subprocess.Popen(
            [executable, '--port', '0', '--unixsocket', cls.socket,
             '--save', '', '--appendonly', 'no'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        cls.redis = Redis(unix_socket_path=cls.socket)
        deadline = time.monotonic() + 10
        while True:
            try:
                cls.redis.ping()
                break
            except ConnectionError:
                if time.monotonic() >= deadline:
                    cls.server.terminate()
                    cls.server.wait()
                    cls.directory.cleanup()
                    raise
                time.sleep(.02)

    @classmethod
    def tearDownClass(cls):
        cls.redis.close()
        cls.server.terminate()
        cls.server.wait(timeout=10)
        cls.directory.cleanup()

    def setUp(self):
        self.redis.flushdb()


class ScreeningMarketLockTests(RedisTestCase):
    def test_concurrent_requests_for_same_market_have_one_owner(self):
        def submit(number):
            try:
                return queue.acquire_screening_locks(self.redis, f'job-{number}', ['HK'], date(2026, 10, 29))
            except BusinessError as exc:
                self.assertEqual(exc.error_code, 'SCREENING_MARKET_BUSY')
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
            results = list(executor.map(submit, range(30)))
        self.assertEqual(sum(item is not None for item in results), 1)
        self.assertEqual(self.redis.ttl('screening:active:2026-10-29:HK'), -1)

    def test_other_markets_and_days_do_not_conflict(self):
        for day, market in [(28, 'HK'), (29, 'HK'), (29, 'US'), (29, 'A')]:
            queue.acquire_screening_locks(self.redis, f'{day}-{market}', [market], date(2026, 10, day))
        self.assertEqual(self.redis.dbsize(), 4)

    def test_multi_market_conflict_acquires_nothing(self):
        queue.acquire_screening_locks(self.redis, 'existing', ['US'], date(2026, 10, 29))
        with self.assertRaises(BusinessError) as raised:
            queue.acquire_screening_locks(self.redis, 'new', ['A', 'US', 'HK'], date(2026, 10, 29))
        self.assertIn('existing', raised.exception.message)
        self.assertIn('US', raised.exception.message)
        self.assertIsNone(self.redis.get('screening:active:2026-10-29:A'))
        self.assertIsNone(self.redis.get('screening:active:2026-10-29:HK'))

    def test_old_owner_cannot_release_new_attempt(self):
        old = queue.acquire_screening_locks(self.redis, 'same-job', ['HK'], date(2026, 10, 29))
        queue.release_screening_locks(self.redis, 'same-job', old)
        new = queue.acquire_screening_locks(self.redis, 'same-job', ['HK'], date(2026, 10, 29))
        queue.release_screening_locks(self.redis, 'same-job', old)
        self.assertIsNotNone(self.redis.get('screening:active:2026-10-29:HK'))
        queue.release_screening_locks(self.redis, 'same-job', new)
        self.assertEqual(self.redis.dbsize(), 0)

    def test_releasing_one_market_preserves_other_market(self):
        lock = queue.acquire_screening_locks(self.redis, 'group', ['HK', 'US'], date(2026, 10, 29))
        queue.release_screening_locks(self.redis, 'group', lock, ['HK'])
        self.assertIsNone(self.redis.get('screening:active:2026-10-29:HK'))
        self.assertIsNotNone(self.redis.get('screening:active:2026-10-29:US'))

    def test_missing_redis_configuration_does_not_fall_back_to_thread(self):
        with patch.dict('os.environ', {'REDIS_URL': ''}):
            with self.assertRaises(BusinessError) as raised:
                queue.screening_queue()
        self.assertEqual(raised.exception.error_code, 'SCREENING_QUEUE_UNAVAILABLE')

    def test_unreachable_redis_rejects_lock(self):
        unavailable = Redis(unix_socket_path=str(Path(self.directory.name) / 'absent.sock'))
        try:
            with self.assertRaises(BusinessError) as raised:
                queue.acquire_screening_locks(unavailable, 'new', ['HK'], date(2026, 10, 29))
            self.assertEqual(raised.exception.error_code, 'SCREENING_QUEUE_UNAVAILABLE')
        finally:
            unavailable.close()
