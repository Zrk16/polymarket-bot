document.addEventListener("DOMContentLoaded", () => {
  const REFRESH_MS = 15000;

  const $ = (id) => document.getElementById(id);

  const els = {
    botPulse: $("botPulse"),
    botStatus: $("botStatus"),
    totalValue: $("totalValue"),
    cashValue: $("cashValue"),
    realizedPnl: $("realizedPnl"),
    unrealizedPnl: $("unrealizedPnl"),
    openCount: $("openCount"),
    winLoss: $("winLoss"),
    positionsBody: $("positionsBody"),
    positionsCount: $("positionsCount"),
    historyBody: $("historyBody"),
    historyCount: $("historyCount"),
    decisionsBody: $("decisionsBody"),
  };

  function money(n) {
    const val = parseFloat(n) || 0;
    return "$" + val.toFixed(2);
  }

  function signedMoney(n) {
    const val = parseFloat(n) || 0;
    const prefix = val >= 0 ? "+$" : "-$";
    return prefix + Math.abs(val).toFixed(2);
  }

  function pnlClass(n) {
    const val = parseFloat(n) || 0;
    if (val > 0) return "pnl--pos";
    if (val < 0) return "pnl--neg";
    return "";
  }

  function truncate(str, len) {
    if (!str) return "";
    return str.length > len ? str.slice(0, len) + "…" : str;
  }

  function emptyRow(cols, msg) {
    return `<tr class="table__empty"><td colspan="${cols}">${msg}</td></tr>`;
  }

  // ── Bot status ──────────────────────────────────────────────────
  async function fetchBotStatus() {
    try {
      const res = await fetch("/api/bot");
      const data = await res.json();

      if (data.running) {
        els.botPulse.classList.add("nav__pulse--live");
        els.botStatus.textContent = `cycle ${data.cycle}`;
      } else {
        els.botPulse.classList.remove("nav__pulse--live");
        els.botStatus.textContent = "idle";
      }
    } catch {
      els.botPulse.classList.remove("nav__pulse--live");
      els.botStatus.textContent = "offline";
    }
  }

  // ── Portfolio stats ─────────────────────────────────────────────
  async function fetchStatus() {
    try {
      const res = await fetch("/api/status");
      const s = await res.json();

      els.totalValue.textContent = money(s.total_value);
      els.cashValue.textContent = money(s.bankroll);

      els.realizedPnl.textContent = signedMoney(s.realized_pnl);
      els.realizedPnl.className = "stat__value " + pnlClass(s.realized_pnl);

      els.unrealizedPnl.textContent = signedMoney(s.unrealized_pnl);
      els.unrealizedPnl.className = "stat__value " + pnlClass(s.unrealized_pnl);

      els.openCount.textContent = s.open_count;
      els.winLoss.textContent = `${s.win_count} / ${s.loss_count}`;
    } catch {
      // keep stale data on screen
    }
  }

  // ── Ledger (positions + history) ────────────────────────────────
  async function fetchLedger() {
    try {
      const res = await fetch("/api/ledger");
      const data = await res.json();
      const trades = data.trades || [];

      const open = trades.filter((t) => t.status === "open");
      const resolved = trades.filter((t) => t.status === "resolved");

      renderPositions(open);
      renderHistory(resolved);
    } catch {
      // keep stale data
    }
  }

  function renderPositions(trades) {
    els.positionsCount.textContent = `${trades.length} active`;

    if (!trades.length) {
      els.positionsBody.innerHTML = emptyRow(6, "No open positions yet");
      return;
    }

    els.positionsBody.innerHTML = trades
      .map((t) => {
        const pnl = parseFloat(t.unrealized_pnl) || 0;
        const dirClass = t.direction === "YES" ? "badge--yes" : "badge--no";
        return `<tr>
          <td>${truncate(t.question, 50)}</td>
          <td><span class="badge ${dirClass}">${t.direction}</span></td>
          <td>${parseFloat(t.entry_odds).toFixed(3)}</td>
          <td>${parseFloat(t.current_price || t.entry_odds).toFixed(3)}</td>
          <td>${money(t.bet_amount)}</td>
          <td class="${pnlClass(pnl)}">${signedMoney(pnl)}</td>
        </tr>`;
      })
      .join("");
  }

  function renderHistory(trades) {
    els.historyCount.textContent = `${trades.length} trades`;

    if (!trades.length) {
      els.historyBody.innerHTML = emptyRow(6, "No resolved trades yet");
      return;
    }

    els.historyBody.innerHTML = trades
      .reverse()
      .map((t) => {
        const pnl = parseFloat(t.realized_pnl) || 0;
        const dirClass = t.direction === "YES" ? "badge--yes" : "badge--no";
        const resultClass = pnl >= 0 ? "badge--win" : "badge--loss";
        const resultText = pnl >= 0 ? "WIN" : "LOSS";
        return `<tr>
          <td>${t.resolved_at || t.placed_at || "—"}</td>
          <td>${truncate(t.question, 45)}</td>
          <td><span class="badge ${dirClass}">${t.direction}</span></td>
          <td>${parseFloat(t.entry_odds).toFixed(3)}</td>
          <td><span class="badge ${resultClass}">${resultText}</span></td>
          <td class="${pnlClass(pnl)}">${signedMoney(pnl)}</td>
        </tr>`;
      })
      .join("");
  }

  // ── AI decisions ────────────────────────────────────────────────
  async function fetchDecisions() {
    try {
      const res = await fetch("/api/decisions");
      const rows = await res.json();

      if (!rows.length) {
        els.decisionsBody.innerHTML = emptyRow(5, "No decisions logged yet");
        return;
      }

      els.decisionsBody.innerHTML = rows
        .reverse()
        .map((r) => {
          const conf = parseFloat(r.confidence) || 0;
          const dirClass =
            r.direction === "YES"
              ? "badge--yes"
              : r.direction === "NO"
                ? "badge--no"
                : "";
          return `<tr>
            <td>${r.timestamp || "—"}</td>
            <td>${truncate(r.market_title, 45)}</td>
            <td>${r.direction ? `<span class="badge ${dirClass}">${r.direction}</span>` : "—"}</td>
            <td>${(conf * 100).toFixed(0)}%</td>
            <td>${truncate(r.ai_reasoning, 80)}</td>
          </tr>`;
        })
        .join("");
    } catch {
      // keep stale data
    }
  }

  // ── Scroll reveals ──────────────────────────────────────────────
  function initReveals() {
    const targets = document.querySelectorAll(".reveal");
    if (!targets.length) return;

    const prefersReduced = window.matchMedia(
      "(prefers-reduced-motion: reduce)"
    ).matches;

    if (prefersReduced) {
      targets.forEach((el) => el.classList.add("visible"));
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("visible");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.1 }
    );

    targets.forEach((el) => observer.observe(el));
  }

  // ── Init ────────────────────────────────────────────────────────
  async function refresh() {
    await Promise.all([
      fetchBotStatus(),
      fetchStatus(),
      fetchLedger(),
      fetchDecisions(),
    ]);
  }

  initReveals();
  refresh();
  setInterval(refresh, REFRESH_MS);
});
