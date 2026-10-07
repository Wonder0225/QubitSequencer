import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from qubit_sequencer.exports import build_tables, export_csv, export_xlsx
from qubit_sequencer.storage import ChipStore, SCHEMA_VERSION
from qubit_sequencer.templates import rotated_surface_code, square_grid


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "chip.qchip"

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip_parameters_wiring_calibration_and_backup(self):
        with ChipStore.create(self.path, "样片", "custom") as store:
            a = store.add_device("qubit", "tunable_transmon", "Q1", 0, 0)
            b = store.add_device("qubit", "fixed_transmon", "Q2", 1, 0)
            coupler = store.add_device("coupler", "tunable_coupler", "C1", .5, 0)
            direct = store.add_coupling(a, b, "direct")
            mediated = store.add_coupling(a, b, "coupler", coupler)
            store.set_parameter(a, "qubit.f01", 0, point="idle", source="design")
            store.set_parameter(a, "qubit.f01", 5.12, point="idle", source="measurement")
            store.set_parameter(a, "qubit.f01", None, point="readout")
            store.set_parameter(direct, "coupling.g_direct", 12.0)
            first_link = next(link for link in store.links() if link["qubit_id"] == a)
            store.set_parameter(first_link["id"], "link.g_qc", 75.0)
            store.add_definition("qubit.operator", "负责人", "qubit", "text")
            store.set_parameter(b, "qubit.operator", "Alice")
            line = store.add_line("XY", "DR-1", "P12", "AWG4", devices=[a, b])
            store.update_line(line, "Z", "DR-1", "P13", "DAC2")
            self.assertEqual(store.line(line)["port"], "P13")
            store.unassign_line(line, b)
            self.assertEqual(store.line_devices(line), [a])
            store.assign_line(line, b)
            store.update_coupling_notes(direct, "静态电容")
            self.assertEqual(store.coupling(direct)["notes"], "静态电容")
            calibration = store.add_calibration(a, "2D spectroscopy", {"f01": 5.12})
            attachment = store.add_attachment(calibration, "scan.png", "image/png", b"PNGDATA")
            self.assertEqual(set(store.line_devices(line)), {a, b})
            self.assertEqual(store.current_values("idle")[(a, "qubit.f01")], 5.12)
            self.assertIsNone(store.current_values("readout")[(a, "qubit.f01")])
            self.assertEqual(len(store.records(a, point="idle")), 2)
            self.assertEqual(store.validate(), [])
            backup = Path(self.tmp.name) / "backup.qchip"
            store.backup_to(backup)
        with ChipStore.open(self.path) as store:
            self.assertEqual(store.meta()["chip_name"], "样片")
            self.assertEqual(len(store.couplings()), 2)
            self.assertEqual(len(store.links()), 2)
            self.assertEqual(store.current_values()[(b, "qubit.operator")], "Alice")
            self.assertEqual(store.db.execute("SELECT sha256 FROM attachments WHERE id=?", (attachment,)).fetchone()[0],
                             "2d4566582844690f8634a8b2534ea5221560038c6c0650c99140759bad603ae2")
            self.assertEqual(store.validate(), [])
        with ChipStore.open(backup) as store:
            self.assertEqual(len(store.devices()), 3)

    def test_square_and_rotated_templates(self):
        with ChipStore.create(self.path, "格子", "square") as store:
            square_grid(store, 3, 4, "direct")
            self.assertEqual(len(store.devices("qubit")), 12)
            self.assertEqual(len(store.couplings()), 17)
            self.assertEqual(store.validate(), [])
        large_path = Path(self.tmp.name) / "square_6.qchip"
        with ChipStore.create(large_path, "扩展格子", "square") as store:
            square_grid(store, 6, 6, "coupler")
            self.assertEqual(len(store.devices("qubit")), 36)
            self.assertEqual(len(store.couplings()), 60)
            self.assertEqual(len(store.links()), 120)
            self.assertEqual(store.validate(), [])
        for distance, expected_edges in ((3, 24), (5, 80)):
            path = Path(self.tmp.name) / f"d{distance}.qchip"
            with ChipStore.create(path, "表面码", "surface") as store:
                rotated_surface_code(store, distance, "coupler")
                qubits = store.devices("qubit")
                self.assertEqual(len(qubits), 2 * distance * distance - 1)
                self.assertEqual(sum(d["role"] == "data" for d in qubits), distance * distance)
                self.assertEqual(sum(d["role"] == "measure_x" for d in qubits), (distance * distance - 1) // 2)
                self.assertEqual(sum(d["role"] == "measure_z" for d in qubits), (distance * distance - 1) // 2)
                self.assertEqual(len(store.couplings()), expected_edges)
                self.assertEqual(len(store.devices("coupler")), expected_edges)
                roles = {q["id"]: q["role"] for q in qubits}
                neighbors = {q["id"]: set() for q in qubits if q["role"] != "data"}
                for edge in store.couplings():
                    a, b = edge["qubit_a"], edge["qubit_b"]
                    if a in neighbors:
                        neighbors[a].add(b)
                    if b in neighbors:
                        neighbors[b].add(a)
                xs = [v for k, v in neighbors.items() if roles[k] == "measure_x"]
                zs = [v for k, v in neighbors.items() if roles[k] == "measure_z"]
                self.assertTrue(all(len(x & z) % 2 == 0 for x in xs for z in zs))
                self.assertEqual(store.validate(), [])

    def test_tables_xlsx_and_csv_preserve_zero_and_empty(self):
        with ChipStore.create(self.path, "导出样片") as store:
            q = store.add_device("qubit", "fixed_transmon", "Q0", 0, 0)
            store.set_parameter(q, "qubit.f01", 0, point="idle")
            store.set_parameter(q, "qubit.t1", None, point="idle")
            store.set_parameter(q, "qubit.f01", 5.0, point="idle")
            data = build_tables(store, "idle", True)
            headers, rows = data["比特"]
            frequency_col = next(i for i, h in enumerate(headers) if "qubit.f01" in h)
            t1_col = next(i for i, h in enumerate(headers) if "qubit.t1" in h)
            self.assertEqual(rows[0][frequency_col], 5.0)
            self.assertIsNone(rows[0][t1_col])
            xlsx = Path(self.tmp.name) / "params.xlsx"
            export_xlsx(store, xlsx, "idle", True)
            csv_dir = Path(self.tmp.name) / "csv"
            written = export_csv(store, csv_dir, "idle", True)
            self.assertEqual(len(written), 7)
            wb = load_workbook(xlsx, read_only=True)
            self.assertEqual(wb.sheetnames, ["芯片信息", "比特", "耦合器", "耦合", "比特-耦合器连接", "接线", "参数历史"])
            self.assertEqual(wb["比特"].cell(2, frequency_col + 1).value, 5.0)
            self.assertIn(wb["比特"].cell(2, t1_col + 1).value, (None, ""))
            wb.close()
            with (csv_dir / "qubits.csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(rows[1][frequency_col], "5.0")
            self.assertEqual(rows[1][t1_col], "")

    def test_delete_qubit_preserves_other_link_and_coupler(self):
        with ChipStore.create(self.path, "删除测试") as store:
            a = store.add_device("qubit", "fixed_transmon", "A", 0, 0)
            b = store.add_device("qubit", "fixed_transmon", "B", 1, 0)
            c = store.add_device("coupler", "fixed_coupler", "C", .5, 0)
            store.add_coupling(a, b, "coupler", c)
            store.delete_device(a)
            self.assertEqual(len(store.couplings()), 0)
            self.assertEqual([d["label"] for d in store.devices()], ["C", "B"])
            self.assertEqual(len(store.links()), 1)

    def test_v1_migration_preserves_per_segment_strength_and_history(self):
        db = sqlite3.connect(self.path)
        db.executescript("""
            CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE devices(id TEXT PRIMARY KEY,kind TEXT NOT NULL,subtype TEXT NOT NULL,
                label TEXT NOT NULL UNIQUE,role TEXT NOT NULL DEFAULT '',x REAL NOT NULL,y REAL NOT NULL,
                dx REAL NOT NULL DEFAULT 0,dy REAL NOT NULL DEFAULT 0,notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL);
            CREATE TABLE couplings(id TEXT PRIMARY KEY,qubit_a TEXT NOT NULL,qubit_b TEXT NOT NULL,
                mode TEXT NOT NULL,coupler_id TEXT,notes TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
            CREATE TABLE parameter_definitions(key TEXT PRIMARY KEY,label TEXT NOT NULL,scope TEXT NOT NULL,
                category TEXT NOT NULL,value_type TEXT NOT NULL,unit TEXT NOT NULL DEFAULT '',
                is_custom INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE parameter_records(id TEXT PRIMARY KEY,definition_key TEXT NOT NULL,
                device_id TEXT,coupling_id TEXT,point TEXT NOT NULL DEFAULT 'default',bias_value REAL,
                bias_unit TEXT NOT NULL DEFAULT '',numeric_value REAL,text_value TEXT,
                observed_at TEXT NOT NULL,source TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '',
                is_current INTEGER NOT NULL DEFAULT 1);
            CREATE UNIQUE INDEX current_device_parameter ON parameter_records(device_id,definition_key,point)
                WHERE is_current=1 AND device_id IS NOT NULL;
            CREATE UNIQUE INDEX current_coupling_parameter ON parameter_records(coupling_id,definition_key,point)
                WHERE is_current=1 AND coupling_id IS NOT NULL;
            PRAGMA user_version=1;
            INSERT INTO meta VALUES ('format','qchip'),('chip_name','旧文件'),('template','custom');
            INSERT INTO devices VALUES
              ('q1','qubit','fixed_transmon','Q1','',0,0,0,0,'','2024-01-01'),
              ('q2','qubit','fixed_transmon','Q2','',1,0,0,0,'','2024-01-01'),
              ('c','coupler','fixed_coupler','C','',0.5,0,0,0,'','2024-01-01');
            INSERT INTO couplings VALUES ('pair','q1','q2','coupler','c','','2024-01-01');
            INSERT INTO parameter_definitions VALUES
              ('coupling.g_q1c','Q1','coupling','measured','number','MHz',0),
              ('coupling.g_q2c','Q2','coupling','measured','number','MHz',0);
            INSERT INTO parameter_records VALUES
              ('r1','coupling.g_q1c',NULL,'pair','idle',NULL,'',11,NULL,'2024-01-01','fit','',0),
              ('r2','coupling.g_q1c',NULL,'pair','idle',NULL,'',12,NULL,'2024-01-02','fit','',1),
              ('r3','coupling.g_q2c',NULL,'pair','idle',NULL,'',21,NULL,'2024-01-02','fit','',1);
        """)
        db.close()
        with ChipStore.open(self.path) as store:
            self.assertEqual(store.db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(len(store.links()), 2)
            by_qubit = {link["qubit_id"]: link["id"] for link in store.links()}
            self.assertEqual(store.current_values("idle")[(by_qubit["q1"], "link.g_qc")], 12)
            self.assertEqual(store.current_values("idle")[(by_qubit["q2"], "link.g_qc")], 21)
            self.assertEqual(len(store.records(by_qubit["q1"])), 2)
            self.assertEqual(store.validate(), [])
        self.assertTrue((Path(self.tmp.name) / "chip.pre-v1.qchip").exists())
        with ChipStore.open(self.path) as store:
            self.assertEqual(len(store.links()), 2)


if __name__ == "__main__":
    unittest.main()
