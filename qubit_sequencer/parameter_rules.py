"""Registration fields for the sample calibration overview."""

VERTEX_FREQUENCY = "qubit.vertex_frequency"
BIASED_FREQUENCY = "qubit.biased_frequency"
COUPLED_CAVITY_FREQUENCY = "qubit.coupled_cavity_frequency"
PI_LENGTH = "qubit.pi_length"
PI_AMPLITUDE = "qubit.pi_amplitude"
PI_PRODUCT = "derived.pi_product"

CALIBRATION_PRESETS = [
    (VERTEX_FREQUENCY, "顶点频率 f_{10}", "qubit", "measured", "number", "GHz"),
    (BIASED_FREQUENCY, "偏置频率 f_{10}", "qubit", "measured", "number", "GHz"),
    ("qubit.iq_visibility", "IQ Visibility", "qubit", "measured", "number", ""),
    ("qubit.snr", "SNR", "qubit", "measured", "number", ""),
    (PI_LENGTH, "π 脉冲长度 piLen", "qubit", "measured", "number", "ns"),
    (PI_AMPLITUDE, "π 脉冲幅度 piAmp", "qubit", "measured", "number", ""),
    ("qubit.ec_measured", "实测 E_{C}/h", "qubit", "measured", "number", "MHz"),
    ("qubit.t1_average", "平均 T_{1}", "qubit", "measured", "number", "µs"),
    ("qubit.t2_star", "Ramsey T_{2}^{*}", "qubit", "measured", "number", "µs"),
    ("qubit.t2_echo", "Spin Echo T_{2}", "qubit", "measured", "number", "µs"),
    ("qubit.cavity_name", "读取腔名称", "qubit", "measured", "text", ""),
    ("qubit.decoupled_cavity_frequency", "解耦腔频", "qubit", "measured", "number", "GHz"),
    (COUPLED_CAVITY_FREQUENCY, "耦合腔频", "qubit", "measured", "number", "GHz"),
    ("qubit.dispersive_shift", "色散位移", "qubit", "measured", "number", "MHz"),
    ("qubit.qubit_cavity_coupling", "腔与比特的耦合强度", "qubit", "measured", "number", "MHz"),
]

_primary_keys = [definition[0] for definition in CALIBRATION_PRESETS]
_primary_keys.insert(_primary_keys.index("qubit.t1_average"), "qubit.t1")
PRIMARY_ORDER = {key: index for index, key in enumerate(_primary_keys)}
BIAS_Z_HEADERS = {BIASED_FREQUENCY: "偏置频率 BiasZ", COUPLED_CAVITY_FREQUENCY: "耦合腔频 BiasZ"}

PARAMETER_HINTS = {
    PI_LENGTH: "piLen 用 ns 登记；piAmp 在独立的幅度字段填写。",
    PI_AMPLITUDE: "填写仪器或控制软件的原始幅度；单位或量程如有需要请在备注注明。",
    "qubit.iq_visibility": "按实验记录的原始数值填写，例如 0.71；软件不自动换算为百分数。",
    "qubit.snr": "按实验记录的原始 SNR 数值填写；如采用 dB，请在备注注明。",
    "qubit.t1_average": "登记实验给出的平均值；软件不对历史 T1 记录自动求平均。",
    "qubit.dispersive_shift": "请在备注注明色散位移的定义，例如 χ 或 2χ；保留测量值的符号。",
}


def is_secondary_parameter(key):
    return key is not None and key.startswith("qubit.") and key not in PRIMARY_ORDER


def uses_bias_z(definition):
    return not definition["is_custom"] and definition["key"] in BIAS_Z_HEADERS


def validate_bias_z(definition, value, bias):
    if uses_bias_z(definition) and value is not None and bias is None:
        raise ValueError("请填写对应的 BiasZ；零偏置填写 0。")


def bias_context(record):
    return record["bias_value"], record["bias_unit"].strip()


def record_value(record):
    if record is None:
        return None
    return record["numeric_value"] if record["numeric_value"] is not None else record["text_value"]


def format_bias_z(record):
    if record is None or record["numeric_value"] is None or record["bias_value"] is None:
        return None
    value, unit = bias_context(record)
    return f"{value} {unit}" if unit else str(value)


def pi_product(length, amplitude):
    if length is None or amplitude is None or bias_context(length) != bias_context(amplitude):
        return None
    if length["source"].strip() != amplitude["source"].strip():
        return None
    time, control = length["numeric_value"], amplitude["numeric_value"]
    return time * control if time is not None and control is not None else None
