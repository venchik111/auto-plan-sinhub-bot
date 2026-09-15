import asyncio

from app.review.queue import ReviewQueue

WEEK = "14.09-20.09"


def test_group_is_processed_in_order_with_progress() -> None:
    calls = []

    async def worker(student, week):
        calls.append((student, week))

    async def scenario():
        queue = ReviewQueue(worker, autostart=False)
        assert queue.enqueue_group(WEEK, ["А", "Б", "В"]) == 3
        assert queue.state_of("Б", WEEK) == "queued"
        await queue.run_pending()
        return queue

    queue = asyncio.run(scenario())
    assert calls == [("А", WEEK), ("Б", WEEK), ("В", WEEK)]
    assert queue.progress() == {
        "week": WEEK, "total": 3, "done": 3, "failed": 0, "current": None, "queued": 0, "busy": False,
    }
    assert queue.state_of("Б", WEEK) is None


def test_duplicates_are_ignored_and_single_student_jumps_ahead() -> None:
    calls = []

    async def worker(student, week):
        calls.append(student)

    async def scenario():
        queue = ReviewQueue(worker, autostart=False)
        queue.enqueue_group(WEEK, ["А", "Б"])
        assert queue.enqueue_group(WEEK, ["Б", "В"]) == 1
        queue.enqueue_student("В", WEEK)
        queue.enqueue_student("Г", WEEK)
        assert queue.progress()["total"] == 4
        await queue.run_pending()

    asyncio.run(scenario())
    assert calls == ["Г", "В", "А", "Б"]


def test_failure_does_not_stop_queue() -> None:
    calls = []

    async def worker(student, week):
        calls.append(student)
        if student == "А":
            raise RuntimeError("boom")

    async def scenario():
        queue = ReviewQueue(worker, autostart=False)
        queue.enqueue_group(WEEK, ["А", "Б"])
        await queue.run_pending()
        return queue.progress()

    progress = asyncio.run(scenario())
    assert calls == ["А", "Б"]
    assert (progress["done"], progress["failed"]) == (2, 1)


def test_running_state_and_progress_reset_for_new_batch() -> None:
    seen = []
    holder = {}

    async def worker(student, week):
        seen.append(holder["queue"].state_of(student, week))

    async def scenario():
        queue = ReviewQueue(worker, autostart=False)
        holder["queue"] = queue
        queue.enqueue_group(WEEK, ["А"])
        await queue.run_pending()
        queue.enqueue_group("21.09-27.09", ["Б"])
        return queue.progress()

    progress = asyncio.run(scenario())
    assert seen == ["running"]
    assert (progress["week"], progress["total"], progress["done"], progress["busy"]) == ("21.09-27.09", 1, 0, True)


def test_autostart_worker_processes_queue() -> None:
    async def scenario():
        finished = asyncio.Event()

        async def worker(student, week):
            finished.set()

        queue = ReviewQueue(worker)
        queue.enqueue_student("А", WEEK)
        await asyncio.wait_for(finished.wait(), timeout=1)
        await queue.stop()

    asyncio.run(scenario())
