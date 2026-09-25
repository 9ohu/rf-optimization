"""The Data Resources store: the data the app works from, kept on disk.

Six resources — Site Details Data (EP), KPI Data, KMZ Data, Complaint Data,
Coverage Data and History ticket — each with ONE active dataset: the files the
pages read. There are no versions and no history.

* Nothing comes in on its own: a file is only ever stored because the user
  uploaded it on the Data Resources page. The app never looks for files on
  the computer.
* An upload is a **pending** replacement: it is checked and previewed, and the
  active dataset is untouched until the user presses Apply (Cancel throws the
  upload away). Apply makes it the active dataset and deletes the files it
  replaced from the store.
* KPI Data holds as many exports as the user adds. A new export replaces an
  active one only when it is a newer copy of it (`same_export`: every KPI and
  nearly every cell of it, most of its hours again, and none older); another
  KPI set, area or period is added beside the active files. The preview says
  which active files go, and the user can keep any of them (`set_drop`) or
  take a new file back out (`unstage`) before Apply.
  Complaint Data holds one file per role (the Daily Target, the CC Process
  history). Site Details (EP), KMZ, Coverage Data and History ticket are
  replaced as a whole.

Files are stored by content (``files/<sha1>/<name>``) under
``RFOPT_CACHE_DIR/resources`` (default ``~/.rfopt_cache``); ``registry.json``
says which files are active and which are pending. It is written to a
temporary file, swapped in and read back, and a verified copy is kept as
``registry.prev.json`` in case the main file is ever damaged — that copy
records the same active data, never an older dataset.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

KINDS = ("ep", "kpi", "kmz", "complaints", "coverage", "history")
ACTIVE, PENDING = "Active", "Awaiting Apply"
# KPI Data: any number of exports, a new one replaces only what it is a newer copy of;
# Complaint Data: a new file replaces the active file of its own role (Daily
# Target / history); the other resources are replaced as a whole
MANY = ("kpi",)
BY_ROLE = ("complaints",)
COVER_CELLS = 0.9          # a newer copy has (nearly) every cell of the active file…
COVER_HOURS = 0.5          # …and at least half of its hours
SKETCH = 64                # object-name hashes kept per KPI file (`object_sketch`)
_SCHEMA = 2
_STAMP = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class Spec:
    kind: str
    title: str
    short: str
    description: str
    icon: str
    colour: str
    types: tuple
    multi: bool
    used_by: tuple


SPECS = {
    "ep": Spec("ep", "Site Details Data (EP)", "Site Details (EP)",
               "Site information, coordinates, territory and site details from the EP file.",
               "pin", "#1597FF", ("xlsx", "xlsm", "xls"), False,
               ("Sites (topology, sector details)", "KPI Analysis (areas, cell IDs)",
                "Report Export", "Complaints (site name, place, RSRP position)",
                "Dashboard (audit)")),
    "kpi": Spec("kpi", "KPI Data", "KPI Data",
                "KPI files (2G / 3G / 4G hourly exports), performance and threshold data.",
                "chart", "#A78BFA", ("zip", "csv"), True,
                ("KPI Analysis", "Draw Data", "Report Export",
                 "Complaints (KPI Evidence, timeline)", "Sites (sector KPI colours)",
                 "Dashboard (4G KPI verdicts)")),
    "kmz": Spec("kmz", "KMZ Data", "KMZ Data",
                "KMZ file of the R5 sites and sectors for the map visualization and layers.",
                "layers", "#22C55E", ("kmz", "kml"), False,
                ("Sites (every tower and beam)",)),
    "complaints": Spec("complaints", "Complaint Data", "Complaint Data",
                       "Tickets and complaints: the Daily Target and, optionally, the "
                       "CC Process history.",
                       "ticket", "#FB923C", ("xlsx", "xls", "csv"), True,
                       ("Complaints (Delay Tickets Analysis)", "Report Export (tickets)",
                        "KPI Analysis (site tickets)",
                        "Dashboard (worklist, complaint history)")),
    "coverage": Spec("coverage", "Coverage Data", "Coverage Data",
                     "Coverage grid (DL Coverage Insight) with LAT and log files, for coverage "
                     "analysis and troubleshooting.",
                     "tower", "#20BFFF", ("zip", "xlsx", "xls", "csv", "txt", "log"), True,
                     ("Sites (Coverage basemap)", "Complaints (site-area RSRP)",
                      "KPI Analysis (site RSRP)")),
    "history": Spec("history", "History ticket", "History ticket",
                    "The R5 ticket history (CC Process export): every ticket with its status, "
                    "group, user, site, city and Sup District.",
                    "history", "#F472B6", ("xlsx", "xls", "csv"), False,
                    ("History of Tickets",)),
}


class ResourceError(RuntimeError):
    """A store operation that cannot be done (the message says why)."""


def root() -> Path:
    base = Path(os.environ.get("RFOPT_CACHE_DIR", Path.home() / ".rfopt_cache"))
    return base / "resources"


def _now() -> str:
    return datetime.now().strftime(_STAMP)


def _safe_name(name: str) -> str:
    name = Path(str(name).replace("\\", "/")).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or "file"


# --------------------------------------------------------------------------- #
# the records
# --------------------------------------------------------------------------- #
@dataclass
class StoredFile:
    name: str
    sha1: str
    size: int
    blob: str                      # relative to root()
    role: str = ""
    summary: dict = field(default_factory=dict)
    uploaded_at: str = ""

    @property
    def path(self) -> Path:
        return root() / self.blob

    def to_json(self) -> dict:
        return {"name": self.name, "sha1": self.sha1, "size": self.size, "blob": self.blob,
                "role": self.role, "summary": self.summary, "uploaded_at": self.uploaded_at}

    @classmethod
    def from_json(cls, d: dict) -> "StoredFile":
        return cls(name=d["name"], sha1=d["sha1"], size=int(d["size"]), blob=d["blob"],
                   role=d.get("role", ""), summary=dict(d.get("summary") or {}),
                   uploaded_at=d.get("uploaded_at", ""))

    def copy(self) -> "StoredFile":
        return StoredFile.from_json(self.to_json())


@dataclass
class Resource:
    kind: str
    files: list = field(default_factory=list)      # the active dataset
    applied_at: str | None = None
    pending: list = field(default_factory=list)    # uploaded, waiting for Apply
    pending_at: str | None = None
    drop: list = field(default_factory=list)       # KPI Data: active files Apply removes

    @property
    def active(self) -> bool:
        return bool(self.files)

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)

    def after_apply(self) -> list:
        """The active dataset Apply would leave."""
        if not self.pending:
            return list(self.files)
        if self.kind in MANY:
            gone = set(self.drop) | {f.sha1 for f in self.pending}
            return [f for f in self.files if f.sha1 not in gone] + list(self.pending)
        if self.kind in BY_ROLE:
            roles = {f.role for f in self.pending}
            return [f for f in self.files if f.role not in roles] + list(self.pending)
        return list(self.pending)

    def replaced(self) -> list:
        """The active files Apply would remove."""
        keep = {f.sha1 for f in self.after_apply()}
        return [f for f in self.files if f.sha1 not in keep]

    def kept(self) -> list:
        """The active files that stay active after Apply."""
        new = {f.sha1 for f in self.pending}
        return [f for f in self.after_apply() if f.sha1 not in new]

    def to_json(self) -> dict:
        return {"files": [f.to_json() for f in self.files], "applied_at": self.applied_at,
                "pending": [f.to_json() for f in self.pending], "pending_at": self.pending_at,
                "drop": list(self.drop)}

    @classmethod
    def from_json(cls, kind: str, d: dict) -> "Resource":
        return cls(kind, files=[StoredFile.from_json(f) for f in d.get("files", [])],
                   applied_at=d.get("applied_at"),
                   pending=[StoredFile.from_json(f) for f in d.get("pending", [])],
                   pending_at=d.get("pending_at"), drop=list(d.get("drop") or []))


def object_sketch(names) -> list:
    """A fixed-size sample of a KPI file's object names: the SKETCH smallest of
    their hashes. Two files' samples tell how much of their cells are the same
    (`sketch_overlap`) without keeping thousands of names in the registry."""
    hs = {hashlib.blake2b(str(n).encode("utf-8"), digest_size=6).hexdigest() for n in names}
    return sorted(hs)[:SKETCH]


def sketch_overlap(a, b) -> float:
    """The share of two files' objects that are the same (0 … 1), from their
    sketches: of the SKETCH smallest hashes of both, those both files have."""
    a, b = set(a or ()), set(b or ())
    union = sorted(a | b)[:SKETCH]
    return sum(1 for h in union if h in a and h in b) / len(union) if union else 0.0


def cell_cover(old: dict, new: dict) -> float:
    """The share of `old`'s objects that `new` has too (0 … 1), from the two
    summaries' sketches and object counts."""
    j = sketch_overlap(old.get("object_sketch"), new.get("object_sketch"))
    n_old, n_new = int(old.get("objects") or 0), int(new.get("objects") or 0)
    if not j or not n_old:
        return 0.0
    return min(1.0, j * (n_old + n_new) / (1 + j) / n_old)


def same_export(old: StoredFile, new: StoredFile) -> bool:
    """Is `new` a newer copy of the KPI export `old`? The same technology, every
    KPI of `old`, (nearly) all of its cells and at least half of its hours
    again, and nothing older — then Apply replaces `old`, unless the user keeps
    it, and nothing `old` held is lost. Another KPI set, another area or
    another period is not a copy: it is added beside. When a summary does not
    say, the answer is no — no file is removed on a guess."""
    if old.role != new.role:
        return False
    a, b = old.summary or {}, new.summary or {}
    try:
        a0, a1, b0, b1 = (datetime.strptime(str(x[k])[:16], "%Y-%m-%d %H:%M")
                          for x, k in ((a, "start"), (a, "end"), (b, "start"), (b, "end")))
    except (KeyError, ValueError):
        return False
    if b1 < a1:
        return False                               # it stops before the active file
    span = (a1 - a0).total_seconds() / 3600 + 1
    shared = (min(a1, b1) - max(a0, b0)).total_seconds() / 3600 + 1
    if shared < COVER_HOURS * span:
        return False
    ka, kb = set(a.get("kpi_set") or ()), set(b.get("kpi_set") or ())
    if not ka or not ka <= kb:
        return False
    return cell_cover(a, b) >= COVER_CELLS


@dataclass
class Registry:
    resources: dict
    migrated: bool = False         # the one-time move of the app's own older storage
    saved_at: str = ""
    recovered: bool = False        # read from registry.prev.json

    def res(self, kind: str) -> Resource:
        if kind not in KINDS:
            raise ResourceError(f"unknown resource {kind!r}")
        return self.resources.setdefault(kind, Resource(kind))

    def to_json(self) -> dict:
        return {"schema": _SCHEMA, "migrated": self.migrated, "saved_at": self.saved_at,
                "resources": {k: r.to_json() for k, r in self.resources.items()}}

    @classmethod
    def from_json(cls, d: dict) -> "Registry":
        if int(d.get("schema", 1)) < 2:
            return _from_versions(d)
        return cls(resources={k: Resource.from_json(k, r)
                              for k, r in (d.get("resources") or {}).items() if k in KINDS},
                   migrated=bool(d.get("migrated")), saved_at=d.get("saved_at", ""))


def _from_versions(d: dict) -> Registry:
    """The earlier registry (versions, Current / Previous / Archived) as active
    data: each resource keeps its Current version's files and its pending
    upload; every older version is dropped (its files are deleted on the next
    save). Files the earlier release copied in by itself from the PC's folders
    ("found in …" on first start) are dropped too: only what the user put in
    stays."""
    out = {}
    for kind, r in (d.get("resources") or {}).items():
        if kind not in KINDS:
            continue
        versions = r.get("versions", [])
        cur = next((v for v in versions if v.get("number") == r.get("active")), None)
        pend = next((v for v in versions if v.get("pending")), None)

        def files_of(v, new_only: bool = False) -> list:
            if v is None:
                return []
            src = str(v.get("source", ""))
            out_ = []
            for f in v.get("files", []):
                if new_only and f.get("kept_from") is not None:
                    continue           # carried over from the active files: not new
                auto = ("found in" in src and "saved upload" not in src) or (
                    "history found in" in src and f.get("role") == "Complaint history")
                if not auto:
                    sf = StoredFile.from_json(f)
                    sf.uploaded_at = v.get("uploaded_at", "")
                    out_.append(sf)
            return out_

        res = Resource(kind, files=files_of(cur), applied_at=(cur or {}).get("applied_at"))
        if pend is not None:
            res.pending = files_of(pend, new_only=True)
            res.pending_at = pend.get("uploaded_at") if res.pending else None
        out[kind] = res
    return Registry(resources=out, migrated=bool(d.get("bootstrapped")),
                    saved_at=d.get("saved_at", ""))


# --------------------------------------------------------------------------- #
# reading and writing the registry
# --------------------------------------------------------------------------- #
_LOCK = threading.RLock()
_MEMO: dict = {}


def _reg_path() -> Path:
    return root() / "registry.json"


def _swap(src: Path, dst: Path) -> None:
    """os.replace, patient with Windows: while a virus scanner or a reader has
    `dst` open for a moment, the swap fails with "Access is denied" — it is
    tried again for up to two seconds before that counts as a failure."""
    for attempt in range(40):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.05)


def _read(path: Path) -> tuple[Registry, bool]:
    d = json.loads(path.read_text(encoding="utf-8"))
    return Registry.from_json(d), int(d.get("schema", 1)) < 2


def _read_fresh() -> Registry:
    main, prev = _reg_path(), root() / "registry.prev.json"
    if not main.is_file():
        return Registry(resources={})
    try:
        reg, old = _read(main)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if prev.is_file():
            reg, old = _read(prev)
            reg.recovered = True
        else:
            raise ResourceError(f"the registry could not be read ({exc})") from exc
    if old:
        # the earlier, versioned registry: written back once as active data
        with _LOCK:
            _save(reg)
            gc(reg)
    return reg


def load() -> Registry:
    """The registry as it is on disk (read again only when the file changed)."""
    path = _reg_path()
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        key = (str(path), 0, 0)
    hit = _MEMO.get("reg")
    if hit is not None and hit[0] == key:
        return hit[1]
    reg = _read_fresh()
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        pass
    _MEMO["reg"] = (key, reg)
    return reg


def _save(reg: Registry) -> None:
    base = root()
    base.mkdir(parents=True, exist_ok=True)
    reg.saved_at = _now()
    text = json.dumps(reg.to_json(), indent=1, ensure_ascii=False)
    main = _reg_path()
    tmp = base / "registry.json.part"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    _swap(tmp, main)
    if json.loads(main.read_text(encoding="utf-8")) != json.loads(text):
        raise ResourceError("the registry did not read back as written")
    # a verified copy of the same registry, read if registry.json is ever damaged
    ptmp = base / "registry.prev.json.part"
    shutil.copyfile(main, ptmp)
    _swap(ptmp, base / "registry.prev.json")
    _MEMO.pop("reg", None)


# --------------------------------------------------------------------------- #
# files
# --------------------------------------------------------------------------- #
def put_file(name: str, data) -> StoredFile:
    """Store one uploaded file (bytes, or a path to copy) by its content."""
    if isinstance(data, (str, Path)):
        src = Path(data)
        h = hashlib.sha1()
        with open(src, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        size = src.stat().st_size
    else:
        src, h, size = None, hashlib.sha1(data), len(data)
    sha = h.hexdigest()
    folder = root() / "files" / sha[:20]
    existing = [p for p in folder.glob("*") if p.is_file() and not p.name.endswith(".part")
                and not p.name.startswith(".")] if folder.is_dir() else []
    if existing and existing[0].stat().st_size == size:
        target = existing[0]
    else:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / _safe_name(name)
        tmp = folder / (target.name + ".part")
        if src is not None:
            shutil.copyfile(src, tmp)
        else:
            tmp.write_bytes(data)
        _swap(tmp, target)
    return StoredFile(name=_safe_name(name), sha1=sha, size=size,
                      blob=target.relative_to(root()).as_posix(), uploaded_at=_now())


def _referenced(reg: Registry) -> set:
    return {f.blob.split("/")[1] for r in reg.resources.values()
            for f in list(r.files) + list(r.pending) if f.blob.count("/") >= 2}


def gc(reg: Registry | None = None) -> int:
    """Delete the stored files neither the active data nor a pending upload
    uses. Returns the bytes freed."""
    with _LOCK:
        reg = reg or _read_fresh()
        keep = _referenced(reg)
        freed = 0
        folder = root() / "files"
        if not folder.is_dir():
            return 0
        for d in folder.iterdir():
            if d.is_dir() and d.name not in keep:
                freed += sum(p.stat().st_size for p in d.rglob("*") if p.is_file())
                shutil.rmtree(d, ignore_errors=True)
        return freed


def forget_unused(files) -> None:
    """Drop freshly stored files that were not taken (a rejected upload)."""
    with _LOCK:
        keep = _referenced(_read_fresh())
        for f in files:
            parts = f.blob.split("/")
            if len(parts) >= 2 and parts[1] not in keep:
                shutil.rmtree(root() / parts[0] / parts[1], ignore_errors=True)


# --------------------------------------------------------------------------- #
# the active data, and its replacement
# --------------------------------------------------------------------------- #
def resource(kind: str) -> Resource:
    return load().res(kind)


def active_files(kind: str) -> list:
    return list(resource(kind).files)


def stage(kind: str, files: list, *, source: str = "upload") -> Resource:
    """Take uploaded (already checked) files as the pending replacement. The
    active dataset is not touched. A single-file resource keeps the newest
    upload; Complaint Data one pending file per role; KPI Data every file, each
    new export marking the active copy of the same export for removal (a file
    already active is not added again)."""
    if not files:
        raise ResourceError("nothing to stage")
    spec = SPECS[kind]
    with _LOCK:
        reg = _read_fresh()
        res = reg.res(kind)
        pend = list(res.pending)
        active = {f.sha1 for f in res.files}
        for f in files:
            f = f.copy()
            if kind in MANY:
                if f.sha1 in active:
                    continue
                pend = [g for g in pend if g.sha1 != f.sha1] + [f]
                res.drop = list(dict.fromkeys(
                    list(res.drop) + [a.sha1 for a in res.files if same_export(a, f)]))
            elif not spec.multi:
                pend = [f]
            elif kind in BY_ROLE:
                pend = [g for g in pend if g.role != f.role or not f.role] + [f]
            else:
                pend = [g for g in pend if g.sha1 != f.sha1] + [f]
        res.pending = list({g.sha1: g for g in pend}.values())
        res.pending_at = _now() if res.pending else None
        if not res.pending:
            res.drop = []
        _save(reg)
        return res


def unstage(kind: str, sha1: str) -> Resource:
    """Take one file back out of the pending upload (KPI Data)."""
    with _LOCK:
        reg = _read_fresh()
        res = reg.res(kind)
        out = next((f for f in res.pending if f.sha1 == sha1), None)
        res.pending = [f for f in res.pending if f.sha1 != sha1]
        if out is not None:
            # an active file only that upload was replacing stays active after all
            freed = {f.sha1 for f in res.files if same_export(f, out)
                     and not any(same_export(f, p) for p in res.pending)}
            res.drop = [a for a in res.drop if a not in freed]
        if not res.pending:
            res.pending_at, res.drop = None, []
        _save(reg)
        gc(reg)
        return res


def set_drop(kind: str, sha1: str, remove: bool) -> Resource:
    """Whether Apply removes an active KPI file (the user's choice in the preview)."""
    with _LOCK:
        reg = _read_fresh()
        res = reg.res(kind)
        drop = [x for x in res.drop if x != sha1]
        if remove and any(f.sha1 == sha1 for f in res.files):
            drop.append(sha1)
        res.drop = drop
        _save(reg)
        return res


def cancel(kind: str) -> None:
    """Throw the pending upload away; the active dataset stays as it is."""
    with _LOCK:
        reg = _read_fresh()
        res = reg.res(kind)
        res.pending, res.pending_at, res.drop = [], None, []
        _save(reg)
        gc(reg)


def apply(kind: str) -> Resource:
    """Make the pending upload the active dataset. The files it replaces are
    deleted from the store: no earlier dataset stays behind."""
    with _LOCK:
        reg = _read_fresh()
        res = reg.res(kind)
        if not res.pending:
            raise ResourceError(f"no upload is waiting for Apply in {SPECS[kind].title}")
        missing = [f.name for f in res.pending if not f.path.is_file()]
        if missing:
            raise ResourceError(f"missing from the storage folder: {', '.join(missing)}")
        res.files = res.after_apply()
        res.applied_at = _now()
        res.pending, res.pending_at, res.drop = [], None, []
        _save(reg)
        gc(reg)
        return res


def remove(kind: str, sha1: str | None = None) -> int:
    """Delete one active file (`sha1`) or the whole active dataset — the user's
    explicit Delete. Returns the bytes freed."""
    with _LOCK:
        reg = _read_fresh()
        res = reg.res(kind)
        before = len(res.files)
        res.files = [f for f in res.files if sha1 is not None and f.sha1 != sha1]
        if len(res.files) == before:
            raise ResourceError("nothing to delete")
        res.drop = [x for x in res.drop if any(f.sha1 == x for f in res.files)]
        if not res.files:
            res.applied_at = None
        _save(reg)
        return gc(reg)


def set_summary(kind: str, sha1: str, summary: dict) -> None:
    """Remember what a file holds (worked out once, shown without reading it again)."""
    with _LOCK:
        reg = _read_fresh()
        res = reg.res(kind)
        for f in list(res.files) + list(res.pending):
            if f.sha1 == sha1:
                f.summary = dict(summary)
        _save(reg)


def mark_migrated() -> None:
    with _LOCK:
        reg = _read_fresh()
        reg.migrated = True
        _save(reg)


def total_bytes() -> int:
    folder = root() / "files"
    if not folder.is_dir():
        return 0
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file()
               and not p.name.endswith(".part"))


# --------------------------------------------------------------------------- #
# is it really saved?
# --------------------------------------------------------------------------- #
@dataclass
class Health:
    state: str                     # "saved" | "empty" | "error"
    message: str
    detail: str
    folder: Path
    saved: int = 0                 # resources with active data
    total_bytes: int = 0


def health() -> Health:
    """Checked, not assumed: the registry reads back, the folder takes a write,
    and every active file is there at its stored size."""
    folder = root()
    try:
        reg = _read_fresh()
    except ResourceError as exc:
        return Health("error", "Data not saved", f"The store could not be read: {exc}", folder)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Health("error", "Data not saved",
                      f"The storage folder does not accept writes ({exc})", folder)
    saved, broken = 0, []
    for kind in KINDS:
        res = reg.res(kind)
        if not res.files:
            continue
        saved += 1
        for f in res.files:
            try:
                ok = f.path.stat().st_size == f.size
            except OSError:
                ok = False
            if not ok:
                broken.append(f"{SPECS[kind].title}: {f.name}")
    size = total_bytes()
    if broken:
        return Health("error", "Data not saved",
                      "Missing from the storage folder: " + "; ".join(broken), folder, saved,
                      size)
    if reg.recovered:
        return Health("error", "Store recovered",
                      "registry.json was unreadable; its verified copy was used. Apply or "
                      "delete any file to write it back.", folder, saved, size)
    if not saved:
        return Health("empty", "Nothing saved yet",
                      "Upload a resource below; it is kept on disk and loaded again when the "
                      "app reopens.", folder, 0, size)
    return Health("saved", "Data Auto-Saved",
                  "Your data is saved and will be loaded when you reopen the application.",
                  folder, saved, size)
