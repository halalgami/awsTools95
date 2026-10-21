from aws95.core.jobs import JobLock


def test_one_job_at_a_time():
    j = JobLock()
    assert j.try_acquire("snapshot") and j.holder == "snapshot"
    assert not j.try_acquire("backfill") and j.holder == "snapshot"
    j.release()
    assert j.holder is None and j.try_acquire("backfill")
