from __future__ import annotations
import json, os, shutil
from freeze_router_v3_h5_r5 import RUN_DIR
from prepare_router_subscription_h5_r5 import H5SubscriptionCoordinatorR5

def test_r5_coordinator_prepares_exact_twenty_classifier_requests(tmp_path,monkeypatch):
    run=tmp_path/"run";shutil.copytree(RUN_DIR,run)
    monkeypatch.setenv("ROUTER_H5_R5_SYNTHETIC_TEST_MODE","1")
    coordinator=H5SubscriptionCoordinatorR5(run)
    result=coordinator.prepare_classifier()
    assert result["prepared_count"]==20
    assert coordinator.status()["prepared_envelopes"]==20
