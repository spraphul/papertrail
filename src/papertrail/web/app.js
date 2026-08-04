const app = document.querySelector('#app');
let data;
let favoritePapers = [];
let favoriteIds = new Set();

const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[character]));
const safeUrl = value => /^https?:\/\//i.test(String(value || '')) ? esc(value) : '#';
const number = value => new Intl.NumberFormat().format(value || 0);

function markdown(raw) {
  let value = esc(raw);
  value = value.replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h2>$1</h2>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
    .replace(/\[(ev_[a-z0-9_]+)\]/g, '<code>[$1]</code>');
  return value.split(/\n\s*\n/).map(block =>
    /^<h[23]>/.test(block) ? block : `<p>${block.replace(/\n/g, '<br>')}</p>`
  ).join('');
}

function starButton(paperId, compact = false) {
  const favorite = favoriteIds.has(paperId);
  return `<button class="star-button${compact ? ' compact' : ''}${favorite ? ' selected' : ''}" ` +
    `data-favorite-paper="${esc(paperId)}" aria-pressed="${favorite}" ` +
    `title="${favorite ? 'Remove from favourites' : 'Add to favourites'}">` +
    `<span aria-hidden="true">${favorite ? '★' : '☆'}</span>` +
    `<span class="star-label">${favorite ? 'Saved' : 'Save'}</span></button>`;
}

async function loadFavorites() {
  const response = await fetch('/v1/favorites');
  if (!response.ok) throw new Error('Favourites unavailable');
  const value = await response.json();
  favoritePapers = value.favorites || [];
  favoriteIds = new Set(favoritePapers.map(paper => paper.paper_id));
  const count = document.querySelector('#favorite-count');
  if (count) count.textContent = number(value.count);
  if (data?.totals) data.totals.favorites = value.count;
}

function bindStars() {
  document.querySelectorAll('[data-favorite-paper]').forEach(button => {
    button.onclick = async event => {
      event.preventDefault();
      event.stopPropagation();
      const paperId = button.dataset.favoritePaper;
      const desired = !favoriteIds.has(paperId);
      button.disabled = true;
      try {
        const response = await fetch(`/v1/favorites/${encodeURIComponent(paperId)}`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({favorite: desired})
        });
        if (!response.ok) throw new Error('Could not update favourites');
        await loadFavorites();
        await renderRoute();
      } catch (error) {
        button.disabled = false;
        button.title = error.message;
      }
    };
  });
}

function selectionChip(blog) {
  const mode = blog.selection_mode || 'editorial';
  const labels = {preference: 'For you', exploration: 'Explore', editorial: 'Editor pick'};
  return `<span class="selection-chip ${esc(mode)}">${labels[mode] || labels.editorial}</span>`;
}

function card(blog) {
  return `<article class="blog-card"><div class="card-top"><div class="tags">` +
    `${selectionChip(blog)}${blog.themes.slice(0, 2).map(theme => `<span class="tag">${esc(theme)}</span>`).join('')}</div>` +
    `${starButton(blog.paper_id, true)}</div><h3>${esc(blog.title)}</h3><p>${esc(blog.dek)}</p>` +
    `<p class="selection-reason">${esc(blog.selection_reason)}</p><p class="why">${esc(blog.surprise)}</p>` +
    `<a href="#/blog/${encodeURIComponent(blog.slug)}">Read the deep dive →</a></article>`;
}

function rhythm(items) {
  if (!items.length) return '<p class="date">No dated papers yet.</p>';
  const maximum = Math.max(...items.map(item => item.count), 1);
  return `<div class="rhythm" aria-label="Papers indexed by publication day">` +
    items.map(item => `<i title="${esc(item.day)} · ${item.count} papers" ` +
      `style="height:${Math.max(5, Math.round(item.count / maximum * 100))}%"></i>`).join('') +
    `</div><div class="rhythm-labels"><span>${esc(items[0].day)}</span>` +
    `<span>${esc(items.at(-1).day)}</span></div>`;
}

function groupCard(group, limit = 4) {
  const papers = group.papers.slice(0, limit).map(paper =>
    `<div class="group-paper"><a href="/v1/papers/${encodeURIComponent(paper.paper_id)}/artifact" ` +
    `target="_blank">${esc(paper.title)}</a><span>${paper.is_new ? '<small>New</small>' : ''}` +
    `<a class="source" href="${safeUrl(paper.source_url)}" target="_blank" rel="noreferrer">Source ↗</a>` +
    `${starButton(paper.paper_id, true)}</span></div>`
  ).join('');
  return `<article class="group-card"><span class="group-count">${group.paper_count} papers · ` +
    `${group.new_paper_count} new</span><h3>${esc(group.label)}</h3>` +
    `<p class="date">${esc(group.description)}</p><div class="tags">` +
    `${group.top_terms.slice(0, 5).map(term => `<span class="tag">${esc(term)}</span>`).join('')}</div>` +
    `<div class="group-papers">${papers}</div></article>`;
}

function dashboard() {
  const digest = data.digests.find(item => item.status === 'complete');
  const blogs = data.blogs;
  app.innerHTML = `<div class="topline"><span class="eyebrow">Daily intelligence</span>` +
    `<span class="date">${esc(digest?.run_date || 'Waiting for the first digest')}</span></div>` +
    `<h1>${esc(digest?.headline || 'Your research trail starts here.')}</h1>` +
    `<p class="lede">${esc(digest?.synthesis || 'PaperTrail is indexing evidence. Daily patterns and agent-written deep dives will appear after the next completed run.')}</p>` +
    `<section class="stats"><div class="stat"><strong>${number(data.totals.papers)}</strong><span>Papers indexed</span></div>` +
    `<div class="stat"><strong>${number(data.totals.evidence)}</strong><span>Evidence passages</span></div>` +
    `<div class="stat"><strong>${number(data.totals.figures)}</strong><span>Paper figures</span></div>` +
    `<div class="stat"><strong>${number(data.totals.favorites)}</strong><span>Favourites</span></div></section>` +
    `<div class="panel"><span class="eyebrow">Publication rhythm · recent indexed dates</span>${rhythm(data.papers_by_day)}</div>` +
    `<div class="section-head"><h2>Worth your attention</h2><p>Evidence-first, not popularity-first</p></div>` +
    (blogs.length ? `<section class="blog-grid">${blogs.slice(0, 3).map(card).join('')}</section>` :
      '<div class="empty">No deep dives yet. Run <code>papertrail daily</code> after setup.</div>') +
    `<div class="section-head" id="signals"><h2>Signals across the corpus</h2></div>` +
    `<section class="trend-layout"><div class="panel"><span class="eyebrow">Emerging patterns</span>` +
    `<div class="trend-list">${(digest?.trends || []).map(trend => `<div class="trend">${esc(trend)}</div>`).join('') || '<p class="date">Patterns arrive with the first daily analysis.</p>'}</div></div>` +
    `<div class="panel"><span class="eyebrow">Recurring themes</span>` +
    `${data.themes.map(theme => `<div class="theme-row"><span>${esc(theme.theme)}</span><strong>${theme.count}</strong></div>`).join('') || '<p class="date">No themes yet.</p>'}</div></section>`;
  const groups = data.organization?.groups || [];
  if (groups.length) {
    document.querySelector('#signals').insertAdjacentHTML('beforebegin',
      `<div class="section-head"><h2>Research neighborhoods</h2><p>` +
      `<a class="article-back" href="#/groups">Explore all ${groups.length} groups →</a></p></div>` +
      `<section class="group-grid">${groups.slice(0, 4).map(group => groupCard(group, 3)).join('')}</section>`
    );
  }
}

function groups() {
  const organization = data.organization;
  if (!organization) {
    app.innerHTML = '<div class="empty">No organization run yet. Run <code>papertrail organize --snapshot SNAPSHOT_ID</code>.</div>';
    return;
  }
  const refinement = organization.configuration?.llm_refinement;
  const method = refinement?.enabled
    ? `Hybrid candidates were adjudicated by ${esc(refinement.model || 'the reasoning model')}; ${number(refinement.fallback_clusters)} batches used the safe fallback.`
    : 'These groups use hybrid semantic and lexical candidate signals.';
  app.innerHTML = `<div class="topline"><span class="eyebrow">Corpus organization</span>` +
    `<span class="date">${esc(organization.run_date)}</span></div><h1>Research groups</h1>` +
    `<p class="lede">${organization.paper_count} papers consolidated into ${organization.cluster_count} problem neighborhoods. ` +
    `${organization.semantic_paper_count} papers had semantic coverage.</p>` +
    `<div class="section-head"><h2>Problem neighborhoods</h2><p>${esc(organization.snapshot_id)}</p></div>` +
    `<section class="group-grid">${organization.groups.map(group => groupCard(group, 8)).join('')}</section>` +
    `<p class="method-note">Groups are navigation aids, not scientific claims. ${method}</p>`;
}

function archive() {
  app.innerHTML = `<div class="topline"><span class="eyebrow">Archive</span>` +
    `<span class="date">${data.blogs.length} essays</span></div><h1>Deep dives</h1>` +
    `<p class="lede">Long-form readings grounded in local full text, exact evidence, and inspected paper figures.</p>` +
    `<div class="section-head"><h2>All writing</h2></div>` +
    (data.blogs.length ? `<section class="blog-grid">${data.blogs.map(card).join('')}</section>` :
      '<div class="empty">The archive is empty.</div>');
}

function favoriteCard(paper) {
  return `<article class="favorite-card"><div class="card-top"><span class="eyebrow">Saved paper</span>` +
    `${starButton(paper.paper_id, true)}</div><h3>${esc(paper.title)}</h3>` +
    `<p class="favorite-authors">${esc((paper.authors || []).join(', '))}</p>` +
    `<p>${esc(paper.abstract || 'No abstract is available.')}</p><div class="favorite-meta">` +
    `<span>${esc(paper.published_date || 'Undated')}</span><span>Saved ${esc(paper.favorited_at.slice(0, 10))}</span></div>` +
    `<div class="actions"><a class="button" href="/v1/papers/${encodeURIComponent(paper.paper_id)}/artifact" target="_blank">Read paper</a>` +
    `<a class="button secondary" href="${safeUrl(paper.source_url)}" target="_blank" rel="noreferrer">Open source ↗</a></div></article>`;
}

function favorites() {
  app.innerHTML = `<div class="topline"><span class="eyebrow">Personal library</span>` +
    `<span class="date">${favoritePapers.length} saved</span></div><h1>Favourites</h1>` +
    `<p class="lede">The papers you want to return to, kept locally with the rest of your research trail.</p>` +
    (favoritePapers.length ? `<section class="favorite-grid">${favoritePapers.map(favoriteCard).join('')}</section>` :
      '<div class="empty">No favourites yet. Use the star beside a paper or deep dive to save it here.</div>');
}

async function blog(slug) {
  const response = await fetch('/v1/blogs/' + encodeURIComponent(slug));
  if (!response.ok) throw new Error('Blog not found');
  const value = await response.json();
  app.innerHTML = `<article class="article"><a class="article-back" href="#/archive">← All deep dives</a>` +
    `<div class="eyebrow" style="margin-top:42px">Worth a read · ${esc(value.run_date)}</div>` +
    `<div class="article-title-row"><h1>${esc(value.title)}</h1>${starButton(value.paper_id)}</div>` +
    `<p class="dek">${esc(value.dek)}</p><div class="meta"><span>${esc(value.paper_title)}</span>` +
    `<span>${esc((value.authors || []).join(', '))}</span><span>${esc(value.published_date || '')}</span></div>` +
    `<div class="actions"><button class="button" id="read-paper">Read paper</button>` +
    `<a class="button secondary" href="${safeUrl(value.source_url)}" target="_blank" rel="noreferrer">Open source ↗</a></div>` +
    `<div class="panel selection-panel"><span class="eyebrow">${selectionChip(value)} Why this was picked</span>` +
    `<p>${esc(value.selection_reason)}</p></div>` +
    `<div class="panel" style="margin-bottom:35px"><span class="eyebrow">Why it surprised us</span>` +
    `<p class="why">${esc(value.surprise)}</p></div><div class="prose">${markdown(value.markdown)}</div>` +
    `${value.figure_ids.map((id, index) => `<figure class="figure"><img loading="lazy" ` +
      `src="/v1/figures/${encodeURIComponent(id)}/image" alt="Paper figure ${index + 1}">` +
      `<figcaption>Visual evidence ${esc(id)} from the immutable local paper version.</figcaption></figure>`).join('')}</article>`;
  document.querySelector('#read-paper').onclick = () => reader(value);
}

function reader(paper) {
  const shell = document.createElement('div');
  shell.className = 'pdf-shell';
  shell.innerHTML = `<div class="pdf-bar"><span>${esc(paper.paper_title || paper.title)}</span>` +
    `<button aria-label="Close">×</button></div><iframe title="${esc(paper.paper_title || paper.title)}" ` +
    `src="/v1/papers/${encodeURIComponent(paper.paper_id)}/artifact"></iframe>`;
  shell.querySelector('button').onclick = () => shell.remove();
  document.body.append(shell);
}

async function renderRoute() {
  const parts = location.hash.slice(2).split('/');
  document.querySelectorAll('nav a').forEach(link =>
    link.classList.toggle('active', link.getAttribute('href') === location.hash)
  );
  if (parts[0] === 'blog') await blog(decodeURIComponent(parts.slice(1).join('/')));
  else if (parts[0] === 'groups') groups();
  else if (parts[0] === 'archive') archive();
  else if (parts[0] === 'favorites') favorites();
  else dashboard();
  bindStars();
}

async function route() {
  try {
    if (!data) {
      const [dashboardResponse] = await Promise.all([fetch('/v1/dashboard'), loadFavorites()]);
      if (!dashboardResponse.ok) throw new Error('Dashboard unavailable');
      data = await dashboardResponse.json();
      data.totals.favorites = favoritePapers.length;
      document.querySelector('#rail-count').textContent = number(data.totals.papers) + ' papers';
    }
    await renderRoute();
  } catch (error) {
    app.innerHTML = `<div class="error">${esc(error.message)}</div>`;
  }
}

window.addEventListener('hashchange', route);
route();
