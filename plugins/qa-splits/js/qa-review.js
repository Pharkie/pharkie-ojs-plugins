/**
 * QA Splits — review interface
 *
 * Vanilla JS app for the 3-pane QA review screen.
 * Uses PDF.js for PDF rendering, fetches data from the plugin API.
 *
 * Keyboard shortcuts (when not in text input):
 *   Left/Right arrow  — previous/next article
 *   A                 — approve
 *   R                 — reject (opens comment box)
 *   ?                 — show shortcut help
 */

(function () {
    'use strict';

    const API = window.QA_CONFIG.apiBase;
    const PLUGIN_URL = window.QA_CONFIG.pluginUrl;

    // State
    let articles = [];
    let currentIndex = -1;
    let lastSeenId = parseInt(localStorage.getItem('qa-last-seen'), 10) || null;
    let pdfDoc = null;
    let loadGeneration = 0;     // Incremented per loadArticle to cancel stale fetches
    let scrollHandler = null;   // Stored ref for cleanup

    // DOM refs
    const els = {};
    const elIds = [
        'qa-title', 'qa-status', 'qa-authors', 'qa-section', 'qa-issue',
        'qa-pages', 'qa-progress', 'pdf-page-info', 'pdf-container',
        'html-content', 'endmatter-items',
        'btn-approve', 'btn-reject', 'reject-comment', 'btn-submit-reject',
        'btn-last-seen', 'btn-prev', 'btn-next', 'btn-random', 'btn-problem',
    ];

    function init() {
        elIds.forEach(id => els[id] = document.getElementById(id));

        els['qa-authors'].setAttribute('data-label', 'Authors:');
        els['qa-section'].setAttribute('data-label', 'Section:');
        els['qa-issue'].setAttribute('data-label', 'Issue:');
        els['qa-pages'].setAttribute('data-label', 'ID:');

        if (window.pdfjsLib) {
            pdfjsLib.GlobalWorkerOptions.workerSrc = PLUGIN_URL + '/js/pdf.worker.min.js';
        }

        bindEvents();
        loadArticles();
    }

    function bindEvents() {
        els['btn-approve'].addEventListener('click', () => submitReview('approved'));
        els['btn-reject'].addEventListener('click', showRejectInput);
        els['btn-submit-reject'].addEventListener('click', () => {
            const comment = els['reject-comment'].value.trim();
            if (!comment) {
                toast('Comment required for rejection', 'error');
                els['reject-comment'].focus();
                return;
            }
            submitReview('rejected', comment);
        });

        els['btn-prev'].addEventListener('click', () => navigate(-1));
        els['btn-next'].addEventListener('click', () => navigate(1));
        els['btn-last-seen'].addEventListener('click', goToLastSeen);
        els['btn-random'].addEventListener('click', goToRandom);
        els['btn-problem'].addEventListener('click', goToProblem);
        els['qa-progress'].addEventListener('click', showDashboard);

        document.addEventListener('keydown', (e) => {
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

            switch (e.key) {
                case 'ArrowLeft':  e.preventDefault(); navigate(-1); break;
                case 'ArrowRight': e.preventDefault(); navigate(1); break;
                case 'a': case 'A': e.preventDefault(); submitReview('approved'); break;
                case 'r': case 'R': e.preventDefault(); showRejectInput(); break;
                case '?': e.preventDefault(); showHelp(); break;
            }
        });

        els['reject-comment'].addEventListener('keydown', (e) => {
            if (e.key === 'Escape') hideRejectInput();
            if (e.key === 'Enter') {
                e.preventDefault();
                const comment = els['reject-comment'].value.trim();
                if (!comment) {
                    toast('Comment required', 'error');
                    return;
                }
                submitReview('rejected', comment);
            }
        });
    }

    // ── Data loading ──

    async function loadArticles() {
        try {
            const res = await fetch(API + '/articles', { credentials: 'same-origin' });
            const data = await res.json();
            articles = data.articles || [];

            updateProgress(data.counts);

            if (data.warnings && data.warnings.length > 0) {
                console.warn('QA Splits — unimported JATS files:', data.warnings);
            }

            if (articles.length === 0) {
                els['qa-title'].textContent = 'No articles found';
                return;
            }

            let startIndex = 0;
            if (lastSeenId) {
                const idx = articles.findIndex(a => a.submission_id === lastSeenId);
                if (idx >= 0) startIndex = idx;
            }

            loadArticle(startIndex);
        } catch (err) {
            els['qa-title'].textContent = 'Error loading articles';
            console.error('Failed to load articles:', err);
        }
    }

    async function loadArticle(index) {
        if (index < 0 || index >= articles.length) return;

        // Increment generation to invalidate any in-flight requests
        const gen = ++loadGeneration;
        currentIndex = index;
        const article = articles[index];

        localStorage.setItem('qa-last-seen', article.submission_id);
        lastSeenId = article.submission_id;

        // Update metadata
        els['qa-title'].textContent = article.title;
        els['qa-authors'].textContent = article.authors.join(', ');
        els['qa-section'].textContent = article.section;
        els['qa-issue'].textContent = 'Vol ' + article.volume + ' No ' + article.number + ' (' + article.year + ')';
        els['qa-pages'].textContent = '#' + article.submission_id;

        updateStatusBadge(article.status, article.reviewer, article.reviewed_at, article.comment);
        hideRejectInput();

        // Check hash validity per-article (deferred from list to avoid O(n) file reads)
        if (article.status === 'approved' || article.status === 'rejected') {
            checkHashValidity(article, gen);
        }

        els['btn-prev'].disabled = (index === 0);
        els['btn-next'].disabled = (index === articles.length - 1);

        // Load content in parallel, checking generation before rendering
        loadPdf(article.submission_id, gen);
        loadHtml(article.submission_id, gen);
        loadClassification(article.submission_id, gen);
    }

    // ── PDF rendering ──

    async function loadPdf(submissionId, gen) {
        // Clean up previous PDF document to prevent memory leak
        if (pdfDoc) {
            pdfDoc.destroy();
            pdfDoc = null;
        }

        // Remove old scroll handler
        if (scrollHandler) {
            els['pdf-container'].removeEventListener('scroll', scrollHandler);
            scrollHandler = null;
        }

        els['pdf-container'].innerHTML = '<div class="qa-loading">Loading PDF...</div>';
        els['pdf-page-info'].textContent = 'Loading...';

        try {
            const url = API + '/articles/' + submissionId + '/pdf';
            const loadingTask = pdfjsLib.getDocument({ url, withCredentials: true });
            const doc = await loadingTask.promise;

            // Stale check: if user navigated away, discard
            if (gen !== loadGeneration) {
                doc.destroy();
                return;
            }

            pdfDoc = doc;
            els['pdf-container'].innerHTML = '';
            const totalPages = pdfDoc.numPages;
            els['pdf-page-info'].textContent = totalPages + ' page' + (totalPages !== 1 ? 's' : '');

            for (let i = 1; i <= totalPages; i++) {
                if (gen !== loadGeneration) return;  // Abort if navigated away
                await renderPage(i);
            }

            // Attach scroll handler (stored for cleanup)
            scrollHandler = updatePageIndicator;
            els['pdf-container'].addEventListener('scroll', scrollHandler);
        } catch (err) {
            if (gen !== loadGeneration) return;
            els['pdf-container'].innerHTML = '<div class="qa-loading">PDF not available</div>';
            console.error('PDF load error:', err);
        }
    }

    async function renderPage(pageNum) {
        const page = await pdfDoc.getPage(pageNum);
        const containerWidth = els['pdf-container'].clientWidth - 16;
        const viewport = page.getViewport({ scale: 1 });
        const scale = containerWidth / viewport.width;
        const scaledViewport = page.getViewport({ scale });

        const canvas = document.createElement('canvas');
        canvas.dataset.page = pageNum;
        canvas.width = scaledViewport.width;
        canvas.height = scaledViewport.height;

        els['pdf-container'].appendChild(canvas);

        const ctx = canvas.getContext('2d');
        await page.render({ canvasContext: ctx, viewport: scaledViewport }).promise;
    }

    function updatePageIndicator() {
        const container = els['pdf-container'];
        const canvases = container.querySelectorAll('canvas');
        const scrollTop = container.scrollTop;
        let currentPage = 1;

        canvases.forEach((canvas) => {
            if (canvas.offsetTop <= scrollTop + 50) {
                currentPage = parseInt(canvas.dataset.page, 10);
            }
        });

        const total = pdfDoc ? pdfDoc.numPages : 0;
        els['pdf-page-info'].textContent = 'Page ' + currentPage + ' of ' + total;
    }

    // ── HTML + classification loading ──

    async function loadHtml(submissionId, gen) {
        els['html-content'].innerHTML = '<div class="qa-loading">Loading HTML...</div>';

        try {
            const res = await fetch(API + '/articles/' + submissionId + '/html', { credentials: 'same-origin' });
            if (gen !== loadGeneration) return;

            if (!res.ok) {
                els['html-content'].innerHTML = '<div class="qa-loading">HTML galley not available</div>';
                return;
            }
            const html = await res.text();
            if (gen !== loadGeneration) return;

            // Strip any script tags for safety (server CSP also blocks scripts)
            const sanitized = html.replace(/<script[\s\S]*?<\/script>/gi, '');
            els['html-content'].innerHTML = sanitized;
        } catch (err) {
            if (gen !== loadGeneration) return;
            els['html-content'].innerHTML = '<div class="qa-loading">Error loading HTML</div>';
        }
    }

    async function checkHashValidity(article, gen) {
        try {
            const res = await fetch(API + '/articles/' + article.submission_id, { credentials: 'same-origin' });
            if (gen !== loadGeneration) return;
            const data = await res.json();
            if (gen !== loadGeneration) return;

            if (data.current_hash && data.reviews && data.reviews.length > 0) {
                const latestHash = data.reviews[0].content_hash;
                if (latestHash && latestHash !== data.current_hash) {
                    article.status = 'invalidated';
                    updateStatusBadge('invalidated', article.reviewer, article.reviewed_at, article.comment);
                    toast('Content changed since last review', 'error');
                    recalculateProgress();
                }
            }
        } catch (err) {
            // Non-critical — hash check failure doesn't block review
        }
    }

    async function loadClassification(submissionId, gen) {
        els['endmatter-items'].innerHTML = '';

        try {
            const res = await fetch(API + '/articles/' + submissionId + '/classification', { credentials: 'same-origin' });
            if (gen !== loadGeneration) return;

            const data = await res.json();
            if (gen !== loadGeneration) return;

            renderClassification(data);
        } catch (err) {
            if (gen !== loadGeneration) return;
            els['endmatter-items'].innerHTML = '<div class="qa-endmatter-empty">Classification not available</div>';
        }
    }

    function renderClassification(data) {
        const container = els['endmatter-items'];
        container.innerHTML = '';

        if (data.error) {
            container.innerHTML = '<div class="qa-endmatter-empty">JATS parse error: ' + escapeHtml(data.error) + '</div>';
            return;
        }

        const sections = [
            { key: 'references', label: 'Reference', pillClass: 'qa-pill-reference' },
            { key: 'notes',      label: 'Note',      pillClass: 'qa-pill-note' },
            { key: 'bios',       label: 'Bio',        pillClass: 'qa-pill-bio' },
            { key: 'provenance', label: 'Provenance', pillClass: 'qa-pill-provenance' },
        ];

        let totalItems = 0;

        sections.forEach(sec => {
            const items = data[sec.key] || [];
            totalItems += items.length;

            items.forEach(item => {
                const li = document.createElement('div');
                li.className = 'qa-endmatter-item';
                li.setAttribute('role', 'listitem');

                const pill = document.createElement('span');
                pill.className = 'qa-pill ' + sec.pillClass;
                pill.textContent = sec.label;

                const text = document.createElement('span');
                text.className = 'qa-endmatter-text';
                text.textContent = item.text;  // textContent = safe from XSS

                li.appendChild(pill);
                li.appendChild(text);
                container.appendChild(li);
            });
        });

        if (totalItems === 0) {
            container.innerHTML = '<div class="qa-endmatter-empty">No end-matter items classified</div>';
        }
    }

    // ── Review actions ──

    function showRejectInput() {
        els['reject-comment'].style.display = '';
        els['btn-submit-reject'].style.display = '';
        els['reject-comment'].focus();
    }

    function hideRejectInput() {
        els['reject-comment'].style.display = 'none';
        els['btn-submit-reject'].style.display = 'none';
        els['reject-comment'].value = '';
    }

    async function submitReview(decision, comment) {
        if (currentIndex < 0) return;

        const article = articles[currentIndex];

        try {
            const res = await fetch(API + '/reviews', {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Csrf-Token': window.QA_CONFIG.csrfToken,
                },
                body: JSON.stringify({
                    submissionId: article.submission_id,
                    decision: decision,
                    comment: comment || '',
                }),
            });

            const data = await res.json();

            if (!res.ok) {
                toast(data.error || 'Review failed', 'error');
                return;
            }

            article.status = decision;
            article.reviewer = 'you';
            article.reviewed_at = new Date().toISOString();
            article.comment = comment || null;

            updateStatusBadge(decision, 'you', article.reviewed_at, article.comment);
            hideRejectInput();
            toast(decision === 'approved' ? 'Approved' : 'Rejected', decision === 'approved' ? 'success' : 'error');

            recalculateProgress();
        } catch (err) {
            toast('Network error', 'error');
            console.error('Review submission error:', err);
        }
    }

    // ── Navigation ──

    function navigate(delta) {
        const newIndex = currentIndex + delta;
        if (newIndex >= 0 && newIndex < articles.length) {
            loadArticle(newIndex);
        }
    }

    function goToLastSeen() {
        if (!lastSeenId) return;
        const idx = articles.findIndex(a => a.submission_id === lastSeenId);
        if (idx >= 0 && idx !== currentIndex) {
            loadArticle(idx);
        }
    }

    async function goToRandom() {
        try {
            const res = await fetch(API + '/nav/random-unreviewed', { credentials: 'same-origin' });
            const data = await res.json();
            if (data.submission_id) {
                const idx = articles.findIndex(a => a.submission_id === data.submission_id);
                if (idx >= 0) loadArticle(idx);
            } else {
                toast('All articles reviewed', 'success');
            }
        } catch (err) {
            toast('Error finding random article', 'error');
        }
    }

    async function goToProblem() {
        try {
            const res = await fetch(API + '/nav/problem-case', { credentials: 'same-origin' });
            const data = await res.json();
            if (data.submission_id) {
                const idx = articles.findIndex(a => a.submission_id === data.submission_id);
                if (idx >= 0) {
                    loadArticle(idx);
                    toast('Problem: ' + data.reason, 'error');
                }
            } else {
                toast('No problem cases found', 'success');
            }
        } catch (err) {
            toast('Error finding problem case', 'error');
        }
    }

    // ── UI helpers ──

    function updateStatusBadge(status, reviewer, reviewedAt, comment) {
        const badge = els['qa-status'];
        badge.className = 'qa-badge qa-badge-' + status;
        badge.setAttribute('role', 'status');

        let label = status.charAt(0).toUpperCase() + status.slice(1);
        if (reviewer && reviewedAt) {
            const date = new Date(reviewedAt).toLocaleDateString();
            label += ' by ' + reviewer + ' on ' + date;
        }
        badge.textContent = label;
        badge.title = comment || '';
    }

    function updateProgress(counts) {
        if (!counts) return;
        const parts = [];
        parts.push(counts.total + ' total');
        if (counts.approved) parts.push(counts.approved + ' approved');
        if (counts.rejected) parts.push(counts.rejected + ' rejected');
        if (counts.invalidated) parts.push(counts.invalidated + ' invalidated');
        if (counts.unreviewed) parts.push(counts.unreviewed + ' unreviewed');
        els['qa-progress'].textContent = parts.join(' | ');
    }

    function recalculateProgress() {
        const counts = { total: articles.length, approved: 0, rejected: 0, invalidated: 0, unreviewed: 0 };
        articles.forEach(a => {
            if (a.status === 'approved') counts.approved++;
            else if (a.status === 'rejected') counts.rejected++;
            else if (a.status === 'invalidated') counts.invalidated++;
            else counts.unreviewed++;
        });
        updateProgress(counts);
    }

    function toast(message, type) {
        const div = document.createElement('div');
        div.className = 'qa-toast qa-toast-' + type;
        div.setAttribute('role', 'alert');
        div.textContent = message;
        document.body.appendChild(div);
        setTimeout(() => div.remove(), 2500);
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function showHelp() {
        const existing = document.querySelector('.qa-help-overlay');
        if (existing) { existing.remove(); return; }

        const overlay = document.createElement('div');
        overlay.className = 'qa-help-overlay';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-label', 'Keyboard shortcuts');
        overlay.innerHTML = '<div class="qa-help-box">'
            + '<h3>Keyboard Shortcuts</h3>'
            + '<table>'
            + '<tr><td><kbd>&larr;</kbd> / <kbd>&rarr;</kbd></td><td>Previous / Next article</td></tr>'
            + '<tr><td><kbd>A</kbd></td><td>Approve article</td></tr>'
            + '<tr><td><kbd>R</kbd></td><td>Reject (opens comment box)</td></tr>'
            + '<tr><td><kbd>Esc</kbd></td><td>Close reject box / this help</td></tr>'
            + '<tr><td><kbd>?</kbd></td><td>Toggle this help</td></tr>'
            + '</table>'
            + '<p>Press any key to close</p>'
            + '</div>';

        overlay.addEventListener('click', () => overlay.remove());
        document.addEventListener('keydown', function dismiss() {
            overlay.remove();
            document.removeEventListener('keydown', dismiss);
        }, { once: true });

        document.body.appendChild(overlay);
    }

    // ── Stats dashboard overlay ──

    async function showDashboard() {
        const existing = document.querySelector('.qa-dashboard-overlay');
        if (existing) { existing.remove(); return; }

        const overlay = document.createElement('div');
        overlay.className = 'qa-dashboard-overlay';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-label', 'QA Progress Dashboard');
        overlay.innerHTML = '<div class="qa-dashboard"><div class="qa-loading">Loading stats...</div></div>';

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) overlay.remove();
        });
        document.addEventListener('keydown', function dismiss(e) {
            if (e.key === 'Escape') { overlay.remove(); document.removeEventListener('keydown', dismiss); }
        });

        document.body.appendChild(overlay);

        try {
            const res = await fetch(API + '/stats', { credentials: 'same-origin' });
            const data = await res.json();
            renderDashboard(overlay.querySelector('.qa-dashboard'), data);
        } catch (err) {
            overlay.querySelector('.qa-dashboard').innerHTML = '<p>Failed to load stats</p>';
        }
    }

    function renderDashboard(container, data) {
        const o = data.overall;
        const pctApproved = o.total ? Math.round((o.approved / o.total) * 100) : 0;
        const pctRejected = o.total ? Math.round((o.rejected / o.total) * 100) : 0;
        const pctUnreviewed = o.total ? Math.round((o.unreviewed / o.total) * 100) : 0;

        // Reviewer depth
        const rd = data.by_reviewer_count || [];

        let html = '<div class="qa-dash-header">'
            + '<h2>QA Progress</h2>'
            + '<button class="qa-dash-close" onclick="this.closest(\'.qa-dashboard-overlay\').remove()">&times;</button>'
            + '</div>';

        // Overall donut
        html += '<div class="qa-dash-overview">';
        html += '<div class="qa-dash-donut-wrap">';
        html += renderDonut(pctApproved, pctRejected, pctUnreviewed, o.total);
        html += '</div>';
        html += '<div class="qa-dash-numbers">';
        html += '<div class="qa-dash-stat qa-dash-stat-approved"><span class="qa-dash-num">' + o.approved + '</span><span class="qa-dash-label">Approved</span></div>';
        html += '<div class="qa-dash-stat qa-dash-stat-rejected"><span class="qa-dash-num">' + o.rejected + '</span><span class="qa-dash-label">Rejected</span></div>';
        html += '<div class="qa-dash-stat qa-dash-stat-unreviewed"><span class="qa-dash-num">' + o.unreviewed + '</span><span class="qa-dash-label">Unreviewed</span></div>';
        html += '</div>';
        html += '</div>';

        // Reviewer depth
        html += '<div class="qa-dash-section"><h3>Review Depth</h3><div class="qa-dash-bars">';
        rd.forEach(r => {
            const pct = o.total ? Math.round((r.count / o.total) * 100) : 0;
            html += '<div class="qa-dash-bar-row">'
                + '<span class="qa-dash-bar-label">' + r.label + '</span>'
                + '<div class="qa-dash-bar-track"><div class="qa-dash-bar-fill" style="width:' + pct + '%"></div></div>'
                + '<span class="qa-dash-bar-value">' + r.count + '</span>'
                + '</div>';
        });
        html += '</div></div>';

        // Section breakdown
        html += '<div class="qa-dash-section"><h3>By Section</h3><table class="qa-dash-table">';
        html += '<tr><th>Section</th><th>Total</th><th>Approved</th><th>Rejected</th><th>Unreviewed</th><th>Progress</th></tr>';

        const sections = Object.entries(data.by_section || {}).sort((a, b) => b[1].total - a[1].total);
        sections.forEach(([name, s]) => {
            const pct = s.total ? Math.round((s.approved / s.total) * 100) : 0;
            html += '<tr>'
                + '<td>' + escapeHtml(name) + '</td>'
                + '<td>' + s.total + '</td>'
                + '<td>' + s.approved + '</td>'
                + '<td>' + s.rejected + '</td>'
                + '<td>' + s.unreviewed + '</td>'
                + '<td><div class="qa-dash-mini-bar"><div class="qa-dash-mini-fill" style="width:' + pct + '%"></div><span>' + pct + '%</span></div></td>'
                + '</tr>';
        });
        html += '</table></div>';

        container.innerHTML = html;

        // Animate donut segments in
        requestAnimationFrame(() => {
            container.querySelectorAll('.qa-dash-donut-seg').forEach(seg => {
                seg.style.strokeDashoffset = seg.dataset.target;
            });
            container.querySelectorAll('.qa-dash-bar-fill').forEach(bar => {
                bar.style.transition = 'width 0.6s cubic-bezier(0.16, 1, 0.3, 1)';
            });
        });
    }

    function renderDonut(pctApproved, pctRejected, pctUnreviewed, total) {
        // SVG donut chart
        const r = 70, cx = 90, cy = 90, circ = 2 * Math.PI * r;
        const segApproved = (pctApproved / 100) * circ;
        const segRejected = (pctRejected / 100) * circ;
        const segUnreviewed = (pctUnreviewed / 100) * circ;

        const offApproved = 0;
        const offRejected = segApproved;
        const offUnreviewed = segApproved + segRejected;

        return '<svg class="qa-dash-donut" viewBox="0 0 180 180">'
            + '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" stroke="var(--divider)" stroke-width="18"/>'
            + '<circle class="qa-dash-donut-seg" cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" '
            + 'stroke="var(--accent-green)" stroke-width="18" '
            + 'stroke-dasharray="' + segApproved + ' ' + circ + '" '
            + 'stroke-dashoffset="0" '
            + 'data-target="0" '
            + 'transform="rotate(-90 ' + cx + ' ' + cy + ')"/>'
            + '<circle class="qa-dash-donut-seg" cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" '
            + 'stroke="var(--accent-red)" stroke-width="18" '
            + 'stroke-dasharray="' + segRejected + ' ' + circ + '" '
            + 'stroke-dashoffset="-' + offRejected + '" '
            + 'data-target="-' + offRejected + '" '
            + 'transform="rotate(-90 ' + cx + ' ' + cy + ')"/>'
            + '<text x="' + cx + '" y="' + cy + '" text-anchor="middle" dy="0.35em" '
            + 'font-family="var(--font-ui)" font-size="28" font-weight="700" fill="var(--text-primary)">'
            + pctApproved + '%</text>'
            + '<text x="' + cx + '" y="' + (cy + 18) + '" text-anchor="middle" '
            + 'font-family="var(--font-ui)" font-size="11" fill="var(--text-muted)">'
            + total + ' articles</text>'
            + '</svg>';
    }

    // Boot
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
