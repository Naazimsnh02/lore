"""Branch Documentary Manager — manages nested sub-topic exploration.

Design reference: LORE design.md, Branch Documentary section.
Requirements:
  13.1 — Detect branch requests (handled upstream by ConversationManager)
  13.2 — Create Branch_Documentary
  13.3 — Maintain independent stream while preserving parent context
  13.4 — Support nesting up to 3 levels
  13.5 — Return to parent documentary context
  13.6 — Record branching structure in Session Memory

Architecture notes
------------------
The BranchDocumentaryManager sits between the ConversationManager (which
detects branch intents) and the Orchestrator (which generates content).
It owns the branch navigation stack and coordinates with SessionMemoryManager
to persist BranchNode records in Firestore.

The manager is instantiated per-session so each user has an independent
branch stack.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from .models import (
    MAX_BRANCH_DEPTH,
    BranchDepthExceeded,
    BranchDocumentary,
    BranchDocumentaryContext,
    BranchStackEntry,
    NoBranchToReturn,
)

logger = logging.getLogger(__name__)


class BranchDocumentaryManager:
    """Manages branch documentary creation, navigation, and persistence.

    Parameters
    ----------
    session_id:
        Current session identifier.
    user_id:
        Current user identifier.
    session_memory:
        SessionMemoryManager instance for persisting branch structure (Task 3).
        Optional — when absent, branch nodes are tracked in memory only.
    orchestrator:
        DocumentaryOrchestrator instance for generating branch content (Task 12).
        Optional — when absent, ``create_branch`` returns a BranchDocumentary
        with ``stream=None``.  The caller is responsible for generating content.
    max_depth:
        Maximum branch nesting depth (default: 3, per Req 13.4).
    """

    def __init__(
        self,
        *,
        session_id: str = "",
        user_id: str = "",
        session_memory: Any = None,
        orchestrator: Any = None,
        max_depth: int = MAX_BRANCH_DEPTH,
    ) -> None:
        self._session_id = session_id
        self._user_id = user_id
        self._session_memory = session_memory
        self._orchestrator = orchestrator
        self._max_depth = max_depth
        self._branch_stack: list[BranchStackEntry] = []
        # Root context info for returning to main documentary
        self._root_topic: Optional[str] = None
        self._root_mode: str = "voice"
        self._root_language: str = "en"
        self._root_depth_dial: str = "explorer"

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def current_depth(self) -> int:
        """Current branch nesting depth (0 = at root)."""
        return len(self._branch_stack)

    @property
    def max_depth(self) -> int:
        """Maximum allowed branch depth."""
        return self._max_depth

    @property
    def is_at_root(self) -> bool:
        """True when not inside any branch."""
        return len(self._branch_stack) == 0

    @property
    def current_branch_id(self) -> Optional[str]:
        """ID of the current (innermost) branch, or None at root."""
        if self._branch_stack:
            return self._branch_stack[-1].branch_id
        return None

    @property
    def current_topic(self) -> Optional[str]:
        """Topic of the current (innermost) branch, or root topic."""
        if self._branch_stack:
            return self._branch_stack[-1].topic
        return self._root_topic

    # ── Configuration ────────────────────────────────────────────────────────

    def set_root_context(
        self,
        *,
        topic: str,
        mode: str = "voice",
        language: str = "en",
        depth_dial: str = "explorer",
    ) -> None:
        """Set the root documentary context for return navigation."""
        self._root_topic = topic
        self._root_mode = mode
        self._root_language = language
        self._root_depth_dial = depth_dial

    # ── Branch creation (Req 13.2) ───────────────────────────────────────────

    async def create_branch(
        self,
        branch_topic: str,
        *,
        stream_position: float = 0.0,
        mode: Optional[str] = None,
        language: Optional[str] = None,
        depth_dial: Optional[str] = None,
    ) -> BranchDocumentary:
        """Create a new branch documentary for *branch_topic*.

        Raises ``BranchDepthExceeded`` if the current depth equals the
        maximum (Req 13.4).

        Steps:
          1. Enforce depth limit.
          2. Build branch context (inheriting parent settings).
          3. Push onto navigation stack.
          4. Persist BranchNode in Session Memory (Req 13.6).
          5. Optionally generate content via Orchestrator.
          6. Return BranchDocumentary result.
        """
        # 1. Depth check (Req 13.4)
        if self.current_depth >= self._max_depth:
            raise BranchDepthExceeded(
                f"Maximum branch depth of {self._max_depth} reached "
                f"(current depth: {self.current_depth})"
            )

        # 2. Build context
        branch_id = uuid.uuid4().hex[:16]
        parent_branch_id = self.current_branch_id
        new_depth = self.current_depth + 1

        # Inherit from parent or root
        effective_mode = mode or (
            self._branch_stack[-1].topic if self._branch_stack else self._root_mode
        )
        # Correct: inherit mode string, not parent topic
        effective_mode = mode or self._root_mode
        effective_language = language or self._root_language
        effective_depth_dial = depth_dial or self._root_depth_dial

        # Collect previous topics chain for context
        previous_topics = self.get_branch_path()

        context = BranchDocumentaryContext(
            branch_id=branch_id,
            parent_branch_id=parent_branch_id,
            topic=branch_topic,
            depth=new_depth,
            mode=effective_mode,
            language=effective_language,
            depth_dial=effective_depth_dial,
            session_id=self._session_id,
            user_id=self._user_id,
            previous_topics=previous_topics,
        )

        # 3. Push to stack (Req 13.3 — preserve parent context on stack)
        stack_entry = BranchStackEntry(
            branch_id=branch_id,
            parent_branch_id=parent_branch_id,
            topic=branch_topic,
            depth=new_depth,
            stream_position=stream_position,
        )
        self._branch_stack.append(stack_entry)

        logger.info(
            "Created branch '%s' (id=%s, depth=%d, parent=%s) in session %s",
            branch_topic,
            branch_id,
            new_depth,
            parent_branch_id or "root",
            self._session_id,
        )

        # 4. Persist in Session Memory (Req 13.6)
        await self._persist_branch_node(context)

        # 5. Generate content if orchestrator available
        stream = None
        if self._orchestrator is not None:
            try:
                from ..orchestrator.models import DocumentaryRequest, Mode

                request = DocumentaryRequest(
                    user_id=self._user_id,
                    session_id=self._session_id,
                    mode=Mode(effective_mode),
                    branch_topic=branch_topic,
                    branch_parent_id=parent_branch_id,
                    previous_topics=previous_topics + [branch_topic],
                    language=effective_language,
                    depth_dial=effective_depth_dial,
                )
                stream = await self._orchestrator.branch_documentary_workflow(request)
            except Exception:
                logger.exception(
                    "Failed to generate branch documentary for '%s'", branch_topic
                )
                # Graceful degradation — return branch without stream

        # 6. Return result
        return BranchDocumentary(
            branch_id=branch_id,
            context=context,
            stream=stream,
        )

    # ── Return to parent (Req 13.5) ──────────────────────────────────────────

    async def return_to_parent(self) -> BranchStackEntry:
        """Pop the current branch and return the parent stack entry.

        Raises ``NoBranchToReturn`` if already at root.

        Returns the popped branch entry so the caller can use its
        ``stream_position`` to resume the parent documentary.
        """
        if self.is_at_root:
            raise NoBranchToReturn("Already at root documentary — no branch to return from")

        popped = self._branch_stack.pop()

        # Persist end time for the closed branch
        await self._close_branch_node(popped.branch_id)

        logger.info(
            "Returned from branch '%s' (depth=%d) → depth now %d in session %s",
            popped.topic,
            popped.depth,
            self.current_depth,
            self._session_id,
        )

        return popped

    # ── Navigation helpers ───────────────────────────────────────────────────

    def get_branch_path(self) -> list[str]:
        """Return the current branch path as a list of topic strings.

        Useful for breadcrumb display and context passing.
        """
        path: list[str] = []
        if self._root_topic:
            path.append(self._root_topic)
        path.extend(entry.topic for entry in self._branch_stack)
        return path

    def get_branch_stack(self) -> list[BranchStackEntry]:
        """Return a copy of the current branch stack."""
        return list(self._branch_stack)

    def get_parent_topic(self) -> Optional[str]:
        """Return the parent branch topic, or root topic if at depth 1."""
        if len(self._branch_stack) >= 2:
            return self._branch_stack[-2].topic
        return self._root_topic

    def can_branch(self) -> bool:
        """Check if a new branch can be created at the current depth."""
        return self.current_depth < self._max_depth

    def reset(self) -> None:
        """Clear all branch state (e.g. on session end)."""
        self._branch_stack.clear()
        self._root_topic = None

    # ── Session Memory persistence (Req 13.6) ────────────────────────────────

    async def _persist_branch_node(self, context: BranchDocumentaryContext) -> None:
        """Store a BranchNode in Firestore via SessionMemoryManager."""
        if self._session_memory is None:
            return

        try:
            from ..session_memory.models import BranchNode

            node = BranchNode(
                branch_id=context.branch_id,
                parent_branch_id=context.parent_branch_id,
                topic=context.topic,
                depth=context.depth,
            )
            await self._session_memory.add_branch_node(self._session_id, node)
        except Exception:
            logger.exception(
                "Failed to persist branch node '%s' to session %s",
                context.branch_id,
                self._session_id,
            )

    async def _close_branch_node(self, branch_id: str) -> None:
        """Update the branch node's end time in Firestore."""
        if self._session_memory is None:
            return

        try:
            import time

            # Load session, find the branch, update end_time_ms
            session = await self._session_memory.load_session(self._session_id)
            if session is None:
                return

            for branch in session.branch_structure:
                if branch.branch_id == branch_id:
                    branch.end_time_ms = int(time.time() * 1000)
                    break

            await self._session_memory.update_session(
                self._session_id,
                branch_structure=[
                    b.model_dump(mode="json") for b in session.branch_structure
                ],
            )
        except Exception:
            logger.exception(
                "Failed to close branch node '%s' in session %s",
                branch_id,
                self._session_id,
            )
