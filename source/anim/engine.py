"""Animation engine for smooth UI transitions.

Animations target one object attribute and are keyed by (object, attribute),
so a newer animation replaces an older one without retaining stale targets.
"""
import time
import math

# --- Easing functions ---

def easing_linear(t):
    """t in [0,1] -> value in [0,1]"""
    return t

def easing_indent(t):
    """Cubic ease-out with exact endpoints."""
    return 1 - (1 - t) ** 3

def easing_indent_inv(t):
    """Cubic ease-in with exact endpoints."""
    return t ** 3


def easing_smooth(t):
    """Cubic ease-in-out for full-screen movement."""
    if t < 0.5:
        return 4 * t * t * t
    return 1 - ((-2 * t + 2) ** 3) / 2

def easing_bounce(t):
    """Exponential decay sine: overshoot and bounce."""
    return pow(2, -10 * t) * math.sin((t * 10 - 0.75) * (2 * 3.14159265 / 3)) + 1

EASING_MAP = {
    "LINEAR": easing_linear,
    "INDENT": easing_indent,
    "INDENTINV": easing_indent_inv,
    "SMOOTH": easing_smooth,
    "BOUNCE": easing_bounce,
}

# --- Animation class ---

class Animation:
    def __init__(self, target, start_val, end_val, duration, easing="INDENT", delay=0):
        self.target = target          # UIElement or dict with key
        self.attr = None              # attribute name string (e.g. 'x', 'y')
        self.start_val = start_val
        self.end_val = end_val
        self.duration = duration      # ms
        self.easing = easing          # string key into EASING_MAP
        self.delay = delay            # ms
        self.created = time.ticks_ms()
        self.started = False
        self.finished = False

    def step(self):
        now = time.ticks_ms()
        elapsed = time.ticks_diff(now, self.created)

        if elapsed < self.delay:
            return True  # still registered, waiting to start

        if not self.started:
            self.started = True
            self.start_time = now

        anim_elapsed = time.ticks_diff(now, self.start_time)
        t = 1.0 if self.duration <= 0 else min(1.0, anim_elapsed / self.duration)

        eased = EASING_MAP[self.easing](t)
        val = self.start_val + (self.end_val - self.start_val) * eased

        if hasattr(self.target, self.attr):
            setattr(self.target, self.attr, int(val))
        elif isinstance(self.target, dict) and self.attr in self.target:
            self.target[self.attr] = int(val)

        if t >= 1.0:
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
_tmp_targets = []  # elements kept alive for exit animations


def insert_animation(target, attr, start_val, end_val, duration, easing="INDENT", delay=0):
    """Register an animation. Replaces any existing animation on (target, attr)."""
    anim = Animation(target, start_val, end_val, duration, easing, delay)
    anim.attr = attr
    key = (id(target), attr)

    if key in _animations:
        del _animations[key]

    _animations[key] = anim
    return anim


def insert_tmp_target(target):
    """Keep a target alive for animations even after it leaves the active tree."""
    if target not in _tmp_targets:
        _tmp_targets.append(target)


def animate_all():
    """Drive all active animations. Call once per frame."""
    dead = [k for k, a in _animations.items() if not a.step()]
    for k in dead:
        del _animations[k]


def update_tmp():
    """Clean up tmp targets whose animations are done."""
    global _tmp_targets
    if not _tmp_targets:
        return
    surviving = []
    for t in _tmp_targets:
        tid = id(t)
        for k in _animations:
            if k[0] == tid:
                surviving.append(t)
                break
    _tmp_targets = surviving


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
    _tmp_targets[:] = []


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
    kept = []
    for target in _tmp_targets:
        if id(target) not in target_ids:
            kept.append(target)
    _tmp_targets[:] = kept
