"""Page residency and crash-detecting, session-only page snapshots."""
import json
import os


SWAP_MAGIC = "SCI-CALC-PAGE"
SWAP_VERSION = 1
MAX_SNAPSHOT_BYTES = 4096
SETTLE_MORE = 1
SETTLE_REDRAW = 2


class SwapError(Exception):
    """A page snapshot is unavailable or cannot be trusted."""


def _join(directory, filename):
    if directory.endswith("/") or directory.endswith("\\"):
        return directory + filename
    separator = "\\" if "\\" in directory and "/" not in directory else "/"
    return directory + separator + filename


def _checksum(payload):
    """Small allocation-free checksum suitable for disposable session data."""
    value = 1
    for byte in payload.encode("utf-8"):
        value = (value + byte) & 0xFFFFFFFF
        value = ((value << 5) | (value >> 27)) & 0xFFFFFFFF
    return value


def _safe_key(key):
    key = str(key)
    if not key or any(not (char.isalnum() or char in "_-") for char in key):
        raise ValueError("Invalid page swap key")
    return key


class SessionSwap:
    """Store independent bounded page records for the current boot session."""

    def __init__(self, directory="/sd/.sci-calc/swap",
                 max_snapshot_bytes=MAX_SNAPSHOT_BYTES):
        self.directory = directory
        self.max_snapshot_bytes = max(128, int(max_snapshot_bytes))
        self.available = False
        self.last_error = ""

    def _path(self, key, suffix=".swp"):
        return _join(self.directory, _safe_key(key) + suffix)

    def _ensure_directory(self):
        current = ""
        path = self.directory.replace("\\", "/")
        absolute = path.startswith("/")
        for part in path.split("/"):
            if not part:
                continue
            if current:
                current += "/" + part
            elif absolute:
                current = "/" + part
            else:
                current = part
            try:
                os.mkdir(current)
            except OSError:
                try:
                    os.stat(current)
                except OSError:
                    raise

    def start_session(self):
        """Create the swap directory and discard records from an older boot."""
        try:
            self._ensure_directory()
            for name in os.listdir(self.directory):
                if name.endswith((".swp", ".tmp", ".bak")):
                    try:
                        os.remove(_join(self.directory, name))
                    except OSError:
                        pass
            self.available = True
            self.last_error = ""
        except Exception as error:
            self.available = False
            self.last_error = str(error) or "SD unavailable"
        return self.available

    def discard(self, key):
        for suffix in (".swp", ".tmp", ".bak"):
            try:
                os.remove(self._path(key, suffix))
            except OSError:
                pass

    def pack(self, state):
        """Encode one bounded state record without touching the filesystem."""
        payload = json.dumps(state)
        if len(payload.encode("utf-8")) > self.max_snapshot_bytes:
            raise SwapError("Page snapshot is too large")
        return payload

    def write_packed(self, key, payload):
        """Atomically replace one already-packed page record."""
        if not self.available:
            # A page remains usable after an error.  Retry the storage seam on
            # the next real state change so a remounted/reinserted card can
            # recover without rebooting the calculator.
            try:
                self._ensure_directory()
                self.available = True
            except Exception as error:
                self.last_error = str(error) or "SD unavailable"
                return False
        try:
            payload_size = len(payload.encode("utf-8"))
            if payload_size > self.max_snapshot_bytes:
                raise SwapError("Page snapshot is too large")
            envelope = {
                "magic": SWAP_MAGIC,
                "version": SWAP_VERSION,
                "length": payload_size,
                "checksum": _checksum(payload),
                "payload": payload,
            }
            primary = self._path(key)
            temporary = self._path(key, ".tmp")
            backup = self._path(key, ".bak")
            with open(temporary, "w") as target:
                json.dump(envelope, target)
                flusher = getattr(target, "flush", None)
                if flusher is not None:
                    flusher()
            try:
                os.remove(backup)
            except OSError:
                pass
            try:
                os.rename(primary, backup)
            except OSError:
                pass
            try:
                os.rename(temporary, primary)
            except Exception:
                try:
                    os.rename(backup, primary)
                except OSError:
                    pass
                raise
            try:
                os.remove(backup)
            except OSError:
                pass
            self.last_error = ""
            return True
        except Exception as error:
            self.last_error = str(error) or "Page snapshot write failed"
            try:
                os.remove(self._path(key, ".tmp"))
            except OSError:
                pass
            return False

    def write(self, key, state):
        """Atomically replace one page record; return false on storage failure."""
        try:
            return self.write_packed(key, self.pack(state))
        except Exception as error:
            self.last_error = str(error) or "Page snapshot write failed"
            return False

    def read(self, key):
        """Return one verified page state or raise and invalidate that page."""
        if not self.available:
            raise SwapError(self.last_error or "SD unavailable")
        try:
            with open(self._path(key), "r") as source:
                envelope = json.load(source)
            payload = envelope.get("payload")
            if (envelope.get("magic") != SWAP_MAGIC
                    or envelope.get("version") != SWAP_VERSION
                    or not isinstance(payload, str)):
                raise SwapError("Invalid page snapshot header")
            size = len(payload.encode("utf-8"))
            if (size != envelope.get("length")
                    or size > self.max_snapshot_bytes
                    or _checksum(payload) != envelope.get("checksum")):
                raise SwapError("Page snapshot checksum failed")
            state = json.loads(payload)
            if not isinstance(state, dict):
                raise SwapError("Invalid page snapshot payload")
            self.last_error = ""
            return state
        except SwapError:
            self.discard(key)
            raise
        except Exception as error:
            self.discard(key)
            raise SwapError(str(error) or "Page snapshot read failed")


class PageResidency:
    """Hide page snapshot, release and post-transition restore sequencing.

    ``leave`` and ``prepare`` never perform filesystem I/O.  ``settle`` does
    at most one storage or rebuild operation so the main loop can keep input
    and presentation responsive between steps.
    """

    def __init__(self, swap=None, memory=None):
        self.swap = swap or SessionSwap()
        self.memory = memory
        self._pending = {}
        self._expected = set()
        self._errors = {}
        self._current = None
        self._restore_pending = False
        self._restore_finished = False
        self._storage_error = ""

    @staticmethod
    def _key(screen):
        return getattr(screen, "swap_key", None)

    def leave(self, screen):
        """Pack bounded state and release the outgoing page without file I/O."""
        # A page may be left while its old snapshot is still being restored.
        # Never replace that valid snapshot with the visible default shell.
        capture_state = not (screen is self._current
                             and not self._restore_finished)
        releaser = getattr(screen, "release_memory", None)
        released = bool(releaser is not None and releaser())
        if released and self.memory is not None:
            self.memory.collect()

        key = self._key(screen)
        snapshotter = getattr(screen, "snapshot_state", None)
        if capture_state and key and snapshotter is not None:
            try:
                self._pending[key] = self.swap.pack(snapshotter())
                self._expected.add(key)
                self._errors.pop(key, None)
            except Exception as error:
                self._pending.pop(key, None)
                self._expected.discard(key)
                message = str(error) or "Page snapshot failed"
                self._errors[key] = message
                self._storage_error = message

        deactivator = getattr(screen, "deactivate", None)
        if deactivator is not None:
            deactivator()
        resetter = getattr(screen, "reset_state", None)
        if resetter is not None:
            resetter()

    def prepare(self, screen):
        """Activate the target in its allocation-bounded default state."""
        self._current = screen
        key = self._key(screen)
        self._restore_pending = bool(key and key in self._expected)
        self._restore_finished = False
        activator = getattr(screen, "activate_default", None)
        if activator is None:
            activator = getattr(screen, "activate", None)
        if activator is not None:
            activator()

    def _flush_one(self):
        if not self._pending:
            return False
        key = next(iter(self._pending))
        payload = self._pending.pop(key)
        if not self.swap.write_packed(key, payload):
            self._expected.discard(key)
            message = self.swap.last_error or "Page snapshot write failed"
            self._errors[key] = message
            self._storage_error = message
        return True

    def _reset_with_error(self, screen, key, message):
        if key:
            self._expected.discard(key)
            self._pending.pop(key, None)
            self.swap.discard(key)
        resetter = getattr(screen, "reset_state", None)
        if resetter is not None:
            resetter()
        reporter = getattr(screen, "show_residency_error", None)
        if reporter is not None:
            reporter(message)

    def settle(self, screen):
        """Perform one post-animation write, restore or page rebuild step."""
        if screen is not self._current or self._restore_finished:
            return 0

        if self._flush_one():
            return SETTLE_MORE

        key = self._key(screen)
        if self._storage_error:
            message = self._storage_error
            self._storage_error = ""
            self._reset_with_error(screen, key, message)
            self._restore_pending = False
            self._restore_finished = True
            return SETTLE_REDRAW
        if key and key in self._errors:
            self._reset_with_error(screen, key, self._errors.pop(key))
            self._restore_pending = False
            self._restore_finished = True
            return SETTLE_REDRAW

        if self._restore_pending:
            try:
                state = self.swap.read(key)
                restorer = getattr(screen, "restore_state", None)
                if restorer is not None:
                    restorer(state)
            except Exception as error:
                self._reset_with_error(screen, key,
                                       str(error) or "Page snapshot read failed")
            self._restore_pending = False
            if getattr(screen, "_residency_error", ""):
                self._restore_finished = True
                return SETTLE_REDRAW
            return SETTLE_REDRAW | SETTLE_MORE

        stepper = getattr(screen, "settle_step", None)
        flags = int(stepper() or 0) if stepper is not None else 0
        if not (flags & SETTLE_MORE):
            self._restore_finished = True
        return flags
