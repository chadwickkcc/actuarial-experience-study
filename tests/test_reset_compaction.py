"""scripts/reset_for_testing.py must release the disk space its DELETEs leave behind.

DuckDB's DELETE never shrinks the file, so before this the demo DB grew with every
reset/rebuild cycle (433 MB holding ~113 MB of live data).
"""

import importlib.util
from pathlib import Path

import duckdb

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "reset_for_testing.py"
_spec = importlib.util.spec_from_file_location("reset_for_testing", _SCRIPT)
reset_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reset_mod)


def test_compact_database_shrinks_file_and_keeps_rows_and_schema(tmp_path):
    db = tmp_path / "bloated.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v VARCHAR)")
    con.execute("INSERT INTO t SELECT i, repeat('x', 200) FROM range(200000) r(i)")
    con.execute("CHECKPOINT")
    con.execute("DELETE FROM t WHERE id >= 10")
    con.close()
    before = db.stat().st_size

    reset_mod.compact_database(db)

    assert db.stat().st_size < before / 2
    con = duckdb.connect(str(db), read_only=True)
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 10
    assert con.execute(
        "SELECT count(*) FROM duckdb_constraints() "
        "WHERE table_name = 't' AND constraint_type = 'PRIMARY KEY'"
    ).fetchone()[0] == 1
    con.close()
