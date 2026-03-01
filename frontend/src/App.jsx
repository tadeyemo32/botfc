import { useState, useEffect, useRef, useCallback } from "react";

// ── Constants ─────────────────────────────────────────────────────────────────
const SERVER = "http://localhost:5050";
const WS     = "ws://localhost:5050";

const STATE_COLORS = {
  IDLE: "#4d7a60", SEARCH: "#ffcc00", APPROACH: "#a8ff4d",
  ALIGN: "#4d9fff", TACKLE: "#ff6b35", KICK: "#ffffff",
  HALFTIME: "#ff6600", RECOVER: "#ff4d4d", INIT: "#666",
};

const MODE_COLORS = { offense: "#ff4d4d", defense: "#4d9fff", balanced: "#a8ff4d" };
const DIFF_COLORS = { easy: "#a8ff4d", medium: "#ffcc00", hard: "#ff4d4d" };
const MODES = ["offense", "defense", "balanced"];
const DIFFS = ["easy", "medium", "hard"];

// ── Canvas ball-tracking overlay ──────────────────────────────────────────────
function drawOverlay(ctx, w, h, telem) {
  ctx.clearRect(0, 0, w, h);

  // Head-yaw crosshair
  const hYaw = telem.head_yaw || 0;
  const rx = w * 0.5 + hYaw * w * 0.28;
  ctx.strokeStyle = "rgba(77,159,255,0.3)";
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 6]);
  ctx.beginPath(); ctx.moveTo(rx, 0);     ctx.lineTo(rx, h); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(0, h * 0.5); ctx.lineTo(w, h * 0.5); ctx.stroke();
  ctx.setLineDash([]);

  if (!telem.ball_valid) return;

  const bx   = telem.ball_bx  || 0, by  = telem.ball_by  || 0;
  const vx   = telem.ball_vx  || 0, vy  = telem.ball_vy  || 0;
  const pbx  = telem.ball_pred_bx || 0, pby = telem.ball_pred_by || 0;
  const conf = telem.ball_confidence || 0;
  const bsz  = telem.ball_bsz || 0, dist = telem.ball_dist || 0;

  const cx = (0.5 + bx) * w, cy = (0.5 + by) * h;
  const radius = Math.max(10, Math.sqrt(bsz) * w * 0.6);
  const cc = conf > 0.7 ? "#a8ff4d" : conf > 0.4 ? "#ffcc00" : "#ff6b35";

  // Ball circle
  ctx.shadowColor = cc; ctx.shadowBlur = 12;
  ctx.strokeStyle = cc; ctx.lineWidth = 2.5;
  ctx.beginPath(); ctx.arc(cx, cy, radius, 0, Math.PI * 2); ctx.stroke();
  ctx.shadowBlur = 0;

  // Velocity arrow
  const speed = Math.sqrt(vx * vx + vy * vy);
  if (speed > 0.04) {
    const sc = Math.min(w, h) * 0.45;
    const ex = cx + vx * sc, ey = cy + vy * sc;
    const ang = Math.atan2(ey - cy, ex - cx), aL = 9;
    ctx.strokeStyle = "#ffcc00"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(ex, ey); ctx.stroke();
    ctx.fillStyle = "#ffcc00";
    ctx.beginPath();
    ctx.moveTo(ex, ey);
    ctx.lineTo(ex - aL * Math.cos(ang - 0.5), ey - aL * Math.sin(ang - 0.5));
    ctx.lineTo(ex - aL * Math.cos(ang + 0.5), ey - aL * Math.sin(ang + 0.5));
    ctx.closePath(); ctx.fill();
  }

  // Prediction line + dot
  const pcx = (0.5 + pbx) * w, pcy = (0.5 + pby) * h;
  ctx.strokeStyle = "rgba(168,255,77,0.5)"; ctx.lineWidth = 1.5;
  ctx.setLineDash([5, 5]);
  ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(pcx, pcy); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = "rgba(168,255,77,0.9)";
  ctx.strokeStyle = "#a8ff4d"; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.arc(pcx, pcy, 5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();

  // Confidence arc (top-right)
  const [ax, ay, ar] = [w - 28, 28, 20];
  ctx.strokeStyle = "#0a1a10"; ctx.lineWidth = 4;
  ctx.beginPath(); ctx.arc(ax, ay, ar, -Math.PI / 2, Math.PI * 1.5); ctx.stroke();
  ctx.shadowColor = cc; ctx.shadowBlur = 8; ctx.strokeStyle = cc;
  ctx.beginPath(); ctx.arc(ax, ay, ar, -Math.PI / 2, -Math.PI / 2 + conf * Math.PI * 2); ctx.stroke();
  ctx.shadowBlur = 0;
  ctx.fillStyle = cc; ctx.font = "bold 9px 'Share Tech Mono', monospace";
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(Math.round(conf * 100) + "%", ax, ay);

  // Distance label
  ctx.fillStyle = cc; ctx.font = "10px 'Share Tech Mono', monospace";
  ctx.textAlign = "left"; ctx.textBaseline = "top";
  ctx.shadowColor = "#000"; ctx.shadowBlur = 4;
  ctx.fillText("d:" + dist.toFixed(1) + "m", cx + radius + 5, cy - 7);
  ctx.shadowBlur = 0;
}

// ── Camera Feed ───────────────────────────────────────────────────────────────
function CameraFeed({ telemetry }) {
  const [frame,     setFrame]     = useState(null);
  const [connected, setConnected] = useState(false);
  const containerRef = useRef(null);
  const canvasRef    = useRef(null);
  const wsRef        = useRef(null);

  useEffect(() => {
    const connect = () => {
      wsRef.current = new WebSocket(WS + "/api/ws/camera_feed");
      wsRef.current.onopen  = () => setConnected(true);
      wsRef.current.onclose = () => { setConnected(false); setTimeout(connect, 2000); };
      wsRef.current.onmessage = (e) => {
        try { const d = JSON.parse(e.data); setFrame(d.jpg || d.frame || null); } catch (_) {}
      };
    };
    connect();
    return () => wsRef.current?.close();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current, container = containerRef.current;
    if (!canvas || !container) return;
    canvas.width  = container.clientWidth  || 640;
    canvas.height = container.clientHeight || 480;
    drawOverlay(canvas.getContext("2d"), canvas.width, canvas.height, telemetry);
  }, [telemetry, frame]);

  return (
    <div style={{ position: "relative", width: "100%", aspectRatio: "4/3", background: "#000", borderRadius: 4, overflow: "hidden" }} ref={containerRef}>
      {frame
        ? <img src={`data:image/jpeg;base64,${frame}`} alt="bot pov" style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }} />
        : <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 8 }}>
            <div style={{ width: 40, height: 40, border: "2px solid #1a3a2a", borderTopColor: "#4d9fff", borderRadius: "50%", animation: "spin 1s linear infinite" }} />
            <div style={{ color: "#1a3a2a", fontSize: "0.65rem", letterSpacing: "0.2em", fontFamily: "'Share Tech Mono', monospace" }}>AWAITING STREAM</div>
          </div>
      }
      <canvas ref={canvasRef} style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }} />
      {/* Status badge */}
      <div style={{ position: "absolute", top: 8, left: 8, display: "flex", alignItems: "center", gap: 5, background: "rgba(0,0,0,0.7)", padding: "3px 8px", borderRadius: 3 }}>
        <div style={{ width: 6, height: 6, borderRadius: "50%", background: connected ? "#4d9fff" : "#333", boxShadow: connected ? "0 0 8px #4d9fff" : "none" }} />
        <span style={{ color: connected ? "#4d9fff" : "#444", fontSize: "0.5rem", letterSpacing: "0.2em", fontFamily: "'Share Tech Mono', monospace" }}>{connected ? "LIVE" : "OFFLINE"}</span>
      </div>
    </div>
  );
}

// ── Field Diagram ─────────────────────────────────────────────────────────────
function FieldDiagram({ telem }) {
  const W = 120, H = 72;
  const bx = telem.ball_bx || 0, by = telem.ball_by || 0;
  const pbx = telem.ball_pred_bx || 0, pby = telem.ball_pred_by || 0;
  const dotX = Math.round((0.5 + bx) * W), dotY = Math.round((0.5 + by) * H);
  const pdX  = Math.round((0.5 + pbx) * W), pdY = Math.round((0.5 + pby) * H);

  return (
    <div style={{ position: "relative", width: W, height: H, background: "#081208", border: "1px solid #0d2010", borderRadius: 3, flexShrink: 0 }}>
      <div style={{ position: "absolute", inset: "3px 3px", border: "1px solid #0d2010" }} />
      <div style={{ position: "absolute", top: 3, bottom: 3, left: "50%", width: 1, background: "#0d2010" }} />
      <div style={{ position: "absolute", top: "50%", left: "50%", transform: "translate(-50%,-50%)", width: 18, height: 18, borderRadius: "50%", border: "1px solid #0d2010" }} />
      {telem.ball_valid && <>
        <div style={{ position: "absolute", left: pdX - 3, top: pdY - 3, width: 6, height: 6, borderRadius: "50%", background: "rgba(168,255,77,0.5)", border: "1px solid #a8ff4d" }} />
        <div style={{ position: "absolute", left: dotX - 5, top: dotY - 5, width: 10, height: 10, borderRadius: "50%", background: "#ff4d4d", boxShadow: "0 0 8px #ff4d4d" }} />
      </>}
      {!telem.ball_valid && <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: "#0d2010", fontSize: "0.4rem", letterSpacing: "0.1em", fontFamily: "'Share Tech Mono', monospace" }}>NO BALL</div>}
    </div>
  );
}

// ── Stat Row ──────────────────────────────────────────────────────────────────
const Stat = ({ label, value, color }) => (
  <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.6rem", padding: "2px 0" }}>
    <span style={{ color: "#3d6a50", letterSpacing: "0.08em" }}>{label}</span>
    <span style={{ color: color || "#ccc", fontWeight: 600, fontFamily: "'Share Tech Mono', monospace" }}>{value}</span>
  </div>
);

// ── Telemetry Panel ───────────────────────────────────────────────────────────
function TelemetryPanel() {
  const [telem, setTelem]       = useState({
    state: "IDLE", kicks: 0, ball_age: -1, break_remaining: 0,
    battery_pct: -1, robot_connected: false,
    ball_valid: false, ball_bx: 0, ball_by: 0, ball_bsz: 0,
    ball_dist: 0, ball_vx: 0, ball_vy: 0, ball_pred_bx: 0, ball_pred_by: 0,
    ball_confidence: 0, head_yaw: 0, inertial_roll: 0, inertial_pitch: 0,
    cpp_action: "IDLE",
  });
  const [connected, setConn]    = useState(false);
  const [robotIp,   setRobotIp] = useState("");
  const [testing,   setTesting] = useState(false);
  const wsRef = useRef(null);

  useEffect(() => {
    fetch(SERVER + "/api/robot/config").then(r => r.json()).then(d => setRobotIp(d.ip || "")).catch(() => {});
    const connect = () => {
      wsRef.current = new WebSocket(WS + "/api/ws/frontend");
      wsRef.current.onopen  = () => setConn(true);
      wsRef.current.onclose = () => { setConn(false); setTimeout(connect, 2000); };
      wsRef.current.onmessage = (e) => { try { setTelem(p => ({ ...p, ...JSON.parse(e.data) })); } catch (_) {} };
    };
    connect();
    return () => wsRef.current?.close();
  }, []);

  const handlePing = async () => {
    setTesting(true);
    try {
      await fetch(SERVER + "/api/robot/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ip: robotIp }) });
      await fetch(SERVER + "/api/robot/test", { method: "POST" });
    } catch (_) {}
    setTimeout(() => setTesting(false), 2500);
  };

  const sendCommand = async (action) => {
    try { await fetch(SERVER + "/api/command", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) }); } catch (_) {}
  };

  const sc = STATE_COLORS[telem.state] || "#666";
  const cppSc = STATE_COLORS[telem.cpp_action] || "#4d9fff";
  const pct = telem.battery_pct;
  const batC = pct < 0 ? "#1a3a2a" : pct <= 20 ? "#ff4d4d" : pct <= 40 ? "#ffcc00" : "#a8ff4d";
  const conf = telem.ball_confidence;
  const confC = conf > 0.7 ? "#a8ff4d" : conf > 0.4 ? "#ffcc00" : "#ff6b35";

  return (
    <div style={{
      display: "flex", flexDirection: "column", gap: 10,
      fontFamily: "'Share Tech Mono', monospace", color: "#ccc",
    }}>
      {/* Camera hero */}
      <CameraFeed telemetry={telem} />

      {/* Robot link */}
      <div style={{ padding: "8px 10px", background: "#060f08", border: "1px solid #0d2010", borderRadius: 4 }}>
        <div style={{ fontSize: "0.5rem", color: "#3d6a50", letterSpacing: "0.15em", marginBottom: 6, display: "flex", justifyContent: "space-between" }}>
          <span>ROBOT LINK</span>
          <span style={{ color: telem.robot_connected ? "#a8ff4d" : "#ff4d4d" }}>{telem.robot_connected ? "● CONNECTED" : "○ DISCONNECTED"}</span>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <input value={robotIp} onChange={e => setRobotIp(e.target.value)} placeholder="192.168.x.x"
            style={{ flex: 1, background: "#000", border: "1px solid #0d2010", color: "#a8ff4d", fontSize: "0.7rem", padding: "4px 8px", fontFamily: "'Share Tech Mono', monospace", outline: "none", borderRadius: 3 }} />
          <button onClick={handlePing} disabled={testing}
            style={{ background: "#0d2010", color: testing ? "#3d6a50" : "#a8ff4d", border: "1px solid #1a4a20", padding: "4px 12px", fontSize: "0.6rem", cursor: "pointer", fontFamily: "'Share Tech Mono', monospace", borderRadius: 3, letterSpacing: "0.1em" }}>
            {testing ? "..." : "PING"}
          </button>
        </div>
      </div>

      {/* State + Kicks + Ball age */}
      <div style={{ display: "flex", gap: 8 }}>
        <div style={{ flex: 2, padding: "8px 10px", background: `${sc}10`, border: `1px solid ${sc}30`, borderRadius: 4 }}>
          <div style={{ fontSize: "0.45rem", color: "#3d6a50", letterSpacing: "0.15em", marginBottom: 3 }}>BOT STATE</div>
          <div style={{ fontSize: "1rem", fontWeight: 700, color: sc, letterSpacing: "0.05em" }}>{telem.state}</div>
        </div>
        <div style={{ flex: 1, padding: "8px 10px", background: "#060f08", border: "1px solid #0d2010", borderRadius: 4 }}>
          <div style={{ fontSize: "0.45rem", color: "#3d6a50", letterSpacing: "0.15em", marginBottom: 3 }}>KICKS</div>
          <div style={{ fontSize: "1rem", fontWeight: 700, color: "#fff" }}>{telem.kicks}</div>
        </div>
        <div style={{ flex: 1, padding: "8px 10px", background: "#060f08", border: "1px solid #0d2010", borderRadius: 4 }}>
          <div style={{ fontSize: "0.45rem", color: "#3d6a50", letterSpacing: "0.15em", marginBottom: 3 }}>BALL</div>
          <div style={{ fontSize: "1rem", fontWeight: 700, color: telem.ball_age > 8 || telem.ball_age < 0 ? "#ff4d4d" : "#a8ff4d" }}>
            {telem.ball_age < 0 ? "—" : telem.ball_age.toFixed(1) + "s"}
          </div>
        </div>
      </div>

      {/* C++ AI Decision */}
      <div style={{ padding: "8px 10px", background: `${cppSc}08`, border: `1px solid ${cppSc}25`, borderRadius: 4 }}>
        <div style={{ fontSize: "0.45rem", color: "#3d6a50", letterSpacing: "0.15em", marginBottom: 4 }}>LOCAL AI DECISION</div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ width: 8, height: 8, borderRadius: "50%", background: cppSc, boxShadow: `0 0 8px ${cppSc}` }} />
          <div style={{ fontSize: "0.9rem", fontWeight: 700, color: cppSc, letterSpacing: "0.1em" }}>{telem.cpp_action || "IDLE"}</div>
          <div style={{ flex: 1, height: 2, background: `${cppSc}30`, borderRadius: 1 }}>
            <div style={{ height: "100%", width: telem.ball_confidence ? `${telem.ball_confidence * 100}%` : "0%", background: cppSc, transition: "width 0.3s" }} />
          </div>
        </div>
      </div>

      {/* Ball tracker */}
      <div style={{ padding: "8px 10px", background: telem.ball_valid ? "rgba(168,255,77,0.04)" : "#060f08", border: `1px solid ${telem.ball_valid ? "#a8ff4d25" : "#0d2010"}`, borderRadius: 4 }}>
        <div style={{ fontSize: "0.45rem", color: "#3d6a50", letterSpacing: "0.15em", marginBottom: 6, display: "flex", justifyContent: "space-between" }}>
          <span>BALL TRACK</span>
          <span style={{ color: telem.ball_valid ? "#a8ff4d" : "#ff4d4d" }}>{telem.ball_valid ? "ACQUIRED" : "SEARCHING"}</span>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "flex-start", marginBottom: 6 }}>
          <FieldDiagram telem={telem} />
          <div style={{ flex: 1 }}>
            <Stat label="BX"   value={telem.ball_bx.toFixed(3)}     color={telem.ball_valid ? "#ccc" : "#333"} />
            <Stat label="BY"   value={telem.ball_by.toFixed(3)}     color={telem.ball_valid ? "#ccc" : "#333"} />
            <Stat label="DIST" value={telem.ball_dist.toFixed(2)+"m"} color="#4d9fff" />
            <Stat label="VX"   value={telem.ball_vx.toFixed(3)}     color="#ffcc00" />
            <Stat label="VY"   value={telem.ball_vy.toFixed(3)}     color="#ffcc00" />
          </div>
        </div>
        <div style={{ fontSize: "0.45rem", color: "#3d6a50", letterSpacing: "0.1em", marginBottom: 3, display: "flex", justifyContent: "space-between" }}>
          <span>CONFIDENCE</span><span style={{ color: confC }}>{Math.round(conf * 100)}%</span>
        </div>
        <div style={{ height: 4, background: "#060f08", borderRadius: 2, overflow: "hidden" }}>
          <div style={{ height: "100%", width: `${conf * 100}%`, background: confC, transition: "width 0.3s, background 0.3s", boxShadow: `0 0 4px ${confC}80` }} />
        </div>
      </div>

      {/* Inertial */}
      <div style={{ padding: "8px 10px", background: "#060f08", border: "1px solid #0d2010", borderRadius: 4 }}>
        <div style={{ fontSize: "0.45rem", color: "#3d6a50", letterSpacing: "0.15em", marginBottom: 4 }}>INERTIAL / POSE</div>
        <Stat label="ROLL"     value={telem.inertial_roll.toFixed(3)+" rad"}  color={Math.abs(telem.inertial_roll) > 0.3 ? "#ff4d4d" : "#ccc"} />
        <Stat label="PITCH"    value={telem.inertial_pitch.toFixed(3)+" rad"} color={Math.abs(telem.inertial_pitch) > 0.3 ? "#ff4d4d" : "#ccc"} />
        <Stat label="HEAD YAW" value={telem.head_yaw.toFixed(3)+" rad"}      color="#4d9fff" />
      </div>

      {/* Battery */}
      <div>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.5rem", color: "#3d6a50", marginBottom: 3 }}>
          <span>BATTERY</span><span style={{ color: batC }}>{pct < 0 ? "N/A" : pct + "%"}</span>
        </div>
        <div style={{ height: 5, background: "#060f08", borderRadius: 3, overflow: "hidden" }}>
          <div style={{ height: "100%", width: `${Math.max(0, pct)}%`, background: batC, transition: "width 1s, background 0.5s", boxShadow: pct > 0 ? `0 0 5px ${batC}80` : "none" }} />
        </div>
      </div>

      {/* WS status */}
      <div style={{ fontSize: "0.45rem", color: connected ? "#3d6a50" : "#ff4d4d33", letterSpacing: "0.15em", textAlign: "right" }}>
        {connected ? "● SERVER ONLINE" : "○ SERVER OFFLINE"}
      </div>

      {/* Manual control */}
      <div style={{ padding: "8px 10px", background: "#060f08", border: "1px solid #0d2010", borderRadius: 4 }}>
        <div style={{ fontSize: "0.45rem", color: "#3d6a50", letterSpacing: "0.15em", marginBottom: 6 }}>MANUAL OVERRIDE</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 4 }}>
          {[["SEARCH","#ffcc00"],["APPROACH","#a8ff4d"],["ALIGN","#4d9fff"],["KICK","#fff"],["STOP","#ff4d4d"],["KICK_NOW","#ff6b35"]].map(([a,c]) => (
            <button key={a} onClick={() => sendCommand(a)} style={{
              background: "#000", border: `1px solid ${c}40`, color: c, padding: "5px 4px",
              fontSize: "0.5rem", letterSpacing: "0.08em", cursor: "pointer",
              fontFamily: "'Share Tech Mono', monospace", borderRadius: 3, transition: "all 0.15s",
            }}>{a}</button>
          ))}
        </div>
      </div>

      {/* Halftime */}
      {telem.break_remaining > 0 && (
        <div style={{ padding: "8px", background: "#ff660015", border: "1px solid #ff6600", borderRadius: 4, textAlign: "center" }}>
          <div style={{ fontSize: "0.5rem", color: "#ff6600", letterSpacing: "0.2em" }}>COOLING DOWN</div>
          <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "#ff6600", marginTop: 2 }}>{telem.break_remaining}s</div>
        </div>
      )}
    </div>
  );
}

// ── Match Timer ───────────────────────────────────────────────────────────────
function MatchTimer({ running }) {
  const [secs, setSecs] = useState(0);
  const ref = useRef(null);

  useEffect(() => {
    if (running) {
      setSecs(0);
      ref.current = setInterval(() => setSecs(s => s + 1), 1000);
    } else {
      clearInterval(ref.current);
    }
    return () => clearInterval(ref.current);
  }, [running]);

  const mm = String(Math.floor(secs / 60)).padStart(2, "0");
  const ss = String(secs % 60).padStart(2, "0");
  return <span style={{ fontFamily: "'Share Tech Mono', monospace", letterSpacing: "0.15em", color: running ? "#a8ff4d" : "#333" }}>{mm}:{ss}</span>;
}

// ── Player Card ───────────────────────────────────────────────────────────────
function PlayerCard({ number, player, setPlayer, disabled }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(player.name);
  const mc = MODE_COLORS[player.mode];

  return (
    <div style={{
      background: "#050d07", border: `1px solid ${mc}30`,
      borderTop: `3px solid ${mc}`, padding: "1.5rem", flex: 1, minWidth: 240,
      fontFamily: "'Barlow Condensed', sans-serif",
      clipPath: "polygon(0 0, calc(100% - 16px) 0, 100% 16px, 100% 100%, 0 100%)",
      opacity: disabled ? 0.5 : 1, transition: "all 0.3s",
    }}>
      <div style={{ position: "absolute", top: 0, right: 0, width: 0, height: 0, borderTop: `16px solid ${mc}`, borderLeft: "16px solid transparent" }} />
      <div style={{ fontSize: "0.6rem", letterSpacing: "0.25em", color: "#3d6a50", marginBottom: "0.4rem" }}>P{number} · NAO UNIT</div>

      {/* Name */}
      <div style={{ marginBottom: "1.5rem" }}>
        {editing
          ? <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input autoFocus value={draft}
                onChange={e => setDraft(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") { setPlayer({ ...player, name: draft || `Player ${number}` }); setEditing(false); } if (e.key === "Escape") { setDraft(player.name); setEditing(false); } }}
                style={{ background: "transparent", border: "none", borderBottom: `2px solid ${mc}`, color: "#fff", fontSize: "1.6rem", fontWeight: 700, fontFamily: "'Barlow Condensed', sans-serif", outline: "none", width: "100%", textTransform: "uppercase" }} />
              <button onClick={() => { setPlayer({ ...player, name: draft || `Player ${number}` }); setEditing(false); }}
                style={{ background: mc, border: "none", color: "#000", fontWeight: 700, padding: "2px 8px", cursor: "pointer", fontSize: "0.7rem", borderRadius: 2 }}>SET</button>
            </div>
          : <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={{ fontSize: "1.6rem", fontWeight: 700, color: "#fff", textTransform: "uppercase" }}>{player.name}</div>
              {!disabled && <button onClick={() => { setDraft(player.name); setEditing(true); }}
                style={{ background: "transparent", border: "1px solid #3d6a50", color: "#3d6a50", padding: "1px 8px", cursor: "pointer", fontSize: "0.55rem", letterSpacing: "0.1em", borderRadius: 2, fontFamily: "'Barlow Condensed', sans-serif" }}>EDIT</button>}
            </div>
        }
      </div>

      {/* Mode */}
      <div style={{ marginBottom: "1.2rem" }}>
        <div style={{ fontSize: "0.55rem", color: "#3d6a50", letterSpacing: "0.2em", marginBottom: 6 }}>PLAY STYLE</div>
        <div style={{ display: "flex", gap: 4 }}>
          {MODES.map(m => <button key={m} onClick={() => !disabled && setPlayer({ ...player, mode: m })} style={{ flex: 1, background: player.mode === m ? `${MODE_COLORS[m]}15` : "transparent", border: `1px solid ${player.mode === m ? MODE_COLORS[m] : "#0d2010"}`, color: player.mode === m ? MODE_COLORS[m] : "#3d6a50", padding: "5px 2px", cursor: disabled ? "default" : "pointer", fontFamily: "'Barlow Condensed', sans-serif", fontSize: "0.7rem", letterSpacing: "0.1em", textTransform: "uppercase", transition: "all 0.15s", borderRadius: 2 }}>{m}</button>)}
        </div>
      </div>

      {/* Difficulty */}
      <div>
        <div style={{ fontSize: "0.55rem", color: "#3d6a50", letterSpacing: "0.2em", marginBottom: 6 }}>DIFFICULTY</div>
        <div style={{ display: "flex", gap: 4 }}>
          {DIFFS.map(d => <button key={d} onClick={() => !disabled && setPlayer({ ...player, difficulty: d })} style={{ flex: 1, background: player.difficulty === d ? `${DIFF_COLORS[d]}12` : "transparent", border: `1px solid ${player.difficulty === d ? DIFF_COLORS[d] : "#0d2010"}`, color: player.difficulty === d ? DIFF_COLORS[d] : "#3d6a50", padding: "5px 2px", cursor: disabled ? "default" : "pointer", fontFamily: "'Barlow Condensed', sans-serif", fontSize: "0.7rem", letterSpacing: "0.1em", textTransform: "uppercase", transition: "all 0.15s", borderRadius: 2 }}>{d}</button>)}
        </div>
      </div>
    </div>
  );
}

// ── App Root ──────────────────────────────────────────────────────────────────
export default function App() {
  const [p1, setP1]           = useState({ name: "ATLAS", mode: "offense",  difficulty: "medium" });
  const [p2, setP2]           = useState({ name: "ARES",  mode: "defense",  difficulty: "medium" });
  const [matchState, setMState] = useState("config"); // config | deploying | live | error
  const [deployMsg, setDeployMsg] = useState("");
  const [error, setError]       = useState("");

  const deploy = async (key) => {
    setMState("deploying"); setDeployMsg("Connecting to robot..."); setError("");
    try {
      const payload = {};
      if (key === "P1" || key === "BOTH") payload.player1 = p1;
      if (key === "P2" || key === "BOTH") payload.player2 = p2;
      const res = await fetch(SERVER + "/api/start_match", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (!res.ok) throw new Error("Server " + res.status);
      setDeployMsg("Match deployed successfully");
      setMState("live");
    } catch (e) {
      setError(e.message); setMState("error");
    }
  };

  const stop = async () => {
    try { await fetch(SERVER + "/api/stop_match", { method: "POST" }); } catch (_) {}
    setMState("config"); setDeployMsg("");
  };

  const stateColor = matchState === "live" ? "#a8ff4d" : matchState === "deploying" ? "#ffcc00" : matchState === "error" ? "#ff4d4d" : "#4d9fff";

  return (
    <div style={{ minHeight: "100vh", background: "#020e05", color: "#ccc", fontFamily: "'Barlow Condensed', sans-serif" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@300;400;500;600;700;900&family=Share+Tech+Mono&display=swap');
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        button { transition: filter 0.15s, transform 0.1s; }
        button:not(:disabled):hover { filter: brightness(1.2); }
        button:not(:disabled):active { transform: scale(0.97); }
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes fadeUp { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
        @keyframes livePulse { 0%,100% { box-shadow: 0 0 0 rgba(168,255,77,0); } 50% { box-shadow: 0 0 30px rgba(168,255,77,0.15); } }
        @keyframes scan { 0% { transform: translateY(-100%); } 100% { transform: translateY(100vh); } }
        ::-webkit-scrollbar { width: 4px; } ::-webkit-scrollbar-track { background: #010a03; } ::-webkit-scrollbar-thumb { background: #0d2010; }
      `}</style>

      {/* Scanline */}
      <div style={{ position: "fixed", top: 0, left: 0, right: 0, height: 2, background: "linear-gradient(transparent, rgba(0,255,80,0.05), transparent)", animation: "scan 7s linear infinite", pointerEvents: "none", zIndex: 9999 }} />

      {/* ── Top bar ── */}
      <div style={{ borderBottom: "1px solid #0d2010", padding: "0 2rem", height: 52, display: "flex", alignItems: "center", justifyContent: "space-between", background: "rgba(2,14,5,0.95)", backdropFilter: "blur(8px)", position: "sticky", top: 0, zIndex: 100 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div>
            <div style={{ fontSize: "0.5rem", color: "#3d6a50", letterSpacing: "0.4em", fontFamily: "'Share Tech Mono', monospace" }}>NAO ROBOTICS</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 900, color: "#fff", letterSpacing: "0.05em", lineHeight: 1 }}>BOT<span style={{ color: "#a8ff4d" }}>FC</span></div>
          </div>
          <div style={{ width: 1, height: 30, background: "#0d2010" }} />
          <div>
            <div style={{ fontSize: "0.45rem", color: "#3d6a50", letterSpacing: "0.2em", fontFamily: "'Share Tech Mono', monospace" }}>STATUS</div>
            <div style={{ fontSize: "0.75rem", fontWeight: 700, color: stateColor, letterSpacing: "0.1em", fontFamily: "'Share Tech Mono', monospace" }}>
              {matchState === "live" ? "● MATCH LIVE" : matchState === "deploying" ? "◌ DEPLOYING" : matchState === "error" ? "✕ ERROR" : "○ STANDBY"}
            </div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
          <div style={{ textAlign: "right" }}>
            <div style={{ fontSize: "0.45rem", color: "#3d6a50", letterSpacing: "0.2em", fontFamily: "'Share Tech Mono', monospace" }}>MATCH TIME</div>
            <div style={{ fontSize: "1rem", fontWeight: 700 }}><MatchTimer running={matchState === "live"} /></div>
          </div>
          <div style={{ fontSize: "0.5rem", color: "#3d6a50", letterSpacing: "0.2em", fontFamily: "'Share Tech Mono', monospace" }}>
            FOOTBALL AI v2.1 · 2026
          </div>
        </div>
      </div>

      {/* ── Main layout ── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: 0, minHeight: "calc(100vh - 52px)" }}>

        {/* ── Left: config / live ── */}
        <div style={{ padding: "1.5rem", display: "flex", flexDirection: "column", gap: 16, borderRight: "1px solid #0d2010" }}>

          {/* Header */}
          <div style={{ animation: "fadeUp 0.5s ease" }}>
            <h1 style={{ fontSize: "clamp(2rem, 4vw, 3.5rem)", fontWeight: 900, color: "#fff", lineHeight: 0.9, letterSpacing: "0.04em", textTransform: "uppercase" }}>
              MATCH{" "}
              <span style={{ color: stateColor, transition: "color 0.5s" }}>
                {matchState === "live" ? "LIVE" : matchState === "deploying" ? "DEPLOY" : "CONFIG"}
              </span>
            </h1>
            <div style={{ width: 60, height: 2, background: `linear-gradient(90deg, ${stateColor}, transparent)`, margin: "8px 0 4px" }} />
            <div style={{ fontSize: "0.7rem", color: "#3d6a50", letterSpacing: "0.2em", fontFamily: "'Share Tech Mono', monospace" }}>
              {matchState === "live" ? "ROBOT ACTIVE · C++ AI RUNNING LOCALLY" : "SELECT PLAYER SETTINGS · KICK OFF TO DEPLOY"}
            </div>
          </div>

          {/* Player cards */}
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", animation: "fadeUp 0.5s ease 0.1s both" }}>
            <div style={{ flex: 1, minWidth: 240, position: "relative" }}>
              <PlayerCard number={1} player={p1} setPlayer={setP1} disabled={matchState === "live"} />
            </div>

            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 8, padding: "0 8px" }}>
              <div style={{ width: 1, flex: 1, background: "linear-gradient(transparent, #0d2010, transparent)" }} />
              <div style={{ fontSize: "1rem", fontWeight: 900, color: matchState === "live" ? "#a8ff4d" : "#0d2010", border: `1px solid ${matchState === "live" ? "#a8ff4d30" : "#0d2010"}`, padding: "4px 8px", letterSpacing: "0.1em", transition: "all 0.4s", fontFamily: "'Barlow Condensed', sans-serif" }}>VS</div>
              <div style={{ width: 1, flex: 1, background: "linear-gradient(transparent, #0d2010, transparent)" }} />
            </div>

            <div style={{ flex: 1, minWidth: 240, position: "relative" }}>
              <PlayerCard number={2} player={p2} setPlayer={setP2} disabled={matchState === "live"} />
            </div>
          </div>

          {/* Error */}
          {matchState === "error" && (
            <div style={{ padding: "12px 16px", background: "#ff4d4d10", border: "1px solid #ff4d4d40", borderRadius: 4, display: "flex", justifyContent: "space-between", alignItems: "center", fontFamily: "'Share Tech Mono', monospace", fontSize: "0.7rem", color: "#ff4d4d", animation: "fadeUp 0.3s ease" }}>
              <span>⚠ {error}</span>
              <button onClick={() => setMState("config")} style={{ background: "transparent", border: "1px solid #ff4d4d", color: "#ff4d4d", padding: "2px 10px", cursor: "pointer", fontFamily: "'Share Tech Mono', monospace", fontSize: "0.6rem", borderRadius: 3 }}>DISMISS</button>
            </div>
          )}

          {/* Deploy controls */}
          {(matchState === "config" || matchState === "error") && (
            <div style={{ padding: "14px 16px", background: "#040a05", border: "1px solid #0d2010", borderBottom: "2px solid #a8ff4d", borderRadius: "4px 4px 0 0", display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12, animation: "fadeUp 0.5s ease 0.2s both", fontFamily: "'Share Tech Mono', monospace" }}>
              <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
                <div>
                  <div style={{ fontSize: "0.45rem", color: "#3d6a50", letterSpacing: "0.15em" }}>P1</div>
                  <div style={{ fontSize: "0.75rem", color: MODE_COLORS[p1.mode] }}>{p1.name} · {p1.mode.toUpperCase()}</div>
                </div>
                <div>
                  <div style={{ fontSize: "0.45rem", color: "#3d6a50", letterSpacing: "0.15em" }}>P2</div>
                  <div style={{ fontSize: "0.75rem", color: MODE_COLORS[p2.mode] }}>{p2.name} · {p2.mode.toUpperCase()}</div>
                </div>
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <button onClick={() => deploy("P1")}   style={{ background: "#a8ff4d", color: "#000", border: "none", padding: "8px 18px", fontSize: "0.75rem", fontWeight: 700, letterSpacing: "0.15em", cursor: "pointer", fontFamily: "'Barlow Condensed', sans-serif", clipPath: "polygon(0 0, calc(100%-8px) 0, 100% 8px, 100% 100%, 0 100%)" }}>▶ P1</button>
                <button onClick={() => deploy("BOTH")} style={{ background: "linear-gradient(135deg,#a8ff4d,#4d9fff)", color: "#000", border: "none", padding: "8px 24px", fontSize: "0.85rem", fontWeight: 700, letterSpacing: "0.2em", cursor: "pointer", fontFamily: "'Barlow Condensed', sans-serif", clipPath: "polygon(0 0, calc(100%-8px) 0, 100% 8px, 100% 100%, 0 100%)" }}>⚽ KICK OFF</button>
                <button onClick={() => deploy("P2")}   style={{ background: "#4d9fff", color: "#000", border: "none", padding: "8px 18px", fontSize: "0.75rem", fontWeight: 700, letterSpacing: "0.15em", cursor: "pointer", fontFamily: "'Barlow Condensed', sans-serif", clipPath: "polygon(0 0, calc(100%-8px) 0, 100% 8px, 100% 100%, 0 100%)" }}>▶ P2</button>
              </div>
            </div>
          )}

          {/* Deploying state */}
          {matchState === "deploying" && (
            <div style={{ padding: "20px", background: "#040a05", border: "1px solid #ffcc00", borderRadius: 4, textAlign: "center", animation: "fadeUp 0.3s ease" }}>
              <div style={{ fontSize: "0.6rem", color: "#ffcc00", letterSpacing: "0.4em", fontFamily: "'Share Tech Mono', monospace", marginBottom: 8, animation: "pulse 1.5s infinite" }}>DEPLOYING TO ROBOT</div>
              <div style={{ fontSize: "1.3rem", fontWeight: 700, color: "#ffcc00" }}>⚡ {deployMsg}</div>
            </div>
          )}

          {/* Live state */}
          {matchState === "live" && (
            <div style={{ padding: "16px", background: "#040a05", border: "1px solid #a8ff4d30", borderRadius: 4, animation: "fadeUp 0.3s ease, livePulse 3s infinite" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                <div>
                  <div style={{ fontSize: "0.5rem", color: "#3d6a50", letterSpacing: "0.2em", fontFamily: "'Share Tech Mono', monospace" }}>MATCH IN PROGRESS</div>
                  <div style={{ fontSize: "1.5rem", fontWeight: 900, color: "#a8ff4d" }}>⚽ ROBOTS ON PITCH</div>
                </div>
                <div style={{ display: "flex", gap: 6 }}>
                  <button onClick={stop} style={{ background: "#ff4d4d", border: "none", color: "#fff", padding: "8px 20px", fontFamily: "'Barlow Condensed', sans-serif", fontSize: "0.85rem", fontWeight: 700, letterSpacing: "0.2em", cursor: "pointer", clipPath: "polygon(0 0, calc(100%-8px) 0, 100% 8px, 100% 100%, 0 100%)" }}>■ STOP</button>
                  <button onClick={() => setMState("config")} style={{ background: "transparent", border: "1px solid #3d6a50", color: "#3d6a50", padding: "8px 16px", fontFamily: "'Barlow Condensed', sans-serif", fontSize: "0.75rem", letterSpacing: "0.15em", cursor: "pointer" }}>← CONFIG</button>
                </div>
              </div>
              <div style={{ display: "flex", gap: 16, fontFamily: "'Share Tech Mono', monospace", fontSize: "0.65rem", color: "#3d6a50" }}>
                <span style={{ color: MODE_COLORS[p1.mode] }}>{p1.name} · {p1.mode}</span>
                <span>vs</span>
                <span style={{ color: MODE_COLORS[p2.mode] }}>{p2.name} · {p2.mode}</span>
              </div>
            </div>
          )}

          {/* Architecture note */}
          <div style={{ marginTop: "auto", padding: "10px 12px", background: "#040a05", border: "1px solid #0d2010", borderRadius: 4, fontFamily: "'Share Tech Mono', monospace", fontSize: "0.5rem", color: "#3d6a50", lineHeight: 1.8, letterSpacing: "0.05em" }}>
            <div style={{ color: "#a8ff4d", marginBottom: 4 }}>SYSTEM ARCHITECTURE</div>
            <div>Robot → WS /api/ws/bot_camera → Python Server → /api/ws/camera_feed → browser</div>
            <div>Robot → WS /api/ws/bot → Python AI Decision → /api/ws/frontend</div>
            <div>Browser → POST /api/command → Python Server → Robot polling</div>
          </div>
        </div>

        {/* ── Right: telemetry panel ── */}
        <div style={{ padding: "1rem", overflowY: "auto", borderLeft: "1px solid #0d2010", background: "#020e05" }}>
          <TelemetryPanel />
        </div>
      </div>
    </div>
  );
}
