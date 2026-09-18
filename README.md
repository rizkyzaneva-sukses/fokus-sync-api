# FOKUS Sync API

API sinkronisasi untuk app **FOKUS** (desktop, `fokus.pyw`).

## Kenapa ada ini

App FOKUS simpan datanya di file JSON lokal. Akibatnya:
- data cuma ada di 1 PC
- gak bisa dilihat dari PC lain / HP
- gak ada backup

Server ini jadi **tempat bertemunya**. App tetap jalan offline (lokal jadi cache),
begitu online dia push/pull otomatis.

```
fokus.pyw (PC-1) ──┐
                   ├──► API ini ──► Postgres
fokus.pyw (PC-2) ──┘
```

## Model sinkronisasi

- **Offline-first** — app gak pernah nunggu server. Lokal selalu bisa dipakai.
- **Last-write-wins per record** — dibandingkan pakai `updated_at`.
  Kalau 2 PC ngedit record yang sama, yang `updated_at`-nya lebih baru menang.
- **Incremental pull** — klien simpan `server_seq` terakhir, jadi gak tarik ulang semua.
- **Per-device** — tiap PC punya `device_id`; record milik sendiri gak dikirim balik.

## Auth

Header `X-Sync-Key` harus sama dengan env `SYNC_KEY`. Perbandingan pakai
`hmac.compare_digest` (konstan waktu).

## Endpoint

| Method | Path | Fungsi |
|---|---|---|
| GET | `/health` | Cek server + DB hidup |
| POST | `/push` | Kirim record dari klien |
| GET | `/pull?device_id=&since_seq=&limit=` | Ambil record baru dari device lain |
| GET | `/devices` | Daftar PC yang pernah sync |
| GET | `/stats` | Ringkasan isi server |

### Kind yang dikenal
`sprint` · `session` · `idea` · `config` · `stats` · `note`

## Environment

| Var | Wajib | Isi |
|---|---|---|
| `DATABASE_URL` | ya | `postgresql://user:pass@host:5432/db` |
| `SYNC_KEY` | ya | kunci rahasia buat klien |
| `PORT` | tidak | default `8000` |

## Uji

```bash
bash test_api.sh    # 14 assertion: auth, push/pull, last-write-wins, incremental
```

## Deploy

Easypanel → project `zaneva` → service `fokus-sync` + `fokus-sync-db`.
Push ke branch `main` = auto-deploy.

## Catatan teknis

- Nilai `jsonb` **wajib** dibungkus `psycopg.types.json.Jsonb` — kalau tidak,
  psycopg3 error `cannot adapt type 'dict'`.
- Pool punya retry 10x saat startup, jadi API tetap hidup walau DB belum siap.