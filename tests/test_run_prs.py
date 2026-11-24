"""Positive PR associations survive partial payloads without claiming completeness."""

from datetime import UTC, datetime

import pytest

from cadence.providers.github import GitHubProvider
from cadence.run_prs import MAX_PR_NUMBER, parse_pr_references

NOW = datetime(2026, 11, 17, tzinfo=UTC)


def raw_run(**over):
    return {
        "id": 100, "head_sha": "a" * 40, "created_at": NOW.isoformat(),
        "pull_requests": [{"number": 42}, {"number": 43}, {"number": 42}], **over,
    }


@pytest.mark.parametrize("raw,numbers,state", [
    (None, [], "unknown"), ([], [], "empty"), ({}, [], "malformed"),
    ("42", [], "malformed"), ([{"number": 42}, {"number": 43}, {"number": 42}],
                              [42, 43], "reported"),
    ([{"number": MAX_PR_NUMBER}], [MAX_PR_NUMBER], "reported"),
    ([{"number": 42}, None, {"number": 43}], [42, 43], "malformed"),
    ([{"number": True}, {"number": False}, {"number": 0}, {"number": -1},
      {"number": "42"}, {"number": 1.5}, {"number": MAX_PR_NUMBER + 1}, {}], [], "malformed"),
])
def test_payload_states_and_positive_evidence(raw, numbers, state):
    assert parse_pr_references(raw) == (numbers, state)


async def test_rest_and_workflow_run_webhooks_preserve_the_same_complete_positive_list():
    provider = GitHubProvider("test")
    try:
        raw = raw_run()
        rest = provider._to_run(raw, 1)
        event = provider.normalize_event("workflow_run", {
            "repository": {"id": 1, "owner": {"login": "acme"}, "name": "widget"},
            "workflow_run": raw,
        })
        assert rest.pull_request_numbers == [42, 43]
        assert rest.pull_request_number == 42  # compatibility for existing scalar consumers
        assert rest.pr_payload_state == "reported"
        assert event.run.pull_request_numbers == rest.pull_request_numbers
        assert event.run.pr_payload_state == rest.pr_payload_state
    finally:
        await provider.aclose()


@pytest.mark.parametrize("field,state", [({}, "unknown"), ({"pull_requests": None}, "unknown"),
                                        ({"pull_requests": []}, "empty")])
def test_missing_and_empty_lists_do_not_claim_absence(field, state):
    raw = raw_run()
    del raw["pull_requests"]
    run = GitHubProvider._to_run({**raw, **field}, 1)
    assert run.pull_request_number is None
    assert run.pull_request_numbers == []
    assert run.pr_payload_state == state


def test_valid_numbers_in_a_malformed_payload_remain_observed():
    run = GitHubProvider._to_run(raw_run(pull_requests=[{}, {"number": 42}, "bad"]), 1)
    assert run.pull_request_number == 42 and run.pull_request_numbers == [42]
    assert run.pr_payload_state == "malformed"
