"""Finite high-precision decimal arithmetic for SCI-CALC.

MicroPython's ``float`` is intentionally not part of the calculator's core
numeric representation: ESP32 floats overflow to ``inf`` long before an
expression such as ``10^1000`` should stop being useful.  ``Number`` stores a
bounded significant-digit coefficient and a separate base-10 exponent instead.
The coefficient keeps calculation work bounded; the exponent can describe very
large or very small finite values without allocating their decimal expansion.
"""
import math


# Keep the arithmetic precision independent from the user-facing display
# setting.  Thirty significant digits is a practical ceiling for ESP32 heap
# use while still being far above the previous single-precision float path.
WORKING_DIGITS = 30
DEFAULT_DISPLAY_DIGITS = 4
MIN_DISPLAY_DIGITS = 1
MAX_DISPLAY_DIGITS = 12
MAX_ABS_EXPONENT = 999999999
MAX_POWER_INTEGER_DIGITS = 7
MAX_MODULO_SHIFT = 200


def _digit_count(value):
    return len(str(abs(value)))


def _normalise_pair(coefficient, exponent, significant_digits=WORKING_DIGITS):
    """Round a signed coefficient and remove decimal factors of ten."""
    coefficient = int(coefficient)
    exponent = int(exponent)
    if coefficient == 0:
        return 0, 0

    magnitude = abs(coefficient)
    digits = _digit_count(magnitude)
    if digits > significant_digits:
        removed = digits - significant_digits
        divisor = 10 ** removed
        quotient, remainder = divmod(magnitude, divisor)
        if remainder * 2 >= divisor:
            quotient += 1
        coefficient = quotient if coefficient > 0 else -quotient
        exponent += removed
        if _digit_count(quotient) > significant_digits:
            coefficient = (quotient // 10) if coefficient > 0 else -(quotient // 10)
            exponent += 1

    while coefficient % 10 == 0:
        coefficient //= 10
        exponent += 1

    if abs(exponent) > MAX_ABS_EXPONENT:
        raise ValueError("Decimal exponent is too large")
    return coefficient, exponent


class Number:
    """A finite decimal value represented as ``coefficient * 10^exponent``.

    Instances are treated as immutable.  Arithmetic returns a new normalized
    instance and retains at most ``WORKING_DIGITS`` significant digits.
    """

    __slots__ = ("coefficient", "exponent")

    def __init__(self, coefficient=0, exponent=0):
        self.coefficient, self.exponent = _normalise_pair(coefficient, exponent)

    @classmethod
    def from_parts(cls, coefficient, exponent=0):
        return cls(coefficient, exponent)

    @classmethod
    def parse(cls, text):
        """Parse a decimal literal, including an optional ``e`` exponent."""
        if not isinstance(text, str):
            raise TypeError("Number literal must be a string")
        value = text.strip()
        if not value:
            raise ValueError("Invalid number")

        sign = 1
        if value[0] in ("+", "-"):
            if value[0] == "-":
                sign = -1
            value = value[1:]
        if not value:
            raise ValueError("Invalid number")

        marker = -1
        for index, char in enumerate(value):
            if char in ("e", "E"):
                if marker != -1:
                    raise ValueError("Invalid number")
                marker = index
        explicit_exponent = 0
        if marker != -1:
            exponent_text = value[marker + 1:]
            value = value[:marker]
            exponent_sign = 1
            if exponent_text.startswith(("+", "-")):
                if exponent_text[0] == "-":
                    exponent_sign = -1
                exponent_text = exponent_text[1:]
            if not exponent_text or not exponent_text.isdigit():
                raise ValueError("Invalid number")
            trimmed_exponent = exponent_text.lstrip("0") or "0"
            if len(trimmed_exponent) > 9:
                raise ValueError("Decimal exponent is too large")
            explicit_exponent = exponent_sign * int(trimmed_exponent)
            if abs(explicit_exponent) > MAX_ABS_EXPONENT:
                raise ValueError("Decimal exponent is too large")

        if value.count(".") > 1:
            raise ValueError("Invalid number")
        if "." in value:
            whole, fraction = value.split(".")
        else:
            whole, fraction = value, ""
        digits = whole + fraction
        if not digits or not digits.isdigit():
            raise ValueError("Invalid number")
        return cls(sign * int(digits), explicit_exponent - len(fraction))

    @classmethod
    def from_float(cls, value):
        if not math.isfinite(value):
            raise ValueError("Non-finite number")
        return cls.parse(repr(value))

    @property
    def is_zero(self):
        return self.coefficient == 0

    @property
    def sign(self):
        if self.coefficient > 0:
            return 1
        if self.coefficient < 0:
            return -1
        return 0

    @property
    def order(self):
        """Base-10 order of magnitude for nonzero values (zero has order 0)."""
        if self.coefficient == 0:
            return 0
        return self.exponent + _digit_count(self.coefficient) - 1

    def copy(self):
        return Number(self.coefficient, self.exponent)

    def to_literal(self):
        """Return a compact lossless literal accepted by :meth:`parse`."""
        if self.coefficient == 0:
            return "0"
        return str(self.coefficient) + "e" + str(self.exponent)

    def _rounded(self, significant_digits):
        coefficient, exponent = _normalise_pair(
            self.coefficient, self.exponent, significant_digits)
        return Number(coefficient, exponent)

    def to_scientific(self, decimal_places=DEFAULT_DISPLAY_DIGITS):
        """Format as ``x.xxxx*10^x`` with an exact requested decimal count."""
        if (not isinstance(decimal_places, int)
                or decimal_places < 0 or decimal_places > MAX_DISPLAY_DIGITS):
            raise ValueError("Invalid display digit count")
        significant_digits = decimal_places + 1
        if self.coefficient == 0:
            mantissa = "0"
            if decimal_places:
                mantissa += "." + "0" * decimal_places
            return mantissa + "*10^0"

        rounded = self._rounded(significant_digits)
        digits = str(abs(rounded.coefficient))
        scientific_exponent = rounded.exponent + len(digits) - 1
        if len(digits) < significant_digits:
            digits += "0" * (significant_digits - len(digits))
        else:
            digits = digits[:significant_digits]
        mantissa = digits[0]
        if decimal_places:
            mantissa += "." + digits[1:]
        if rounded.coefficient < 0:
            mantissa = "-" + mantissa
        return mantissa + "*10^" + str(scientific_exponent)

    def to_plain(self):
        """Use a short decimal where it is cheap; otherwise use scientific form."""
        if self.coefficient == 0:
            return "0"
        sign = "-" if self.coefficient < 0 else ""
        digits = str(abs(self.coefficient))
        if self.exponent >= 0:
            if len(digits) + self.exponent <= 48:
                return sign + digits + "0" * self.exponent
        else:
            point = len(digits) + self.exponent
            if point > 0:
                return sign + digits[:point] + "." + digits[point:]
            if -point <= 24:
                return sign + "0." + "0" * (-point) + digits
        return self.to_scientific(min(MAX_DISPLAY_DIGITS, len(digits) - 1))

    def __str__(self):
        return self.to_plain()

    def __repr__(self):
        return "Number('" + self.to_literal() + "')"

    def to_float(self):
        """Convert only when a float-only adapter, such as plotting, needs it."""
        if self.coefficient == 0:
            return 0.0
        if self.order > 308:
            raise OverflowError("Number is outside float range")
        if self.order < -324:
            return 0.0
        try:
            value = float(self.coefficient) * (10.0 ** self.exponent)
        except (OverflowError, ValueError):
            raise OverflowError("Number is outside float range")
        if not math.isfinite(value):
            raise OverflowError("Number is outside float range")
        return value

    def __float__(self):
        return self.to_float()

    def is_integer(self):
        return self.coefficient == 0 or self.exponent >= 0

    def integer_value(self, max_digits=None):
        """Return an exact integer or reject values that are not integral."""
        if not self.is_integer():
            raise ValueError("Number is not an integer")
        if self.coefficient == 0:
            return 0
        digits = _digit_count(self.coefficient) + self.exponent
        if max_digits is not None and digits > max_digits:
            raise ValueError("Integer is too large")
        return self.coefficient * (10 ** self.exponent)

    def trunc_int(self, max_digits=MAX_POWER_INTEGER_DIGITS):
        """Truncate toward zero for bounded range-reduction calculations."""
        if self.coefficient == 0:
            return 0
        digits = _digit_count(self.coefficient)
        if self.exponent >= 0:
            total_digits = digits + self.exponent
            if total_digits > max_digits:
                raise ValueError("Integer is too large")
            return self.coefficient * (10 ** self.exponent)
        removed = -self.exponent
        if removed >= digits:
            return 0
        divisor = 10 ** removed
        magnitude = abs(self.coefficient) // divisor
        return magnitude if self.coefficient > 0 else -magnitude

    def _compare(self, other):
        other = coerce(other)
        if self.coefficient < 0 <= other.coefficient:
            return -1
        if other.coefficient < 0 <= self.coefficient:
            return 1
        if self.coefficient == 0 and other.coefficient == 0:
            return 0
        if self.coefficient == 0:
            return -other.sign
        if other.coefficient == 0:
            return self.sign

        left_order = self.order
        right_order = other.order
        if left_order != right_order:
            comparison = -1 if left_order < right_order else 1
            return comparison if self.sign > 0 else -comparison

        common_exponent = min(self.exponent, other.exponent)
        left = abs(self.coefficient) * (10 ** (self.exponent - common_exponent))
        right = abs(other.coefficient) * (10 ** (other.exponent - common_exponent))
        if left == right:
            return 0
        comparison = -1 if left < right else 1
        return comparison if self.sign > 0 else -comparison

    def __eq__(self, other):
        if not isinstance(other, (Number, int, float)):
            return NotImplemented
        try:
            return self._compare(other) == 0
        except (TypeError, ValueError):
            return False

    def __lt__(self, other):
        return self._compare(other) < 0

    def __le__(self, other):
        return self._compare(other) <= 0

    def __gt__(self, other):
        return self._compare(other) > 0

    def __ge__(self, other):
        return self._compare(other) >= 0

    def __bool__(self):
        return self.coefficient != 0

    def __neg__(self):
        return Number(-self.coefficient, self.exponent)

    def __pos__(self):
        return self

    def __abs__(self):
        return Number(abs(self.coefficient), self.exponent)

    def __add__(self, other):
        other = coerce(other)
        if self.coefficient == 0:
            return other.copy()
        if other.coefficient == 0:
            return self.copy()
        # Compare actual decimal orders, not just stored exponents.  A short
        # coefficient can carry a much larger exponent than a 30-digit one
        # while the two values are still close enough to affect the result.
        if self.order >= other.order:
            if self.order - other.order > WORKING_DIGITS + 3:
                return self.copy()
        elif other.order - self.order > WORKING_DIGITS + 3:
            return other.copy()
        common_exponent = min(self.exponent, other.exponent)
        left = self.coefficient * (10 ** (self.exponent - common_exponent))
        right = other.coefficient * (10 ** (other.exponent - common_exponent))
        return Number(left + right, common_exponent)

    def __radd__(self, other):
        return self.__add__(other)

    def __sub__(self, other):
        return self.__add__(-coerce(other))

    def __rsub__(self, other):
        return coerce(other).__sub__(self)

    def __mul__(self, other):
        other = coerce(other)
        return Number(self.coefficient * other.coefficient,
                      self.exponent + other.exponent)

    def __rmul__(self, other):
        return self.__mul__(other)

    def __truediv__(self, other):
        other = coerce(other)
        if other.coefficient == 0:
            raise ZeroDivisionError("Division by zero")
        if self.coefficient == 0:
            return Number()
        guard_digits = WORKING_DIGITS + 3
        # Account for both coefficient lengths.  A fixed multiplier loses
        # digits whenever the divisor carries many more significant digits
        # than the dividend (for example during Newton iteration).
        shift = (guard_digits + _digit_count(other.coefficient)
                 - _digit_count(self.coefficient))
        numerator = abs(self.coefficient) * (10 ** shift)
        quotient, remainder = divmod(numerator, abs(other.coefficient))
        if remainder * 2 >= abs(other.coefficient):
            quotient += 1
        if (self.coefficient < 0) != (other.coefficient < 0):
            quotient = -quotient
        return Number(quotient,
                      self.exponent - other.exponent - shift)

    def __rtruediv__(self, other):
        return coerce(other).__truediv__(self)

    def __mod__(self, other):
        other = coerce(other)
        if other.coefficient == 0:
            raise ZeroDivisionError("Modulo by zero")
        common_exponent = min(self.exponent, other.exponent)
        left_shift = self.exponent - common_exponent
        right_shift = other.exponent - common_exponent
        if max(left_shift, right_shift) > MAX_MODULO_SHIFT:
            raise ValueError("Modulo operands are too far apart")
        left = self.coefficient * (10 ** left_shift)
        right = other.coefficient * (10 ** right_shift)
        return Number(left % right, common_exponent)

    def __rmod__(self, other):
        return coerce(other).__mod__(self)

    def _pow_integer(self, exponent):
        if exponent == 0:
            return Number(1)
        if self.coefficient == 0:
            if exponent < 0:
                raise ZeroDivisionError("Zero cannot have a negative power")
            return Number()
        negative_power = exponent < 0
        exponent = abs(exponent)
        result = Number(1)
        factor = self
        while exponent:
            if exponent & 1:
                result = result * factor
            exponent >>= 1
            if exponent:
                factor = factor * factor
        return Number(1) / result if negative_power else result

    def __pow__(self, other):
        other = coerce(other)
        if other.is_integer():
            return self._pow_integer(
                other.integer_value(MAX_POWER_INTEGER_DIGITS))
        if self.coefficient <= 0:
            raise ValueError("Fractional powers require a positive base")
        return exp(ln(self) * other)

    def __rpow__(self, other):
        return coerce(other).__pow__(self)


def coerce(value):
    """Normalize Python numeric values at the calculator/plugin seam."""
    if isinstance(value, Number):
        return value
    if isinstance(value, bool):
        return Number(1 if value else 0)
    if isinstance(value, int):
        return Number(value)
    if isinstance(value, float):
        return Number.from_float(value)
    raise TypeError("Expected a number")


def format_number(value, decimal_places=DEFAULT_DISPLAY_DIGITS):
    """Format calculator values without leaking a float ``inf`` representation."""
    if isinstance(value, (Number, int, float)):
        return coerce(value).to_scientific(decimal_places)
    return str(value)


def isfinite(value):
    if isinstance(value, Number):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    return False


ZERO = Number(0)
ONE = Number(1)
TWO = Number(2)
TEN = Number(10)
PI = Number.parse("3.141592653589793238462643383279")
E = Number.parse("2.718281828459045235360287471352")
LN2 = Number.parse("0.693147180559945309417232121458")
LN10 = Number.parse("2.302585092994045684017991454684")
SQRT10 = Number.parse("3.162277660168379331998893544433")
HALF_PI = PI / TWO
QUARTER_PI = PI / Number(4)
TWO_PI = PI * TWO


def _is_small(term):
    return term.is_zero or term.order <= -(WORKING_DIGITS + 3)


def sqrt(value):
    value = coerce(value)
    if value < ZERO:
        raise ValueError("Square root requires a non-negative value")
    if value.is_zero:
        return ZERO
    guess = Number(1, value.order // 2)
    for _ in range(WORKING_DIGITS + 4):
        next_guess = (guess + value / guess) / TWO
        if next_guess == guess:
            return next_guess
        if _is_small(next_guess - guess):
            return next_guess
        guess = next_guess
    return guess


def exp(value):
    value = coerce(value)
    if value.is_zero:
        return ONE
    if abs(value).order > MAX_POWER_INTEGER_DIGITS - 1:
        raise ValueError("Exponent is too large for high-precision exp")

    quotient = value / LN2
    steps = quotient.trunc_int(MAX_POWER_INTEGER_DIGITS)
    remainder = value - Number(steps) * LN2
    term = ONE
    total = ONE
    for index in range(1, 160):
        term = term * remainder / Number(index)
        total = total + term
        if _is_small(term):
            break
    return total * Number(2)._pow_integer(steps)


def ln(value):
    value = coerce(value)
    if value <= ZERO:
        raise ValueError("Logarithm requires a positive value")
    if value == ONE:
        return ZERO

    decimal_order = value.order
    mantissa = Number(value.coefficient, value.exponent - decimal_order)
    if mantissa > SQRT10:
        mantissa = mantissa / TEN
        decimal_order += 1
    z = (mantissa - ONE) / (mantissa + ONE)
    z_squared = z * z
    term = z
    total = z
    for index in range(1, 220):
        term = term * z_squared
        contribution = term / Number(2 * index + 1)
        total = total + contribution
        if _is_small(contribution):
            break
    return total * TWO + Number(decimal_order) * LN10


def log10(value):
    return ln(value) / LN10


def _reduce_angle(value):
    value = coerce(value)
    if abs(value).order > MAX_POWER_INTEGER_DIGITS - 1:
        raise ValueError("Angle is too large for high-precision trig")
    angle = value % TWO_PI
    if angle > PI:
        angle = angle - TWO_PI
    return angle


def sin(value):
    angle = _reduce_angle(value)
    term = angle
    total = angle
    square = angle * angle
    for index in range(1, 180):
        term = -term * square / Number((2 * index) * (2 * index + 1))
        total = total + term
        if _is_small(term):
            break
    return total


def cos(value):
    angle = _reduce_angle(value)
    term = ONE
    total = ONE
    square = angle * angle
    for index in range(1, 180):
        term = -term * square / Number((2 * index - 1) * (2 * index))
        total = total + term
        if _is_small(term):
            break
    return total


def tan(value):
    cosine = cos(value)
    if cosine.is_zero or cosine.order <= -(WORKING_DIGITS - 2):
        raise ValueError("Tangent is undefined at this angle")
    return sin(value) / cosine


def _atan_small(value):
    square = value * value
    term = value
    total = value
    for index in range(1, 220):
        term = -term * square
        contribution = term / Number(2 * index + 1)
        total = total + contribution
        if _is_small(contribution):
            break
    return total


def atan(value):
    value = coerce(value)
    if value.is_zero:
        return ZERO
    if value < ZERO:
        return -atan(-value)
    if value > ONE:
        return HALF_PI - _atan_small(ONE / value)
    if value > Number(1) / TWO:
        return QUARTER_PI + _atan_small((value - ONE) / (value + ONE))
    return _atan_small(value)


def asin(value):
    value = coerce(value)
    if value < -ONE or value > ONE:
        raise ValueError("asin requires a value from -1 to 1")
    if value == ONE:
        return HALF_PI
    if value == -ONE:
        return -HALF_PI
    return atan(value / sqrt(ONE - value * value))


def acos(value):
    return HALF_PI - asin(value)


def sinh(value):
    value = coerce(value)
    positive = exp(value)
    negative = exp(-value)
    return (positive - negative) / TWO


def cosh(value):
    value = coerce(value)
    positive = exp(value)
    negative = exp(-value)
    return (positive + negative) / TWO


def tanh(value):
    value = coerce(value)
    positive = exp(value)
    negative = exp(-value)
    return (positive - negative) / (positive + negative)
