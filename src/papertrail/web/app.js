const app = document.querySelector('#app');
let data;
let preferenceData;
let favoritePapers = [];
let favoriteIds = new Set();
const expandedFavoriteIds = new Set();

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

async function loadDashboard() {
  const response = await fetch('/v1/dashboard');
  if (!response.ok) throw new Error('Dashboard unavailable');
  data = await response.json();
  if (data?.totals) data.totals.favorites = favoritePapers.length;
}

async function loadPreferences() {
  const response = await fetch('/v1/preferences');
  if (!response.ok) throw new Error('Research profile unavailable');
  preferenceData = await response.json();
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
        await loadDashboard();
        await renderRoute();
      } catch (error) {
        button.disabled = false;
        button.title = error.message;
      }
    };
  });
}

function bindInterestForm() {
  const form = document.querySelector('#interest-form');
  if (!form) return;
  form.onsubmit = async event => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const status = form.querySelector('.interest-status');
    button.disabled = true;
    status.textContent = 'Understanding and applying your interests…';
    try {
      const response = await fetch('/v1/preferences/explicit', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text: form.elements.interests.value})
      });
      const value = await response.json();
      if (!response.ok) throw new Error(value.message || 'Could not save interests');
      await Promise.all([loadPreferences(), loadDashboard(), loadFavorites()]);
      await renderRoute();
    } catch (error) {
      button.disabled = false;
      status.textContent = error.message;
      status.classList.add('failed');
    }
  };
  form.querySelector('[data-interest-cancel]').onclick = () => {
    form.elements.interests.value = preferenceData?.explicit?.text || '';
  };
}

function bindFavoriteToggles() {
  document.querySelectorAll('[data-favorite-toggle]').forEach(button => {
    button.onclick = () => {
      const paperId = button.dataset.favoriteToggle;
      if (expandedFavoriteIds.has(paperId)) expandedFavoriteIds.delete(paperId);
      else expandedFavoriteIds.add(paperId);
      renderRoute();
    };
  });
}

function rankingReasons(paper) {
  const reasons = paper.ranking?.reasons || [];
  return reasons.length ? `<div class="ranking-reasons">${reasons.map(reason =>
    `<span>${esc(reason)}</span>`).join('')}</div>` : '';
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

function groupCard(group, limit = 4, expandable = false) {
  const clusterId = String(group.cluster_id || '');
  const availableCount = group.papers.length;
  const canExpand = expandable && availableCount > limit;
  const visiblePapers = group.papers.slice(0, limit);
  const paperListId = `group-papers-${clusterId.replace(/[^a-zA-Z0-9_-]/g, '-')}`;
  const papers = visiblePapers.map(paper =>
    `<div class="group-paper"><div><a href="#/paper/${encodeURIComponent(paper.paper_id)}">` +
    `${esc(paper.title)}</a>${rankingReasons(paper)}</div><span>` +
    `${paper.citation_count != null ? `<small>${number(paper.citation_count)} cites</small>` : ''}` +
    `${paper.is_new ? '<small>New</small>' : ''}` +
    `<a class="source" href="${safeUrl(paper.source_url)}" target="_blank" rel="noreferrer">Source ↗</a>` +
    `${starButton(paper.paper_id, true)}</span></div>`
  ).join('');
  const toggle = canExpand
    ? `<a class="group-toggle" href="#/groups/${encodeURIComponent(clusterId)}">` +
      `View all ${availableCount} papers →</a>`
    : '';
  return `<article class="group-card"><span class="group-count">${group.paper_count} papers · ` +
    `${group.new_paper_count} new</span><h3>${esc(group.label)}</h3>` +
    `<p class="date">${esc(group.description)}</p><div class="tags">` +
    `${group.top_terms.slice(0, 5).map(term => `<span class="tag">${esc(term)}</span>`).join('')}</div>` +
    `<div class="group-papers" id="${esc(paperListId)}">${papers}</div>${toggle}</article>`;
}

function groupDetail(clusterId) {
  const organization = data.organization;
  const group = (organization?.groups || []).find(item => item.cluster_id === clusterId);
  if (!group) {
    app.innerHTML = '<div class="empty">Research neighborhood not found.</div>';
    return;
  }
  const papers = group.papers.map((paper, index) =>
    `<article class="group-detail-paper"><span class="group-rank">${index + 1}</span><div>` +
    `<a class="favorite-title" href="#/paper/${encodeURIComponent(paper.paper_id)}">${esc(paper.title)}</a>` +
    `<p>${esc((paper.authors || []).slice(0, 4).join(', '))}${(paper.authors || []).length > 4 ? ' et al.' : ''}</p>` +
    `${rankingReasons(paper)}</div><div class="group-paper-actions">` +
    `${paper.citation_count == null ? '' : `<small>${number(paper.citation_count)} cites</small>`}` +
    `<a class="source" href="${safeUrl(paper.source_url)}" target="_blank" rel="noreferrer">Source ↗</a>` +
    `${starButton(paper.paper_id, true)}</div></article>`
  ).join('');
  app.innerHTML = `<div class="topline"><a class="article-back" href="#/groups">← All research groups</a>` +
    `<span class="date">${group.paper_count} papers · ranked for you</span></div>` +
    `<span class="eyebrow">Problem neighborhood</span><h1>${esc(group.label)}</h1>` +
    `<p class="lede">${esc(group.description)}</p><div class="tags">` +
    `${group.top_terms.map(term => `<span class="tag">${esc(term)}</span>`).join('')}</div>` +
    `<section class="group-detail-list">${papers}</section>` +
    `<p class="method-note">Membership is LLM-adjudicated from abstracts and extracted scientific ` +
    `features. Ordering blends that relevance with your LLM-structured profile, recency, and ` +
    `age-normalized citations.</p>`;
}

function interestCard() {
  const explicit = preferenceData?.explicit || {};
  const profile = preferenceData?.profile || {};
  const concepts = (profile.concepts || []).slice(0, 8);
  const state = explicit.extraction_status === 'pending'
    ? 'Saved · deeper understanding will retry with the next daily run'
    : explicit.updated_at ? `Saved ${esc(explicit.updated_at.slice(0, 10))}` : 'Add interests anytime';
  return `<section class="interest-card"><div class="interest-copy"><span class="eyebrow">Personalization</span>` +
    `<h2>Your research interests</h2><p>Write naturally. These explicit interests guide current ` +
    `rankings, future ingestion, and selective deep dives—and outweigh implicitly learned signals.</p>` +
    (concepts.length ? `<div class="interest-signals">${concepts.map(item =>
      `<span class="${item.polarity === 'negative' ? 'negative' : ''}">${esc(item.label)}</span>`
    ).join('')}</div>` : '') + `</div><form id="interest-form"><label for="interests">Areas, ` +
    `problems, methods, or work you want less of</label><textarea id="interests" name="interests" ` +
    `maxlength="12000" placeholder="I care about agents that adapt to changing tools…">` +
    `${esc(explicit.text || '')}</textarea><div class="interest-actions"><span class="interest-status">` +
    `${state}</span><button type="button" class="button secondary" data-interest-cancel>Cancel</button>` +
    `<button type="submit" class="button">Save interests</button></div></form></section>`;
}

function dashboard() {
  const digest = data.digests.find(item => item.status === 'complete');
  const blogs = data.blogs;
  app.innerHTML = `<div class="topline"><span class="eyebrow">Daily intelligence</span>` +
    `<span class="date">${esc(digest?.run_date || 'Waiting for the first digest')}</span></div>` +
    `<h1>${esc(digest?.headline || 'Your research trail starts here.')}</h1>` +
    `<p class="lede">${esc(digest?.synthesis || 'PaperTrail is indexing evidence. Daily patterns and agent-written deep dives will appear after the next completed run.')}</p>` +
    interestCard() +
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
    `<section class="group-grid">${organization.groups.map(group => groupCard(group, 8, true)).join('')}</section>` +
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

function favoriteRow(paper) {
  const expanded = expandedFavoriteIds.has(paper.paper_id);
  const detailId = `favorite-detail-${paper.paper_id.replace(/[^a-zA-Z0-9_-]/g, '-')}`;
  return `<article class="favorite-row"><div class="favorite-summary"><button type="button" ` +
    `class="favorite-expand" data-favorite-toggle="${esc(paper.paper_id)}" aria-expanded="${expanded}" ` +
    `aria-controls="${esc(detailId)}"><span aria-hidden="true">${expanded ? '−' : '+'}</span></button>` +
    `<div><a class="favorite-title" href="#/paper/${encodeURIComponent(paper.paper_id)}">` +
    `${esc(paper.title)}</a><p>${esc((paper.authors || []).slice(0, 3).join(', '))}` +
    `${(paper.authors || []).length > 3 ? ' et al.' : ''} · ${esc(paper.published_date || 'Undated')}</p>` +
    `${rankingReasons(paper)}</div><span class="saved-date">Saved ${esc((paper.favorited_at || '').slice(0, 10))}</span>` +
    `${starButton(paper.paper_id, true)}</div>` +
    (expanded ? `<div class="favorite-detail" id="${esc(detailId)}"><p>${esc(paper.abstract || 'No abstract is available.')}</p>` +
      `<div class="favorite-meta"><span>${paper.citation_count == null ? 'Citation metadata unavailable' : `${number(paper.citation_count)} citations`}</span>` +
      `<span>${esc((paper.authors || []).join(', '))}</span></div><div class="actions">` +
      `<a class="button" href="#/paper/${encodeURIComponent(paper.paper_id)}">View paper</a>` +
      `<a class="button secondary" href="${safeUrl(paper.source_url)}" target="_blank" rel="noreferrer">Open source ↗</a>` +
      `</div></div>` : '') + `</article>`;
}

function favorites() {
  app.innerHTML = `<div class="topline"><span class="eyebrow">Personal library</span>` +
    `<span class="date">${favoritePapers.length} saved</span></div><h1>Favourites</h1>` +
    `<p class="lede">The papers you want to return to, kept locally with the rest of your research trail.</p>` +
    (favoritePapers.length ? `<section class="favorite-list">${favoritePapers.map(favoriteRow).join('')}</section>` :
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
    `<div class="actions"><a class="button" href="#/paper/${encodeURIComponent(value.paper_id)}">View paper</a>` +
    `<a class="button secondary" href="${safeUrl(value.source_url)}" target="_blank" rel="noreferrer">Open source ↗</a></div>` +
    `<div class="panel selection-panel"><span class="eyebrow">${selectionChip(value)} Why this was picked</span>` +
    `<p>${esc(value.selection_reason)}</p></div>` +
    `<div class="panel" style="margin-bottom:35px"><span class="eyebrow">Why it surprised us</span>` +
    `<p class="why">${esc(value.surprise)}</p></div><div class="prose">${markdown(value.markdown)}</div>` +
    `${value.figure_ids.map((id, index) => `<figure class="figure"><img loading="lazy" ` +
      `src="/v1/figures/${encodeURIComponent(id)}/image" alt="Paper figure ${index + 1}">` +
      `<figcaption>Visual evidence ${esc(id)} from the immutable local paper version.</figcaption></figure>`).join('')}</article>`;
}

async function paperDetail(paperId) {
  const response = await fetch(`/v1/papers/${encodeURIComponent(paperId)}`);
  if (!response.ok) throw new Error('Paper not found');
  const payload = await response.json();
  const value = payload.results?.[0];
  if (!value) throw new Error('Paper not found');
  const localArtifact = value.artifact_available
    ? `<div class="paper-frame"><iframe title="Full paper: ${esc(value.canonical_title)}" ` +
      `src="/v1/papers/${encodeURIComponent(paperId)}/artifact"></iframe></div>`
    : '<div class="empty">The local paper artifact is not available yet.</div>';
  app.innerHTML = `<article class="paper-detail"><a class="article-back" href="#/groups">← Research groups</a>` +
    `<div class="paper-title-row"><div><span class="eyebrow">Local paper reader</span>` +
    `<h1>${esc(value.canonical_title)}</h1></div>${starButton(paperId)}</div>` +
    `<div class="meta"><span>${esc((value.authors || []).join(', '))}</span>` +
    `<span>${esc(value.published_date || 'Undated')}</span>` +
    `<span>${value.citation_count == null ? 'Citation metadata unavailable' : `${number(value.citation_count)} citations`}</span></div>` +
    `<p class="paper-abstract">${esc(value.abstract || 'No abstract is available.')}</p>` +
    `<div class="actions"><a class="button secondary" href="${safeUrl(value.source_url)}" ` +
    `target="_blank" rel="noreferrer">Open source ↗</a></div>${localArtifact}` +
    ((value.figures || []).length ? `<div class="section-head"><h2>Extracted figures</h2><p>${value.figures.length} visuals</p></div>` +
      `<div class="reader-figures">${value.figures.map(figure => `<figure><img loading="lazy" ` +
        `src="/v1/figures/${encodeURIComponent(figure.figure_id)}/image" alt="${esc(figure.label)}">` +
        `<figcaption>${esc(figure.caption || figure.label)}</figcaption></figure>`).join('')}</div>` : '') +
    `</article>`;
}

async function renderRoute() {
  const parts = location.hash.slice(2).split('/');
  document.querySelectorAll('nav a').forEach(link =>
    link.classList.toggle('active', link.getAttribute('href') === location.hash)
  );
  if (parts[0] === 'blog') await blog(decodeURIComponent(parts.slice(1).join('/')));
  else if (parts[0] === 'paper') await paperDetail(decodeURIComponent(parts.slice(1).join('/')));
  else if (parts[0] === 'groups' && parts[1]) groupDetail(decodeURIComponent(parts.slice(1).join('/')));
  else if (parts[0] === 'groups') groups();
  else if (parts[0] === 'archive') archive();
  else if (parts[0] === 'favorites') favorites();
  else dashboard();
  bindStars();
  bindFavoriteToggles();
  bindInterestForm();
}

async function route() {
  try {
    if (!data) {
      await Promise.all([loadDashboard(), loadFavorites(), loadPreferences()]);
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
