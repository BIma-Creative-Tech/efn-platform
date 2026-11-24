"""Login per pengguna ESB + sesi memori (docs/04 §30.17.3–§30.17.5, §30.17.15). ESB palsu; tanpa kredensial nyata."""

import base64  # noqa: I001 - `test_access` = modul tetangga (bukan first-party), urutan blok sengaja
import json
import logging

import pytest

from efn_prodplan import metrics
from efn_prodplan.access import DEFAULT_DOC_ACCESS, parse_doc_access, parse_rules
from efn_prodplan.actor import sid_hash
from efn_prodplan.config import BrandConfig
from efn_prodplan.esb import EsbInvalidCredentials, EsbUnavailable, LoginResult
from efn_prodplan.logs import JsonFormatter
from efn_prodplan.provision import OdooProvisioner
from efn_prodplan.session import (
    LoginGate,
    LoginLimiter,
    LoginService,
    Session,
    SessionStore,
    token_expiry,
    user_mask,
    valid_login_shape,
)
from efn_prodplan.workflow import ApiError
from test_access import ADMIN_ACCESS

NOW = 1_795_000_000.0
USER = "Planner.Uji"
PASSWORD = "rahasia-rekaan-123"
REFRESH = "refresh-rekaan-999"


def jwt(claims):
    def seg(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{seg({'alg': 'HS256'})}.{seg(claims)}.c2lnLXJla2Fhbg"


TOKEN = jwt({"exp": int(NOW) + 3600, "userRoleID": 7, "username": "planner.uji", "fullName": "Planner Uji"})
BRANCHES = {"page": 1, "pageSize": 100, "dataCount": 2, "data": [{"branchID": 4, "branchName": "CK Rekaan"}]}
BRANCHES["data"].append({"branchID": 13, "branchName": "WH Rekaan"})


class FakeClient:
    def __init__(self, esb, token):
        self.esb = esb
        self.token = token

    def login(self, username, password):
        self.esb.logins.append((username, password))
        outcome = self.esb.login_outcome
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def get(self, path, params=None):
        self.esb.gets.append(path)
        value = self.esb.routes[path]
        if isinstance(value, Exception):
            raise value
        return value

    def logout(self):
        self.esb.logouts.append(self.token)


class FakeEsb:
    def __init__(self):
        self.logins = []
        self.gets = []
        self.logouts = []
        self.login_outcome = LoginResult(TOKEN, "Planner Uji", "CORP-UJI", True)
        self.routes = {"/user/access": ADMIN_ACCESS, "/branch/list/user": BRANCHES}


class FakeClients:
    def __init__(self, esb):
        self.esb = esb

    def make(self, token, bucket_key, **kw):
        return FakeClient(self.esb, token)

    def for_session(self, session, **kw):
        return FakeClient(self.esb, session.access_token)


class FakeActors:
    def __init__(self):
        self.ids = {}
        self.calls = []

    def upsert(self, esb_env, esb_brand, username_norm, display_name):
        self.calls.append((esb_env, esb_brand, username_norm, display_name))
        return self.ids.setdefault((esb_env, esb_brand, username_norm), 1_000_000 + len(self.ids))


class NoWaitGate(LoginGate):
    def __init__(self):
        super().__init__(sleep=lambda _s: None)


C1 = "c" * 22  # kunci klien `c` rekaan (sumber 1)
C2 = "d" * 22  # sumber 2


class _LoginService(LoginService):
    """`create` dengan kunci klien default C1 agar test lama tetap ringkas."""

    def create(self, username, password, client_key=C1):
        return super().create(username, password, client_key)


def service(esb=None, sessions=None, actors=None, provisioner=None, now=NOW):
    esb = esb or FakeEsb()
    return _LoginService(
        esb_env="staging",
        brands=[BrandConfig(key="pb", company_code="PB", esb_company_code="CORP-UJI", ck={4: (7,)})],
        planner_rules=parse_rules("B.4:view+insert;K.5:view;D.16:view;C.1:view", "P"),
        warehouse_rules=parse_rules("D.9:view+insert;D.4:view+insert;D.16:view", "W"),
        doc_access_map=parse_doc_access(DEFAULT_DOC_ACCESS),
        sessions=sessions if sessions is not None else SessionStore(clock=lambda: now),
        clients=FakeClients(esb),
        actors=actors or FakeActors(),
        provisioner=provisioner,
        gate=NoWaitGate(),
        clock=lambda: now,
    )


def code(fn, *args):
    with pytest.raises(ApiError) as exc:
        fn(*args)
    return exc.value.status, exc.value.code


# ---------------------------------------------------------------- sukses
def test_login_success_creates_session_and_upserts_actor():
    esb, actors, sessions = FakeEsb(), FakeActors(), SessionStore(clock=lambda: NOW)
    out = service(esb, sessions, actors).create(f"  {USER} ", PASSWORD)
    assert len(out["sid"]) == 43 and out["expires_at"] == "2026-11-18T12:06:40Z"  # NOW + 3600 (klaim exp)
    assert actors.calls == [("staging", "pb", "planner.uji", "Planner Uji")]
    assert esb.logins == [(USER, PASSWORD)]  # username di-trim, password apa adanya, SATU kali
    view = out["session"]
    assert view["roles"] == ["pp_planner", "pp_warehouse"] and view["esb_brand"] == "pb"
    assert view["branches"] == [{"branch_id": 4, "name": "CK Rekaan"}, {"branch_id": 13, "name": "WH Rekaan"}]
    assert view["doc_access"]["insert"] == ["goods_transfer_request", "simple_manufacturing", "simple_transfer"]
    assert view["actor_id"] == 1_000_000 and view["company_code"] == "PB"
    assert set(view) == {
        "actor_id",
        "name",
        "esb_env",
        "esb_brand",
        "company_code",
        "roles",
        "doc_access",
        "branches",
        "expires_at",
    }
    stored = sessions.get(sid_hash(out["sid"]))
    assert stored is not None and stored.access_token == TOKEN and stored.user_role_id == 7
    assert stored.exp == NOW + 3600 and esb.logouts == []


def test_token_never_leaves_process():
    out = service().create(USER, PASSWORD)
    text = json.dumps(out)
    for secret in (TOKEN, REFRESH, PASSWORD, "planner.uji", "userRoleID"):
        assert secret not in text


# ---------------------------------------------------------------- galat
def test_wrong_password_calls_esb_once_and_counts():
    esb = FakeEsb()
    esb.login_outcome = EsbInvalidCredentials("HTTP 400")
    svc = service(esb)
    assert code(svc.create, USER, PASSWORD) == (401, "invalid_credentials")
    assert len(esb.logins) == 1


def test_five_failures_lock_only_that_source_without_esb_call():
    esb = FakeEsb()
    esb.login_outcome = EsbInvalidCredentials("HTTP 400")
    svc = service(esb)
    for _ in range(5):
        assert code(svc.create, USER, PASSWORD, C1) == (401, "invalid_credentials")
    assert code(svc.create, USER.lower(), PASSWORD, C1) == (429, "login_locked")  # username dinormalisasi
    assert len(esb.logins) == 5
    # SEC-234: korban dari sumber lain tetap bisa masuk; username lain tidak terkunci
    esb.login_outcome = LoginResult(TOKEN, "X", "CORP-UJI", True)
    assert svc.create(USER, PASSWORD, C2)["sid"]
    assert svc.create("lain.user", PASSWORD, C1)["sid"]


def test_global_username_brake_locks_all_sources():
    now = [0.0]
    lim = LoginLimiter(clock=lambda: now[0])
    for i in range(30):  # 30 gagal/jam dari banyak sumber (masing-masing < 5)
        lim.failure("u", f"{i:022d}")
        now[0] += 60
    assert lim.locked("u", C2)  # sumber baru pun terkunci
    lim.success("u", C2)  # sukses hanya menghapus pasangan, bukan rem global
    assert lim.locked("u", C2)
    now[0] += 901
    assert not lim.locked("u", C2)


def test_limiter_window_expiry_and_success_clears_pair_only():
    now = [0.0]
    lim = LoginLimiter(clock=lambda: now[0])
    for _ in range(4):
        lim.failure("u", C1)
    now[0] = 901  # kegagalan lama keluar jendela 15 menit
    lim.failure("u", C1)
    assert not lim.locked("u", C1)
    for _ in range(4):
        lim.failure("u", C1)
    assert lim.locked("u", C1) and not lim.locked("u", C2)
    now[0] = 901 + 900
    assert not lim.locked("u", C1)
    lim.failure("v", C1)
    lim.success("v", C1)
    assert not lim.locked("v", C1)


def test_limiter_memory_is_bounded_and_swept(monkeypatch):
    from efn_prodplan import session as session_mod

    monkeypatch.setattr(session_mod, "LIMITER_MAX_KEYS", 3)
    now = [0.0]
    lim = LoginLimiter(clock=lambda: now[0])
    for i in range(5):
        lim.failure(f"user{i}", C1)
    assert lim.sizes() == (3, 3)  # penuh -> yang tertua dibuang
    now[0] = 3601
    lim.sweep()
    assert lim.sizes() == (0, 0)


def test_missing_or_invalid_client_key_is_400_without_esb():
    esb = FakeEsb()
    svc = service(esb)
    for bad in ("", "short", "c" * 23, "c" * 21 + "=", None):
        assert code(svc.create, USER, PASSWORD, bad) == (400, "invalid_request")
    assert esb.logins == []


def test_esb_unavailable_does_not_count_toward_lockout():
    esb = FakeEsb()
    esb.login_outcome = EsbUnavailable("5xx")
    svc = service(esb)
    for _ in range(6):
        assert code(svc.create, USER, PASSWORD) == (502, "esb_unavailable")
    assert len(esb.logins) == 6


def test_inactive_account_is_invalid_credentials_and_logs_out():
    esb = FakeEsb()
    esb.login_outcome = LoginResult(TOKEN, "X", "CORP-UJI", False)
    assert code(service(esb).create, USER, PASSWORD) == (401, "invalid_credentials")
    assert esb.logouts == [TOKEN]


def test_foreign_company_is_rejected_and_logged_out():
    esb = FakeEsb()
    esb.login_outcome = LoginResult(TOKEN, "X", "CORP-LAIN", True)
    assert code(service(esb).create, USER, PASSWORD) == (403, "esb_company_not_allowed")
    assert esb.logouts == [TOKEN] and esb.gets == []


def test_no_pp_role_or_no_branch_is_no_pp_access_and_logged_out():
    esb = FakeEsb()
    esb.routes["/user/access"] = [{"accessDetails": []}]
    assert code(service(esb).create, USER, PASSWORD) == (403, "no_pp_access")
    assert esb.logouts == [TOKEN]
    esb = FakeEsb()
    esb.routes["/branch/list/user"] = {"page": 1, "pageSize": 100, "dataCount": 0, "data": []}
    assert code(service(esb).create, USER, PASSWORD) == (403, "no_pp_access")


def test_access_lookup_failure_is_unavailable_and_logged_out():
    esb = FakeEsb()
    esb.routes["/user/access"] = EsbUnavailable("down")
    assert code(service(esb).create, USER, PASSWORD) == (502, "esb_unavailable")
    assert esb.logouts == [TOKEN]


@pytest.mark.parametrize(("username", "password"), [("", "x"), ("a b", "x"), ("x" * 65, "x"), ("u", ""), (None, "x")])
def test_bad_shape_is_invalid_credentials_without_esb(username, password):
    esb = FakeEsb()
    assert code(service(esb).create, username, password) == (401, "invalid_credentials")
    assert esb.logins == []
    assert not valid_login_shape(username, password)
    assert valid_login_shape("a@b.c_d-1", "p" * 128) and not valid_login_shape("a", "p" * 129)


def test_session_capacity():
    sessions = SessionStore(clock=lambda: NOW)
    for i in range(200):
        sessions.add(_session(f"{i:043d}", actor_id=i))
    esb = FakeEsb()
    assert code(service(esb, sessions).create, USER, PASSWORD) == (503, "session_capacity")
    assert esb.logins == []


# ---------------------------------------------------------------- sesi
def _session(sid, actor_id=1, exp=NOW + 3000, created=NOW):
    return Session(
        sid_hash=sid_hash(sid),
        actor_id=actor_id,
        esb_env="staging",
        esb_brand="pb",
        company_code="PB",
        username_norm="planner.uji",
        name="Planner Uji",
        user_role_id=7,
        access_token=f"tok-{sid[:4]}",
        exp=exp,
        created_at=created,
    )


def test_three_sessions_per_actor_oldest_evicted_and_logged_out():
    esb, sessions = FakeEsb(), SessionStore(clock=lambda: NOW)
    sids = [service(esb, sessions, now=NOW + i).create(USER, PASSWORD)["sid"] for i in range(4)]
    assert sessions.get(sid_hash(sids[0])) is None
    assert all(sessions.get(sid_hash(s)) is not None for s in sids[1:])
    assert len(esb.logouts) == 1


def test_store_expiry_token_for_and_repr():
    now = [NOW]
    store = SessionStore(clock=lambda: now[0])
    store.add(_session("a" * 43, exp=NOW + 100))
    store.add(_session("b" * 43, exp=NOW + 3000))
    best = store.token_for(1, "staging", "pb", 120)
    assert best is not None and best.access_token == "tok-bbbb"
    assert store.token_for(1, "staging", "pb", 3001) is None  # sisa token < min_remaining
    assert store.token_for(2, "staging", "pb", 30) is None
    assert store.token_for(1, "staging", "wtz", 30) is None
    now[0] = NOW + 101
    assert store.get(sid_hash("a" * 43)) is None
    now[0] = NOW + 3001
    assert store.sweep() == 1 and len(store) == 0
    text = repr(_session("c" * 43)) + repr(_session("c" * 43).actor())
    for secret in ("tok-cccc", "planner.uji", "Planner Uji", sid_hash("c" * 43)):
        assert secret not in text


def test_logout_removes_session_and_calls_esb_logout():
    esb, sessions = FakeEsb(), SessionStore(clock=lambda: NOW)
    svc = service(esb, sessions)
    sid = svc.create(USER, PASSWORD)["sid"]
    session = sessions.get(sid_hash(sid))
    svc.logout(session)
    assert sessions.get(sid_hash(sid)) is None and esb.logouts == [TOKEN]


def test_token_expiry_rules():
    assert token_expiry(jwt({"exp": int(NOW) + 1800}), NOW) == NOW + 1800
    assert token_expiry(jwt({"exp": int(NOW) + 7200}), NOW) == NOW + 3540  # > now+3600
    assert token_expiry("rusak", NOW) == NOW + 3540
    assert token_expiry(jwt({"exp": "1"}), NOW) == NOW + 3540


def test_user_mask():
    # SEC-239: <= 6 karakter -> hanya karakter pertama
    assert user_mask("abc") == "a***" and user_mask("rina1") == "r***" and user_mask("budi.c") == "b***"
    assert user_mask("planner.uji") == "pl***i" and user_mask("abcdefg") == "ab***g"


def test_gate_spaces_logins():
    now = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    gate = LoginGate(clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        with gate.slot():
            pass
    assert slept == [1.0, 1.0]


# ---------------------------------------------------------------- log/metrik bebas rahasia
def test_logs_and_metrics_never_contain_secrets(caplog):
    caplog.set_level(logging.INFO, logger="efn_prodplan")
    esb, sessions = FakeEsb(), SessionStore(clock=lambda: NOW)
    svc = service(esb, sessions)
    out = svc.create(USER, PASSWORD)
    svc.logout(sessions.get(sid_hash(out["sid"])))
    esb.login_outcome = EsbInvalidCredentials("HTTP 400")
    with pytest.raises(ApiError) as exc:
        svc.create(USER, PASSWORD)
    esb.routes["/user/access"] = RuntimeError(PASSWORD)  # exception membawa teks rahasia
    esb.login_outcome = LoginResult(TOKEN, "Planner Uji", "CORP-UJI", True)
    with pytest.raises(ApiError):
        svc.create(USER, PASSWORD)
    formatted = "\n".join(JsonFormatter().format(r) for r in caplog.records)
    blob = formatted + metrics.render() + str(exc.value) + json.dumps(exc.value.body())
    for secret in (PASSWORD, TOKEN, REFRESH, out["sid"], sid_hash(out["sid"]), "planner.uji", "Planner Uji"):
        assert secret not in blob, secret
    assert '"event":"pp_login"' in formatted and '"user_mask":"pl***i"' in formatted
    assert 'prodplan_login_total{result="ok"}' in metrics.render()


# ---------------------------------------------------------------- provisioning Odoo (non-blocking)
def test_provisioning_enqueued_after_login_without_blocking():
    sent = []

    def slow_send(body):
        raise TimeoutError("odoo 503")

    prov = OdooProvisioner(slow_send, enabled=True)
    out = service(provisioner=prov).create(USER, PASSWORD)
    assert out["sid"]  # login sukses walau Odoo mati (belum ada pengiriman di thread request)
    assert prov.run_once() is True  # thread latar: gagal -> metrik failed, tidak melempar
    assert sent == []
