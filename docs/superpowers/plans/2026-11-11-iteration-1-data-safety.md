# Iterasi 1 — Data Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Menghapus seluruh jalur kehilangan data pada Lyra — lock database tidak lagi dianggap kerusakan, database konkuren aman, deduplikasi bekerja, dan `--dry-run` benar-benar kering.

**Architecture:** `DatabaseConnection` berubah dari singleton global menjadi objek biasa dengan `initialize()` eksplisit yang dipanggil sekali dari `main()`. Skema dikelola modul migrasi bernomor (`PRAGMA user_version`) yang berjalan dalam satu transaksi `BEGIN IMMEDIATE`. `dry_run` menjadi sifat store (konstruktor), bukan parameter yang harus diingat tiap pemanggil.

**Tech Stack:** Python 3.10+, stdlib `sqlite3`, `mutagen`, `pytest` + `pytest-mock`, `ruff`, `mypy`, Docker.

**Spec:** `docs/superpowers/specs/2026-11-11-lyra-flow-tui-design.md` (bagian A: §7, plus §10 dan §11)

## Global Constraints

- **Tanpa dependency runtime baru.** Yang boleh ditambah hanya tool dev: `ruff`, `mypy`, `pytest-cov` di `[project.optional-dependencies] dev`.
- **Python `>=3.10`.** Jangan pakai sintaks 3.12+ (`type` statement, `itertools.batched`).
- **Migrasi boleh mengubah skema dan membersihkan duplikat**, tetapi wajib membuat backup `<db>.backup-<ts>-<pid>` lebih dulu; gagal backup → migrasi batal.
- **Repair database tidak pernah otomatis.** Hanya lewat flag eksplisit `--repair-db`.
- **Tidak ada `executescript()` di dalam transaksi migrasi** (ia meng-commit lebih dulu dan memutus atomisitas).
- **Semua tes harus hermetik**: tidak menyentuh `music_automation.db` di CWD, tidak butuh jaringan, tidak butuh TTY.
- **Setiap task diakhiri commit sendiri** dengan pesan `type(scope): ...`.
- Branch: `feat/flow-tui-hardening`. Jangan menyentuh `main`.

## Review Focus

Kegagalan yang spec implikasikan tetapi tidak otomatis tertutup oleh tes happy-path — masing-masing sudah punya tes di task pemiliknya:

1. **Dua proses start bersamaan** (loop + interactive) saat DB masih `user_version=0`: hanya satu yang boleh memigrasi, yang lain menunggu lalu melewati (tes di Task 3).
2. **Migrasi gagal di tengah** (disk penuh, proses dibunuh): `user_version` tetap 0, skema lama utuh, start berikutnya mengulang (tes di Task 3).
3. **Migrasi dijalankan dua kali** (idempotensi) dan pada DB kosong 0 baris (tes di Task 3).
4. **Dua baris dengan `(artist,title)` sama tetapi `file_path` berbeda**: satu bertahan, path yang kalah tercatat di file laporan, tidak hilang diam-diam (tes di Task 3).
5. **Huruf non-ASCII pada dedupe**: `COLLATE NOCASE` SQLite hanya melipat ASCII, jadi `Beyoncé` dan `beyoncé` tetap dua baris. Tes di Task 3 memakukan perilaku ini apa adanya supaya batasannya terdokumentasi, bukan mengejutkan.
6. **`--dry-run` digabung `--rescan`**: rescan tidak boleh menulis apa pun (tes di Task 6).

---

### Task 1: `DatabaseConnection` bukan singleton, dengan `initialize()` dan PRAGMA konkurensi

**Files:**
- Modify: `src/lyra/database/connection.py`
- Modify: `src/lyra/main.py:133-140`
- Test: `tests/test_connection.py` (create)

**Interfaces:**
- Consumes: tidak ada (task pertama).
- Produces: `DatabaseConnection(path: str | Path)`, `.path: Path`, `.initialize() -> None`, `.get_connection() -> ContextManager[sqlite3.Connection]`. Konstruktor **tidak** menyentuh disk; semua efek samping ada di `initialize()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_connection.py
import sqlite3
import pytest
from lyra.database.connection import DatabaseConnection


def test_two_instances_do_not_share_path(tmp_path):
    a = DatabaseConnection(tmp_path / "a.db")
    b = DatabaseConnection(tmp_path / "b.db")
    assert a.path != b.path
    assert a is not b


def test_initialize_enables_wal_and_busy_timeout(tmp_path):
    conn = DatabaseConnection(tmp_path / "t.db")
    conn.initialize()
    with conn.get_connection() as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_reader_not_blocked_by_open_writer_transaction(tmp_path):
    """Semantik WAL: reader tidak menunggu writer yang transaksinya belum commit."""
    conn = DatabaseConnection(tmp_path / "t.db")
    conn.initialize()
    with conn.get_connection() as w:
        w.execute("CREATE TABLE x (v INTEGER)")
        w.commit()
        w.isolation_level = None
        w.execute("BEGIN IMMEDIATE")
        w.execute("INSERT INTO x VALUES (1)")
        with conn.get_connection() as r:
            assert r.execute("SELECT COUNT(*) FROM x").fetchone()[0] == 0
        w.execute("COMMIT")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_connection.py -v`
Expected: FAIL — `DatabaseConnection` masih singleton sehingga `a is not b` gagal, dan `initialize` belum ada (`AttributeError`).

- [ ] **Step 3: Implement**

Hapus `_instance`/`_initialized`/`__new__`; simpan `self.path = Path(path)` dan `self._initialized = False`. `initialize()` membuat direktori induk, membuka satu koneksi, menjalankan berurutan: `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`, `PRAGMA synchronous=NORMAL`, lalu memanggil `self._create_schema(conn)` (fungsi yang sudah ada). `get_connection()` memakai `sqlite3.connect(self.path, timeout=30, check_same_thread=False)`. `main.py` mengganti `db_connection = DatabaseConnection(Config.DB_PATH)` menjadi konstruksi + `db_connection.initialize()`.

**Catatan untuk Task 3:** panggilan `self._create_schema(conn)` di `initialize()` diganti menjadi `apply_migrations(conn, self.path)`. Task 1 sengaja tidak memakai `apply_migrations` karena modul itu belum ada.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_connection.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/lyra/database/connection.py src/lyra/main.py tests/test_connection.py
git commit -m "refactor(db): replace connection singleton with explicit initialize"
```

---

### Task 2: Lock bukan kerusakan, dan repair hanya lewat `--repair-db`

**Files:**
- Modify: `src/lyra/database/connection.py:125-170`
- Create: `src/lyra/database/errors.py`
- Create: `src/lyra/database/maintenance.py`
- Modify: `src/lyra/main.py` (argparse: `--repair-db`)
- Test: `tests/test_repair.py` (create)

**Interfaces:**
- Consumes: `DatabaseConnection(path)` dari Task 1.
- Produces: `RepairReport` dataclass dengan field `backup_path: Path`, `tables: dict[str, int]`, `integrity_ok: bool`; `repair_database(db_path: Path) -> RepairReport`; `backup_database(db_path: Path) -> Path` (dipakai juga oleh migrasi di Task 3).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_repair.py
import sqlite3
import pytest
from lyra.database.connection import DatabaseConnection
from lyra.database.maintenance import repair_database


def test_lock_is_not_treated_as_corruption(tmp_path):
    db = tmp_path / "live.db"
    conn = DatabaseConnection(db)
    conn.initialize()
    holder = sqlite3.connect(db, timeout=1)
    holder.isolation_level = None
    holder.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(sqlite3.OperationalError):
            DatabaseConnection(db).initialize()
        assert not list(tmp_path.glob("*.corrupt*")), "DB tidak boleh di-rename saat lock"
        assert db.exists()
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_repair_creates_backup_and_reports_lost_rows(tmp_path):
    db = tmp_path / "bad.db"
    db.write_bytes(b"this is not a database at all" * 100)
    report = repair_database(db)
    assert report.backup_path.exists()
    assert report.integrity_ok
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM local_library").fetchone()[0] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_repair.py -v`
Expected: FAIL — `ModuleNotFoundError: lyra.database.maintenance`, dan tes pertama gagal karena DB di-rename.

- [ ] **Step 3: Implement**

Di `connection.py`, hapus cabang `except sqlite3.DatabaseError` yang me-rename. Tangkap `sqlite3.OperationalError` lebih dulu dan `raise` tanpa aksi. Untuk `sqlite3.DatabaseError` lain, jalankan `PRAGMA integrity_check`; bila hasilnya `ok`, `raise` apa adanya (bukan korup), bila tidak, lempar `DatabaseCorruptError` baru di `src/lyra/database/errors.py` yang membawa path dan instruksi `lyra --repair-db`. `maintenance.repair_database` menyalin file ke `<db>.backup-<ts>-<pid>`, membuat file DB baru, memanggil `DatabaseConnection(path).initialize()`, menghitung baris per tabel, mengembalikan `RepairReport`. Di `main.py` tambah `--repair-db` yang menjalankan repair lalu `return 0` sebelum jalur lain.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_repair.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/lyra/database/ src/lyra/main.py tests/test_repair.py
git commit -m "fix(db): never rename a live database on lock; add explicit repair"
```

---

### Task 3: Migrasi bernomor, kunci natural `local_library`, dedupe, dan laporan duplikat

**Files:**
- Create: `src/lyra/database/migrations.py`
- Modify: `src/lyra/database/connection.py` (panggil `apply_migrations`)
- Test: `tests/test_migrations.py` (create)

**Interfaces:**
- Consumes: `DatabaseConnection` dari Task 1.
- Produces: `SCHEMA_VERSION: int = 1`; `apply_migrations(conn: sqlite3.Connection, db_path: Path) -> list[int]` (mengembalikan versi yang dijalankan, `[]` bila sudah terbaru); `_migrate_v1(conn: sqlite3.Connection, db_path: Path) -> None` sebagai fungsi terpisah agar bisa digagalkan di tes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_migrations.py
import sqlite3
import pytest
from lyra.database.connection import DatabaseConnection

LEGACY = """
CREATE TABLE local_library (
    id INTEGER PRIMARY KEY AUTOINCREMENT, artist TEXT NOT NULL, album TEXT,
    title TEXT NOT NULL, recording_mbid TEXT, file_path TEXT, added_at TEXT NOT NULL,
    UNIQUE(artist, title, recording_mbid));
"""


def _legacy_db(tmp_path):
    db = tmp_path / "legacy.db"
    c = sqlite3.connect(db)
    c.executescript(LEGACY)   # executescript hanya untuk MENYIAPKAN data uji, bukan migrasi
    now = "2026-01-01T00:00:00+00:00"
    for i in range(3):
        c.execute("INSERT INTO local_library (artist,album,title,recording_mbid,file_path,added_at)"
                  " VALUES (?,?,?,NULL,?,?)",
                  ("Cup of Joe", "Multo", "Multo", "/music/a.mp3" if i == 0 else None, now))
    c.execute("INSERT INTO local_library (artist,album,title,recording_mbid,file_path,added_at)"
              " VALUES ('cup of joe','Multo','multo',NULL,'/music/b.mp3',?)", (now,))
    c.commit(); c.close()
    return db


def test_dedupe_keeps_lowest_id_and_records_losing_paths(tmp_path):
    db = _legacy_db(tmp_path)
    conn = DatabaseConnection(db)
    conn.initialize()
    with conn.get_connection() as c:
        rows = c.execute("SELECT artist,title,file_path FROM local_library").fetchall()
    assert len(rows) == 1
    assert rows[0][2] == "/music/a.mp3"
    report = tmp_path / "migration-duplicates-1.txt"
    assert report.exists()
    assert "/music/b.mp3" in report.read_text()


def test_unique_index_blocks_duplicate_insert(tmp_path):
    conn = DatabaseConnection(tmp_path / "t.db")
    conn.initialize()
    with conn.get_connection() as c:
        c.execute("INSERT INTO local_library (artist,title,added_at) VALUES ('A','B','x')")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO local_library (artist,title,added_at) VALUES ('a','b','y')")


def test_migration_is_idempotent(tmp_path):
    conn = DatabaseConnection(tmp_path / "t.db")
    conn.initialize()
    from lyra.database.migrations import apply_migrations
    with conn.get_connection() as c:
        assert apply_migrations(c, tmp_path / "t.db") == []


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    """Kalau satu langkah migrasi gagal, tidak ada yang setengah jadi."""
    db = _legacy_db(tmp_path)
    from lyra.database import migrations

    def boom(conn, db_dir):
        raise RuntimeError("disk full")

    monkeypatch.setattr(migrations, "_migrate_v1", boom)
    conn = DatabaseConnection(db)
    with pytest.raises(RuntimeError):
        conn.initialize()
    c = sqlite3.connect(db)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM local_library").fetchone()[0] == 4
    c.close()


def test_two_processes_migrate_only_once(tmp_path):
    """Dua proses start bersamaan: satu memigrasi, yang lain menunggu lalu melewati."""
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    db = _legacy_db(tmp_path)
    code = (
        f"import sys; sys.path.insert(0, {str(repo / 'src')!r});"
        f"from lyra.database.connection import DatabaseConnection;"
        f"DatabaseConnection({str(db)!r}).initialize()"
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", code], cwd=str(repo),
                         stderr=subprocess.PIPE, text=True)
        for _ in range(2)
    ]
    errs = [p.communicate()[1] for p in procs]
    assert [p.returncode for p in procs] == [0, 0], errs
    c = sqlite3.connect(db)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM local_library").fetchone()[0] == 1
    c.close()


def test_scan_twice_does_not_add_rows(tmp_path):
    """Inti goal 4: memindai library dua kali tidak menambah baris."""
    from lyra.database.library_scanner import LibraryScanner

    conn = DatabaseConnection(tmp_path / "t.db")
    conn.initialize()
    album = tmp_path / "Music" / "Artist" / "Album"
    album.mkdir(parents=True)
    (album / "Song.mp3").write_bytes(b"\x00")
    for _ in range(2):
        LibraryScanner(str(tmp_path / "Music"), conn).scan_library(force=False)
    with conn.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM local_library").fetchone()[0] == 1


def test_non_ascii_case_is_not_folded(tmp_path):
    """Batas yang diketahui: COLLATE NOCASE hanya melipat ASCII."""
    conn = DatabaseConnection(tmp_path / "t.db")
    conn.initialize()
    with conn.get_connection() as c:
        c.execute("INSERT INTO local_library (artist,title,added_at) VALUES ('Beyoncé','X','x')")
        c.execute("INSERT INTO local_library (artist,title,added_at) VALUES ('beyoncé','X','y')")
        assert c.execute("SELECT COUNT(*) FROM local_library").fetchone()[0] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_migrations.py -v`
Expected: FAIL — `ModuleNotFoundError: lyra.database.migrations`; tes dedupe gagal karena 4 baris masih tersisa.

- [ ] **Step 3: Implement**

`migrations.py` memuat `SCHEMA_VERSION = 1`, `apply_migrations(conn, db_path)`, dan `_migrate_v1(conn, db_path)`. `apply_migrations`: baca `PRAGMA user_version`; bila `>= SCHEMA_VERSION` kembalikan `[]`; bila `0`, buka `BEGIN IMMEDIATE` lalu panggil `_migrate_v1(conn, db_path)` dan `COMMIT`; bungkus `try/except` yang `ROLLBACK` lalu `raise` bila ada error; kembalikan `[1]`. `_migrate_v1` memakai `execute()` per statement (**bukan** `executescript`) untuk: dedupe `local_library` (pemenang `MIN(id)` per `artist COLLATE NOCASE, title COLLATE NOCASE`; `file_path`/`recording_mbid` dari pemenang, fallback ke baris lain yang tidak NULL), tulis path yang kalah ke `migration-duplicates-1.txt` di `db_path.parent`, rebuild tabel, buat `CREATE UNIQUE INDEX ux_local_library_natural ON local_library(artist COLLATE NOCASE, title COLLATE NOCASE)`, lalu `PRAGMA user_version = 1`. `DatabaseConnection.initialize()` memanggil `backup_database(self.path)` lebih dulu (fungsi dari Task 2) sebelum `apply_migrations(conn, self.path)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_migrations.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/lyra/database/ tests/test_migrations.py
git commit -m "feat(db): versioned migration with natural key dedupe and duplicate report"
```

---

### Task 4: Skema `tracked_tracks` untuk state machine dan pemetaan status lama

**Files:**
- Modify: `src/lyra/database/migrations.py` (migrasi 1 diperluas, atau migrasi 2 bila Task 3 sudah ter-merge ke `main` pengguna)
- Modify: `src/lyra/database/track_manager.py`
- Test: `tests/test_migrations.py` (append), `tests/test_state.py` (append)

**Interfaces:**
- Consumes: `apply_migrations` dari Task 3.
- Produces: kolom `attempts INTEGER NOT NULL DEFAULT 0`, `last_attempt_at TEXT`, `next_retry_at TEXT`, `last_error TEXT`, `lidarr_checked_at TEXT` pada `tracked_tracks`; unique index `ux_tracked_natural`; `TrackStateManager.add_tracked_track(...) -> bool` memakai `INSERT ... ON CONFLICT DO NOTHING`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_migrations.py (append)
def test_legacy_pending_status_becomes_new(tmp_path):
    db = tmp_path / "legacy2.db"
    c = sqlite3.connect(db)
    c.executescript("""
    CREATE TABLE tracked_tracks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, recording_mbid TEXT, artist TEXT NOT NULL,
        title TEXT NOT NULL, source TEXT DEFAULT 'listenbrainz', status TEXT DEFAULT 'PENDING',
        added_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(recording_mbid, artist, title));
    """)
    for _ in range(2):
        c.execute("INSERT INTO tracked_tracks (artist,title,status,added_at,updated_at)"
                  " VALUES ('A','B','PENDING','x','x')")
    c.execute("INSERT INTO tracked_tracks (artist,title,status,added_at,updated_at)"
              " VALUES ('C','D','COMPLETED','x','x')")
    c.commit(); c.close()

    conn = DatabaseConnection(db)
    conn.initialize()
    with conn.get_connection() as c:
        rows = dict(c.execute("SELECT artist, status FROM tracked_tracks").fetchall())
        assert rows == {"A": "NEW", "C": "COMPLETED"}
        cols = {r[1] for r in c.execute("PRAGMA table_info(tracked_tracks)")}
        assert {"attempts", "next_retry_at", "last_error", "lidarr_checked_at"} <= cols
```

```python
# tests/test_state.py (append)
def test_add_tracked_track_is_idempotent(tmp_path):
    conn = DatabaseConnection(tmp_path / "t.db"); conn.initialize()
    tm = TrackStateManager(conn)
    assert tm.add_tracked_track(None, "A", "B") is True
    assert tm.add_tracked_track(None, "A", "B") is False
    with conn.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM tracked_tracks").fetchone()[0] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_migrations.py::test_legacy_pending_status_becomes_new tests/test_state.py::test_add_tracked_track_is_idempotent -v`
Expected: FAIL — status masih `PENDING`, kolom belum ada, dan `add_tracked_track` masih memakai check-then-insert.

- [ ] **Step 3: Implement**

Tambahkan langkah di migrasi: dedupe `tracked_tracks` dengan prioritas status `COMPLETED > RUNNING > QUEUED_FALLBACK > WAITING_LIDARR > NEW > FAILED > DEAD` lalu `id` terkecil, `attempts = MAX(attempts)`; `UPDATE tracked_tracks SET status='NEW' WHERE status='PENDING'`; `ALTER TABLE ... ADD COLUMN` untuk lima kolom baru; buat `ux_tracked_natural` dan `ix_tracked_queue(status, next_retry_at)`; `DROP COLUMN fallback_done` dari `artists`. `track_manager.add_tracked_track` mengganti pre-check + `INSERT` menjadi satu `INSERT ... ON CONFLICT DO NOTHING` dan mengembalikan `cursor.rowcount > 0`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_migrations.py tests/test_state.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/lyra/database/ tests/test_migrations.py tests/test_state.py
git commit -m "feat(db): tracked_tracks state columns and idempotent insert"
```

---

### Task 5: `recording_mbid` benar-benar terbaca dari file audio

**Files:**
- Modify: `src/lyra/database/library_scanner.py:99-136`
- Test: `tests/test_library_scanner.py` (create)

**Interfaces:**
- Consumes: `LibraryScanner(library_path, db)` yang sudah ada.
- Produces: `read_recording_mbid(file_path: str) -> str | None` (fungsi modul di `library_scanner.py`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_library_scanner.py
from lyra.database import library_scanner


class _FakeAudio(dict):
    pass


def test_reads_mbid_from_non_easy_tags(monkeypatch):
    fake = _FakeAudio({"musicbrainz_trackid": ["3f2b1c9e-0000-4000-8000-000000000000"]})
    monkeypatch.setattr(library_scanner.mutagen, "File", lambda *a, **k: fake)
    assert library_scanner.read_recording_mbid("x.mp3") == "3f2b1c9e-0000-4000-8000-000000000000"


def test_returns_none_when_tag_missing(monkeypatch):
    monkeypatch.setattr(library_scanner.mutagen, "File", lambda *a, **k: _FakeAudio({}))
    assert library_scanner.read_recording_mbid("x.mp3") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_library_scanner.py -v`
Expected: FAIL — `AttributeError: module 'lyra.database.library_scanner' has no attribute 'read_recording_mbid'`.

- [ ] **Step 3: Implement**

Tambah `read_recording_mbid` yang membuka file dengan `mutagen.File(file_path)` (**bukan** `easy=True`), lalu mencari berurutan: `musicbrainz_trackid`, `TXXX:MusicBrainz Recording Id`, `UFID:http://musicbrainz.org`; nilai list diambil elemen pertama, `str` dipakai apa adanya, selain itu `None`. Bungkus `try/except Exception` → `None`. Di `_extract_metadata`, ganti `audio.get('musicbrainz_recordingid')` dengan `read_recording_mbid(file_path)`; artist/title/album tetap lewat `MutagenFile(..., easy=True)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_library_scanner.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/lyra/database/library_scanner.py tests/test_library_scanner.py
git commit -m "fix(db): read recording mbid from non-easy tags"
```

---

### Task 6: `dry_run` sebagai sifat store, bukan parameter

**Files:**
- Modify: `src/lyra/database/state.py`, `src/lyra/database/track_manager.py`, `src/lyra/database/library_scanner.py`, `src/lyra/downloader/downloader.py`
- Modify: `src/lyra/orchestrator/orchestrator.py:51-130`
- Test: `tests/test_dry_run.py` (create)

**Interfaces:**
- Consumes: `DatabaseConnection` (Task 1), skema baru (Task 3, 4).
- Produces: `StateManager(db, dry_run: bool = False)`, `TrackStateManager(db, dry_run: bool = False)`, `LibraryScanner(library_path, db, dry_run: bool = False)`, `Downloader(library_path=None, dry_run: bool = False)`; `StateManager.add_local_track` dan `TrackStateManager.add_tracked_track`/`mark_*` menjadi no-op (mengembalikan `True`) saat `dry_run`. `StateManager.ensure_library_indexed` meneruskan `dry_run` ke `LibraryScanner` — tanpa itu scanner menulis lewat store-nya sendiri.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dry_run.py
import pytest
from lyra.database.connection import DatabaseConnection
from lyra.database.state import StateManager
from lyra.database.track_manager import TrackStateManager
from lyra.downloader.downloader import Downloader


def test_dry_run_store_writes_nothing(tmp_path):
    conn = DatabaseConnection(tmp_path / "t.db"); conn.initialize()
    sm = StateManager(conn, dry_run=True)
    tm = TrackStateManager(conn, dry_run=True)
    sm.add_local_track("A", "B")
    tm.add_tracked_track(None, "A", "B")
    with conn.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM local_library").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM tracked_tracks").fetchone()[0] == 0


def test_dry_run_downloader_never_spawns_yt_dlp(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: called.append(a))
    d = Downloader(library_path=str(tmp_path), dry_run=True)
    assert d.download_manual("url", "A", "B") is True
    assert called == []


def test_orchestrator_dry_run_leaves_db_empty(tmp_path):
    from lyra.orchestrator import ApplicationOrchestrator
    conn = DatabaseConnection(tmp_path / "t.db"); conn.initialize()
    orch = ApplicationOrchestrator(conn, dry_run=True)
    orch.state_manager.add_local_track("A", "B")
    with conn.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM local_library").fetchone()[0] == 0


def test_dry_run_rescan_writes_nothing(tmp_path, monkeypatch):
    from lyra.config import Config
    conn = DatabaseConnection(tmp_path / "t.db"); conn.initialize()
    album = tmp_path / "Music" / "A" / "B"; album.mkdir(parents=True)
    (album / "S.mp3").write_bytes(b"\x00")
    monkeypatch.setattr(Config, "LIBRARY_PATH", str(tmp_path / "Music"), raising=False)
    StateManager(conn, dry_run=True).ensure_library_indexed(force_rescan=True)
    with conn.get_connection() as c:
        assert c.execute("SELECT COUNT(*) FROM local_library").fetchone()[0] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dry_run.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'dry_run'`.

- [ ] **Step 3: Implement**

Tambahkan `dry_run` ke konstruktor `StateManager`, `TrackStateManager`, `Downloader`, `ApplicationOrchestrator`. Setiap method yang menulis memeriksa `if self.dry_run: logger.info("[DRY RUN] ..."); return True` **sebelum** membuka koneksi. `Downloader.download_manual` mengembalikan `True` tanpa memanggil subprocess saat `dry_run`. Di `orchestrator.add_artists_from_recommendations`, hapus cabang `if dry_run:` yang terpisah dan teruskan flag ke store (satu jalur kode untuk kedua mode).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_dry_run.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/lyra/database/ src/lyra/downloader/ src/lyra/orchestrator/ tests/test_dry_run.py
git commit -m "fix: make dry_run a store property so no write path can skip it"
```

---

### Task 7: `--dry-run` sampai ke mode interaktif

**Files:**
- Modify: `src/lyra/main.py:126-130`, `src/lyra/cli/interactive.py:40-49`, `src/lyra/cli/interactive.py:312-359`
- Test: `tests/test_cli_dry_run.py` (create)

**Interfaces:**
- Consumes: `Downloader(dry_run=...)` dari Task 6.
- Produces: `InteractiveDownloader(dry_run: bool = False)`; `_perform_download` meneruskan `dry_run` ke `download_manual`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_dry_run.py
from unittest.mock import MagicMock
from lyra.cli.interactive import InteractiveDownloader


def test_interactive_passes_dry_run_to_downloader():
    dl = MagicMock()
    cli = InteractiveDownloader(downloader=dl, dry_run=True)
    cli._perform_download("url", "A", "B")
    assert dl.download_manual.call_args.kwargs["dry_run"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_dry_run.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'downloader'`.

- [ ] **Step 3: Implement**

`InteractiveDownloader.__init__(self, downloader=None, db_integration=None, console=None, dry_run=False)` dengan `self.dry_run = dry_run` dan DI (fallback ke konstruksi lama bila `None`). `_perform_download` menambahkan `dry_run=self.dry_run` pada panggilan `download_manual`. `main.py` mengubah cabang interaktif menjadi `InteractiveDownloader(dry_run=args.dry_run)`. Saat `dry_run` aktif, cetak badge `DRY RUN` sekali di awal sesi.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_dry_run.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/lyra/main.py src/lyra/cli/interactive.py tests/test_cli_dry_run.py
git commit -m "fix(cli): propagate dry_run to interactive downloader"
```

---

### Task 8: Secret tidak masuk image Docker

**Files:**
- Create: `.dockerignore`
- Modify: `Dockerfile:11-16`

**Interfaces:**
- Consumes: tidak ada.
- Produces: image yang tidak memuat `.env`, `.venv`, `.git`, `data/`, `*.db`, `*.log`, `cookies.txt`, `.pytest_cache`, `.review`, `docs/superpowers`.

- [ ] **Step 1: Write the failing check**

```bash
docker build -t lyra:test . && docker run --rm lyra:test sh -c 'ls -a /app'
```
Expected: daftar memuat `.env` dan `.venv` → gagal.

- [ ] **Step 2: Run to verify it fails**

Run perintah di Step 1.
Expected: `.env` terlihat di `/app`.

- [ ] **Step 3: Implement**

Buat `.dockerignore` berisi `.env`, `.env.*`, `.venv/`, `.git/`, `data/`, `*.db`, `*.log`, `cookies.txt`, `.pytest_cache/`, `.review/`, `docs/superpowers/`, `__pycache__/`. Ubah `Dockerfile`: `COPY pyproject.toml README.md ./` lalu `COPY src/ ./src/`, dan ganti `pip install -r requirements.txt` menjadi `pip install .`; hapus `ENV PYTHONPATH` karena paket kini terpasang.

- [ ] **Step 4: Verify**

```bash
docker build -t lyra:test . && docker run --rm lyra:test sh -c 'ls -a /app && python -c "import lyra; print(lyra.__version__)"'
```
Expected: tidak ada `.env`/`.venv`; versi tercetak.

- [ ] **Step 5: Commit**

```bash
git add .dockerignore Dockerfile
git commit -m "fix(docker): exclude secrets and venv from build context"
```

---

### Task 9: CI dan tes hermetik

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `tests/conftest.py`
- Modify: `pyproject.toml` (dev extras, hapus `addopts = "-q"`)

**Interfaces:**
- Consumes: seluruh task sebelumnya.
- Produces: fixture `autouse` yang memaksa `Config.DB_PATH` ke `tmp_path`; job CI `test`, `lint`, `docker`.

- [ ] **Step 1: Write the failing test**

```python
# tests/conftest.py (append)
import pytest

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    from lyra.config import Config
    monkeypatch.setattr(Config, "DB_PATH", str(tmp_path / "test.db"), raising=False)
```

Tes yang membuktikan isolasi:

```python
# tests/test_isolation.py (create)
def test_no_db_file_created_in_cwd(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.chdir(tmp_path)
    from lyra.database.connection import DatabaseConnection
    from lyra.database.state import StateManager
    conn = DatabaseConnection(tmp_path / "x.db"); conn.initialize()
    StateManager(conn).add_local_track("A", "B")
    assert not (tmp_path / "music_automation.db").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_isolation.py -v` dan `cd /tmp && pytest /home/samvivan/Documents/lyra/tests/test_musicpro.py`
Expected: tes kedua membuat `music_automation.db` di `/tmp` → membuktikan kebocoran.

- [ ] **Step 3: Implement**

Tambahkan fixture di atas. Di `pyproject.toml`: `dev` extras menjadi `pytest`, `pytest-mock`, `pytest-cov`, `ruff`, `mypy`; hapus `addopts = "-q"`. Buat `.github/workflows/ci.yml` dengan tiga job: `test` (matrix `3.10`–`3.13`, `pip install -e ".[dev]"`, `pytest --cov=lyra --cov-fail-under=70`), `lint` (`ruff check src tests`, `mypy src`), `docker` (`docker build .`). Tambahkan `[tool.ruff]`/`[tool.mypy]` minimal.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest -q` dan `cd /tmp && pytest /home/samvivan/Documents/lyra/tests/test_musicpro.py`
Expected: keduanya PASS, dan tidak ada `music_automation.db` baru di `/tmp`.

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_isolation.py pyproject.toml .github/workflows/ci.yml
git commit -m "ci: add pipeline and make tests hermetic"
```

---

## Definition of Done (iterasi 1)

- `pytest` hijau; coverage `lyra/database/` ≥ 70%.
- `ruff check` dan `mypy src` bersih.
- `docker run --rm lyra:test ls -a /app` tidak memuat `.env`.
- Menjalankan `python -m lyra --loop` dan `python -m lyra --interactive` bersamaan tidak memindahkan/menghapus file DB, dan tidak ada `database is locked` yang lolos ke pengguna.
- `python -m lyra -i --dry-run` tidak membuat file audio dan tidak menambah baris DB.
- Memindai library dua kali berturut-turut tidak menambah satu baris pun di `local_library`.
