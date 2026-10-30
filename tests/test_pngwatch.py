from pathlib import Path
import shutil

import numpy as np
import pytest

from chainmark.pngio import decode_png, encode_png, load_png, sample_drawing
from chainmark.pngwatch import FolderWatcher, PNGWorkspace, WorkspaceLock, initialize, sha


@pytest.fixture
def workspace(tmp_path):
    root = initialize(tmp_path/"watch")
    value = PNGWorkspace(root)
    yield value
    value.close()


def arrive(workspace, user, origin=None, name="drawing.png"):
    path = workspace.root/user/name
    shutil.copy2(origin or workspace.root/"source.png", path)
    outcome = workspace.process(path, user)
    assert outcome["status"] == "ready"
    return path, outcome


def test_copy_and_move_preserve_abc_and_log_order(workspace):
    a, _ = arrive(workspace, "A")
    b, _ = arrive(workspace, "B", a)
    c = workspace.root/"C"/"drawing.png"
    shutil.move(str(b), str(c))
    assert not b.exists()
    assert workspace.process(c, "C")["status"] == "ready"
    found = workspace.inspect(c)
    assert found["pattern_evidence"]["detected"] == ["A", "B", "C"]
    assert found["transfer_evidence"]["recorded_order"] == ["OWNER", "A", "B", "C"]
    assert len(workspace.receipts()) == 3


def test_watcher_waits_for_copy_completion_and_ignores_own_writes(workspace):
    raw = (workspace.root/"source.png").read_bytes()
    path = workspace.root/"A"/"drawing.PNG"
    now = [0.0]
    watcher = FolderWatcher(workspace, .4, clock=lambda: now[0])
    path.write_bytes(raw[:len(raw)//2])
    assert watcher.scan_once() == []
    now[0] = .2
    path.write_bytes(raw)
    assert watcher.scan_once() == []
    now[0] = .5
    assert watcher.scan_once() == []
    now[0] = .7
    assert watcher.scan_once()[0]["status"] == "ready"
    output = path.read_bytes()
    for _ in range(4):
        now[0] += .5
        assert watcher.scan_once() == []
    assert len(workspace.receipts()) == 1
    assert path.read_bytes() == output


def test_restart_rename_and_same_input_do_not_amplify(workspace):
    a, first = arrive(workspace, "A")
    data = a.read_bytes()
    renamed = a.with_name("renamed.png")
    a.rename(renamed)
    assert workspace.process(renamed, "A")["status"] == "already_processed"
    reopened = PNGWorkspace(workspace.root)
    try:
        assert reopened.process(renamed, "A")["status"] == "already_processed"
        copy, result = arrive(reopened, "A", name="another.png")
        assert result["reused_receipt"] is True
        assert result["receipt_id"] == first["receipt_id"]
        assert copy.read_bytes() == data
        assert len(reopened.receipts()) == 1
    finally:
        reopened.close()


def test_branches_follow_parent_hash_not_global_latest_event(workspace):
    a, _ = arrive(workspace, "A")
    b, _ = arrive(workspace, "B", a)
    c, _ = arrive(workspace, "C", a)
    assert workspace.inspect(b)["transfer_evidence"]["recorded_order"] == ["OWNER", "A", "B"]
    found = workspace.inspect(c)
    assert found["transfer_evidence"]["recorded_order"] == ["OWNER", "A", "C"]
    assert found["pattern_evidence"]["detected"] == ["A", "C"]


def test_return_to_earlier_user_logs_receipt_without_reembedding(workspace):
    a, _ = arrive(workspace, "A")
    b, _ = arrive(workspace, "B", a)
    returned, result = arrive(workspace, "A", b, name="returned.png")
    assert result["pattern_added"] is False
    np.testing.assert_array_equal(load_png(returned), load_png(b))
    assert returned.read_bytes() != b.read_bytes()  # unique receipt token, same pixels
    assert workspace.inspect(returned)["transfer_evidence"]["recorded_order"] == ["OWNER", "A", "B", "A"]


def test_unknown_bytes_left_unchanged_and_no_order_invented(workspace):
    unknown = workspace.root/"B"/"unknown.png"
    pixels = sample_drawing()
    pixels[200:260, 200:260] = 64
    unknown.write_bytes(encode_png(pixels))
    before = unknown.read_bytes()
    result = workspace.process(unknown, "B")
    assert result["status"] == "unknown_source"
    assert unknown.read_bytes() == before
    assert workspace.inspect(unknown)["transfer_evidence"]["recorded_order"] is None
    assert len(workspace.receipts()) == 0


def test_copying_before_first_processing_does_not_invent_a_hop(workspace):
    raw_a = workspace.root/"A"/"too_early.png"
    shutil.copy2(workspace.root/"source.png", raw_a)
    b, _ = arrive(workspace, "B", raw_a)
    found = workspace.inspect(b)
    assert found["pattern_evidence"]["detected"] == ["B"]
    assert found["transfer_evidence"]["recorded_order"] == ["OWNER", "B"]


def test_metadata_removal_preserves_codes_but_does_not_prove_order(workspace):
    a, _ = arrive(workspace, "A")
    stripped = workspace.root/"B"/"stripped.png"
    stripped.write_bytes(encode_png(load_png(a)))
    found = workspace.inspect(stripped)
    assert found["pattern_evidence"]["detected"] == ["A"]
    assert found["transfer_evidence"]["status"] == "unregistered_bytes"
    assert workspace.process(stripped, "B")["status"] == "unknown_source"


def test_locked_publish_retries_one_prepared_receipt(workspace, monkeypatch):
    path = workspace.root/"A"/"drawing.png"
    shutil.copy2(workspace.root/"source.png", path)
    original_replace = Path.replace

    def locked_replace(self, target):
        if Path(target) == path:
            raise PermissionError("simulated viewer lock")
        return original_replace(self, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", locked_replace)
        with pytest.raises(PermissionError):
            workspace.process(path, "A")
    assert len(workspace.receipts()) == 1
    assert workspace.receipts()[0]["published"] == 0
    found = workspace.process(path, "A")
    assert found["reused_receipt"] is True
    assert len(workspace.receipts()) == 1
    assert workspace.inspect(path)["pattern_evidence"]["detected"] == ["A"]


def test_crash_after_file_replace_can_acknowledge_on_restart(workspace):
    path, receipt = arrive(workspace, "A")
    with workspace.db:
        workspace.db.execute("UPDATE receipts SET published=0 WHERE id=?", (receipt["receipt_id"],))
    assert workspace.trace(sha(path.read_bytes()))["status"] == "incomplete_or_invalid"
    result = workspace.process(path, "A")
    assert result["recovered"] is True
    assert workspace.trace(sha(path.read_bytes()))["status"] == "complete_registered_lineage"


@pytest.mark.parametrize("damage", ["missing_parent", "modified_signature"])
def test_missing_or_tampered_logs_do_not_claim_order(workspace, damage):
    a, _ = arrive(workspace, "A")
    b, _ = arrive(workspace, "B", a)
    first = workspace.receipts()[0]
    with workspace.db:
        if damage == "missing_parent":
            workspace.db.execute("DELETE FROM receipts WHERE id=?", (first["id"],))
        else:
            workspace.db.execute("UPDATE receipts SET signature='forged' WHERE id=?", (first["id"],))
    found = workspace.inspect(b)
    assert found["pattern_evidence"]["detected"] == ["A", "B"]
    assert found["transfer_evidence"]["status"] == "incomplete_or_invalid"
    assert found["transfer_evidence"]["recorded_order"] is None


def test_single_watcher_lock(workspace):
    lock = workspace.state/"watcher.lock"
    with WorkspaceLock(lock):
        with pytest.raises(RuntimeError, match="already using"):
            with WorkspaceLock(lock):
                pass
    with WorkspaceLock(lock):
        pass


def test_existing_workspace_and_outside_paths_rejected(workspace):
    with pytest.raises(FileExistsError):
        initialize(workspace.root)
    with pytest.raises(ValueError, match="directly inside"):
        workspace.process(workspace.root/"source.png", "A")


def test_png_alpha_is_composited_on_white_and_non_png_rejected():
    from io import BytesIO
    from PIL import Image
    stream = BytesIO()
    Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(stream, "PNG")
    assert np.all(decode_png(stream.getvalue()) == 255)
    with pytest.raises(ValueError, match="Expected PNG"):
        decode_png(b"%PDF-1.7")
