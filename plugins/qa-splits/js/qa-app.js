/**
 * QA Splits — Alpine.js application
 *
 * Replaces vanilla JS with reactive state management.
 * PDF.js rendering stays imperative (it's a canvas library).
 */

// Non-reactive state — PDF.js objects can't be proxied (private class fields)
const _pdf = { doc: null, loadGen: 0, scrollHandler: null };

document.addEventListener('alpine:init', () => {
    Alpine.data('qaApp', () => ({
        // Config
        api: window.QA_CONFIG.apiBase,
        pluginUrl: window.QA_CONFIG.pluginUrl,
        csrfToken: window.QA_CONFIG.csrfToken,

        // Article data
        articles: [],
        counts: null,
        loading: true,

        // Current article
        currentIndex: -1,
        htmlContent: '',
        htmlLoading: true,
        pdfLoading: true,
        classification: null,

        // Working set (filtered subset)
        workingSet: [],
        setIndex: -1,
        setFilter: null,

        // Filters
        searchQuery: '',
        issueFilter: '',
        activeStatuses: new Set(),
        activeSections: new Set(),

        // Review UI
        showRejectForm: false,
        rejectComment: '',
        submitting: false,

        // Sidebar
        showChecklist: false,

        // Prefetch
        prefetchCache: new Map(),


        // ── Lifecycle ──

        async init() {
            if (window.pdfjsLib) {
                pdfjsLib.GlobalWorkerOptions.workerSrc = this.pluginUrl + '/js/pdf.worker.min.js';
            }
            this.bindKeys();
            this.setupPdfResize();
            await this.loadArticles();
        },

        // ── Data loading ──

        async loadArticles() {
            try {
                const res = await fetch(this.api + '/articles', { credentials: 'same-origin' });
                const data = await res.json();
                this.articles = data.articles || [];
                this.counts = data.counts;
                this.loading = false;

                if (this.articles.length === 0) return;

                this.workingSet = this.articles.map((_, i) => i);

                // Parse URL params
                const params = new URL(window.location).searchParams;
                const urlSet = params.get('set');
                const urlQ = params.get('q');
                const urlPos = parseInt(params.get('pos'), 10);
                const urlId = parseInt(params.get('id'), 10);

                if (urlSet && urlQ) {
                    this.applyFilterFromUrl(urlSet, urlQ);
                    this.goToSetIndex(urlPos > 0 ? urlPos - 1 : 0);
                } else {
                    let start = 0;
                    if (urlId) {
                        const idx = this.articles.findIndex(a => a.submission_id === urlId);
                        if (idx >= 0) start = idx;
                    } else {
                        const lastSeen = parseInt(localStorage.getItem('qa-last-seen'), 10);
                        if (lastSeen) {
                            const idx = this.articles.findIndex(a => a.submission_id === lastSeen);
                            if (idx >= 0) start = idx;
                        }
                    }
                    this.setIndex = start;
                    this.loadArticle(start);
                }

                this.prefetchRandomTarget();
            } catch (err) {
                this.loading = false;
                console.error('Failed to load articles:', err);
            }
        },

        // ── Article display ──

        get article() {
            return this.currentIndex >= 0 ? this.articles[this.currentIndex] : null;
        },

        get titleDisplay() {
            const a = this.article;
            if (!a) return this.loading ? 'Loading...' : 'No articles found';
            const section = a.section ? ' [' + a.section.toLowerCase() + ']' : '';
            return a.volume + '.' + a.number + ' #' + a.seq + ' (' + a.year + ') ' + a.title + section;
        },

        get authorsDisplay() {
            const a = this.article;
            return a && a.authors.length ? 'by ' + a.authors.join(', ') : '';
        },

        get statusLabel() {
            const labels = { approved: 'Approved', needs_fix: 'Fix Requested', invalidated: 'Invalidated', unreviewed: 'Unreviewed' };
            const a = this.article;
            if (!a) return '';
            let label = labels[a.status] || a.status;
            if (a.reviewed_at) {
                const d = new Date(a.reviewed_at);
                const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
                label += ' ' + String(d.getDate()).padStart(2,'0') + months[d.getMonth()] + String(d.getFullYear()).slice(2);
            }
            if (a.comment && a.status === 'needs_fix') label += ' ⓘ';
            return label;
        },

        get statusClass() {
            return this.article ? 'qa-badge qa-badge-' + this.article.status : 'qa-badge';
        },

        get positionDisplay() {
            if (this.workingSet.length === 0) return '';
            return (this.setIndex + 1) + ' / ' + this.workingSet.length;
        },

        get progressDisplay() {
            if (!this.counts) return '';
            const c = this.counts;
            const parts = [];
            if (c.approved) parts.push(c.approved + ' approved');
            if (c.needs_fix) parts.push(c.needs_fix + ' needs fix');
            parts.push(c.total + ' total');
            const remaining = (c.unreviewed || 0) + (c.invalidated || 0);
            if (remaining > 0) parts.push(remaining + ' remaining');
            return parts.join(' / ');
        },

        showFixReason() {
            const a = this.article;
            if (a && a.comment && a.status === 'needs_fix') {
                alert(a.comment); // Simple for now — could be a toast
            }
        },

        // ── Navigation ──

        loadArticle(index) {
            if (index < 0 || index >= this.articles.length) return;
            _pdf.loadGen++;
            this.currentIndex = index;
            const a = this.articles[index];

            localStorage.setItem('qa-last-seen', a.submission_id);
            const si = this.workingSet.indexOf(index);
            if (si >= 0) this.setIndex = si;
            this.updateUrl();
            this.showRejectForm = false;

            this.loadPdf(a.submission_id);
            this.loadHtml(a.submission_id);
            this.loadClassification(a.submission_id);
            this.prefetchNearby(this.setIndex);
        },

        goToSetIndex(si) {
            if (si >= 0 && si < this.workingSet.length) {
                this.setIndex = si;
                this.loadArticle(this.workingSet[si]);
            }
        },

        navigate(delta) {
            this.goToSetIndex(this.setIndex + delta);
        },

        async goToRandom() {
            try {
                const res = await fetch(this.api + '/nav/random-unreviewed', { credentials: 'same-origin' });
                const data = await res.json();
                if (data.submission_id) {
                    const idx = this.articles.findIndex(a => a.submission_id === data.submission_id);
                    if (idx >= 0) {
                        const si = this.workingSet.indexOf(idx);
                        if (si >= 0) this.goToSetIndex(si);
                        else this.loadArticle(idx);
                    }
                }
            } catch (err) {
                console.error('Random navigation error:', err);
            }
        },

        get canGoPrev() { return this.setIndex > 0; },
        get canGoNext() { return this.setIndex < this.workingSet.length - 1; },

        // ── PDF ──

        async loadPdf(submissionId) {
            const gen = _pdf.loadGen;
            if (_pdf.doc) { _pdf.doc.destroy(); _pdf.doc = null; }

            const container = document.getElementById('pdf-container');
            if (_pdf.scrollHandler) {
                container.removeEventListener('scroll', _pdf.scrollHandler);
                _pdf.scrollHandler = null;
            }
            container.innerHTML = '';
            this.pdfLoading = true;
            document.getElementById('pdf-page-info').textContent = 'Loading...';

            try {
                const cached = this.prefetchCache.get(submissionId);
                let doc;
                if (cached && cached.pdf) {
                    doc = await pdfjsLib.getDocument({ data: await cached.pdf.arrayBuffer() }).promise;
                } else {
                    doc = await pdfjsLib.getDocument({ url: this.api + '/articles/' + submissionId + '/pdf', withCredentials: true }).promise;
                }
                if (gen !== _pdf.loadGen) { doc.destroy(); return; }

                _pdf.doc = doc;
                this.pdfLoading = false;
                const total = doc.numPages;
                document.getElementById('pdf-page-info').textContent = total + ' page' + (total !== 1 ? 's' : '');

                for (let i = 1; i <= total; i++) {
                    if (gen !== _pdf.loadGen) return;
                    await this.renderPdfPage(i);
                }

                _pdf.scrollHandler = () => this.updatePageIndicator();
                container.addEventListener('scroll', _pdf.scrollHandler);
            } catch (err) {
                if (gen !== _pdf.loadGen) return;
                this.pdfLoading = false;
                document.getElementById('pdf-page-info').textContent = 'PDF not available';
            }
        },

        async renderPdfPage(pageNum) {
            const page = await _pdf.doc.getPage(pageNum);
            const container = document.getElementById('pdf-container');
            const containerWidth = container.clientWidth - 16;
            const viewport = page.getViewport({ scale: 1 });
            const scale = containerWidth / viewport.width;
            const sv = page.getViewport({ scale });

            const wrapper = document.createElement('div');
            wrapper.className = 'qa-pdf-page';
            wrapper.dataset.page = pageNum;
            wrapper.style.setProperty('--pdf-render-width', sv.width + 'px');
            wrapper.style.setProperty('--pdf-render-height', sv.height + 'px');
            wrapper.style.aspectRatio = sv.width + '/' + sv.height;

            const canvas = document.createElement('canvas');
            canvas.width = sv.width;
            canvas.height = sv.height;
            wrapper.appendChild(canvas);

            const textLayerDiv = document.createElement('div');
            textLayerDiv.className = 'qa-pdf-text-layer';
            wrapper.appendChild(textLayerDiv);

            container.appendChild(wrapper);
            await page.render({ canvasContext: canvas.getContext('2d'), viewport: sv }).promise;

            const textContent = await page.getTextContent();
            pdfjsLib.renderTextLayer({ textContentSource: textContent, container: textLayerDiv, viewport: sv });
        },

        updatePageIndicator() {
            const container = document.getElementById('pdf-container');
            const pages = container.querySelectorAll('.qa-pdf-page');
            let currentPage = 1;
            pages.forEach(p => {
                if (p.offsetTop <= container.scrollTop + 50) currentPage = parseInt(p.dataset.page, 10);
            });
            document.getElementById('pdf-page-info').textContent = 'Page ' + currentPage + ' of ' + (_pdf.doc ? _pdf.doc.numPages : 0);
        },

        setupPdfResize() {
            if (typeof ResizeObserver === 'undefined') return;
            const el = document.getElementById('pdf-container');
            if (!el) return;
            new ResizeObserver(() => {
                el.querySelectorAll('.qa-pdf-page').forEach(wrapper => {
                    const renderW = parseFloat(wrapper.style.getPropertyValue('--pdf-render-width'));
                    if (renderW) wrapper.style.setProperty('--pdf-css-scale', wrapper.clientWidth / renderW);
                });
            }).observe(el);
        },

        // ── HTML + Classification ──

        async loadHtml(submissionId) {
            const gen = _pdf.loadGen;
            this.htmlLoading = true;
            this.htmlContent = '';
            try {
                const cached = this.prefetchCache.get(submissionId);
                let html;
                if (cached && cached.html) {
                    html = cached.html;
                } else {
                    const res = await fetch(this.api + '/articles/' + submissionId + '/html', { credentials: 'same-origin' });
                    if (gen !== _pdf.loadGen) return;
                    if (!res.ok) { this.htmlLoading = false; return; }
                    html = await res.text();
                }
                if (gen !== _pdf.loadGen) return;
                this.htmlContent = html.replace(/<script[\s\S]*?<\/script>/gi, '');
                this.htmlLoading = false;
            } catch (err) {
                if (gen !== _pdf.loadGen) return;
                this.htmlLoading = false;
            }
        },

        async loadClassification(submissionId) {
            const gen = _pdf.loadGen;
            this.classification = null;
            try {
                const cached = this.prefetchCache.get(submissionId);
                let data;
                if (cached && cached.classification) {
                    data = cached.classification;
                } else {
                    const res = await fetch(this.api + '/articles/' + submissionId + '/classification', { credentials: 'same-origin' });
                    if (gen !== _pdf.loadGen) return;
                    data = await res.json();
                }
                if (gen !== _pdf.loadGen) return;
                this.classification = data;
            } catch (err) {
                if (gen !== _pdf.loadGen) return;
            }
        },

        get hasClassification() {
            if (!this.classification) return false;
            return (this.classification.references || []).length +
                   (this.classification.notes || []).length +
                   (this.classification.bios || []).length +
                   (this.classification.provenance || []).length > 0;
        },

        get classificationGroups() {
            if (!this.classification) return [];
            const defs = [
                { key: 'bios', label: 'Author Bios', cls: 'qa-pill-bio',
                  hint: 'Classified as author biographical notes. Also rendered in the HTML galley above.' },
                { key: 'references', label: 'References', cls: 'qa-pill-reference',
                  hint: 'Extracted from HTML body and imported into OJS citations table. OJS renders these separately on the article page.' },
                { key: 'notes', label: 'Notes', cls: 'qa-pill-note',
                  hint: 'Classified as footnotes/endnotes. Rendered in the HTML galley below the body text.' },
                { key: 'provenance', label: 'Provenance', cls: 'qa-pill-provenance',
                  hint: 'Publication history, acknowledgements, or source notes. Rendered in the HTML galley.' },
            ];
            return defs
                .map(d => {
                    const items = (this.classification[d.key] || []).map(item => item.text);
                    return { ...d, items, count: items.length };
                })
                .filter(g => g.count > 0);
        },

        // ── Reviews ──

        async submitReview(decision) {
            if (this.currentIndex < 0 || this.submitting) return;
            this.submitting = true;
            const a = this.articles[this.currentIndex];
            const comment = decision === 'needs_fix' ? this.rejectComment.trim() : '';

            if (decision === 'needs_fix' && !comment) {
                this.submitting = false;
                return;
            }

            try {
                const res = await fetch(this.api + '/reviews', {
                    method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'X-Csrf-Token': this.csrfToken },
                    body: JSON.stringify({ submissionId: a.submission_id, decision, comment }),
                });
                const data = await res.json();
                if (!res.ok) { this.submitting = false; return; }

                a.status = decision;
                a.reviewer = 'you';
                a.reviewed_at = new Date().toISOString();
                a.comment = comment || null;
                this.showRejectForm = false;
                this.rejectComment = '';
                this.recalculateCounts();

                // Auto-advance
                if (this.setIndex < this.workingSet.length - 1) {
                    this.goToSetIndex(this.setIndex + 1);
                }
            } catch (err) {
                console.error('Review submission error:', err);
            } finally {
                this.submitting = false;
            }
        },

        approve() { this.submitReview('approved'); },

        requestFix() {
            if (!this.showRejectForm) {
                this.showRejectForm = true;
                // Prepopulate with existing comment
                if (this.article && this.article.comment) {
                    this.rejectComment = this.article.comment;
                }
                this.$nextTick(() => this.$refs.rejectTextarea?.focus());
            }
        },

        submitFix() {
            if (this.rejectComment.trim()) this.submitReview('needs_fix');
        },

        cancelFix() {
            this.showRejectForm = false;
            this.rejectComment = '';
        },

        recalculateCounts() {
            const c = { total: this.articles.length, approved: 0, needs_fix: 0, unreviewed: 0, invalidated: 0 };
            this.articles.forEach(a => {
                if (c[a.status] !== undefined) c[a.status]++;
                else c.unreviewed++;
            });
            this.counts = c;
        },

        // ── Filtering ──

        // Articles matching all filters EXCEPT issue (so issue dropdown shows counts within current status/section/search context)
        get _baseFilteredForIssues() {
            const q = this.searchQuery.toLowerCase();
            return this.articles.filter(a => {
                if (this.activeStatuses.size > 0 && !this.activeStatuses.has(a.status)) return false;
                if (this.activeSections.size > 0 && !this.activeSections.has(a.section)) return false;
                if (q && !a.title.toLowerCase().includes(q)
                    && !a.authors.some(auth => auth.toLowerCase().includes(q))
                    && !(a.section || '').toLowerCase().includes(q)) return false;
                return true;
            });
        },

        get allIssues() {
            const base = this._baseFilteredForIssues;
            const issues = {};
            base.forEach(a => {
                const key = a.volume + '.' + a.number;
                issues[key] = (issues[key] || 0) + 1;
            });
            return Object.entries(issues)
                .sort((a, b) => {
                    const [av, an] = a[0].split('.').map(Number);
                    const [bv, bn] = b[0].split('.').map(Number);
                    return bv - av || bn - an;
                })
                .map(([k, c]) => ({ key: k, count: c }));
        },

        // Articles matching all filters EXCEPT status (so status pills show counts within the current issue/search/section context)
        get _baseFiltered() {
            const q = this.searchQuery.toLowerCase();
            const issue = this.issueFilter;
            return this.articles.filter((a, i) => {
                if (issue && (a.volume + '.' + a.number) !== issue) return false;
                if (this.activeSections.size > 0 && !this.activeSections.has(a.section)) return false;
                if (q && !a.title.toLowerCase().includes(q)
                    && !a.authors.some(auth => auth.toLowerCase().includes(q))
                    && !(a.section || '').toLowerCase().includes(q)) return false;
                return true;
            });
        },

        // Articles matching all filters EXCEPT section (so section pills show counts within the current issue/search/status context)
        get _baseFilteredForSections() {
            const q = this.searchQuery.toLowerCase();
            const issue = this.issueFilter;
            return this.articles.filter((a, i) => {
                if (issue && (a.volume + '.' + a.number) !== issue) return false;
                if (this.activeStatuses.size > 0 && !this.activeStatuses.has(a.status)) return false;
                if (q && !a.title.toLowerCase().includes(q)
                    && !a.authors.some(auth => auth.toLowerCase().includes(q))
                    && !(a.section || '').toLowerCase().includes(q)) return false;
                return true;
            });
        },

        get statusPills() {
            const base = this._baseFiltered;
            const counts = { approved: 0, needs_fix: 0, unreviewed: 0 };
            base.forEach(a => { if (counts[a.status] !== undefined) counts[a.status]++; });
            return [
                { key: 'approved', label: 'Approved', count: counts.approved },
                { key: 'needs_fix', label: 'Needs Fix', count: counts.needs_fix },
                { key: 'unreviewed', label: 'Unreviewed', count: counts.unreviewed },
            ].filter(p => p.count > 0);
        },

        get sectionPills() {
            const base = this._baseFilteredForSections;
            const counts = {};
            base.forEach(a => { if (a.section) counts[a.section] = (counts[a.section] || 0) + 1; });
            return Object.entries(counts)
                .sort((a, b) => b[1] - a[1])
                .map(([k, c]) => ({ key: k, label: k, count: c }));
        },

        toggleStatus(key) {
            if (this.activeStatuses.has(key)) this.activeStatuses.delete(key);
            else this.activeStatuses.add(key);
            this.refilter();
        },

        toggleSection(key) {
            if (this.activeSections.has(key)) this.activeSections.delete(key);
            else this.activeSections.add(key);
            this.refilter();
        },

        isStatusActive(key) { return this.activeStatuses.has(key); },
        isSectionActive(key) { return this.activeSections.has(key); },

        refilter() {
            const q = this.searchQuery.toLowerCase();
            const issue = this.issueFilter;

            this.workingSet = [];
            this.articles.forEach((a, i) => {
                if (issue && (a.volume + '.' + a.number) !== issue) return;
                if (this.activeStatuses.size > 0 && !this.activeStatuses.has(a.status)) return;
                if (this.activeSections.size > 0 && !this.activeSections.has(a.section)) return;
                if (q && !a.title.toLowerCase().includes(q)
                    && !a.authors.some(auth => auth.toLowerCase().includes(q))
                    && !(a.section || '').toLowerCase().includes(q)) return;
                this.workingSet.push(i);
            });

            if (issue) this.setFilter = { type: 'issue', query: issue };
            else if (this.activeStatuses.size === 1) this.setFilter = { type: 'status', query: [...this.activeStatuses][0] };
            else if (q) this.setFilter = { type: 'search', query: this.searchQuery };
            else this.setFilter = null;

            this.setIndex = this.workingSet.indexOf(this.currentIndex);
            if (this.setIndex < 0 && this.workingSet.length > 0) {
                this.goToSetIndex(0);
            }
            this.updateUrl();
        },

        clearFilters() {
            this.searchQuery = '';
            this.issueFilter = '';
            this.activeStatuses.clear();
            this.activeSections.clear();
            this.refilter();
        },

        get hasFilters() {
            return this.searchQuery || this.issueFilter || this.activeStatuses.size > 0 || this.activeSections.size > 0;
        },

        applyFilterFromUrl(type, query) {
            if (type === 'issue') this.issueFilter = query;
            else if (type === 'status') this.activeStatuses.add(query);
            else if (type === 'search') this.searchQuery = query;
            this.refilter();
        },

        // ── Working set article list ──

        get workingSetArticles() {
            return this.workingSet.map((artIdx, si) => {
                const a = this.articles[artIdx];
                return {
                    si,
                    artIdx,
                    num: si + 1,
                    icon: a.status === 'approved' ? '✓' : a.status === 'needs_fix' ? '⚠' : '·',
                    statusCls: 'qa-drawer-item-status qa-drawer-item-status-' + a.status,
                    title: a.volume + '.' + a.number + ' (' + a.year + ') ' + a.title,
                    active: si === this.setIndex,
                };
            });
        },

        selectArticle(si) {
            this.goToSetIndex(si);
        },

        // ── URL management ──

        updateUrl() {
            const url = new URL(window.location);
            if (this.setFilter) {
                url.searchParams.set('set', this.setFilter.type);
                url.searchParams.set('q', this.setFilter.query);
                url.searchParams.set('pos', String(this.setIndex + 1));
                url.searchParams.delete('id');
            } else {
                url.searchParams.delete('set');
                url.searchParams.delete('q');
                url.searchParams.delete('pos');
                if (this.currentIndex >= 0) url.searchParams.set('id', this.articles[this.currentIndex].submission_id);
            }
            history.replaceState(null, '', url);
        },

        // ── Keyboard shortcuts ──

        bindKeys() {
            document.addEventListener('keydown', (e) => {
                if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
                    if (e.key === 'Escape') this.cancelFix();
                    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); this.submitFix(); }
                    return;
                }
                switch (e.key) {
                    case 'ArrowLeft': e.preventDefault(); this.navigate(-1); break;
                    case 'ArrowRight': e.preventDefault(); this.navigate(1); break;
                    case 'a': case 'A': e.preventDefault(); this.approve(); break;
                    case 'r': case 'R': e.preventDefault(); this.requestFix(); break;
                    case '?': e.preventDefault(); this.showHelp = !this.showHelp; break;
                }
            });
        },

        showHelp: false,

        // ── Prefetch ──

        prefetchNearby(index) {
            for (let offset = 1; offset <= 5; offset++) {
                for (const si of [index + offset, index - offset]) {
                    if (si >= 0 && si < this.workingSet.length) {
                        const id = this.articles[this.workingSet[si]].submission_id;
                        if (!this.prefetchCache.has(id)) {
                            this.prefetchCache.set(id, { pdf: null, html: null, classification: null });
                            fetch(this.api + '/articles/' + id + '/pdf', { credentials: 'same-origin' })
                                .then(r => r.ok ? r.blob() : null)
                                .then(b => { if (this.prefetchCache.has(id)) this.prefetchCache.get(id).pdf = b; }).catch(() => {});
                            fetch(this.api + '/articles/' + id + '/html', { credentials: 'same-origin' })
                                .then(r => r.ok ? r.text() : null)
                                .then(h => { if (this.prefetchCache.has(id)) this.prefetchCache.get(id).html = h; }).catch(() => {});
                            fetch(this.api + '/articles/' + id + '/classification', { credentials: 'same-origin' })
                                .then(r => r.ok ? r.json() : null)
                                .then(d => { if (this.prefetchCache.has(id)) this.prefetchCache.get(id).classification = d; }).catch(() => {});
                        }
                    }
                }
            }
            // Evict far entries
            for (const [id] of this.prefetchCache) {
                const ai = this.articles.findIndex(a => a.submission_id === id);
                if (ai >= 0 && Math.abs(this.workingSet.indexOf(ai) - index) > 7) this.prefetchCache.delete(id);
            }
        },

        async prefetchRandomTarget() {
            // Pre-warm one random target
            try {
                const res = await fetch(this.api + '/nav/random-unreviewed', { credentials: 'same-origin' });
                const data = await res.json();
                if (data.submission_id) {
                    const idx = this.articles.findIndex(a => a.submission_id === data.submission_id);
                    if (idx >= 0) this.prefetchNearby(this.workingSet.indexOf(idx));
                }
            } catch (err) {}
        },

        // ── Stats dashboard ──

        showDashboard: false,
        dashboardData: null,

        async openDashboard() {
            this.showDashboard = true;
            try {
                const res = await fetch(this.api + '/stats', { credentials: 'same-origin' });
                this.dashboardData = await res.json();
            } catch (err) {
                this.dashboardData = null;
            }
        },
    }));
});
