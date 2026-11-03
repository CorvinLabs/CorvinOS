"""AtomicSymlinkManager must work where symlinks are unavailable (Windows w/o privilege)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from concurrency_model import AtomicSymlinkManager  # noqa: E402


def test_update_twice_and_read_back(tmp_path):
    (tmp_path / "p.v1.json").write_text('{"v":1}', encoding="utf-8")
    (tmp_path / "p.v2.json").write_text('{"v":2}', encoding="utf-8")
    assert AtomicSymlinkManager.atomic_symlink_update(tmp_path, "p", "1")
    assert AtomicSymlinkManager.get_current_profile_version(tmp_path, "p") == "1"
    # second update replaces an existing target (os.rename raised FileExistsError on Windows)
    assert AtomicSymlinkManager.atomic_symlink_update(tmp_path, "p", "2")
    assert AtomicSymlinkManager.get_current_profile_version(tmp_path, "p") == "2"
    assert (tmp_path / "p.json").read_text(encoding="utf-8") == '{"v":2}'
