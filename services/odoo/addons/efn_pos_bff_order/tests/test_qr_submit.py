"""E-27 submit/menu/status (docs/08 §13.1b test 1-12, 16 disesuaikan kontrak docs/04 §14.A.5 + L-70/L-76). Tag efn."""

import json
import uuid
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import EfnQrCommon


@tagged("post_install", "-at_install", "efn")
class TestEfnQrSubmit(EfnQrCommon):
    def _assert_qr_error(self, code, func, *args, **kwargs):
        # Savepoint = rollback seperti transaksi /json/2 yang gagal (order/kiriman yang sempat dibuat ikut batal).
        with self.assertRaises(UserError) as ctx, self.env.cr.savepoint():
            func(*args, **kwargs)
        self.env.invalidate_all()
        message = str(ctx.exception.args[0])
        # r15: item_unavailable boleh membawa sufiks id (`efn_qr:item_unavailable:<id>,<id>`); prefiks tetap stabil.
        self.assertTrue(message == f"efn_qr:{code}" or message.startswith(f"efn_qr:{code}:"), message)
        return message

    def _menu_all(self):
        """Semua halaman menu (DB uji bisa berisi produk POS demo lain > 200)."""
        Qr = self.env["efn.pos.qr.order"].with_user(self.tech)
        cursor, items, first = None, [], None
        for _page in range(100):
            menu = Qr.efn_qr_menu(config_id=self.config.id, cursor=cursor)
            first = first or menu
            items += menu["items"]
            cursor = menu["next_cursor"]
            if not cursor:
                break
        return first, {item["product_id"]: item for item in items}

    def _line(self, product, qty=1, values=None, note=None):
        values = values or self.env["product.template.attribute.value"]
        line = {"product_id": product.id, "qty": qty, "attribute_value_ids": values.ids}
        if note is not None:
            line["note"] = note
        return line

    # 1 ------------------------------------------------------------------------------------------------
    def test_01_submit_creates_draft_order_dine_in_taxes(self):
        _result, session, anchor = self._open()
        result = self._submit(session, [self._line(self.latte, 1, self.ptav_normal)])
        order = self._qr_order(result).order_id
        self.assertEqual(order.state, "draft")
        self.assertEqual(order.table_id, self.table)
        self.assertEqual(order.preset_id, self.preset)
        self.assertEqual(order.fiscal_position_id, self.fp_dine)
        self.assertFalse(order.partner_id)
        self.assertNotEqual(order, anchor, "L-70: kiriman = order baru")
        self.assertEqual(order.efn_qr_table_session_id, session)
        # Rp100.000 + SC 5% (masuk DPP PB1) + PB1 10% = 115.500 (docs/04 §10.4 / ADR-027).
        self.assertAlmostEqual(order.amount_total, 115500.0, places=2)
        self.assertAlmostEqual(order.amount_tax, 15500.0, places=2)
        self.assertEqual(result["state"], "pending_confirmation", "L-71 default pelayan")
        self.assertEqual(result["amount_indicative_excl"], 100000.0)
        # L-78: modifier ikut di hasil (BFF mengganti tampilan kiriman dengan baris ini).
        self.assertEqual(
            result["lines"],
            [
                {
                    "product_id": self.latte.id,
                    "name": self.latte.display_name,
                    "qty": 1,
                    "attribute_value_ids": self.ptav_normal.ids,
                    "attributes_text": "Gula (uji QR): Normal",
                }
            ],
        )
        self.assertEqual(set(result), {"submission_ref", "state", "order_uuid", "lines", "amount_indicative_excl"})
        # K1 r15: uuid order kiriman ada di hasil submit (BFF mengenali order QR untuk earn_pending).
        self.assertEqual(result["order_uuid"], order.uuid)
        self.assertEqual(self._qr_order(result).order_uuid, order.uuid)

    def test_01d_result_lines_carry_sorted_modifiers_and_null_text_without_any(self):
        _r, session, _a = self._open()
        result = self._submit(
            session, [self._line(self.latte, 2, self.ptav_less | self.ptav_shot), self._line(self.croissant)]
        )
        latte_line, croissant_line = result["lines"]
        self.assertEqual(latte_line["attribute_value_ids"], sorted((self.ptav_less | self.ptav_shot).ids))
        self.assertEqual(latte_line["attributes_text"], "Gula (uji QR): Kurang; Ekstra (uji QR): Shot espresso")
        self.assertEqual((croissant_line["attribute_value_ids"], croissant_line["attributes_text"]), ([], None))

    def test_01b_menu_prices_and_tax_labels(self):
        menu, items = self._menu_all()
        self.assertEqual(menu["config"]["company_code"], "PB")
        self.assertEqual(menu["config"]["confirm_mode"], "waiter")
        self.assertTrue(menu["config"]["qr_enabled"])
        latte = items[self.latte.id]
        self.assertEqual(latte["price_excl"], 100000.0)
        self.assertEqual(latte["tax_labels"], ["Service 5%", "PB1 10%"])
        attrs = {a["attribute_id"]: a for a in latte["attributes"]}
        self.assertEqual(set(attrs), {self.attr_sugar.id, self.attr_extra.id}, "nilai is_custom tidak ditawarkan")
        self.assertTrue(attrs[self.attr_sugar.id]["required"])
        self.assertFalse(attrs[self.attr_extra.id]["required"])
        self.assertEqual(attrs[self.attr_extra.id]["values"][0]["price_extra_excl"], 6000.0)
        self.assertFalse(items[self.hidden.id]["available"], "self_order_available=False -> tidak tersedia")
        self.assertNotIn(self.not_pos.id, items)
        self.assertEqual(menu["deleted"], [])
        # Paging.
        page = self.env["efn.pos.qr.order"].with_user(self.tech).efn_qr_menu(config_id=self.config.id, limit=1)
        self.assertEqual(len(page["items"]), 1)
        self.assertTrue(page["next_cursor"])

    def test_01c_menu_delta_reports_deleted(self):
        since = fields.Datetime.now() - timedelta(seconds=1)
        self.croissant.product_tmpl_id.available_in_pos = False
        self.env.flush_all()
        old = since - timedelta(hours=1)
        # Transaksi uji = satu cap waktu: atur write_date eksplisit (pengaturan lama, produk berubah).
        for model in (
            "pos.config",
            "pos.preset",
            "account.fiscal.position",
            "account.tax",
            "product.pricelist",
            "product.pricelist.item",
            "product.template.attribute.value",
            "product.template",
            "product.product",
        ):
            self.env.cr.execute(f"UPDATE {self.env[model]._table} SET write_date = %s", [old])  # noqa: S608
        self.env.cr.execute(
            "UPDATE product_template SET write_date = %s WHERE id = %s",
            [since + timedelta(seconds=5), self.croissant.product_tmpl_id.id],
        )
        self.env.invalidate_all()
        menu = (
            self.env["efn.pos.qr.order"]
            .with_user(self.tech)
            .efn_qr_menu(config_id=self.config.id, since=since.isoformat() + "Z")
        )
        self.assertIn(self.croissant.id, menu["deleted"])
        ids = [i["product_id"] for i in menu["items"]]
        self.assertNotIn(self.latte.id, ids, "delta: produk tak berubah tidak dikirim")

    # 2 ------------------------------------------------------------------------------------------------
    def test_02_client_price_ignored_and_attribute_extra(self):
        _r, session, _a = self._open()
        line = self._line(self.latte, 2, self.ptav_less | self.ptav_shot)
        self._assert_qr_error("invalid_input", self._submit, session, [{**line, "price_unit": 1}])
        result = self._submit(session, [line])
        order_line = self._qr_order(result).order_id.lines
        self.assertEqual(order_line.price_unit, 106000.0, "harga pricelist + price_extra atribut, dihitung server")
        self.assertEqual(order_line.qty, 2)
        self.assertEqual(order_line.attribute_value_ids, self.ptav_less | self.ptav_shot)

    # 3/5 ----------------------------------------------------------------------------------------------
    def test_03_unavailable_products(self):
        _r, session, _a = self._open()
        other_company = self.env["res.company"].create({"name": "PT Lain (uji QR)"})
        foreign = self.env["product.product"].create(
            {"name": "Produk PT lain", "available_in_pos": True, "company_id": other_company.id}
        )
        for product in (self.hidden, self.not_pos, foreign):
            with self.subTest(product=product.name):
                message = self._assert_qr_error("item_unavailable", self._submit, session, [self._line(product)])
                self.assertEqual(message, f"efn_qr:item_unavailable:{product.id}", "r15: id produk di sufiks")
        both = self._assert_qr_error(
            "item_unavailable", self._submit, session, [self._line(self.not_pos), self._line(self.hidden)]
        )
        ids = ",".join(str(i) for i in sorted((self.hidden | self.not_pos).ids))
        self.assertEqual(both, f"efn_qr:item_unavailable:{ids}")
        self.config.write({"limit_categories": True, "iface_available_categ_ids": [Command.set(self.categ_bar.ids)]})
        self._assert_qr_error("item_unavailable", self._submit, session, [self._line(self.croissant)])
        self.assertFalse(self.env["efn.pos.qr.order"].search([("table_session_id", "=", session.id)]))

    def test_03b_combo_product_excluded(self):
        """D-22-9: combo ditunda untuk QR (tetap di POS kasir)."""
        _r, session, _a = self._open()
        item = self.env["product.combo"].create(
            {
                "name": "Pilihan roti (uji QR)",
                "combo_item_ids": [Command.create({"product_id": self.croissant.id, "extra_price": 0.0})],
            }
        )
        combo = self.env["product.product"].create(
            {
                "name": "Paket (uji QR)",
                "type": "combo",
                "available_in_pos": True,
                "list_price": 30000.0,
                "combo_ids": [Command.set(item.ids)],
            }
        )
        _menu, items = self._menu_all()
        self.assertNotIn(combo.id, items)
        self._assert_qr_error("item_unavailable", self._submit, session, [self._line(combo)])

    # 4 ------------------------------------------------------------------------------------------------
    def test_04_invalid_attributes(self):
        _r, session, _a = self._open()
        PTAV = self.env["product.template.attribute.value"]
        other_tmpl = self.env["product.template"].create(
            {
                "name": "Teh (uji QR)",
                "available_in_pos": True,
                "pos_categ_ids": [Command.set(self.categ_bar.ids)],
                "attribute_line_ids": [
                    Command.create(
                        {"attribute_id": self.attr_sugar.id, "value_ids": [Command.set(self.attr_sugar.value_ids.ids)]}
                    )
                ],
            }
        )
        other_ptav = other_tmpl.attribute_line_ids.product_template_value_ids[:1]
        cases = {
            "milik produk lain": self._line(self.latte, 1, self.ptav_normal | other_ptav),
            "is_custom": self._line(self.latte, 1, self.ptav_normal | self.ptav_custom),
            "atribut wajib kosong": self._line(self.latte, 1, PTAV),
            "dua nilai radio": self._line(self.latte, 1, self.ptav_normal | self.ptav_less),
            "id tidak ada": {
                **self._line(self.latte, 1, self.ptav_normal),
                "attribute_value_ids": [self.ptav_normal.id, 99999999],
            },
        }
        for name, line in cases.items():
            with self.subTest(case=name):
                self._assert_qr_error("invalid_input", self._submit, session, [line])

    # 6 ------------------------------------------------------------------------------------------------
    def test_06_qr_disabled_and_pos_session_closed(self):
        _r, session, _a = self._open()
        line = [self._line(self.croissant)]
        self.config.efn_qr_enabled = False
        self._assert_qr_error("qr_disabled", self._submit, session, line)
        self.config.efn_qr_enabled = True
        self.pos_session.write({"state": "closing_control"})
        self.config.invalidate_recordset(["current_session_id", "current_session_state"])
        self._assert_qr_error("pos_session_closed", self._submit, session, line)

    # 7 ------------------------------------------------------------------------------------------------
    def test_07_table_session_closed_expired_foreign(self):
        _r, session, _a = self._open()
        line = [self._line(self.croissant)]
        self._assert_qr_error(
            "session_closed",
            self.env["efn.pos.qr.order"].with_user(self.tech).efn_qr_submit,
            config_id=self.config.id,
            table_session_ref=str(uuid.uuid4()),
            submission=self._submission(line),
        )
        session.sudo().write({"expires_at": fields.Datetime.now() - timedelta(minutes=1)})
        self._assert_qr_error("session_closed", self._submit, session, line)
        session.sudo().write({"expires_at": fields.Datetime.now() + timedelta(hours=1)})
        session._efn_close("closed_by_staff")
        self._assert_qr_error("session_closed", self._submit, session, line)
        # User teknis PT lain -> AccessError seragam (tidak membocorkan keberadaan config).
        other_company = self.env["res.company"].create({"name": "PT Lain QR"})
        foreign_tech = self.env["res.users"].create(
            {
                "name": "QR teknis PT lain",
                "login": "efn_qr_tech_other",
                "company_id": other_company.id,
                "company_ids": [Command.set(other_company.ids)],
                "group_ids": [
                    Command.set([self.env.ref("base.group_user").id, self.env.ref("efn_pos_bff_order.group_qr_bff").id])
                ],
            }
        )
        with self.assertRaises(AccessError):
            self._submit(session, line, user=foreign_tech)
        with self.assertRaises(AccessError):
            self.env["efn.pos.qr.order"].with_user(self.tech).efn_qr_menu(config_id=999999999)

    # 8 ------------------------------------------------------------------------------------------------
    def test_08_idempotency(self):
        _r, session, _a = self._open()
        ref = str(uuid.uuid4())
        lines = [self._line(self.croissant, 2)]
        first = self._submit(session, lines, ref=ref)
        again = self._submit(session, lines, ref=ref)
        self.assertEqual(first, again)
        self.assertTrue(again["order_uuid"], "K1 r15: replay juga membawa order_uuid")
        # Hasil tersimpan sebelum r15 (tanpa order_uuid) -> replay tetap mengisi order_uuid dari record.
        record = self.env["efn.pos.qr.order"].search([("submission_ref", "=", ref)])
        legacy = dict(record.result)
        legacy.pop("order_uuid")
        record.sudo().write({"result": legacy})
        self.assertEqual(self._submit(session, lines, ref=ref)["order_uuid"], record.order_uuid)
        self.assertEqual(len(self.env["efn.pos.qr.order"].search([("table_session_id", "=", session.id)])), 1)
        self.assertEqual(len(session.order_ids.filtered("efn_qr_order_ref")), 1)
        self._assert_qr_error("idempotency_conflict", self._submit, session, [self._line(self.croissant, 3)], ref=ref)
        # submission_ref sama dengan kunci berbeda -> konflik juga.
        self._assert_qr_error("idempotency_conflict", self._submit, session, lines, ref=ref, key=f"fnb:submit:x{ref}")

    # 9 ------------------------------------------------------------------------------------------------
    def test_09_limits_per_table_session(self):
        _r, session, _a = self._open()
        self.assertEqual(self.config.efn_qr_max_submissions, 10, "L-76 default 10")
        self._assert_qr_error("limit_exceeded", self._submit, session, [self._line(self.croissant, 21)])
        self._assert_qr_error("limit_exceeded", self._submit, session, [self._line(self.croissant)] * 31)
        self.config.write({"efn_qr_max_amount": 50000.0})
        self._assert_qr_error("limit_exceeded", self._submit, session, [self._line(self.croissant, 2)])
        self.assertFalse(session.order_ids.filtered("efn_qr_order_ref"), "order ditolak ikut di-rollback")
        self.config.write({"efn_qr_max_amount": 3000000.0, "efn_qr_max_submissions": 1})
        self._submit(session, [self._line(self.croissant)])
        self._assert_qr_error("limit_exceeded", self._submit, session, [self._line(self.croissant)])
        self.config.write({"efn_qr_max_submissions": 10, "efn_qr_max_items": 3})
        self._assert_qr_error("limit_exceeded", self._submit, session, [self._line(self.croissant, 3)])

    # 10 -----------------------------------------------------------------------------------------------
    def test_10_note_validation(self):
        _r, session, _a = self._open()
        for bad in ("telp 0812 3456 7890", "kirim ke budi@example.com", "x" * 141):
            with self.subTest(note=bad[:20]):
                self._assert_qr_error("invalid_input", self._submit, session, [self._line(self.croissant, note=bad)])
        result = self._submit(session, [self._line(self.croissant, note="  tanpa\nbawang  ")])
        line = self._qr_order(result).order_id.lines
        self.assertEqual(json.loads(line.note), [{"text": "tanpa bawang", "colorIndex": 0}])
        self.assertNotIn("bawang", json.dumps(self._qr_order(result).result))

    def test_10b_note_pii_filter_vectors(self):
        """SEC-168: vektor bersama BFF <-> Odoo (README "Filter catatan"). Dibangun dari chr(), tanpa literal."""
        from odoo.addons.efn_pos_bff_order.models.qr_common import clean_note

        def u(*codes):
            """Teks dari kode titik heksadesimal (tanpa karakter non-ASCII literal di sumber)."""
            return "".join(chr(code) for code in codes)

        fullwidth_digits = u(0xFF10, 0xFF18, 0xFF11, 0xFF12, 0xFF13, 0xFF14, 0xFF15, 0xFF16, 0xFF17, 0xFF18, 0xFF19)
        rejected = {
            "fullwidth digits": fullwidth_digits,
            "zero-width between digits": "0812" + u(0x200B) + "3456" + u(0x200C) + "789",
            "word joiner + BOM": "0812" + u(0x2060) + "3456" + u(0xFEFF) + "7890",
            "bidi override reversed digits": u(0x202E) + "0987654321" + u(0x202C),
            "bidi isolate": "telp " + u(0x2066) + "0812" + u(0x2069) + " 3456 7890",
            "soft hyphen (not removed, digit count)": "0812" + u(0x00AD) + "3456" + u(0x00AD) + "789",
            "digits split by letters": "0812a3456b789",
            "1000 raw chars": "a" * 1001,
            "slash breaks pattern but 8+ digits": "0812-3456 / 789",
            "digits spread in words": "meja 1 kursi 2 nomor 3456 kode 78",
            "fullwidth at sign": "budi" + u(0xFF20) + "example.com",
            "C1 control then digits": "0812\x853456789",
        }
        for name, value in rejected.items():
            with self.subTest(case=name):
                with self.assertRaises(UserError):
                    clean_note(value)
        accepted = {
            "plain": ("tanpa bawang, pedas level 2", "tanpa bawang, pedas level 2"),
            "fullwidth letters normalized": (u(0xFF54, 0xFF41, 0xFF4E, 0xFF50, 0xFF41) + " es", "tanpa es"),
            "zero-width removed": ("tanpa" + u(0x200B) + " gula", "tanpa gula"),
            "line separator to space": ("tanpa" + u(0x2028) + "es", "tanpa es"),
            "7 digits total": ("2 es teh, 1 kopi, meja 12, 345", "2 es teh, 1 kopi, meja 12, 345"),
            "140 code points after cleanup": ("x" * 140 + u(0x200B) * 50, "x" * 140),
        }
        for name, (value, expected) in accepted.items():
            with self.subTest(case=name):
                self.assertEqual(clean_note(value), expected)
        # Lewat RPC: ditolak sebagai invalid_input (tidak ada order dibuat).
        _r, session, _a = self._open()
        self._assert_qr_error(
            "invalid_input", self._submit, session, [self._line(self.croissant, note=fullwidth_digits)]
        )

    # 11 -----------------------------------------------------------------------------------------------
    def test_11_kitchen_only_after_waiter_order_and_status_events(self):
        _r, session, _a = self._open()
        result = self._submit(session, [self._line(self.latte, 1, self.ptav_normal), self._line(self.croissant)])
        record = self._qr_order(result)
        order = record.order_id
        tickets = self.env["efn.kds.ticket"].sudo().search([("order_id", "=", order.id)])
        self.assertFalse(tickets, "server tidak membuat tiket dari submit (L-71)")
        self.assertFalse(self._outbox(session).filtered(lambda o: o.event == "submission_status"))
        qr_notes = [m for _t, kind, m in self.sent if kind == "EFN_QR_ORDER"]
        self.assertEqual(qr_notes[-1]["order_id"], order.id)
        self.assertEqual(set(qr_notes[-1]), {"order_id", "table_id", "auto_send", "config_id"})
        self._send_to_kitchen(order)
        tickets = self.env["efn.kds.ticket"].sudo().search([("order_id", "=", order.id)])
        self.assertEqual(tickets.station_id, self.station)
        self.assertEqual(tickets.line_ids.product_id, self.latte, "croissant tanpa stasiun")
        self.assertEqual(record.last_status, "accepted")
        tickets.efn_kds_bump("in_progress")
        self.assertEqual(record.last_status, "preparing")
        tickets.efn_kds_bump("ready")
        self.assertEqual(record.last_status, "ready")
        statuses = self._outbox(session).filtered(lambda o: o.event == "submission_status")
        self.assertEqual(statuses.mapped("submission_status"), ["accepted", "preparing", "ready"])
        self.assertEqual(statuses.mapped("seq"), sorted(statuses.mapped("seq")))
        status = (
            self.env["efn.pos.qr.order"]
            .with_user(self.tech)
            .efn_qr_status(config_id=self.config.id, submission_refs=[record.submission_ref, str(uuid.uuid4())])
        )
        expected = {
            "submission_ref": record.submission_ref,
            "state": "ready",
            "seq": statuses[-1].seq,
            "order_uuid": order.uuid,  # K1 r15
        }
        self.assertEqual(status[0], expected)
        self.assertEqual((status[1]["state"], status[1]["order_uuid"]), ("absent", None))
        # K1 r15: event submission_status juga membawa order_uuid.
        payload = statuses[-1]._efn_payload()
        expected_submission = {"submission_ref": record.submission_ref, "status": "ready", "order_uuid": order.uuid}
        self.assertEqual(payload["submission"], expected_submission)

    def test_11b_waiter_rejects_by_deleting_order(self):
        _r, session, _a = self._open()
        record = self._qr_order(self._submit(session, [self._line(self.croissant)]))
        record.order_id.unlink()
        self.assertEqual(record.last_status, "rejected")
        self.assertEqual(self._outbox(session)[-1:].submission_status, "rejected")

    # 12 -----------------------------------------------------------------------------------------------
    def test_12_waiter_order_untouched(self):
        anchor = self._anchor(lines=[{"product": self.croissant, "quantity": 1, "price_unit": 5.0}])
        before = (anchor.amount_total, anchor.lines.price_unit, len(anchor.lines))
        _r, session, _a = self._open(anchor=anchor)
        result = self._submit(session, [self._line(self.croissant)])
        self.assertNotEqual(self._qr_order(result).order_id, anchor)
        self.assertEqual((anchor.amount_total, anchor.lines.price_unit, len(anchor.lines)), before)
        # Sinkron POS berikutnya (pos_restaurant: uuid ATAU draft meja, id terbesar) tidak boleh memilih order QR.
        lookup = {"table_id": self.table.id, "state": "draft", "session_id": anchor.session_id.id}
        PosOrder = self.env["pos.order"]
        self.assertEqual(PosOrder._get_open_order({**lookup, "uuid": anchor.uuid}), anchor)
        self.assertEqual(PosOrder._get_open_order({**lookup, "uuid": str(uuid.uuid4())}), anchor)

    def test_12b_get_open_order_uuid_wins_and_qr_never_merged(self):
        """L-81 (a): uuid cocok menang (juga untuk order QR); kiriman QR internal tanpa uuid cocok = order baru;
        fallback meja tidak memilih order QR walau id-nya terbesar; meja hanya berisi order QR -> kosong."""
        from odoo.addons.efn_core.scope import internal_scope
        from odoo.addons.efn_pos_bff_order.models.efn_pos_qr_order import QR_INTERNAL_SCOPE

        anchor = self._anchor()
        _r, session, _a = self._open(anchor=anchor)
        qr_order = self._qr_order(self._submit(session, [self._line(self.croissant)])).order_id
        self.assertGreater(qr_order.id, anchor.id)
        PosOrder = self.env["pos.order"]
        lookup = {"table_id": self.table.id, "state": "draft", "session_id": anchor.session_id.id}
        self.assertEqual(PosOrder._get_open_order({**lookup, "uuid": qr_order.uuid}), qr_order, "uuid cocok menang")
        self.assertEqual(PosOrder._get_open_order({**lookup, "uuid": anchor.uuid}), anchor)
        self.assertEqual(PosOrder._get_open_order({**lookup, "uuid": str(uuid.uuid4())}), anchor, "fallback bukan QR")
        with internal_scope(self.env, QR_INTERNAL_SCOPE, {("submission", str(uuid.uuid4()))}):
            self.assertFalse(PosOrder._get_open_order({**lookup, "uuid": str(uuid.uuid4())}), "kiriman QR = order baru")
            self.assertEqual(PosOrder._get_open_order({**lookup, "uuid": anchor.uuid}), anchor, "uuid tetap menang")
        # Order pelayan dibatalkan: tersisa hanya order QR -> sinkron pelayan baru tidak boleh menimpanya.
        anchor.action_pos_order_cancel()
        self.assertFalse(PosOrder._get_open_order({**lookup, "uuid": str(uuid.uuid4())}))

    def test_12c_waiter_sync_does_not_overwrite_qr_order(self):
        anchor = self._anchor()
        _r, session, _a = self._open(anchor=anchor)
        qr_order = self._qr_order(self._submit(session, [self._line(self.croissant, 2)])).order_id
        before = (qr_order.amount_total, len(qr_order.lines), qr_order.efn_qr_order_ref)
        # Sinkron order pelayan di meja yang sama dengan uuid baru (pos_restaurant memetakan ke draft meja non-QR).
        waiter_2 = self._anchor()
        self.assertNotEqual(waiter_2, qr_order)
        self.assertEqual((qr_order.amount_total, len(qr_order.lines), qr_order.efn_qr_order_ref), before)
        self.assertEqual(qr_order.state, "draft")

    # 16 -----------------------------------------------------------------------------------------------
    def test_16_auto_mode_single_claim(self):
        self.config.efn_qr_confirm_mode = "auto"
        _r, session, _a = self._open()
        result = self._submit(session, [self._line(self.croissant)])
        self.assertEqual(result["state"], "accepted")
        order = self._qr_order(result).order_id
        self.assertTrue(order.with_user(self.cashier).efn_qr_claim_send())
        self.assertFalse(order.with_user(self.cashier).efn_qr_claim_send(), "terminal kedua kalah")
        self.assertTrue(order.efn_qr_send_claimed_at)
        self.assertFalse(self.env["efn.kds.ticket"].sudo().search([("order_id", "=", order.id)]), "server tidak kirim")
