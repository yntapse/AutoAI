"""The pipeline AI Copilot. Feature 7 of Phase 4.

Every answer is retrieved from what the platform already decided and recorded -
the stored recommendation reasons, the measured join verifications, the
approved relationships, the quality rules, the run history. Nothing is
generated, so an answer can never contradict the pipeline it describes, and
every sentence traces back to a stored row (returned as `citations`).

That is the point: "all answers must be explainable" is only true if the answer
IS the explanation that was stored when the decision was made.

PURE. No database handle, no network, no LLM, no row data.
"""

from typing import Any, Dict, List, Optional, Sequence

_JOIN_WORDS = ("join", "left", "inner", "cardinality", "fan", "explode", "explosion")
_TABLE_WORDS = ("table", "why", "select", "chose", "include", "exclude", "reject")
_COLUMN_WORDS = ("column", "derived", "feature", "segment", "created", "calculated")
_QUALITY_WORDS = ("quality", "rule", "null", "unique", "validity", "freshness", "check")
_TRANSFORM_WORDS = ("transform", "clean", "duplicate", "impute", "fill", "convert")
_RUN_WORDS = ("run", "fail", "failed", "execution", "duration", "last", "history")
_VERIFY_WORDS = ("verified", "match rate", "orphan", "measured", "measurement")

_STOPWORDS = {
    "the", "a", "an", "why", "was", "is", "did", "does", "do", "how", "what",
    "for", "and", "to", "of", "in", "on", "it", "this", "that", "my", "our",
    "ai", "you", "chosen", "choose", "chose", "created", "create", "used",
    "use", "join", "table", "column", "rule", "there", "are", "were", "with",
}


def _tokens(text: str) -> List[str]:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in str(text or ""))
    return [token for token in cleaned.split() if len(token) > 2]


def _entities(question: str) -> List[str]:
    return [token for token in _tokens(question) if token not in _STOPWORDS]


def _mentions(haystack: str, entities: Sequence[str]) -> int:
    lowered = str(haystack or "").lower()
    return sum(1 for entity in entities if entity in lowered)


def _score_recommendation(rec: Dict[str, Any], entities: Sequence[str]) -> int:
    payload = rec.get("payload") or {}
    text = " ".join(
        str(value)
        for value in (
            rec.get("ref_key"),
            rec.get("title"),
            payload.get("table_name"),
            payload.get("left_table"),
            payload.get("right_table"),
            payload.get("left_column"),
            payload.get("right_column"),
            payload.get("name"),
            payload.get("step_type"),
            payload.get("rule_type"),
            (payload.get("rule_config") or {}).get("column_name")
            if isinstance(payload.get("rule_config"), dict)
            else None,
            (payload.get("configuration") or {}).get("column_name")
            if isinstance(payload.get("configuration"), dict)
            else None,
        )
        if value
    )
    return _mentions(text, entities)


def _kinds_for(question: str) -> List[str]:
    lowered = str(question or "").lower()
    kinds: List[str] = []
    if any(word in lowered for word in _JOIN_WORDS):
        kinds.append("join")
    if any(word in lowered for word in _COLUMN_WORDS):
        kinds.append("derived_column")
    if any(word in lowered for word in _QUALITY_WORDS):
        kinds.append("quality_rule")
    if any(word in lowered for word in _TRANSFORM_WORDS):
        kinds.append("transformation")
    if any(word in lowered for word in _TABLE_WORDS):
        kinds.append("table")
    if not kinds:
        kinds = ["join", "table", "transformation", "quality_rule", "derived_column"]
    return kinds


def _format_recommendation(rec: Dict[str, Any]) -> str:
    lines = [rec.get("reason") or ""]
    if rec.get("alternative_reason"):
        lines.append(f"Why not the alternative: {rec['alternative_reason']}")
    if rec.get("expected_impact"):
        lines.append(f"Expected impact: {rec['expected_impact']}")
    decision = rec.get("decision")
    if decision and decision != "pending":
        lines.append(f"You {decision} this recommendation.")
    else:
        lines.append("This is still pending your approval, so it is not active.")
    confidence = rec.get("confidence")
    if confidence is not None:
        lines.append(f"Confidence {round(float(confidence) * 100)}%.")
    return "\n".join(line for line in lines if line)


def _format_verification(verification: Dict[str, Any]) -> str:
    if not verification.get("verified"):
        return (
            f"This relationship is NOT verified. "
            f"{verification.get('error') or 'No successful measurement exists.'}"
        )
    return "\n".join(
        [
            f"Measured on {verification.get('measured_at')}: "
            f"{verification.get('match_percentage')}% of "
            f"{verification.get('base_table')} rows match "
            f"{verification.get('related_table')}, "
            f"{verification.get('orphan_percentage')}% are orphans.",
            f"Cardinality {verification.get('cardinality')}, fan-out risk "
            f"{verification.get('fanout_risk')}, each base row becomes "
            f"{verification.get('row_explosion_ratio')} output rows "
            f"({verification.get('base_rows')} in, "
            f"{verification.get('inner_result_rows')} out on an INNER join).",
            f"Recommendation: {verification.get('recommended_join') or 'none'} "
            f"({verification.get('severity')}) - {verification.get('headline')}.",
        ]
    )


def answer(question: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Answer one question about this plan from stored decisions only.

    context keys:
        plan:            {requirement_text, status, intent, ...}
        recommendations: list of recommendation dicts
        verifications:   list of measured verification dicts
        runs:            list of {status, started_at, row_count, execution_time}
    """
    plan = context.get("plan") or {}
    recommendations: List[Dict[str, Any]] = list(context.get("recommendations") or [])
    verifications: List[Dict[str, Any]] = list(context.get("verifications") or [])
    runs: List[Dict[str, Any]] = list(context.get("runs") or [])

    entities = _entities(question)
    lowered = str(question or "").lower()

    # --- run history --------------------------------------------------------
    if any(word in lowered for word in _RUN_WORDS) and runs:
        succeeded = sum(1 for run in runs if run.get("status") == "completed")
        failed = sum(1 for run in runs if run.get("status") == "failed")
        latest = runs[0]
        return _reply(
            "\n".join(
                [
                    f"This pipeline has {len(runs)} recorded run(s): "
                    f"{succeeded} completed, {failed} failed.",
                    f"The most recent finished with status "
                    f"'{latest.get('status')}'"
                    + (
                        f", producing {latest['row_count']} rows"
                        if latest.get("row_count") is not None
                        else ""
                    )
                    + (
                        f" in {latest['execution_time']}s."
                        if latest.get("execution_time") is not None
                        else "."
                    ),
                ]
            ),
            source="run_history",
            citations=[],
        )

    # --- measured verification ---------------------------------------------
    # Only an explicit question ABOUT the measurement routes here. A plain
    # "why LEFT JOIN" wants the stored reason, with the measurement attached as
    # supporting evidence - which the recommendation branch below does.
    verification_question = any(word in lowered for word in _VERIFY_WORDS)
    if verification_question and verifications:
        scored = sorted(
            verifications,
            key=lambda item: _mentions(
                f"{item.get('base_table')} {item.get('related_table')} "
                f"{item.get('base_column')} {item.get('related_column')}",
                entities,
            ),
            reverse=True,
        )
        best = scored[0]
        if _mentions(
            f"{best.get('base_table')} {best.get('related_table')}", entities
        ) or len(verifications) == 1:
            return _reply(
                _format_verification(best),
                source="join_verification",
                citations=[
                    f"{best.get('base_table')}.{best.get('base_column')}"
                    f"->{best.get('related_table')}.{best.get('related_column')}"
                ],
            )

    # --- stored recommendation reasons -------------------------------------
    kinds = _kinds_for(question)
    candidates = [rec for rec in recommendations if rec.get("kind") in kinds]
    if not candidates:
        candidates = recommendations

    scored_recs = sorted(
        candidates,
        key=lambda rec: (
            _score_recommendation(rec, entities),
            float(rec.get("confidence") or 0),
        ),
        reverse=True,
    )

    if scored_recs and _score_recommendation(scored_recs[0], entities) > 0:
        best = scored_recs[0]
        body = _format_recommendation(best)
        matching = _matching_verification(best, verifications)
        if matching:
            body += "\n\nMeasured evidence:\n" + _format_verification(matching)
        return _reply(
            f"{best.get('title')}\n\n{body}",
            source="plan_recommendation",
            citations=[best.get("ref_key")],
        )

    # --- fall back to describing the plan itself ----------------------------
    counts: Dict[str, int] = {}
    for rec in recommendations:
        counts[rec.get("kind", "other")] = counts.get(rec.get("kind", "other"), 0) + 1
    breakdown = ", ".join(f"{count} {kind}" for kind, count in sorted(counts.items()))

    return _reply(
        "\n".join(
            [
                "I answer from what this plan actually recorded, and I could "
                "not match your question to a stored decision.",
                f"Requirement: {plan.get('requirement_text') or 'not set'}.",
                f"Status: {plan.get('status') or 'unknown'}. "
                f"Recommendations: {breakdown or 'none'}.",
                "Try naming a table, column or join - for example "
                "\"why LEFT JOIN on customer_id\".",
            ]
        ),
        source="plan_summary",
        citations=[],
        answered=False,
    )


def _matching_verification(
    rec: Dict[str, Any], verifications: Sequence[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    payload = rec.get("payload") or {}
    left, right = payload.get("left_table"), payload.get("right_table")
    if not left or not right:
        return None
    for verification in verifications:
        if (
            verification.get("base_table") == left
            and verification.get("related_table") == right
        ) or (
            verification.get("base_table") == right
            and verification.get("related_table") == left
        ):
            return verification
    return None


def _reply(
    text: str,
    source: str,
    citations: Sequence[Optional[str]],
    answered: bool = True,
) -> Dict[str, Any]:
    return {
        "answer": text,
        "source": source,
        "citations": [citation for citation in citations if citation],
        "answered": answered,
        "generated": False,
    }
