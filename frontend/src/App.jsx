import { useState } from "react";

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
  const [launched, setLaunched] = useState(false);

  const ready = p1.name && p2.name;

  return (
    <div style={{
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
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@300;400;500;600;700;900&family=Share+Tech+Mono&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        button:hover { filter: brightness(1.2); transform: translateY(-1px); }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }
        @keyframes scanline { 0%{transform:translateY(-100%)} 100%{transform:translateY(100vh)} }
        @keyframes fadeUp { from{opacity:0;transform:translateY(20px)} to{opacity:1;transform:translateY(0)} }
        @keyframes glitch {
          0%,100%{clip-path:inset(0 0 95% 0);transform:translate(-2px,0)}
          10%{clip-path:inset(40% 0 50% 0);transform:translate(2px,0)}
          20%{clip-path:inset(80% 0 10% 0);transform:translate(0,0)}
        }
      `}</style>

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
          MATCH <span style={{ color: "#a8ff4d" }}>CONFIG</span>
        </h1>
        <div style={{ width: 80, height: 2, background: "linear-gradient(90deg, transparent, #a8ff4d, transparent)", margin: "1rem auto" }} />
        <div style={{ fontSize: "0.75rem", color: "#4d7a60", letterSpacing: "0.2em", fontFamily: "'Share Tech Mono', monospace" }}>
          SELECT PLAYERS · ASSIGN ROLES · DEPLOY
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
            fontSize: "1.2rem", fontWeight: 900, color: "#1a3a2a",
            border: "1px solid #1a3a2a", padding: "0.4rem 0.6rem",
            letterSpacing: "0.1em",
          }}>VS</div>
          <div style={{ width: 1, flex: 1, background: "linear-gradient(transparent, #1a3a2a, transparent)" }} />
        </div>

        <PlayerCard number={2} player={p2} setPlayer={setP2} />
      </div>

      {/* Match summary */}
      {!launched && (
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

          <button
            disabled={!ready}
            onClick={() => setLaunched(true)}
            style={{
              background: ready ? "#a8ff4d" : "#1a3a2a",
              color: ready ? "#000" : "#4d7a60",
              border: "none",
              padding: "0.8rem 2.5rem",
              fontSize: "0.9rem",
              fontWeight: 700,
              letterSpacing: "0.25em",
              cursor: ready ? "pointer" : "not-allowed",
              fontFamily: "'Barlow Condensed', sans-serif",
              textTransform: "uppercase",
              transition: "all 0.2s",
              clipPath: "polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%)",
            }}>
            ▶ DEPLOY ROBOTS
          </button>
        </div>
      )}

      {/* Launch screen */}
      {launched && (
        <div style={{
          marginTop: "2rem",
          padding: "2.5rem",
          background: "rgba(0,30,15,0.95)",
          border: "1px solid #a8ff4d",
          width: "100%",
          maxWidth: 900,
          textAlign: "center",
          animation: "fadeUp 0.4s ease",
        }}>
          <div style={{ fontSize: "0.7rem", letterSpacing: "0.4em", color: "#4d7a60", fontFamily: "'Share Tech Mono', monospace", marginBottom: "0.8rem" }}>
            MATCH INITIALIZED
          </div>
          <div style={{ fontSize: "2rem", fontWeight: 900, color: "#a8ff4d", letterSpacing: "0.1em", marginBottom: "1.5rem" }}>
            ⚽ ROBOTS DEPLOYING TO PITCH
          </div>

          {/* Config payload preview */}
          <div style={{
            background: "rgba(0,0,0,0.5)",
            border: "1px solid #1a3a2a",
            padding: "1rem 1.5rem",
            textAlign: "left",
            fontFamily: "'Share Tech Mono', monospace",
            fontSize: "0.75rem",
            color: "#4d7a60",
            lineHeight: 1.8,
            marginBottom: "1.5rem",
          }}>
            <span style={{ color: "#a8ff4d" }}>{"{"}</span><br />
            &nbsp;&nbsp;<span style={{ color: "#4d9fff" }}>"player1"</span>: {"{"} name: <span style={{ color: "#ffcc00" }}>"{p1.name}"</span>, mode: <span style={{ color: modeColors[p1.mode] }}>"{p1.mode}"</span>, difficulty: <span style={{ color: diffColors[p1.difficulty] }}>"{p1.difficulty}"</span> {"}"}<br />
            &nbsp;&nbsp;<span style={{ color: "#4d9fff" }}>"player2"</span>: {"{"} name: <span style={{ color: "#ffcc00" }}>"{p2.name}"</span>, mode: <span style={{ color: modeColors[p2.mode] }}>"{p2.mode}"</span>, difficulty: <span style={{ color: diffColors[p2.difficulty] }}>"{p2.difficulty}"</span> {"}"}<br />
            <span style={{ color: "#a8ff4d" }}>{"}"}</span>
          </div>

          <button onClick={() => setLaunched(false)} style={{
            background: "transparent", border: "1px solid #4d7a60",
            color: "#4d7a60", padding: "0.6rem 2rem",
            fontFamily: "'Barlow Condensed', sans-serif",
            fontSize: "0.8rem", letterSpacing: "0.2em",
            cursor: "pointer", textTransform: "uppercase",
          }}>← RECONFIGURE</button>
        </div>
      )}

      {/* Footer */}
      <div style={{ marginTop: "3rem", fontSize: "0.6rem", color: "#1a3a2a", letterSpacing: "0.3em", fontFamily: "'Share Tech Mono', monospace", textAlign: "center" }}>
        NAO FOOTBALL AI SYSTEM · HACKATHON BUILD · 2026
      </div>
    </div>
  );
}
