"""Shared readout buses, qubit branches and filters anchored on the bus."""

import math

from .undo import undoable

READOUT_STATEMENTS = (
    """CREATE TABLE readout_links (
        id TEXT PRIMARY KEY, readout_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
        device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
        position REAL CHECK(position>=0 AND position<=1), notes TEXT NOT NULL DEFAULT '',
        UNIQUE(readout_id,device_id))""",
    """CREATE UNIQUE INDEX filter_readout ON readout_links(device_id) WHERE position IS NOT NULL""",
)
READOUT_SCHEMA = ";\n".join(READOUT_STATEMENTS) + ";\n"


def readout_endpoints(device):
    x, y = device["x"] + device["dx"], device["y"] + device["dy"]
    half = device["readout_length"] / 2
    if device["readout_angle"] is not None:
        angle = math.radians(device["readout_angle"])
        vx, vy = half*math.cos(angle), half*math.sin(angle)
        return ((x-vx, y-vy), (x+vx, y+vy))
    return ((x-half, y), (x+half, y)) if device["readout_direction"] == "horizontal" else ((x, y-half), (x, y+half))


def readout_geometry(start, end):
    vx, vy = end[0]-start[0], end[1]-start[1]
    length = math.hypot(vx, vy)
    if not math.isfinite(length) or length == 0:
        raise ValueError("读取线的起点和终点必须不同")
    return dict(x=(start[0]+end[0])/2, y=(start[1]+end[1])/2,
                readout_length=length, readout_angle=math.degrees(math.atan2(vy, vx)),
                readout_direction="horizontal" if vy == 0 else "vertical" if vx == 0 else "angled")


def readout_route(device, obstacles=()):
    """Keep selected endpoints fixed; preserve legacy automatic parallel lanes."""
    a, b = readout_endpoints(device)
    if device["readout_angle"] is not None:
        return lambda position: (a[0]+(b[0]-a[0])*position, a[1]+(b[1]-a[1])*position)
    horizontal = device["readout_direction"] == "horizontal"
    start, end = (a[0], b[0]) if horizontal else (a[1], b[1])
    baseline = a[1] if horizontal else a[0]
    nearby = []
    for obstacle in obstacles:
        if obstacle["kind"] not in ("qubit", "coupler"):
            continue
        ox, oy = obstacle["x"]+obstacle["dx"], obstacle["y"]+obstacle["dy"]
        along, across = (ox, oy) if horizontal else (oy, ox)
        if start-.5 <= along <= end+.5 and abs(across-baseline) < 1.2:
            nearby.append((along, across, .29 if obstacle["kind"] == "qubit" else .21))
    offset = 0
    if any(abs(across-baseline) < .32 for _, across, _ in nearby):
        def score(shift):
            overlap = sum(max(0, radius-math.hypot(max(start,min(end,along))-along, baseline+shift-across))**2
                          for along, across, radius in nearby)
            return overlap * 1000 + abs(shift) * .001
        offset = min((.30,-.30,.42,-.42,.62,-.62,.82,-.82), key=score)

    def point(position):
        axis = start+(end-start)*position
        across = baseline+offset
        return (axis, across) if horizontal else (across, axis)
    return point


def readout_point(device, position, obstacles=()):
    return readout_route(device, obstacles)(position)


def project_to_readout(device, x, y, obstacles=(), *, route=None):
    a, b = readout_endpoints(device)
    vx, vy = b[0]-a[0], b[1]-a[1]
    position = max(0, min(1, ((x-a[0])*vx + (y-a[1])*vy) / (vx*vx + vy*vy)))
    return position, (route or readout_route(device, obstacles))(position)


class ReadoutStore:
    def _available_filter_position(self, readout, position, ident):
        devices = self.devices()
        point = readout_route(readout, devices)
        length = readout["readout_length"]
        start, end = point(0), point(1)
        ux, uy = (end[0]-start[0])/length, (end[1]-start[1])/length
        forbidden = []
        candidates = {position, 0.0, 1.0}
        for device in devices:
            if device["id"] == ident or device["kind"] not in ("qubit", "coupler", "filter"):
                continue
            x, y = device["x"]+device["dx"], device["y"]+device["dy"]
            vx, vy = x-start[0], y-start[1]
            along, gap = vx*ux+vy*uy, abs(vx*uy-vy*ux)
            clearance = .4 if device["kind"] == "qubit" else .3
            if gap >= clearance:
                continue
            half = math.sqrt(clearance**2-gap**2)
            lower, upper = (along-half)/length, (along+half)/length
            forbidden.append((lower, upper))
            candidates.update((lower-.01/length, upper+.01/length))
        available = [value for value in candidates if 0 <= value <= 1 and
                     not any(lower < value < upper for lower, upper in forbidden)]
        if not available:
            raise ValueError("此读取线没有足够空间放置 filter，请调整线长或位置")
        return min(available, key=lambda value: (abs(value-position), value))

    def readout_links(self, readout_id=None):
        if readout_id is not None:
            return self.db.execute("SELECT * FROM readout_links WHERE readout_id=? ORDER BY rowid", (readout_id,)).fetchall()
        return self.db.execute("SELECT * FROM readout_links ORDER BY rowid").fetchall()

    def readout_link(self, ident):
        row = self.db.execute("SELECT * FROM readout_links WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        return row

    def filter_readout(self, ident):
        return self.db.execute("SELECT * FROM readout_links WHERE device_id=? AND position IS NOT NULL", (ident,)).fetchone()

    @undoable("连接共享读取线")
    def add_readout_link(self, readout_id, device_id, *, position=None):
        from .storage import new_id
        readout, device = self.device(readout_id), self.device(device_id)
        if readout["kind"] != "readout" or device["kind"] not in ("qubit", "filter"):
            raise ValueError("读取线只能关联比特或可调 filter")
        if device["kind"] == "filter":
            if position is None:
                position, _ = project_to_readout(readout, device["x"]+device["dx"], device["y"]+device["dy"], self.devices())
            if not math.isfinite(position) or not 0 <= position <= 1:
                raise ValueError("filter 在线上的相对位置必须在 0–1 之间")
            position = self._available_filter_position(readout, position, device_id)
            existing = self.filter_readout(device_id)
        else:
            position = None
            existing = self.db.execute("SELECT * FROM readout_links WHERE readout_id=? AND device_id=?",
                                       (readout_id, device_id)).fetchone()
        ident = existing["id"] if existing else new_id()
        with self.db:
            if existing:
                self.db.execute("UPDATE readout_links SET readout_id=?,position=? WHERE id=?", (readout_id, position, ident))
            else:
                self.db.execute("INSERT INTO readout_links VALUES (?,?,?,?,?)", (ident, readout_id, device_id, position, ""))
            if position is not None:
                x, y = readout_point(readout, position, self.devices())
                self.db.execute("UPDATE devices SET x=?,y=?,dx=0,dy=0 WHERE id=?", (x, y, device_id))
        return ident

    def _move_readout_filters(self, readout_id):
        readout = self.device(readout_id)
        point = readout_route(readout, self.devices())
        for link in self.readout_links(readout_id):
            if link["position"] is not None:
                x, y = point(link["position"])
                self.db.execute("UPDATE devices SET x=?,y=?,dx=0,dy=0 WHERE id=?", (x, y, link["device_id"]))

    @undoable("修改读取线连接")
    def update_readout_link_notes(self, ident, notes):
        with self.db:
            self.db.execute("UPDATE readout_links SET notes=? WHERE id=?", (notes, ident))

    @undoable("解除读取线关联")
    def delete_readout_link(self, ident):
        with self.db:
            self.db.execute("DELETE FROM readout_links WHERE id=?", (ident,))

    def readout_summary(self, ident):
        device = self.device(ident)
        if device["kind"] == "readout":
            members = [self.device(link["device_id"]) for link in self.readout_links(ident)]
            qubits = "、".join(row["label"] for row in members if row["kind"] == "qubit") or "未关联"
            filters = "、".join(row["label"] for row in members if row["kind"] == "filter") or "未放置"
            return f"关联比特：{qubits}；filter：{filters}"
        lines = self.db.execute("SELECT readout_id FROM readout_links WHERE device_id=?", (ident,)).fetchall()
        if lines or device["kind"] == "filter":
            return "读取线：" + ("、".join(self.device(row[0])["label"] for row in lines) or "未关联")
        return ""
