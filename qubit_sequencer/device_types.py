"""Device names shared by persistence, editors and both chip views."""

DEVICE_NAMES = {"qubit": "比特", "coupler": "耦合器",
                "readout": "读取线", "filter": "可调 filter"}
DEVICE_PREFIXES = {"qubit": "q", "coupler": "c", "readout": "r", "filter": "f"}
SUBTYPES = {
    "qubit": (("固定 Transmon", "fixed_transmon"), ("可调 Transmon", "tunable_transmon")),
    "coupler": (("固定耦合器", "fixed_coupler"), ("可调耦合器", "tunable_coupler")),
    "readout": (("读取线", "readout_line"),),
    "filter": (("可调 filter", "tunable_filter"),),
}
DEVICE_COLORS = {"qubit": "#2563eb", "coupler": "#187f69",
                 "readout": "#087e8b", "filter": "#9866a9"}
