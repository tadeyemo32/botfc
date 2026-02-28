import { useState, useEffect, useRef } from "react";

function TelemetryHUD() {
  const [telemetry, setTelemetry] = useState({
    state: "IDLE",
    kicks: 0,
    ball_age: -1,
    break_remaining: 0,
    trait: "none",
    robot_connected: false,
  });
  const [connected, setConnected] = useState(false);
  const [robotIp, setRobotIp] = useState("");
  const [testing, setTesting] = useState(false);
  const ws = useRef(null);

  useEffect(() => {
    // Fetch initial robot IP
    fetch("http://localhost:5050/api/robot/config")
      .then(r => r.json())
      .then(data => setRobotIp(data.ip))
      .catch(err => console.error("Failed to fetch robot IP:", err));

    const connect = () => {
      ws.current = new WebSocket("ws://localhost:5050/api/ws/frontend");
      ws.current.onopen = () => setConnected(true);
      ws.current.onclose = () => {
        setConnected(false);
        setTimeout(connect, 2000);
      };
      ws.current.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          setTelemetry((prev) => ({ ...prev, ...data }));
        } catch (err) {
          console.error("WS Parse Error:", err);
        }
      };
    };
    connect();
    return () => ws.current?.close();
  }, []);

  const handleTestConnection = async () => {
    setTesting(true);
    try {
      // First save the IP
      await fetch("http://localhost:5050/api/robot/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ip: robotIp }),
      });
      // Then trigger test
      await fetch("http://localhost:5050/api/robot/test", { method: "POST" });
    } catch (e) {
      console.error("Test failed:", e);
    }
    setTimeout(() => setTesting(false), 2000);
  };

  const stateColors = {
    IDLE: "#4d7a60",
    SEARCH: "#ffcc00",
    APPROACH: "#a8ff4d",
    ALIGN: "#4d9fff",
    TACKLE: "#ff4d4d",
    KICK: "#fff",
    HALFTIME: "#ff6600",
  };

  return (
    <div
      style={{
        position: "fixed",
        top: 20,
        right: 20,
        zIndex: 1000,
        width: 300,
        background: "rgba(0,15,5,0.9)",
        border: `1px solid ${connected ? "#a8ff4d40" : "#ff4d4d40"}`,
        borderLeft: `4px solid ${stateColors[telemetry.state] || "#4d7a60"}`,
        padding: "1rem",
        fontFamily: "'Share Tech Mono', monospace",
        color: "#fff",
        boxShadow: "0 10px 30px rgba(0,0,0,0.5)",
        backdropFilter: "blur(10px)",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          fontSize: "0.6rem",
          letterSpacing: "0.2em",
          color: "#4d7a60",
          marginBottom: "0.8rem",
        }}
      >
        <span>TELEMETRY STREAM</span>
        <span style={{ color: connected ? "#a8ff4d" : "#ff4d4d" }}>
          {connected ? "ONLINE" : "OFFLINE"}
        </span>
      </div>

      {/* Robot Connection Block */}
      <div style={{
        marginBottom: "1.2rem",
        padding: "0.8rem",
        background: "rgba(0,40,20,0.3)",
        border: "1px solid #1a3a2a"
      }}>
        <div style={{ fontSize: "0.6rem", color: "#4d7a60", marginBottom: "0.5rem" }}>
          ROBOT LINK: <span style={{ color: telemetry.robot_connected ? "#a8ff4d" : "#ff4d4d" }}>
            {telemetry.robot_connected ? "LINKED" : "UNLINKED"}
          </span>
        </div>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <input
            value={robotIp}
            onChange={e => setRobotIp(e.target.value)}
            placeholder="ROBOT IP"
            style={{
              flex: 1,
              background: "#000",
              border: "1px solid #1a3a2a",
              color: "#a8ff4d",
              fontSize: "0.75rem",
              padding: "0.3rem",
              fontFamily: "'Share Tech Mono', monospace",
              outline: "none"
            }}
          />
          <button
            onClick={handleTestConnection}
            disabled={testing}
            style={{
              background: "#1a3a2a",
              color: "#fff",
              border: "none",
              padding: "0 0.6rem",
              fontSize: "0.6rem",
              cursor: "pointer",
              fontFamily: "'Share Tech Mono', monospace"
            }}
          >
            {testing ? "..." : "SYNC"}
          </button>
        </div>
      </div>

      <div style={{ marginBottom: "1rem" }}>
        <div style={{ fontSize: "0.65rem", color: "#4d7a60", marginBottom: 2 }}>
          CURRENT STATE
        </div>
        <div
          style={{
            fontSize: "1.4rem",
            fontWeight: 700,
            color: stateColors[telemetry.state] || "#fff",
            letterSpacing: "0.05em",
          }}
        >
          {telemetry.state}
        </div>
      </div>

      <div style={{ display: "flex", gap: "1rem" }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: "0.6rem", color: "#4d7a60", marginBottom: 2 }}>
            KICKS
          </div>
          <div style={{ fontSize: "1.2rem", fontWeight: 700 }}>
            {telemetry.kicks}
          </div>
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: "0.6rem", color: "#4d7a60", marginBottom: 2 }}>
            BALL AGE
          </div>
          <div
            style={{
              fontSize: "1.2rem",
              fontWeight: 700,
              color:
                telemetry.ball_age > 10 || telemetry.ball_age < 0
                  ? "#ff4d4d"
                  : "#a8ff4d",
            }}
          >
            {telemetry.ball_age < 0 ? "N/A" : `${telemetry.ball_age.toFixed(1)}s`}
          </div>
        </div>
      </div>

      {telemetry.break_remaining > 0 && (
        <div
          style={{
            marginTop: "1rem",
            padding: "0.5rem",
            background: "#ff660020",
            border: "1px solid #ff6600",
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: "0.6rem", color: "#ff6600" }}>
            COOLING DOWN...
          </div>
          <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "#ff6600" }}>
            {telemetry.break_remaining}s
          </div>
        </div>
      )}
    </div>
  );
}

const MODES = ["offense", "defense", "balanced"];
const DIFFICULTIES = ["easy", "medium", "hard"];

const modeIcons = { offense: "⚡", defense: "🛡", balanced: "⚖" };
const diffIcons = { easy: "●", medium: "●●", hard: "●●●" };

const modeColors = {
  offense: "#ff4d4d",
  defense: "#4d9fff",
  balanced: "#a8ff4d",
};

const diffColors = {
  easy: "#a8ff4d",
  medium: "#ffcc00",
  hard: "#ff4d4d",
};

function PlayerCard({ number, player, setPlayer }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(player.name);

  const selectedModeColor = modeColors[player.mode];
  const selectedDiffColor = diffColors[player.difficulty];

  return (
    <div
      style={{
        position: "relative",
        background: "rgba(0,20,10,0.85)",
        border: `1px solid ${selectedModeColor}40`,
        borderTop: `3px solid ${selectedModeColor}`,
        padding: "2rem",
        flex: 1,
        minWidth: 280,
        maxWidth: 420,
        fontFamily: "'Barlow Condensed', sans-serif",
        clipPath: "polygon(0 0, calc(100% - 20px) 0, 100% 20px, 100% 100%, 0 100%)",
        boxShadow: `0 0 40px ${selectedModeColor}20, inset 0 0 60px rgba(0,0,0,0.4)`,
        transition: "all 0.3s ease",
      }}
    >
      {/* Corner notch accent */}
      <div style={{
        position: "absolute", top: 0, right: 0,
        width: 0, height: 0,
        borderTop: `20px solid ${selectedModeColor}`,
        borderLeft: "20px solid transparent",
      }} />

      {/* Player number badge */}
      <div style={{
        position: "absolute", top: "1.5rem", right: "2.5rem",
        fontSize: "4rem", fontWeight: 900, color: `${selectedModeColor}20`,
        lineHeight: 1, userSelect: "none",
        fontFamily: "'Barlow Condensed', sans-serif",
      }}>P{number}</div>

      {/* Label */}
      <div style={{
        fontSize: "0.7rem", letterSpacing: "0.3em", color: "#4d7a60",
        textTransform: "uppercase", marginBottom: "0.5rem",
        fontFamily: "'Barlow Condensed', sans-serif",
      }}>PLAYER {number} — NAO UNIT</div>

      {/* Name */}
      <div style={{ marginBottom: "2rem" }}>
        {editing ? (
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
            <input
              autoFocus
              value={draft}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={e => {
                if (e.key === "Enter") {
                  setPlayer({ ...player, name: draft || `Player ${number}` });
                  setEditing(false);
                }
                if (e.key === "Escape") { setDraft(player.name); setEditing(false); }
              }}
              style={{
                background: "transparent",
                border: "none",
                borderBottom: `2px solid ${selectedModeColor}`,
                color: "#fff",
                fontSize: "1.8rem",
                fontWeight: 700,
                fontFamily: "'Barlow Condensed', sans-serif",
                outline: "none",
                width: "100%",
                textTransform: "uppercase",
                letterSpacing: "0.05em",
              }}
            />
            <button onClick={() => { setPlayer({ ...player, name: draft || `Player ${number}` }); setEditing(false); }}
              style={{ background: selectedModeColor, border: "none", color: "#000", fontWeight: 700, padding: "0.3rem 0.7rem", cursor: "pointer", fontSize: "0.75rem", fontFamily: "'Barlow Condensed', sans-serif", letterSpacing: "0.1em" }}>
              SET
            </button>
          </div>
        ) : (
          <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
            <div style={{
              fontSize: "1.8rem", fontWeight: 700, color: "#fff",
              letterSpacing: "0.05em", textTransform: "uppercase",
              fontFamily: "'Barlow Condensed', sans-serif",
            }}>{player.name}</div>
            <button onClick={() => { setDraft(player.name); setEditing(true); }}
              style={{ background: "transparent", border: `1px solid #4d7a60`, color: "#4d7a60", padding: "0.2rem 0.6rem", cursor: "pointer", fontSize: "0.65rem", letterSpacing: "0.15em", fontFamily: "'Barlow Condensed', sans-serif" }}>
              EDIT
            </button>
          </div>
        )}
      </div>

      {/* Mode selector */}
      <div style={{ marginBottom: "1.5rem" }}>
        <div style={{ fontSize: "0.65rem", letterSpacing: "0.25em", color: "#4d7a60", marginBottom: "0.6rem" }}>PLAY STYLE</div>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          {MODES.map(m => (
            <button key={m} onClick={() => setPlayer({ ...player, mode: m })}
              style={{
                flex: 1,
                background: player.mode === m ? `${modeColors[m]}20` : "transparent",
                border: `1px solid ${player.mode === m ? modeColors[m] : "#1a3a2a"}`,
                color: player.mode === m ? modeColors[m] : "#4d7a60",
                padding: "0.6rem 0.3rem",
                cursor: "pointer",
                fontFamily: "'Barlow Condensed', sans-serif",
                fontSize: "0.75rem",
                letterSpacing: "0.1em",
                textTransform: "uppercase",
                transition: "all 0.2s",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: "0.2rem",
              }}>
              <span style={{ fontSize: "1.1rem" }}>{modeIcons[m]}</span>
              {m}
            </button>
          ))}
        </div>
      </div>

      {/* Difficulty selector */}
      <div>
        <div style={{ fontSize: "0.65rem", letterSpacing: "0.25em", color: "#4d7a60", marginBottom: "0.6rem" }}>DIFFICULTY</div>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          {DIFFICULTIES.map(d => (
            <button key={d} onClick={() => setPlayer({ ...player, difficulty: d })}
              style={{
                flex: 1,
                background: player.difficulty === d ? `${diffColors[d]}18` : "transparent",
                border: `1px solid ${player.difficulty === d ? diffColors[d] : "#1a3a2a"}`,
                color: player.difficulty === d ? diffColors[d] : "#4d7a60",
                padding: "0.6rem 0.3rem",
                cursor: "pointer",
                fontFamily: "'Barlow Condensed', sans-serif",
                fontSize: "0.7rem",
                letterSpacing: "0.1em",
                textTransform: "uppercase",
                transition: "all 0.2s",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: "0.2rem",
              }}>
              <span style={{ fontSize: "0.8rem", letterSpacing: "0.05em" }}>{diffIcons[d]}</span>
              {d}
            </button>
          ))}
        </div>
      </div>

      {/* Status bar */}
      <div style={{
        marginTop: "1.5rem", paddingTop: "1rem",
        borderTop: "1px solid #1a3a2a",
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <div style={{ fontSize: "0.65rem", color: "#4d7a60", letterSpacing: "0.15em" }}>STATUS</div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
          <div style={{ width: 6, height: 6, borderRadius: "50%", background: selectedModeColor, boxShadow: `0 0 8px ${selectedModeColor}` }} />
          <span style={{ fontSize: "0.65rem", color: selectedModeColor, letterSpacing: "0.15em" }}>CONFIGURED</span>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [p1, setP1] = useState({ name: "ATLAS", mode: "offense", difficulty: "medium" });
  const [p2, setP2] = useState({ name: "ARES", mode: "defense", difficulty: "medium" });
  const [matchState, setMatchState] = useState("config"); // config | deploying | live | error
  const [deployStatus, setDeployStatus] = useState("");
  const [errorMsg, setErrorMsg] = useState("");

  const handleDeploy = async (playerKey) => {
    setMatchState("deploying");
    setDeployStatus(`Deploying ${playerKey === "BOTH" ? "both players" : playerKey}...`);
    setErrorMsg("");

    try {
      const payload = {};
      if (playerKey === "P1" || playerKey === "BOTH") payload.player1 = p1;
      if (playerKey === "P2" || playerKey === "BOTH") payload.player2 = p2;

      const res = await fetch("http://localhost:5050/api/start_match", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) throw new Error(`Server returned ${res.status}`);
      setDeployStatus("Brain deployed to robot. Match is LIVE!");
      setMatchState("live");
    } catch (e) {
      console.error("Deploy failed:", e);
      setErrorMsg(`Deploy failed: ${e.message}`);
      setMatchState("error");
    }
  };

  const handleStop = async () => {
    setDeployStatus("Stopping match...");
    try {
      await fetch("http://localhost:5050/api/stop_match", { method: "POST" });
      setMatchState("config");
      setDeployStatus("");
    } catch (e) {
      console.error("Stop failed:", e);
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        background: "#010d06",
        backgroundImage: `
        radial-gradient(ellipse at 20% 50%, rgba(0,60,20,0.3) 0%, transparent 60%),
        radial-gradient(ellipse at 80% 50%, rgba(0,40,60,0.3) 0%, transparent 60%),
        repeating-linear-gradient(0deg, transparent, transparent 39px, rgba(0,255,80,0.03) 39px, rgba(0,255,80,0.03) 40px),
        repeating-linear-gradient(90deg, transparent, transparent 39px, rgba(0,255,80,0.03) 39px, rgba(0,255,80,0.03) 40px)
      `,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        padding: "3rem 1.5rem",
        fontFamily: "'Barlow Condensed', sans-serif",
      }}
    >
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@300;400;500;600;700;900&family=Share+Tech+Mono&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        button:hover { filter: brightness(1.2); transform: translateY(-1px); }
        button:active { transform: translateY(0); }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }
        @keyframes scanline { 0%{transform:translateY(-100%)} 100%{transform:translateY(100vh)} }
        @keyframes fadeUp { from{opacity:0;transform:translateY(20px)} to{opacity:1;transform:translateY(0)} }
        @keyframes livePulse { 0%,100%{box-shadow:0 0 20px rgba(168,255,77,0.3)} 50%{box-shadow:0 0 40px rgba(168,255,77,0.6)} }
      `}</style>

      {/* Telemetry HUD */}
      <TelemetryHUD />

      {/* Scanline overlay */}
      <div style={{
        position: "fixed", top: 0, left: 0, right: 0, height: "3px",
        background: "linear-gradient(transparent, rgba(0,255,80,0.06), transparent)",
        animation: "scanline 6s linear infinite", pointerEvents: "none", zIndex: 100,
      }} />

      {/* Header */}
      <div style={{ textAlign: "center", marginBottom: "3rem", animation: "fadeUp 0.6s ease" }}>
        <div style={{ fontSize: "0.7rem", letterSpacing: "0.5em", color: "#4d7a60", marginBottom: "0.5rem", fontFamily: "'Share Tech Mono', monospace" }}>
          NAO ROBOTICS // FOOTBALL CONTROL SYSTEM v2.1
        </div>
        <h1 style={{
          fontSize: "clamp(2.5rem, 6vw, 4.5rem)",
          fontWeight: 900,
          color: "#fff",
          letterSpacing: "0.05em",
          textTransform: "uppercase",
          lineHeight: 0.9,
          position: "relative",
        }}>
          {matchState === "live" ? (
            <>MATCH <span style={{ color: "#a8ff4d" }}>LIVE</span></>
          ) : (
            <>MATCH <span style={{ color: "#a8ff4d" }}>CONFIG</span></>
          )}
        </h1>
        <div style={{ width: 80, height: 2, background: "linear-gradient(90deg, transparent, #a8ff4d, transparent)", margin: "1rem auto" }} />
        <div style={{ fontSize: "0.75rem", color: "#4d7a60", letterSpacing: "0.2em", fontFamily: "'Share Tech Mono', monospace" }}>
          {matchState === "live" ? "ROBOTS ON THE PITCH · MONITORING" : "SELECT PLAYERS · ASSIGN ROLES · DEPLOY"}
        </div>
      </div>

      {/* VS layout */}
      <div style={{
        display: "flex",
        gap: "1rem",
        alignItems: "stretch",
        width: "100%",
        maxWidth: 900,
        animation: "fadeUp 0.6s ease 0.2s both",
        flexWrap: "wrap",
        justifyContent: "center",
      }}>
        <PlayerCard number={1} player={p1} setPlayer={setP1} />

        {/* VS divider */}
        <div style={{
          display: "flex", flexDirection: "column", alignItems: "center",
          justifyContent: "center", gap: "0.5rem", padding: "0 0.5rem",
          minWidth: 50,
        }}>
          <div style={{ width: 1, flex: 1, background: "linear-gradient(transparent, #1a3a2a, transparent)" }} />
          <div style={{
            fontSize: "1.2rem", fontWeight: 900, color: matchState === "live" ? "#a8ff4d" : "#1a3a2a",
            border: `1px solid ${matchState === "live" ? "#a8ff4d" : "#1a3a2a"}`, padding: "0.4rem 0.6rem",
            letterSpacing: "0.1em",
            transition: "all 0.3s",
          }}>VS</div>
          <div style={{ width: 1, flex: 1, background: "linear-gradient(transparent, #1a3a2a, transparent)" }} />
        </div>

        <PlayerCard number={2} player={p2} setPlayer={setP2} />
      </div>

      {/* Error message */}
      {matchState === "error" && (
        <div style={{
          marginTop: "1.5rem", padding: "1rem 2rem",
          background: "rgba(255,0,0,0.1)", border: "1px solid #ff4d4d",
          width: "100%", maxWidth: 900, textAlign: "center",
          fontFamily: "'Share Tech Mono', monospace", fontSize: "0.8rem",
          color: "#ff4d4d", animation: "fadeUp 0.3s ease",
        }}>
          ⚠ {errorMsg}
          <button onClick={() => setMatchState("config")} style={{
            marginLeft: "1rem", background: "transparent", border: "1px solid #ff4d4d",
            color: "#ff4d4d", padding: "0.3rem 1rem", cursor: "pointer",
            fontFamily: "'Share Tech Mono', monospace", fontSize: "0.7rem",
          }}>DISMISS</button>
        </div>
      )}

      {/* Deploy controls (when in config mode) */}
      {(matchState === "config" || matchState === "error") && (
        <div style={{
          marginTop: "2rem",
          padding: "1.2rem 2rem",
          background: "rgba(0,20,10,0.8)",
          border: "1px solid #1a3a2a",
          borderBottom: "2px solid #a8ff4d",
          width: "100%",
          maxWidth: 900,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "1rem",
          animation: "fadeUp 0.6s ease 0.4s both",
          fontFamily: "'Share Tech Mono', monospace",
        }}>
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
            <button
              onClick={() => handleDeploy('P1')}
              style={{
                background: "#a8ff4d", color: "#000", border: "none",
                padding: "0.8rem 1.8rem", fontSize: "0.8rem", fontWeight: 700,
                letterSpacing: "0.2em", cursor: "pointer",
                fontFamily: "'Barlow Condensed', sans-serif", textTransform: "uppercase",
                transition: "all 0.2s",
                clipPath: "polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%)",
              }}>
              ▶ P1
            </button>
            <button
              onClick={() => handleDeploy('BOTH')}
              style={{
                background: "linear-gradient(135deg, #a8ff4d, #4d9fff)", color: "#000", border: "none",
                padding: "0.8rem 2.5rem", fontSize: "0.9rem", fontWeight: 700,
                letterSpacing: "0.25em", cursor: "pointer",
                fontFamily: "'Barlow Condensed', sans-serif", textTransform: "uppercase",
                transition: "all 0.2s",
                clipPath: "polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%)",
              }}>
              ⚽ KICK OFF
            </button>
            <button
              onClick={() => handleDeploy('P2')}
              style={{
                background: "#4d9fff", color: "#000", border: "none",
                padding: "0.8rem 1.8rem", fontSize: "0.8rem", fontWeight: 700,
                letterSpacing: "0.2em", cursor: "pointer",
                fontFamily: "'Barlow Condensed', sans-serif", textTransform: "uppercase",
                transition: "all 0.2s",
                clipPath: "polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%)",
              }}>
              ▶ P2
            </button>
          </div>
        </div>
      )}

      {/* Deploying spinner */}
      {matchState === "deploying" && (
        <div style={{
          marginTop: "2rem", padding: "2.5rem",
          background: "rgba(0,30,15,0.95)", border: "1px solid #ffcc00",
          width: "100%", maxWidth: 900, textAlign: "center",
          animation: "fadeUp 0.4s ease",
        }}>
          <div style={{ fontSize: "0.7rem", letterSpacing: "0.4em", color: "#ffcc00", fontFamily: "'Share Tech Mono', monospace", marginBottom: "0.8rem", animation: "pulse 1.5s infinite" }}>
            DEPLOYING TO ROBOT
          </div>
          <div style={{ fontSize: "1.6rem", fontWeight: 900, color: "#ffcc00", letterSpacing: "0.1em" }}>
            ⚡ {deployStatus}
          </div>
        </div>
      )}

      {/* Live match panel */}
      {matchState === "live" && (
        <div style={{
          marginTop: "2rem", padding: "2.5rem",
          background: "rgba(0,30,15,0.95)", border: "1px solid #a8ff4d",
          width: "100%", maxWidth: 900, textAlign: "center",
          animation: "fadeUp 0.4s ease, livePulse 3s infinite",
        }}>
          <div style={{ fontSize: "0.7rem", letterSpacing: "0.4em", color: "#4d7a60", fontFamily: "'Share Tech Mono', monospace", marginBottom: "0.8rem" }}>
            MATCH IN PROGRESS
          </div>
          <div style={{ fontSize: "2rem", fontWeight: 900, color: "#a8ff4d", letterSpacing: "0.1em", marginBottom: "1.5rem" }}>
            ⚽ ROBOTS ON THE PITCH
          </div>

          {/* Live config preview */}
          <div style={{
            background: "rgba(0,0,0,0.5)", border: "1px solid #1a3a2a",
            padding: "1rem 1.5rem", textAlign: "left",
            fontFamily: "'Share Tech Mono', monospace", fontSize: "0.75rem",
            color: "#4d7a60", lineHeight: 1.8, marginBottom: "1.5rem",
          }}>
            <span style={{ color: "#a8ff4d" }}>{"{"}</span><br />
            &nbsp;&nbsp;<span style={{ color: "#4d9fff" }}>"player1"</span>: {"{"} name: <span style={{ color: "#ffcc00" }}>"{p1.name}"</span>, mode: <span style={{ color: modeColors[p1.mode] }}>"{p1.mode}"</span>, difficulty: <span style={{ color: diffColors[p1.difficulty] }}>"{p1.difficulty}"</span> {"}"}<br />
            &nbsp;&nbsp;<span style={{ color: "#4d9fff" }}>"player2"</span>: {"{"} name: <span style={{ color: "#ffcc00" }}>"{p2.name}"</span>, mode: <span style={{ color: modeColors[p2.mode] }}>"{p2.mode}"</span>, difficulty: <span style={{ color: diffColors[p2.difficulty] }}>"{p2.difficulty}"</span> {"}"}<br />
            <span style={{ color: "#a8ff4d" }}>{"}"}</span>
          </div>

          <div style={{ display: "flex", gap: "1rem", justifyContent: "center" }}>
            <button onClick={handleStop} style={{
              background: "#ff4d4d", border: "none", color: "#fff",
              padding: "0.8rem 2.5rem", fontFamily: "'Barlow Condensed', sans-serif",
              fontSize: "0.9rem", fontWeight: 700, letterSpacing: "0.25em",
              cursor: "pointer", textTransform: "uppercase", transition: "all 0.2s",
              clipPath: "polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%)",
            }}>■ STOP MATCH</button>
            <button onClick={() => setMatchState("config")} style={{
              background: "transparent", border: "1px solid #4d7a60",
              color: "#4d7a60", padding: "0.8rem 2rem",
              fontFamily: "'Barlow Condensed', sans-serif",
              fontSize: "0.8rem", letterSpacing: "0.2em",
              cursor: "pointer", textTransform: "uppercase",
            }}>← RECONFIGURE</button>
          </div>
        </div>
      )}

      {/* Footer */}
      <div style={{ marginTop: "3rem", fontSize: "0.6rem", color: "#1a3a2a", letterSpacing: "0.3em", fontFamily: "'Share Tech Mono', monospace", textAlign: "center" }}>
        NAO FOOTBALL AI SYSTEM · HACKATHON BUILD · 2026
      </div>
    </div>
  );
}
