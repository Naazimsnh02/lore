import { useState, useEffect, useRef } from "react";

const LORE_STYLES = `
  @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300;0,400;0,600;1,300;1,400&family=DM+Mono:wght@300;400&family=Syne:wght@400;600;700;800&display=swap');

  * { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --black: #080706;
    --deep: #0d0b09;
    --amber: #c8922a;
    --amber-dim: #8a621c;
    --gold: #e8c86e;
    --cream: #f2ead8;
    --fog: #b8ab94;
    --charcoal: #1a1714;
    --charcoal2: #252119;
    --rust: #8b3a1e;
    --film: rgba(200,146,42,0.06);
  }

  body { background: var(--black); color: var(--cream); font-family: 'Syne', sans-serif; }

  .grain {
    position: fixed; inset: 0; pointer-events: none; z-index: 9999; opacity: 0.035;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)'/%3E%3C/svg%3E");
    background-size: 128px;
  }

  @keyframes fadeUp {
    from { opacity: 0; transform: translateY(32px); }
    to { opacity: 1; transform: translateY(0); }
  }
  @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
  @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.4; } }
  @keyframes scanline {
    0% { transform: translateY(-100%); }
    100% { transform: translateY(100vh); }
  }
  @keyframes shimmer {
    0% { background-position: -200% center; }
    100% { background-position: 200% center; }
  }
  @keyframes orbit {
    from { transform: rotate(0deg) translateX(140px) rotate(0deg); }
    to { transform: rotate(360deg) translateX(140px) rotate(-360deg); }
  }
  @keyframes float {
    0%,100% { transform: translateY(0px); }
    50% { transform: translateY(-8px); }
  }
  @keyframes waveform {
    0%,100% { height: 4px; }
    50% { height: 20px; }
  }
  @keyframes typewriter {
    from { width: 0; }
    to { width: 100%; }
  }
  @keyframes blink { 0%,100%{opacity:1;} 50%{opacity:0;} }
  @keyframes gradientShift {
    0% { background-position: 0% 50%; }
    50% { background-position: 100% 50%; }
    100% { background-position: 0% 50%; }
  }
`;

const GrainOverlay = () => <div className="grain" />;

const NAV_SECTIONS = ["Concept", "Modes", "Experience", "Architecture", "Stack"];

function Navbar({ active, onNav }) {
  return (
    <nav style={{
      position: "fixed", top: 0, left: 0, right: 0, zIndex: 100,
      display: "flex", alignItems: "center", justifyContent: "space-between",
      padding: "20px 48px",
      background: "linear-gradient(to bottom, rgba(8,7,6,0.95), transparent)",
      backdropFilter: "blur(12px)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{
          width: 32, height: 32, borderRadius: "50%",
          background: "radial-gradient(circle at 35% 35%, #e8c86e, #c8922a, #5a3a0a)",
          boxShadow: "0 0 20px rgba(200,146,42,0.5)",
          animation: "float 4s ease-in-out infinite"
        }} />
        <span style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 22, fontWeight: 300, letterSpacing: 6, color: "#e8c86e" }}>
          LORE
        </span>
      </div>
      <div style={{ display: "flex", gap: 32 }}>
        {NAV_SECTIONS.map(s => (
          <button key={s} onClick={() => onNav(s)}
            style={{
              background: "none", border: "none", cursor: "pointer",
              fontFamily: "'Syne', sans-serif", fontSize: 11, letterSpacing: 3,
              textTransform: "uppercase",
              color: active === s ? "#c8922a" : "#b8ab94",
              transition: "color 0.3s",
              borderBottom: active === s ? "1px solid #c8922a" : "1px solid transparent",
              paddingBottom: 2
            }}
          >{s}</button>
        ))}
      </div>
      <div style={{
        fontFamily: "'DM Mono', monospace", fontSize: 10, color: "#8a621c", letterSpacing: 2
      }}>HACKATHON BUILD v1.0</div>
    </nav>
  );
}

function Hero({ onNav }) {
  const [typed, setTyped] = useState("");
  const phrases = [
    "Point at the Colosseum...",
    "Ask about the 1918 pandemic...",
    "Stand where empires fell...",
    "Hear the voices of history...",
  ];
  const [pIdx, setPIdx] = useState(0);

  useEffect(() => {
    let i = 0;
    let dir = 1;
    let current = phrases[pIdx];
    const interval = setInterval(() => {
      if (dir === 1) {
        setTyped(current.slice(0, i));
        i++;
        if (i > current.length) { dir = -1; setTimeout(() => {}, 1200); }
      } else {
        setTyped(current.slice(0, i));
        i--;
        if (i < 0) {
          dir = 1; i = 0;
          setPIdx(p => (p + 1) % phrases.length);
          current = phrases[(pIdx + 1) % phrases.length];
        }
      }
    }, 55);
    return () => clearInterval(interval);
  }, [pIdx]);

  return (
    <section id="Concept" style={{
      minHeight: "100vh", display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center",
      position: "relative", overflow: "hidden",
      background: "radial-gradient(ellipse 80% 60% at 50% 60%, #1a100400, #080706)",
      paddingTop: 100
    }}>
      {/* Cinematic light beam */}
      <div style={{
        position: "absolute", top: 0, left: "50%", transform: "translateX(-50%)",
        width: 2, height: "45%",
        background: "linear-gradient(to bottom, rgba(200,146,42,0.6), transparent)",
        filter: "blur(8px)"
      }} />
      <div style={{
        position: "absolute", top: 0, left: "50%", transform: "translateX(-50%)",
        width: 300, height: "55%",
        background: "radial-gradient(ellipse at top, rgba(200,146,42,0.12), transparent 70%)",
      }} />

      {/* Floating orbs */}
      {[[-320, -120, 200, 0.3], [280, 80, 160, 0.2], [-180, 200, 120, 0.15]].map(([x, y, s, o], i) => (
        <div key={i} style={{
          position: "absolute",
          left: `calc(50% + ${x}px)`, top: `calc(50% + ${y}px)`,
          width: s, height: s, borderRadius: "50%",
          background: `radial-gradient(circle, rgba(200,146,42,${o}), transparent)`,
          filter: "blur(40px)",
          animation: `float ${4 + i}s ease-in-out infinite`,
          animationDelay: `${i * 0.8}s`
        }} />
      ))}

      <div style={{ textAlign: "center", position: "relative", zIndex: 2, animation: "fadeUp 1.2s ease both" }}>
        <div style={{
          fontFamily: "'DM Mono', monospace", fontSize: 10, letterSpacing: 6,
          color: "#c8922a", textTransform: "uppercase", marginBottom: 32
        }}>
          ◆ &nbsp; Gemini Live Agent Challenge &nbsp; ◆
        </div>

        <h1 style={{
          fontFamily: "'Cormorant Garamond', serif", fontWeight: 300,
          fontSize: "clamp(80px, 14vw, 160px)", lineHeight: 0.9,
          letterSpacing: -2, color: "#f2ead8",
          textShadow: "0 0 80px rgba(200,146,42,0.3)"
        }}>
          LORE
        </h1>

        <div style={{
          fontFamily: "'Cormorant Garamond', serif", fontStyle: "italic",
          fontSize: "clamp(18px, 3vw, 28px)", color: "#c8922a",
          marginTop: 8, marginBottom: 48, fontWeight: 300, letterSpacing: 4
        }}>
          The World Is Your Documentary
        </div>

        <div style={{
          fontFamily: "'DM Mono', monospace", fontSize: 14,
          color: "#b8ab94", height: 24, letterSpacing: 1
        }}>
          {typed}<span style={{ animation: "blink 1s infinite", color: "#c8922a" }}>|</span>
        </div>

        <p style={{
          maxWidth: 560, margin: "40px auto 0",
          fontFamily: "'Syne', sans-serif", fontSize: 15, lineHeight: 1.8,
          color: "#8a7a62", fontWeight: 400
        }}>
          Point your camera at any place in the world. Speak any question. LORE generates a
          living documentary — narrated, illustrated, cinematic — in real time.
          History has never been this immersive.
        </p>

        <div style={{ display: "flex", gap: 16, justifyContent: "center", marginTop: 48 }}>
          <button onClick={() => onNav("Modes")} style={{
            padding: "14px 36px", background: "#c8922a", border: "none",
            fontFamily: "'Syne', sans-serif", fontSize: 11, letterSpacing: 3,
            textTransform: "uppercase", color: "#080706", cursor: "pointer",
            fontWeight: 700, transition: "all 0.3s",
          }}
            onMouseOver={e => e.target.style.background = "#e8c86e"}
            onMouseOut={e => e.target.style.background = "#c8922a"}
          >
            Explore Modes
          </button>
          <button onClick={() => onNav("Architecture")} style={{
            padding: "14px 36px", background: "transparent",
            border: "1px solid rgba(200,146,42,0.4)",
            fontFamily: "'Syne', sans-serif", fontSize: 11, letterSpacing: 3,
            textTransform: "uppercase", color: "#c8922a", cursor: "pointer",
            transition: "all 0.3s",
          }}
            onMouseOver={e => { e.target.style.background = "rgba(200,146,42,0.08)"; }}
            onMouseOut={e => { e.target.style.background = "transparent"; }}
          >
            View Architecture
          </button>
        </div>
      </div>

      {/* Bottom fade */}
      <div style={{
        position: "absolute", bottom: 0, left: 0, right: 0, height: 200,
        background: "linear-gradient(transparent, #080706)"
      }} />
    </section>
  );
}

const MODES = [
  {
    id: "sight",
    icon: "◉",
    name: "SightMode",
    origin: "TimeLens DNA",
    color: "#c8922a",
    tagline: "Point. See. Understand.",
    description: "Your camera becomes a portal into history. Point at any monument, battlefield, or building and LORE instantly recognizes it, researches its story, and begins generating a narrated documentary — with Veo 3.1 video scenes and Nano Banana illustrations streaming in real time.",
    steps: [
      { icon: "📷", label: "Camera captures live frame" },
      { icon: "🧭", label: "Gemini + Maps identifies location" },
      { icon: "📚", label: "Search grounds historical facts" },
      { icon: "🎬", label: "Documentary streams instantly" },
    ],
    example: '"You are standing where 40,000 soldiers fought in 1764. The ground beneath you absorbed the blood of three empires..."',
    uniqueFeature: "WALKING TOUR MODE — GPS-triggered narration as you move through a site. Each zone unlocks a new chapter."
  },
  {
    id: "voice",
    icon: "◎",
    name: "VoiceMode",
    origin: "DocuCast DNA",
    color: "#7a9e7e",
    tagline: "Speak. Ask. Receive a film.",
    description: "Speak any topic and receive a fully interleaved documentary — narration, generated illustrations, cinematic Veo 3.1 video clips, and infographics — flowing together as a single coherent output stream. Interrupt at any time to branch into sub-documentaries.",
    steps: [
      { icon: "🎙️", label: "User speaks natural question" },
      { icon: "🧠", label: "Gemini 3 Flash Preview plans documentary arc" },
      { icon: "🖼️", label: "Nano Banana generates illustrations" },
      { icon: "🎞️", label: "Veo 3.1 generates cinematic clips" },
    ],
    example: '"It began not with a crash, but a whisper. In 2003, a banker in Connecticut created a product that would — in five years — bring the world to its knees..."',
    uniqueFeature: "BRANCH DOCS — Tap any claim in the narration to spawn a full sub-documentary on that topic, in the same voice, same style."
  },
  {
    id: "lore",
    icon: "◈",
    name: "LoreMode",
    origin: "The Fusion ✦",
    color: "#e8c86e",
    tagline: "See + Ask = Something New.",
    description: "The breakthrough: Camera AND voice simultaneously. LORE sees where you are AND hears your question — then generates a response that fuses both. This unlocks entirely new experiences impossible in either SightMode or VoiceMode alone.",
    steps: [
      { icon: "📷", label: "Camera streams live visual context" },
      { icon: "🗣️", label: "Voice question adds intent" },
      { icon: "⚡", label: "Gemini fuses both inputs" },
      { icon: "✨", label: "Reality + imagination combined" },
    ],
    example: '"Standing at the Colosseum, you ask: what if Rome never fell? LORE generates an alternate-history film — shot against your real location\'s visual context."',
    uniqueFeature: "ALTERNATE HISTORY — Ground any historical 'what if?' in a real place you're physically standing in for maximum immersive impact."
  }
];

function ModesSection() {
  const [active, setActive] = useState(0);
  const m = MODES[active];

  return (
    <section id="Modes" style={{ minHeight: "100vh", padding: "120px 48px", background: "var(--deep)" }}>
      <div style={{ maxWidth: 1100, margin: "0 auto" }}>
        <div style={{ marginBottom: 64, animation: "fadeUp 0.8s ease both" }}>
          <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, letterSpacing: 4, color: "#c8922a", marginBottom: 16 }}>
            02 — MODES
          </div>
          <h2 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 56, fontWeight: 300, color: "#f2ead8" }}>
            Three inputs.<br />One living documentary.
          </h2>
        </div>

        {/* Mode Tabs */}
        <div style={{ display: "flex", gap: 0, marginBottom: 48, borderBottom: "1px solid rgba(200,146,42,0.15)" }}>
          {MODES.map((mode, i) => (
            <button key={mode.id} onClick={() => setActive(i)} style={{
              flex: 1, padding: "20px 24px", background: "none",
              border: "none", cursor: "pointer",
              borderBottom: active === i ? `2px solid ${mode.color}` : "2px solid transparent",
              transition: "all 0.3s",
            }}>
              <div style={{ fontSize: 20, marginBottom: 6 }}>{mode.icon}</div>
              <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 13, fontWeight: 700, letterSpacing: 2, color: active === i ? mode.color : "#8a7a62" }}>
                {mode.name}
              </div>
              <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 9, color: active === i ? mode.color : "#4a4035", letterSpacing: 2, marginTop: 4 }}>
                {mode.origin}
              </div>
            </button>
          ))}
        </div>

        {/* Mode Content */}
        <div key={active} style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 48, animation: "fadeIn 0.4s ease both" }}>
          <div>
            <div style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 32, fontStyle: "italic", color: m.color, marginBottom: 24 }}>
              {m.tagline}
            </div>
            <p style={{ fontFamily: "'Syne', sans-serif", fontSize: 15, color: "#b8ab94", lineHeight: 1.8, marginBottom: 32 }}>
              {m.description}
            </p>

            {/* Steps */}
            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              {m.steps.map((step, i) => (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 16 }}>
                  <div style={{
                    width: 40, height: 40, borderRadius: "50%",
                    background: `${m.color}18`,
                    border: `1px solid ${m.color}30`,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    fontSize: 16, flexShrink: 0
                  }}>{step.icon}</div>
                  <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 13, color: "#b8ab94", letterSpacing: 1 }}>
                    {step.label}
                  </div>
                  {i < m.steps.length - 1 && (
                    <div style={{ position: "absolute", marginLeft: 20, marginTop: 40 }} />
                  )}
                </div>
              ))}
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
            {/* Example output */}
            <div style={{
              background: "#0d0b09", border: `1px solid ${m.color}20`,
              padding: 28, position: "relative", overflow: "hidden"
            }}>
              <div style={{
                position: "absolute", top: 0, left: 0, right: 0, height: 1,
                background: `linear-gradient(to right, transparent, ${m.color}, transparent)`
              }} />
              <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 9, color: m.color, letterSpacing: 3, marginBottom: 16 }}>
                ▶ SAMPLE NARRATION OUTPUT
              </div>
              <div style={{
                fontFamily: "'Cormorant Garamond', serif", fontSize: 18, fontStyle: "italic",
                color: "#d4c8b0", lineHeight: 1.7
              }}>
                {m.example}
              </div>
              {/* Waveform visualization */}
              <div style={{ display: "flex", alignItems: "center", gap: 3, marginTop: 20 }}>
                {Array.from({ length: 24 }).map((_, i) => (
                  <div key={i} style={{
                    width: 3, background: m.color, borderRadius: 2,
                    opacity: 0.6,
                    animation: `waveform ${0.4 + Math.random() * 0.8}s ease-in-out infinite`,
                    animationDelay: `${Math.random() * 0.5}s`,
                    minHeight: 4, maxHeight: 24
                  }} />
                ))}
              </div>
            </div>

            {/* Unique Feature */}
            <div style={{
              background: `${m.color}0a`,
              border: `1px solid ${m.color}25`,
              padding: 24
            }}>
              <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 9, color: m.color, letterSpacing: 3, marginBottom: 12 }}>
                ✦ SIGNATURE FEATURE
              </div>
              <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 13, color: "#d4c8b0", lineHeight: 1.6 }}>
                {m.uniqueFeature}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

const FEATURES = [
  {
    icon: "🧠",
    title: "Living Session Memory",
    desc: "LORE remembers every frame it has seen, every place you've visited, every question you've asked — within a session. Ask: 'Compare what we saw at the Colosseum to what we discussed about Egypt.' It knows.",
    tag: "Gemini Live API"
  },
  {
    icon: "🎙️",
    title: "Affective Narration",
    desc: "LORE detects your emotional engagement from voice tone. If you sound amazed, it goes deeper. If you sound bored, it jumps to the most dramatic moment. If you're confused, it simplifies.",
    tag: "Affective Dialog"
  },
  {
    icon: "🎬",
    title: "Veo 3.1 Scene Chains",
    desc: "Every documentary chapter generates Veo 3.1 clips with native audio — crowd noise, cannon fire, ambient soundscapes at 48kHz. Clips chain seamlessly for continuous cinematic flow.",
    tag: "Veo 3.1"
  },
  {
    icon: "🖼️",
    title: "Nano Banana Illustrations",
    desc: "Sub-2-second portrait and scene illustrations with character consistency. The same historical figure's face remains consistent across all generated imagery in a documentary.",
    tag: "Gemini 3.1 Flash Image Preview"
  },
  {
    icon: "📜",
    title: "Chronicle Export",
    desc: "Every session auto-assembles into a 'Chronicle' — an illustrated, narrated PDF field report of everywhere you went and everything you learned. Share it, print it, keep it.",
    tag: "Cloud Storage"
  },
  {
    icon: "🌐",
    title: "Search-Grounded Truth",
    desc: "Every historical claim is verified against Google Search in real time. LORE never fabricates dates, figures, or events — all narration is factually grounded before it streams.",
    tag: "Search Grounding"
  },
  {
    icon: "🔀",
    title: "Branch Documentaries",
    desc: "Interrupt any narration to branch into a sub-documentary. Tap any claim, name, or event to spawn a full 60-second mini-doc — same voice, same visual style, nested seamlessly.",
    tag: "ADK Orchestration"
  },
  {
    icon: "🏛️",
    title: "Alternate History Mode",
    desc: "Only possible in LoreMode: ground 'what if' questions in a real location. Standing at Thermopylae, ask: 'What if Persia won?' Veo 3.1 generates an alternate-history film against your real backdrop.",
    tag: "LoreMode Exclusive"
  }
];

function ExperienceSection() {
  return (
    <section id="Experience" style={{ minHeight: "100vh", padding: "120px 48px", background: "#080706" }}>
      <div style={{ maxWidth: 1100, margin: "0 auto" }}>
        <div style={{ marginBottom: 64 }}>
          <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, letterSpacing: 4, color: "#c8922a", marginBottom: 16 }}>
            03 — EXPERIENCE
          </div>
          <h2 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 56, fontWeight: 300, color: "#f2ead8" }}>
            Features that emerge<br />from the fusion.
          </h2>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 2 }}>
          {FEATURES.map((f, i) => (
            <div key={i} style={{
              padding: 32, background: "#0d0b09",
              border: "1px solid rgba(200,146,42,0.08)",
              transition: "all 0.3s", cursor: "default",
              animation: `fadeUp 0.6s ease both`,
              animationDelay: `${i * 0.07}s`
            }}
              onMouseOver={e => e.currentTarget.style.background = "#121009"}
              onMouseOut={e => e.currentTarget.style.background = "#0d0b09"}
            >
              <div style={{ fontSize: 28, marginBottom: 16 }}>{f.icon}</div>
              <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 14, fontWeight: 700, color: "#f2ead8", marginBottom: 12, letterSpacing: 0.5 }}>
                {f.title}
              </div>
              <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 13, color: "#7a6e5c", lineHeight: 1.7, marginBottom: 20 }}>
                {f.desc}
              </div>
              <div style={{
                display: "inline-block",
                fontFamily: "'DM Mono', monospace", fontSize: 9, letterSpacing: 2,
                color: "#c8922a", borderTop: "1px solid rgba(200,146,42,0.2)",
                paddingTop: 12, width: "100%"
              }}>
                {f.tag}
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function ArchitectureSection() {
  const nodes = [
    { x: 50, y: 8, label: "USER", sub: "Camera + Voice", color: "#e8c86e", size: 60 },
    { x: 20, y: 35, label: "SightMode", sub: "Visual Input", color: "#c8922a", size: 48 },
    { x: 50, y: 35, label: "LoreMode", sub: "Fused Input", color: "#e8c86e", size: 56 },
    { x: 80, y: 35, label: "VoiceMode", sub: "Audio Input", color: "#7a9e7e", size: 48 },
    { x: 50, y: 58, label: "Gemini Live API", sub: "Vision + Audio + Memory", color: "#c8922a", size: 70 },
    { x: 20, y: 78, label: "Nano Banana 2", sub: "Illustrations", color: "#8a7e6a", size: 48 },
    { x: 50, y: 80, label: "Veo 3.1", sub: "Video + Audio", color: "#8a7e6a", size: 52 },
    { x: 80, y: 78, label: "Search Grounding", sub: "Factual Truth", color: "#8a7e6a", size: 48 },
    { x: 50, y: 96, label: "LORE OUTPUT", sub: "Interleaved Documentary", color: "#c8922a", size: 64 },
  ];

  const edges = [
    [0, 1], [0, 2], [0, 3],
    [1, 4], [2, 4], [3, 4],
    [4, 5], [4, 6], [4, 7],
    [5, 8], [6, 8], [7, 8]
  ];

  return (
    <section id="Architecture" style={{ minHeight: "100vh", padding: "120px 48px", background: "var(--deep)" }}>
      <div style={{ maxWidth: 1100, margin: "0 auto" }}>
        <div style={{ marginBottom: 64 }}>
          <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, letterSpacing: 4, color: "#c8922a", marginBottom: 16 }}>
            04 — ARCHITECTURE
          </div>
          <h2 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 56, fontWeight: 300, color: "#f2ead8" }}>
            How LORE thinks.
          </h2>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 64, alignItems: "start" }}>
          {/* SVG Diagram */}
          <div style={{ position: "relative", background: "#080706", border: "1px solid rgba(200,146,42,0.1)", padding: 32 }}>
            <svg viewBox="0 0 100 110" style={{ width: "100%", height: "auto" }}>
              {/* Draw edges */}
              {edges.map(([from, to], i) => {
                const a = nodes[from], b = nodes[to];
                return (
                  <line key={i}
                    x1={a.x} y1={a.y + 2.5} x2={b.x} y2={b.y - 2.5}
                    stroke="rgba(200,146,42,0.2)" strokeWidth={0.3}
                    strokeDasharray="1,1"
                  />
                );
              })}
              {/* Draw nodes */}
              {nodes.map((n, i) => (
                <g key={i}>
                  <circle cx={n.x} cy={n.y} r={3.2} fill={`${n.color}22`} stroke={n.color} strokeWidth={0.4} />
                  <text x={n.x} y={n.y + 0.3} textAnchor="middle" dominantBaseline="middle"
                    fill={n.color} fontSize={2} fontFamily="Syne" fontWeight="700">{n.label}</text>
                  <text x={n.x} y={n.y + 2.8} textAnchor="middle" dominantBaseline="middle"
                    fill="#7a6e5c" fontSize={1.4} fontFamily="DM Mono">{n.sub}</text>
                </g>
              ))}
            </svg>
          </div>

          {/* Flow description */}
          <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
            {[
              { step: "01", title: "Input Capture", detail: "Gemini Live API opens a WebSocket session. Camera frames stream as video input; microphone as audio input. Both processed simultaneously with sub-200ms latency.", color: "#c8922a" },
              { step: "02", title: "Intent Recognition", detail: "Gemini 3 Flash Preview identifies: location (Maps API cross-ref), topic (Search grounding), emotional tone (Affective Dialog), and documentary structure needed.", color: "#a07828" },
              { step: "03", title: "Parallel Generation", detail: "ADK orchestrates three parallel generation streams: Narration via Live API native audio, Illustrations via Nano Banana 2 (sub-2s), Video clips via Veo 3.1 (8-60s).", color: "#7a9e7e" },
              { step: "04", title: "Interleaved Streaming", detail: "Output flows to the client as a single coherent stream — narration first, then illustration, then video, timed to match the narrative arc. Session memory accumulates across the full visit.", color: "#c8922a" },
            ].map((item) => (
              <div key={item.step} style={{ display: "flex", gap: 20 }}>
                <div style={{
                  fontFamily: "'DM Mono', monospace", fontSize: 11, color: item.color,
                  width: 32, flexShrink: 0, paddingTop: 2
                }}>{item.step}</div>
                <div>
                  <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 14, fontWeight: 700, color: "#f2ead8", marginBottom: 8 }}>
                    {item.title}
                  </div>
                  <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 13, color: "#7a6e5c", lineHeight: 1.7 }}>
                    {item.detail}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

const STACK = [
  { category: "LIVE INTELLIGENCE", items: [
    { name: "gemini-2.5-flash-native-audio-preview-12-2025", role: "Core: vision + audio + session memory + barge-in", badge: "PRIMARY" },
    { name: "Gemini 3 Flash Preview", role: "Documentary arc planning, Search grounding, fact verification", badge: "ORCHESTRATOR" },
  ]},
  { category: "GENERATION LAYER", items: [
    { name: "Veo 3.1", role: "1080p video clips with native audio — dialogue, SFX, ambiance at 48kHz", badge: "VIDEO" },
    { name: "Nano Banana 2 (gemini-3.1-flash-image-preview)", role: "Sub-2s illustrations with character consistency across scenes", badge: "IMAGE" },
    { name: "Gemini 2.5 Flash Native Audio (Live API)", role: "30 HD voices, 24 languages, tone-adaptive narration", badge: "AUDIO" },
  ]},
  { category: "KNOWLEDGE & GROUNDING", items: [
    { name: "Google Search (Grounding)", role: "Real-time fact verification — all historical claims grounded before streaming", badge: "TRUTH" },
    { name: "Google Maps + Places API", role: "Location recognition, GPS walking tour triggers, site metadata", badge: "LOCATION" },
  ]},
  { category: "GOOGLE CLOUD BACKEND", items: [
    { name: "Agent Development Kit (ADK)", role: "Multi-agent orchestration: parallel generation, tool routing, session state", badge: "ADK" },
    { name: "Cloud Run", role: "Containerized backend, WebSocket server, auto-scaling", badge: "COMPUTE" },
    { name: "Firestore", role: "Session memory persistence, Chronicle accumulation, user preferences", badge: "DATABASE" },
    { name: "Cloud Storage", role: "Veo clip storage, illustration caching, Chronicle PDF export", badge: "STORAGE" },
    { name: "Vertex AI", role: "Model hosting, Provisioned Throughput for Live API", badge: "AI INFRA" },
  ]},
];

const BADGE_COLORS = {
  PRIMARY: "#c8922a", ORCHESTRATOR: "#a07828", VIDEO: "#7a9e7e",
  IMAGE: "#8a7a62", AUDIO: "#9e7a8a", TRUTH: "#6a8e9e",
  LOCATION: "#8e9e6a", ADK: "#c8922a", COMPUTE: "#7a8e9e",
  DATABASE: "#8e7a9e", STORAGE: "#9e8a7e", "AI INFRA": "#c8922a"
};

function StackSection() {
  return (
    <section id="Stack" style={{ minHeight: "100vh", padding: "120px 48px 160px", background: "#080706" }}>
      <div style={{ maxWidth: 1100, margin: "0 auto" }}>
        <div style={{ marginBottom: 64 }}>
          <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, letterSpacing: 4, color: "#c8922a", marginBottom: 16 }}>
            05 — TECH STACK
          </div>
          <h2 style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 56, fontWeight: 300, color: "#f2ead8" }}>
            Built with the full<br />Gemini stack.
          </h2>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 40 }}>
          {STACK.map((cat) => (
            <div key={cat.category}>
              <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 9, letterSpacing: 4, color: "#4a3e2a", marginBottom: 16, borderBottom: "1px solid rgba(200,146,42,0.08)", paddingBottom: 12 }}>
                {cat.category}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                {cat.items.map((item) => (
                  <div key={item.name} style={{
                    display: "flex", alignItems: "center", justifyContent: "space-between",
                    padding: "16px 20px",
                    background: "#0d0b09",
                    border: "1px solid rgba(200,146,42,0.06)",
                    gap: 24,
                    transition: "background 0.2s"
                  }}
                    onMouseOver={e => e.currentTarget.style.background = "#121009"}
                    onMouseOut={e => e.currentTarget.style.background = "#0d0b09"}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 20, flex: 1 }}>
                      <div style={{
                        fontFamily: "'DM Mono', monospace", fontSize: 10,
                        padding: "3px 8px",
                        background: `${BADGE_COLORS[item.badge] || "#c8922a"}18`,
                        border: `1px solid ${BADGE_COLORS[item.badge] || "#c8922a"}30`,
                        color: BADGE_COLORS[item.badge] || "#c8922a",
                        letterSpacing: 2, whiteSpace: "nowrap", flexShrink: 0
                      }}>{item.badge}</div>
                      <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 13, fontWeight: 600, color: "#d4c8b0" }}>
                        {item.name}
                      </div>
                    </div>
                    <div style={{ fontFamily: "'Syne', sans-serif", fontSize: 12, color: "#5a5040", textAlign: "right", maxWidth: 360 }}>
                      {item.role}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Bottom tagline */}
        <div style={{ marginTop: 100, textAlign: "center", borderTop: "1px solid rgba(200,146,42,0.1)", paddingTop: 60 }}>
          <div style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 48, fontWeight: 300, color: "#f2ead8", lineHeight: 1.2 }}>
            The world has always had stories.
          </div>
          <div style={{ fontFamily: "'Cormorant Garamond', serif", fontSize: 48, fontStyle: "italic", fontWeight: 300, color: "#c8922a", lineHeight: 1.2 }}>
            LORE just learned to tell them.
          </div>
          <div style={{ fontFamily: "'DM Mono', monospace", fontSize: 10, color: "#4a3e2a", letterSpacing: 4, marginTop: 32 }}>
            TIMELENS × DOCUCAST → LORE ◆ GEMINI LIVE AGENT CHALLENGE 2025
          </div>
        </div>
      </div>
    </section>
  );
}

export default function LoreApp() {
  const [activeSection, setActiveSection] = useState("Concept");

  const scrollTo = (section) => {
    setActiveSection(section);
    const el = document.getElementById(section);
    if (el) el.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    const style = document.createElement("style");
    style.textContent = LORE_STYLES;
    document.head.appendChild(style);
    return () => document.head.removeChild(style);
  }, []);

  return (
    <div style={{ background: "#080706", minHeight: "100vh" }}>
      <GrainOverlay />
      <Navbar active={activeSection} onNav={scrollTo} />
      <Hero onNav={scrollTo} />
      <ModesSection />
      <ExperienceSection />
      <ArchitectureSection />
      <StackSection />
    </div>
  );
}
