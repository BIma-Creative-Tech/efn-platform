import hashlib

import pytest

from efn_text2sql.scope import Scope, ScopeError


def test_settings_sorted_dedup_and_signed() -> None:
    scope = Scope.build([3, 1, 3], ["PB", "BCR"])
    s = scope.settings("ab" * 16)
    assert s["SQL_efn_cids"] == "1,3"
    assert s["SQL_efn_codes"] == "BCR,PB"
    assert s["SQL_efn_sig"] == hashlib.sha256(("ab" * 16 + "|1,3|BCR,PB").encode()).hexdigest()


def test_signature_depends_on_key_and_scope() -> None:
    a = Scope.build([1], ["PB"]).settings("a" * 32)["SQL_efn_sig"]
    assert a != Scope.build([1, 2], ["PB"]).settings("a" * 32)["SQL_efn_sig"]
    assert a != Scope.build([1], ["PB"]).settings("b" * 32)["SQL_efn_sig"]


@pytest.mark.parametrize(
    ("ids", "codes"),
    [
        ([], ["PB"]),
        ("1", ["PB"]),
        ([0], []),
        ([-1], []),
        ([True], []),
        (["1"], []),
        (list(range(1, 40)), []),
        ([1], ["XX"]),
        ([1], ["pb"]),
        ([1], "PB"),
        ([1], ["PB'; DROP"]),
    ],
)
def test_invalid_scope_rejected(ids: object, codes: object) -> None:
    with pytest.raises(ScopeError):
        Scope.build(ids, codes)
