# ponytail: recursive descent by precedence, all ops are pluggable functions
"""Expression parser using Pratt parsing (recursive descent + precedence).
Tokenizes with position info, raises descriptive errors on failure."""
import math

# Token types
T_NUM = "NUM"
T_NAME = "NAME"
T_OP = "OP"
T_LP = "LP"
T_RP = "RP"
T_COMMA = "COMMA"
T_SEMI = "SEMI"
T_STR = "STR"


class ParseError(ValueError):
    """Error with position info for displaying in UI."""
    def __init__(self, msg, pos=0, expr=""):
        super().__init__(msg)
        self.pos = pos
        self.expr = expr


def _tok(type_, val, pos):
    """Create a token tuple with position."""
    return (type_, val, pos)


def tokenize(expr_str):
    """Convert expression string to token list. Each token is (type, value, pos)."""
    tokens = []
    i = 0
    n = len(expr_str)
    while i < n:
        c = expr_str[i]
        if c.isspace():
            i += 1
            continue
        if c.isdigit() or (c == '.' and i + 1 < n and expr_str[i + 1].isdigit()):
            start = i
            j = i
            dots = 0
            while j < n and (expr_str[j].isdigit() or expr_str[j] == '.'):
                if expr_str[j] == '.':
                    dots += 1
                    if dots > 1:
                        break
                j += 1
            val = float(expr_str[i:j]) if '.' in expr_str[i:j] else int(expr_str[i:j])
            tokens.append(_tok(T_NUM, val, start))
            i = j
        elif c.isalpha() or c == '_':
            start = i
            j = i
            while j < n and (expr_str[j].isalpha() or expr_str[j].isdigit() or expr_str[j] == '_'):
                j += 1
            tokens.append(_tok(T_NAME, expr_str[i:j], start))
            i = j
        elif c == '(':
            tokens.append(_tok(T_LP, '(', i))
            i += 1
        elif c == ')':
            tokens.append(_tok(T_RP, ')', i))
            i += 1
        elif c == ',':
            tokens.append(_tok(T_COMMA, ',', i))
            i += 1
        elif c == ';':
            tokens.append(_tok(T_SEMI, ';', i))
            i += 1
        elif c == '"' or c == "'":
            # String literal — single or double quoted
            quote = c
            j = i + 1
            while j < n and expr_str[j] != quote:
                j += 1
            if j >= n:
                raise ParseError("Unterminated string", i, expr_str)
            tokens.append(_tok(T_STR, expr_str[i + 1:j], i))
            i = j + 1
        elif c in '+-*/^=':
            tokens.append(_tok(T_OP, c, i))
            i += 1
        else:
            raise ParseError(f"Invalid character: '{c}'", i, expr_str)
    return tokens


def _tok_pos(tokens, pos):
    """Get source position of token at index pos, or end of string."""
    if 0 <= pos < len(tokens):
        return tokens[pos][2]
    return 0


def evaluate(expr_str, vars_dict, func_table):
    """Evaluate an expression string. Returns (result, updated_vars_dict)."""
    if not expr_str or not expr_str.strip():
        return None, vars_dict

    tokens = tokenize(expr_str)
    if not tokens:
        return None, vars_dict

    try:
        pos, result, vars_dict = _parse_toplevel(tokens, 0, vars_dict, func_table, expr_str)
        # If we didn't consume all tokens, something is wrong
        if pos < len(tokens):
            tp = _tok_pos(tokens, pos)
            raise ParseError(f"Unexpected '{tokens[pos][1]}'", tp, expr_str)
        if isinstance(result, VarRef):
            raise ParseError(f"Undefined variable: '{result.name}'", result.pos, expr_str)
        return result, vars_dict
    except ParseError:
        raise
    except Exception as e:
        # Wrap generic errors with position if possible
        raise ParseError(str(e), 0, expr_str)


def _parse_toplevel(tokens, pos, vars_dict, func_table, expr_str):
    """Parse top-level expressions, handling semicolons."""
    pos, val, vars_dict = _parse_expr(tokens, pos, vars_dict, func_table, 0, expr_str)

    while pos < len(tokens) and tokens[pos][0] == T_SEMI:
        pos += 1
        if pos >= len(tokens):
            break
        pos, val, vars_dict = _parse_expr(tokens, pos, vars_dict, func_table, 0, expr_str)

    return pos, val, vars_dict


def _parse_expr(tokens, pos, vars_dict, func_table, min_prec, expr_str):
    """Pratt parser: parse expression with minimum precedence min_prec."""
    pos, left, vars_dict = _parse_prefix(tokens, pos, vars_dict, func_table, expr_str)

    while pos < len(tokens):
        tok = tokens[pos]

        if tok[0] in (T_RP, T_COMMA, T_SEMI):
            break

        if tok[0] != T_OP:
            # Non-operator token after complete expression — unexpected
            tp = tok[2]
            raise ParseError(f"Expected operator, got '{tok[1]}'", tp, expr_str)

        op_char = tok[1]
        op_def = func_table.get(op_char, None)
        if op_def is None:
            tp = tok[2]
            raise ParseError(f"Unknown operator: '{op_char}' (not loaded in function table)", tp, expr_str)

        _, op_prio, op_kind, op_arity, op_assoc, op_func = op_def

        eff_prio = op_prio
        if op_assoc == "right":
            eff_prio -= 0.5

        if eff_prio < min_prec:
            break

        if op_kind == "infix":
            pos += 1
            next_min = op_prio + 1 if op_assoc == "left" else op_prio

            if op_char == "=":
                var_name = None
                if isinstance(left, VarRef):
                    var_name = left.name
                if var_name is None:
                    tp = _tok_pos(tokens, pos - 1)
                    raise ParseError("Left side of '=' must be a variable name", tp, expr_str)
                pos, right, vars_dict = _parse_expr(tokens, pos, vars_dict, func_table, next_min, expr_str)
                left, vars_dict = op_func(var_name, right, vars_dict)
            else:
                pos, right, vars_dict = _parse_expr(tokens, pos, vars_dict, func_table, next_min, expr_str)
                left, vars_dict = op_func(left, right, vars_dict)

        elif op_kind == "prefix":
            pos += 1
            pos, arg, vars_dict = _parse_expr(tokens, pos, vars_dict, func_table, op_prio, expr_str)
            left, vars_dict = op_func(arg, vars_dict)

        elif op_kind == "postfix":
            pos += 1
            left, vars_dict = op_func(left, vars_dict)

        elif op_kind == "list":
            pos += 1
            if pos >= len(tokens) or tokens[pos][0] != T_LP:
                tp = _tok_pos(tokens, pos - 1)
                raise ParseError(f"'{op_char}' requires parentheses with arguments", tp, expr_str)
            pos += 1
            args = []
            while pos < len(tokens) and tokens[pos][0] != T_RP:
                pos, arg_val, vars_dict = _parse_expr(tokens, pos, vars_dict, func_table, 0, expr_str)
                args.append(arg_val)
                if pos < len(tokens) and tokens[pos][0] == T_COMMA:
                    pos += 1
            if pos < len(tokens) and tokens[pos][0] == T_RP:
                pos += 1
            left, vars_dict = op_func(args, vars_dict)

    return pos, left, vars_dict


class VarRef:
    """Reference to a variable by name, with source position."""
    def __init__(self, name, pos=0):
        self.name = name
        self.pos = pos


def _parse_prefix(tokens, pos, vars_dict, func_table, expr_str):
    """Parse a primary expression."""
    if pos >= len(tokens):
        raise ParseError("Unexpected end of expression", len(expr_str), expr_str)

    tok = tokens[pos]

    if tok[0] == T_NUM:
        return pos + 1, tok[1], vars_dict

    if tok[0] == T_STR:
        return pos + 1, tok[1], vars_dict

    if tok[0] == T_NAME:
        name = tok[1]
        name_pos = tok[2]
        op_def = func_table.get(name, None)

        # List function: name(...)
        if op_def and op_def[2] == "list" and pos + 1 < len(tokens) and tokens[pos + 1][0] == T_LP:
            pos += 2
            args = []
            while pos < len(tokens) and tokens[pos][0] != T_RP:
                pos, arg_val, vars_dict = _parse_expr(tokens, pos, vars_dict, func_table, 0, expr_str)
                args.append(arg_val)
                if pos < len(tokens) and tokens[pos][0] == T_COMMA:
                    pos += 1
            if pos < len(tokens) and tokens[pos][0] == T_RP:
                pos += 1
            result, vars_dict = op_def[5](args, vars_dict)
            return pos, result, vars_dict

        # Prefix function: name arg
        if op_def and op_def[2] == "prefix":
            pos += 1
            pos, arg, vars_dict = _parse_expr(tokens, pos, vars_dict, func_table, op_def[1], expr_str)
            result, vars_dict = op_def[5](arg, vars_dict)
            return pos, result, vars_dict

        # Variable reference — peek ahead: if followed by '=', this is a
        # reassignment target, not a value read. Return VarRef so the '='
        # operator can extract the name.
        if name in vars_dict:
            if pos + 1 < len(tokens) and tokens[pos + 1][0] == T_OP and tokens[pos + 1][1] == '=':
                pos += 1
                return pos, VarRef(name, name_pos), vars_dict
            pos += 1
            return pos, vars_dict[name], vars_dict

        # Constants
        if name == "pi":
            pos += 1
            return pos, math.pi, vars_dict
        if name == "e":
            pos += 1
            return pos, math.e, vars_dict

        # Unknown name — might be for assignment, return VarRef
        pos += 1
        return pos, VarRef(name, name_pos), vars_dict

    if tok[0] == T_LP:
        pos += 1
        pos, val, vars_dict = _parse_expr(tokens, pos, vars_dict, func_table, 0, expr_str)
        if pos < len(tokens) and tokens[pos][0] == T_RP:
            pos += 1
        else:
            raise ParseError("Missing closing ')'", _tok_pos(tokens, pos - 1), expr_str)
        return pos, val, vars_dict

    if tok[0] == T_OP:
        op_char = tok[1]
        op_def = func_table.get(op_char, None)
        if op_def and op_def[2] == "prefix":
            pos += 1
            pos, arg, vars_dict = _parse_expr(tokens, pos, vars_dict, func_table, op_def[1], expr_str)
            result, vars_dict = op_def[5](arg, vars_dict)
            return pos, result, vars_dict
        if op_char == '-':
            pos += 1
            pos, right, vars_dict = _parse_expr(tokens, pos, vars_dict, func_table, 4, expr_str)
            sub_def = func_table.get('-')
            if sub_def:
                result, vars_dict = sub_def[5](None, right, vars_dict)
            else:
                result = -right
            return pos, result, vars_dict
        raise ParseError(f"Unexpected operator: '{op_char}'", tok[2], expr_str)

    raise ParseError(f"Unexpected '{tok[1]}'", tok[2], expr_str)
