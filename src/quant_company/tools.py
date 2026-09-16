"""Small deterministic tools. No arbitrary Python, shell or research execution."""

import ast
import operator
from decimal import Decimal, InvalidOperation, localcontext


def calculate(expression: str) -> dict:
    if not isinstance(expression, str) or len(expression) > 240:
        raise ValueError("A short numeric expression is required")
    tree = ast.parse(expression, mode="eval")
    nodes = list(ast.walk(tree))
    if len(nodes) > 80:
        raise ValueError("Expression too complex")
    binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}

    def evaluate(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            value = Decimal(str(node.value))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and type(node.op) in binary:
            value = binary[type(node.op)](evaluate(node.left), evaluate(node.right))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            base, exponent = evaluate(node.left), evaluate(node.right)
            if exponent != int(exponent) or abs(exponent) > 100:
                raise ValueError("Exponent must be an integer between -100 and 100")
            value = base ** int(exponent)
        else:
            raise ValueError("Only numeric arithmetic is allowed")
        if not value.is_finite() or abs(value) > Decimal("1e100"):
            raise ValueError("Numeric result exceeds tool bounds")
        return value

    try:
        with localcontext() as ctx:
            ctx.prec = 28
            value = evaluate(tree.body)
    except (ArithmeticError, InvalidOperation) as exc:
        raise ValueError("Invalid numeric operation") from exc
    return {"expression": expression, "result": str(value), "tool": "decimal-calculator-v1"}
