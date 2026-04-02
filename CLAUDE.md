# Polymarket Stock/Index Market Making Research

## What This Project Is

Research and backtesting system for **market making on Polymarket's stock and index binary event markets**.

Polymarket is a prediction market where users trade binary contracts like **"Will NVDA close above $165 on April 2?"**. Each market has two tokens — YES and NO — that resolve to $1.00 or $0.00 at expiry. Prices between $0 and $1 represent the market's implied probability. YES + NO always sum to $1.00. Trading uses a hybrid CLOB (off-chain matching, on-chain settlement on Polygon). Zero maker fees on most stock/index markets.

**The core thesis**: Polymarket prices are systematically mispriced relative to probabilities implied by listed equity options. We extract risk-neutral probabilities from options chains (Breeden-Litzenberger method) and exploit the gap.

## Data Sources

- **Polymarket orderbook + trades** via **Telonex** ($79/mo) — tick-level L2 snapshots and trades for all stock/index markets
- **Historical options data** via **ThetaData** (Options Standard, $80/mo) — tick-level NBBO quotes, IV, Greeks for all OPRA options back to 2016. Terminal runs locally at `http://127.0.0.1:25503/v3`
- **Polymarket market metadata** via Polymarket REST APIs (Gamma, CLOB, Data)

## MCP Documentation Servers

- `polymarket-docs` — Full Polymarket API docs (CLOB, trading, data, WebSocket, fees, resolution)
- `thetadata-docs` — Full ThetaData API docs (options, stocks, indices, Greeks, streaming)

Use these before making assumptions about API behavior or data availability.

## Target Tickers

AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA, NFLX, PLTR, SPX, NDX (excluded from v1.0 — ThetaData lacks NDX index price data)

## Directory Structure

```
market-making-rnd/
├── CLAUDE.md              ← You are here
├── Vault/                 ← Obsidian vault (research, plans, architecture)
│   ├── .obsidian/
│   ├── Research/          ← Platform mechanics, pricing theory, data APIs, signals
│   ├── Strategies/        ← MM strategies (AS, GLFT), inventory, capital efficiency
│   ├── Backtesting/       ← Engine architecture, fill simulation, data alignment
│   ├── Research Index.md  ← Master index with wikilinks to all notes
│   └── Secrets.md         ← Credentials (never commit or display)
└── Code/                  ← All code, scripts, and data
    ├── scripts/           ← download_options.py, discover_markets.py
    ├── data/              ← Downloaded Parquet files (thetadata, discovery)
    ├── backtesting-engine/ ← Production engine (in development)
    ├── Telonex testing/   ← NVDA POC backtester
    └── notebooks/
```

## The Obsidian Vault (`Vault/`)

The vault is the **single source of truth** for all research, architecture, plans, results, and institutional knowledge accumulated across sessions.

**Always read the vault before starting new work.** Notes are interlinked with `[[wikilinks]]`. If a topic has been researched, build on it rather than starting fresh. New findings, architecture decisions, and results must be written back to the vault as Obsidian-compatible markdown with proper frontmatter and wikilinks.

Start with `Vault/Research Index.md` for a full map of all research notes.

## Cross-Platform Setup

Development and small-dataset testing on **MacBook Air M2 (macOS)**. Full-scale backtests on **Windows 10 PC** (more RAM/cores). Data stored on an **external SSD** shared between both machines.

- SSD must be formatted **exFAT** (native read/write on both macOS and Windows)
- All data paths go through `config.toml` → `paths.data_dir` — the only setting that changes per machine
- Use `pathlib.Path` everywhere in code — never hardcode `/` or `\`
- Parquet files are binary-portable across platforms

## Working Conventions

- Research output → `Vault/` as `.md` files with YAML frontmatter and wikilinks
- Code and scripts → `Code/`
- Data files use Parquet with zstd compression → configured via `config.toml` `data_dir`
- ThetaData terminal must be running for data downloads (`java -jar ~/ThetaTerminalV3.jar`)
- Credentials are in `Vault/Secrets.md` — never commit or display these
- `config.toml` at project root configures data paths, ThetaData settings — adjust per machine
