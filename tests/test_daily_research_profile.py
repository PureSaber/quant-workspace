from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from packaging.requirements import Requirement

PROFILE = Path(__file__).resolve().parents[1] / "profiles" / "daily-research"


def test_daily_research_profile_freezes_provider_policy_and_repository_refs():
    data = yaml.safe_load((PROFILE / "data.yaml").read_text(encoding="utf-8"))
    pipeline = yaml.safe_load((PROFILE / "pipeline.yaml").read_text(encoding="utf-8"))
    stack = json.loads((PROFILE / "stack.json").read_text(encoding="utf-8"))
    lock = PROFILE / "requirements.lock"

    assert data["prices"]["primary"] != "akshare_auto"
    assert data["prices"]["primary"] not in data["prices"]["shadows"]
    assert pipeline["data_config"] == "quant-workspace/profiles/daily-research/data.yaml"
    assert hashlib.sha256(lock.read_bytes()).hexdigest() == stack["requirements_sha256"]

    frozen_refs = {}
    for line in lock.read_text(encoding="utf-8").splitlines():
        if " @ git+" not in line:
            continue
        requirement = Requirement(line)
        frozen_refs[requirement.name] = requirement.url.rsplit("@", 1)[1]

    for repository in ("quant-data-kit", "quant-pipeline"):
        assert frozen_refs[repository] == stack["repositories"][repository]
