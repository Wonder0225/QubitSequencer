"""Laboratory wiring transcribed from the supplied plate tables.

Sample pins and drawing identifiers are deliberately not part of a preset.
Unshown ports return None; zero dB remains an explicitly recorded value.
"""

STAGES = ("3 K", "STILL", "50 mK", "MC")


def _atten(stage, value):
    return {"stage": stage, "kind": "衰减记录", "value": value, "unit": "dB", "mark": ""}


def _mark(stage, text):
    return {"stage": stage, "kind": "标记", "value": None, "unit": "", "mark": text}


def preset_for(board, number):
    number = str(number).upper()
    if board in "DEF" and number.startswith("O"):
        number = number[1:]
    if not number.isdigit():
        return None
    n = int(number)
    if board == "A" and 1 <= n <= 41:
        rows = [_atten("3 K", 20), _atten("STILL", 0), _atten("50 mK", 0)]
        if n <= 12:
            labels = (1, 2, 3, 4, 9, 10, 11, 12, 5, 6, 7, 8)
            rows += [_atten("MC", 20), _atten("MC", 20), _mark("MC", "IR"), _mark("MC", f"I{labels[n-1]}")]
        else:
            label = "IR1" if n <= 30 else "IR2" if n in (32, 33) else "IR"
            rows += [_atten("MC", 10), _mark("MC", label)]
        return {"components": rows, "switch": []}
    if board == "B" and (1 <= n <= 20 or 27 <= n <= 39 or n == 44):
        return {"components": [_atten("3 K", 20), _atten("STILL", 0), _atten("50 mK", 0),
                               _atten("MC", 10), _mark("MC", "IR1")], "switch": []}
    if board == "C" and 1 <= n <= 61:
        rows = [_atten("3 K", 20), _atten("STILL", 0 if n in (5, 25, 45) else 10),
                _atten("50 mK", 0), _atten("MC", 0)]
        if n in (37, 38, 39, 40, 41, 42, 46, 47, 48, 49, 54, 55, 56):
            rows.append(_mark("MC", "500M"))
        if 57 <= n <= 60:
            rows += [_atten("MC", 20), _atten("MC", 10)]
        elif n == 61:
            rows.append(_atten("MC", 10))
        ir2 = {1, 2, 3, 5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 16, 17, 20, 21, 22, 24, 25, 26, 35}
        rows.append(_mark("MC", "IR2" if n in ir2 else "IR"))
        return {"components": rows, "switch": []}
    if board in "DEF" and 1 <= n <= 4:
        index = "DEF".index(board) * 4 + n
        rows = [_atten(stage, 0) for stage in STAGES] + [_mark("MC", "IR"), _mark("MC", f"I{index}")]
        return {"components": rows, "switch": [str(i) for i in range(1, 7)] if board == "F" and n == 4 else []}
    return None
