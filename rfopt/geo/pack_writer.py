"""A raster PMTiles pack that grows in place — the building-scale imagery.

The building-scale imagery of one governorate is a million tiles or more
(tens of GB). `pmtiles.writer.Writer` keeps an object per tile and writes the
tiles twice (a temporary file, then the pack), and growing a pack means
writing it all again. `GrowingPack` appends each tile to the pack file the
moment it arrives, keeps the tile index in compact arrays, and
`checkpoint()` makes the file a complete pack of every tile so far — the map
draws the region while it is still coming down, and an update that stops
(the app closed, the network gone) carries on from the last checkpoint.

Layout (PMTiles v3; readers follow the header's offsets):

    0       header (127 bytes)
    127     root directory, slot A  (8128 bytes)
    8255    root directory, slot B  (8128 bytes)
    16384   tile data, appended; each checkpoint appends the metadata and the
            leaf directories after the tiles so far

A checkpoint writes the new root into the slot the header does not point at,
then the header: a stop at any moment leaves the last checkpoint readable.
Earlier leaf directories stay where they are, so a reader still holding the
previous root keeps working. Reopening a pack drops what was appended after
its last checkpoint.
"""

from __future__ import annotations

import gzip
import json
import os
import threading
from array import array
from pathlib import Path

import numpy as np

SLOT = 8128                      # each root slot; both end before byte 16384
SLOTS = (127, 127 + SLOT)
DATA = 16384                     # tile data starts here
_LEAF = 4096                     # entries per leaf directory (doubled if the root would not fit)


def _kind(data: bytes) -> str | None:
    if data[:4] == b"\x89PNG":
        return "png"
    if data[:2] == b"\xff\xd8":
        return "jpeg"
    return None


class GrowingPack:
    """Append tiles to a raster pack; `checkpoint()` makes it a complete pack."""

    def __init__(self, path, *, metadata: dict | None = None):
        from pmtiles.tile import TileType

        self.path = Path(path)
        self.metadata = dict(metadata or {})
        self.lock = threading.Lock()
        self.tids, self.offs = array("Q"), array("Q")
        self.lens, self.looks = array("I"), array("q")    # looks: hash of the bytes, 0 = unknown
        self.kind: str | None = None
        self.slot = -1                                  # the slot the header points at
        self._types = {"png": TileType.PNG, "jpeg": TileType.JPEG}
        self._held = np.zeros(0, np.uint64)
        if not self._load():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "wb") as f:
                f.write(b"\0" * DATA)
        self.f = open(self.path, "r+b")
        self.f.seek(0, os.SEEK_END)
        self.end = self.f.tell()
        self._held = np.unique(np.frombuffer(self.tids, np.uint64))
        self.loaded = len(self._held)

    # ------------------------------------------------------------------ #
    def _load(self) -> bool:
        """Take up the pack on disk (its last checkpoint); False when there is
        none this writer can grow."""
        from pmtiles.reader import MmapSource, Reader
        from pmtiles.tile import deserialize_directory, deserialize_header

        if not self.path.is_file() or self.path.stat().st_size < DATA:
            return False
        try:
            with open(self.path, "rb") as f:
                head = deserialize_header(f.read(127))
                if head["tile_data_offset"] != DATA or head["root_offset"] not in SLOTS:
                    return False
                todo = [(head["root_offset"], head["root_length"])]
                while todo:
                    off, n = todo.pop()
                    f.seek(off)
                    for e in deserialize_directory(f.read(n)):
                        if e.run_length == 0:
                            todo.append((head["leaf_directory_offset"] + e.offset, e.length))
                            continue
                        for i in range(e.run_length):
                            self.tids.append(e.tile_id + i)
                            self.offs.append(e.offset)
                            self.lens.append(e.length)
                            self.looks.append(0)
                self.metadata = {**Reader(MmapSource(f)).metadata(), **self.metadata}
        except Exception:
            self.tids, self.offs = array("Q"), array("Q")
            self.lens, self.looks = array("I"), array("q")
            return False
        self.kind = {v: k for k, v in self._types.items()}.get(head["tile_type"])
        self.slot = SLOTS.index(head["root_offset"])
        # what was appended after the last checkpoint belongs to no pack: drop it
        valid = max(DATA + head["tile_data_length"],
                    head["leaf_directory_offset"] + head["leaf_directory_length"],
                    head["metadata_offset"] + head["metadata_length"])
        if self.path.stat().st_size > valid:
            with open(self.path, "r+b") as f:
                f.truncate(valid)
        return True

    # ------------------------------------------------------------------ #
    @property
    def count(self) -> int:
        return len(self.tids)

    def holds(self, tids) -> np.ndarray:
        """Which of these tile ids the last checkpoint (or the reopened pack) holds."""
        t = np.asarray(tids, np.uint64)
        if not len(self._held):
            return np.zeros(len(t), bool)
        i = np.searchsorted(self._held, t)
        i[i >= len(self._held)] = 0
        return self._held[i] == t

    def add(self, tid: int, data: bytes) -> bool:
        """Append one tile (False when it is not an image of the pack's kind)."""
        kind = _kind(data)
        if kind is None:
            return False
        with self.lock:
            if self.kind is None:
                self.kind = kind
            elif kind != self.kind:
                return False
            self.f.seek(self.end)
            self.f.write(data)
            self.tids.append(int(tid))
            self.offs.append(self.end - DATA)
            self.lens.append(len(data))
            self.looks.append(hash(data) or 1)
            self.end += len(data)
        return True

    # ------------------------------------------------------------------ #
    def _directories(self, T, O, L):
        from pmtiles.tile import Entry, serialize_directory

        n = len(T)
        if n <= _LEAF:
            root = serialize_directory([Entry(int(T[i]), int(O[i]), int(L[i]), 1)
                                        for i in range(n)])
            if len(root) <= SLOT:
                return root, b""
        leaf = _LEAF
        while True:
            leaves, top = bytearray(), []
            for a in range(0, n, leaf):
                chunk = [Entry(int(T[i]), int(O[i]), int(L[i]), 1)
                         for i in range(a, min(a + leaf, n))]
                b = serialize_directory(chunk)
                top.append(Entry(chunk[0].tile_id, len(leaves), len(b), 0))
                leaves += b
            root = serialize_directory(top)
            if len(root) <= SLOT:
                return root, bytes(leaves)
            leaf *= 2

    def checkpoint(self, *, bounds, drop_repeats_from: int | None = None,
                   repeat_limit: int = 16) -> dict:
        """Make the file a complete pack of every tile so far. `bounds`: lon /
        lat box. `drop_repeats_from`: from this zoom on, one picture on
        `repeat_limit` tiles or more is a placeholder, left out of the pack.
        Returns {"tiles": n, "dropped": n}."""
        from pmtiles.tile import Compression, serialize_header, tileid_to_zxy

        with self.lock:
            T = np.frombuffer(self.tids, np.uint64).copy()
            O = np.frombuffer(self.offs, np.uint64).copy()
            L = np.frombuffer(self.lens, np.uint32).copy()
            H = np.frombuffer(self.looks, np.int64).copy()
            if not len(T):
                return {"tiles": 0, "dropped": 0}
            order = np.argsort(T, kind="stable")
            T, O, L, H = T[order], O[order], L[order], H[order]
            last = np.r_[T[1:] != T[:-1], True]           # a tile added twice: the newest
            T, O, L, H = T[last], O[last], L[last], H[last]
            dropped = 0
            if drop_repeats_from is not None:
                first = _first_tile_id(drop_repeats_from)
                street = (T >= first) & (H != 0)
                vals, counts = np.unique(H[street], return_counts=True)
                same = vals[counts >= repeat_limit]
                if len(same):
                    bad = street & np.isin(H, same)
                    dropped = int(bad.sum())
                    T, O, L = T[~bad], O[~bad], L[~bad]
            if not len(T):
                return {"tiles": 0, "dropped": dropped}
            root, leaves = self._directories(T, O, L)
            meta = gzip.compress(json.dumps(self.metadata).encode(), mtime=0)
            self.f.flush()
            os.fsync(self.f.fileno())
            meta_at = self.end
            leaves_at = meta_at + len(meta)
            self.f.seek(meta_at)
            self.f.write(meta)
            self.f.write(leaves)
            self.end = leaves_at + len(leaves)
            slot = 1 - self.slot if self.slot in (0, 1) else 0
            lon0, lat0, lon1, lat1 = bounds
            zmin, zmax = tileid_to_zxy(int(T[0]))[0], tileid_to_zxy(int(T[-1]))[0]
            head = serialize_header({
                "root_offset": SLOTS[slot], "root_length": len(root),
                "metadata_offset": meta_at, "metadata_length": len(meta),
                "leaf_directory_offset": leaves_at, "leaf_directory_length": len(leaves),
                "tile_data_offset": DATA, "tile_data_length": int((O + L).max()),
                "addressed_tiles_count": len(T), "tile_entries_count": len(T),
                "tile_contents_count": len(T), "clustered": False,
                "internal_compression": Compression.GZIP,
                "tile_compression": Compression.NONE,
                "tile_type": self._types[self.kind or "jpeg"],
                "min_zoom": zmin, "max_zoom": zmax,
                "min_lon_e7": int(lon0 * 1e7), "min_lat_e7": int(lat0 * 1e7),
                "max_lon_e7": int(lon1 * 1e7), "max_lat_e7": int(lat1 * 1e7),
                "center_zoom": zmin, "center_lon_e7": int((lon0 + lon1) / 2 * 1e7),
                "center_lat_e7": int((lat0 + lat1) / 2 * 1e7)})
            self.f.flush()
            os.fsync(self.f.fileno())
            self.f.seek(SLOTS[slot])                       # the new root, beside the old
            self.f.write(root)
            self.f.flush()
            os.fsync(self.f.fileno())
            self.f.seek(0)                                 # then the header points at it
            self.f.write(head)
            self.f.flush()
            os.fsync(self.f.fileno())
            self.slot = slot
            self._held = T
            return {"tiles": len(T), "dropped": dropped}

    def close(self) -> None:
        try:
            self.f.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _first_tile_id(z: int) -> int:
    """The first PMTiles tile id of zoom z (ids run zoom by zoom)."""
    return sum(4 ** i for i in range(z))


__all__ = ["DATA", "GrowingPack", "SLOT", "SLOTS"]
