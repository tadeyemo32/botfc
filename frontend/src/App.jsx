import { useState, useEffect, useCallback } from "react";

const API = "http://localhost:5000/api";

const MODES = ["offense", "defense", "balanced"];
const DIFFICULTIES = ["easy", "medium", "hard"];
const modeIcons = { offense: "⚡", defense: "🛡", balanced: "⚖" };
const diffIcons  = { easy: "●", medium: "●●", hard: "●●●" };
const modeColors = { offense: "#ff4d4d", defense: "#4d9fff", balanced: "#a8ff4d" };
const diffColors = { easy: "#a8ff4d", medium: "#ffcc00", hard: "#ff4d4d" };

// ── Nav ──────────────────────────────────────────────────────
function Nav({ page, setPage, robotsOnline }) {
  const links = [
    { id: "config", label: "MATCH CONFIG" },
    { id: "status", label: "ROBOT STATUS" },
  ];
  return (
    <nav style={{ width: "100%", maxWidth: 900, display: "flex", gap: "0.5rem", marginBottom: "2.5rem", borderBottom: "1px solid #1a3a2a" }}>
      {links.map(({ id, label }) => (
        <button key={id} onClick={() => setPage(id)} style={{
          padding: "0.6rem 1.2rem", background: "transparent", border: "none",
          fontFamily: "'Barlow Condensed', sans-serif", fontSize: "0.8rem",
          letterSpacing: "0.2em", cursor: "pointer", textTransform: "uppercase",
          color: page === id ? "#a8ff4d" : "#4d7a60",
          borderBottom: page === id ? "2px solid #a8ff4d" : "2px solid transparent",
          transition: "all 0.2s",
        }}>
          {label}
          {id === "status" && (
            <span style={{ marginLeft: "0.4rem", width: 6, height: 6, borderRadius: "50%", display: "inline-block", background: robotsOnline ? "#a8ff4d" : "#ff4d4d", boxShadow: robotsOnline ? "0 0 6px #a8ff4d" : "none" }} />
          )}
        </button>
      ))}
    </nav>
  );
}

// ── PlayerCard ───────────────────────────────────────────────
function PlayerCard({ number, player, setPlayer }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(player.name);
  const mc = modeColors[player.mode];
  const dc = diffColors[player.difficulty];

  return (
    <div style={{ position: "relative", background: "rgba(0,20,10,0.85)", border: `1px solid ${mc}40`, borderTop: `3px solid ${mc}`, padding: "2rem", flex: 1, minWidth: 280, maxWidth: 420, fontFamily: "'Barlow Condensed', sans-serif", clipPath: "polygon(0 0, calc(100% - 20px) 0, 100% 20px, 100% 100%, 0 100%)", boxShadow: `0 0 40px ${mc}20, inset 0 0 60px rgba(0,0,0,0.4)`, transition: "all 0.3s ease" }}>
      <div style={{ position: "absolute", top: 0, right: 0, width: 0, height: 0, borderTop: `20px solid ${mc}`, borderLeft: "20px solid transparent" }} />
      <div style={{ position: "absolute", top: "1.5rem", right: "2.5rem", fontSize: "4rem", fontWeight: 900, color: `${mc}20`, lineHeight: 1, userSelect: "none" }}>P{number}</div>
      <div style={{ fontSize: "0.7rem", letterSpacing: "0.3em", color: "#4d7a60", textTransform: "uppercase", marginBottom: "0.5rem" }}>PLAYER {number} — NAO UNIT</div>

      <div style={{ marginBottom: "2rem" }}>
        {editing ? (
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
            <input autoFocus value={draft} onChange={e => setDraft(e.target.value)}
              onKeyDown={e => {
                if (e.key === "Enter") { setPlayer({ ...player, name: draft || `Player ${number}` }); setEditing(false); }
                if (e.key === "Escape") { setDraft(player.name); setEditing(false); }
              }}
              style={{ background: "transparent", border: "none", borderBottom: `2px solid ${mc}`, color: "#fff", fontSize: "1.8rem", fontWeight: 700, fontFamily: "'Barlow Condensed', sans-serif", outline: "none", width: "100%", textTransform: "uppercase", letterSpacing: "0.05em" }} />
            <button onClick={() => { setPlayer({ ...player, name: draft || `Player ${number}` }); setEditing(false); }}
              style={{ background: mc, border: "none", color: "#000", fontWeight: 700, padding: "0.3rem 0.7rem", cursor: "pointer", fontSize: "0.75rem", fontFamily: "'Barlow Condensed', sans-serif", letterSpacing: "0.1em" }}>SET</button>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
            <div style={{ fontSize: "1.8rem", fontWeight: 700, color: "#fff", letterSpacing: "0.05em", textTransform: "uppercase" }}>{player.name}</div>
            <button onClick={() => { setDraft(player.name); setEditing(true); }}
              style={{ background: "transparent", border: "1px solid #4d7a60", color: "#4d7a60", padding: "0.2rem 0.6rem", cursor: "pointer", fontSize: "0.65rem", letterSpacing: "0.15em", fontFamily: "'Barlow Condensed', sans-serif" }}>EDIT</button>
          </div>
        )}
      </div>

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

      <div>
        <div style={{ fontSize: "0.65rem", letterSpacing: "0.25em", color: "#4d7a60", marginBottom: "0.6rem" }}>DIFFICULTY</div>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          {DIFFICULTIES.map(d => (
            <button key={d} onClick={() => setPlayer({ ...player, difficulty: d })}
              style={{ flex: 1, background: player.difficulty === d ? `${diffColors[d]}18` : "transparent", border: `1px solid ${player.difficulty === d ? diffColors[d] : "#1a3a2a"}`, color: player.difficulty === d ? diffColors[d] : "#4d7a60", padding: "0.6rem 0.3rem", cursor: "pointer", fontFamily: "'Barlow Condensed', sans-serif", fontSize: "0.7rem", letterSpacing: "0.1em", textTransform: "uppercase", transition: "all 0.2s", display: "flex", flexDirection: "column", alignItems: "center", gap: "0.2rem" }}>
              <span style={{ fontSize: "0.8rem" }}>{diffIcons[d]}</span>{d}
            </button>
          ))}
        </div>
      </div>

      <div style={{ marginTop: "1.5rem", paddingTop: "1rem", borderTop: "1px solid #1a3a2a", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ fontSize: "0.65rem", color: "#4d7a60", letterSpacing: "0.15em" }}>STATUS</div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
          <div style={{ width: 6, height: 6, borderRadius: "50%", background: mc, boxShadow: `0 0 8px ${mc}` }} />
          <span style={{ fontSize: "0.65rem", color: mc, letterSpacing: "0.15em" }}>CONFIGURED</span>
        </div>
      </div>
    </div>
  );
}

// ── Match Config page ────────────────────────────────────────
function MatchConfig({ onMatchStart }) {
  const [p1, setP1] = useState({ name: "ATLAS", mode: "offense", difficulty: "medium" });
  const [p2, setP2] = useState({ name: "ARES",  mode: "defense", difficulty: "medium" });
  const [launched, setLaunched]   = useState(false);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState(null);
  const ready = p1.name && p2.name;

  const handleDeploy = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(API + "/start_match", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ player1: p1, player2: p2 }),
      });
      if (!res.ok) throw new Error("Backend returned " + res.status);
      const data = await res.json();
      if (data.ok) {
        setLaunched(true);
        onMatchStart(true);
      }
    } catch (err) {
      setError("Could not reach backend: " + err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleStop = async () => {
    try {
      await fetch(API + "/stop_match", { method: "POST" });
    } catch (_) {}
    setLaunched(false);
    onMatchStart(false);
  };

  return (
    <>
      <div style={{ textAlign: "center", marginBottom: "3rem" }}>
        <div style={{ fontSize: "0.7rem", letterSpacing: "0.5em", color: "#4d7a60", marginBottom: "0.5rem", fontFamily: "'Share Tech Mono', monospace" }}>NAO ROBOTICS // FOOTBALL CONTROL SYSTEM v2.1</div>
        <h1 style={{ fontSize: "clamp(2.5rem, 6vw, 4.5rem)", fontWeight: 900, color: "#fff", letterSpacing: "0.05em", textTransform: "uppercase", lineHeight: 0.9 }}>
          MATCH <span style={{ color: "#a8ff4d" }}>CONFIG</span>
        </h1>
        <div style={{ width: 80, height: 2, background: "linear-gradient(90deg, transparent, #a8ff4d, transparent)", margin: "1rem auto" }} />
        <div style={{ fontSize: "0.75rem", color: "#4d7a60", letterSpacing: "0.2em", fontFamily: "'Share Tech Mono', monospace" }}>SELECT PLAYERS · ASSIGN ROLES · DEPLOY</div>
      </div>

      <div style={{ display: "flex", gap: "1rem", alignItems: "stretch", width: "100%", maxWidth: 900, flexWrap: "wrap", justifyContent: "center" }}>
        <PlayerCard number={1} player={p1} setPlayer={setP1} />
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: "0.5rem", padding: "0 0.5rem", minWidth: 50 }}>
          <div style={{ width: 1, flex: 1, background: "linear-gradient(transparent, #1a3a2a, transparent)" }} />
          <div style={{ fontSize: "1.2rem", fontWeight: 900, color: "#1a3a2a", border: "1px solid #1a3a2a", padding: "0.4rem 0.6rem", letterSpacing: "0.1em" }}>VS</div>
          <div style={{ width: 1, flex: 1, background: "linear-gradient(transparent, #1a3a2a, transparent)" }} />
        </div>
        <PlayerCard number={2} player={p2} setPlayer={setP2} />
      </div>

      {/* Error banner */}
      {error && (
        <div style={{ marginTop: "1rem", padding: "0.8rem 1.5rem", border: "1px solid #ff4d4d40", borderLeft: "3px solid #ff4d4d", background: "rgba(255,77,77,0.05)", color: "#ff4d4d", fontFamily: "'Share Tech Mono', monospace", fontSize: "0.75rem", width: "100%", maxWidth: 900 }}>
          ⚠ {error}
        </div>
      )}

      {!launched ? (
        <div style={{ marginTop: "2rem", padding: "1.2rem 2rem", background: "rgba(0,20,10,0.8)", border: "1px solid #1a3a2a", borderBottom: "2px solid #a8ff4d", width: "100%", maxWidth: 900, display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "1rem", fontFamily: "'Share Tech Mono', monospace" }}>
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
          <button disabled={!ready || loading} onClick={handleDeploy}
            style={{ background: ready && !loading ? "#a8ff4d" : "#1a3a2a", color: ready && !loading ? "#000" : "#4d7a60", border: "none", padding: "0.8rem 2.5rem", fontSize: "0.9rem", fontWeight: 700, letterSpacing: "0.25em", cursor: ready && !loading ? "pointer" : "not-allowed", fontFamily: "'Barlow Condensed', sans-serif", textTransform: "uppercase", transition: "all 0.2s", clipPath: "polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%)" }}>
            {loading ? "CONNECTING..." : "▶ DEPLOY ROBOTS"}
          </button>
        </div>
      ) : (
        <div style={{ marginTop: "2rem", padding: "2.5rem", background: "rgba(0,30,15,0.95)", border: "1px solid #a8ff4d", width: "100%", maxWidth: 900, textAlign: "center" }}>
          <div style={{ fontSize: "0.7rem", letterSpacing: "0.4em", color: "#4d7a60", fontFamily: "'Share Tech Mono', monospace", marginBottom: "0.8rem" }}>MATCH INITIALIZED</div>
          <div style={{ fontSize: "2rem", fontWeight: 900, color: "#a8ff4d", letterSpacing: "0.1em", marginBottom: "1.5rem" }}>⚽ ROBOTS DEPLOYING TO PITCH</div>
          <div style={{ background: "rgba(0,0,0,0.5)", border: "1px solid #1a3a2a", padding: "1rem 1.5rem", textAlign: "left", fontFamily: "'Share Tech Mono', monospace", fontSize: "0.75rem", color: "#4d7a60", lineHeight: 1.8, marginBottom: "1.5rem" }}>
            <span style={{ color: "#a8ff4d" }}>{"{"}</span><br />
            &nbsp;&nbsp;<span style={{ color: "#4d9fff" }}>"player1"</span>: {"{"} name: <span style={{ color: "#ffcc00" }}>"{p1.name}"</span>, mode: <span style={{ color: modeColors[p1.mode] }}>"{p1.mode}"</span>, difficulty: <span style={{ color: diffColors[p1.difficulty] }}>"{p1.difficulty}"</span> {"}"}<br />
            &nbsp;&nbsp;<span style={{ color: "#4d9fff" }}>"player2"</span>: {"{"} name: <span style={{ color: "#ffcc00" }}>"{p2.name}"</span>, mode: <span style={{ color: modeColors[p2.mode] }}>"{p2.mode}"</span>, difficulty: <span style={{ color: diffColors[p2.difficulty] }}>"{p2.difficulty}"</span> {"}"}<br />
            <span style={{ color: "#a8ff4d" }}>{"}"}</span>
          </div>
          <div style={{ display: "flex", gap: "1rem", justifyContent: "center", flexWrap: "wrap" }}>
            <button onClick={handleStop} style={{ background: "#ff4d4d", border: "none", color: "#000", padding: "0.6rem 2rem", fontFamily: "'Barlow Condensed', sans-serif", fontSize: "0.8rem", letterSpacing: "0.2em", cursor: "pointer", textTransform: "uppercase", fontWeight: 700 }}>■ STOP MATCH</button>
            <button onClick={() => setLaunched(false)} style={{ background: "transparent", border: "1px solid #4d7a60", color: "#4d7a60", padding: "0.6rem 2rem", fontFamily: "'Barlow Condensed', sans-serif", fontSize: "0.8rem", letterSpacing: "0.2em", cursor: "pointer", textTransform: "uppercase" }}>← RECONFIGURE</button>
          </div>
        </div>
      )}
    </>
  );
}

// ── Robot Status page ────────────────────────────────────────
function RobotStatus({ onRobotsUpdate }) {
  const [robots, setRobots]           = useState([]);
  const [loading, setLoading]         = useState(true);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [backendError, setBackendError] = useState(null);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(API + "/telemetry");
      if (!res.ok) throw new Error("Backend returned " + res.status);
      const data = await res.json();
      setRobots(data);
      setLastUpdated(new Date().toLocaleTimeString());
      setBackendError(null);
      onRobotsUpdate(data.some(r => r.online));
    } catch (err) {
      setBackendError("Cannot reach backend — is it running on port 5000?");
    } finally {
      setLoading(false);
    }
  }, [onRobotsUpdate]);

  useEffect(() => {
    fetchStatus();
    const t = setInterval(fetchStatus, 5000);
    return () => clearInterval(t);
  }, [fetchStatus]);

  return (
    <>
      <div style={{ textAlign: "center", marginBottom: "3rem" }}>
        <div style={{ fontSize: "0.7rem", letterSpacing: "0.5em", color: "#4d7a60", marginBottom: "0.5rem", fontFamily: "'Share Tech Mono', monospace" }}>NAO ROBOTICS // FOOTBALL CONTROL SYSTEM v2.1</div>
        <h1 style={{ fontSize: "clamp(2rem, 5vw, 3.5rem)", fontWeight: 900, color: "#fff", letterSpacing: "0.05em", textTransform: "uppercase", lineHeight: 0.9 }}>
          ROBOT <span style={{ color: "#a8ff4d" }}>STATUS</span>
        </h1>
        <div style={{ width: 80, height: 2, background: "linear-gradient(90deg, transparent, #a8ff4d, transparent)", margin: "1rem auto" }} />
        {lastUpdated && <div style={{ fontSize: "0.7rem", color: "#4d7a60", fontFamily: "'Share Tech Mono', monospace", letterSpacing: "0.15em" }}>LAST UPDATED: {lastUpdated} · AUTO-REFRESH 5s</div>}
      </div>

      {backendError && (
        <div style={{ padding: "1rem 1.5rem", border: "1px solid #ff4d4d40", borderLeft: "3px solid #ff4d4d", background: "rgba(255,77,77,0.05)", color: "#ff4d4d", fontFamily: "'Share Tech Mono', monospace", fontSize: "0.75rem", marginBottom: "2rem", width: "100%", maxWidth: 900 }}>
          ⚠ {backendError}
        </div>
      )}

      {loading && <div style={{ color: "#4d7a60", fontFamily: "'Share Tech Mono', monospace", fontSize: "0.85rem", letterSpacing: "0.2em" }}>SCANNING FOR ROBOTS...</div>}

      <div style={{ display: "flex", gap: "1.5rem", flexWrap: "wrap", justifyContent: "center", width: "100%", maxWidth: 900 }}>
        {robots.map(robot => {
          const sc = robot.online ? "#a8ff4d" : "#ff4d4d";
          const bat = robot.battery;
          const batColor = bat > 50 ? "#a8ff4d" : bat > 20 ? "#ffcc00" : "#ff4d4d";
          return (
            <div key={robot.id} style={{ position: "relative", background: "rgba(0,20,10,0.85)", border: `1px solid ${sc}30`, borderTop: `3px solid ${sc}`, padding: "1.8rem", flex: 1, minWidth: 260, maxWidth: 420, fontFamily: "'Barlow Condensed', sans-serif", clipPath: "polygon(0 0, calc(100% - 20px) 0, 100% 20px, 100% 100%, 0 100%)", boxShadow: `0 0 40px ${sc}15` }}>
              <div style={{ position: "absolute", top: 0, right: 0, width: 0, height: 0, borderTop: `20px solid ${sc}`, borderLeft: "20px solid transparent" }} />
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "1.5rem" }}>
                <div>
                  <div style={{ fontSize: "0.65rem", color: "#4d7a60", letterSpacing: "0.3em", marginBottom: "0.3rem" }}>NAO UNIT</div>
                  <div style={{ fontSize: "2rem", fontWeight: 900, color: "#fff", textTransform: "uppercase" }}>{robot.name}</div>
                </div>
                <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "0.3rem" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
                    <div style={{ width: 8, height: 8, borderRadius: "50%", background: sc, boxShadow: robot.online ? `0 0 10px ${sc}` : "none", animation: robot.online ? "pulse 2s infinite" : "none" }} />
                    <span style={{ fontSize: "0.7rem", color: sc, letterSpacing: "0.15em", fontFamily: "'Share Tech Mono', monospace" }}>{robot.online ? "ONLINE" : "OFFLINE"}</span>
                  </div>
                  <div style={{ fontSize: "0.6rem", color: "#4d7a60", fontFamily: "'Share Tech Mono', monospace" }}>{robot.ip || "NO IP"}</div>
                </div>
              </div>

              {/* Battery bar */}
              {robot.online && bat !== null ? (
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.3rem" }}>
                    <span style={{ fontSize: "0.65rem", color: "#4d7a60", letterSpacing: "0.2em" }}>BATTERY</span>
                    <span style={{ fontSize: "0.65rem", color: batColor, fontFamily: "'Share Tech Mono', monospace" }}>{bat}%</span>
                  </div>
                  <div style={{ height: 6, background: "#1a3a2a", borderRadius: 2, overflow: "hidden" }}>
                    <div style={{ height: "100%", width: bat + "%", background: batColor, boxShadow: `0 0 8px ${batColor}`, transition: "width 0.6s ease", borderRadius: 2 }} />
                  </div>
                </div>
              ) : (
                <div style={{ fontSize: "0.7rem", color: "#ff4d4d", fontFamily: "'Share Tech Mono', monospace" }}>{robot.error || "Unreachable"}</div>
              )}

              <div style={{ marginTop: "1.5rem", paddingTop: "1rem", borderTop: "1px solid #1a3a2a", display: "flex", justifyContent: "space-between" }}>
                <div>
                  <div style={{ fontSize: "0.6rem", color: "#4d7a60", letterSpacing: "0.2em" }}>TRAIT</div>
                  <div style={{ fontSize: "0.85rem", color: "#fff", textTransform: "uppercase", marginTop: "0.2rem" }}>{robot.trait || "—"}</div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: "0.6rem", color: "#4d7a60", letterSpacing: "0.2em" }}>MATCH STATE</div>
                  <div style={{ fontSize: "0.85rem", color: robot.status === "running" ? "#a8ff4d" : "#4d7a60", textTransform: "uppercase", marginTop: "0.2rem" }}>{robot.status}</div>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {!loading && (
        <button onClick={fetchStatus} style={{ marginTop: "2rem", background: "transparent", border: "1px solid #4d7a60", color: "#4d7a60", padding: "0.6rem 2rem", fontFamily: "'Barlow Condensed', sans-serif", fontSize: "0.8rem", letterSpacing: "0.2em", cursor: "pointer", textTransform: "uppercase" }}>
          ↻ REFRESH NOW
        </button>
      )}
    </>
  );
}

// ── App shell ────────────────────────────────────────────────
export default function App() {
  const [page, setPage]               = useState("config");
  const [matchRunning, setMatchRunning] = useState(false);
  const [robotsOnline, setRobotsOnline] = useState(false);

  return (
    <div style={{
      minHeight: "100vh", background: "#010d06",
      backgroundImage: `
        radial-gradient(ellipse at 20% 50%, rgba(0,60,20,0.3) 0%, transparent 60%),
        radial-gradient(ellipse at 80% 50%, rgba(0,40,60,0.3) 0%, transparent 60%),
        repeating-linear-gradient(0deg, transparent, transparent 39px, rgba(0,255,80,0.03) 39px, rgba(0,255,80,0.03) 40px),
        repeating-linear-gradient(90deg, transparent, transparent 39px, rgba(0,255,80,0.03) 39px, rgba(0,255,80,0.03) 40px)
      `,
      display: "flex", flexDirection: "column", alignItems: "center",
      padding: "3rem 1.5rem", fontFamily: "'Barlow Condensed', sans-serif",
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@300;400;500;600;700;900&family=Share+Tech+Mono&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        button:hover:not(:disabled) { filter: brightness(1.2); transform: translateY(-1px); }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
        @keyframes scanline { 0%{transform:translateY(-100%)} 100%{transform:translateY(100vh)} }
        @keyframes fadeUp { from{opacity:0;transform:translateY(20px)} to{opacity:1;transform:translateY(0)} }
      `}</style>

      <div style={{ position: "fixed", top: 0, left: 0, right: 0, height: "3px", background: "linear-gradient(transparent, rgba(0,255,80,0.06), transparent)", animation: "scanline 6s linear infinite", pointerEvents: "none", zIndex: 100 }} />

      <Nav page={page} setPage={setPage} robotsOnline={robotsOnline} />

      {page === "config" && <MatchConfig onMatchStart={setMatchRunning} />}
      {page === "status" && <RobotStatus onRobotsUpdate={setRobotsOnline} />}

      <div style={{ marginTop: "3rem", fontSize: "0.6rem", color: "#1a3a2a", letterSpacing: "0.3em", fontFamily: "'Share Tech Mono', monospace", textAlign: "center" }}>
        NAO FOOTBALL AI SYSTEM · HACKATHON BUILD · 2026{matchRunning ? " · MATCH LIVE" : ""}
      </div>
    </div>
  );
}