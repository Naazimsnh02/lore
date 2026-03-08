"""Mode Switch Manager — orchestrates mode transitions with content preservation.

Design reference: LORE design.md, Section 1 – Core Mode Selection.
Requirements:
  1.6 — THE LORE_System SHALL allow mode switching during an active session.
  1.7 — WHEN mode switching occurs, THE Session_Memory SHALL preserve all
        previously generated content.

Architecture notes
------------------
The ModeSwitchManager is a stateless coordinator.  It:

1. Validates the requested transition (currently all are valid).
2. Snapshots all content generated so far from session memory.
3. Records the mode switch as a UserInteraction in session memory.
4. Updates the session's active mode in Firestore.
5. Returns a ``ModeSwitchResult`` the caller can forward to the client.

The manager never deletes or modifies existing content — it only reads and
records.  This guarantees that Property 1 (Mode Transition Content
Preservation) holds: all narration, video, illustrations, and facts remain
accessible after any mode switch.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from .models import (
    ModeSwitchContext,
    ModeSwitchError,
    ModeSwitchRecord,
    ModeSwitchResult,
    PreservedContent,
    SwitchableMode,
)

logger = logging.getLogger(__name__)


class ModeSwitchManager:
    """Manages mode transitions during active documentary sessions.

    Parameters
    ----------
    session_memory:
        SessionMemoryManager instance for reading/persisting session data.
        When ``None``, the manager still works but skips persistence
        (useful for unit testing without Firestore).
    """

    def __init__(self, session_memory: Any = None) -> None:
        self._session_memory = session_memory
        # Per-session history of mode switches (in-memory cache)
        self._switch_history: dict[str, list[ModeSwitchRecord]] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    async def switch_mode(
        self,
        *,
        session_id: str,
        user_id: str,
        from_mode: SwitchableMode,
        to_mode: SwitchableMode,
        depth_dial: str = "explorer",
        language: str = "en",
    ) -> ModeSwitchResult:
        """Execute a mode transition with full content preservation.

        This is the main entry point.  It:
        1. Validates the transition.
        2. Snapshots preserved content from session memory.
        3. Records the interaction in session memory.
        4. Updates the session mode in Firestore.

        Returns
        -------
        ModeSwitchResult
            Contains the switch ID, preserved content snapshot, and a
            human-readable transition message.

        Raises
        ------
        ModeSwitchError
            If the transition is invalid or persistence fails fatally.
        """
        if not self.validate_transition(from_mode, to_mode):
            raise ModeSwitchError(
                f"Invalid mode transition: {from_mode.value} → {to_mode.value}"
            )

        logger.info(
            "Mode switch: %s → %s (session=%s, user=%s)",
            from_mode.value,
            to_mode.value,
            session_id,
            user_id,
        )

        # Step 1: Snapshot existing content (Req 1.7)
        preserved = await self._snapshot_content(session_id)

        # Step 2: Create the switch record
        record = ModeSwitchRecord(
            from_mode=from_mode,
            to_mode=to_mode,
            preserved=preserved,
            session_id=session_id,
        )

        # Step 3: Persist interaction + update session mode (best-effort)
        await self._persist_switch(session_id, record)

        # Step 4: Cache locally
        self._switch_history.setdefault(session_id, []).append(record)

        message = self._build_transition_message(from_mode, to_mode, preserved)

        result = ModeSwitchResult(
            switch_id=record.switch_id,
            from_mode=from_mode,
            to_mode=to_mode,
            preserved=preserved,
            session_id=session_id,
            transition_message=message,
        )

        logger.info(
            "Mode switch %s completed: %s → %s, preserved %d items",
            record.switch_id,
            from_mode.value,
            to_mode.value,
            len(preserved.content_ids),
        )

        return result

    def validate_transition(
        self, from_mode: SwitchableMode, to_mode: SwitchableMode
    ) -> bool:
        """Check whether a mode transition is valid.

        All transitions are valid in LORE's design (Req 1.6).
        This method exists as a hook for future constraints.
        """
        return True

    def get_switch_history(self, session_id: str) -> list[ModeSwitchRecord]:
        """Return all mode switches recorded for a session."""
        return list(self._switch_history.get(session_id, []))

    def get_current_mode(
        self, session_id: str, default: SwitchableMode = SwitchableMode.SIGHT
    ) -> SwitchableMode:
        """Return the current mode based on switch history.

        If no switches have been recorded, returns *default*.
        """
        history = self._switch_history.get(session_id, [])
        if history:
            return history[-1].to_mode
        return default

    def reset(self, session_id: str) -> None:
        """Clear in-memory switch history for a session."""
        self._switch_history.pop(session_id, None)

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _snapshot_content(self, session_id: str) -> PreservedContent:
        """Read session memory and count all generated content.

        When session memory is unavailable, returns an empty snapshot
        (graceful degradation).
        """
        if self._session_memory is None:
            return PreservedContent()

        try:
            session = await self._session_memory.load_session(session_id)
        except Exception as exc:
            logger.warning(
                "Failed to load session %s for content snapshot: %s",
                session_id,
                exc,
            )
            return PreservedContent()

        content_ids: list[str] = []
        narration_count = 0
        illustration_count = 0
        video_count = 0
        fact_count = 0
        total_duration = 0.0

        # Count content from the session's content_refs
        for ref in getattr(session, "content_refs", []):
            content_ids.append(ref.get("content_id", "") if isinstance(ref, dict) else getattr(ref, "content_id", ""))
            content_type = ref.get("content_type", "") if isinstance(ref, dict) else getattr(ref, "content_type", "")
            if content_type == "narration":
                narration_count += 1
            elif content_type == "illustration":
                illustration_count += 1
            elif content_type == "video":
                video_count += 1
            elif content_type == "fact":
                fact_count += 1

        # Also count from content_counts if available
        counts = getattr(session, "content_counts", None)
        if counts is not None:
            if isinstance(counts, dict):
                narration_count = max(narration_count, counts.get("narration_segments", 0))
                illustration_count = max(illustration_count, counts.get("illustrations", 0))
                video_count = max(video_count, counts.get("video_clips", 0))
                fact_count = max(fact_count, counts.get("facts", 0))
            else:
                narration_count = max(narration_count, getattr(counts, "narration_segments", 0))
                illustration_count = max(illustration_count, getattr(counts, "illustrations", 0))
                video_count = max(video_count, getattr(counts, "video_clips", 0))
                fact_count = max(fact_count, getattr(counts, "facts", 0))

        total_duration = getattr(session, "total_duration_seconds", 0.0) or 0.0

        branch_ids = [
            b.get("branch_id", "") if isinstance(b, dict) else getattr(b, "branch_id", "")
            for b in getattr(session, "branches", [])
        ]

        return PreservedContent(
            narration_count=narration_count,
            illustration_count=illustration_count,
            video_count=video_count,
            fact_count=fact_count,
            content_ids=[cid for cid in content_ids if cid],
            branch_ids=[bid for bid in branch_ids if bid],
            total_duration_seconds=total_duration,
        )

    async def _persist_switch(
        self, session_id: str, record: ModeSwitchRecord
    ) -> None:
        """Record the mode switch in session memory (best-effort).

        - Adds a UserInteraction with type MODE_SWITCH.
        - Updates the session's active mode.

        Failures are logged but do not prevent the switch from succeeding
        (graceful degradation).
        """
        if self._session_memory is None:
            return

        # Record the interaction
        try:
            from ..session_memory.models import InteractionType, UserInteraction

            interaction = UserInteraction(
                interaction_type=InteractionType.MODE_SWITCH,
                input=f"Switch from {record.from_mode.value} to {record.to_mode.value}",
                response=(
                    f"Mode switched. Preserved {record.preserved.narration_count} narrations, "
                    f"{record.preserved.illustration_count} illustrations, "
                    f"{record.preserved.video_count} videos, "
                    f"{record.preserved.fact_count} facts."
                ),
                processing_time_ms=0,
            )
            await self._session_memory.add_interaction(session_id, interaction)
        except Exception as exc:
            logger.warning(
                "Failed to record mode switch interaction for session %s: %s",
                session_id,
                exc,
            )

        # Update the session mode
        try:
            from ..session_memory.models import OperatingMode

            new_mode = OperatingMode(record.to_mode.value)
            await self._session_memory.update_session(session_id, mode=new_mode)
        except Exception as exc:
            logger.warning(
                "Failed to update session mode for %s: %s",
                session_id,
                exc,
            )

    @staticmethod
    def _build_transition_message(
        from_mode: SwitchableMode,
        to_mode: SwitchableMode,
        preserved: PreservedContent,
    ) -> str:
        """Build a human-readable transition message for the client."""
        total = (
            preserved.narration_count
            + preserved.illustration_count
            + preserved.video_count
            + preserved.fact_count
        )

        mode_labels = {
            SwitchableMode.SIGHT: "SightMode (camera)",
            SwitchableMode.VOICE: "VoiceMode (voice)",
            SwitchableMode.LORE: "LoreMode (camera + voice)",
        }

        from_label = mode_labels.get(from_mode, from_mode.value)
        to_label = mode_labels.get(to_mode, to_mode.value)

        if total > 0:
            return (
                f"Switching from {from_label} to {to_label}. "
                f"All {total} content items have been preserved."
            )
        return f"Switching from {from_label} to {to_label}."
