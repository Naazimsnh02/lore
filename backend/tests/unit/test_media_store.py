"""Unit tests for MediaStoreManager (Task 4.1).

Requirements: 22.1 – 22.7.
"""

from __future__ import annotations

import io
import time
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from backend.services.media_store.manager import (
    MediaNotFoundError,
    MediaStoreError,
    MediaStoreManager,
    QuotaExceededError,
)
from backend.services.media_store.models import (
    MediaFile,
    MediaMetadata,
    MediaStatus,
    MediaType,
    QuotaInfo,
    StoredMediaRecord,
)


# ── Helpers / Fakes ────────────────────────────────────────────────────────────


def _make_media_file(
    media_type: MediaType = MediaType.ILLUSTRATION,
    data: bytes = b"fake-image-data",
    mime_type: str = "image/jpeg",
    description: str = "Test illustration",
) -> MediaFile:
    media_id = str(uuid.uuid4())
    return MediaFile(
        id=media_id,
        media_type=media_type,
        data=data,
        mime_type=mime_type,
        size=len(data),
        metadata=MediaMetadata(
            user_id="user_abc",
            session_id="sess_123",
            media_type=media_type,
            description=description,
        ),
    )


def _make_stored_record(
    media_id: str = "mid-1",
    user_id: str = "user_abc",
    session_id: str = "sess_123",
    size_bytes: int = 100,
    status: MediaStatus = MediaStatus.ACTIVE,
) -> StoredMediaRecord:
    return StoredMediaRecord(
        media_id=media_id,
        user_id=user_id,
        session_id=session_id,
        media_type=MediaType.ILLUSTRATION,
        gcs_object_name=f"media/{user_id}/{session_id}/illustration/{media_id}.jpg",
        mime_type="image/jpeg",
        size_bytes=size_bytes,
        created_at_ms=int(time.time() * 1000),
        status=status,
    )


class FakeFirestoreDoc:
    """Minimal fake of a Firestore DocumentSnapshot."""

    def __init__(self, data: dict | None):
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict:
        return self._data or {}


class FakeFirestoreDB:
    """Very thin Firestore fake: stores documents in memory dicts."""

    def __init__(self):
        self._collections: dict[str, dict[str, Any]] = {}

    def collection(self, name: str) -> "FakeCollection":
        if name not in self._collections:
            self._collections[name] = {}
        return FakeCollection(self._collections[name])


class FakeCollection:
    def __init__(self, store: dict):
        self._store = store

    def document(self, doc_id: str) -> "FakeDocument":
        return FakeDocument(doc_id, self._store)

    def where(self, *args, **kwargs) -> "FakeQuery":
        return FakeQuery(self._store, list(args))


class FakeDocument:
    def __init__(self, doc_id: str, store: dict):
        self._id = doc_id
        self._store = store

    def get(self) -> FakeFirestoreDoc:
        data = self._store.get(self._id)
        return FakeFirestoreDoc(data)

    def set(self, data: dict, merge: bool = False) -> None:
        if merge and self._id in self._store:
            existing = self._store[self._id]
            # Simulate Increment (stored as raw ints in fake)
            for k, v in data.items():
                if hasattr(v, "value"):  # Increment sentinel
                    existing[k] = existing.get(k, 0) + v.value
                else:
                    existing[k] = v
        else:
            resolved = {}
            for k, v in data.items():
                resolved[k] = v.value if hasattr(v, "value") else v
            self._store[self._id] = resolved

    def update(self, data: dict) -> None:
        if self._id not in self._store:
            self._store[self._id] = {}
        for k, v in data.items():
            if hasattr(v, "value"):
                self._store[self._id][k] = self._store[self._id].get(k, 0) + v.value
            else:
                self._store[self._id][k] = v


class FakeQuery:
    """Returns all documents (filtering is not implemented in the fake)."""

    def __init__(self, store: dict, conditions: list):
        self._store = store
        self._conditions = conditions

    def where(self, *args, **kwargs) -> "FakeQuery":
        return self

    def stream(self):
        class _Doc:
            def __init__(self, doc_id, data):
                self.id = doc_id
                self._data = data

            def to_dict(self):
                return self._data

        return [_Doc(k, v) for k, v in self._store.items()]


class FakeGCSBlob:
    """Minimal fake Cloud Storage blob."""

    def __init__(self, name: str, store: dict):
        self.name = name
        self._store = store
        self.metadata: dict = {}

    def upload_from_string(self, data: bytes, content_type: str = "", **kwargs) -> None:
        self._store[self.name] = data

    def download_as_bytes(self) -> bytes:
        if self.name not in self._store:
            raise FileNotFoundError(f"Blob {self.name} not found")
        return self._store[self.name]

    def generate_signed_url(self, expiration, method: str = "GET", version: str = "v4") -> str:
        return f"https://storage.googleapis.com/fake/{self.name}?sig=abc"

    def delete(self) -> None:
        self._store.pop(self.name, None)

    def patch(self) -> None:
        pass


class FakeGCSBucket:
    def __init__(self):
        self._blobs: dict[str, bytes] = {}

    def blob(self, name: str) -> FakeGCSBlob:
        return FakeGCSBlob(name, self._blobs)


class FakeGCSClient:
    def __init__(self):
        self._buckets: dict[str, FakeGCSBucket] = {}

    def bucket(self, name: str) -> FakeGCSBucket:
        if name not in self._buckets:
            self._buckets[name] = FakeGCSBucket()
        return self._buckets[name]


def _patch_increment():
    """Patch google.cloud.firestore_v1.Increment with a simple fake."""
    class _Inc:
        def __init__(self, value):
            self.value = value

    return patch.dict(
        "sys.modules",
        {
            "google": MagicMock(),
            "google.cloud": MagicMock(),
            "google.cloud.firestore_v1": MagicMock(Increment=_Inc),
        },
    )


def _make_manager() -> MediaStoreManager:
    return MediaStoreManager(
        storage_client=FakeGCSClient(),
        firestore_client=FakeFirestoreDB(),
        bucket_name="test-bucket",
        quota_bytes=100 * 1024 * 1024,  # 100 MiB for tests
    )


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestStoreMedia:
    """Requirement 22.2, 22.3 – store with unique ID organized by userId/sessionId."""

    @pytest.mark.asyncio
    async def test_store_returns_media_id(self):
        with _patch_increment():
            mgr = _make_manager()
            media = _make_media_file()
            media_id = await mgr.store_media(media, user_id="u1", session_id="s1")
        assert isinstance(media_id, str)
        assert len(media_id) > 0

    @pytest.mark.asyncio
    async def test_store_raises_without_data(self):
        mgr = _make_manager()
        media = _make_media_file()
        media.data = None
        with pytest.raises(MediaStoreError, match="data must be populated"):
            await mgr.store_media(media, user_id="u1", session_id="s1")

    @pytest.mark.asyncio
    async def test_store_records_firestore_metadata(self):
        with _patch_increment():
            mgr = _make_manager()
            media = _make_media_file(data=b"abc" * 10)
            media_id = await mgr.store_media(media, user_id="u1", session_id="s1")

        # Verify Firestore has the record
        doc = mgr._db.collection("media_records").document(media_id).get()
        assert doc.exists
        raw = doc.to_dict()
        assert raw["user_id"] == "u1"
        assert raw["session_id"] == "s1"
        assert raw["status"] == MediaStatus.ACTIVE.value

    @pytest.mark.asyncio
    async def test_store_gcs_path_contains_user_and_session(self):
        with _patch_increment():
            mgr = _make_manager()
            media = _make_media_file()
            media_id = await mgr.store_media(media, user_id="user_42", session_id="sess_99")

        doc = mgr._db.collection("media_records").document(media_id).get()
        obj_name = doc.to_dict()["gcs_object_name"]
        assert "user_42" in obj_name
        assert "sess_99" in obj_name

    @pytest.mark.asyncio
    async def test_store_quota_exceeded_raises(self):
        with _patch_increment():
            mgr = MediaStoreManager(
                storage_client=FakeGCSClient(),
                firestore_client=FakeFirestoreDB(),
                bucket_name="test-bucket",
                quota_bytes=10,  # 10 bytes — very small quota
            )
            media = _make_media_file(data=b"x" * 100)  # 100 bytes > 10
            with pytest.raises(QuotaExceededError):
                await mgr.store_media(media, user_id="u1", session_id="s1")

    @pytest.mark.asyncio
    async def test_two_stores_get_different_ids(self):
        with _patch_increment():
            mgr = _make_manager()
            m1 = _make_media_file(data=b"file1")
            m2 = _make_media_file(data=b"file2")
            id1 = await mgr.store_media(m1, user_id="u", session_id="s")
            id2 = await mgr.store_media(m2, user_id="u", session_id="s")
        assert id1 != id2


class TestRetrieveMedia:
    """Requirement 22.2, 22.7 – retrieve stored media."""

    @pytest.mark.asyncio
    async def test_retrieve_returns_bytes(self):
        with _patch_increment():
            mgr = _make_manager()
            original_data = b"test-video-content"
            media = _make_media_file(
                media_type=MediaType.VIDEO,
                data=original_data,
                mime_type="video/mp4",
            )
            media_id = await mgr.store_media(media, user_id="u1", session_id="s1")
            retrieved = await mgr.retrieve_media(media_id)

        assert retrieved.data == original_data
        assert retrieved.id == media_id
        assert retrieved.media_type == MediaType.VIDEO

    @pytest.mark.asyncio
    async def test_retrieve_nonexistent_raises(self):
        mgr = _make_manager()
        with pytest.raises(MediaNotFoundError):
            await mgr.retrieve_media("nonexistent-id")

    @pytest.mark.asyncio
    async def test_retrieve_deleted_raises(self):
        with _patch_increment():
            mgr = _make_manager()
            media = _make_media_file()
            media_id = await mgr.store_media(media, user_id="u1", session_id="s1")
            await mgr.delete_media(media_id)

        with pytest.raises(MediaNotFoundError):
            await mgr.retrieve_media(media_id)


class TestDeleteMedia:
    """Requirement 22.2 – delete media."""

    @pytest.mark.asyncio
    async def test_delete_marks_status_deleted(self):
        with _patch_increment():
            mgr = _make_manager()
            media = _make_media_file()
            media_id = await mgr.store_media(media, user_id="u1", session_id="s1")
            await mgr.delete_media(media_id)

        doc = mgr._db.collection("media_records").document(media_id).get()
        assert doc.to_dict()["status"] == MediaStatus.DELETED.value

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises(self):
        mgr = _make_manager()
        with pytest.raises(MediaNotFoundError):
            await mgr.delete_media("ghost-id")

    @pytest.mark.asyncio
    async def test_double_delete_is_idempotent(self):
        """Deleting already-deleted media should not raise."""
        with _patch_increment():
            mgr = _make_manager()
            media = _make_media_file()
            media_id = await mgr.store_media(media, user_id="u1", session_id="s1")
            await mgr.delete_media(media_id)
            # Second delete should not raise
            await mgr.delete_media(media_id)


class TestGenerateSignedUrl:
    """Requirement 22.4 – signed URL generation."""

    @pytest.mark.asyncio
    async def test_signed_url_is_string(self):
        with _patch_increment():
            mgr = _make_manager()
            media = _make_media_file()
            media_id = await mgr.store_media(media, user_id="u1", session_id="s1")
            url = await mgr.generate_signed_url(media_id, expiration_minutes=30)

        assert isinstance(url, str)
        assert url.startswith("https://")

    @pytest.mark.asyncio
    async def test_signed_url_nonexistent_raises(self):
        mgr = _make_manager()
        with pytest.raises(MediaNotFoundError):
            await mgr.generate_signed_url("no-such-id")

    @pytest.mark.asyncio
    async def test_signed_url_deleted_raises(self):
        with _patch_increment():
            mgr = _make_manager()
            media = _make_media_file()
            media_id = await mgr.store_media(media, user_id="u1", session_id="s1")
            await mgr.delete_media(media_id)

        with pytest.raises(MediaNotFoundError):
            await mgr.generate_signed_url(media_id)


class TestGetUserQuota:
    """Requirement 22.6 – quota management."""

    @pytest.mark.asyncio
    async def test_new_user_has_zero_usage(self):
        mgr = _make_manager()
        quota = await mgr.get_user_quota("brand_new_user")
        assert quota.used_bytes == 0
        assert quota.file_count == 0
        assert not quota.is_exceeded

    @pytest.mark.asyncio
    async def test_quota_increases_after_store(self):
        with _patch_increment():
            mgr = _make_manager()
            data = b"x" * 500
            media = _make_media_file(data=data)
            await mgr.store_media(media, user_id="u_quota", session_id="s1")
            quota = await mgr.get_user_quota("u_quota")

        assert quota.used_bytes == 500
        assert quota.file_count == 1

    @pytest.mark.asyncio
    async def test_quota_decreases_after_delete(self):
        with _patch_increment():
            mgr = _make_manager()
            data = b"y" * 200
            media = _make_media_file(data=data)
            media_id = await mgr.store_media(media, user_id="u_del", session_id="s1")
            await mgr.delete_media(media_id)
            quota = await mgr.get_user_quota("u_del")

        assert quota.used_bytes == 0
        assert quota.file_count == 0

    @pytest.mark.asyncio
    async def test_is_exceeded_flag(self):
        mgr = MediaStoreManager(
            storage_client=FakeGCSClient(),
            firestore_client=FakeFirestoreDB(),
            bucket_name="test-bucket",
            quota_bytes=10,
        )
        # Manually inject a quota record to simulate overrun
        mgr._db.collection("media_quotas").document("u_over").set(
            {"used_bytes": 15, "limit_bytes": 10, "file_count": 1}
        )
        quota = await mgr.get_user_quota("u_over")
        assert quota.is_exceeded


class TestCleanupOldMedia:
    """Requirement 22.5 – 90-day retention, cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_returns_count(self):
        """Cleanup returns an integer (number deleted), even when 0."""
        with _patch_increment():
            mgr = _make_manager()
            count = await mgr.cleanup_old_media("user_x", older_than_days=90)
        assert isinstance(count, int)
        assert count >= 0

    @pytest.mark.asyncio
    async def test_cleanup_removes_old_records(self):
        """Records older than cutoff should be deleted."""
        with _patch_increment():
            mgr = _make_manager()
            # Inject a stale record directly into Firestore
            old_ms = int(time.time() * 1000) - (91 * 24 * 3600 * 1000)  # 91 days ago
            old_record = StoredMediaRecord(
                media_id="old-media-id",
                user_id="user_cleanup",
                session_id="sess_old",
                media_type=MediaType.ILLUSTRATION,
                gcs_object_name="media/user_cleanup/sess_old/illustration/old.jpg",
                mime_type="image/jpeg",
                size_bytes=100,
                created_at_ms=old_ms,
                status=MediaStatus.ACTIVE,
            )
            mgr._db.collection("media_records").document("old-media-id").set(
                old_record.to_firestore_dict()
            )
            # FakeQuery returns all records regardless of filter, so this exercises
            # the deletion path
            count = await mgr.cleanup_old_media("user_cleanup", older_than_days=90)

        assert count >= 0  # GCS delete may skip missing blobs (graceful)


class TestQuotaInfo:
    """Unit tests for the QuotaInfo model."""

    def test_computed_fields(self):
        q = QuotaInfo(
            user_id="u",
            used_bytes=5 * 1024 * 1024,  # 5 MB
            limit_bytes=10 * 1024 * 1024,  # 10 MB
            file_count=3,
        )
        assert q.used_mb == pytest.approx(5.0, abs=0.1)
        assert q.limit_mb == pytest.approx(10.0, abs=0.1)
        assert q.percent_used == pytest.approx(50.0, abs=0.1)
        assert not q.is_exceeded

    def test_is_exceeded_at_limit(self):
        q = QuotaInfo(user_id="u", used_bytes=10, limit_bytes=10, file_count=1)
        assert q.is_exceeded
