"""Exercise the real forwarding fixtures without a cluster.

The sandbox pytest session uses their source with fake installation and port
forwarding. It catches waits before installation and retries of failed setup.
"""
import inspect

import pytest

import conftest

pytest_plugins = ["pytester"]
pytestmark = pytest.mark.smoke


@pytest.mark.parametrize("install_ok,via_port_forward", [(False, True), (True, True), (True, False)])
def test_forwards_follow_installation_and_teardown(pytester, install_ok, via_port_forward):
    pytester.makeconftest('''
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator, Optional
import pytest

EVENTS = Path("events")
def event(name):
    with EVENTS.open("a") as out:
        out.write(name + "\\n")

class Kube:
    kubeconfig = "unused"

class PortForward:
    def __init__(self, config, namespace, service, local_port, remote_port):
        self.service = service
    def start(self):
        event("start " + self.service)
        return self
    def stop(self):
        event("stop " + self.service)

NAMESPACE = "agent-platform"
AGENTGATEWAY_SERVICE = "agentgateway"
MUSTER_SERVICE_PORT = 8090
AGENTGATEWAY_SERVICE_PORT = 8080
SCENARIO = SimpleNamespace(via_port_forward=''' + repr(via_port_forward) + ''', muster_port=8090, mcp_port=8080)

def wait_for_endpoints(kube, namespace, service):
    event("wait " + service)
    assert Path("installed").exists(), "endpoint wait ran before installation"

@pytest.fixture(scope="module")
def kube():
    return Kube()

@pytest.fixture(scope="module")
def app_deployment():
    event("install")
    assert ''' + repr(install_ok) + ''', "deliberate installation failure"
    Path("installed").touch()
    return 1.0

''' + inspect.getsource(conftest.muster_forward) + "\n" + inspect.getsource(conftest.mcp_forward))
    pytester.makepyfile('''
def test_first(muster_forward, mcp_forward):
    pass

def test_second(muster_forward, mcp_forward):
    pass
''')
    result = pytester.runpytest_subprocess("-q", "-p", "no:pytest_helm_charts")
    events_file = pytester.path / "events"
    events = events_file.read_text().splitlines() if events_file.exists() else []
    if not install_ok:
        result.assert_outcomes(errors=2)
        assert events == ["install"]  # No wait, no forward, no second install.
    else:
        result.assert_outcomes(passed=2)
        assert events == (["install", "wait muster", "start muster", "wait agentgateway", "start agentgateway",
                           "stop agentgateway", "stop muster"] if via_port_forward else ["install"])
