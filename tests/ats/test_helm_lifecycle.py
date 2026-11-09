"""The maintenance preconditions for a self-managed Helm release."""

import json
import subprocess
from unittest.mock import Mock

import pytest

import conftest
from conftest import Helm

pytestmark = pytest.mark.smoke


def suspended(reconciling: bool = False) -> dict:
    return {"spec": {"suspend": True}, "status": {"conditions": [
        {"type": "Reconciling", "status": "True" if reconciling else "False"},
    ]}}


def test_uninstall_drains_the_controller_before_changing_helm_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = Mock()
    cluster.get.side_effect = [suspended(), suspended(True), suspended(), suspended(), suspended()]
    events = []
    cluster.cmd.side_effect = lambda args: events.append(args[0])
    monkeypatch.setattr(conftest, "Kube", lambda _: cluster)
    monkeypatch.setattr(conftest.time, "sleep", lambda _: None)
    statuses = iter(("pending-upgrade", "deployed"))

    def run(arguments: list, **_: object) -> subprocess.CompletedProcess:
        action = arguments[3]
        events.append(action)
        if action == "status":
            return subprocess.CompletedProcess(arguments, 0, json.dumps({"info": {"status": next(statuses)}}), "")
        assert action == "uninstall"
        assert cluster.get.call_count == 5
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(conftest, "run", run)
    Helm("/test/kubeconfig").uninstall()
    assert events == ["delete", "patch", "status", "status", "delete", "uninstall"]


@pytest.mark.parametrize("current", [None, {"spec": {"suspend": False}}])
def test_uninstall_refuses_a_self_release_that_disappears_or_resumes(monkeypatch: pytest.MonkeyPatch, current: dict | None) -> None:
    cluster = Mock()
    cluster.get.side_effect = [suspended(), current]
    monkeypatch.setattr(conftest, "Kube", lambda _: cluster)
    run = Mock()
    monkeypatch.setattr(conftest, "run", run)
    with pytest.raises(AssertionError, match="no longer suspended"):
        Helm("/test/kubeconfig").uninstall()
    run.assert_not_called()


def test_uninstall_does_not_run_after_suspension_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = Mock()
    cluster.get.return_value = suspended()
    cluster.cmd.side_effect = [None, AssertionError("suspension refused")]
    monkeypatch.setattr(conftest, "Kube", lambda _: cluster)
    run = Mock()
    monkeypatch.setattr(conftest, "run", run)
    with pytest.raises(AssertionError, match="suspension refused"):
        Helm("/test/kubeconfig").uninstall()
    run.assert_not_called()


def test_uninstall_rechecks_suspension_after_stopping_the_resumer(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = Mock()
    cluster.get.side_effect = [suspended(), suspended(), {"spec": {"suspend": False}}]
    monkeypatch.setattr(conftest, "Kube", lambda _: cluster)
    run = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps({"info": {"status": "deployed"}}), ""))
    monkeypatch.setattr(conftest, "run", run)
    with pytest.raises(AssertionError, match="resumed before uninstall"):
        Helm("/test/kubeconfig").uninstall()
    assert [call.args[0][3] for call in run.call_args_list] == ["status"]


def test_uninstall_without_self_management_needs_no_suspension(monkeypatch: pytest.MonkeyPatch) -> None:
    cluster = Mock()
    cluster.get.return_value = None
    monkeypatch.setattr(conftest, "Kube", lambda _: cluster)
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(conftest, "run", run)
    Helm("/test/kubeconfig").uninstall()
    cluster.cmd.assert_not_called()
    assert run.call_args.args[0][3] == "uninstall"
