"""Persistent PNG folder-arrival experiment. A folder name simulates an account.

Polling observes stable file ARRIVALS, not network transfers or authenticated
people. Exact registered file hashes connect receipts into a branchable graph.
Renames, restarts, and our own writes do not embed the same code repeatedly.
"""
from contextlib import AbstractContextManager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid

from .detector import Detector
from .pngio import decode_png, encode_png, sample_drawing
from .watermark import Config, embed


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def signed(body, key):
    return hmac.new(key, b"ChainMark/PNG/receipt/v1\0" + canonical(body), hashlib.sha256).hexdigest()


def write_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+"\n", encoding="utf-8")
    temp.replace(path)


class WorkspaceLock(AbstractContextManager):
    """OS-held lock: released on exit or crash, including on Windows."""
    def __init__(self, path):
        self.path, self.stream = Path(path), None

    def __enter__(self):
        try:
            self.stream = self.path.open("a+b")
            # Reading an already locked byte raises PermissionError on Windows.
            # Only initialize an empty file, then attempt the OS lock directly.
            if self.path.stat().st_size == 0:
                self.stream.write(b"0")
                self.stream.flush()
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if self.stream:
                self.stream.close()
            self.stream = None
            raise RuntimeError("Another watcher is already using this workspace") from exc
        return self

    def __exit__(self, *args):
        if self.stream:
            self.stream.close()
        return False


def initialize(root, source=None, users=("A", "B", "C"), config=Config()):
    root = Path(root).resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError("Choose an empty/new workspace; existing files will not be reset")
    if not users or len({u.casefold() for u in users}) != len(users) or any(not u.isalnum() or u.upper() in ("OWNER", "CONTROL") for u in users):
        raise ValueError("Use unique alphanumeric folder IDs, excluding OWNER and CONTROL")
    raw = Path(source).read_bytes() if source else encode_png(sample_drawing())
    image = decode_png(raw)
    if (image.shape[0]//config.block)*(image.shape[1]//config.block) < config.min_blocks:
        raise ValueError("Source image is too small for this watermark configuration")
    state = root / ".chainmark"
    state.mkdir(parents=True)
    (state / "objects").mkdir()
    (state / "reference.png").write_bytes(raw)
    (state / "key.bin").write_bytes(secrets.token_bytes(32))
    (root / "source.png").write_bytes(raw)
    for user in users:
        (root / user).mkdir()
    settings = {"format": 1, "doc_id": "png-"+uuid.uuid4().hex, "users": list(users),
                "reference_sha256": sha(raw), "config": config.to_dict(),
                "threshold_calibrated": False,
                "identity_model": "Folder names simulate accounts; no human authentication."}
    write_json(state / "config.json", settings)
    with sqlite3.connect(state / "events.sqlite3") as db:
        db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE receipts (
                id TEXT PRIMARY KEY, input_sha TEXT NOT NULL, recipient TEXT NOT NULL,
                output_sha TEXT NOT NULL UNIQUE, body TEXT NOT NULL, signature TEXT NOT NULL,
                published INTEGER NOT NULL DEFAULT 0, UNIQUE(input_sha, recipient));
            CREATE TABLE observations (
                id INTEGER PRIMARY KEY, utc TEXT NOT NULL, path TEXT NOT NULL,
                input_sha TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL,
                UNIQUE(path, input_sha, status));
        """)
    return root


class PNGWorkspace:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.state = self.root / ".chainmark"
        self.settings = json.loads((self.state / "config.json").read_text(encoding="utf-8"))
        self.key = (self.state / "key.bin").read_bytes()
        self.config = Config(**self.settings["config"])
        self.reference_bytes = (self.state / "reference.png").read_bytes()
        if sha(self.reference_bytes) != self.settings["reference_sha256"]:
            raise ValueError("Clean reference has changed")
        self.reference = decode_png(self.reference_bytes)
        self.db = sqlite3.connect(self.state / "events.sqlite3", timeout=10)
        self.db.row_factory = sqlite3.Row
        self.detector = None

    def close(self):
        self.db.close()

    def _body(self, row):
        body = json.loads(row["body"])
        if not hmac.compare_digest(row["signature"], signed(body, self.key)):
            raise ValueError("Invalid receipt signature")
        if (body["id"] != row["id"] or body["input_sha256"] != row["input_sha"]
                or body["output_sha256"] != row["output_sha"] or body["recipient"] != row["recipient"]
                or body["doc_id"] != self.settings["doc_id"] or body["config"] != self.settings["config"]
                or body["reference_sha256"] != self.settings["reference_sha256"]):
            raise ValueError("Receipt index or document mismatch")
        return body

    def receipts(self):
        return self.db.execute("SELECT * FROM receipts ORDER BY rowid").fetchall()

    def _lookup(self, fingerprint):
        return self.db.execute("SELECT * FROM receipts WHERE output_sha=?", (fingerprint,)).fetchone()

    def observe(self, path, fingerprint, status, detail):
        rel = str(Path(path).relative_to(self.root))
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO observations(utc,path,input_sha,status,detail) VALUES(?,?,?,?,?)",
                            (datetime.now(timezone.utc).isoformat(), rel, fingerprint, status, detail))
        return {"status": status, "path": rel, "detail": detail}

    def trace(self, fingerprint):
        """Order follows authenticated parent links, never pattern score order."""
        row = self._lookup(fingerprint)
        if row is None:
            return {"status": "unregistered_bytes", "recorded_order": None,
                    "unverified_segments": ["No exact registered PNG hash; image may still contain detectable patterns."]}
        chain, seen = [], set()
        try:
            while row:
                body = self._body(row)
                if body["id"] in seen:
                    raise ValueError("Receipt cycle")
                seen.add(body["id"])
                if not row["published"]:
                    raise ValueError("Receipt prepared but destination publication unconfirmed")
                chain.append(body)
                parent = body["parent_id"]
                if parent is None:
                    if body["input_sha256"] != self.settings["reference_sha256"] or body["sender"] != "OWNER":
                        raise ValueError("Unknown history before first receipt")
                    break
                row = self.db.execute("SELECT * FROM receipts WHERE id=?", (parent,)).fetchone()
                if row is None:
                    raise ValueError("Missing parent receipt")
                previous = self._body(row)
                if previous["output_sha256"] != body["input_sha256"] or previous["recipient"] != body["sender"]:
                    raise ValueError("Disconnected transfer edge")
            chain.reverse()
            patterns = []
            for body in chain:
                added = body["recipient"] not in patterns
                if added:
                    patterns.append(body["recipient"])
                if body["patterns"] != patterns or body["pattern_added"] != added:
                    raise ValueError("Inconsistent pattern history")
            return {"status": "complete_registered_lineage", "recorded_order": ["OWNER"]+[b["recipient"] for b in chain],
                    "receipts": chain, "unverified_segments": [],
                    "caveat": "Folder arrivals only. External intermediaries, human identities, and copy-vs-move are unverified."}
        except (ValueError, KeyError, TypeError) as exc:
            return {"status": "incomplete_or_invalid", "recorded_order": None,
                    "observed_receipts": chain, "unverified_segments": [str(exc)]}

    def _archive(self, raw):
        path = self.state / "objects" / (sha(raw)+".png")
        if not path.exists():
            path.write_bytes(raw)
        elif sha(path.read_bytes()) != sha(raw):
            raise ValueError("Archived PNG has changed")
        return path

    def process(self, path, user, expected_sha=None):
        """Prepare persistently, recheck incoming bytes, atomically replace PNG.

        The journal is committed BEFORE replacement. A locked file or restart
        retries the same prepared rendition, without adding another watermark.
        """
        path = Path(path).resolve()
        if user not in self.settings["users"] or path.parent != self.root/user or path.is_symlink():
            raise ValueError("PNG must be a regular file directly inside the configured user's folder")
        raw = path.read_bytes()
        fingerprint = sha(raw)
        if expected_sha is not None and fingerprint != expected_sha:
            return {"status": "changed_during_read"}
        known = self._lookup(fingerprint)
        if known and self._body(known)["recipient"] == user:
            # Crash recovery: our published PNG may precede the SQLite ack.
            with self.db:
                self.db.execute("UPDATE receipts SET published=1 WHERE id=?", (known["id"],))
            return {"status": "already_processed", "receipt_id": known["id"], "recovered": not bool(known["published"])}
        cached = self.db.execute("SELECT * FROM receipts WHERE input_sha=? AND recipient=?", (fingerprint, user)).fetchone()
        if cached:
            body = self._body(cached)
            outgoing = (self.state/"objects"/(body["output_sha256"]+".png")).read_bytes()
            if sha(outgoing) != body["output_sha256"]:
                raise ValueError("Cached output was modified")
        else:
            if fingerprint == self.settings["reference_sha256"]:
                parent_id, sender, patterns = None, "OWNER", []
            elif known:
                ancestry = self.trace(fingerprint)
                if ancestry["status"] != "complete_registered_lineage":
                    return self.observe(path, fingerprint, "unverified_parent", str(ancestry["unverified_segments"]))
                parent = self._body(known)
                parent_id, sender, patterns = parent["id"], parent["recipient"], list(parent["patterns"])
            else:
                return self.observe(path, fingerprint, "unknown_source", "Unregistered bytes left unchanged; no transfer order invented.")
            image = decode_png(raw)
            if image.shape != self.reference.shape:
                return self.observe(path, fingerprint, "geometry_changed", "Inspect patterns separately; no automatic attribution.")
            added = user not in patterns
            if added:
                image = embed(image, user, self.settings["doc_id"], 0, self.key, self.config)
                patterns.append(user)
            receipt_id = uuid.uuid4().hex
            outgoing = encode_png(image, receipt_id)
            body = {"id": receipt_id, "parent_id": parent_id, "utc": datetime.now(timezone.utc).isoformat(),
                    "doc_id": self.settings["doc_id"], "sender": sender, "recipient": user,
                    "config": self.settings["config"], "reference_sha256": self.settings["reference_sha256"],
                    "input_sha256": fingerprint, "output_sha256": sha(outgoing),
                    "patterns": patterns, "pattern_added": added, "observation": "stable_png_arrival",
                    "destination": str(path.relative_to(self.root))}
            self._archive(raw)
            self._archive(outgoing)
            with self.db:
                self.db.execute("INSERT INTO receipts(id,input_sha,recipient,output_sha,body,signature) VALUES(?,?,?,?,?,?)",
                                (body["id"], fingerprint, user, body["output_sha256"], canonical(body).decode(), signed(body, self.key)))
        temp = path.with_name(".chainmark-"+body["id"]+".tmp")
        try:
            with temp.open("wb") as stream:
                stream.write(outgoing)
                stream.flush()
                os.fsync(stream.fileno())
            # Best-effort concurrent writer check; not a security boundary.
            if sha(path.read_bytes()) != fingerprint:
                return {"status": "changed_before_replace"}
            temp.replace(path)
            with self.db:
                self.db.execute("UPDATE receipts SET published=1 WHERE id=?", (body["id"],))
        finally:
            temp.unlink(missing_ok=True)
        return {"status": "ready", "receipt_id": body["id"], "sender": body["sender"], "recipient": user,
                "pattern_added": body["pattern_added"], "output_sha256": body["output_sha256"],
                "patterns": body["patterns"], "reused_receipt": cached is not None}

    def inspect(self, path):
        raw = Path(path).read_bytes()
        if self.detector is None:
            self.detector = Detector(self.reference, self.settings["doc_id"], 0, self.key, self.config)
        result, _ = self.detector.detect(decode_png(raw), self.settings["users"]+["CONTROL"])
        return {"pattern_evidence": result, "threshold_calibrated": False,
                "transfer_evidence": self.trace(sha(raw))}

    def export(self):
        rows = self.receipts()
        events = [{**json.loads(r["body"]), "signature": r["signature"], "published": bool(r["published"])} for r in rows]
        write_json(self.root/"events.json", events)
        write_json(self.root/"observations.json", [dict(r) for r in self.db.execute("SELECT * FROM observations ORDER BY id")])


class FolderWatcher:
    def __init__(self, workspace, settle_seconds=.4, clock=time.monotonic):
        if settle_seconds < 0:
            raise ValueError("settle_seconds must be nonnegative")
        self.workspace, self.settle_seconds, self.clock = workspace, settle_seconds, clock
        self.pending, self.handled, self.last_errors = {}, {}, {}

    def scan_once(self):
        outcomes, present = [], set()
        for user in self.workspace.settings["users"]:
            for path in sorted((self.workspace.root/user).iterdir()):
                if path.suffix.lower() != ".png" or path.is_symlink() or not path.is_file():
                    continue
                present.add(path)
                fingerprint = "unreadable"
                try:
                    fingerprint = sha(path.read_bytes())
                    if self.handled.get(path) == fingerprint:
                        continue
                    now = self.clock()
                    last = self.pending.get(path)
                    if last is None or last[0] != fingerprint:
                        self.pending[path] = (fingerprint, now)
                        continue
                    if now-last[1] < self.settle_seconds:
                        continue
                    outcome = self.workspace.process(path, user, fingerprint)
                    if outcome["status"] in ("ready", "already_processed", "unknown_source", "unverified_parent", "geometry_changed"):
                        self.handled[path] = outcome.get("output_sha256", fingerprint)
                    if outcome["status"] == "ready" or outcome.get("recovered"):
                        self.workspace.export()
                    self.last_errors.pop(path, None)
                    outcomes.append({"file": str(path), **outcome})
                except (OSError, ValueError) as exc:
                    # Partial copies, locked destinations and invalid files do
                    # not kill the watcher. Unchanged bad input is not spammed.
                    outcome = self.workspace.observe(path, fingerprint, "retry_or_invalid_png", str(exc))
                    if self.last_errors.get(path) != (fingerprint, str(exc)):
                        outcomes.append(outcome)
                        self.last_errors[path] = (fingerprint, str(exc))
        for path in set(self.pending)-present:
            self.pending.pop(path, None)
            self.handled.pop(path, None)
            self.last_errors.pop(path, None)
        if any(r["status"] not in ("ready", "already_processed", "changed_during_read", "changed_before_replace") for r in outcomes):
            self.workspace.export()
        return outcomes


def watch(root, interval=.2, settle_seconds=.4):
    if interval <= 0 or settle_seconds < 0:
        raise ValueError("Polling interval must be positive and settling delay nonnegative")
    root = Path(root).resolve()
    with WorkspaceLock(root/".chainmark"/"watcher.lock"):
        workspace = PNGWorkspace(root)
        stop = root/".chainmark"/"stop.request"
        stop.unlink(missing_ok=True)
        write_json(root/"watcher_status.json", {"status": "watching", "pid": os.getpid()})
        print(f"WATCHING: {root}", flush=True)
        print("Copy source.png into A. Wait for READY, then copy/move that PNG into B and C.", flush=True)
        try:
            watcher = FolderWatcher(workspace, settle_seconds)
            while not stop.exists():
                for outcome in watcher.scan_once():
                    if outcome["status"] == "ready":
                        print(f"READY {outcome['sender']} -> {outcome['recipient']}: {','.join(outcome['patterns'])} | {outcome['file']}", flush=True)
                    elif outcome["status"] not in ("already_processed", "changed_during_read", "changed_before_replace"):
                        print(json.dumps(outcome, ensure_ascii=False), flush=True)
                time.sleep(interval)
        except KeyboardInterrupt:
            pass
        finally:
            workspace.export()
            workspace.close()
            write_json(root/"watcher_status.json", {"status": "stopped", "pid": os.getpid()})
