import Link from "next/link";
import { AppHeader } from "@/components/AppHeader";

function GlassCard({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-2xl border border-white/10 p-6 ${className}`}
      style={{ background: "rgba(30,41,59,0.75)", backdropFilter: "blur(12px)" }}
    >
      {children}
    </div>
  );
}

export default function AboutPage() {
  return (
    <div className="min-h-screen" style={{ backgroundColor: "#0f172a", color: "#f8fafc" }}>
      <AppHeader
        title="About Sentiment Trading Alpha"
        titleSize="text-2xl"
        subtitle="Builder notes, origin story, and the not-a-quant disclaimer"
        maxWidth="max-w-5xl"
        py="py-4"
      >
        <div className="flex items-center gap-3">
          <Link href="/" className="text-xs text-slate-300 hover:text-white border border-slate-700 rounded-lg px-3 py-2">
            Dashboard
          </Link>
          <Link href="/health" className="text-xs text-emerald-300 hover:text-emerald-200 border border-emerald-500/20 rounded-lg px-3 py-2">
            Health
          </Link>
          <Link href="/admin" className="text-xs text-blue-300 hover:text-blue-200 border border-blue-500/20 rounded-lg px-3 py-2">
            Admin
          </Link>
        </div>
      </AppHeader>

      <main className="max-w-5xl mx-auto px-6 py-10 space-y-6">
        <GlassCard>
          <p className="text-[11px] uppercase tracking-[0.22em] text-emerald-300">The Builder</p>
          <h2 className="text-3xl font-black text-white mt-2">Jeff Eberhard</h2>
          <p className="text-slate-300 mt-4 leading-relaxed max-w-3xl">
            Jeff Eberhard is the founder of TexasPCS LLC, an Austin-based software company building SaaS products.
            By day he works in senior enterprise tech with deep Oracle experience. On the side, he builds software
            with AI-assisted development and ships weirdly practical projects like this one.
          </p>
          <div className="flex flex-wrap gap-3 mt-5">
            <a
              href="https://texaspcs.com"
              target="_blank"
              rel="noreferrer"
              className="text-sm text-blue-300 hover:text-blue-200 border border-blue-500/20 rounded-lg px-4 py-2"
            >
              texaspcs.com
            </a>
            <a
              href="https://github.com/techjeffe/qwen-3.5-9b-getrich"
              target="_blank"
              rel="noreferrer"
              className="text-sm text-slate-200 hover:text-white border border-slate-700 rounded-lg px-4 py-2"
            >
              GitHub Repo
            </a>
          </div>
        </GlassCard>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <GlassCard>
            <p className="text-[11px] uppercase tracking-[0.22em] text-blue-300">Why He Built It</p>
            <p className="text-slate-300 mt-4 leading-relaxed">
              Sentiment Trading Alpha exists because Jeff got tired of watching the market move on geopolitical news
              before he could react. So he built a local-first sentiment pipeline to close that gap: pull live headlines,
              sanity-check them with structured market data, and turn the mess into something actionable faster than a
              doom-scroll and a guess.
            </p>
            <p className="text-slate-400 mt-4 leading-relaxed">
              This is not a “former hedge fund wizard emerges from stealth” story. It is more “sales guy who codes got
              annoyed enough to automate the workflow.”
            </p>
          </GlassCard>

          <GlassCard>
            <p className="text-[11px] uppercase tracking-[0.22em] text-yellow-300">Important Disclaimer</p>
            <p className="text-slate-300 mt-4 leading-relaxed">
              This project is open source, educational, and absolutely not financial advice. It is a side project built
              for learning, experimentation, and faster interpretation of news-driven market moves.
            </p>
            <p className="text-slate-400 mt-4 leading-relaxed">
              Jeff is not a quant. He is not pretending to be a quant. He is a sales guy who codes, uses AI tools, and
              wanted a better way to keep up when headlines started moving markets before humans could blink.
            </p>
          </GlassCard>
        </div>

        <GlassCard>
          <p className="text-[11px] uppercase tracking-[0.22em] text-slate-400">What This App Tries To Do</p>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mt-5">
            <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
              <p className="text-sm font-semibold text-white">Pull fast</p>
              <p className="text-xs text-slate-400 mt-2">Local-first pipeline for RSS headlines, prices, and structured validation context.</p>
            </div>
            <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
              <p className="text-sm font-semibold text-white">Filter noise</p>
              <p className="text-xs text-slate-400 mt-2">Separate signal from geopolitical theater before everything gets called “market-moving.”</p>
            </div>
            <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
              <p className="text-sm font-semibold text-white">Stay inspectable</p>
              <p className="text-xs text-slate-400 mt-2">Keep the prompts and data visible enough that you can audit what the model actually saw.</p>
            </div>
          </div>
        </GlassCard>
      </main>
    </div>
  );
}
