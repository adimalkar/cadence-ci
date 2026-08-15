from __future__ import annotations

from cadence.logstore import LocalLogStore


class TestLocalLogStore:
    def test_put_then_get_roundtrips(self, tmp_path):
        store = LocalLogStore(tmp_path)
        data = b"##[group]Run npm ci\n" * 500
        result = store.put(data)
        assert not result.already_stored
        assert store.get(result.storage_key) == data

    def test_identical_content_is_never_written_twice(self, tmp_path):
        # Content-addressing means a job re-fetched (e.g. after a retry, or a second
        # ingest pass) is a guaranteed no-op on the second write, not a duplicate copy.
        store = LocalLogStore(tmp_path)
        data = b"same log content"
        first = store.put(data)
        second = store.put(data)
        assert first.storage_key == second.storage_key
        assert not first.already_stored
        assert second.already_stored

    def test_storage_key_is_content_addressed(self, tmp_path):
        store = LocalLogStore(tmp_path)
        result = store.put(b"hello")
        assert result.storage_key == f"{result.sha256[:2]}/{result.sha256}.log.gz"

    def test_stored_bytes_are_gzipped(self, tmp_path):
        # CI logs compress ~20:1 -- verify we're actually gzipping, not just renaming
        # files, since that ratio is the entire storage-cost assumption in the plan.
        store = LocalLogStore(tmp_path)
        data = b"x" * 100_000  # highly compressible
        result = store.put(data)
        on_disk = (tmp_path / result.storage_key).read_bytes()
        assert on_disk[:2] == b"\x1f\x8b"  # gzip magic bytes
        assert len(on_disk) < len(data) / 10

    def test_different_content_gets_different_keys(self, tmp_path):
        store = LocalLogStore(tmp_path)
        a = store.put(b"log A")
        b = store.put(b"log B")
        assert a.storage_key != b.storage_key
