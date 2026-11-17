// Renders Deni's game log and averages into templates/_player.html. Needs results.js (Results.escapeHtml etc.).

const PlayerStats = (() => {
  const $ = id => document.getElementById(id);
  const esc = Results.escapeHtml;
  const COLLAPSED_ROWS = 10;
  const CARDS = [
    ['pts', 'PTS'], ['reb', 'REB'], ['ast', 'AST'], ['stl', 'STL'], ['blk', 'BLK'],
    ['tov', 'TO', true], ['min', 'MIN'], ['fg_pct', 'FG%'], ['fg3_pct', '3P%'], ['ft_pct', 'FT%'],
  ];
  let data = null;
  let showAll = false;

  const fmt = v => (v == null ? '—' : Number.isInteger(v) ? String(v) : v.toFixed(1));
  const pctSuffix = key => (key.endsWith('_pct') ? '%' : '');

  // Games are scheduled in US time, so show the date there rather than in the viewer's timezone.
  function gameDate(s) {
    const d = new Date(s);
    return isNaN(d) ? '—' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'America/Los_Angeles' });
  }

  function card(key, label, lowerIsBetter) {
    const season = data.averages[key], recent = data.recent_averages[key];
    let detail = '';
    // With five or fewer games the recent window is the whole season, so there is nothing to compare.
    if (data.games_played > data.recent_count && recent != null && season != null) {
      const diff = Math.round((recent - season) * 10) / 10;
      const good = lowerIsBetter ? diff < 0 : diff > 0;
      const trend = diff === 0 ? '' :
        ` <span class="${good ? 'trend-up' : 'trend-down'}">${diff > 0 ? '▲' : '▼'} ${Math.abs(diff).toFixed(1)}</span>`;
      detail = `Last ${data.recent_count}: ${fmt(recent)}${pctSuffix(key)}${trend}`;
    }
    return `<div class="stat"><div class="stat-value">${fmt(season)}${pctSuffix(key)}</div>
      <div class="stat-label">${label}</div>${detail ? `<div class="stat-detail">${detail}</div>` : ''}</div>`;
  }

  const shooting = (m, a) => (m == null ? '—' : `${fmt(m)}-${fmt(a)}`);

  function row(g) {
    return `<tr>
      <td class="date-cell">${esc(gameDate(g.date))}</td>
      <td class="nowrap"><a href="${esc(g.url)}" target="_blank" rel="noopener" title="${esc(g.opponent_name)} – box score">${g.home ? 'vs' : '@'} ${esc(g.opponent)}</a></td>
      <td class="nowrap"><span class="result result-${esc(g.result)}">${esc(g.result || '')}</span>
        ${g.score != null ? `<span class="muted">${g.score}-${g.opp_score}</span>` : ''}</td>
      <td class="num">${fmt(g.min)}</td>
      <td class="num pts">${fmt(g.pts)}</td>
      <td class="num">${fmt(g.reb)}</td><td class="num">${fmt(g.ast)}</td>
      <td class="num">${fmt(g.stl)}</td><td class="num">${fmt(g.blk)}</td><td class="num">${fmt(g.tov)}</td>
      <td class="num">${shooting(g.fgm, g.fga)}</td><td class="num">${shooting(g.tpm, g.tpa)}</td>
      <td class="num">${shooting(g.ftm, g.fta)}</td>
    </tr>`;
  }

  function renderGames() {
    const games = showAll ? data.games : data.games.slice(0, COLLAPSED_ROWS);
    $('gamelog-body').innerHTML = games.map(row).join('');
    const toggle = $('gamelog-toggle');
    toggle.hidden = data.games.length <= COLLAPSED_ROWS;
    toggle.textContent = showAll ? `Show last ${COLLAPSED_ROWS} only` : `Show all ${data.games.length} games`;
  }

  function render(player) {
    const panel = $('player-panel');
    if (!player || !player.games?.length) { panel.hidden = true; return; }
    data = player;
    $('player-title').textContent = `${player.player} – season stats`;
    $('player-sub').textContent =
      `${player.season} · ${player.games_played} games${player.team ? ` · ${player.team}` : ''}`;
    $('player-averages').innerHTML = CARDS.map(c => card(...c)).join('');
    $('player-source').innerHTML =
      `Per game · <a href="${esc(player.source_url)}" target="_blank" rel="noopener">ESPN</a> · ` +
      `updated <time datetime="${esc(player.fetched_at)}" title="${esc(Results.fmtDateTime(player.fetched_at))}">${esc(Results.fmtRelative(player.fetched_at))}</time>`;
    renderGames();
    panel.hidden = false;
  }

  document.addEventListener('DOMContentLoaded', () => {
    $('gamelog-toggle').addEventListener('click', () => { showAll = !showAll; renderGames(); });
    window.addEventListener('beforeprint', () => { if (data) { showAll = true; renderGames(); } });
  });

  return { render };
})();
