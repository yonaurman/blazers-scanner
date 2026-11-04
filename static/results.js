// Results rendering shared by the live dashboard (index.html) and the static site (site.html).
// Expects the markup from templates/_results.html.

const Results = (() => {
  const $ = id => document.getElementById(id);
  let current = null;

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

  function render(results) {
    current = results;
    const { articles } = results;
    $('articles-count').textContent = `(${articles.length}, newest first)`;
    $('articles-body').innerHTML = articles.length ? articles.map(a => `
      <tr>
        <td class="date-cell">${escapeHtml(fmtDate(a.article_date))}</td>
        <td><a href="${escapeHtml(a.url)}" target="_blank" rel="noopener"><strong>${escapeHtml(a.title)}</strong></a></td>
        <td class="snippet">${highlight(a.snippet_or_body, a.matched_keywords)}</td>
        <td><div class="keywords">${splitKeywords(a.matched_keywords).map(k => `<span>${escapeHtml(k)}</span>`).join('')}</div></td>
      </tr>`).join('') : '<tr><td colspan="4" class="empty">No matching articles.</td></tr>';
    renderComments();
  }

  function renderComments() {
    if (!current) return;
    const q = $('comment-filter').value.trim().toLowerCase();
    const all = current.comments;
    const rows = q ? all.filter(c => `${c.author} ${c.article_title} ${c.comment_text}`.toLowerCase().includes(q)) : all;
    $('comments-count').textContent = q ? `(${rows.length} of ${all.length})` : `(${all.length}, grouped by article, newest first)`;
    let lastArticle = null;
    $('comments-body').innerHTML = rows.length ? rows.map(c => {
      const first = c.article_url !== lastArticle;
      lastArticle = c.article_url;
      return `
      <tr class="${first ? 'group-start' : ''}">
        <td>${first ? `<a href="${escapeHtml(c.article_url)}" target="_blank" rel="noopener">${escapeHtml(c.article_title || 'Article')}</a>
              <div class="date-cell">${escapeHtml(fmtDate(c.article_date))}</div>` : ''}</td>
        <td class="nowrap">${escapeHtml(commentNumber(c) || '—')}${c.is_reply ? ' <span class="reply-tag">reply</span>' : ''}</td>
        <td class="date-cell">${escapeHtml(fmtDateTime(c.comment_created_at))}</td>
        <td>${escapeHtml(c.author)}</td>
        <td class="snippet comment-text">${highlight(c.comment_text, c.matched_keywords)}</td>
      </tr>`;
    }).join('') : `<tr><td colspan="5" class="empty">${q ? 'No comments match the filter.' : 'No matching comments.'}</td></tr>`;
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

  document.addEventListener('DOMContentLoaded', () => $('comment-filter').addEventListener('input', renderComments));

  return { render, exportCsv, escapeHtml, fmtDateTime };
})();
