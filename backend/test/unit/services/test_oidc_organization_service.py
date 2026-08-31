from __future__ import annotations

import pytest

from yuxi.services.oidc_organization_service import department_code_chain, normalize_entity_code
from yuxi.storage.postgres.models_business import Department


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("JXI-BM46", ["JXI-BM46"]),
        ("jxi-bm4605", ["JXI-BM46", "JXI-BM4605"]),
        (" JXI-BM460503 ", ["JXI-BM46", "JXI-BM4605", "JXI-BM460503"]),
        ("WH-BM2203", ["WH-BM22", "WH-BM2203"]),
        ("cn-np-bm2203", ["CN-NP-BM22", "CN-NP-BM2203"]),
        ("BM22", ["BM22"]),
        ("bm2203", ["BM22", "BM2203"]),
        (" BM220301 ", ["BM22", "BM2203", "BM220301"]),
    ],
)
def test_department_code_chain_builds_two_digit_hierarchy(value, expected):
    assert department_code_chain(value) == expected


@pytest.mark.parametrize(
    "value",
    [None, "", "BM2", "BM220", "BM22A3", "JXI-BM4", "JXI-BM460", "JXI-BM46A5", "JXI_BM4605", "JXI!-BM4605"],
)
def test_department_code_chain_rejects_invalid_values(value):
    assert department_code_chain(value) is None


def test_normalize_entity_code_accepts_stable_code_and_rejects_invalid_values():
    assert normalize_entity_code(" hk600001 ") == "HK600001"
    assert normalize_entity_code("HK-600001_A") == "HK-600001_A"
    assert normalize_entity_code("HK 600001") is None
    assert normalize_entity_code(None) is None


def test_department_display_name_prefers_local_then_oidc_then_codes():
    department = Department(
        name="本地别名",
        oidc_name="OIDC 部门",
        department_code="JXI-BM4605",
        entity_code="HK600001",
    )
    assert department.display_name == "本地别名"

    department.name = None
    assert department.display_name == "OIDC 部门"
    department.oidc_name = None
    assert department.display_name == "JXI-BM4605"
    department.department_code = None
    assert department.display_name == "HK600001"
