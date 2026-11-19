"""Component types and formatting shared by the cryogenic circuit views."""

STAGES = ("室温", "50 K", "3 K", "STILL", "50 mK", "MC")
SWITCH_KIND = "微波开关"
SWITCH_DIRECTIONS = {"fan_out": "一路到多路", "fan_in": "多路到一路"}
BIAS_TEE_KIND = "Bias Tee"
BIAS_TEE_TERMINALS = "RF / DC / RF+DC"
BIAS_TEE_INPUTS = (("rf_input_port_id", "RF"), ("dc_input_port_id", "DC"))
IR_MARKS = {"IR", "IR1", "IR2"}
LIBRARY_DEFAULTS = (
    {"kind": "衰减器", "unit": "dB"},
    *({"kind": kind, "unit": "dB"} for kind in ("放大器", "HEMT", "LNA", "TWPA")),
    *({"kind": kind} for kind in ("滤波器", "低通滤波器", "高通滤波器", "IR 滤波器",
                                "隔离器", "环行器", "定向耦合器")),
    {"kind": "终端负载", "unit": "Ω"},
    {"kind": "电阻", "unit": "Ω"},
    {"kind": BIAS_TEE_KIND, "unit": "dB", "terminal": BIAS_TEE_TERMINALS},
    {"kind": SWITCH_KIND},
)


def classify_infrared(component):
    """Treat old infrared labels as filter records while preserving their identifiers."""
    kind = component.get("kind", "")
    mark = component.get("mark") or ""
    if kind in IR_MARKS:
        return dict(component, kind="滤波器", mark=mark or kind)
    if kind == "标记" and mark in IR_MARKS:
        return dict(component, kind="滤波器")
    return component


def classify_circuit_components(circuit):
    circuit = dict(circuit, components=[classify_infrared(row) for row in circuit.get("components", [])])
    if "branch_components" in circuit:
        circuit["branch_components"] = {
            name: [classify_infrared(row) for row in rows]
            for name, rows in circuit["branch_components"].items()
        }
    return circuit


def child_routes(circuit, route):
    if route is None:
        return circuit.get("switch", [])
    children = circuit.get("branch_switches", {}).get(route, {}).get("branches", [])
    return [f"{route}/{child}" for child in children]


def route_parents(circuit):
    """Map each route to its upstream segment, in drawing order."""
    parents = {}

    def visit(route):
        for child in child_routes(circuit, route):
            if child in parents:
                raise ValueError(f"分支路径重复：{child}")
            parents[child] = route
            visit(child)

    visit(None)
    return parents


def terminal_branches(circuit):
    switches = circuit.get("branch_switches", {})
    merges = circuit.get("merge_switches", {})
    joined = {route for node in merges.values() for route in node["inputs"]}
    return [route for route in route_parents(circuit) if route not in switches and route not in joined] + list(merges)


def transfer_destinations(previous, circuit, source):
    parents = route_parents(circuit)
    old_inputs = set(previous.get("merge_switches", {}).get(source, {}).get("inputs", [source]))
    merges = circuit.get("merge_switches", {})
    options = []
    for route in terminal_branches(circuit):
        leaves = merges[route]["inputs"] if route in merges else [route]
        if any(old_inputs.intersection(ancestor_routes(parents, leaf)) for leaf in leaves):
            options.append(route)
    return options


def ancestor_routes(parents, route):
    chain = []
    while route is not None:
        chain.append(route)
        route = parents[route]
    return list(reversed(chain))


def switch_on(circuit, route):
    if route is None:
        return circuit.get("switch_component", {}) if circuit.get("switch") else None
    node = circuit.get("branch_switches", {}).get(route)
    return node["component"] if node else None


def component_summary(component):
    value = component.get("value")
    amount = f"{value:g} {component.get('unit', '')}".strip() if value is not None else ""
    return " · ".join(filter(None, (component.get("mark"), component.get("model"), amount,
                                   component.get("frequency"))))


def component_text(component):
    direction = SWITCH_DIRECTIONS.get(component.get("direction")) if component.get("kind") == SWITCH_KIND else None
    wire = component.get("wire_number")
    wiring = [f"电线编号 {wire}"] if wire else []
    if component.get("kind") == SWITCH_KIND:
        controls = component.get("control_wires", {})
        terminals = [f"{label}={number}" for label, number in controls.items() if label != "Reset" and number]
        if component.get("has_reset"):
            terminals.append(f"Reset={controls.get('Reset') or '未登记'}")
        if terminals:
            wiring.append("控制电线 " + "，".join(terminals))
    return " · ".join(filter(None, (component.get("kind"), direction, component_summary(component),
                                   component.get("terminal"), *wiring, component.get("notes"))))


def bias_tee_input_labels(component, ports):
    if component.get("kind") != BIAS_TEE_KIND or not any(component.get(field) for field, _ in BIAS_TEE_INPUTS):
        return ()
    labels = []
    for field, terminal in BIAS_TEE_INPUTS:
        port_id = component.get(field)
        port = ports.get(port_id)
        label = f"{port['board']}{port['number']}" if port else "端口已删除" if port_id else "未关联"
        labels.append(f"{terminal} {label}")
    return tuple(labels)


def circuit_description(circuit, branch=None):
    def describe(rows):
        return "\n".join(f"{c.get('stage') or '温级未登记'} / {component_text(c)}" for c in rows)

    common = describe(circuit.get("components", []))
    if branch is None:
        return common
    paths = circuit.get("branch_components", {})
    parents = route_parents(circuit)
    merge = circuit.get("merge_switches", {}).get(branch)
    routes = [None]
    for leaf in merge["inputs"] if merge else [branch]:
        for route in ancestor_routes(parents, leaf):
            if route not in routes:
                routes.append(route)
    sections = []
    for route in routes:
        switch = switch_on(circuit, route)
        entries = common if route is None else describe(paths.get(route, []))
        if switch is not None:
            label = f"{switch.get('stage') or '温级未登记'} / {component_text(dict(switch, kind=SWITCH_KIND))}"
            entries = "\n".join(filter(None, (entries, label)))
        sections.append(f"{'共用段' if route is None else f'分支 {route}'}\n{entries or '未登记'}")
    if merge:
        component = merge["component"]
        label = f"{component.get('stage') or '温级未登记'} / {component_text(dict(component, kind=SWITCH_KIND))}"
        sections.append(f"合路开关 · 输入 {'、'.join(merge['inputs'])} → {branch}\n{label}\n"
                        f"{describe(paths.get(branch, [])) or '未登记输出元件'}")
    return "\n".join(sections)
