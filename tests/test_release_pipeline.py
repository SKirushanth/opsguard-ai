"""Exercise Single-mode rollback order and its failure status without Azure writes."""
import pytest
from deploy.pipeline import release


def test_rollback_restores_snapshots_in_reverse(monkeypatch):
    events=[]
    monkeypatch.setenv("GITHUB_RUN_ID","123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT","1")
    monkeypatch.setattr(release, "activate", lambda b,i,s: events.append((b["repo"],i)))
    for name in ("demo","guard","browser"):
        monkeypatch.setattr(release.smoke,name,lambda:None)
    plan=[(n,dict(repo=n,digest="sha256:previous",source="abcd"),"candidate") for n in ("backend","frontend")]
    def regression():
        raise RuntimeError("smoke failed")
    with pytest.raises(RuntimeError,match="smoke failed"):
        release.transaction(plan,regression)
    assert events==[("backend","candidate"),("frontend","candidate"),("frontend","frontend@sha256:previous"),("backend","backend@sha256:previous")]
