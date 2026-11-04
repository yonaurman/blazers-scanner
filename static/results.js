// Results rendering shared by the live dashboard (index.html) and the static site (site.html).
// Expects the markup from templates/_results.html.

const Results = (() => {
  const $ = id => document.getElementById(id);
  let current = null;
  let previousKeys = null;
  const collapsed = new Set();
  const filters = { q: '', keywords: new Set(), article: '', hideReplies: false, onlyNew: false, sort: 'article' };

  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // Older rows were stored without a timezone suffix; they are UTC.
  function parseUtc(s) {
    if (!s) return null;
    const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s + 'Z');
    return isNaN(d) ? null : d;
  }
  function fmtDate(s) {
    const d = parseUtc(s);
    return d ? d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : '—';
  }
  function fmtDateTime(s) {
    const d = parseUtc(s);
    return d ? d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—';
  }
  function fmtRelative(s) {
    const d = parseUtc(s);
    if (!d) return '—';
    const min = Math.round((Date.now() - d) / 60000);
    if (min < 1) return 'just now';
    if (min < 60) return `${min} min ago`;
    const hr = Math.round(min / 60);
    if (hr < 24) return `${hr} h ago`;
    const day = Math.round(hr / 24);
    return day < 7 ? `${day} d ago` : fmtDate(s);
  }
  function ageHours(s) {
    const d = parseUtc(s);
    return d ? (Date.now() - d) / 3600000 : null;
  }
  function timeTag(s) {
    return `<time datetime="${escapeHtml(s || '')}" title="${escapeHtml(fmtDateTime(s))}">${escapeHtml(fmtRelative(s))}</time>`;
  }
  function plural(n, word) { return `${n} ${word}${n === 1 ? '' : 's'}`; }

  function splitKeywords(str) { return (str || '').split(',').map(k => k.trim()).filter(Boolean); }

  // Mirrors scanner/keywords.py: word boundaries, case-insensitive, optional possessive.
  function highlight(text, keywordsStr) {
    const kws = splitKeywords(keywordsStr).sort((a, b) => b.length - a.length);
    if (!text || !kws.length) return escapeHtml(text);
    const alt = kws.map(k => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/ /g, '\\s+')).join('|');
    const re = new RegExp(`\\b((?:${alt})(?:['\u2019]?s)?)\\b`, 'gi');
    return text.split(re).map((part, i) => i % 2 ? `<mark>${escapeHtml(part)}</mark>` : escapeHtml(part)).join('');
  }

  function commentNumber(c) {
    return c.comment_index != null ? `${c.comment_index}${c.total_comments ? ' of ' + c.total_comments : ''}` : '';
  }
  function commentKey(c) { return `${c.article_url}#${c.comment_index ?? c.comment_text}`; }
  function isNew(c) { return previousKeys != null && !previousKeys.has(commentKey(c)); }

  function countBy(items) {
    const counts = new Map();
    for (const x of items) counts.set(x, (counts.get(x) || 0) + 1);
    return [...counts].sort((a, b) => b[1] - a[1]);
  }

  // options.previous: results of the scan before this one, used to tag new comments.
  function render(results, options = {}) {
    current = results;
    previousKeys = options.previous ? new Set(options.previous.comments.map(commentKey)) : null;
    filters.keywords.clear();
    filters.article = '';
    filters.onlyNew = false;
    collapsed.clear();
    renderStats();
    renderArticles();
    renderFilterControls();
    renderComments();
  }

  function renderStats() {
    const { articles, comments } = current;
    const articleCount = new Set(comments.map(c => c.article_url)).size;
    const authors = countBy(comments.map(c => c.author || 'Anonymous'));
    const keywords = countBy(comments.flatMap(c => splitKeywords(c.matched_keywords)));
    const newCount = previousKeys ? comments.filter(isNew).length : null;
    const card = (value, label, detail = '') => `
      <div class="stat"><div class="stat-value">${value}</div><div class="stat-label">${label}</div>
        ${detail ? `<div class="stat-detail">${detail}</div>` : ''}</div>`;
    $('stats').innerHTML = [
      card(comments.length, 'Comment mentions', `in ${plural(articleCount, 'article')}`),
      card(articles.length, 'Article mentions', 'in title or body'),
      newCount != null ? card(newCount, 'New since last scan', newCount ? 'tagged “new” below' : 'nothing new') : '',
      card(authors.length, 'Commenters', authors.slice(0, 3)
        .map(([a, n]) => `${escapeHtml(a)} <span class="muted">(${n})</span>`).join(', ')),
      keywords.length ? card(keywords.length === 1 ? escapeHtml(keywords[0][0]) : keywords.length,
        keywords.length === 1 ? 'Keyword matched' : 'Keywords matched',
        keywords.map(([k, n]) => `${escapeHtml(k)} <span class="muted">(${n})</span>`).join(', ')) : '',
    ].join('');
  }

  function renderArticles() {
    const { articles, comments } = current;
    const perArticle = new Map(countBy(comments.map(c => c.article_url)));
    $('articles-count').textContent = `(${articles.length}, newest first)`;
    $('articles-body').innerHTML = articles.length ? articles.map(a => `
      <tr>
        <td class="date-cell" title="${escapeHtml(fmtDateTime(a.article_date))}">${escapeHtml(fmtDate(a.article_date))}</td>
        <td><a href="${escapeHtml(a.url)}" target="_blank" rel="noopener"><strong>${escapeHtml(a.title)}</strong></a></td>
        <td class="snippet">${highlight(a.snippet_or_body, a.matched_keywords)}</td>
        <td><div class="keywords">${splitKeywords(a.matched_keywords).map(k => `<span>${escapeHtml(k)}</span>`).join('')}</div></td>
        <td class="nowrap">${perArticle.get(a.url) || 0}</td>
      </tr>`).join('') : '<tr><td colspan="5" class="empty">No matching articles.</td></tr>';
  }

  function renderFilterControls() {
    const keywords = countBy(current.comments.flatMap(c => splitKeywords(c.matched_keywords)));
    const chips = $('keyword-chips');
    chips.hidden = keywords.length < 2;
    chips.innerHTML = keywords.map(([k, n]) =>
      `<button type="button" class="chip" data-keyword="${escapeHtml(k)}" aria-pressed="false">${escapeHtml(k)} <span class="muted">${n}</span></button>`
    ).join('');

    const seen = new Map();
    for (const c of current.comments) if (!seen.has(c.article_url)) seen.set(c.article_url, c.article_title || c.article_url);
    $('article-filter').innerHTML = '<option value="">All articles</option>' + [...seen].map(([url, title]) =>
      `<option value="${escapeHtml(url)}">${escapeHtml(title)}</option>`).join('');

    $('only-new-wrap').hidden = !previousKeys;
    $('only-new').checked = false;
  }

  function filteredComments() {
    const q = filters.q.toLowerCase();
    const rows = current.comments.filter(c =>
      (!q || `${c.author} ${c.article_title} ${c.comment_text}`.toLowerCase().includes(q)) &&
      (!filters.keywords.size || splitKeywords(c.matched_keywords).some(k => filters.keywords.has(k))) &&
      (!filters.article || c.article_url === filters.article) &&
      (!filters.hideReplies || !c.is_reply) &&
      (!filters.onlyNew || isNew(c)));
    if (filters.sort === 'article') return rows;
    const dir = filters.sort === 'newest' ? -1 : 1;
    return rows.sort((a, b) => dir * ((parseUtc(a.comment_created_at) || 0) - (parseUtc(b.comment_created_at) || 0)));
  }

  function isFiltered() {
    return Boolean(filters.q || filters.keywords.size || filters.article || filters.hideReplies || filters.onlyNew);
  }

  function commentHtml(c, showArticle) {
    return `
      <li class="comment${isNew(c) ? ' is-new' : ''}">
        <div class="comment-meta">
          <span class="author">${escapeHtml(c.author || 'Anonymous')}</span>
          ${timeTag(c.comment_created_at)}
          <span class="comment-num" title="Position in the article's comment thread">#${escapeHtml(commentNumber(c) || '—')}</span>
          ${c.is_reply ? '<span class="tag">reply</span>' : ''}
          ${isNew(c) ? '<span class="tag tag-new">new</span>' : ''}
          ${showArticle ? `<a class="comment-article" href="${escapeHtml(c.article_url)}" target="_blank" rel="noopener">${escapeHtml(c.article_title || 'Article')}</a>` : ''}
        </div>
        <div class="comment-text">${highlight(c.comment_text, c.matched_keywords)}</div>
      </li>`;
  }

  function renderComments() {
    if (!current) return;
    const all = current.comments;
    const rows = filteredComments();
    const grouped = filters.sort === 'article';
    $('comments-count').textContent = isFiltered() ? `(${rows.length} of ${all.length})` : `(${all.length})`;
    $('clear-filters').hidden = !isFiltered();
    $('toggle-groups').hidden = !grouped || !rows.length;
    const list = $('comments-list');

    if (!rows.length) {
      list.innerHTML = `<p class="empty">${all.length ? 'No comments match the filters.' : 'No matching comments.'}</p>`;
      return;
    }
    if (!grouped) {
      list.innerHTML = `<ul class="comment-list flat">${rows.map(c => commentHtml(c, true)).join('')}</ul>`;
      return;
    }
    const groups = new Map();
    for (const c of rows) {
      if (!groups.has(c.article_url)) groups.set(c.article_url, []);
      groups.get(c.article_url).push(c);
    }
    list.innerHTML = [...groups.values()].map(cs => {
      const a = cs[0];
      return `
      <details class="article-group" data-url="${escapeHtml(a.article_url)}"${collapsed.has(a.article_url) ? '' : ' open'}>
        <summary>
          <span class="group-title">${escapeHtml(a.article_title || 'Article')}</span>
          <span class="group-meta">${escapeHtml(fmtDate(a.article_date))} · ${plural(cs.length, 'match')}${a.total_comments ? ` in ${a.total_comments} comments` : ''}</span>
          <a class="group-link" href="${escapeHtml(a.article_url)}" target="_blank" rel="noopener">Open article ↗</a>
        </summary>
        <ul class="comment-list">${cs.map(c => commentHtml(c, false)).join('')}</ul>
      </details>`;
    }).join('');
    updateToggleLabel();
  }

  function updateToggleLabel() {
    const groups = [...document.querySelectorAll('.article-group')];
    $('toggle-groups').textContent = groups.some(g => g.open) ? 'Collapse all' : 'Expand all';
  }

  function setAllGroups(open) {
    document.querySelectorAll('.article-group').forEach(g => { g.open = open; });
  }

  function clearFilters() {
    Object.assign(filters, { q: '', article: '', hideReplies: false, onlyNew: false });
    filters.keywords.clear();
    $('comment-filter').value = '';
    $('article-filter').value = '';
    $('hide-replies').checked = $('only-new').checked = false;
    document.querySelectorAll('#keyword-chips .chip').forEach(ch => ch.setAttribute('aria-pressed', 'false'));
    renderComments();
  }

  function csvCell(v) {
    let s = String(v ?? '');
    if (/^[=+\-@]/.test(s)) s = "'" + s;  // stop spreadsheet formula injection
    return `"${s.replace(/"/g, '""')}"`;
  }

  function exportCsv(filename) {
    if (!current) return;
    const rows = [['Type', 'Article date', 'Article', 'URL', 'Comment #', 'Comment posted', 'Author', 'Text', 'Keywords']];
    for (const a of current.articles) {
      rows.push(['Article', a.article_date, a.title, a.url, '', '', '', a.snippet_or_body, a.matched_keywords]);
    }
    for (const c of current.comments) {
      rows.push(['Comment', c.article_date, c.article_title, c.article_url, commentNumber(c), c.comment_created_at,
                 c.author, c.comment_text, c.matched_keywords]);
    }
    // BOM so Excel opens the file as UTF-8.
    const blob = new Blob(['\ufeff' + rows.map(r => r.map(csvCell).join(',')).join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  // Dropdown menus built from <details class="menu">: close on outside click, Escape, or choosing an item.
  function initMenus() {
    document.addEventListener('click', ev => {
      document.querySelectorAll('details.menu[open]').forEach(m => {
        if (!m.contains(ev.target) || ev.target.closest('.menu-items button')) m.open = false;
      });
    });
    document.addEventListener('keydown', ev => {
      if (ev.key === 'Escape') document.querySelectorAll('details.menu[open]').forEach(m => { m.open = false; });
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    $('comment-filter').addEventListener('input', ev => { filters.q = ev.target.value.trim(); renderComments(); });
    $('article-filter').addEventListener('change', ev => { filters.article = ev.target.value; renderComments(); });
    $('comment-sort').addEventListener('change', ev => { filters.sort = ev.target.value; renderComments(); });
    $('hide-replies').addEventListener('change', ev => { filters.hideReplies = ev.target.checked; renderComments(); });
    $('only-new').addEventListener('change', ev => { filters.onlyNew = ev.target.checked; renderComments(); });
    $('clear-filters').addEventListener('click', clearFilters);
    $('keyword-chips').addEventListener('click', ev => {
      const chip = ev.target.closest('.chip');
      if (!chip) return;
      const k = chip.dataset.keyword;
      if (filters.keywords.has(k)) filters.keywords.delete(k); else filters.keywords.add(k);
      chip.setAttribute('aria-pressed', String(filters.keywords.has(k)));
      renderComments();
    });
    $('toggle-groups').addEventListener('click', () =>
      setAllGroups(![...document.querySelectorAll('.article-group')].some(g => g.open)));
    // 'toggle' does not bubble, so listen in the capture phase.
    $('comments-list').addEventListener('toggle', ev => {
      const url = ev.target.dataset?.url;
      if (!url) return;
      if (ev.target.open) collapsed.delete(url); else collapsed.add(url);
      updateToggleLabel();
    }, true);
    window.addEventListener('beforeprint', () => setAllGroups(true));
    initMenus();
  });

  return { render, exportCsv, escapeHtml, fmtDateTime, fmtRelative, ageHours };
})();
