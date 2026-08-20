import logging

import pykube
from pytest_helm_charts.clusters import Cluster
import pytest

logger = logging.getLogger(__name__)


@pytest.mark.smoke
def test_api_working(kube_cluster: Cluster) -> None:
    """Placeholder smoke test.

    The smoke step is skipped via .ats/main.yaml because the agent-platform
    umbrella is an app-of-apps meta-package that cannot install on a bare kind
    cluster without ~9 external CRD families. This test only asserts the cluster
    API is reachable, and exists so the tests/ats directory is present if the
    smoke step is ever re-enabled.
    """
    assert kube_cluster.kube_client is not None
    assert len(pykube.Node.objects(kube_cluster.kube_client)) >= 1
