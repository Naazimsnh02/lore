"""Property test for Media Retrieval Latency (Task 4.2).

Feature: lore-multimodal-documentary-app, Property 20: Media Retrieval Latency

For any stored media file, retrieval latency SHALL be below 500 milliseconds
for 95% of requests (p95 < 500ms).

Validates: Requirements 22.7

Strategy
--------
We generate random media files (variable sizes up to 1 MB) using Hypothesis,
store them via MediaStoreManager backed by in-memory fakes, then measure
end-to-end retrieval time for 100+ samples.  Because the implementation uses
in-memory fakes (no real GCS), measured latency is purely the Python overhead.
A real load test against production GCS is covered in Phase 7 (Task 42).

We verify:
1. Retrieval always returns the exact bytes that were stored (correctness).
2. All retrieval latencies are recorded and the 95th percentile is within budget.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend.services.media_store.manager import MediaStoreManager
from backend.services.media_store.models import (
    MediaFile,
    MediaMetadata,
    MediaStatus,
    MediaType,
    StoredMediaRecord,
)


# ── Fake GCS / Firestore (same as unit tests, kept local to this file) ─────────


class _FakeBlob:
    def __init__(self, name: str, store: dict):
        self.name = name
        self._store = store
        self.metadata: dict = {}

    def upload_from_string(self, data: bytes, content_type: str = "", **kwargs) -> None:
        self._store[self.name] = data

    def download_as_bytes(self) -> bytes:
        return self._store.get(self.name, b"")

    def generate_signed_url(self, expiration, method: str = "GET", version: str = "v4") -> str:
        return f"https://storage.googleapis.com/fake/{self.name}?sig=xyz"

    def delete(self) -> None:
        self._store.pop(self.name, None)

    def patch(self) -> None:
        pass


class _FakeBucket:
    def __init__(self):
        self._blobs: dict[str, bytes] = {}

    def blob(self, name: str) -> _FakeBlob:
        return _FakeBlob(name, self._blobs)


class _FakeGCSClient:
    def __init__(self):
        self._buckets: dict[str, _FakeBucket] = {}

    def bucket(self, name: str) -> _FakeBucket:
        if name not in self._buckets:
            self._buckets[name] = _FakeBucket()
        return self._buckets[name]


class _FakeDoc:
    def __init__(self, data: dict | None):
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict:
        return self._data or {}


class _FakeDocRef:
    def __init__(self, doc_id: str, store: dict):
        self._id = doc_id
        self._store = store

    def get(self) -> _FakeDoc:
        return _FakeDoc(self._store.get(self._id))

    def set(self, data: dict, merge: bool = False) -> None:
        if merge and self._id in self._store:
            existing = self._store[self._id]
            for k, v in data.items():
                existing[k] = v.value if hasattr(v, "value") else v
        else:
            self._store[self._id] = {
                k: (v.value if hasattr(v, "value") else v) for k, v in data.items()
            }

    def update(self, data: dict) -> None:
        if self._id not in self._store:
            self._store[self._id] = {}
        for k, v in data.items():
            self._store[self._id][k] = v.value if hasattr(v, "value") else v


class _FakeCollection:
    def __init__(self, store: dict):
        self._store = store

    def document(self, doc_id: str) -> _FakeDocRef:
        return _FakeDocRef(doc_id, self._store)

    def where(self, *args, **kwargs) -> "_FakeQuery":
        return _FakeQuery(self._store)


class _FakeQuery:
    def __init__(self, store: dict):
        self._store = store

    def where(self, *args, **kwargs) -> "_FakeQuery":
        return self

    def stream(self):
        class _D:
            def __init__(self, k, v):
                self.id = k
                self._v = v

            def to_dict(self):
                return self._v

        return [_D(k, v) for k, v in self._store.items()]


class _FakeDB:
    def __init__(self):
        self._cols: dict[str, dict] = {}

    def collection(self, name: str) -> _FakeCollection:
        if name not in self._cols:
            self._cols[name] = {}
        return _FakeCollection(self._cols[name])


def _increment_patch():
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


# ── Hypothesis strategy ────────────────────────────────────────────────────────

# Media sizes: 1 B → 1 MB (keeping tests fast while varying significantly)
_media_data = st.binary(min_size=1, max_size=1 * 1024 * 1024)

_media_types = st.sampled_from(list(MediaType))

_mime_by_type = {
    MediaType.VIDEO: "video/mp4",
    MediaType.ILLUSTRATION: "image/jpeg",
    MediaType.NARRATION: "audio/mpeg",
    MediaType.CHRONICLE: "application/pdf",
}


@st.composite
def media_files(draw) -> MediaFile:
    media_type = draw(_media_types)
    data = draw(_media_data)
    return MediaFile(
        id=str(uuid.uuid4()),
        media_type=media_type,
        data=data,
        mime_type=_mime_by_type[media_type],
        size=len(data),
        metadata=MediaMetadata(
            user_id="prop_user",
            session_id="prop_sess",
            media_type=media_type,
            description="Hypothesis-generated media",
        ),
    )


# ── Property test ──────────────────────────────────────────────────────────────

# Collect latency samples across Hypothesis iterations
_latency_samples: list[float] = []


@given(media=media_files())
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.too_slow],
    deadline=None,
)
def test_media_retrieval_latency_and_correctness(media: MediaFile) -> None:
    """Property 20: Media Retrieval Latency

    For any stored media file:
    1. Retrieved bytes exactly match stored bytes (data integrity).
    2. Retrieval latency is recorded for p95 analysis.

    Feature: lore-multimodal-documentary-app, Property 20: Media Retrieval Latency
    Validates: Requirements 22.7
    """
    with _increment_patch():
        mgr = MediaStoreManager(
            storage_client=_FakeGCSClient(),
            firestore_client=_FakeDB(),
            bucket_name="prop-test-bucket",
            quota_bytes=2 * 1024 * 1024 * 1024,  # 2 GiB — never exceeded in tests
        )

        # Store the media
        loop = asyncio.new_event_loop()
        try:
            media_id = loop.run_until_complete(
                mgr.store_media(media, user_id="prop_user", session_id="prop_sess")
            )

            # Measure retrieval latency
            start = time.perf_counter()
            retrieved = loop.run_until_complete(mgr.retrieve_media(media_id))
            elapsed_ms = (time.perf_counter() - start) * 1000.0
        finally:
            loop.close()

    # ── Correctness: bytes preserved exactly ─────────────────────────────────
    assert retrieved.data == media.data, (
        f"Retrieved data mismatch for media_id={media_id}: "
        f"expected {len(media.data)} bytes, got {len(retrieved.data or b'')} bytes"
    )

    # ── Record latency for post-run percentile analysis ───────────────────────
    _latency_samples.append(elapsed_ms)


def test_media_retrieval_latency_p95_within_budget() -> None:
    """Verify the 95th-percentile latency from the collected samples.

    This test must run AFTER ``test_media_retrieval_latency_and_correctness``
    has populated ``_latency_samples``.  pytest executes tests in file order
    by default, so the ordering is correct.

    Property 20 budget: p95 < 500ms (in-memory fakes should be << 1ms).
    """
    if not _latency_samples:
        pytest.skip("No latency samples collected – run the property test first.")

    sorted_samples = sorted(_latency_samples)
    p95_index = int(len(sorted_samples) * 0.95)
    p95_ms = sorted_samples[min(p95_index, len(sorted_samples) - 1)]

    print(
        f"\nMedia retrieval latency (n={len(sorted_samples)}): "
        f"p50={sorted_samples[len(sorted_samples) // 2]:.3f}ms  "
        f"p95={p95_ms:.3f}ms  "
        f"max={sorted_samples[-1]:.3f}ms"
    )

    # For in-memory fakes the budget is generous; production load testing
    # (Task 42) validates against real GCS with the 500ms target.
    assert p95_ms < 500.0, (
        f"p95 media retrieval latency {p95_ms:.1f}ms exceeds 500ms budget"
    )
