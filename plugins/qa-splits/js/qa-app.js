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
        currentUsername: window.QA_CONFIG.username,

        // Article data
        articles: [],
        counts: null,
        loading: true,

        // Current article
        currentIndex: -1,
        htmlContent: '',
        htmlLoading: true,
        pdfLoading: true,
        pdfPageInfo: 'Loading...',
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
        activeReviewers: new Set(),

        // Review UI
        showRejectForm: false,
        rejectComment: '',
        submitting: false,

        // Sidebar
        showChecklist: false,

        // Prefetch
        prefetchCache: new Map(),
        _prefetchController: null,


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

                // Parse URL params — restore all filters
                const params = new URL(window.location).searchParams;
                const urlId = parseInt(params.get('id'), 10);
                let hasFilters = false;

                if (params.get('issue')) { this.issueFilter = params.get('issue'); hasFilters = true; }
                if (params.get('status')) { params.get('status').split(',').forEach(s => this.activeStatuses.add(s)); hasFilters = true; }
                if (params.get('section')) { params.get('section').split(',').forEach(s => this.activeSections.add(s)); hasFilters = true; }
                if (params.get('reviewer')) { params.get('reviewer').split(',').forEach(s => this.activeReviewers.add(s)); hasFilters = true; }
                if (params.get('q')) { this.searchQuery = params.get('q'); hasFilters = true; }
                if (hasFilters) this.refilter();

                if (hasFilters) {
                    // Navigate to the URL article within the filtered set, or first match
                    let si = 0;
                    if (urlId) {
                        const artIdx = this.articles.findIndex(a => a.submission_id === urlId);
                        const found = this.workingSet.indexOf(artIdx);
                        if (found >= 0) si = found;
                    }
                    this.setIndex = si;
                    await this.loadArticle(this.workingSet[si]);
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
                    await this.loadArticle(start);
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
            return a.volume + '.' + a.number + ' #' + a.seq + ' (' + a.year + ') ' + a.title + section + ' [id: ' + a.submission_id + ']';
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

        async loadArticle(index) {
            if (index < 0 || index >= this.articles.length) return;
            _pdf.loadGen++;
            this.currentIndex = index;
            const a = this.articles[index];

            // Abort any in-flight prefetch requests so they don't block
            // PHP-FPM workers needed for the current article.
            if (this._prefetchController) {
                this._prefetchController.abort();
                this._prefetchController = null;
            }

            localStorage.setItem('qa-last-seen', a.submission_id);
            const si = this.workingSet.indexOf(index);
            if (si >= 0) this.setIndex = si;
            this.updateUrl();
            this.showRejectForm = false;

            // Load current article, then prefetch nearby once done.
            await Promise.all([
                this.loadPdf(a.submission_id),
                this.loadHtml(a.submission_id),
                this.loadClassification(a.submission_id),
            ]);

            // Scroll right pane and PDF container to top
            document.querySelector('.qa-right')?.scrollTo(0, 0);
            document.querySelector('.qa-pdf-container')?.scrollTo(0, 0);
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
                const ids = data.submission_ids || [];
                if (ids.length === 0) return;

                const indices = ids
                    .map(id => this.articles.findIndex(a => a.submission_id === id))
                    .filter(i => i >= 0);
                if (indices.length === 0) return;

                this.searchQuery = '';
                this.issueFilter = '';
                this.activeStatuses.clear();
                this.activeSections.clear();
                this.activeReviewers.clear();
                this.workingSet = indices;
                this.setFilter = { type: 'random', query: 'random' };
                this.goToSetIndex(0);
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
            this.pdfPageInfo = 'Loading...';

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
                this.pdfPageInfo = total + ' page' + (total !== 1 ? 's' : '');

                for (let i = 1; i <= total; i++) {
                    if (gen !== _pdf.loadGen) return;
                    await this.renderPdfPage(i);
                }

                _pdf.scrollHandler = () => this.updatePageIndicator();
                container.addEventListener('scroll', _pdf.scrollHandler);
            } catch (err) {
                if (gen !== _pdf.loadGen) return;
                this.pdfLoading = false;
                this.pdfPageInfo = 'PDF not available';
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
            this.pdfPageInfo = 'Page ' + currentPage + ' of ' + (_pdf.doc ? _pdf.doc.numPages : 0);
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
            const c = this.classification;
            return (c.references || []).length > 0
                || (c.notes_count || 0) > 0
                || (c.bios_count || 0) > 0
                || (c.provenance_count || 0) > 0;
        },

        get classificationGroups() {
            if (!this.classification) return [];
            const c = this.classification;
            const groups = [];

            // Bios — count only (content visible in HTML pane above)
            if (c.bios_count > 0) {
                groups.push({ label: 'Author Bios', cls: 'qa-pill-bio', count: c.bios_count, items: [] });
            }

            // References — full list (not in HTML galley, from citations table)
            const refs = c.references || [];
            if (refs.length > 0) {
                groups.push({ label: 'References', cls: 'qa-pill-reference', count: refs.length, items: refs.map(r => r.text) });
            }

            // Notes — count only
            if (c.notes_count > 0) {
                groups.push({ label: 'Notes', cls: 'qa-pill-note', count: c.notes_count, items: [] });
            }

            // Provenance — count only
            if (c.provenance_count > 0) {
                groups.push({ label: 'Provenance', cls: 'qa-pill-provenance', count: c.provenance_count, items: [] });
            }

            return groups;
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
                    && !(a.section || '').toLowerCase().includes(q)
                    && !String(a.submission_id).includes(q)) return false;
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
                    && !(a.section || '').toLowerCase().includes(q)
                    && !String(a.submission_id).includes(q)) return false;
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
                    && !(a.section || '').toLowerCase().includes(q)
                    && !String(a.submission_id).includes(q)) return false;
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

        get _baseFilteredForReviewers() {
            const q = this.searchQuery.toLowerCase();
            const issue = this.issueFilter;
            return this.articles.filter(a => {
                if (issue && (a.volume + '.' + a.number) !== issue) return false;
                if (this.activeStatuses.size > 0 && !this.activeStatuses.has(a.status)) return false;
                if (this.activeSections.size > 0 && !this.activeSections.has(a.section)) return false;
                if (q && !a.title.toLowerCase().includes(q)
                    && !a.authors.some(auth => auth.toLowerCase().includes(q))
                    && !(a.section || '').toLowerCase().includes(q)
                    && !String(a.submission_id).includes(q)) return false;
                return true;
            });
        },

        get reviewerPills() {
            const base = this._baseFilteredForReviewers;
            let byMe = 0, byOthers = 0;
            base.forEach(a => {
                if (!a.reviewer) return;
                if (a.reviewer === 'you' || a.reviewer === this.currentUsername) byMe++;
                else byOthers++;
            });
            return [
                { key: 'me', label: 'By me', count: byMe },
                { key: 'others', label: 'By others', count: byOthers },
            ].filter(p => p.count > 0);
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

        toggleReviewer(key) {
            if (this.activeReviewers.has(key)) this.activeReviewers.delete(key);
            else this.activeReviewers.add(key);
            this.refilter();
        },
        isReviewerActive(key) { return this.activeReviewers.has(key); },

        refilter() {
            const q = this.searchQuery.toLowerCase();
            const issue = this.issueFilter;

            this.workingSet = [];
            this.articles.forEach((a, i) => {
                if (issue && (a.volume + '.' + a.number) !== issue) return;
                if (this.activeStatuses.size > 0 && !this.activeStatuses.has(a.status)) return;
                if (this.activeSections.size > 0 && !this.activeSections.has(a.section)) return;
                if (this.activeReviewers.size > 0) {
                    const isMine = a.reviewer === 'you' || a.reviewer === this.currentUsername;
                    const byMe = this.activeReviewers.has('me');
                    const byOthers = this.activeReviewers.has('others');
                    if (byMe && !byOthers && !isMine) return;
                    if (byOthers && !byMe && (isMine || !a.reviewer)) return;
                    if (byMe && byOthers && !a.reviewer) return;
                }
                if (q) {
                    const isNumeric = /^\d+$/.test(q);
                    if (isNumeric) {
                        if (String(a.submission_id) !== q) return;
                    } else {
                        if (!a.title.toLowerCase().includes(q)
                            && !a.authors.some(auth => auth.toLowerCase().includes(q))
                            && !(a.section || '').toLowerCase().includes(q)
                            && String(a.submission_id) !== q) return;
                    }
                }
                this.workingSet.push(i);
            });

            this.setFilter = this.hasFilters ? { type: 'filters' } : null;
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
            this.activeReviewers.clear();
            this.refilter();
        },

        get hasFilters() {
            return this.searchQuery || this.issueFilter || this.activeStatuses.size > 0 || this.activeSections.size > 0 || this.activeReviewers.size > 0 || (this.setFilter && this.setFilter.type === 'random');
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
                    title: a.volume + '.' + a.number + ' (' + a.year + ') ' + a.title + ' [id: ' + a.submission_id + ']',
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
            // Clear legacy params
            url.searchParams.delete('set');
            url.searchParams.delete('pos');

            // Serialize all active filters
            const setOrDelete = (key, val) => val ? url.searchParams.set(key, val) : url.searchParams.delete(key);

            setOrDelete('issue', this.issueFilter);
            setOrDelete('status', this.activeStatuses.size > 0 ? [...this.activeStatuses].sort().join(',') : '');
            setOrDelete('section', this.activeSections.size > 0 ? [...this.activeSections].sort().join(',') : '');
            setOrDelete('reviewer', this.activeReviewers.size > 0 ? [...this.activeReviewers].sort().join(',') : '');
            setOrDelete('q', this.searchQuery);

            // Always store current article ID for direct linking
            if (this.currentIndex >= 0) url.searchParams.set('id', this.articles[this.currentIndex].submission_id);
            else url.searchParams.delete('id');

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

        async prefetchNearby(index) {
            this._prefetchController = new AbortController();
            const signal = this._prefetchController.signal;

            // Collect IDs to prefetch (next 1 article only — keep concurrency low)
            const ids = [];
            for (const si of [index + 1, index - 1]) {
                if (si >= 0 && si < this.workingSet.length) {
                    const id = this.articles[this.workingSet[si]].submission_id;
                    if (!this.prefetchCache.has(id)) ids.push(id);
                }
            }

            // Prefetch sequentially — one article at a time to avoid saturating
            // Apache workers (~10 under Rosetta). Each article = 3 requests.
            for (const id of ids) {
                if (signal.aborted) return;
                this.prefetchCache.set(id, { pdf: null, html: null, classification: null });
                try {
                    const [pdfRes, htmlRes, classRes] = await Promise.all([
                        fetch(this.api + '/articles/' + id + '/pdf', { credentials: 'same-origin', signal }),
                        fetch(this.api + '/articles/' + id + '/html', { credentials: 'same-origin', signal }),
                        fetch(this.api + '/articles/' + id + '/classification', { credentials: 'same-origin', signal }),
                    ]);
                    const entry = this.prefetchCache.get(id);
                    if (!entry) continue;
                    entry.pdf = pdfRes.ok ? await pdfRes.blob() : null;
                    entry.html = htmlRes.ok ? await htmlRes.text() : null;
                    entry.classification = classRes.ok ? await classRes.json() : null;
                } catch (err) {
                    // Aborted or network error — remove incomplete cache entry
                    this.prefetchCache.delete(id);
                }
            }

            // Evict far entries
            for (const [id] of this.prefetchCache) {
                const ai = this.articles.findIndex(a => a.submission_id === id);
                if (ai >= 0 && Math.abs(this.workingSet.indexOf(ai) - index) > 4) this.prefetchCache.delete(id);
            }
        },

        async prefetchRandomTarget() {
            // Pre-warm one random target — just fetch the nav endpoint,
            // don't prefetch article data (would saturate workers)
            try {
                await fetch(this.api + '/nav/random-unreviewed', { credentials: 'same-origin' });
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
