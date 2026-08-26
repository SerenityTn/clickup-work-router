from clickup_work_router.config import WorkspaceConfig
from clickup_work_router.core.dedupe import stable_event_key
from clickup_work_router.core.profile_mapping import ProfileMapper
from clickup_work_router.core.routing import route_event
from clickup_work_router.models import ExternalDependency, ExternalUser, WorkEvent, WorkItem


def cfg():
    c = WorkspaceConfig(name="example", company_slug="example", list_ids=["l1"], kanban_board="example")
    c.profile_map = {
        "clickup_user_ids": {"u1": "product-lead-example", "agent1": "founding-engineer-example", "growth1": "growth-specialist-example"},
        "emails": {"eng@example.com": "founding-engineer-example"},
        "usernames": {},
        "role_aliases": {"ceo": "ceo-example", "product-lead": "product-lead-example", "growth-specialist": "growth-specialist-example"},
        "agent_user_ids": {"agent1": "founding-engineer-example", "growth1": "growth-specialist-example"},
    }
    return c


def item(deps=None, assignees=None):
    return WorkItem(
        source_system="clickup", workspace_id="ws", project_id="p", list_id="l1",
        issue_id="t1", issue_key="T1", url="http://clickup/t1", title="Title", description="",
        status="open", is_closed=False,
        assignees=assignees or [ExternalUser(id="u1", email="lead@example.com")], watchers=[], dependencies=deps or [], custom_fields={}, raw={},
    )


def event(**kw):
    defaults = dict(source_system="clickup", event_type="taskCreated", event_id="e1", event_time="time-a", payload_hash="h", work_item=item(), author=None, raw={})
    defaults.update(kw)
    return WorkEvent(**defaults)


def test_profile_mapping_user_id_and_email():
    mapper = ProfileMapper(cfg())
    assert mapper.profile_for_user(ExternalUser(id="u1")) == "product-lead-example"
    assert mapper.profile_for_user(ExternalUser(id="x", email="eng@example.com")) == "founding-engineer-example"


def test_assignee_route_and_loop_prevention():
    decision = route_event(event(), cfg())
    assert decision.action == "dispatch"
    assert decision.profile == "product-lead-example"

    agent_created_handoff = route_event(event(author=ExternalUser(id="agent1")), cfg())
    assert agent_created_handoff.action == "dispatch"
    assert agent_created_handoff.profile == "product-lead-example"

    ignored_comment = route_event(event(event_type="taskCommentPosted", comment_text="agent reply", author=ExternalUser(id="agent1")), cfg())
    assert ignored_comment.action == "ignore"
    assert "loop" in ignored_comment.reason

    cross_agent_handoff = route_event(
        event(
            event_type="taskCommentPosted",
            comment_text="Product definition is complete; Growth can proceed with the no-code waitlist path.",
            author=ExternalUser(id="agent1"),
            work_item=item(assignees=[ExternalUser(id="growth1")]),
        ),
        cfg(),
    )
    assert cross_agent_handoff.action == "dispatch"
    assert cross_agent_handoff.profile == "growth-specialist-example"
    assert "cross-agent handoff" in cross_agent_handoff.reason

    self_comment = route_event(
        event(
            event_type="taskCommentPosted",
            comment_text="complete",
            author=ExternalUser(id="growth1"),
            work_item=item(assignees=[ExternalUser(id="growth1")]),
        ),
        cfg(),
    )
    assert self_comment.action == "ignore"
    assert "own task" in self_comment.reason

    human_mapped = route_event(event(author=ExternalUser(id="u1")), cfg())
    assert human_mapped.action == "dispatch"


def test_comment_mention_overrides_assignee():
    e = event(event_type="taskCommentPosted", comment_text="@ceo please", mentioned_aliases=["ceo"])
    decision = route_event(e, cfg())
    assert decision.profile == "ceo-example"
    assert "mention" in decision.reason


def test_dependency_routing_upstream_blocks_downstream_does_not():
    blocked_item = item([ExternalDependency(issue_id="b1", relation="blocks_current", is_closed=False, title="Blocker")])
    decision = route_event(event(work_item=blocked_item), cfg())
    assert decision.action == "blocked"
    assert "Open upstream blockers" in decision.dependency_summary

    downstream_item = item([ExternalDependency(issue_id="d1", relation="current_blocks", is_closed=False)])
    decision2 = route_event(event(work_item=downstream_item), cfg())
    assert decision2.action == "dispatch"
    assert "not blockers" in decision2.dependency_summary


def test_stable_dedupe_ignores_timestamp_changes():
    e1 = event(event_time="time-a")
    e2 = event(event_time="time-b")
    assert stable_event_key(e1, "profile") == stable_event_key(e2, "profile")


def test_route_all_task_updates_false_ignores_non_comment_updates():
    decision = route_event(event(event_type="taskUpdated"), cfg())
    assert decision.action == "ignore"
    assert "task update routing disabled" in decision.reason
