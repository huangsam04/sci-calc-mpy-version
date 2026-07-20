"""Compile and evaluate SCI-CALC expressions with a Pratt parser."""
import math
from micropython import const  # type: ignore

from calc.functions import KIND_INFIX, KIND_LIST, KIND_POSTFIX, KIND_PREFIX
from calc.functions import UNARY_CALLBACKS


T_NUM = const(1)
T_NAME = const(2)
T_OP = const(3)
T_LP = const(4)
T_RP = const(5)
T_COMMA = const(6)
T_SEMI = const(7)
T_STR = const(8)
MAX_PARSE_DEPTH = const(30)


class ParseError(ValueError):
    def __init__(self, message, pos=0, expr=""):
        super().__init__(message)
        self.pos = pos
        self.expr = expr


def _token(type_, value, pos):
    return (type_, value, pos)


def tokenize(expr, registry):
    tokens = []
    symbols = registry.symbolic_names()
    i = 0
    length = len(expr)
    while i < length:
        char = expr[i]
        if char.isspace():
            i += 1
            continue
        if char.isdigit() or (char == "." and i + 1 < length and expr[i + 1].isdigit()):
            start = i
            dots = 0
            while i < length and (expr[i].isdigit() or expr[i] == "."):
                if expr[i] == ".":
                    dots += 1
                    if dots > 1:
                        raise ParseError("Invalid number", i, expr)
                i += 1
            if i < length and expr[i] in ("e", "E"):
                exponent = i
                i += 1
                if i < length and expr[i] in ("+", "-"):
                    i += 1
                digit_start = i
                while i < length and expr[i].isdigit():
                    i += 1
                if digit_start == i:
                    i = exponent
            try:
                value = float(expr[start:i])
            except ValueError:
                raise ParseError("Invalid number", start, expr)
            tokens.append(_token(T_NUM, value, start))
            continue
        if char.isalpha() or char == "_":
            start = i
            i += 1
            while i < length and (expr[i].isalnum() or expr[i] == "_"):
                i += 1
            tokens.append(_token(T_NAME, expr[start:i], start))
            continue
        if char in ("'", '"'):
            quote = char
            start = i
            i += 1
            value = ""
            while i < length and expr[i] != quote:
                if expr[i] == "\\" and i + 1 < length:
                    i += 1
                value += expr[i]
                i += 1
            if i >= length:
                raise ParseError("Unterminated string", start, expr)
            i += 1
            tokens.append(_token(T_STR, value, start))
            continue
        structural = {"(": T_LP, ")": T_RP, ",": T_COMMA, ";": T_SEMI}
        if char in structural:
            tokens.append(_token(structural[char], char, i))
            i += 1
            continue
        matched = None
        for symbol in symbols:
            if expr.startswith(symbol, i):
                matched = symbol
                break
        if matched is None:
            raise ParseError("Invalid character: '" + char + "'", i, expr)
        tokens.append(_token(T_OP, matched, i))
        i += len(matched)
    return tokens


class _Compiler:
    def __init__(self, expr, registry):
        self.expr = expr
        self.registry = registry
        self.tokens = tokenize(expr, registry)
        self.pos = 0

    def current(self):
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def compile(self):
        if not self.tokens:
            return ("literal", None, 0)
        statements = [self.parse_expr(0, 0)]
        while self.current() is not None and self.current()[0] == T_SEMI:
            self.pos += 1
            if self.current() is not None:
                statements.append(self.parse_expr(0, 0))
        if self.current() is not None:
            token = self.current()
            raise ParseError("Unexpected '" + str(token[1]) + "'", token[2], self.expr)
        if len(statements) == 1:
            return statements[0]
        return ("sequence", tuple(statements), statements[0][-1])

    def parse_expr(self, min_precedence, depth):
        if depth > MAX_PARSE_DEPTH:
            raise ParseError("Expression too deeply nested", self._position(), self.expr)
        left = self.parse_prefix(depth)
        while True:
            token = self.current()
            if token is None or token[0] in (T_RP, T_COMMA, T_SEMI):
                break
            definition = self.registry.get(token[1])
            if (token[0] not in (T_OP, T_NAME) or definition is None
                    or definition[2] not in (KIND_INFIX, KIND_POSTFIX)):
                if token[0] != T_OP:
                    raise ParseError("Expected operator, got '" + str(token[1]) + "'", token[2], self.expr)
                raise ParseError("Unexpected operator: '" + str(token[1]) + "'", token[2], self.expr)
            precedence = definition[1]
            if precedence < min_precedence:
                break
            self.pos += 1
            if definition[2] == KIND_POSTFIX:
                left = ("postfix", token[1], left, token[2])
                continue
            next_min = precedence + 1 if definition[4] == "left" else precedence
            right = self.parse_expr(next_min, depth + 1)
            if token[1] == "=" and left[0] != "variable":
                raise ParseError("Left side of '=' must be a variable name", token[2], self.expr)
            left = ("infix", token[1], left, right, token[2])
        return left

    def parse_prefix(self, depth):
        token = self.current()
        if token is None:
            raise ParseError("Unexpected end of expression", len(self.expr), self.expr)
        self.pos += 1
        if token[0] == T_NUM or token[0] == T_STR:
            return ("literal", token[1], token[2])
        if token[0] == T_LP:
            value = self.parse_expr(0, depth + 1)
            closing = self.current()
            if closing is None or closing[0] != T_RP:
                raise ParseError("Missing closing ')'", token[2], self.expr)
            self.pos += 1
            return value
        if token[0] == T_OP and token[1] in UNARY_CALLBACKS:
            # Power binds tighter than unary signs, while signs remain legal on
            # the right side of a power expression (2^-2).
            value = self.parse_expr(40, depth + 1)
            return ("unary", token[1], value, token[2])
        if token[0] == T_NAME:
            definition = self.registry.get(token[1])
            if definition is not None and definition[2] == KIND_LIST:
                return self.parse_call(token, definition, depth)
            if definition is not None and definition[2] == KIND_PREFIX:
                if self.current() is not None and self.current()[0] == T_LP:
                    self.pos += 1
                    value = self.parse_expr(0, depth + 1)
                    closing = self.current()
                    if closing is None or closing[0] != T_RP:
                        raise ParseError("Missing closing ')'", token[2], self.expr)
                    self.pos += 1
                else:
                    value = self.parse_expr(definition[1], depth + 1)
                return ("prefix", token[1], value, token[2])
            return ("variable", token[1], token[2])
        raise ParseError("Unexpected '" + str(token[1]) + "'", token[2], self.expr)

    def parse_call(self, token, definition, depth):
        opening = self.current()
        if opening is None or opening[0] != T_LP:
            raise ParseError("'" + token[1] + "' requires parentheses", token[2], self.expr)
        self.pos += 1
        args = []
        if self.current() is not None and self.current()[0] != T_RP:
            while True:
                args.append(self.parse_expr(0, depth + 1))
                current = self.current()
                if current is not None and current[0] == T_COMMA:
                    self.pos += 1
                    if self.current() is None or self.current()[0] == T_RP:
                        raise ParseError("Missing argument after comma", current[2], self.expr)
                    continue
                break
        closing = self.current()
        if closing is None or closing[0] != T_RP:
            raise ParseError("Missing closing ')'", token[2], self.expr)
        self.pos += 1
        if len(args) < definition[3]:
            raise ParseError("'" + token[1] + "' needs at least " + str(definition[3]) + " arguments", token[2], self.expr)
        return ("list", token[1], tuple(args), token[2])

    def _position(self):
        token = self.current()
        return token[2] if token is not None else len(self.expr)


def compile_expression(expr, registry):
    if not isinstance(expr, str):
        raise TypeError("Expression must be a string")
    return _Compiler(expr, registry).compile()


def _evaluate(node, context):
    kind = node[0]
    if kind == "literal":
        return node[1]
    if kind == "sequence":
        value = None
        for statement in node[1]:
            value = _evaluate(statement, context)
        return value
    if kind == "variable":
        name = node[1]
        if name in context.variables:
            return context.variables[name]
        if name == "pi":
            return math.pi
        if name == "e":
            return math.e
        raise ParseError("Undefined variable: '" + name + "'", node[2])
    if kind == "unary":
        return UNARY_CALLBACKS[node[1]](_evaluate(node[2], context), context)
    definition = context.registry.get(node[1])
    if definition is None:
        raise ParseError("Function is no longer loaded: '" + node[1] + "'", node[-1])
    callback = definition[5]
    if kind == "prefix" or kind == "postfix":
        return callback(_evaluate(node[2], context), context)
    if kind == "list":
        args = []
        for child in node[2]:
            args.append(_evaluate(child, context))
        return callback(args, context)
    if kind == "infix":
        if node[1] == "=":
            return callback(node[2][1], _evaluate(node[3], context), context)
        return callback(_evaluate(node[2], context), _evaluate(node[3], context), context)
    raise ParseError("Invalid compiled expression", node[-1])


def evaluate_program(program, context):
    try:
        return _evaluate(program, context)
    except ParseError:
        raise
    except Exception as error:
        raise ParseError(str(error), program[-1] if isinstance(program, tuple) else 0)


def evaluate(expr, context):
    program = compile_expression(expr, context.registry)
    try:
        return evaluate_program(program, context)
    except ParseError as error:
        if not error.expr:
            error.expr = expr
        raise
