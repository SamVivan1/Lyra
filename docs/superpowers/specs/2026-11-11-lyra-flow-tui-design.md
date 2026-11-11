# Lyra — Flow & TUI Hardening: Design

**Tanggal:** 2026-11-11
**Branch:** `feat/flow-tui-hardening` (dari `overhaul`)
**Status:** draft untuk direview
**Basis bukti:** [`.review/LAPORAN.md`](../../../.review/LAPORAN.md) — 7 P0, 40 P1, 61 P2; semua temuan P0/P1 direproduksi atau diverifikasi langsung.

---

## 1. Intent dan kenapa

Lyra saat ini **bisa kehilangan seluruh state-nya tanpa peringatan**, dan TUI-nya **tidak bisa dipakai di luar terminal interaktif** (hang saat ESC, crash saat stdin berupa pipe). Keduanya bukan bug kosmetik: satu adalah kehilangan data, satu adalah aplikasi yang tidak bisa dijalankan di Docker/CI/cron.

Yang ingin dicapai: Lyra aman dijalankan **tanpa pengawasan** (loop 6 jam di container) dan **enak dipakai manual** (unduh satu lagu sambil melihat progres), tanpa menulis ulang arsitektur — lapisan `api / database / downloader / orchestrator / cli` sudah benar dan dipertahankan.

Kenapa sekarang: repo punya dua branch yang berpisah (`main` menyimpan audit fix yang hilang saat rewrite `overhaul`), test hijau tapi coverage inti 0%, dan tidak ada CI. Setiap perbaikan tanpa gate akan regresi lagi.

---

## 2. Goals

1. **Tidak ada jalur kehilangan data.** Lock database tidak boleh pernah dianggap kerusakan; DB tidak pernah di-*rename* otomatis.
2. **Dua proses boleh berjalan bersamaan** (`--loop` + `--interactive`) tanpa saling mengunci dan tanpa state terbelah.
3. **`--dry-run` benar-benar kering** — tidak mengunduh, tidak menulis DB, di mode batch maupun interaktif.
4. **Deduplikasi bekerja** — memindai library dua kali tidak menambah satu baris pun.
5. **Track yang gagal tidak dicoba selamanya** — ada `attempts`, backoff, dan status akhir `DEAD`.
6. **TUI tidak pernah hang atau crash** karena input terminal: ESC, EOF, pipe, `TERM=dumb`, Ctrl-C.
7. **Unduhan memberi progres nyata**, bukan layar diam.
8. **Perbaikan tidak bisa regresi diam-diam** — ada CI dan test untuk setiap P0.

## 3. Non-goals

- Tidak menambah fitur baru (web UI, multi-user, playlist, dsb).
- Tidak mengganti SQLite, tidak menambah server/daemon.
- Tidak menulis ulang lapisan `api/` — hanya menambal retry, rate limit, dan escape query.
- Tidak menyentuh Textual di iterasi ini (lihat §6.3).
- Tidak memperbaiki docs secara menyeluruh; hanya bagian yang bertentangan langsung dengan perilaku baru.

## 4. Anti-goals (gagal walau secara teknis jalan)

- **Auto-repair yang destruktif.** Fitur "recovery" yang memindahkan file pengguna tanpa izin lebih buruk daripada crash. Kegagalan harus berisik dan bisa dipulihkan operator.
- **Migrasi yang menghapus data tanpa backup.** Setiap migrasi wajib membuat backup file DB lebih dulu.
- **TUI yang menuntut TTY.** Kalau `docker run` tanpa `-t` tidak bisa memakai Lyra, desainnya salah.
- **Menambah dependency baru** untuk mencapai semua ini. `rich` dan `questionary` sudah dideklarasikan; keduanya cukup.
- **Menutupi kegagalan dengan log.** "Will retry later" tanpa jejak di DB adalah pola yang dihapus.

## 5. Constraints (hasil keputusan)

| Keputusan | Nilai | Sumber |
|---|---|---|
| Ambisi flow | Patch + state machine minimal (bukan queue persisten penuh) | dipilih |
| Urutan | P0 data-safety → state machine → TUI | dipilih |
| Agresivitas fallback | Segera (tanpa tenggang 24 jam), tetapi lewati album yang sedang ada di antrean Lidarr | dipilih |
| Arah TUI | Hibrida: `rich` untuk render, `questionary` untuk input | dipilih |
| Migrasi DB | Boleh migrasi skema + bersihkan duplikat lama | dipilih |
| Git | Branch baru dari `overhaul` | dipilih |
| Dependency | Tidak menambah; `questionary` yang menganggur dipakai | keputusan saya |
| Python | `>=3.10` (tetap), dikembangkan di 3.14.8 | constraint repo |

---

## 6. Pendekatan yang dipertimbangkan

### 6.1 Flow / state machine

| Opsi | Yang didapat | Biaya | Verdict |
|---|---|---|---|
| **A. Tambal bug saja** | Cepat (~1 hari), P0 beres | Track gagal tetap retry selamanya; 26 baris `PENDING` hari ini tetap nyangkut | Ditolak — tidak menyelesaikan keluhan nyata |
| **B. Patch + state machine minimal** | `attempts`/`next_retry_at`/`FAILED`/`DEAD` + backoff + cek `/wanted/missing` | ~2–3 hari, satu migrasi skema | **Dipilih** |
| **C. Queue persisten penuh** | 7 status + rekonsiliasi + transisi atomik antar worker | ~1 minggu, model baru, butuh desain worker | Ditolak untuk sekarang — overkill untuk ±3.500 baris; B sudah memberi 80% manfaatnya dan C bisa menyusul tanpa membongkar B |

Alasan B: masalah nyata hari ini bukan "belum ada queue", tapi "tidak ada memori tentang kegagalan". Menambah tiga kolom dan satu aturan backoff menyelesaikannya; status `RUNNING` + rekonsiliasi tetap diambil dari B karena dibutuhkan agar proses yang mati di tengah tidak menyangkut.

### 6.2 TUI

| Opsi | Yang didapat | Biaya | Verdict |
|---|---|---|---|
| **rich + questionary (hibrida)** | Render kaya (Panel/Table/Progress/Live) + navigasi/filter/validasi bawaan questionary; seluruh kode `termios`/raw-mode buatan sendiri hilang | Dua library dengan peran berbeda; prompt dan `Live` tidak boleh tampil bersamaan | **Dipilih** |
| rich saja | Perubahan lebih kecil | Key handling tetap milik kita → bug ESC-hang, non-TTY, Ctrl-C harus ditambal manual | Ditolak — itu justru akar masalahnya |
| Textual | App framework penuh, worker async, snapshot test | Dependency baru, model async baru, kurva belajar | Ditunda — pemicu di §6.3 |

Alasan hibrida: alurnya **sekuensial** (pilih → konfirmasi → unduh → selesai), jadi `Live` dan prompt questionary tidak pernah butuh tampil bersamaan. Ini menghindari konflik render sambil tetap mendapat navigasi yang benar. Ironinya `questionary` sudah jadi dependency yang tidak dipakai — memakainya **mengurangi** kode, bukan menambah.

### 6.3 Pemicu naik ke Textual (ditulis sebagai ADR nanti)

Pindah hanya jika muncul ≥2 dari: antrean multi-item, browse/filter library, edit tag per-field, panel detail + log berdampingan, kebutuhan snapshot test layar. Selama satu layar satu fokus → rich cukup.

---

## 7. Desain — Bagian A: Data safety (iterasi 1)

### 7.1 Lock bukan kerusakan

`DatabaseConnection._initialize_schema` saat ini menangkap `sqlite3.DatabaseError` — yang mencakup `OperationalError("database is locked")` — lalu me-rename DB hidup. Perilaku baru:

- `sqlite3.OperationalError` ditangkap **lebih dulu** dan diteruskan (`raise`), tidak pernah memicu repair.
- Repair hanya berjalan bila `PRAGMA integrity_check` mengembalikan sesuatu selain `ok`.
- Repair **tidak pernah otomatis**. Ada subcommand eksplisit `lyra --repair-db` yang: (a) menyalin DB ke `<db>.backup-<ts>`, (b) membuat DB baru, (c) memberi tahu berapa baris yang hilang.
- Tanpa `--repair-db`, DB yang gagal `integrity_check` membuat startup **berhenti** dengan pesan yang menyebut path, hasil integrity check, dan perintah pemulihan.

**Keputusan saya:** flag `--repair-db` (bukan env var) karena ini operasi operator, bukan konfigurasi.

### 7.2 Konkurensi SQLite

Saat koneksi dibuat: `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`, `PRAGMA synchronous=NORMAL`, `sqlite3.connect(..., timeout=30)`.

WAL memungkinkan satu writer + banyak reader bersamaan; `busy_timeout` membuat konflik menunggu, bukan gagal. Ini yang membuat `--loop` + `--interactive` aman tanpa file lock tambahan.

**Keputusan saya:** tidak menambah `flock` terpisah. WAL + `busy_timeout` cukup untuk beban Lyra (tulis jarang, baca sering); `flock` akan menyulitkan tooling seperti `sqlite3` CLI.

### 7.3 Deduplikasi

Masalahnya berlapis: `UNIQUE(artist, title, recording_mbid)` tidak menahan apa pun saat `recording_mbid` NULL, dan `recording_mbid` selalu NULL karena `musicbrainz_recordingid` bukan key `EasyID3`.

Perilaku baru:

- **Kunci natural = `(artist, title)`**, case-insensitive. `recording_mbid` disimpan sebagai atribut, **bukan** bagian kunci. Alasannya: judul+artis adalah identitas yang stabil, sedangkan MBID sering tidak tersedia (file lokal hasil rip) dan NULL merusak UNIQUE.
- Unique index: `local_library(artist COLLATE NOCASE, title COLLATE NOCASE)`; sama untuk `tracked_tracks`.
- Penulisan memakai `INSERT ... ON CONFLICT DO UPDATE` (upsert) untuk `file_path`, `album`, `added_at`.
- MBID dibaca dengan `mutagen.File(path)` (non-easy) dari `musicbrainz_trackid` / `TXXX:MusicBrainz Recording Id`; `EasyID3` hanya untuk artist/title/album.

**Konsekuensi yang saya terima:** dua rekaman berbeda dengan artis+judul sama (mis. studio vs live) akan dianggap satu track. Untuk aplikasi yang tujuannya "unduh lagu yang belum ada", ini perilaku yang diinginkan; kalau nanti butuh membedakan versi, album masuk ke kunci lewat migrasi baru.

### 7.4 Migrasi

Satu migrasi bernomor, dijalankan sekali, dengan `PRAGMA user_version` sebagai penanda. Semua langkah berjalan dalam **satu** transaksi `BEGIN IMMEDIATE`:

1. Backup file DB ke `<db>.backup-<ts>-<pid>` (wajib; gagal → migrasi batal). Nama memuat `pid` supaya dua proses tidak bertabrakan.
2. Dedupe `local_library`: pemenang = baris `id` terkecil per `(artist, title)` NOCASE. Untuk kolom non-kunci, `file_path`/`recording_mbid` memakai nilai pemenang, dan hanya bila NULL diambil dari baris berikutnya menurut `id` yang tidak NULL.
3. **Baris yang kalah tidak dibuang diam-diam.** Bila punya `file_path` non-NULL yang berbeda dari pemenang, path itu ditulis ke `migration-duplicates-<ts>.txt` di samping DB (satu baris per path, dengan `id`, artis, judul). File audionya tidak disentuh — hanya barisnya yang tidak lagi terindeks, dan operator punya daftarnya.
4. Dedupe `tracked_tracks`: pemenang ditentukan **prioritas status** `COMPLETED > RUNNING > QUEUED_FALLBACK > WAITING_LIDARR > NEW > FAILED > DEAD`, lalu `id` terkecil. `attempts` = `MAX(attempts)` dari semua baris yang digabung; `last_error` ikut pemenang.
5. **Pemetaan status lama**: `PENDING` → `NEW` (dievaluasi ulang lewat cek Lidarr, **bukan** langsung diantrekan ke YouTube), `COMPLETED` tetap `COMPLETED`, nilai lain → `NEW`. `attempts = 0`, `next_retry_at = NULL`.
6. `artists.fallback_done` di-*drop*: di branch ini kolom itu tidak pernah di-set — satu-satunya penulisnya (`mark_fallback_done`) adalah dead code dengan 0 pemanggil, jadi nilainya dijamin 0 dan tidak ada informasi yang hilang.
7. Rebuild `local_library` (CREATE new + INSERT SELECT + DROP + RENAME) untuk membuang constraint UNIQUE lama, lalu tambah unique index natural dan kolom state machine (§8.1).
8. `PRAGMA user_version = 1`.

**Keputusan saya:** rebuild tabel (CREATE new + INSERT SELECT + DROP + RENAME) di dalam satu transaksi, bukan `ALTER TABLE`, karena SQLite tidak bisa menghapus constraint UNIQUE.

**Dua jebakan yang sudah saya buktikan pada spike di salinan DB (jangan diulang builder):**

- **`executescript()` tidak boleh dipakai di dalam transaksi.** Method itu meng-*commit* transaksi yang sedang berjalan lebih dulu, sehingga `BEGIN`/`COMMIT` eksplisit gagal dengan `cannot commit - no transaction is active` — migrasi jadi tidak atomik. Pakai `execute()` per statement.
- **`MAX(file_path)` memilih path secara arbitrer.** Pada data uji, baris yang bertahan justru mendapat `/music/b.mp3` (secara leksikografis lebih besar), bukan path dari baris asli. Dedupe harus memakai nilai dari baris `id` terkecil, dengan `COALESCE` ke baris lain hanya bila nilai itu NULL.

Hasil spike (4 baris duplikat → 1, `integrity_check = ok`, insert duplikat ditolak `UNIQUE constraint failed`): pendekatan ini valid, dan `PRAGMA user_version = 1` bisa di-set di dalam transaksi yang sama.

### 7.5 `--dry-run` sebagai sifat lapisan, bukan parameter

Akar bug: `dry_run` dicek di tempat yang salah (dan lupa diteruskan ke mode interaktif). Perilaku baru: `dry_run` menjadi sifat **store**, bukan parameter yang harus diingat setiap pemanggil.

- `StateManager`/`TrackStateManager` menerima `dry_run: bool` saat konstruksi.
- Semua method yang menulis mengembalikan hasil simulasi tanpa menyentuh DB.
- `Downloader` menerima `dry_run` yang sama, tetapi **hanya memblokir operasi yang mengubah sesuatu** (`download_manual` → subprocess unduh, tulis file, tag). Panggilan baca-saja tetap jalan: `get_search_results` dan `get_video_info` harus tetap bekerja, kalau tidak mode interaktif `--dry-run` tidak akan pernah bisa mencapai layar unduh untuk disimulasikan. Jadi `dry_run` adalah properti konstruktor, dengan satu pengecualian eksplisit untuk dua method baca-saja itu.
- `InteractiveDownloader` menerima `dry_run` dari `main.py` dan menampilkan badge `DRY RUN` permanen di header.

**Keputusan saya:** flag di konstruktor, bukan parameter per method — supaya tidak ada jalur tulis yang bisa lupa.

### 7.6 Secret tidak masuk image

`.dockerignore` baru (`.env`, `.venv`, `.git`, `data/`, `*.db`, `*.log`, `cookies.txt`, `.pytest_cache`, `.review`, `docs/superpowers`), dan `Dockerfile` memakai `COPY src/ ./src/` + `COPY pyproject.toml README.md ./` + `pip install .` (sekaligus menghapus `PYTHONPATH` hack).

**Tindakan operator (di luar kode):** kalau image dengan `.env` pernah di-push, rotate token ListenBrainz + API key Lidarr.

---

## 8. Desain — Bagian B: Flow & state machine (iterasi 2)

### 8.1 Skema `tracked_tracks` setelah migrasi

| Kolom | Tipe | Fungsi |
|---|---|---|
| `id` | INTEGER PK | — |
| `recording_mbid` | TEXT NULL | atribut, bukan kunci |
| `artist`, `title` | TEXT NOT NULL | kunci natural (NOCASE) |
| `source` | TEXT | `listenbrainz` / `manual` |
| `status` | TEXT NOT NULL | lihat §8.2 |
| `attempts` | INTEGER NOT NULL DEFAULT 0 | jumlah percobaan unduh |
| `last_attempt_at` | TEXT NULL | kapan terakhir dicoba |
| `next_retry_at` | TEXT NULL | kapan boleh dicoba lagi |
| `last_error` | TEXT NULL | pesan kegagalan terakhir (dipotong 500 char) |
| `lidarr_checked_at` | TEXT NULL | kapan terakhir dicek ke Lidarr |
| `added_at`, `updated_at` | TEXT NOT NULL | — |

Index: `(status, next_retry_at)`, `(artist COLLATE NOCASE, title COLLATE NOCASE)` UNIQUE.

### 8.2 Status dan transisi

```
NEW ──cek Lidarr──> WAITING_LIDARR ──(tidak di antrean / lewat FALLBACK_HOURS)──> QUEUED_FALLBACK
 │                                                        │
 └──tidak tercakup Lidarr────────────────────────────────> QUEUED_FALLBACK
                                                          │
                                              (klaim atomik) ▼
                                                        RUNNING
                                                       │       │
                                                  sukses│       │gagal
                                                       ▼       ▼
                                                  COMPLETED  attempts+=1
                                                                 │
                                          attempts < max ────────┤
                                              │                  │ attempts >= max
                                              ▼                  ▼
                                        QUEUED_FALLBACK         DEAD
```

Aturan:

- **Klaim atomik**: `UPDATE tracked_tracks SET status='RUNNING', last_attempt_at=? WHERE id=? AND status='QUEUED_FALLBACK'` — kalau `rowcount=0`, worker lain sudah mengambilnya, lewati. Ini yang mencegah dua proses mengunduh track yang sama.
- **Backoff**: `next_retry_at = now + min(2^attempts jam, 24 jam)` → 1, 2, 4, 8, 16, 24 jam.
- **`max_attempts = 5`** → `DEAD`. Nilai ini **konfigurasi** (`FALLBACK_MAX_ATTEMPTS`), bukan konstanta.
- **Rekonsiliasi saat startup**: baris `RUNNING` dengan `last_attempt_at` lebih tua dari 2 jam dikembalikan ke `QUEUED_FALLBACK` (proses mati di tengah unduhan).
- **Batas per run**: `FALLBACK_MAX_PER_RUN` (default 10), diambil lewat `ORDER BY next_retry_at LIMIT n`.
- **Filter umur pindah ke SQL**, bukan Python: `WHERE status='QUEUED_FALLBACK' AND COALESCE(next_retry_at, added_at) <= ?`.

**Keputusan saya:** `WAITING_LIDARR` dipertahankan sebagai status (bukan flag) karena ia punya arti berbeda saat dibaca operator — "Lidarr sedang mengerjakan ini" vs "gagal, akan dicoba lagi". Setelah keputusan pemilik produk di §8.3, status ini berarti **"album sedang ada di antrean unduh Lidarr"**, bukan "menunggu tenggat waktu".

**Peran `FALLBACK_HOURS` berubah** (keputusan pemilik produk): ia bukan lagi masa tenggang sebelum cek pertama, melainkan **batas maksimum sebuah track boleh menunggu di `WAITING_LIDARR`**. Kalau album sudah 24 jam di antrean Lidarr dan belum juga selesai, track dipindahkan ke `QUEUED_FALLBACK` — supaya antrean Lidarr yang macet tidak membekukan fallback selamanya. Nama kuncinya dipertahankan agar `.env` yang ada tetap bermakna; dokumentasinya yang berubah.

### 8.3 Cek Lidarr sebelum fallback

Langkah yang hari ini hilang. Sekali per run, orchestrator mengambil **dua** hal dari Lidarr, keduanya dengan pagination penuh, lalu memakainya untuk semua track:

- `GET /api/v1/wanted/missing` — album yang diketahui Lidarr tapi belum dimiliki.
- `GET /api/v1/queue` — unduhan yang **sedang berjalan** di Lidarr.

**Keputusan pemilik produk:** fallback berjalan segera — tidak ada masa tenggang 24 jam — tetapi album yang **sedang ada di antrean Lidarr dilewati**, supaya YouTube tidak mengunduh hal yang sedang diunduh Lidarr.

Tabel keputusan per track:

| Kondisi di Lidarr | Status |
|---|---|
| Lidarr tidak terjangkau | tetap `NEW`; run berhenti dengan error jelas |
| Artis tidak ada di Lidarr dan tidak akan pernah ada (lookup tanpa exact match, atau `add_artist` gagal permanen) | `QUEUED_FALLBACK` segera |
| Artis punya album hilang **dan** album itu ada di `/queue` (Lidarr sedang mengunduh) | `WAITING_LIDARR`, `lidarr_checked_at=now` |
| Artis punya album hilang dan album itu **tidak** ada di `/queue` | `QUEUED_FALLBACK` segera |
| Artis tidak dilaporkan punya album hilang | cek `local_library`; kalau track memang tidak ada → pindai ulang sekali, lalu `QUEUED_FALLBACK` |

Tiga hal yang menjaga tabel ini tidak salah simpul:

1. **Lidarr tidak bisa dihubungi bukan berarti "kosong".** Semua track tetap `NEW` dan run berhenti dengan error. Ini memperbaiki bug hari ini, di mana timeout Lidarr membuat `existing_artists=[]` sehingga semua artis dianggap baru lalu ditambahkan ulang.
2. **Artis yang baru ditambahkan di run yang sama tidak boleh dinilai "lengkap" dari daftar missing yang kosong.** Lidarr memetakan album secara asinkron; daftar kosong saat itu berarti "belum tahu", bukan "sudah punya". Track milik artis seperti itu tetap `NEW` sampai run berikutnya.
3. **Kegagalan sementara ≠ kegagalan permanen.** `add_artist` yang gagal karena `APIError` (jaringan/5xx) meninggalkan track di `NEW` untuk dicoba lagi; hanya kegagalan yang bersifat permanen (lookup tanpa exact match) yang langsung masuk `QUEUED_FALLBACK`.

`artists.fallback_done` dan `get_pending_fallback_artists()` dihapus bersama kolomnya (dead code).

**Perlu verifikasi sebelum implementasi:** bentuk respons `GET /api/v1/queue` (apakah ia memuat `albumId`/`artistId`, atau hanya judul) belum bisa saya pastikan karena Lidarr pada mesin ini tidak terjangkau. Kalau queue tidak memberi id album, pencocokan turun ke nama artis + judul album, dan itu harus dicatat sebagai keterbatasan. Kalau endpoint-nya tidak ada di versi Lidarr pengguna, desain mundur ke: perlakukan "artist punya album hilang" sebagai pemicu langsung dan terima risiko duplikasi.

### 8.4 Download yang jujur

- `subprocess.run` mendapat `timeout` (unduh 900 s, search 120 s, info 60 s) + penanganan `TimeoutExpired` (kill, catat sebagai kegagalan).
- `--no-playlist` ditambahkan; template output memuat `%(id)s` agar judul kembar tidak tertukar.
- Path hasil diambil dari `--print after_move:filepath`, bukan glob judul.
- `download_track` mengembalikan path file (atau `None`), bukan `bool`. Pemanggil mencatat `file_path` itu ke DB.
- Tagging gagal = unduhan gagal (tidak boleh `return True`), **tapi file yang sudah terunduh tidak dihapus.** Status menjadi `FAILED` dengan `last_error="tagging failed"` dan `file_path` tetap dicatat.
- **Retry tidak boleh mengunduh ulang bila file target sudah ada.** Sebelum memanggil yt-dlp, `download_manual` memeriksa: kalau file tujuan ada dan status terakhir `FAILED` karena tagging, ia hanya men-tag ulang. Tanpa ini, retry akan memakai `--print after_move` yang tidak mengeluarkan apa pun (yt-dlp menganggap sudah terunduh), lalu track menghabiskan 5 percobaan sampai `DEAD` padahal audionya ada di disk.
- **Satu mekanisme subprocess.** `Downloader` selalu memakai `Popen` (bukan campuran `run`/`Popen`) dengan `progress_callback: Optional[Callable]`. Mode batch mengirim `None` dan tidak ada yang mengonsumsi progres; mode interaktif mengirim callback ke `rich.progress`. Kontrak tunggal: `download_track(...) -> Optional[str]` (path file atau `None`). Iterasi 2 sudah berbentuk `Popen` supaya iterasi 3 tidak menulis ulang modul ini.
- **DB hanya otoritatif setelah scan.** `local_library` dipangkas saat scan: baris yang file-nya tidak lagi ada di disk ditandai hilang (bukan langsung dihapus) dan tidak dihitung sebagai "sudah ada". Tanpa ini, menghapus file dari disk membuat track selamanya dianggap selesai.
- `_track_exists` diganti query DB (`track_exists`), bukan glob filesystem. Kalau tetap perlu cek file, pakai `os.scandir` per folder artis, bukan `**` glob.
- Nama artis/judul ditolak bila `""`, `"."`, `".."`; setelah join, verifikasi `os.path.commonpath([root, target]) == root`.
- `yt-dlp` dipanggil sebagai `sys.executable -m yt_dlp` supaya tidak bergantung PATH.

### 8.5 API

- `BaseAPIClient` memakai `requests.Session` + `HTTPAdapter(Retry(total=3, backoff_factor=0.5, status_forcelist=[429,500,502,503,504], respect_retry_after_header=True))`.
- Kegagalan **tidak** dikembalikan sebagai `None`/`[]` yang tidak bisa dibedakan dari "kosong". Method mengembalikan hasil atau melempar `APIError`; pemanggil memutuskan.
- `listenbrainz.get_recording_metadata` memakai `MusicBrainzClient` (rate-limited 1 req/s) — bukan `requests.get` langsung.
- `search_recording` menyusun **satu** query (`recording:"X" AND artist:"Y"`); varian hanya dipakai bila hasil kosong, maksimum 2 percobaan.
- `_escape_query` meng-escape karakter Lucene.
- `_strip_parenthetical` diganti `re.sub(r'\([^()]*\)', '', text)` (aman untuk kurung tak berpasangan) — dipakai juga oleh `cli`.
- `add_artist` mewajibkan exact match `artistName` (NOCASE); tanpa match → `False` + log, bukan menambah `search_results[0]`.
- `get_missing_artists` dengan pagination penuh.
- `User-Agent` memuat kontak.

### 8.6 Konfigurasi

- Semua path di-resolve absolut (`expanduser().resolve()`); `DB_PATH` absolut dicatat di log saat startup.
- `.env` dicari dari CWD **dan** root paket; yang ditemukan dicatat di log.
- Parsing numerik lazy dengan pesan yang menyebut nama env var (bukan `ValueError` mentah saat import).
- `LOG_LEVEL` benar-benar dipakai sebagai default `--log-level`.
- `parse_library_paths` dedupe (`dict.fromkeys`) dan tidak memakai `os.pathsep` sebagai pemisah.
- Validasi per-mode: mode interaktif tidak menuntut kredensial Lidarr/ListenBrainz.
- **Log punya path dan rotasi yang eksplisit** (ini yang di §13 disebut "log Docker", sebelumnya tidak dispesifikkan): kunci baru `LOG_FILE`, default `<direktori DB>/lyra.log` — sehingga di container ia jatuh ke volume `./data` dan tidak hilang saat restart. `RotatingFileHandler(maxBytes=10MB, backupCount=5)`. Saat berjalan di container, log tetap juga ke `stdout` supaya `docker logs` bekerja; saat TUI aktif, handler konsol pindah ke `stderr` (§9.3).

---

## 9. Desain — Bagian C: TUI (iterasi 3)

### 9.1 Pemecahan modul

| File | Tanggung jawab | Aturan |
|---|---|---|
| `cli/io.py` | `supports_tui()`, `PlainIO` (menu bernomor), `TuiIO` | **satu-satunya** gerbang TTY |
| `cli/keys.py` | `read_key()` dengan `select` + `tty.setcbreak` | **satu-satunya** file yang boleh mengimpor `termios`/`tty` |
| `cli/render.py` | `Console(markup=False, highlight=False)`, `result_row()`, `metadata_panel()`, `menu_panel()` | semua konten dinamis lewat `Text` |
| `cli/prompter.py` | pembungkus `questionary` (select/text/confirm) + validasi | tidak menyentuh `termios` |
| `cli/interactive.py` | state machine layar + alur | tidak mencetak langsung, tidak membaca key langsung |

**Keputusan saya:** `questionary` dipakai untuk input/seleksi, `rich.Live` hanya untuk layar yang butuh redraw (daftar hasil, progres). Keduanya tidak tampil bersamaan karena alur sekuensial.

### 9.2 Layar dan transisi

`BOOT → MENU → (SEARCH_INPUT → SEARCHING → RESULTS) | URL_INPUT → METADATA_REVIEW → (METADATA_EDIT) → DOWNLOADING → DONE | ERROR → MENU`

Satu fokus per layar. Header (konteks + badge `DRY RUN`) dan footer (keybinding kontekstual) tetap; status bar memuat pesan terakhir/error. Tidak ada `clear` layar dan tidak ada `os.system`.

### 9.3 Perilaku terminal

- `supports_tui()` = `stdin.isatty() and stdout.isatty() and TERM not in ("", "dumb")`. Di luar itu → `PlainIO`, tanpa satu pun panggilan `termios`.
- `read_key` memakai `tty.setcbreak` (bukan `setraw`) supaya Ctrl-C tetap `KeyboardInterrupt`.
- ESC dibaca dengan `select` timeout 10 ms → tidak pernah memblokir. Key tak dikenal = no-op, bukan batal.
- `finally` selalu memulihkan: `show_cursor(True)`, termios, handler signal.
- `run() -> int` (bukan `sys.exit` di tengah method); `EOFError` diperlakukan seperti batal.
- SIGTERM/SIGINT: 1× Ctrl-C batal langkah, 2× membatalkan unduhan (SIGINT ke yt-dlp), kembali ke menu. Di `--loop`, SIGTERM = selesai rapi + exit 130.
- Log ke `stderr`; saat TUI aktif, `Live(redirect_stdout=True, redirect_stderr=True)`.

### 9.4 Progres unduhan

`Popen` + `--newline --progress-template` → baris `LYRA|done|total|speed|eta` → `rich.progress.Progress`. Kalau template tidak didukung versi yt-dlp, tampilkan spinner + elapsed (degradasi anggun, bukan gagal).

**Risiko yang saya terima:** nama field template belum diverifikasi pada unduhan nyata; karena itu progres bersifat *opsional* dan tidak boleh menjadi alasan unduhan gagal.

### 9.5 Aksesibilitas

Hormati `NO_COLOR` (rich otomatis). Sediakan `--plain` yang memaksa `PlainIO` + ASCII (`[ok]`/`[x]`, bukan `✓`/`✗`). Jangan pernah menyampaikan status hanya lewat warna — selalu ada simbol atau teks (`▸`, `1/5`).

---

## 10. Cross-cutting: test dan CI

Test yang **wajib** ada karena mengunci P0 (semua ini saat ini tidak ada):

| Test | Mengunci |
|---|---|
| Lock DB → `_initialize_schema` **tidak** me-rename, melempar error yang jelas | P0-1 |
| Dua koneksi menulis bergantian dengan WAL → tidak ada `database is locked` | P0-2 |
| Scan 2× → tetap 1 baris | P0-3 |
| `dry_run` → tidak ada baris baru di `local_library`/`tracked_tracks`, `yt-dlp` tidak dipanggil | P0-4/5 |
| `--repair-db` → backup dibuat, DB baru, baris hilang dilaporkan | P0-1 |
| Judul `) (`, `[Official Video]`, `..`, `.part` | P1 |
| `read_key` via PTY: ESC tunggal tidak menggantung; `TERM=dumb` → `PlainIO` | TUI |
| `--interactive` dengan stdin pipe → exit rapi tanpa traceback | TUI |

CI (`.github/workflows/ci.yml`): matrix Python 3.10–3.13, `pip install -e ".[dev]"`, `pytest`, `ruff check`, `mypy`, `docker build`. Test harus **hermetik**: fixture `autouse` memaksa `Config.DB_PATH` ke `tmp_path` dan me-reset singleton.

`DatabaseConnection` berhenti menjadi singleton (DI sudah setengah ada di semua konstruktor) — ini yang membuat test bisa hermetik.

**Inisialisasi dan migrasi tidak boleh implisit di konstruktor** (konsekuensi dari menghapus singleton + goal 2 yang mengizinkan dua proses start bersamaan):

- `main()` memanggil `initialize()` **sekali**, eksplisit, sebelum komponen lain dibuat; semua komponen menerima objek lewat DI dan tidak pernah memicu migrasi sendiri.
- Di dalam `initialize()`, seluruh pemeriksaan versi + backup + rebuild berjalan dalam `BEGIN IMMEDIATE`. `BEGIN IMMEDIATE` mengambil write-lock di awal, jadi proses kedua menunggu (sampai `busy_timeout`) lalu membaca `user_version = 1` dan **melewati** migrasi. Ini yang mencegah dua proses men-backup dan me-rebuild tabel bersamaan.
- `PRAGMA integrity_check` hanya dijalankan setelah migrasi selesai atau saat `--repair-db`; ia bukan bagian dari jalur start normal (mahal untuk library besar).

---

## 11. Urutan pengerjaan

| Iterasi | Isi | Hasil yang bisa dilihat |
|---|---|---|
| **1** | Bagian A (P0) + `.dockerignore` + CI minimal | Lyra tidak bisa kehilangan DB; `--dry-run` kering; image tanpa secret |
| **2** | Bagian B (state machine, cek Lidarr, download jujur, API, config) | Fallback punya memori; tidak ada lagi track `PENDING` abadi |
| **3** | Bagian C (TUI) | TUI tanpa hang/crash; progres nyata; aman di Docker |

Setiap iterasi berdiri sendiri dan bisa dirilis.

---

## 12. Yang diserahkan ke builder

- Struktur test yang tepat per task dan nama test-nya.
- Pemecahan task dan urutan commit.
- Implementasi `PlainIO` (format menu bernomor) dan detail styling rich.
- Formula backoff final bila hasil uji menunjukkan 1–24 jam terlalu agresif.
- Nama kolom/method persis, selama sesuai §8.1 dan §9.1.
- Pemilihan `ruff`/`mypy` config.

---

## 13. Risiko terbuka

1. **Kunci natural `(artist,title)`** menggabungkan versi berbeda dari lagu yang sama. Diterima (§7.3); jalan keluar: masukkan album ke kunci di migrasi berikutnya.
2. **WAL + `busy_timeout` tanpa `flock`** cukup untuk beban sekarang; kalau nanti ada banyak worker paralel, `flock` per proses perlu ditinjau.
3. **Progres yt-dlp** bergantung pada `--progress-template`; sudah dirancang opsional.
4. **Migrasi rebuild tabel** menyentuh seluruh `local_library`; wajib backup + diuji pada salinan DB nyata sebelum dijalankan di data pengguna.
5. **`main` dan `overhaul` tetap berpisah.** 6 fix dari `main` (glob.escape, exact match, pagination, WAL, log Docker, cek file hasil unduhan) diangkat ulang sebagai bagian iterasi 1–2, lalu `main` ditutup agar tidak ada dua sumber kebenaran.
6. **`GET /api/v1/queue` belum terverifikasi** (Lidarr pada mesin ini tidak terjangkau). Ini satu-satunya bagian desain yang bergantung pada asumsi tentang API eksternal; §8.3 menuliskan rencana mundurnya bila bentuk responsnya tidak memuat id album.
7. **Fallback agresif memperbesar beban YouTube.** Tanpa tenggang, satu run bisa mengantrekan banyak track sekaligus. Batas `FALLBACK_MAX_PER_RUN` (default 10) dan jeda antar-unduh menjadi pengaman wajib, bukan opsional.
