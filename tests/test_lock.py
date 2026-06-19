import os
import time

import pytest

from solvent.ops.lock import SingleWriterLock


def test_single_writer_lock_refuses_overlap(tmp_path):
    lock_path = tmp_path / "writer.lock"
    with SingleWriterLock(lock_path):
        with pytest.raises(RuntimeError, match="state writer already active"):
            with SingleWriterLock(lock_path):
                pass


def test_single_writer_lock_removes_stale_lock(tmp_path):
    lock_path = tmp_path / "writer.lock"
    lock_path.write_text("{}")
    old = time.time() - 3600
    os.utime(lock_path, (old, old))

    with SingleWriterLock(lock_path, stale_after_s=1):
        assert lock_path.exists()

    assert not lock_path.exists()
