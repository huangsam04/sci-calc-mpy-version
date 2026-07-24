"""Allocation-light integer animation for high-frequency UI feedback.

Animations target one object attribute and are keyed by (object, attribute),
so a newer animation replaces an older one without retaining stale targets.
"""
import time

PROGRESS_SCALE = 1024


def ease_out_quad(progress, scale=PROGRESS_SCALE):
    """Return integer quadratic ease-out progress with exact endpoints."""
    progress = max(0, min(int(scale), int(progress)))
    remaining = int(scale) - progress
    return int(scale) - remaining * remaining // int(scale)


# --- Animation class ---

class Animation:
    __slots__ = (
        "target", "attr", "start_val", "end_val", "duration",
        "delay", "ensure_progress", "created", "started", "start_time",
        "finished", "_stepped")

    def __init__(self, target, start_val, end_val, duration,
                 delay=0, ensure_progress=False):
        self.target = target          # UIElement or dict with key
        self.attr = None              # attribute name string (e.g. 'x', 'y')
        self.start_val = start_val
        self.end_val = end_val
        self.duration = duration      # ms
        self.delay = delay            # ms
        self.ensure_progress = ensure_progress
        self.created = time.ticks_ms()
        self.started = False
        self.finished = False
        self._stepped = False

    def step(self):
        now = time.ticks_ms()
        elapsed = time.ticks_diff(now, self.created)

        if elapsed < self.delay:
            return True  # still registered, waiting to start

        if not self.started:
            self.started = True
            self.start_time = now

        anim_elapsed = time.ticks_diff(now, self.start_time)
        progress = (
            PROGRESS_SCALE if self.duration <= 0 else
            min(PROGRESS_SCALE,
                max(0, anim_elapsed) * PROGRESS_SCALE // self.duration))
        eased = ease_out_quad(progress)
        val = (self.start_val
               + (self.end_val - self.start_val)
               * eased // PROGRESS_SCALE)

        if (self.ensure_progress and self._stepped
                and progress < PROGRESS_SCALE
                and self.end_val != self.start_val):
            if hasattr(self.target, self.attr):
                current = getattr(self.target, self.attr)
            elif isinstance(self.target, dict) and self.attr in self.target:
                current = self.target[self.attr]
            else:
                current = None
            if current == val:
                direction = 1 if self.end_val > self.start_val else -1
                candidate = current + direction
                if ((direction > 0 and candidate <= self.end_val)
                        or (direction < 0 and candidate >= self.end_val)):
                    val = candidate

        if hasattr(self.target, self.attr):
            setattr(self.target, self.attr, val)
        elif isinstance(self.target, dict) and self.attr in self.target:
            self.target[self.attr] = val

        self._stepped = True

        if progress >= PROGRESS_SCALE:
            # Snap to exact end
            if hasattr(self.target, self.attr):
                setattr(self.target, self.attr, self.end_val)
            elif isinstance(self.target, dict) and self.attr in self.target:
                self.target[self.attr] = self.end_val
            self.finished = True
            return False

        return True  # still animating


# --- Global registry ---

_animations = {}  # (id(target), attribute) -> Animation


def insert_animation(target, attr, start_val, end_val, duration,
                     delay=0, ensure_progress=False):
    """Register an animation. Replaces any existing animation on (target, attr)."""
    anim = Animation(target, start_val, end_val, duration, delay,
                     ensure_progress)
    anim.attr = attr
    key = (id(target), attr)

    if key in _animations:
        del _animations[key]

    _animations[key] = anim
    return anim


def cancel_animation(target, attr):
    """Cancel one attribute without disturbing other motion on the target."""
    key = (id(target), attr)
    if key in _animations:
        del _animations[key]
        return True
    return False


def animate_all():
    """Drive all active animations. Call once per frame."""
    if not _animations:
        return
    dead = None
    for key, anim in _animations.items():
        if not anim.step():
            if dead is None:
                dead = []
            dead.append(key)
    if dead is not None:
        for key in dead:
            del _animations[key]


def is_animating(target):
    """Check if a target has any active animations."""
    tid = id(target)
    for k in _animations:
        if k[0] == tid:
            return True
    return False


def has_active_animations():
    """Check if any animations are currently running."""
    return len(_animations) > 0


def active_animation_count():
    return len(_animations)


def cancel_all_animations():
    """Release every animation target before changing screen ownership."""
    _animations.clear()


def cancel_animations(root):
    """Cancel animations owned by one explicit UI element tree."""
    target_ids = {}
    stack = [root]
    while stack:
        target = stack.pop()
        target_ids[id(target)] = True
        children = getattr(target, "animation_children", None)
        if children:
            for child in children():
                stack.append(child)
    for key in list(_animations.keys()):
        if key[0] in target_ids:
            del _animations[key]
