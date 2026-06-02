import pytest

from api.workspace import list_dir, read_file_content, safe_resolve_ws


def test_safe_resolve_blocks_external_symlink_directory(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("outside", encoding="utf-8")
    (workspace / "escape").symlink_to(outside)

    with pytest.raises(ValueError, match="Path traversal blocked"):
        safe_resolve_ws(workspace, "escape")

    with pytest.raises(ValueError, match="Path traversal blocked"):
        list_dir(workspace, "escape")

    assert "escape" not in {entry["name"] for entry in list_dir(workspace, ".")}


def test_read_file_blocks_external_symlink_file(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("outside", encoding="utf-8")
    (workspace / "secret-link.txt").symlink_to(outside / "secret.txt")

    with pytest.raises(ValueError, match="Path traversal blocked"):
        read_file_content(workspace, "secret-link.txt")

    assert "secret-link.txt" not in {entry["name"] for entry in list_dir(workspace, ".")}


def test_internal_symlink_still_resolves_within_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    nested = workspace / "nested"
    nested.mkdir()
    (nested / "inside.txt").write_text("inside", encoding="utf-8")
    (workspace / "inside-link.txt").symlink_to(nested / "inside.txt")

    resolved = safe_resolve_ws(workspace, "inside-link.txt")

    assert resolved == (nested / "inside.txt").resolve()
    assert read_file_content(workspace, "inside-link.txt")["content"] == "inside"
    assert "inside-link.txt" in {entry["name"] for entry in list_dir(workspace, ".")}


# ── TOCTOU hardening (#3398): a path that passes safe_resolve_ws() but is then
#    swapped to an external symlink before the open must not read/list/write
#    outside the workspace. The read/list/write paths use a portable anchored
#    openat-walk (openat + O_NOFOLLOW per component, dir_fd where supported). ──


def test_read_file_toctou_swap_to_external_symlink_blocked(tmp_path, monkeypatch):
    """If the resolved path is swapped to an external symlink AFTER the
    safe_resolve_ws() check, read_file_content must refuse, not follow the
    symlink and leak external content."""
    import api.workspace as w

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "data.txt").write_text("LEGIT", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("SECRET-LEAK", encoding="utf-8")

    real_resolve = w.safe_resolve_ws

    def racing_resolve(root, rel):
        p = real_resolve(root, rel)
        if rel == "data.txt":
            try:
                p.unlink()
            except OSError:
                pass
            p.symlink_to(outside / "secret.txt")
        return p

    monkeypatch.setattr(w, "safe_resolve_ws", racing_resolve)
    try:
        result = w.read_file_content(workspace, "data.txt")
        assert "SECRET" not in result["content"], "TOCTOU symlink swap leaked external content"
    except (FileNotFoundError, ValueError):
        pass  # refused — the correct outcome


def test_list_dir_toctou_swap_to_external_symlink_blocked(tmp_path, monkeypatch):
    """If a checked directory path is swapped to an external symlink after
    safe_resolve_ws(), list_dir must refuse rather than enumerate the external
    directory."""
    import api.workspace as w

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sub").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x", encoding="utf-8")

    real_resolve = w.safe_resolve_ws

    def racing_resolve(root, rel):
        p = real_resolve(root, rel)
        if rel == "sub":
            try:
                p.rmdir()
            except OSError:
                pass
            p.symlink_to(outside)
        return p

    monkeypatch.setattr(w, "safe_resolve_ws", racing_resolve)
    try:
        entries = w.list_dir(workspace, "sub")
        names = {e["name"] for e in entries}
        assert "secret.txt" not in names, "TOCTOU symlink swap leaked external dir listing"
    except (FileNotFoundError, ValueError):
        pass  # refused — the correct outcome


def test_anchored_create_blocks_symlinked_component(tmp_path):
    """open_anchored_create_fd must refuse to write through a symlinked path
    component (the upload / archive-extraction write race), landing nothing
    outside the workspace."""
    from api.workspace import open_anchored_create_fd

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "evil").symlink_to(outside)  # symlinked intermediate dir

    with pytest.raises((FileNotFoundError, ValueError, OSError)):
        open_anchored_create_fd(workspace, (workspace / "evil" / "pwned.txt"))
    assert not (outside / "pwned.txt").exists()


def test_anchored_create_no_fd_leak_on_rejection(tmp_path):
    """Repeated rejected anchored creates must not leak file descriptors."""
    import os

    from api.workspace import open_anchored_create_fd

    if not os.path.isdir("/proc/self/fd"):
        pytest.skip("fd-count check requires /proc/self/fd")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "evil").symlink_to(outside)

    before = len(os.listdir("/proc/self/fd"))
    for _ in range(200):
        try:
            open_anchored_create_fd(workspace, (workspace / "evil" / "x.txt"))
        except Exception:
            pass
    after = len(os.listdir("/proc/self/fd"))
    assert after <= before + 2, f"fd leak: before={before} after={after}"


def test_anchored_create_nested_autocreates_dirs(tmp_path):
    """A normal (non-escaping) nested create works and lands under the workspace."""
    import os

    from api.workspace import open_anchored_create_fd

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fd = open_anchored_create_fd(workspace, workspace / "a" / "b" / "file.txt")
    os.write(fd, b"hello")
    os.close(fd)
    assert (workspace / "a" / "b" / "file.txt").read_text() == "hello"
