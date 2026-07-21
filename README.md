# 📈 Sentiment Trading Alpha

**Turn news headlines into trade insights using AI — automatically.**

Sentiment Trading Alpha reads financial news, understands what it means using artificial intelligence, and helps you make informed trading decisions. It analyzes geopolitical events, economic data, SEC EDGAR filings, and market sentiment to generate buy/sell/hold recommendations for popular ETFs like SPY, QQQ, USO, and crypto funds.

> ⚠️ **Important:** This is experimental software for educational purposes. It is NOT financial advice. Trading involves risk — especially with leveraged ETFs.

---

## 🎯 What Does It Actually Do?

Imagine having a research assistant that:
- **Monitors the news 24/7** — Scans hundreds of financial news sources automatically
- **Understands the context** — Uses AI to figure out if news is bullish, bearish, or neutral
- **Connects the dots** — Combines news with economic data (interest rates, oil inventories, etc.) and SEC EDGAR filings
- **Gives you clear signals** — Tells you BUY, SELL, or HOLD with a confidence level
- **Tests strategies safely** — Simulates trades with fake money so you can learn without risk
- **Unified News View** — Browse all RSS articles, Truth Social posts, and EDGAR filings in one place

### Example Output
```
📰 News: "OPEC announces surprise production cuts"
   ↓ AI Analysis
💡 Signal: BUY USO (Oil ETF)
   Confidence: HIGH (85%)
   Reasoning: Production cuts typically drive oil prices up
   Suggested Leverage: 2x (UCO)
```

---

## 🤔 Why Would You Want This?

### You might want this tool if:
- ✅ You want to understand how world events affect markets
- ✅ You're tired of missing trades because you can't watch the news all day
- ✅ You want to test trading ideas without risking real money
- ✅ You're curious about using AI for market analysis
- ✅ You want to learn about sentiment-based trading strategies

### This is NOT for you if:
- ❌ You want a "get rich quick" automated trading bot
- ❌ You're looking for guaranteed profits (nothing can do that!)
- ❌ You don't want to understand the reasoning behind trades
- ❌ You plan to use it with real money without thorough testing of your LLM choices, news sources, etc.

---

## 🚀 Quick Start (Choose Your Path)

### Path 1: Simple Local Setup (Free, Private)
**Best if:** You want to try it out privately on your own computer

**What you need:**
- A computer (Windows or Mac)
- About 10 minutes
- Internet connection

**Steps:**
1. **Download the software** (instructions below)
2. **Install Ollama** (free AI that runs on your computer) — keeps your data private
3. **Click start** — that's it!

[Detailed instructions below ↓](#option-a-local-setup-private-free)

---

### Path 2: Cloud Setup with Docker (Easier, More Powerful)
**Best if:** You want the easiest setup or don't want to run AI on your own computer

**What you need:**
- A computer (Windows, Mac, or Linux)
- Docker Desktop (free)
- An API key from a cloud AI provider (OpenAI, OpenRouter, etc.) — costs vary

**Steps:**
1. **Install Docker** (one-time setup)
2. **Get an API key** from a cloud AI provider
3. **Run one command** — Docker handles the rest!

[Detailed instructions below ↓](#option-b-docker-setup-easy-powerful)

---

## 📦 Installation Options

## Option A: Local Setup (Private & Free)

### Step 1: Get the Code
```bash
git clone https://github.com/yourusername/Sentiment-Trading-Alpha.git
cd Sentiment-Trading-Alpha
```

### Step 2: Install Ollama (The AI Brain)
Ollama runs AI on your computer — your data stays private.

1. Download from [ollama.com](https://ollama.com)
2. Install and open it
3. Open your terminal/command prompt and type:
```bash
ollama pull qwen3.5:9b
```

### Step 3: Start the System
**Windows (PowerShell):**
```powershell
# Terminal 1 - Start Ollama
ollama serve

# Terminal 2 - Start the app
pip install -r requirements.txt
python -m playwright install chromium
npm run start
```

**Mac:**
```bash
# Terminal 1 - Start Ollama
ollama serve

# Terminal 2 - Start the app
pip install -r requirements.txt
python -m playwright install chromium
npm run start
```

### Step 4: Open Your Browser
Go to [http://localhost:3000](http://localhost:3000)

🎉 **You're done!** The system starts analyzing news automatically.

---

## Option B: Docker Setup (Easy & Powerful)

### Step 1: Install Docker
- **Windows/Mac:** Download [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- **Linux:** Install Docker and Docker Compose

### Step 2: Get an AI API Key
You'll need an API key from one of these providers:
- **OpenAI** (GPT-4o, GPT-4o-mini) — [platform.openai.com](https://platform.openai.com)
- **OpenRouter** (access to 200+ models) — [openrouter.ai](https://openrouter.ai)
- **Anthropic** (Claude) — [console.anthropic.com](https://console.anthropic.com)

💡 **Tip:** OpenRouter has many free models to try!

### Step 3: Configure & Start
```bash
# 1. Get the code
git clone https://github.com/yourusername/Sentiment-Trading-Alpha.git
cd Sentiment-Trading-Alpha

# 2. Create your config file
cp .env.example .env

# 3. Edit .env with your API key (use any text editor)
# Add your INFERENCE_BACKEND, OPENAI_API_KEY, etc.

# 4. Start everything
docker compose up --build
```

### Step 4: Open Your Browser
- **App:** [http://localhost:3000](http://localhost:3000)
- **Settings:** Click "Admin" in the app to configure everything

---

## 🖥️ What You'll See

### Dashboard
- **Live News Feed** — See what the AI is reading in real-time
- **Trade Signals** — Clear BUY/SELL/HOLD recommendations
- **Confidence Levels** — HIGH, MEDIUM, or LOW confidence
- **Price Charts** — Visualize the ETFs being analyzed

### Analysis Page
- **Why did the AI say that?** — See the reasoning behind every signal
- **News Sources** — Which articles influenced the decision
- **Risk Metrics** — Understand the downside

### Paper Trading Simulator
- **Fake money, real testing** — See how strategies would perform
- **No risk** — Learn without losing money
- **Track performance** — See if the AI's suggestions are working

---

## 📊 Alpha Analytics (/alpha)

The **Alpha Analytics** page is your deep-dive dashboard for understanding system performance and signal quality. Access it at [http://localhost:3000/alpha](http://localhost:3000/alpha) after starting the app.

### What You'll Find:

#### Performance Metrics
- **Win Rate** — Percentage of profitable signals
- **Average Return** — Mean return per trade signal
- **Sharpe Ratio** — Risk-adjusted return metric
- **Maximum Drawdown** — Largest peak-to-trough decline

#### Signal Quality Analysis
- **Confidence vs. Outcome** — See if high-confidence signals actually perform better
- **Asset Performance** — Which ETFs/stocks have the best signal accuracy
- **Time-based Patterns** — Performance by hour, day, or market session

#### Backtesting Insights
- **Historical Accuracy** — How past signals would have performed
- **Regime Analysis** — Performance in bull vs. bear markets
- **False Positive/Negative Rates** — Understand where the AI struggles

#### Why It Matters:
- ✅ **Validate the AI** — Ensure signals are actually predictive, not random
- ✅ **Improve Strategies** — Identify which assets or timeframes work best
- ✅ **Build Confidence** — See data-backed evidence of system performance
- ✅ **Optimize Settings** — Use metrics to fine-tune configuration

> 💡 **Pro Tip:** Check Alpha Analytics regularly to ensure the system maintains high signal quality over time. If win rates drop, it may be time to retrain or adjust news sources.

---

## ⚙️ Configuration (Made Simple)

Everything is configured through a friendly **Admin Web Interface** — no coding required!

### What You Can Customize:
- **Which stocks/ETFs to analyze** — SPY, QQQ, USO, Bitcoin funds, or add your own
- **How often to check news** — Every 30 minutes? Every hour?
- **Risk level** — Conservative, moderate, or aggressive
- **News sources** — Choose which RSS feeds to monitor
- **AI settings** — Switch between local and cloud AI, pick different models

### How to Access Admin:
1. Start the app
2. Click the "Admin" link in the dashboard
3. (Optional) Set a password for security

---

## 💡 Understanding the Signals

### What the AI Analyzes:
| Factor | Example |
|--------|---------|
| **Geopolitical Events** | "Trade war tensions rise" → Bearish for stocks |
| **Economic Data** | "Fed raises interest rates" → Bearish for growth stocks |
| **Oil Supply** | "OPEC cuts production" → Bullish for oil (USO) |
| **Crypto News** | "Bitcoin ETF approved" → Bullish for Bitcoin funds |

### Signal Strength:
- **HIGH confidence** (75%+) — Strong evidence, clear direction
- **MEDIUM confidence** (50-75%) — Good evidence, some uncertainty
- **LOW confidence** (<50%) — Mixed signals, proceed with caution

### Leverage Explained:
The system may suggest leveraged ETFs (2x or 3x) for high-confidence trades:
- **2x ETF** (like QLD) = Moves 2x the underlying stock
- **3x ETF** (like TQQQ) = Moves 3x the underlying stock
- ⚠️ **Warning:** Leverage amplifies both gains AND losses!

---

## 🛡️ Safety & Risk Management

### Built-in Safety Features:
- ✅ **Paper trading first** — Test everything with fake money
- ✅ **Confidence thresholds** — Won't trade on weak signals
- ✅ **Stop-loss suggestions** — Helps limit potential losses
- ✅ **Position sizing** — Recommends appropriate trade sizes
- ✅ **Audit trail** — Every decision is logged and explainable

### Your Responsibilities:
- 🔒 **Never risk money you can't afford to lose**
- 🔒 **Understand every trade before considering it**
- 🔒 **Test thoroughly with paper trading first**
- 🔒 **Remember: past performance ≠ future results**

---

## ❓ Frequently Asked Questions

### "Is this legal?"
Yes! It's a research and analysis tool. Just don't use it for insider trading or market manipulation.

### "Do I need to know how to code?"
No! The Docker setup requires zero coding. The local setup needs basic terminal/command prompt usage.

### "Is my data safe?"
- **Local setup:** Everything stays on your computer
- **Cloud setup:** API keys are encrypted; news data is stored locally

### "How much does it cost?"
- **Local setup:** Free (uses your computer's processing power)
- **Cloud setup:** Depends on your AI provider (typically $5-20/month for casual use)

### "Can I use this with real money?"
Technically yes (via Alpaca integration), but we strongly recommend:
1. Extensive paper trading first
2. Understanding every aspect of the system
3. Starting with very small amounts
4. Never risking money you can't afford to lose

### "What if something breaks?"
- Check the [Troubleshooting](#troubleshooting) section
- Enable "Verbose Mode" to see detailed logs
- Open an issue on GitHub

---

## 🔧 Troubleshooting

### "The app won't start"
- **Check:** Is Ollama running? (Local setup)
- **Check:** Is Docker running? (Docker setup)
- **Check:** Are ports 3000 and 8000 free?

### "No signals appearing"
- **Wait:** The system needs 5-10 minutes to analyze the first batch of news
- **Check:** Is the AI configured correctly? (Admin → LLM Configuration)
- **Check:** Are news sources enabled? (Admin → RSS Sources)

### "AI isn't working"
- **Local:** Run `ollama list` to see if a model is installed
- **Cloud:** Verify your API key in Admin settings
- **Test:** Use the "Test Connection" button in Admin

### Enable Debug Mode
If you need more details:

**Backend (Windows):**
```powershell
python run.py --verbose
```

**Backend (Mac):**
```bash
python3.12 run.py --verbose
```

**Frontend:**
```bash
cd frontend
npm run dev:verbose
```

---

## 📚 Learn More

### For Curious Minds:
- **[How It Works](REFERENCE.md)** — Technical details about the AI pipeline
- **[Alpha Analytics](http://localhost:3000/alpha)** — Deep dive into performance metrics
- **[Configuration Guide](REFERENCE.md#configuration)** — All the settings explained

### For Developers:
- The codebase is open-source and well-documented
- Python backend (FastAPI) + React frontend (Next.js)
- Contributions welcome!

---

## ⚖️ Legal Stuff

**License:** Apache 2.0 (open-source)

**Disclaimer:** This software is for educational and research purposes only. It is NOT financial advice. The creators are not financial advisors. Trading involves substantial risk of loss. Past performance does not guarantee future results. Consult a qualified financial advisor before making investment decisions.

**No Liability:** The creators of this software are not responsible for any financial losses incurred from using this tool.

---

## 🙏 Support & Community

- **Bug Reports:** [Open an issue on GitHub](https://github.com/techjeffe/Sentiment-Trading-Alpha/issues)
- **Feature Requests:** We'd love to hear your ideas!
- **Questions:** Check the FAQ above or open a discussion

---


*Remember: The best traders never stop learning. Use this tool to learn, test, and understand — not to get rich quick.*
