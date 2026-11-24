"""Verifikasi `ppa2` (docs/04 §30.17.7): vektor positif + negatif; `ppa1` ditolak."""

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
    canonical_payload,
    make_header,
    sid_hash,
    sign,
    verify_assertion,
)

KEY = ("0123456789abcdef" * 4).encode("ascii")
NOW = 1_795_000_000
BODY = b'{"row_version":3}'
SID = "A" * 21 + "-" + "b" * 21  # 43 karakter base64url (rekaan)


def claims(**over):
    base = {
        "aud": "prodplan",
        "bh": body_hash(b""),
        "exp": NOW + 60,
        "iat": NOW,
        "jti": "AAAAAAAAAAAAAAAAAAAAAA",
        "m": "GET",
        "p": "/v1/plans",
        "sid": SID,
    }
    base.update(over)
    return base


def raw_header(payload_json: str, key: bytes = KEY, prefix: str = "ppa2") -> str:
    payload = b64url_encode(payload_json.encode("utf-8"))
    mac = hmac.new(key, f"{prefix}.{payload}".encode("ascii"), hashlib.sha256).digest()
    return f"{prefix}.{payload}.{b64url_encode(mac)}"


def verify(header, *, method="GET", path="/v1/plans", body=b"", key=KEY, cache=None, now=NOW):
    return verify_assertion(
        header, method=method, path=path, body=body, key=key, jti_cache=cache or JtiCache(), now=now
    )


def reason(header, **kw):
    with pytest.raises(ActorInvalid) as exc:
        verify(header, **kw)
    return exc.value.reason


def test_valid_header_returns_sid():
    assert verify(make_header(claims(), KEY)) == SID
    assert verify(make_header(claims(sid="", p="/v1/meta"), KEY), path="/v1/meta") == ""


def test_signature_and_canonical_payload_vector():
    """Vektor byte-exact untuk insight-web: JSON tanpa spasi, 8 kunci berurutan, HMAC-SHA256('ppa2.' + payload)."""
    c = claims(m="POST", p="/v1/session", sid="", bh=body_hash(b'{"username":"u","password":"p","n":"x"}'))
    text = canonical_payload(c)
    assert text == (
        '{"aud":"prodplan","bh":"' + c["bh"] + '","exp":1795000060,"iat":1795000000,'
        '"jti":"AAAAAAAAAAAAAAAAAAAAAA","m":"POST","p":"/v1/session","sid":""}'
    )
    assert list(json.loads(text)) == list(CLAIM_KEYS)
    header = make_header(c, KEY)
    prefix, payload, sig = header.split(".")
    assert prefix == "ppa2"
    assert base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode() == text
    expected = hmac.new(KEY, b"ppa2." + payload.encode(), hashlib.sha256).digest()
    assert sig == base64.urlsafe_b64encode(expected).rstrip(b"=").decode() == sign(payload, KEY)
    assert body_hash(b"") == "47DEQpj8HBSa-_TImW-5JCeuQeRkm5NMpJWZG3hSuFU"


def test_vectors_identical_to_insight_web():
    """Cermin `services/insight-web/tests/prodplan-token.test.ts` (KEY 'ab'*32, SID 'S'*43, NOW, JTI sama)."""
    key = ("ab" * 32).encode("ascii")
    sid = "S" * 43
    empty = body_hash(b"")
    get = {"aud": "prodplan", "bh": empty, "exp": NOW + 60, "iat": NOW, "jti": "A" * 22, "m": "GET"}
    get.update(p="/v1/meta", sid=sid)
    assert canonical_payload(get) == (
        f'{{"aud":"prodplan","bh":"{empty}","exp":{NOW + 60},"iat":{NOW},"jti":"{"A" * 22}","m":"GET",'
        f'"p":"/v1/meta","sid":"{sid}"}}'
    )
    login_body = json.dumps({"username": "rina", "password": "rahasia", "n": "N" * 22}, separators=(",", ":"))
    login = {**get, "bh": body_hash(login_body.encode()), "m": "POST", "p": "/v1/session", "sid": ""}
    text = canonical_payload(login)
    assert text.endswith('"m":"POST","p":"/v1/session","sid":""}') and "rahasia" not in text
    header = make_header(get, key)
    payload = header.split(".")[1]
    assert header.split(".")[2] == b64url_encode(hmac.new(key, f"ppa2.{payload}".encode(), hashlib.sha256).digest())
    assert verify(header, path="/v1/meta", key=key) == sid


def test_sid_hash_is_hex_sha256():
    assert sid_hash(SID) == hashlib.sha256(SID.encode()).hexdigest() and len(sid_hash(SID)) == 64


def test_wrong_key():
    assert reason(make_header(claims(), b"f" * 64)) == "signature"


def test_ppa1_header_rejected():
    old = raw_header(json.dumps(claims(), separators=(",", ":")), prefix="ppa1")
    assert reason(old) == "format"


@pytest.mark.parametrize(
    "header",
    [None, "", "ppa1.xxxxxxxxxxxxxxxxxxxx.yyy", "ppa2.short.sig", "ppa2." + "A" * 1801 + "." + "B" * 43, "ppa2.a b"],
)
def test_bad_format(header):
    assert reason(header) == "format"


def test_expired_and_future_and_ttl():
    h = make_header(claims(), KEY)
    assert reason(h, now=NOW + 60) == "expired"
    assert reason(make_header(claims(iat=NOW + 31, exp=NOW + 91), KEY)) == "expired"
    assert verify(make_header(claims(iat=NOW + 30, exp=NOW + 90), KEY)) == SID
    assert reason(make_header(claims(exp=NOW + 120), KEY)) == "expired"


def test_replay_rejected():
    cache = JtiCache()
    h = make_header(claims(), KEY)
    verify(h, cache=cache)
    assert reason(h, cache=cache) == "replay"
    verify(make_header(claims(jti="BBBBBBBBBBBBBBBBBBBBBB"), KEY), cache=cache)


def test_binding_method_path_body():
    h = make_header(claims(m="POST", p="/v1/plans", bh=body_hash(BODY)), KEY)
    assert reason(h, method="PUT", path="/v1/plans", body=BODY) == "binding"
    assert reason(h, method="POST", path="/v1/plans?x=1", body=BODY) == "binding"
    assert reason(h, method="POST", path="/v1/plans", body=b'{"row_version":4}') == "body_hash"
    assert reason(h, method="POST", path="/v1/plans", body=b"") == "body_hash"


def test_extra_or_reordered_or_missing_or_old_keys():
    extra = dict(claims())
    extra["zzz"] = 1
    assert reason(raw_header(json.dumps(extra, separators=(",", ":")))) == "keys"
    reordered = dict(reversed(list(claims().items())))
    assert reason(raw_header(json.dumps(reordered, separators=(",", ":")))) == "keys"
    missing = dict(claims())
    missing.pop("sid")
    assert reason(raw_header(json.dumps(missing, separators=(",", ":")))) == "keys"
    # Klaim identitas ppa1 (sub/name/roles/codes) tidak lagi diterima.
    old = {**claims(), "sub": 42}
    assert reason(raw_header(json.dumps(old, separators=(",", ":")))) == "keys"
    text = json.dumps(claims(), separators=(",", ":"))
    assert reason(raw_header(text[:-1] + ',"sid":"x"}')) in {"payload", "keys"}


def test_wrong_audience():
    assert reason(make_header(claims(aud="insight"), KEY)) == "aud"


@pytest.mark.parametrize("over", [{"sid": "short"}, {"sid": "A" * 44}, {"sid": "A" * 42 + "="}, {"sid": None}])
def test_bad_sid_shape(over):
    assert reason(make_header(claims(**over), KEY)) == "claims"


def test_bad_time_types_and_jti():
    assert reason(make_header(claims(iat=str(NOW)), KEY)) == "time"
    assert reason(make_header(claims(jti="short"), KEY)) == "jti"


def test_jti_cache_ttl_and_bound():
    cache = JtiCache(ttl=120, max_size=2)
    assert cache.use("a", 0) and not cache.use("a", 10)
    assert cache.use("a", 121)
    assert cache.use("b", 121)
    assert not cache.use("c", 122)
    assert cache.use("c", 300)
