import { useState, useEffect, useRef } from "react";

// ── Canvas ball-tracking overlay ──────────────────────────────────────────────
function drawBallOverlay(ctx, w, h, telem) {
  ctx.clearRect(0, 0, w, h);

  // Head-yaw crosshair (always drawn)
  const hYaw = telem.head_yaw || 0;
  const rx = w * 0.5 + hYaw * w * 0.28;
  ctx.strokeStyle = "rgba(77,159,255,0.35)";
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 6]);
  ctx.beginPath(); ctx.moveTo(rx, 0); ctx.lineTo(rx, h); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(0, h * 0.5); ctx.lineTo(w, h * 0.5); ctx.stroke();
  ctx.setLineDash([]);

  if (!telem.ball_valid) return;

  const bx   = telem.ball_bx  || 0;
  const by   = telem.ball_by  || 0;
  const vx   = telem.ball_vx  || 0;
  const vy   = telem.ball_vy  || 0;
  const pbx  = telem.ball_pred_bx || 0;
  const pby  = telem.ball_pred_by || 0;
  const conf = telem.ball_confidence || 0;
  const bsz  = telem.ball_bsz || 0;
  const dist = telem.ball_dist || 0;

  const cx = (0.5 + bx) * w;
  const cy = (0.5 + by) * h;
  const radius = Math.max(10, Math.sqrt(bsz) * w * 0.6);

  const confColor = conf > 0.7 ? "#a8ff4d" : conf > 0.4 ? "#ffcc00" : "#ff6b35";

  // Ball circle
  ctx.shadowColor = confColor;
  ctx.shadowBlur  = 10;
  ctx.strokeStyle = confColor;
  ctx.lineWidth   = 2.5;
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.stroke();
  ctx.shadowBlur = 0;

  // Velocity arrow
  const speed = Math.sqrt(vx * vx + vy * vy);
  if (speed > 0.04) {
    const scale = Math.min(w, h) * 0.45;
    const ex = cx + vx * scale;
    const ey = cy + vy * scale;
    ctx.strokeStyle = "#ffcc00";
    ctx.lineWidth   = 2;
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(ex, ey); ctx.stroke();
    const ang = Math.atan2(ey - cy, ex - cx);
    const aLen = 9;
    ctx.fillStyle = "#ffcc00";
    ctx.beginPath();
    ctx.moveTo(ex, ey);
    ctx.lineTo(ex - aLen * Math.cos(ang - 0.5), ey - aLen * Math.sin(ang - 0.5));
    ctx.lineTo(ex - aLen * Math.cos(ang + 0.5), ey - aLen * Math.sin(ang + 0.5));
    ctx.closePath();
    ctx.fill();
  }

  // Prediction dashed line + dot
  const pcx = (0.5 + pbx) * w;
  const pcy = (0.5 + pby) * h;
  ctx.strokeStyle = "rgba(168,255,77,0.55)";
  ctx.lineWidth = 1.5;
  ctx.setLineDash([5, 5]);
  ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(pcx, pcy); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle   = "rgba(168,255,77,0.9)";
  ctx.strokeStyle = "#a8ff4d";
  ctx.lineWidth   = 1.5;
  ctx.beginPath(); ctx.arc(pcx, pcy, 5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();

  // Confidence arc (top-right)
  const ax = w - 26, ay = 26, ar = 18;
  ctx.strokeStyle = "#0a2010";
  ctx.lineWidth   = 4;
  ctx.beginPath(); ctx.arc(ax, ay, ar, -Math.PI / 2, Math.PI * 1.5); ctx.stroke();
  ctx.shadowColor = confColor; ctx.shadowBlur = 6;
  ctx.strokeStyle = confColor;
  ctx.beginPath();
  ctx.arc(ax, ay, ar, -Math.PI / 2, -Math.PI / 2 + conf * Math.PI * 2);
  ctx.stroke();
  ctx.shadowBlur = 0;
  ctx.fillStyle  = confColor;
  ctx.font       = "bold 9px 'Share Tech Mono', monospace";
  ctx.textAlign  = "center"; ctx.textBaseline = "middle";
  ctx.fillText(Math.round(conf * 100) + "%", ax, ay);

  // Ball label
  ctx.fillStyle    = confColor;
  ctx.font         = "10px 'Share Tech Mono', monospace";
  ctx.textAlign    = "left"; ctx.textBaseline = "top";
  ctx.shadowColor  = "#000"; ctx.shadowBlur = 4;
  ctx.fillText(`d:${dist.toFixed(1)}m`, cx + radius + 5, cy - 7);
  ctx.shadowBlur   = 0;
}

// ── Camera feed with canvas overlay ──────────────────────────────────────────
function CameraFeed({ telemetry }) {
  const [frame,     setFrame]     = useState(null);
  const [connected, setConnected] = useState(false);
  const containerRef = useRef(null);
  const canvasRef    = useRef(null);
  const wsRef        = useRef(null);

  useEffect(() => {
    const connect = () => {
      wsRef.current = new WebSocket("ws://localhost:5050/api/ws/camera_feed");
      wsRef.current.onopen  = () => setConnected(true);
      wsRef.current.onclose = () => { setConnected(false); setTimeout(connect, 2000); };
      wsRef.current.onmessage = (e) => {
        try {
          const d = JSON.parse(e.data);
          setFrame(d.jpg || d.frame || null);
        } catch (_) {}
      };
    };
    connect();
    return () => wsRef.current?.close();
  }, []);

  useEffect(() => {
    const canvas    = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;
    const w = container.clientWidth  || 320;
    const h = container.clientHeight || 240;
    canvas.width  = w;
    canvas.height = h;
    drawBallOverlay(canvas.getContext("2d"), w, h, telemetry);
  }, [telemetry, frame]);

  return (
    <div style={{
      background: "rgba(0,10,5,0.95)",
      border:    `1px solid ${connected ? "#4d9fff40" : "#1a3a2a"}`,
      borderTop: `3px solid ${connected ? "#4d9fff" : "#1a3a2a"}`,
      padding: "0.6rem",
      fontFamily: "'Share Tech Mono', monospace",
    }}>
      <div style={{ fontSize: "0.55rem", letterSpacing: "0.2em", color: "#4d9fff", marginBottom: "0.4rem", display: "flex", justifyContent: "space-between" }}>
        <span>BOT CAM · POV</span>
        <span style={{ color: connected ? "#4d9fff" : "#ff4d4d" }}>{connected ? "LIVE ●" : "NO SIGNAL"}</span>
      </div>
      <div ref={containerRef} style={{ width: "100%", aspectRatio: "4/3", background: "#000", position: "relative", overflow: "hidden" }}>
        {frame ? (
          <img
            src={`data:image/jpeg;base64,${frame}`}
            alt="bot pov"
            style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
          />
        ) : (
          <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: "#1a3a2a", fontSize: "0.6rem", letterSpacing: "0.2em" }}>
            AWAITING STREAM
          </div>
        )}
        <canvas
          ref={canvasRef}
          style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}
        />
        <div style={{ position: "absolute", top: 4, left: 4, width: 6, height: 6, borderRadius: "50%", background: connected ? "#4d9fff" : "#1a3a2a", boxShadow: connected ? "0 0 8px #4d9fff" : "none" }} />
      </div>
    </div>
  );
}

// ── Mini field diagram ────────────────────────────────────────────────────────
function FieldDiagram({ telemetry }) {
  const FW = 110, FH = 66;
  const bx  = telemetry.ball_bx || 0;
  const by  = telemetry.ball_by || 0;
  const pbx = telemetry.ball_pred_bx || 0;
  const pby = telemetry.ball_pred_by || 0;

  // bx/by are [-0.5, 0.5] relative to camera frame — use as approximate field pos
  const dotX = Math.round((0.5 + bx) * FW);
  const dotY = Math.round((0.5 + by) * FH);
  const pdotX = Math.round((0.5 + pbx) * FW);
  const pdotY = Math.round((0.5 + pby) * FH);

  return (
    <div style={{ position: "relative", width: FW, height: FH, background: "#0a2010", border: "1px solid #1a3a2a", flexShrink: 0 }}>
      {/* Field markings */}
      <div style={{ position: "absolute", inset: "3px 3px", border: "1px solid #1a4a20" }} />
      <div style={{ position: "absolute", top: "3px", bottom: "3px", left: "50%", width: 1, background: "#1a4a20" }} />
      <div style={{ position: "absolute", top: "50%", left: "50%", transform: "translate(-50%, -50%)", width: 20, height: 20, borderRadius: "50%", border: "1px solid #1a4a20" }} />

      {/* Prediction dot */}
      {telemetry.ball_valid && (
        <div style={{ position: "absolute", left: pdotX - 3, top: pdotY - 3, width: 6, height: 6, borderRadius: "50%", background: "rgba(168,255,77,0.5)", border: "1px solid #a8ff4d" }} />
      )}
      {/* Ball dot */}
      {telemetry.ball_valid && (
        <div style={{ position: "absolute", left: dotX - 4, top: dotY - 4, width: 8, height: 8, borderRadius: "50%", background: "#ff4d4d", boxShadow: "0 0 6px #ff4d4d" }} />
      )}
      {!telemetry.ball_valid && (
        <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: "#1a3a2a", fontSize: "0.45rem", letterSpacing: "0.1em" }}>NO BALL</div>
      )}
    </div>
  );
}

// ── Telemetry HUD ─────────────────────────────────────────────────────────────
function TelemetryHUD() {
  const [telemetry, setTelemetry] = useState({
    state: "IDLE", kicks: 0, ball_age: -1, break_remaining: 0,
    trait: "none", robot_connected: false, battery_pct: -1,
    ball_valid: false, ball_bx: 0, ball_by: 0, ball_bsz: 0,
    ball_dist: 0, ball_vx: 0, ball_vy: 0, ball_pred_bx: 0, ball_pred_by: 0,
    ball_confidence: 0, head_yaw: 0, inertial_roll: 0, inertial_pitch: 0,
  });
  const [connected, setConnected] = useState(false);
  const [robotIp,   setRobotIp]   = useState("");
  const [testing,   setTesting]   = useState(false);
  const wsRef = useRef(null);

  useEffect(() => {
    fetch("http://localhost:5050/api/robot/config")
      .then(r => r.json())
      .then(d => setRobotIp(d.ip || ""))
      .catch(() => {});

    const connect = () => {
      wsRef.current = new WebSocket("ws://localhost:5050/api/ws/frontend");
      wsRef.current.onopen  = () => setConnected(true);
      wsRef.current.onclose = () => { setConnected(false); setTimeout(connect, 2000); };
      wsRef.current.onmessage = (e) => {
        try { setTelemetry(prev => ({ ...prev, ...JSON.parse(e.data) })); }
        catch (_) {}
      };
    };
    connect();
    return () => wsRef.current?.close();
  }, []);

  const handleTestConnection = async () => {
    setTesting(true);
    try {
      await fetch("http://localhost:5050/api/robot/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ip: robotIp }),
      });
      await fetch("http://localhost:5050/api/robot/test", { method: "POST" });
    } catch (_) {}
    setTimeout(() => setTesting(false), 2000);
  };

  const stateColors = {
    IDLE: "#4d7a60", SEARCH: "#ffcc00", APPROACH: "#a8ff4d",
    ALIGN: "#4d9fff", TACKLE: "#ff6b35", KICK: "#fff", HALFTIME: "#ff6600",
  };
  const stateCol = stateColors[telemetry.state] || "#4d7a60";
  const pct      = telemetry.battery_pct;
  const batColor = pct < 0 ? "#1a3a2a" : pct <= 20 ? "#ff4d4d" : pct <= 40 ? "#ffcc00" : "#a8ff4d";
  const conf     = telemetry.ball_confidence;
  const confColor = conf > 0.7 ? "#a8ff4d" : conf > 0.4 ? "#ffcc00" : "#ff6b35";

  const Row = ({ label, value, color }) => (
    <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.65rem", marginBottom: "0.2rem" }}>
      <span style={{ color: "#4d7a60", letterSpacing: "0.1em" }}>{label}</span>
      <span style={{ color: color || "#fff", fontWeight: 700 }}>{value}</span>
    </div>
  );

  return (
    <div style={{
      position: "fixed", top: 16, right: 16, zIndex: 1000, width: 290,
      background: "rgba(0,12,5,0.93)",
      border: `1px solid ${connected ? "#a8ff4d30" : "#ff4d4d30"}`,
      borderLeft: `4px solid ${stateCol}`,
      fontFamily: "'Share Tech Mono', monospace", color: "#fff",
      boxShadow: "0 10px 40px rgba(0,0,0,0.6)",
      backdropFilter: "blur(12px)",
      maxHeight: "calc(100vh - 32px)", overflowY: "auto",
    }}>
      {/* Header */}
      <div style={{ padding: "0.6rem 0.8rem", borderBottom: "1px solid #0a2010", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: "0.55rem", letterSpacing: "0.25em", color: "#4d7a60" }}>BOTFC TELEMETRY</span>
        <span style={{ fontSize: "0.55rem", letterSpacing: "0.15em", color: connected ? "#a8ff4d" : "#ff4d4d" }}>
          {connected ? "● ONLINE" : "● OFFLINE"}
        </span>
      </div>

      <div style={{ padding: "0.7rem 0.8rem" }}>

        {/* Robot IP + test */}
        <div style={{ marginBottom: "0.8rem", padding: "0.6rem", background: "rgba(0,30,15,0.4)", border: "1px solid #0a2010" }}>
          <div style={{ fontSize: "0.55rem", color: "#4d7a60", marginBottom: "0.4rem", letterSpacing: "0.15em" }}>
            ROBOT LINK · <span style={{ color: telemetry.robot_connected ? "#a8ff4d" : "#ff4d4d" }}>
              {telemetry.robot_connected ? "LINKED" : "UNLINKED"}
            </span>
          </div>
          <div style={{ display: "flex", gap: "0.4rem" }}>
            <input
              value={robotIp}
              onChange={e => setRobotIp(e.target.value)}
              placeholder="ROBOT IP"
              style={{ flex: 1, background: "#000", border: "1px solid #0a2010", color: "#a8ff4d", fontSize: "0.7rem", padding: "0.25rem 0.4rem", fontFamily: "'Share Tech Mono', monospace", outline: "none" }}
            />
            <button onClick={handleTestConnection} disabled={testing}
              style={{ background: "#0a2010", color: "#a8ff4d", border: "1px solid #1a4a20", padding: "0 0.6rem", fontSize: "0.6rem", cursor: "pointer", fontFamily: "'Share Tech Mono', monospace" }}>
              {testing ? "..." : "PING"}
            </button>
          </div>
        </div>

        {/* Battery */}
        <div style={{ marginBottom: "0.8rem" }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.55rem", color: "#4d7a60", marginBottom: "0.25rem" }}>
            <span>BATTERY</span>
            <span style={{ color: batColor }}>{pct < 0 ? "N/A" : `${pct}%`}</span>
          </div>
          <div style={{ height: 5, background: "#050f08", borderRadius: 2, overflow: "hidden" }}>
            <div style={{ height: "100%", width: `${Math.max(0, pct)}%`, background: batColor, transition: "width 1s, background 0.5s", boxShadow: pct > 0 ? `0 0 5px ${batColor}80` : "none" }} />
          </div>
        </div>

        {/* Camera feed */}
        <div style={{ marginBottom: "0.8rem" }}>
          <CameraFeed telemetry={telemetry} />
        </div>

        {/* State + kicks */}
        <div style={{ marginBottom: "0.8rem", display: "flex", gap: "0.6rem" }}>
          <div style={{ flex: 2, padding: "0.5rem", background: `${stateCol}12`, border: `1px solid ${stateCol}40` }}>
            <div style={{ fontSize: "0.5rem", color: "#4d7a60", marginBottom: "0.2rem", letterSpacing: "0.15em" }}>STATE</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700, color: stateCol, letterSpacing: "0.05em" }}>{telemetry.state}</div>
          </div>
          <div style={{ flex: 1, padding: "0.5rem", background: "rgba(0,30,15,0.3)", border: "1px solid #0a2010" }}>
            <div style={{ fontSize: "0.5rem", color: "#4d7a60", marginBottom: "0.2rem", letterSpacing: "0.15em" }}>KICKS</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "#fff" }}>{telemetry.kicks}</div>
          </div>
          <div style={{ flex: 1, padding: "0.5rem", background: "rgba(0,30,15,0.3)", border: "1px solid #0a2010" }}>
            <div style={{ fontSize: "0.5rem", color: "#4d7a60", marginBottom: "0.2rem", letterSpacing: "0.15em" }}>BALL</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700, color: telemetry.ball_age > 10 || telemetry.ball_age < 0 ? "#ff4d4d" : "#a8ff4d" }}>
              {telemetry.ball_age < 0 ? "—" : `${telemetry.ball_age.toFixed(1)}s`}
            </div>
          </div>
        </div>

        {/* Ball tracking */}
        <div style={{ marginBottom: "0.8rem", padding: "0.6rem", background: telemetry.ball_valid ? "rgba(168,255,77,0.05)" : "rgba(0,20,10,0.3)", border: `1px solid ${telemetry.ball_valid ? "#a8ff4d30" : "#0a2010"}` }}>
          <div style={{ fontSize: "0.55rem", color: "#4d7a60", letterSpacing: "0.15em", marginBottom: "0.5rem", display: "flex", justifyContent: "space-between" }}>
            <span>BALL TRACK</span>
            <span style={{ color: telemetry.ball_valid ? "#a8ff4d" : "#ff4d4d" }}>{telemetry.ball_valid ? "ACQUIRED" : "LOST"}</span>
          </div>

          <div style={{ display: "flex", gap: "0.6rem", alignItems: "center", marginBottom: "0.4rem" }}>
            <FieldDiagram telemetry={telemetry} />
            <div style={{ flex: 1 }}>
              <Row label="BX" value={telemetry.ball_bx.toFixed(3)} color={telemetry.ball_valid ? "#fff" : "#333"} />
              <Row label="BY" value={telemetry.ball_by.toFixed(3)} color={telemetry.ball_valid ? "#fff" : "#333"} />
              <Row label="DIST" value={`${telemetry.ball_dist.toFixed(2)}m`} color={telemetry.ball_valid ? "#4d9fff" : "#333"} />
            </div>
          </div>

          {/* Velocity */}
          <div style={{ marginBottom: "0.4rem" }}>
            <Row
              label="VELOCITY"
              value={`vx:${telemetry.ball_vx.toFixed(2)} vy:${telemetry.ball_vy.toFixed(2)}`}
              color="#ffcc00"
            />
            <Row
              label="PREDICT"
              value={`px:${telemetry.ball_pred_bx.toFixed(3)} py:${telemetry.ball_pred_by.toFixed(3)}`}
              color="#a8ff4d"
            />
          </div>

          {/* Confidence bar */}
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.5rem", color: "#4d7a60", marginBottom: "0.2rem" }}>
              <span>CONFIDENCE</span>
              <span style={{ color: confColor }}>{Math.round(conf * 100)}%</span>
            </div>
            <div style={{ height: 4, background: "#050f08", borderRadius: 2, overflow: "hidden" }}>
              <div style={{ height: "100%", width: `${conf * 100}%`, background: confColor, transition: "width 0.3s, background 0.3s", boxShadow: `0 0 4px ${confColor}80` }} />
            </div>
          </div>
        </div>

        {/* Inertial */}
        <div style={{ marginBottom: "0.8rem", padding: "0.6rem", background: "rgba(0,20,10,0.3)", border: "1px solid #0a2010" }}>
          <div style={{ fontSize: "0.55rem", color: "#4d7a60", letterSpacing: "0.15em", marginBottom: "0.4rem" }}>INERTIAL</div>
          <Row label="ROLL"      value={`${telemetry.inertial_roll.toFixed(3)} rad`}  color={Math.abs(telemetry.inertial_roll) > 0.3 ? "#ff4d4d" : "#fff"} />
          <Row label="PITCH"     value={`${telemetry.inertial_pitch.toFixed(3)} rad`} color={Math.abs(telemetry.inertial_pitch) > 0.3 ? "#ff4d4d" : "#fff"} />
          <Row label="HEAD YAW"  value={`${telemetry.head_yaw.toFixed(3)} rad`}       color="#4d9fff" />
        </div>

        {/* Halftime countdown */}
        {telemetry.break_remaining > 0 && (
          <div style={{ padding: "0.5rem", background: "#ff660018", border: "1px solid #ff6600", textAlign: "center", marginBottom: "0.8rem" }}>
            <div style={{ fontSize: "0.55rem", color: "#ff6600", letterSpacing: "0.2em" }}>COOLING DOWN</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "#ff6600" }}>{telemetry.break_remaining}s</div>
          </div>
        )}

      </div>
    </div>
  );
}

// ── Player card ───────────────────────────────────────────────────────────────
const MODES = ["offense", "defense", "balanced"];
const DIFFICULTIES = ["easy", "medium", "hard"];
const modeIcons  = { offense: "⚡", defense: "🛡", balanced: "⚖" };
const diffIcons  = { easy: "●", medium: "●●", hard: "●●●" };
const modeColors = { offense: "#ff4d4d", defense: "#4d9fff", balanced: "#a8ff4d" };
const diffColors = { easy: "#a8ff4d", medium: "#ffcc00", hard: "#ff4d4d" };

function PlayerCard({ number, player, setPlayer }) {
  const [editing, setEditing] = useState(false);
  const [draft,   setDraft]   = useState(player.name);
  const selModeColor = modeColors[player.mode];

  return (
    <div style={{
      position: "relative", background: "rgba(0,20,10,0.85)",
      border: `1px solid ${selModeColor}40`, borderTop: `3px solid ${selModeColor}`,
      padding: "2rem", flex: 1, minWidth: 280, maxWidth: 420,
      fontFamily: "'Barlow Condensed', sans-serif",
      clipPath: "polygon(0 0, calc(100% - 20px) 0, 100% 20px, 100% 100%, 0 100%)",
      boxShadow: `0 0 40px ${selModeColor}15, inset 0 0 60px rgba(0,0,0,0.4)`,
      transition: "all 0.3s ease",
    }}>
      <div style={{ position: "absolute", top: 0, right: 0, width: 0, height: 0, borderTop: `20px solid ${selModeColor}`, borderLeft: "20px solid transparent" }} />
      <div style={{ position: "absolute", top: "1.5rem", right: "2.5rem", fontSize: "4rem", fontWeight: 900, color: `${selModeColor}20`, lineHeight: 1, userSelect: "none" }}>P{number}</div>
      <div style={{ fontSize: "0.7rem", letterSpacing: "0.3em", color: "#4d7a60", textTransform: "uppercase", marginBottom: "0.5rem" }}>PLAYER {number} — NAO UNIT</div>

      {/* Name */}
      <div style={{ marginBottom: "2rem" }}>
        {editing ? (
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
            <input autoFocus value={draft} onChange={e => setDraft(e.target.value)}
              onKeyDown={e => {
                if (e.key === "Enter") { setPlayer({ ...player, name: draft || `Player ${number}` }); setEditing(false); }
                if (e.key === "Escape") { setDraft(player.name); setEditing(false); }
              }}
              style={{ background: "transparent", border: "none", borderBottom: `2px solid ${selModeColor}`, color: "#fff", fontSize: "1.8rem", fontWeight: 700, fontFamily: "'Barlow Condensed', sans-serif", outline: "none", width: "100%", textTransform: "uppercase", letterSpacing: "0.05em" }}
            />
            <button onClick={() => { setPlayer({ ...player, name: draft || `Player ${number}` }); setEditing(false); }}
              style={{ background: selModeColor, border: "none", color: "#000", fontWeight: 700, padding: "0.3rem 0.7rem", cursor: "pointer", fontSize: "0.75rem", fontFamily: "'Barlow Condensed', sans-serif", letterSpacing: "0.1em" }}>
              SET
            </button>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
            <div style={{ fontSize: "1.8rem", fontWeight: 700, color: "#fff", letterSpacing: "0.05em", textTransform: "uppercase" }}>{player.name}</div>
            <button onClick={() => { setDraft(player.name); setEditing(true); }}
              style={{ background: "transparent", border: `1px solid #4d7a60`, color: "#4d7a60", padding: "0.2rem 0.6rem", cursor: "pointer", fontSize: "0.65rem", letterSpacing: "0.15em", fontFamily: "'Barlow Condensed', sans-serif" }}>
              EDIT
            </button>
          </div>
        )}
      </div>

      {/* Mode */}
      <div style={{ marginBottom: "1.5rem" }}>
        <div style={{ fontSize: "0.65rem", letterSpacing: "0.25em", color: "#4d7a60", marginBottom: "0.6rem" }}>PLAY STYLE</div>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          {MODES.map(m => (
            <button key={m} onClick={() => setPlayer({ ...player, mode: m })}
              style={{ flex: 1, background: player.mode === m ? `${modeColors[m]}20` : "transparent", border: `1px solid ${player.mode === m ? modeColors[m] : "#1a3a2a"}`, color: player.mode === m ? modeColors[m] : "#4d7a60", padding: "0.6rem 0.3rem", cursor: "pointer", fontFamily: "'Barlow Condensed', sans-serif", fontSize: "0.75rem", letterSpacing: "0.1em", textTransform: "uppercase", transition: "all 0.2s", display: "flex", flexDirection: "column", alignItems: "center", gap: "0.2rem" }}>
              <span style={{ fontSize: "1.1rem" }}>{modeIcons[m]}</span>{m}
            </button>
          ))}
        </div>
      </div>

      {/* Difficulty */}
      <div>
        <div style={{ fontSize: "0.65rem", letterSpacing: "0.25em", color: "#4d7a60", marginBottom: "0.6rem" }}>DIFFICULTY</div>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          {DIFFICULTIES.map(d => (
            <button key={d} onClick={() => setPlayer({ ...player, difficulty: d })}
              style={{ flex: 1, background: player.difficulty === d ? `${diffColors[d]}18` : "transparent", border: `1px solid ${player.difficulty === d ? diffColors[d] : "#1a3a2a"}`, color: player.difficulty === d ? diffColors[d] : "#4d7a60", padding: "0.6rem 0.3rem", cursor: "pointer", fontFamily: "'Barlow Condensed', sans-serif", fontSize: "0.7rem", letterSpacing: "0.1em", textTransform: "uppercase", transition: "all 0.2s", display: "flex", flexDirection: "column", alignItems: "center", gap: "0.2rem" }}>
              <span style={{ fontSize: "0.8rem", letterSpacing: "0.05em" }}>{diffIcons[d]}</span>{d}
            </button>
          ))}
        </div>
      </div>

      <div style={{ marginTop: "1.5rem", paddingTop: "1rem", borderTop: "1px solid #1a3a2a", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ fontSize: "0.65rem", color: "#4d7a60", letterSpacing: "0.15em" }}>STATUS</div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
          <div style={{ width: 6, height: 6, borderRadius: "50%", background: selModeColor, boxShadow: `0 0 8px ${selModeColor}` }} />
          <span style={{ fontSize: "0.65rem", color: selModeColor, letterSpacing: "0.15em" }}>CONFIGURED</span>
        </div>
      </div>
    </div>
  );
}

// ── App root ──────────────────────────────────────────────────────────────────
export default function App() {
  const [p1, setP1] = useState({ name: "ATLAS", mode: "offense",  difficulty: "medium" });
  const [p2, setP2] = useState({ name: "ARES",  mode: "defense",  difficulty: "medium" });
  const [matchState,   setMatchState]   = useState("config");
  const [deployStatus, setDeployStatus] = useState("");
  const [errorMsg,     setErrorMsg]     = useState("");

  const handleDeploy = async (playerKey) => {
    setMatchState("deploying");
    setDeployStatus(`Deploying ${playerKey === "BOTH" ? "both players" : playerKey}...`);
    setErrorMsg("");
    try {
      const payload = {};
      if (playerKey === "P1" || playerKey === "BOTH") payload.player1 = p1;
      if (playerKey === "P2" || playerKey === "BOTH") payload.player2 = p2;
      const res = await fetch("http://localhost:5050/api/start_match", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`Server ${res.status}`);
      setDeployStatus("Brain deployed. Match is LIVE!");
      setMatchState("live");
    } catch (e) {
      setErrorMsg(`Deploy failed: ${e.message}`);
      setMatchState("error");
    }
  };

  const handleStop = async () => {
    setDeployStatus("Stopping...");
    try {
      await fetch("http://localhost:5050/api/stop_match", { method: "POST" });
      setMatchState("config");
      setDeployStatus("");
    } catch (_) {}
  };

  return (
    <div style={{
      minHeight: "100vh", background: "#010d06",
      backgroundImage: `
        radial-gradient(ellipse at 20% 50%, rgba(0,60,20,0.3) 0%, transparent 60%),
        radial-gradient(ellipse at 80% 50%, rgba(0,40,60,0.3) 0%, transparent 60%),
        repeating-linear-gradient(0deg, transparent, transparent 39px, rgba(0,255,80,0.03) 39px, rgba(0,255,80,0.03) 40px),
        repeating-linear-gradient(90deg, transparent, transparent 39px, rgba(0,255,80,0.03) 39px, rgba(0,255,80,0.03) 40px)`,
      display: "flex", flexDirection: "column", alignItems: "center",
      padding: "3rem 1.5rem", fontFamily: "'Barlow Condensed', sans-serif",
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@300;400;500;600;700;900&family=Share+Tech+Mono&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        button:hover { filter: brightness(1.15); transform: translateY(-1px); }
        button:active { transform: translateY(0); }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }
        @keyframes scanline { 0%{transform:translateY(-100%)} 100%{transform:translateY(100vh)} }
        @keyframes fadeUp { from{opacity:0;transform:translateY(20px)} to{opacity:1;transform:translateY(0)} }
        @keyframes livePulse { 0%,100%{box-shadow:0 0 20px rgba(168,255,77,0.3)} 50%{box-shadow:0 0 40px rgba(168,255,77,0.6)} }
        ::-webkit-scrollbar { width: 4px; } ::-webkit-scrollbar-track { background: #010d06; }
        ::-webkit-scrollbar-thumb { background: #1a3a2a; }
      `}</style>

      <TelemetryHUD />

      {/* Scanline */}
      <div style={{ position: "fixed", top: 0, left: 0, right: 0, height: "3px", background: "linear-gradient(transparent, rgba(0,255,80,0.06), transparent)", animation: "scanline 6s linear infinite", pointerEvents: "none", zIndex: 100 }} />

      {/* Header */}
      <div style={{ textAlign: "center", marginBottom: "3rem", animation: "fadeUp 0.6s ease" }}>
        <div style={{ fontSize: "0.7rem", letterSpacing: "0.5em", color: "#4d7a60", marginBottom: "0.5rem", fontFamily: "'Share Tech Mono', monospace" }}>
          NAO ROBOTICS // FOOTBALL CONTROL SYSTEM v2.1
        </div>
        <h1 style={{ fontSize: "clamp(2.5rem, 6vw, 4.5rem)", fontWeight: 900, color: "#fff", letterSpacing: "0.05em", textTransform: "uppercase", lineHeight: 0.9 }}>
          {matchState === "live"
            ? <>MATCH <span style={{ color: "#a8ff4d" }}>LIVE</span></>
            : <>MATCH <span style={{ color: "#a8ff4d" }}>CONFIG</span></>}
        </h1>
        <div style={{ width: 80, height: 2, background: "linear-gradient(90deg, transparent, #a8ff4d, transparent)", margin: "1rem auto" }} />
        <div style={{ fontSize: "0.75rem", color: "#4d7a60", letterSpacing: "0.2em", fontFamily: "'Share Tech Mono', monospace" }}>
          {matchState === "live" ? "ROBOTS ON THE PITCH · MONITORING" : "SELECT PLAYERS · ASSIGN ROLES · DEPLOY"}
        </div>
      </div>

      {/* VS layout */}
      <div style={{ display: "flex", gap: "1rem", alignItems: "stretch", width: "100%", maxWidth: 900, animation: "fadeUp 0.6s ease 0.2s both", flexWrap: "wrap", justifyContent: "center" }}>
        <PlayerCard number={1} player={p1} setPlayer={setP1} />
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: "0.5rem", padding: "0 0.5rem", minWidth: 50 }}>
          <div style={{ width: 1, flex: 1, background: "linear-gradient(transparent, #1a3a2a, transparent)" }} />
          <div style={{ fontSize: "1.2rem", fontWeight: 900, color: matchState === "live" ? "#a8ff4d" : "#1a3a2a", border: `1px solid ${matchState === "live" ? "#a8ff4d" : "#1a3a2a"}`, padding: "0.4rem 0.6rem", letterSpacing: "0.1em", transition: "all 0.3s" }}>VS</div>
          <div style={{ width: 1, flex: 1, background: "linear-gradient(transparent, #1a3a2a, transparent)" }} />
        </div>
        <PlayerCard number={2} player={p2} setPlayer={setP2} />
      </div>

      {/* Error */}
      {matchState === "error" && (
        <div style={{ marginTop: "1.5rem", padding: "1rem 2rem", background: "rgba(255,0,0,0.1)", border: "1px solid #ff4d4d", width: "100%", maxWidth: 900, textAlign: "center", fontFamily: "'Share Tech Mono', monospace", fontSize: "0.8rem", color: "#ff4d4d", animation: "fadeUp 0.3s ease" }}>
          ⚠ {errorMsg}
          <button onClick={() => setMatchState("config")} style={{ marginLeft: "1rem", background: "transparent", border: "1px solid #ff4d4d", color: "#ff4d4d", padding: "0.3rem 1rem", cursor: "pointer", fontFamily: "'Share Tech Mono', monospace", fontSize: "0.7rem" }}>DISMISS</button>
        </div>
      )}

      {/* Deploy controls */}
      {(matchState === "config" || matchState === "error") && (
        <div style={{ marginTop: "2rem", padding: "1.2rem 2rem", background: "rgba(0,20,10,0.8)", border: "1px solid #1a3a2a", borderBottom: "2px solid #a8ff4d", width: "100%", maxWidth: 900, display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "1rem", animation: "fadeUp 0.6s ease 0.4s both", fontFamily: "'Share Tech Mono', monospace" }}>
          <div style={{ display: "flex", gap: "2rem", flexWrap: "wrap" }}>
            <div>
              <div style={{ fontSize: "0.6rem", color: "#4d7a60", letterSpacing: "0.2em" }}>P1 LOADOUT</div>
              <div style={{ fontSize: "0.85rem", color: modeColors[p1.mode] }}>{p1.name} · {p1.mode.toUpperCase()} · {p1.difficulty.toUpperCase()}</div>
            </div>
            <div>
              <div style={{ fontSize: "0.6rem", color: "#4d7a60", letterSpacing: "0.2em" }}>P2 LOADOUT</div>
              <div style={{ fontSize: "0.85rem", color: modeColors[p2.mode] }}>{p2.name} · {p2.mode.toUpperCase()} · {p2.difficulty.toUpperCase()}</div>
            </div>
          </div>
          <div style={{ display: "flex", gap: "0.7rem", flexWrap: "wrap" }}>
            <button onClick={() => handleDeploy("P1")} style={{ background: "#a8ff4d", color: "#000", border: "none", padding: "0.8rem 1.8rem", fontSize: "0.8rem", fontWeight: 700, letterSpacing: "0.2em", cursor: "pointer", fontFamily: "'Barlow Condensed', sans-serif", textTransform: "uppercase", clipPath: "polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%)" }}>▶ P1</button>
            <button onClick={() => handleDeploy("BOTH")} style={{ background: "linear-gradient(135deg, #a8ff4d, #4d9fff)", color: "#000", border: "none", padding: "0.8rem 2.5rem", fontSize: "0.9rem", fontWeight: 700, letterSpacing: "0.25em", cursor: "pointer", fontFamily: "'Barlow Condensed', sans-serif", textTransform: "uppercase", clipPath: "polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%)" }}>⚽ KICK OFF</button>
            <button onClick={() => handleDeploy("P2")} style={{ background: "#4d9fff", color: "#000", border: "none", padding: "0.8rem 1.8rem", fontSize: "0.8rem", fontWeight: 700, letterSpacing: "0.2em", cursor: "pointer", fontFamily: "'Barlow Condensed', sans-serif", textTransform: "uppercase", clipPath: "polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%)" }}>▶ P2</button>
          </div>
        </div>
      )}

      {/* Deploying */}
      {matchState === "deploying" && (
        <div style={{ marginTop: "2rem", padding: "2.5rem", background: "rgba(0,30,15,0.95)", border: "1px solid #ffcc00", width: "100%", maxWidth: 900, textAlign: "center", animation: "fadeUp 0.4s ease" }}>
          <div style={{ fontSize: "0.7rem", letterSpacing: "0.4em", color: "#ffcc00", fontFamily: "'Share Tech Mono', monospace", marginBottom: "0.8rem", animation: "pulse 1.5s infinite" }}>DEPLOYING TO ROBOT</div>
          <div style={{ fontSize: "1.6rem", fontWeight: 900, color: "#ffcc00", letterSpacing: "0.1em" }}>⚡ {deployStatus}</div>
        </div>
      )}

      {/* Live */}
      {matchState === "live" && (
        <div style={{ marginTop: "2rem", padding: "2.5rem", background: "rgba(0,30,15,0.95)", border: "1px solid #a8ff4d", width: "100%", maxWidth: 900, textAlign: "center", animation: "fadeUp 0.4s ease, livePulse 3s infinite" }}>
          <div style={{ fontSize: "0.7rem", letterSpacing: "0.4em", color: "#4d7a60", fontFamily: "'Share Tech Mono', monospace", marginBottom: "0.8rem" }}>MATCH IN PROGRESS</div>
          <div style={{ fontSize: "2rem", fontWeight: 900, color: "#a8ff4d", letterSpacing: "0.1em", marginBottom: "1.5rem" }}>⚽ ROBOTS ON THE PITCH</div>
          <div style={{ background: "rgba(0,0,0,0.5)", border: "1px solid #1a3a2a", padding: "1rem 1.5rem", textAlign: "left", fontFamily: "'Share Tech Mono', monospace", fontSize: "0.75rem", color: "#4d7a60", lineHeight: 1.8, marginBottom: "1.5rem" }}>
            <span style={{ color: "#a8ff4d" }}>{"{"}</span><br />
            &nbsp;&nbsp;<span style={{ color: "#4d9fff" }}>"player1"</span>: {"{"} name: <span style={{ color: "#ffcc00" }}>"{p1.name}"</span>, mode: <span style={{ color: modeColors[p1.mode] }}>"{p1.mode}"</span> {"}"}<br />
            &nbsp;&nbsp;<span style={{ color: "#4d9fff" }}>"player2"</span>: {"{"} name: <span style={{ color: "#ffcc00" }}>"{p2.name}"</span>, mode: <span style={{ color: modeColors[p2.mode] }}>"{p2.mode}"</span> {"}"}<br />
            <span style={{ color: "#a8ff4d" }}>{"}"}</span>
          </div>
          <div style={{ display: "flex", gap: "1rem", justifyContent: "center" }}>
            <button onClick={handleStop} style={{ background: "#ff4d4d", border: "none", color: "#fff", padding: "0.8rem 2.5rem", fontFamily: "'Barlow Condensed', sans-serif", fontSize: "0.9rem", fontWeight: 700, letterSpacing: "0.25em", cursor: "pointer", textTransform: "uppercase", clipPath: "polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%)" }}>■ STOP MATCH</button>
            <button onClick={() => setMatchState("config")} style={{ background: "transparent", border: "1px solid #4d7a60", color: "#4d7a60", padding: "0.8rem 2rem", fontFamily: "'Barlow Condensed', sans-serif", fontSize: "0.8rem", letterSpacing: "0.2em", cursor: "pointer", textTransform: "uppercase" }}>← RECONFIGURE</button>
          </div>
        </div>
      )}

      <div style={{ marginTop: "3rem", fontSize: "0.6rem", color: "#1a3a2a", letterSpacing: "0.3em", fontFamily: "'Share Tech Mono', monospace", textAlign: "center" }}>
        NAO FOOTBALL AI SYSTEM · HACKATHON BUILD · 2026
      </div>
    </div>
  );
}
