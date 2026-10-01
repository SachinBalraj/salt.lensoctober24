"""Module 14 - the operator's recorded decision.

The seventh step of the reconstructed event chain. A recommendation is issued
(stored in ``RECOMMENDATIONS``); this service stores the operator's disposition of
it (accepted / modified / rejected) in ``OPERATOR_DECISIONS``, one record per
recommendation, with the action the operator actually took, their note and the
moment they decided.

The module is deliberately a *record*, not a *judgement*. It never evaluates
whether the operator was right — that is what the later actual-outcome step is
for, and it stays separate. What this module guarantees instead is that the trace
does not end at the advice: a decision row exists, names the recommendation it
      answers, carries the closed-vocabulary action that was really taken (or says
      plainly that none was, in a blank acceptance), and can be rebuilt into the
      full chain for research analysis.
"""

from __future__ import annotations

from typing import Any

from app.db.errors import DocumentNotFoundError, DuplicateDocumentError
from app.db.indexes import OPERATOR_DECISIONS, RECOMMENDATIONS
from app.db.repository import MongoRepository
from app.models.enums import OperatorDecisionType
from app.models.operator_decision import OperatorDecision
from app.models.recommendation import Recommendation
from app.schemas.feedback import (
    FeedbackCreate,
    FeedbackRead,
    FeedbackRefusalResponse,
)


class FeedbackRefusal(RuntimeError):
    """A decision that cannot be recorded, with a code a client can branch on.

    Raised rather than returned, so a route handler can convert it to a 409
    carrying the code without the service depending on FastAPI.
    """

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        recommendation_id: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.recommendation_id = recommendation_id

    def as_body(self) -> dict[str, Any]:
        return FeedbackRefusalResponse(
            code=self.code,
            detail=self.detail,
            recommendation_id=self.recommendation_id,
        ).model_dump(mode="json")


class FeedbackService:
    """Store and read operator decisions against stored recommendations.

    The database is held as a provider and resolved per call (like the outcome
    service) so an unconfigured persistence layer raises inside the request
    handler rather than at import time, and so tests can swap in the in-memory
    fake by overriding ``get_db``.
    """

    def __init__(self, database: Any) -> None:
        self._database = database
        self._resolved_decisions: MongoRepository[OperatorDecision] | None = None
        self._resolved_recommendations: MongoRepository[Recommendation] | None = None

    def _decision_repository(self) -> MongoRepository[OperatorDecision]:
        if self._resolved_decisions is None:
            self._resolved_decisions = MongoRepository(
                self._database, OPERATOR_DECISIONS, OperatorDecision
            )
        return self._resolved_decisions

    def _recommendation_repository(self) -> MongoRepository[Recommendation]:
        if self._resolved_recommendations is None:
            self._resolved_recommendations = MongoRepository(
                self._database, RECOMMENDATIONS, Recommendation
            )
        return self._resolved_recommendations

    # -- the recommendation a decision answers -----------------------------

    async def _recommendation(self, recommendation_id: str) -> Recommendation:
        try:
            return await self._recommendation_repository().get(
                "recommendation_id", recommendation_id
            )
        except DocumentNotFoundError as exc:
            raise FeedbackRefusal(
                "recommendation_unknown",
                (
                    f"No stored recommendation has the ID “{recommendation_id}”. "
                    "Issue one through /api/decisions/advise before recording an "
                    "operator decision for it."
                ),
                recommendation_id=recommendation_id,
            ) from exc

    def _require_is_a_decision(self, recommendation: Recommendation) -> None:
        if recommendation.action is None:
            raise FeedbackRefusal(
                "recommendation_not_a_decision",
                (
                    f"Recommendation “{recommendation.recommendation_id}” is a note "
                    "that named no action. Accepting, modifying or rejecting it "
                    "would record a decision where the advice made none."
                ),
                recommendation_id=recommendation.recommendation_id,
            )

    async def _ensure_undecided(self, recommendation_id: str) -> None:
        existing = await self._decision_repository().find(
            {"recommendation_id": recommendation_id}, limit=1
        )
        if existing:
            raise FeedbackRefusal(
                "decision_already_recorded",
                (
                    f"Recommendation “{recommendation_id}” already has a recorded "
                    "decision. A recommendation is decided once, so the trace keeps "
                    "a single operator disposition per advice."
                ),
                recommendation_id=recommendation_id,
            )

    # -- writes --------------------------------------------------------------

    async def record(self, payload: FeedbackCreate) -> FeedbackRead:
        """File one operator decision against one stored recommendation.

        The order matters. The recommendation is loaded and checked first, so a
        record can never be filed against a missing advice or a note that made no
        decision; uniqueness is checked second, so the trace keeps one disposition
        per advice; the document is built last with the pan derived from the
        recommendation, so a record cannot secretly answer a different pan's
        recommendation.

        The disposition and the action are separate facts. An acceptance with a
        blank ``actual_action`` stays blank — the record says the advice was
        accepted and no physical action followed. Nothing is ever filled in on the
        operator's behalf, because writing the recommended action into a hand that
        never acted would claim an intervention that never occurred.
        """
        recommendation = await self._recommendation(payload.recommendation_id)
        self._require_is_a_decision(recommendation)
        await self._ensure_undecided(payload.recommendation_id)

        resolved_timestamp = payload.resolved_timestamp()
        decision = OperatorDecision(
            recommendation_id=recommendation.recommendation_id,
            pan_id=recommendation.pan_id,
            decision=payload.decision,
            actual_action=payload.actual_action,
            # Copied from the advice now, while it is certainly still there. A
            # reader of this document must be able to see what was advised and by
            # which rule version without a second query, and must not be left
            # guessing if the recommendation it cites has since been removed.
            recommended_action=recommendation.action,
            rule_name=recommendation.rule_name,
            rule_version=recommendation.rule_version,
            specification_hash=recommendation.specification_hash,
            is_demonstration=recommendation.twin.is_demonstration,
            demonstration_sources=list(recommendation.twin.demonstration_sources),
            operator_id=payload.operator_id,
            notes=payload.notes,
            timestamp=resolved_timestamp,
        )
        try:
            stored = await self._decision_repository().insert(decision)
        except DuplicateDocumentError as exc:
            # The unique index refused, so another decision for this
            # recommendation was stored between the check above and this write.
            # The guarantee is the same either way; only the reason differs, and
            # the client gets the same code it would have got from the check.
            raise FeedbackRefusal(
                "decision_already_recorded",
                (
                    f"Recommendation “{recommendation.recommendation_id}” already has "
                    "a recorded decision. A recommendation is decided once, so the "
                    "trace keeps a single operator disposition per advice."
                ),
                recommendation_id=recommendation.recommendation_id,
            ) from exc
        return FeedbackRead.from_decision(stored, recommendation)

    # -- reads ----------------------------------------------------------------

    async def _recommendation_if_present(
        self, recommendation_id: str
    ) -> Recommendation | None:
        """The advice a decision answers, or ``None`` if it is no longer there.

        Deliberately not :meth:`_recommendation`, which refuses. A decision is
        appended after its recommendation and outlives it: recommendations are
        versioned, superseded and pruned, and a restore or a retention policy can
        take one away. When that happens the operator's decision has not become
        wrong or unreadable — the judgement is still the operator's, and the
        record carries its own copy of what was advised and which rule advised
        it. Refusing the read here would let a document the decision does not own
        destroy the record of a person's decision, and would take down the whole
        listing rather than one row, because every other decision in it is
        unaffected.
        """
        try:
            return await self._recommendation_repository().get(
                "recommendation_id", recommendation_id
            )
        except DocumentNotFoundError:
            return None

    async def _read(self, decision: OperatorDecision) -> FeedbackRead:
        recommendation = await self._recommendation_if_present(
            decision.recommendation_id
        )
        return FeedbackRead.from_decision(decision, recommendation)

    async def get(self, decision_id: str) -> FeedbackRead | None:
        try:
            decision = await self._decision_repository().get("decision_id", decision_id)
        except DocumentNotFoundError:
            return None
        return await self._read(decision)

    async def latest_for_recommendation(
        self, recommendation_id: str
    ) -> FeedbackRead | None:
        """The one decision for a recommendation, if the operator has decided yet."""
        records = await self._decision_repository().find(
            {"recommendation_id": recommendation_id}, limit=1
        )
        if not records:
            return None
        return await self._read(records[0])

    async def list(
        self,
        *,
        pan_id: str | None = None,
        recommendation_id: str | None = None,
        decision: str | None = None,
        limit: int = 50,
        skip: int = 0,
    ) -> list[FeedbackRead]:
        """Recorded decisions, newest first, optionally narrowed.

        ``decision`` is validated against the closed vocabulary before it reaches
        the query, so a typo like "accepted, maybe" is refused rather than
        answered with an empty list that looks like a pan nobody has decided on.
        """
        query: dict[str, Any] = {}
        if pan_id is not None:
            query["pan_id"] = pan_id
        if recommendation_id is not None:
            query["recommendation_id"] = recommendation_id
        if decision is not None:
            try:
                OperatorDecisionType(decision)
            except ValueError as exc:
                raise FeedbackRefusal(
                    "unknown_decision_vocabulary",
                    (
                        f"{decision!r} is not one of the recorded decisions this "
                        f"project keeps: accepted, modified, rejected."
                    ),
                    recommendation_id=recommendation_id,
                ) from exc
            query["decision"] = decision

        rows = await self._decision_repository().find(
            query, sort=[("timestamp", -1)], limit=limit, skip=skip
        )
        return [await self._read(row) for row in rows]

    async def count(self, **query: Any) -> int:
        """How many decisions match a filter, for status reporting."""
        return await self._decision_repository().count(query)


__all__ = ["FeedbackRefusal", "FeedbackService"]