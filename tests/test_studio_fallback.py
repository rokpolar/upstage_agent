import pytest

from app.adapters.studio import StudioClient, StudioError


def client(monkeypatch, env_agent, calls, fail_on):
    s = StudioClient()
    monkeypatch.setattr(
        s,
        "settings",
        lambda kind="pumui": ({"key": "k", "agent_id": env_agent, "config_id": "1", "project_id": None}, []),
    )

    def run_one(path, key, agent, config, *rest):
        calls.append((agent, config))
        if agent in fail_on:
            raise StudioError(404, '{"error":{"param":"agent_id"}}', "not found")
        return {"ok": True}, "job"

    monkeypatch.setattr(s, "run_one", run_one)
    return s


def test_stale_env_agent_falls_back_to_configured_agent(tmp_path, monkeypatch):
    calls = []
    default = StudioClient().profile("pumui")["agent_id"]
    s = client(monkeypatch, "agt_old", calls, {"agt_old"})
    result, job = s.run_file(tmp_path / "a.pdf", tmp_path)
    assert result == {"ok": True}
    assert [a for a, _ in calls] == ["agt_old", default]


def test_not_found_on_configured_agent_is_not_retried(tmp_path, monkeypatch):
    calls = []
    default = StudioClient().profile("pumui")["agent_id"]
    s = client(monkeypatch, default, calls, {default})
    with pytest.raises(StudioError):
        s.run_file(tmp_path / "a.pdf", tmp_path)
    assert len(calls) == 1
