from clickup_work_router.core.comment_triage import classify_comment_intent, is_meaningful_cross_agent_handoff


def test_simple_question_routes_to_direct_reply():
    intent = classify_comment_intent("what is this task about?", has_active_task=False)

    assert intent == "direct_reply"


def test_context_or_unblock_comment_appends_to_active_task():
    intent = classify_comment_intent("approved, proceed with Path B instead", has_active_task=True)

    assert intent == "append_to_active"


def test_context_or_unblock_comment_without_active_task_creates_task():
    intent = classify_comment_intent("approved, proceed with Path B instead", has_active_task=False)

    assert intent == "create_task"


def test_new_scope_comment_creates_separate_task_even_when_active_exists():
    intent = classify_comment_intent("also build a referral landing page for this", has_active_task=True)

    assert intent == "create_task"


def test_cross_agent_handoff_classifier_detects_unblock_but_not_chatter():
    assert is_meaningful_cross_agent_handoff("Product definition is complete; Growth can proceed now") is True
    assert is_meaningful_cross_agent_handoff("thanks, noted") is False
