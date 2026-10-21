import threading
import time

import pytest

from aws95.aws.engines.labsnap.collect.fanout import FANOUT, map_ordered
from aws95.core.cancel import CancelledError, CancelToken


def test_results_keep_input_order_and_run_concurrently():
    active, peak = [0], [0]
    lock = threading.Lock()

    def work(n):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.02)
        with lock:
            active[0] -= 1
        return n * 2
    assert map_ordered(work, list(range(10))) == [n * 2 for n in range(10)]
    assert 1 < peak[0] <= FANOUT


def test_an_exception_stays_in_its_slot():
    def work(n):
        if n == 1:
            raise ValueError("x")
        return n
    out = map_ordered(work, [0, 1, 2])
    assert out[0] == 0 and isinstance(out[1], ValueError) and out[2] == 2


def test_cancel_stops_new_items():
    t = CancelToken()
    t.cancel()
    with pytest.raises(CancelledError):
        map_ordered(lambda n: n, [1, 2, 3], cancel=t)


def test_lambda_parallel_reads_match_sequential(monkeypatch):
    from functools import partial
    from tests.test_labsnap_collector_lambda import FakeSession
    from aws95.aws.engines.labsnap.collect.collectors import lambda_
    parallel = lambda_.LambdaCollector().collect(FakeSession(), "us-east-1")
    monkeypatch.setattr(lambda_, "map_ordered", partial(map_ordered, workers=1))
    sequential = lambda_.LambdaCollector().collect(FakeSession(), "us-east-1")
    assert parallel.resources == sequential.resources and parallel.denials == sequential.denials
    assert lambda_.LambdaCollector.timeout_s == 300


def test_s3_parallel_reads_match_sequential(monkeypatch):
    from functools import partial
    from tests.test_labsnap_collector_s3 import FakeSession
    from aws95.aws.engines.labsnap.collect.collectors import s3
    parallel = s3.S3Collector().collect(FakeSession(), "global")
    monkeypatch.setattr(s3, "map_ordered", partial(map_ordered, workers=1))
    sequential = s3.S3Collector().collect(FakeSession(), "global")
    assert parallel.resources == sequential.resources and parallel.denials == sequential.denials
