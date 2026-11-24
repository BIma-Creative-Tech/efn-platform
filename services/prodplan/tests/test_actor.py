"""Verifikasi `ppa1` (docs/04 §30.7): vektor positif + negatif."""

import base64
import hashlib
import hmac
import json

import pytest

from efn_prodplan.actor import (
    CLAIM_KEYS,
    ActorInvalid,
    JtiCache,
    b64url_encode,
    body_hash,
    make_header,
    sign,
    verify_actor,
)

KEY = ("0123456789abcdef" * 4).encode("ascii")
NOW = 1_795_000_000
BODY = b'{"row_version":3}'


def claims(**over):
    base = {
        "aud": "prodplan",
        "bh": body_hash(b""),
        "codes": ["PB"],
        "exp": NOW + 60,
        "iat": NOW,
        "jti": "AAAAAAAAAAAAAAAAAAAAAA",
        "m": "GET",
        "name": "Planner Satu",
        "p": "/v1/meta",
        "roles": ["pp_planner"],
        "sub": 42,
    }
    base.update(over)
    return base


def raw_header(payload_json: str, key: bytes = KEY) -> str:
    payload = b64url_encode(payload_json.encode("utf-8"))
    return f"ppa1.{payload}.{sign(payload, key)}"


def verify(header, *, method="GET", path="/v1/meta", body=b"", key=KEY, cache=None, now=NOW):
    return verify_actor(header, method=method, path=path, body=body, key=key, jti_cache=cache or JtiCache(), now=now)


def reason(header, **kw):
    with pytest.raises(ActorInvalid) as exc:
        verify(header, **kw)
    return exc.value.reason


def test_valid_header():
    actor = verify(make_header(claims(), KEY))
    assert actor.sub == 42 and actor.name == "Planner Satu"
    assert actor.roles == frozenset({"pp_planner"}) and actor.codes == frozenset({"PB"})


def test_signature_is_hmac_sha256_over_prefix_and_payload_with_ascii_hex_key():
    header = make_header(claims(), KEY)
    _, payload, sig = header.split(".")
    expected = hmac.new(KEY, b"ppa1." + payload.encode(), hashlib.sha256).digest()
    assert sig == base64.urlsafe_b64encode(expected).rstrip(b"=").decode()
    decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
    assert decoded == json.dumps(claims(), separators=(",", ":"))
    assert list(json.loads(decoded)) == list(CLAIM_KEYS)
    assert body_hash(b"") == "47DEQpj8HBSa-_TImW-5JCeuQeRkm5NMpJWZG3hSuFU"


def test_post_with_body_and_query_path():
    c = claims(m="POST", p="/v1/plans/x/submit?a=1", bh=body_hash(BODY), roles=["pp_planner", "pp_warehouse"])
    h = make_header(c, KEY)
    actor = verify(h, method="POST", path="/v1/plans/x/submit?a=1", body=BODY)
    assert actor.has("pp_warehouse")


def test_wrong_key():
    assert reason(make_header(claims(), b"f" * 64)) == "signature"


def test_tampered_payload():
    header = make_header(claims(), KEY)
    prefix, payload, sig = header.split(".")
    other = b64url_encode(json.dumps(claims(sub=1), separators=(",", ":")).encode())
    assert reason(f"{prefix}.{other}.{sig}") == "signature"
    assert payload != other


@pytest.mark.parametrize(
    "header",
    [None, "", "ppa2.xxxxxxxxxxxxxxxxxxxx.yyy", "ppa1.short.sig", "ppa1." + "A" * 1801 + "." + "B" * 43, "ppa1.a b"],
)
def test_bad_format(header):
    assert reason(header) == "format"


def test_expired_and_future_and_ttl():
    h = make_header(claims(), KEY)
    assert reason(h, now=NOW + 60) == "expired"
    assert reason(make_header(claims(iat=NOW + 31, exp=NOW + 91), KEY)) == "expired"
    assert verify(make_header(claims(iat=NOW + 30, exp=NOW + 90), KEY)).sub == 42
    assert reason(make_header(claims(exp=NOW + 120), KEY)) == "expired"


def test_replay_rejected():
    cache = JtiCache()
    h = make_header(claims(), KEY)
    verify(h, cache=cache)
    assert reason(h, cache=cache) == "replay"
    # jti lain lolos
    verify(make_header(claims(jti="BBBBBBBBBBBBBBBBBBBBBB"), KEY), cache=cache)


def test_binding_method_path_body():
    h = make_header(claims(m="POST", p="/v1/plans", bh=body_hash(BODY)), KEY)
    assert reason(h, method="PUT", path="/v1/plans", body=BODY) == "binding"
    assert reason(h, method="POST", path="/v1/plans?x=1", body=BODY) == "binding"
    assert reason(h, method="POST", path="/v1/plans", body=b'{"row_version":4}') == "body_hash"
    assert reason(h, method="POST", path="/v1/plans", body=b"") == "body_hash"


def test_extra_or_reordered_or_duplicate_keys():
    extra = dict(claims())
    extra["zzz"] = 1
    assert reason(raw_header(json.dumps(extra, separators=(",", ":")))) == "keys"
    reordered = dict(reversed(list(claims().items())))
    assert reason(raw_header(json.dumps(reordered, separators=(",", ":")))) == "keys"
    missing = dict(claims())
    missing.pop("name")
    assert reason(raw_header(json.dumps(missing, separators=(",", ":")))) == "keys"
    text = json.dumps(claims(), separators=(",", ":"))
    duplicated = text[:-1] + ',"sub":43}'
    assert reason(raw_header(duplicated)) in {"payload", "keys"}


def test_wrong_audience():
    assert reason(make_header(claims(aud="insight"), KEY)) == "aud"


@pytest.mark.parametrize(
    "over",
    [
        {"roles": ["pp_warehouse", "pp_planner"]},
        {"roles": []},
        {"roles": ["exec"]},
        {"codes": ["XX"]},
        {"codes": []},
        {"codes": ["WTZ", "PB"]},
        {"sub": True},
        {"sub": 0},
        {"sub": "42"},
        {"name": ""},
        {"name": "x" * 65},
    ],
)
def test_bad_claim_types(over):
    assert reason(make_header(claims(**over), KEY)) == "claims"


def test_bad_time_types_and_jti():
    assert reason(make_header(claims(iat=str(NOW)), KEY)) == "time"
    assert reason(make_header(claims(jti="short"), KEY)) == "jti"


def test_jti_cache_ttl_and_bound():
    cache = JtiCache(ttl=120, max_size=2)
    assert cache.use("a", 0) and not cache.use("a", 10)
    assert cache.use("a", 121)  # kedaluwarsa -> boleh lagi
    assert cache.use("b", 121)
    assert not cache.use("c", 122)  # penuh, belum ada yang kedaluwarsa -> fail closed
    assert cache.use("c", 300)
