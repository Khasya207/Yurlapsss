"""Offline event plans and race links. Decoder timing stays in RaceEngine."""
from __future__ import annotations

import json
import time
import uuid

from .race_config import normalize


KINDS = {"practice", "qualifying", "heat", "main", "final"}
STATUSES = {"draft", "registration", "running", "completed"}


def _id():
    return str(uuid.uuid4())


def _label(value, name, limit=100):
    value = str(value or "").strip()
    if not value or len(value) > limit:
        raise ValueError(f"{name} wajib diisi (maksimal {limit} karakter)")
    return value


def _optional_label(value, name, limit):
    value = str(value or "").strip()
    if len(value) > limit:
        raise ValueError(f"{name} maksimal {limit} karakter")
    return value


def _points(raw):
    if raw in (None, ""):
        return [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 50:
        raise ValueError("Tabel poin harus berisi 1 sampai 50 posisi")
    try:
        values = [int(str(value).strip()) for value in raw]
    except (TypeError, ValueError):
        raise ValueError("Tabel poin harus berupa angka bulat")
    if any(value < 0 for value in values):
        raise ValueError("Poin tidak boleh negatif")
    return values


class EventStore:
    def __init__(self, club):
        self.club = club

    def _load(self, event_id):
        row = self.club._conn.execute(
            "SELECT document_json FROM event_meet WHERE id=?", (event_id,)).fetchone()
        if not row:
            raise ValueError("Event tidak ditemukan")
        return json.loads(row[0])

    def _save(self, event):
        event["updated_at"] = time.time()
        event["revision"] = int(event.get("revision", 0)) + 1
        self.club._conn.execute(
            "INSERT INTO event_meet(id,club_id,document_json,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET club_id=excluded.club_id, "
            "document_json=excluded.document_json, updated_at=excluded.updated_at",
            (event["id"], event.get("club_id"), json.dumps(event, ensure_ascii=False),
             event["updated_at"]))
        self.club._conn.commit()
        return event

    def list(self, *, include_delete_conflicts=False):
        with self.club.lock:
            club_id = self.club.club().get("club_id")
            rows = self.club._conn.execute(
                "SELECT document_json,sync_error FROM event_meet WHERE club_id IS ? "
                "ORDER BY updated_at DESC", (club_id,)).fetchall()
            output = []
            for row in rows:
                document = json.loads(row["document_json"])
                if not document.get("deleted_at") or (include_delete_conflicts and row["sync_error"]):
                    output.append(document)
            return output

    def get(self, event_id, *, include_deleted=False):
        with self.club.lock:
            event = self._load(event_id)
            if event.get("club_id") != self.club.club().get("club_id"):
                raise ValueError("Event milik club lain")
            if event.get("deleted_at") and not include_deleted:
                raise ValueError("Meeting sudah dihapus")
            return event

    def sync_state(self, event_id):
        with self.club.lock:
            row = self.club._conn.execute(
                "SELECT remote_revision,synced_revision,sync_error FROM event_meet WHERE id=?",
                (event_id,)).fetchone()
            if not row:
                raise ValueError("Event tidak ditemukan")
            return {"pending": self.get(event_id, include_deleted=True).get("revision", 0) > row["synced_revision"],
                    "conflict": bool(row["sync_error"]), "remote_revision": row["remote_revision"]}

    def resolve_sync_conflict(self, event_id, choice):
        with self.club.lock:
            event = self.get(event_id, include_deleted=True)
            row = self.club._conn.execute(
                "SELECT sync_error FROM event_meet WHERE id=?", (event_id,)).fetchone()
            if not row or not row["sync_error"]:
                raise ValueError("Tidak ada konflik Event")
            remote = json.loads(row["sync_error"])
            if not isinstance(remote, dict) or remote.get("id") != event_id or remote.get("club_id") != event["club_id"]:
                raise ValueError("Data konflik tidak valid")
            if choice == "remote":
                document = remote.get("document")
                if not isinstance(document, dict):
                    raise ValueError("Data website tidak lengkap")
                self.club._conn.execute("UPDATE event_meet SET document_json=?,remote_revision=?,"
                    "synced_revision=?,sync_error=NULL WHERE id=?", (
                    json.dumps(document, ensure_ascii=False), remote["revision"],
                    document.get("revision", 0), event_id))
            elif choice == "local":
                self.club._conn.execute("UPDATE event_meet SET remote_revision=?,sync_error=NULL WHERE id=?",
                                        (remote["revision"], event_id))
            else:
                raise ValueError("Pilih versi website atau versi aplikasi")
            self.club._conn.commit()
            return self.get(event_id, include_deleted=True)

    def create(self, data):
        with self.club.lock:
            track_id = str(data.get("track_id") or "")
            track = self.club.track(track_id)
            if not track:
                raise ValueError("Pilih track yang tersedia")
            classes = data.get("classes") or []
            if not isinstance(classes, list):
                raise ValueError("Daftar competition tidak valid")
            names = [_label(item, "Nama kelas", 60) for item in classes]
            if len(set(x.casefold() for x in names)) != len(names):
                raise ValueError("Nama kelas tidak boleh sama")
            event = {
                "id": _id(), "club_id": self.club.club().get("club_id"),
                "name": _label(data.get("name"), "Nama event"),
                "date": str(data.get("date") or ""), "track_id": track_id,
                "location": _optional_label(data.get("location"), "Lokasi", 100),
                "series": _optional_label(data.get("series"), "Seri", 60),
                "track_name": track["name"], "status": "draft",
                "format": data.get("format") if data.get("format") in ("qualifying_main", "heat_final") else "qualifying_main",
                "best_legs": max(1, min(10, int(data.get("best_legs") or 1))),
                "points_table": _points(data.get("points_table")),
                "classes": [{"id": _id(), "name": name} for name in names],
                "entries": [], "stages": [], "created_at": time.time(), "revision": 0,
            }
            return self._save(event)

    def delete_meeting(self, event_id):
        with self.club.lock:
            event = self.get(event_id)
            if any(stage["status"] == "live" for stage in event["stages"]):
                raise ValueError("Selesaikan sesi timing sebelum menghapus meeting")
            event["deleted_at"] = time.time()
            return self._save(event)

    def update_meeting(self, event_id, data):
        with self.club.lock:
            event = self.get(event_id)
            if event["status"] not in ("draft", "registration"):
                raise ValueError("Pengaturan meeting terkunci setelah event dimulai")
            track_id = str(data.get("track_id") or event["track_id"])
            track = self.club.track(track_id)
            if not track:
                raise ValueError("Pilih track yang tersedia")
            format_value = data.get("format", event.get("format", "qualifying_main"))
            if format_value not in ("qualifying_main", "heat_final"):
                raise ValueError("Format meeting tidak dikenal")
            if event["stages"] and format_value != event.get("format", "qualifying_main"):
                raise ValueError("Hapus sesi terjadwal sebelum mengganti format")
            try:
                best_legs = int(data.get("best_legs", event.get("best_legs", 1)))
            except (TypeError, ValueError):
                raise ValueError("Jumlah leg terbaik harus 1–10")
            if not 1 <= best_legs <= 10:
                raise ValueError("Jumlah leg terbaik harus 1–10")
            location = str(data.get("location", event.get("location", ""))).strip()
            series = str(data.get("series", event.get("series", ""))).strip()
            if len(location) > 100 or len(series) > 60:
                raise ValueError("Lokasi atau seri terlalu panjang")
            event.update(name=_label(data.get("name", event["name"]), "Nama meeting"),
                         date=str(data.get("date", event.get("date", ""))),
                         location=location, series=series, track_id=track_id,
                         track_name=track["name"], format=format_value,
                         best_legs=best_legs,
                         points_table=_points(data.get("points_table", event["points_table"])))
            return self._save(event)

    def configure_competition(self, event_id, class_id, data):
        """Store the schedule recipe for one competition without touching results."""
        with self.club.lock:
            event = self.get(event_id)
            if event["status"] == "completed":
                raise ValueError("Meeting sudah selesai")
            competition = next((item for item in event["classes"] if item["id"] == class_id), None)
            if competition is None:
                raise ValueError("Competition tidak ditemukan")
            if any(stage["class_id"] == class_id for stage in event["stages"]):
                raise ValueError("Hapus sesi terjadwal competition ini sebelum mengubah format")
            name = _label(data.get("name") or competition["name"], "Nama competition", 60)
            if any(item["id"] != class_id and item["name"].casefold() == name.casefold()
                   for item in event["classes"]):
                raise ValueError("Nama competition sudah dipakai")
            def count(key, default, low, high):
                try:
                    value = int(data.get(key, default))
                except (TypeError, ValueError):
                    raise ValueError(f"{key} harus berupa angka bulat")
                if not low <= value <= high:
                    raise ValueError(f"{key} harus {low}–{high}")
                return value
            minimum = count("group_min", 4, 1, 30)
            maximum = count("group_max", 12, 2, 30)
            if minimum > maximum:
                raise ValueError("Minimum grup tidak boleh lebih besar dari maksimum")
            qualifier_rounds = count("qualifier_rounds", 3, 0, 10)
            final_rounds = count("final_rounds", 3, 0, 10)
            if not qualifier_rounds and not final_rounds:
                raise ValueError("Competition perlu minimal satu ronde")
            seed = data.get("initial_stacking", "even")
            if seed not in ("even", "front"):
                raise ValueError("Pembagian grup tidak dikenal")
            config = {
                "practice": bool(data.get("practice", False)),
                "qualifier_rounds": qualifier_rounds,
                "final_rounds": final_rounds,
                "group_min": minimum, "group_max": maximum,
                "initial_stacking": seed,
                "qualifier_start": data.get("qualifier_start", "staggered"),
                "final_start": data.get("final_start", "grid"),
                "qualifier_rank": data.get("qualifier_rank", "result"),
                "final_rank": data.get("final_rank", "position"),
                "qualifier_target": count("qualifier_target", 300, 1, 36000),
                "final_target": count("final_target", 300, 1, 36000),
            }
            if config["qualifier_start"] not in ("grid", "staggered") or config["final_start"] not in ("grid", "staggered"):
                raise ValueError("Mode start tidak dikenal")
            ranks = ("position", "result", "best", "top3", "consecutive")
            if config["qualifier_rank"] not in ranks or config["final_rank"] not in ranks:
                raise ValueError("Penilaian tidak dikenal")
            competition["name"] = name
            competition["config"] = config
            return self._save(event)

    def add_competition(self, event_id, name):
        with self.club.lock:
            event = self.get(event_id)
            if event["status"] not in ("draft", "registration"):
                raise ValueError("Competition hanya dapat ditambah sebelum meeting berjalan")
            name = _label(name, "Nama competition", 60)
            if any(item["name"].casefold() == name.casefold() for item in event["classes"]):
                raise ValueError("Nama competition sudah dipakai")
            event["classes"].append({"id": _id(), "name": name})
            return self._save(event)

    def remove_competition(self, event_id, class_id):
        with self.club.lock:
            event = self.get(event_id)
            if event["status"] not in ("draft", "registration"):
                raise ValueError("Competition tidak dapat dihapus setelah meeting berjalan")
            if any(stage["class_id"] == class_id and stage["status"] != "planned"
                   for stage in event["stages"]):
                raise ValueError("Competition memiliki hasil atau sesi berjalan; hapus meeting jika seluruh event dibatalkan")
            before = len(event["classes"])
            event["classes"] = [item for item in event["classes"] if item["id"] != class_id]
            if len(event["classes"]) == before:
                raise ValueError("Competition tidak ditemukan")
            event["entries"] = [item for item in event["entries"] if item["class_id"] != class_id]
            event["stages"] = [item for item in event["stages"] if item["class_id"] != class_id]
            for number, stage in enumerate(event["stages"], 1):
                stage["sequence"] = number
            return self._save(event)

    def set_status(self, event_id, status):
        with self.club.lock:
            event = self.get(event_id)
            if status not in STATUSES:
                raise ValueError("Status event tidak dikenal")
            transitions = {
                "draft": {"registration"}, "registration": {"draft", "running"},
                "running": {"completed"}, "completed": set(),
            }
            if status not in transitions[event["status"]]:
                raise ValueError("Perubahan status event tidak diizinkan")
            if status == "registration" and not event["classes"]:
                raise ValueError("Tambahkan minimal satu competition sebelum membuka pendaftaran")
            if status == "running" and not any(e["approved"] and e["checked_in"] for e in event["entries"]):
                raise ValueError("Setujui dan check-in minimal satu peserta")
            if status == "running" and not event["stages"]:
                raise ValueError("Buat minimal satu sesi sebelum memulai meeting")
            if status == "completed" and any(s["status"] != "saved" for s in event["stages"]):
                raise ValueError("Jalankan atau hapus semua sesi terjadwal sebelum menutup meeting")
            if status == "completed" and not any(s["status"] == "saved" for s in event["stages"]):
                raise ValueError("Simpan minimal satu hasil sesi sebelum menutup event")
            event["status"] = status
            return self._save(event)

    def add_entry(self, event_id, data):
        with self.club.lock:
            event = self.get(event_id)
            if event["status"] not in ("draft", "registration"):
                raise ValueError("Pendaftaran sudah ditutup")
            class_id = str(data.get("class_id") or "")
            if not any(c["id"] == class_id for c in event["classes"]):
                raise ValueError("Kelas tidak ditemukan")
            member_id = str(data.get("member_id") or "")
            member = next((m for m in self.club.members() if m["id"] == member_id), None)
            if not member:
                raise ValueError("Pilih member atau tamu yang terdaftar")
            tp = str(data.get("transponder") or "").strip()
            if tp not in {str(x) for x in member.get("transponder", [])}:
                raise ValueError("Transponder tidak terdaftar untuk peserta ini")
            if any(e["class_id"] == class_id and e["member_id"] == member_id for e in event["entries"]):
                raise ValueError("Pembalap hanya boleh satu mobil per kelas")
            if any(e["class_id"] == class_id and e["transponder"] == tp for e in event["entries"]):
                raise ValueError("Transponder sudah dipakai di kelas ini")
            details = next((d for d in member.get("transponder_details", [])
                            if str(d.get("transponder_id")) == tp), {})
            entry = {
                "id": _id(), "class_id": class_id, "member_id": member_id,
                "account_id": member.get("account_id"), "name": member["name"],
                "nick": member.get("nick") or "", "tts_name": member.get("tts_name") or member["name"],
                "transponder": tp, "car_class": details.get("rc_class") or member.get("kelas") or "",
                "car_brand": details.get("rc_brand") or member.get("mobil") or "",
                "approved": False, "checked_in": False, "source": member.get("source") or "local",
            }
            event["entries"].append(entry)
            self._save(event)
            return entry

    def set_entry(self, event_id, entry_id, *, approved=None, checked_in=None):
        with self.club.lock:
            event = self.get(event_id)
            if event["status"] == "completed":
                raise ValueError("Event sudah selesai")
            entry = next((e for e in event["entries"] if e["id"] == entry_id), None)
            if not entry:
                raise ValueError("Peserta tidak ditemukan")
            if approved is not None:
                entry["approved"] = bool(approved)
                if not entry["approved"]:
                    entry["checked_in"] = False
            if checked_in is not None:
                if checked_in and not entry["approved"]:
                    raise ValueError("Setujui peserta sebelum check-in")
                entry["checked_in"] = bool(checked_in)
            self._save(event)
            return entry

    def remove_entry(self, event_id, entry_id):
        with self.club.lock:
            event = self.get(event_id)
            if event["status"] not in ("draft", "registration"):
                raise ValueError("Peserta tidak bisa dihapus setelah event dimulai")
            before = len(event["entries"])
            event["entries"] = [e for e in event["entries"] if e["id"] != entry_id]
            if len(event["entries"]) == before:
                raise ValueError("Peserta tidak ditemukan")
            return self._save(event)

    def reconcile_entry(self, event_id, entry_id):
        with self.club.lock:
            event = self.get(event_id)
            if event["status"] == "completed":
                raise ValueError("Event sudah selesai")
            entry = next((e for e in event["entries"] if e["id"] == entry_id), None)
            if not entry:
                raise ValueError("Peserta tidak ditemukan")
            if any(stage["status"] == "saved" and stage["class_id"] == entry["class_id"]
                   for stage in event["stages"]):
                raise ValueError("Identitas peserta kelas ini sudah dipakai dalam hasil tersimpan")
            owner = self.club.member_for_transponder(entry["transponder"])
            if not owner:
                raise ValueError("Akun transponder belum ditemukan. Sinkronkan dahulu.")
            entry.update(member_id=owner["id"], account_id=owner["id"] if owner.get("source") in ("cloud", "guest") else None,
                         name=owner["name"], nick=owner.get("nick") or "",
                         tts_name=owner.get("tts_name") or owner["name"],
                         source=owner.get("source") or "local", approved=False, checked_in=False)
            self._save(event)
            return entry

    def add_stage(self, event_id, data):
        with self.club.lock:
            event = self.get(event_id)
            if event["status"] == "completed":
                raise ValueError("Event sudah selesai")
            class_id = str(data.get("class_id") or "")
            if not any(c["id"] == class_id for c in event["classes"]):
                raise ValueError("Pilih kelas event")
            kind = str(data.get("kind") or "")
            if kind not in KINDS:
                raise ValueError("Jenis sesi tidak dikenal")
            group = _label(data.get("group") or "Semua", "Grup", 30)
            cfg = normalize(data.get("cfg") or {})
            stage = {"id": _id(), "class_id": class_id, "kind": kind,
                     "name": _label(data.get("name"), "Nama sesi"), "group": group,
                     "cfg": cfg, "status": "planned", "race_id": None,
                     "entry_ids": list(data.get("entry_ids") or []),
                     "sequence": len(event["stages"]) + 1}
            if group != "Semua" and not stage["entry_ids"]:
                raise ValueError("Pilih peserta untuk grup ini")
            eligible = {e["id"] for e in event["entries"] if e["class_id"] == class_id}
            if any(x not in eligible for x in stage["entry_ids"]):
                raise ValueError("Peserta sesi harus berasal dari kelas yang dipilih")
            event["stages"].append(stage)
            self._save(event)
            return stage

    def stage(self, event_id, stage_id):
        event = self.get(event_id)
        stage = next((s for s in event["stages"] if s["id"] == stage_id), None)
        if not stage:
            raise ValueError("Sesi event tidak ditemukan")
        return event, stage

    def remove_stage(self, event_id, stage_id):
        with self.club.lock:
            event, stage = self.stage(event_id, stage_id)
            if event["status"] == "completed" or stage["status"] == "live":
                raise ValueError("Sesi berjalan atau meeting selesai tidak dapat dihapus")
            if any(item.get("repeat_of") == stage_id for item in event["stages"]):
                raise ValueError("Hapus sesi ulangannya terlebih dahulu")
            event["stages"] = [s for s in event["stages"] if s["id"] != stage_id]
            for index, item in enumerate(event["stages"], 1):
                item["sequence"] = index
                if item.get("superseded_by") == stage_id:
                    item.pop("superseded_by", None)
            self._save(event)
            if stage["status"] == "saved":
                event["published_standings"] = self.standings(event_id)
                self._save(event)
            return event

    def repeat_stage(self, event_id, stage_id):
        with self.club.lock:
            event, original = self.stage(event_id, stage_id)
            if event["status"] == "completed":
                raise ValueError("Meeting sudah selesai")
            if original["status"] != "saved":
                raise ValueError("Hanya sesi dengan hasil tersimpan yang dapat diulang")
            if original.get("superseded_by"):
                raise ValueError("Sesi ini sudah digantikan; ulangi hasil terbaru")
            if self.live_stage():
                raise ValueError("Selesaikan sesi yang sedang berjalan dahulu")
            root_id = original.get("repeat_root") or stage_id
            root = next((item for item in event["stages"] if item["id"] == root_id), original)
            count = sum(1 for stage in event["stages"]
                        if stage.get("repeat_root") == root_id) + 1
            repeated = {key: original[key] for key in
                        ("class_id", "kind", "group")}
            repeated.update(id=_id(), name=f"{root['name']} · Ulang {count}",
                            cfg=dict(original["cfg"]), entry_ids=list(original["entry_ids"]),
                            status="planned", race_id=None, repeat_of=stage_id,
                            repeat_root=root_id,
                            sequence=len(event["stages"]) + 1)
            event["stages"].append(repeated)
            self._save(event)
            return repeated

    def move_stage(self, event_id, stage_id, direction):
        with self.club.lock:
            event, stage = self.stage(event_id, stage_id)
            if stage["status"] != "planned" or direction not in ("up", "down"):
                raise ValueError("Hanya sesi terjadwal yang dapat dipindah")
            stages = event["stages"]
            index = next(i for i, item in enumerate(stages) if item["id"] == stage_id)
            neighbor = index + (-1 if direction == "up" else 1)
            if neighbor < 0 or neighbor >= len(stages) or stages[neighbor]["status"] != "planned":
                raise ValueError("Sesi tidak dapat dipindah melewati sesi berjalan atau tersimpan")
            stages[index], stages[neighbor] = stages[neighbor], stages[index]
            for number, item in enumerate(stages, 1):
                item["sequence"] = number
            return self._save(event)

    def generate_round(self, event_id, data):
        """Build one round of grouped sessions from the approved roster."""
        with self.club.lock:
            event = self.get(event_id)
            if event["status"] == "completed":
                raise ValueError("Event sudah selesai")
            class_id = str(data.get("class_id") or "")
            if not any(item["id"] == class_id for item in event["classes"]):
                raise ValueError("Pilih kelas Event")
            kind = str(data.get("kind") or "")
            if kind not in KINDS or kind == "practice":
                raise ValueError("Pilih jenis ronde perlombaan")
            group_max = int(data.get("group_max") or 0)
            if group_max < 2 or group_max > 30:
                raise ValueError("Kapasitas grup harus 2–30 mobil")
            entries = [entry for entry in event["entries"]
                       if entry["class_id"] == class_id and entry["approved"]]
            if not entries:
                raise ValueError("Setujui peserta kelas ini sebelum membuat ronde")
            basis = data.get("seed_basis") or "registration"
            if basis not in ("registration", "best"):
                raise ValueError("Metode seeding tidak valid")
            if basis == "best":
                best = {}
                for stage in event["stages"]:
                    if stage["class_id"] != class_id or stage["status"] != "saved" or not stage.get("race_id"):
                        continue
                    race = self.club.race(stage["race_id"])
                    for driver in (race or {}).get("drivers", []):
                        lap = driver.get("best_ms")
                        tp = str(driver.get("transponder") or "")
                        if isinstance(lap, (int, float)) and lap > 0:
                            best[tp] = min(best.get(tp, lap), lap)
                if not best:
                    raise ValueError("Belum ada hasil tersimpan untuk seeding best lap")
                entries.sort(key=lambda entry: (best.get(str(entry["transponder"]), float("inf")),
                                                event["entries"].index(entry)))
            mode = data.get("group_mode") or "balanced"
            if mode not in ("balanced", "top"):
                raise ValueError("Pembagian grup tidak valid")
            group_count = (len(entries) + group_max - 1) // group_max
            groups = [[] for _ in range(group_count)]
            for index, entry in enumerate(entries):
                if mode == "top":
                    group_index = index // group_max
                else:
                    span = group_count * 2
                    slot = index % span
                    group_index = slot if slot < group_count else span - slot - 1
                groups[group_index].append(entry["id"])
            cfg = normalize(data.get("cfg") or {})
            name = _label(data.get("name"), "Nama ronde")
            try:
                rounds = int(data.get("rounds") or 1)
            except (TypeError, ValueError):
                raise ValueError("Jumlah ronde harus berupa angka")
            if not 1 <= rounds <= 10:
                raise ValueError("Jumlah ronde harus 1–10")
            created = []
            for round_number in range(1, rounds + 1):
                round_name = f"{name} R{round_number}" if rounds > 1 else name
                for index, ids in enumerate(groups):
                    label = chr(65 + index) if index < 26 else str(index + 1)
                    stage = {"id": _id(), "class_id": class_id, "kind": kind,
                             "name": f"{round_name} · Grup {label}", "group": label,
                             "cfg": dict(cfg), "status": "planned", "race_id": None,
                             "entry_ids": ids, "sequence": len(event["stages"]) + 1}
                    event["stages"].append(stage)
                    created.append(stage)
            self._save(event)
            return created

    def mark_stage(self, event_id, stage_id, status, race_id=None):
        with self.club.lock:
            event, stage = self.stage(event_id, stage_id)
            if status == "live" and stage["status"] != "planned":
                raise ValueError("Sesi ini sudah pernah dijalankan")
            if status == "live" and self.live_stage():
                raise ValueError("Selesaikan sesi Event yang sedang berjalan dahulu")
            if status == "saved" and stage["status"] != "live":
                raise ValueError("Sesi belum berjalan")
            if status not in ("live", "saved"):
                raise ValueError("Status sesi tidak valid")
            stage["status"] = status
            if race_id:
                if not self.club.race(race_id):
                    raise ValueError("Hasil race tidak ditemukan")
                stage["race_id"] = race_id
            if status == "saved" and stage.get("repeat_of"):
                original = next((item for item in event["stages"]
                                 if item["id"] == stage["repeat_of"]), None)
                if original and original["status"] == "saved":
                    original["superseded_by"] = stage_id
            self._save(event)
            if status == "saved":
                event["published_standings"] = self.standings(event_id)
                self._save(event)
            return stage

    def retry_interrupted_stage(self, event_id, stage_id):
        with self.club.lock:
            event, stage = self.stage(event_id, stage_id)
            if stage["status"] != "live":
                raise ValueError("Sesi tidak perlu dipulihkan")
            stage["status"] = "planned"
            stage["interrupted_at"] = time.time()
            self._save(event)
            return stage

    def race_is_linked(self, race_id):
        return any(stage.get("race_id") == race_id for event in self.list()
                   for stage in event["stages"])

    def live_stage(self):
        return next(((event, stage) for event in self.list()
                     for stage in event["stages"] if stage["status"] == "live"), None)

    def stage_for_race(self, race_id):
        return next(((event, stage) for event in self.list()
                     for stage in event["stages"] if stage.get("race_id") == race_id), None)

    def results(self, event_id):
        event = self.get(event_id)
        output = []
        for stage in event["stages"]:
            race = self.club.race(stage["race_id"]) if stage.get("race_id") else None
            output.append({"stage_id": stage["id"], "name": stage["name"],
                           "class_id": stage["class_id"], "kind": stage["kind"],
                           "group": stage["group"], "status": stage["status"],
                           "superseded_by": stage.get("superseded_by"),
                           "race_id": stage.get("race_id"),
                           "drivers": race.get("drivers", []) if race else []})
        return output

    def standings(self, event_id):
        event = self.get(event_id)
        entries = {str(e["transponder"]): e for e in event["entries"]}
        groups = {}
        for result in self.results(event_id):
            if result["status"] != "saved" or result["superseded_by"] or result["kind"] not in ("main", "final"):
                continue
            key = (result["class_id"], result["group"])
            bucket = groups.setdefault(key, {})
            for driver in result["drivers"]:
                entry = entries.get(str(driver.get("transponder")))
                if not entry or entry["class_id"] != result["class_id"]:
                    continue
                row = bucket.setdefault(entry["id"], {"entry_id": entry["id"],
                    "name": entry["name"], "nick": entry["nick"],
                    "transponder": entry["transponder"], "scores": []})
                position = driver.get("pos")
                points = 0 if driver.get("result_status") in ("DNS", "DNF", "DSQ") else (
                    event["points_table"][position-1] if isinstance(position, int)
                    and 1 <= position <= len(event["points_table"]) else 0)
                row["scores"].append(points)
        out = []
        for (class_id, group), rows in groups.items():
            ranked = []
            for row in rows.values():
                row["points"] = sum(sorted(row["scores"], reverse=True)[:event["best_legs"]])
                row["best_leg"] = max(row["scores"], default=0)
                ranked.append(row)
            ranked.sort(key=lambda row: (-row["points"], -row["best_leg"], row["name"].casefold()))
            for position, row in enumerate(ranked, 1):
                row["position"] = position
            out.append({"class_id": class_id, "group": group, "drivers": ranked})
        return out
