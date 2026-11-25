"""Small, exact CP-SAT encodings reusable by rule authors."""

from ortools.sat.python import cp_model


def positive_part(model, expression, upper, name):
    """Return max(0, expression). The caller supplies a valid upper bound."""
    upper = max(0, upper)
    value = model.new_int_var(0, upper, name)
    model.add_max_equality(value, [0, expression])
    return value


def at_most(model, left, right, name):
    """Boolean equivalent to left <= right for integer-valued expressions."""
    flag = model.new_bool_var(name)
    model.add(left <= right).only_enforce_if(flag)
    model.add(left >= right + 1).only_enforce_if(~flag)
    return flag


def all_of(model, literals, name):
    """Boolean AND with equivalence in both directions. Empty AND is true."""
    literals = list(literals)
    if not literals:
        return 1
    result = model.new_bool_var(name)
    model.add_min_equality(result, literals)
    return result


def any_of(model, literals, name):
    """Boolean OR with equivalence in both directions. Empty OR is false."""
    literals = list(literals)
    if not literals:
        return 0
    result = model.new_bool_var(name)
    model.add_max_equality(result, literals)
    return result


def complement(model, value, name):
    """Return a nonnegative Boolean variable or integer equal to 1 - value."""
    if type(value) is int:
        return 1 - value
    result = model.new_bool_var(name)
    model.add(result + value == 1)
    return result


def covered_by_windows(model, slot, windows, name):
    """True exactly when the full slot is contained in a merged half-open window."""
    starts = [(left, right - slot.duration) for left, right in windows
              if right - left >= slot.duration]
    if not starts:
        return 0
    domain = cp_model.Domain.from_intervals(starts)
    flag = model.new_bool_var(name)
    model.add_linear_expression_in_domain(slot.start, domain).only_enforce_if(flag)
    model.add_linear_expression_in_domain(slot.start, domain.complement()).only_enforce_if(~flag)
    return flag
