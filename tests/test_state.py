from clickup_work_router.state import LINK_DONE, LINK_OPEN, RouterState


def test_state_persists_dedupe_links_and_events_across_restarts(tmp_path):
    db = tmp_path / "state" / "router.sqlite3"
    first = RouterState(path=db)
    assert first.mark_seen("k1") is True
    assert first.mark_seen("k1") is False
    link_id = first.add_link("example", "t1", "product-lead-example", "clickup:example:t1:product-lead-example", "t_abc", "ready")
    first.record_event("example", "e1", "dispatch", "native assignee")
    first.record_error("boom")

    restarted = RouterState(path=db)

    assert restarted.has_seen("k1") is True
    assert [link.id for link in restarted.open_links("example")] == [link_id]
    assert restarted.has_open_link("example", "t1", "product-lead-example") is True
    assert restarted.recent("example")[0]["summary"] == "native assignee"
    assert restarted.last_error == "boom"


def test_link_lifecycle_and_duplicate_kanban_ids():
    state = RouterState()
    link_id = state.add_link("example", "t1", "p", "ref", "t_abc")
    assert state.add_link("example", "t1", "p", "ref", "t_abc") == link_id

    state.update_link(link_id, state=LINK_DONE, notified_status="done", ignored_column="x")

    assert state.open_links("example") == []
    assert state.has_open_link("example", "t1", "p") is False
    assert state.known_task_ids("example") == {"t1"}
    assert state.link_counts("example") == {LINK_DONE: 1}
    assert LINK_OPEN not in state.link_counts("example")


def test_events_are_capped_per_workspace_and_seen_keys_prune():
    state = RouterState(max_events=3)
    for index in range(5):
        state.record_event("example", f"e{index}", "ignored", f"event {index}")
    state.record_event("other", "o1", "ignored", "other")

    assert [event["event_id"] for event in state.recent("example")] == ["e4", "e3", "e2"]
    assert state.recent("other")[0]["event_id"] == "o1"

    state.mark_seen("old")
    assert state.prune_seen(-1) == 1
    assert state.has_seen("old") is False


def test_reset_clears_everything():
    state = RouterState()
    state.mark_seen("k")
    state.add_link("example", "t1", "p", "ref", "t_1")
    state.record_error("x")
    state.reset()

    assert not state.has_seen("k")
    assert state.open_links("example") == []
    assert state.last_error == ""
