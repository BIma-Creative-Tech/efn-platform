"""Format bundel §27.3/§27.3a: roundtrip, tamper ditolak, batas gzip, kunci."""

import gzip
import io
import json
import tarfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from efn_mig import bundle as b
from efn_mig.bundle import IntegrityError

RUN_ID = "11111111-2222-4333-8444-555555555555"


def manifest(files: list[dict], maps: list[dict] | None = None) -> dict:
    return {"run_id": RUN_ID, "brand": "pb", "company_codes": ["PB"], "files": files, "maps": maps or []}


def make(keys: b.Keys, members: dict[str, bytes] | None = None) -> bytes:
    members = members or {}
    if not members:
        blob, rows = b.jsonl_bytes([{"a": 1}, {"a": 2}])
        gz = b.gzip_bytes(b.jsonl_bytes([{"x": i} for i in range(5)])[0])
        members = {"product.jsonl": blob, "legacy_flow_daily.jsonl.gz": gz, "maps/uom_map.csv": b"h\nEA,x,1\n"}
        files = [
            b.file_entry("product.jsonl", "product", blob, rows),
            b.file_entry("legacy_flow_daily.jsonl.gz", "legacy_flow_daily", gz, 5),
        ]
        maps = [{"name": "maps/uom_map.csv", "sha256": b.sha256_hex(members["maps/uom_map.csv"]), "rows": 1}]
        return b.build_bundle(manifest(files, maps), members, keys)
    return b.build_bundle(manifest([]), members, keys)


def reencrypt(keys: b.Keys, header: bytes, members: dict[str, bytes]) -> bytes:
    """Bundel dengan isi tar arbitrer, terenkripsi kunci yang BENAR (uji pemeriksaan setelah dekripsi)."""
    tar = b._tar(sorted(members.items()))
    nonce = b"\x01" * 12
    return header + nonce + AESGCM(keys.enc).encrypt(nonce, tar, header)


def test_roundtrip_and_rows(keys: b.Keys) -> None:
    opened = b.open_bundle(make(keys), keys, expect_kind="bundle")
    assert opened.header == {"brand": "pb", "format": b.FORMAT, "kind": "bundle", "run_id": RUN_ID}
    assert [r["a"] for r in opened.rows("product.jsonl")] == [1, 2]
    assert [r["x"] for r in opened.rows("legacy_flow_daily.jsonl.gz")] == [0, 1, 2, 3, 4]
    assert opened.entity_file("product") == "product.jsonl"
    assert len(opened.manifest_sha256) == 64


def test_tar_and_gzip_are_deterministic() -> None:
    members = [("a.jsonl", b"{}\n"), ("b.jsonl", b"{}\n")]
    assert b._tar(members) == b._tar(list(reversed(members)))
    assert b.gzip_bytes(b"abc") == b.gzip_bytes(b"abc")


def test_keys_are_separated_and_validated() -> None:
    keys = b.derive_keys("ab" * 32)
    assert keys.enc != keys.mac and len(keys.enc) == 32
    assert "ab" not in repr(keys)
    for bad in ("ab" * 31, "zz" * 32, "a" * 65):  # pendek, bukan hex, panjang ganjil
        with pytest.raises(b.BundleKeyError):
            b.derive_keys(bad)


def test_wrong_key_rejected(keys: b.Keys) -> None:
    with pytest.raises(IntegrityError, match="dekripsi"):
        b.open_bundle(make(keys), b.derive_keys("cd" * 32))


def test_ciphertext_tamper_rejected(keys: b.Keys) -> None:
    blob = bytearray(make(keys))
    blob[-20] ^= 0x01
    with pytest.raises(IntegrityError):
        b.open_bundle(bytes(blob), keys)


def test_header_tamper_rejected(keys: b.Keys) -> None:
    blob = make(keys)
    header, raw = b.parse_header(blob)
    forged = b.canonical_json({**header, "brand": "wtz"}) + b"\n"
    with pytest.raises(IntegrityError):  # AAD berubah -> tag gagal
        b.open_bundle(forged + blob[len(raw) :], keys)
    with pytest.raises(IntegrityError, match="kind"):
        b.open_bundle(blob, keys, expect_kind="result")


def test_manifest_sig_tamper_rejected(keys: b.Keys) -> None:
    data, rows = b.jsonl_bytes([{"a": 1}])
    signed = b.sign_manifest(
        {**manifest([b.file_entry("product.jsonl", "product", data, rows)]), "format": b.FORMAT, "kind": "bundle"},
        keys,
    )
    signed["company_codes"] = ["WTZ"]  # diubah setelah ditandatangani
    header = b.header_bytes(RUN_ID, "pb", "bundle")
    blob = reencrypt(keys, header, {"manifest.json": b.canonical_json(signed), "product.jsonl": data})
    with pytest.raises(IntegrityError, match="HMAC"):
        b.open_bundle(blob, keys)


def _signed_blob(keys: b.Keys, entries: list[dict], members: dict[str, bytes], brand: str = "pb") -> bytes:
    signed = b.sign_manifest({**manifest(entries), "brand": brand, "format": b.FORMAT, "kind": "bundle"}, keys)
    header = b.header_bytes(RUN_ID, "pb", "bundle")
    return reencrypt(keys, header, {"manifest.json": b.canonical_json(signed), **members})


def test_file_hash_rows_and_listing_checked(keys: b.Keys) -> None:
    data, rows = b.jsonl_bytes([{"a": 1}, {"a": 2}])
    entry = b.file_entry("product.jsonl", "product", data, rows)
    with pytest.raises(IntegrityError, match="sha256"):
        b.open_bundle(_signed_blob(keys, [entry], {"product.jsonl": data + b'{"a":3}\n'}), keys)
    with pytest.raises(IntegrityError, match="baris"):
        b.open_bundle(_signed_blob(keys, [{**entry, "rows": 3}], {"product.jsonl": data}), keys)
    with pytest.raises(IntegrityError, match="daftar"):
        b.open_bundle(_signed_blob(keys, [entry], {"product.jsonl": data, "extra.jsonl": b""}), keys)
    with pytest.raises(IntegrityError, match="header"):
        b.open_bundle(_signed_blob(keys, [entry], {"product.jsonl": data}, brand="wtz"), keys)


def test_tar_member_names_restricted(keys: b.Keys) -> None:
    header = b.header_bytes(RUN_ID, "pb", "bundle")
    for name in ("../evil.jsonl", "/abs.jsonl", "x.sh"):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name)
            info.size = 0
            tar.addfile(info, io.BytesIO(b""))
        nonce = b"\x02" * 12
        blob = header + nonce + AESGCM(keys.enc).encrypt(nonce, buf.getvalue(), header)
        with pytest.raises(IntegrityError, match="tidak diizinkan"):
            b.open_bundle(blob, keys)


def test_gzip_bomb_rejected() -> None:
    bomb = gzip.compress(b"\0" * (4 * 1024 * 1024))  # rasio ~1000x, > 1 MiB terdekompresi
    with pytest.raises(IntegrityError, match="rasio"):
        b.count_lines("x.jsonl.gz", bomb)
    with pytest.raises(IntegrityError, match="gzip rusak"):
        b.count_lines("x.jsonl.gz", b"bukan gzip")


def test_jsonl_rejects_nan_and_non_object() -> None:
    with pytest.raises(IntegrityError):
        list(b.iter_jsonl("x.jsonl", b'{"a": NaN}\n'))
    with pytest.raises(IntegrityError):
        list(b.iter_jsonl("x.jsonl", b"[1,2]\n"))
    with pytest.raises(ValueError):
        b.jsonl_bytes([{"a": float("nan")}])


def test_build_rejects_unlisted_or_bad_names(keys: b.Keys) -> None:
    with pytest.raises(ValueError):
        b.build_bundle(manifest([]), {"product.jsonl": b""}, keys)
    with pytest.raises(ValueError):
        b.header_bytes(RUN_ID, "pb", "other")


def test_three_hkdf_keys_distinct() -> None:
    keys = b.derive_keys("ab" * 32)
    assert len({keys.enc, keys.mac, keys.val}) == 3 and len(keys.val) == 32


def test_validation_report_signed_and_bound(tmp_path: Path, keys: b.Keys) -> None:
    path = tmp_path / "x.efnmig"
    path.write_bytes(make(keys))
    bundle = b.read_bundle(path, keys)
    assert bundle.blob_sha256 == b.sha256_hex(path.read_bytes())
    report = {"run_id": RUN_ID, "bundle_sha256": bundle.blob_sha256, "manifest_sha256": bundle.manifest_sha256,
              "errors": 0, "warnings": 1, "findings": []}  # fmt: skip
    signed = b.sign_validation(report, keys)
    b.verify_validation(json.loads(json.dumps(signed, indent=1)), keys, bundle)  # format file bebas, kanonik ulang
    for forged in ({**signed, "errors": 0, "warnings": 0}, {**signed, "bundle_sha256": "0" * 64}):
        with pytest.raises(IntegrityError, match="tanda tangan"):
            b.verify_validation(forged, keys, bundle)
    with pytest.raises(IntegrityError):  # kunci lain (mis. kunci MAC manifest) tidak sah
        b.verify_validation(b.sign_validation(report, b.derive_keys("cd" * 32)), keys, bundle)
    other = b.sign_validation({**report, "bundle_sha256": "1" * 64}, keys)
    with pytest.raises(IntegrityError, match="bundle_sha256"):
        b.verify_validation(other, keys, bundle)
    with pytest.raises(ValueError, match="error"):
        b.verify_validation(b.sign_validation({**report, "errors": 2}, keys), keys, bundle)
    with pytest.raises(IntegrityError):
        b.verify_validation({k: v for k, v in signed.items() if k != "sig"}, keys, bundle)
    with pytest.raises(ValueError):
        b.sign_validation({"run_id": RUN_ID}, keys)


def test_bundle_size_limit_checked_before_read(tmp_path: Path, keys: b.Keys, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "big.efnmig"
    blob = make(keys)
    path.write_bytes(blob)
    with pytest.raises(IntegrityError, match="melebihi batas"):
        b.read_bundle(path, keys, limit=len(blob) - 1)
    with pytest.raises(IntegrityError, match="melebihi batas"):
        b.open_bundle(blob, keys, limit=len(blob) - 1)
    monkeypatch.setenv(b.MAX_BUNDLE_ENV, "2048")
    with pytest.raises(IntegrityError, match="melebihi batas"):
        b.read_bundle(path, keys)
    monkeypatch.setenv(b.MAX_BUNDLE_ENV, "bukan-angka")
    with pytest.raises(ValueError):
        b.max_bundle_bytes()
    monkeypatch.delenv(b.MAX_BUNDLE_ENV)
    assert b.max_bundle_bytes() == b.DEFAULT_MAX_BUNDLE_BYTES


def test_write_secure_refuses_existing_part_and_symlink(tmp_path: Path) -> None:
    target = tmp_path / "out.efnmig"
    b.write_secure(target, b"satu")
    b.write_secure(target, b"dua")  # target lama diganti atomik
    assert target.read_bytes() == b"dua" and not (tmp_path / "out.efnmig.part").exists()
    (tmp_path / "out.efnmig.part").write_bytes(b"sisa")
    with pytest.raises(FileExistsError):
        b.write_secure(target, b"tiga")
    assert target.read_bytes() == b"dua"
    victim = tmp_path / "korban.txt"
    victim.write_bytes(b"jangan ditimpa")
    link = tmp_path / "x.efnmig.part"
    try:
        link.symlink_to(victim)
    except OSError:
        pytest.skip("symlink tidak didukung di platform ini")
    with pytest.raises(FileExistsError):
        b.write_secure(tmp_path / "x.efnmig", b"serang")
    assert victim.read_bytes() == b"jangan ditimpa"
    if b._NOFOLLOW:
        target_link = tmp_path / "baca.efnmig"
        target_link.symlink_to(victim)
        with pytest.raises(IntegrityError):
            b.read_limited(target_link, 1024)


def test_header_must_be_canonical(keys: b.Keys) -> None:
    blob = make(keys)
    _, raw = b.parse_header(blob)
    spaced = json.dumps(json.loads(raw)).encode() + b"\n"
    with pytest.raises(IntegrityError, match="kanonik"):
        b.parse_header(spaced + blob[len(raw) :])
