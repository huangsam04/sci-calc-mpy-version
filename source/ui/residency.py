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


def _roll_checksum(value, byte):
    value = (value + byte) & 0xFFFFFFFF
    return ((value << 5) | (value >> 27)) & 0xFFFFFFFF


def _payload_metrics(payload):
    """Return UTF-8 byte length/checksum without duplicating the payload."""
    size = 0
    value = 1
    for char in payload:
        code = ord(char)
        if code < 0x80:
            value = _roll_checksum(value, code)
            size += 1
        elif code < 0x800:
            value = _roll_checksum(value, 0xC0 | (code >> 6))
            value = _roll_checksum(value, 0x80 | (code & 0x3F))
            size += 2
        elif code < 0x10000:
            value = _roll_checksum(value, 0xE0 | (code >> 12))
            value = _roll_checksum(value, 0x80 | ((code >> 6) & 0x3F))
            value = _roll_checksum(value, 0x80 | (code & 0x3F))
            size += 3
        else:
            value = _roll_checksum(value, 0xF0 | (code >> 18))
            value = _roll_checksum(value, 0x80 | ((code >> 12) & 0x3F))
            value = _roll_checksum(value, 0x80 | ((code >> 6) & 0x3F))
            value = _roll_checksum(value, 0x80 | (code & 0x3F))
            size += 4
    return size, value


def _safe_key(key):
    key = str(key)
    if not key or any(not (char.isalnum() or char in "_-") for char in key):
        raise ValueError("Invalid page swap key")
    return key


def _record_header(payload_size, checksum):
    return (SWAP_MAGIC + "|" + str(SWAP_VERSION) + "|"
            + str(payload_size) + "|" + str(checksum) + "\n")


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
        payload_size, checksum = _payload_metrics(payload)
        header = _record_header(payload_size, checksum)
        if len(header) + payload_size > self.max_snapshot_bytes:
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
            payload_size, checksum = _payload_metrics(payload)
            header = _record_header(payload_size, checksum)
            if len(header) + payload_size > self.max_snapshot_bytes:
                raise SwapError("Page snapshot is too large")
            primary = self._path(key)
            temporary = self._path(key, ".tmp")
            backup = self._path(key, ".bak")
            with open(temporary, "w") as target:
                target.write(header)
                target.write(payload)
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
            if isinstance(error, OSError):
                self.available = False
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
                header = source.readline(96)
                payload = source.read(self.max_snapshot_bytes + 1)
            parts = header.rstrip("\n").split("|")
            if (len(parts) != 4
                    or parts[0] != SWAP_MAGIC
                    or int(parts[1]) != SWAP_VERSION):
                raise SwapError("Invalid page snapshot header")
            expected_size = int(parts[2])
            expected_checksum = int(parts[3])
            size, checksum = _payload_metrics(payload)
            if (len(header) + size > self.max_snapshot_bytes
                    or size != expected_size
                    or checksum != expected_checksum):
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
        self._pending_key = None
        self._pending_state = None
        self._pending_payload = None
        self._expected = set()
        self._persisted = set()
        self._errors = {}
        self._dirty_screen = None
        self._current = None
        self._restore_pending = False
        self._restore_finished = False
        self._unavailable_error = ""

    @staticmethod
    def _key(screen):
        return getattr(screen, "swap_key", None)

    def _queue_state(self, key, state):
        previous = self._pending_key
        if previous and previous != key:
            return False
        self._pending_key = key
        self._pending_state = state
        self._pending_payload = None
        self._expected.add(key)
        self._errors.pop(key, None)
        return True

    def mark_dirty(self, screen):
        """Queue a zero-copy save request for the next quiet loop."""
        if self._key(screen) and screen is self._current:
            self._dirty_screen = screen

    def leave(self, screen):
        """Pack bounded state and release the outgoing page without file I/O."""
        # A page may be left while its old snapshot is still being restored.
        # Never replace that valid snapshot with the visible default shell.
        capture_state = not (screen is self._current
                             and not self._restore_finished)
        key = self._key(screen)
        snapshotter = getattr(screen, "snapshot_state", None)
        state = None
        captured = False
        snapshot_error = None
        if capture_state and key and snapshotter is not None:
            try:
                # Capture logical state before release_memory() drops any
                # rebuildable objects. Encoding remains bounded and occurs
                # only after those larger caches have been returned.
                state = snapshotter()
                captured = True
            except Exception as error:
                snapshot_error = error

        releaser = getattr(screen, "release_memory", None)
        released = bool(releaser is not None and releaser())
        if released and self.memory is not None:
            self.memory.collect()

        if captured:
            try:
                if not self._queue_state(key, state):
                    raise SwapError("Another page snapshot is still pending")
            except Exception as error:
                snapshot_error = error
        if snapshot_error is not None:
            if self._pending_key == key:
                self._pending_key = None
                self._pending_state = None
                self._pending_payload = None
            self._expected.discard(key)
            message = str(snapshot_error) or "Page snapshot failed"
            self._errors[key] = message
        if self._dirty_screen is screen:
            self._dirty_screen = None

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

    def is_restoring(self, screen):
        return screen is self._current and not self._restore_finished

    def _flush_one(self):
        if self._pending_key is None:
            return False
        key = self._pending_key
        if self._pending_payload is None:
            try:
                self._pending_payload = self.swap.pack(self._pending_state)
                self._pending_state = None
            except Exception as error:
                self._pending_key = None
                self._pending_state = None
                self._expected.discard(key)
                self._persisted.discard(key)
                self._errors[key] = str(error) or "Page snapshot failed"
            return True
        payload = self._pending_payload
        self._pending_key = None
        self._pending_state = None
        self._pending_payload = None
        if not self.swap.write_packed(key, payload):
            self._expected.discard(key)
            self._persisted.discard(key)
            message = self.swap.last_error or "Page snapshot write failed"
            self._errors[key] = message
            if not self.swap.available:
                self._unavailable_error = message
        else:
            self._persisted.add(key)
        return True

    def _reset_with_error(self, screen, key, message):
        if key:
            self._expected.discard(key)
            self._persisted.discard(key)
            if self._pending_key == key:
                self._pending_key = None
                self._pending_state = None
                self._pending_payload = None
            self.swap.discard(key)
        resetter = getattr(screen, "reset_state", None)
        if resetter is not None:
            resetter()
        reporter = getattr(screen, "show_residency_error", None)
        if reporter is not None:
            reporter(message)

    def settle(self, screen):
        """Perform one post-animation write, restore or page rebuild step."""
        if screen is not self._current:
            return 0
        if self._flush_one():
            return SETTLE_MORE
        key = self._key(screen)
        if self._unavailable_error:
            message = self._unavailable_error
            self._unavailable_error = ""
            self._reset_with_error(screen, key, message)
            self._restore_pending = False
            self._restore_finished = True
            return SETTLE_REDRAW
        if key and key in self._errors:
            self._reset_with_error(screen, key, self._errors.pop(key))
            self._restore_pending = False
            self._restore_finished = True
            return SETTLE_REDRAW
        if self._restore_finished:
            flags = self._settle_screen_step(screen)
            if flags:
                return flags
            if self._dirty_screen is screen:
                self._dirty_screen = None
                snapshotter = getattr(screen, "snapshot_state", None)
                if key and snapshotter is not None:
                    try:
                        if not self._queue_state(key, snapshotter()):
                            raise SwapError(
                                "Another page snapshot is still pending")
                        return SETTLE_MORE
                    except Exception as error:
                        self._errors[key] = (
                            str(error) or "Page snapshot failed")
                        self._reset_with_error(screen, key, self._errors.pop(key))
                        return SETTLE_REDRAW
            return 0

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
                self._restore_finished = True
                return SETTLE_REDRAW
            self._restore_pending = False
            return SETTLE_REDRAW | SETTLE_MORE

        flags = self._settle_screen_step(screen)
        if not (flags & SETTLE_MORE):
            self._restore_finished = True
        return flags

    def _settle_screen_step(self, screen):
        stepper = getattr(screen, "settle_step", None)
        if stepper is None:
            return 0
        try:
            return int(stepper() or 0)
        except Exception as error:
            self._reset_with_error(
                screen, self._key(screen),
                str(error) or "Page restore failed")
            self._restore_pending = False
            self._restore_finished = True
            return SETTLE_REDRAW

    def recover(self, screen):
        """Adopt a root page after fatal recovery without retaining RAM work."""
        pending = self._pending_key
        if pending and pending not in self._persisted:
            self._expected.discard(pending)
        self._pending_key = None
        self._pending_state = None
        self._pending_payload = None
        self._unavailable_error = ""
        self._dirty_screen = None
        self._current = screen
        self._restore_pending = False
        self._restore_finished = True
