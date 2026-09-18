(() => {
    "use strict";

    const page = document.querySelector("[data-page]");
    if (!page) return;

    const params = new URLSearchParams(window.location.search);
    const id = params.get("id");
    const status = page.querySelector(".status");
    const api = async (path, options = {}) => {
        const response = await fetch(`/api/${path}`, {
            headers: {"Content-Type": "application/json", ...(options.headers || {})},
            ...options,
        });
        if (!response.ok) throw new Error((await response.json().catch(() => ({}))).message || `Request failed (${response.status})`);
        return response.json();
    };
    const showStatus = (message = "", isError = false) => {
        status.textContent = message;
        status.classList.toggle("error", isError);
    };
    const TOAST_KEY = "diffido-toast";
    const toast = document.querySelector(".toast");
    const showToast = (message = "", isError = false) => {
        if (!toast) return;
        toast.textContent = message;
        toast.classList.toggle("error", isError);
        toast.classList.add("visible");
        clearTimeout(showToast.timer);
        showToast.timer = setTimeout(() => toast.classList.remove("visible"), 3000);
    };
    const queueToast = message => {
        try { sessionStorage.setItem(TOAST_KEY, message); } catch (_) {}
    };
    if (toast) {
        toast.addEventListener("click", () => { clearTimeout(showToast.timer); toast.classList.remove("visible"); });
        try {
            const pending = sessionStorage.getItem(TOAST_KEY);
            if (pending !== null) { sessionStorage.removeItem(TOAST_KEY); showToast(pending); }
        } catch (_) {}
    }
    const date = value => (value || "").split(".")[0] || "Never";
    const escape = value => String(value ?? "").replace(/[&<>"']/g, character => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[character]);
    const safeUrl = value => {
        try {
            const url = new URL(value);
            return ["http:", "https:"].includes(url.protocol) ? url.href : "#";
        } catch (_) {
            return "#";
        }
    };

    const scheduleFrequency = item => {
        if (item.trigger === "cron") return `Cron: ${escape(item.cron_crontab)}`;
        const pieces = ["weeks", "days", "hours", "minutes", "seconds"].flatMap(unit => {
            const value = Number(item[`interval_${unit}`]);
            return value ? [`${value} ${value === 1 ? unit.slice(0, -1) : unit}`] : [];
        });
        return pieces.length ? `Every ${pieces.join(", ")}` : "Not configured";
    };

    const renderPagination = (nav, info, onPage) => {
        const {page = 1, pages = 0, total = 0} = info || {};
        if (!nav) return;
        nav.textContent = "";
        if (!pages || pages <= 1) return;
        const prev = document.createElement("button");
        prev.type = "button";
        prev.className = "button secondary";
        prev.textContent = "Previous";
        prev.disabled = page <= 1;
        const next = document.createElement("button");
        next.type = "button";
        next.className = "button secondary";
        next.textContent = "Next";
        next.disabled = page >= pages;
        prev.addEventListener("click", () => onPage(page - 1));
        next.addEventListener("click", () => onPage(page + 1));
        const label = document.createElement("span");
        label.className = "pagination-info";
        label.textContent = `Page ${page} of ${pages} (${total} total)`;
        nav.append(prev, label, next);
    };

    const initSchedules = async () => {
        const tbody = page.querySelector("tbody");
        const nav = page.querySelector(".pagination");
        const current = Math.max(1, Number(params.get("page")) || 1);
        const pageSize = Math.max(1, Number(params.get("page_size")) || 20);
        const load = async pageNum => {
            try {
                const {schedules = {}, pagination = {}} = await api(`schedules?page=${pageNum}&page_size=${pageSize}`);
                const entries = Object.entries(schedules);
                if (!entries.length && !pagination.total) {
                    tbody.innerHTML = '<tr><td colspan="5">No schedules yet. Add one to begin monitoring.</td></tr>';
                    renderPagination(nav, pagination, load);
                    return;
                }
                tbody.innerHTML = entries.map(([scheduleId, item]) => `<tr>
                    <td><a href="/schedule.html?id=${encodeURIComponent(scheduleId)}">${escape(item.title || "Untitled")}</a><br><small><a href="${safeUrl(item.url)}" target="_blank" rel="noopener">${escape(item.url || "")}</a></small></td>
                    <td>${scheduleFrequency(item)}</td><td>${date(item.last_history && item.last_history.message)}</td><td>${date(item.last_change && item.last_change.message)}</td>
                    <td class="row-actions"><a class="button secondary" href="/history.html?id=${encodeURIComponent(scheduleId)}" aria-label="History" title="History"><span class="material-icons" aria-hidden="true">history</span></a> <button class="button primary" data-run="${escape(scheduleId)}" aria-label="Run now" title="Run now"><span class="material-icons" aria-hidden="true">play_arrow</span></button> <a class="button secondary" href="/schedule.html?id=${encodeURIComponent(scheduleId)}" aria-label="Edit" title="Edit"><span class="material-icons" aria-hidden="true">edit</span></a></td>
                </tr>`).join("");
                renderPagination(nav, pagination, load);
            } catch (error) { showStatus(error.message, true); }
        };
        await load(current);
        tbody.addEventListener("click", async event => {
            const button = event.target.closest("[data-run]");
            if (!button) return;
            button.disabled = true;
            try { await api(`schedules/${encodeURIComponent(button.dataset.run)}/run`, {method: "POST"}); showToast("Schedule started."); }
            catch (error) { showToast(error.message, true); }
            finally { button.disabled = false; }
        });
    };

    const initSchedule = async () => {
        const form = page;
        const input = name => form.elements.namedItem(name);
        const cron = form.querySelector(".cron-field");
        const interval = form.querySelector(".interval-fields");
        const remove = form.querySelector(".delete");
        const updateTrigger = () => {
            const isCron = input("trigger").value === "cron";
            cron.hidden = !isCron; interval.hidden = isCron;
        };
        const authCredFields = form.querySelector(".auth-cred-fields");
        const authTokenField = form.querySelector(".auth-token-field");
        const bodyFields = form.querySelector(".body-fields");
        const httpLegend = form.querySelector(".http-legend");
        const httpOnlyFields = Array.from(form.querySelectorAll(".http-only"));
        const updateHttpFields = () => {
            const authType = input("auth_type").value;
            const isFtp = ["ftp", "ftps"].includes((input("url").value.split(":", 1)[0] || "").toLowerCase());
            httpOnlyFields.forEach(el => { el.hidden = isFtp; });
            httpLegend.textContent = isFtp ? "FTP credentials" : "HTTP request";
            authCredFields.hidden = !((authType === "basic" || authType === "digest") || isFtp);
            authTokenField.hidden = authType !== "bearer" || isFtp;
            bodyFields.hidden = input("http_method").value === "GET" || isFtp;
        };
        const populate = schedule => Object.entries(schedule).forEach(([key, value]) => {
            const field = input(key);
            if (!field) return;
            if (field.type === "checkbox") field.checked = Boolean(value);
            else field.value = value ?? "";
        });
        if (!id) remove.hidden = true;
        else {
            try { populate((await api(`schedules/${encodeURIComponent(id)}`)).schedule || {}); }
            catch (error) { showStatus(error.message, true); }
        }
        updateTrigger();
        updateHttpFields();
        input("trigger").addEventListener("change", updateTrigger);
        input("http_method").addEventListener("change", updateHttpFields);
        input("auth_type").addEventListener("change", updateHttpFields);
        input("url").addEventListener("input", updateHttpFields);
        form.addEventListener("submit", async event => {
            event.preventDefault();
            if (!form.reportValidity()) return;
            const data = Object.fromEntries(new FormData(form).entries());
            data.enabled = input("enabled").checked;
            try {
                await api(id ? `schedules/${encodeURIComponent(id)}` : "schedules", {
                    method: id ? "PUT" : "POST", body: JSON.stringify(data),
                });
                queueToast("Schedule saved.");
                window.location.assign("/");
            } catch (error) { showToast(error.message, true); }
        });
        remove.addEventListener("click", async () => {
            if (!window.confirm("Delete this schedule and its stored history?")) return;
            try { await api(`schedules/${encodeURIComponent(id)}`, {method: "DELETE"}); queueToast("Schedule deleted."); window.location.assign("/"); }
            catch (error) { showToast(error.message, true); }
        });
    };

    const initHistory = async () => {
        const heading = page.querySelector("h1");
        const tbody = page.querySelector("tbody");
        const toggle = page.querySelector("[name=show-empty]");
        const nav = page.querySelector(".pagination");
        if (!id) { showStatus("A schedule ID is required.", true); return; }
        const pageSize = Math.max(1, Number(params.get("page_size")) || 20);
        const render = data => {
            const entries = data.history || [];
            tbody.innerHTML = entries.length ? entries.map(item => `<tr><td><code>${escape(item.id.slice(0, 7))}</code></td><td>+${item.insertions}, −${item.deletions}</td><td>${date(item.message)}</td><td><a class="button secondary" href="/diff.html?id=${encodeURIComponent(id)}&diff=${encodeURIComponent(item.id)}"><span class="material-icons" aria-hidden="true">find_in_page</span>View diff</a></td><td><a class="button secondary" href="/revision.html?id=${encodeURIComponent(id)}&revision=${encodeURIComponent(item.id)}"><span class="material-icons" aria-hidden="true">description</span>View page</a></td></tr>`).join("") : '<tr><td colspan="5">No matching history entries.</td></tr>';
            renderPagination(nav, data.pagination, load);
        };
        const load = async pageNum => {
            try {
                const showEmpty = toggle.checked ? 1 : 0;
                const data = await api(`schedules/${encodeURIComponent(id)}/history?page=${pageNum}&page_size=${pageSize}&show_empty=${showEmpty}`);
                heading.textContent = `${data.schedule.title || "Schedule"} history`;
                render(data);
            } catch (error) { showStatus(error.message, true); }
        };
        await load(Math.max(1, Number(params.get("page")) || 1));
        toggle.addEventListener("change", () => load(1));
    };

    const initDiff = async () => {
        const diff = params.get("diff");
        const oldid = params.get("oldid");
        page.querySelector(".back-history").href = `/history.html?id=${encodeURIComponent(id || "")}`;
        if (!id || !diff) { showStatus("A schedule ID and revision are required.", true); return; }
        try {
            const data = await api(`schedules/${encodeURIComponent(id)}/diff/${encodeURIComponent(diff)}/${encodeURIComponent(oldid || "")}`);
            page.querySelector("h1").textContent = `${data.schedule.title || "Schedule"} diff`;
            const output = page.querySelector(".diff-output");
            const notice = page.querySelector(".diff-truncated-notice");
            const fileNav = page.querySelector(".diff-file-nav");
            const fileSelect = page.querySelector(".diff-file-select");
            const files = data.files || [];
            if (data.truncated) {
                notice.textContent = `Output truncated: showing the first ${data.shown_lines} of ${data.total_lines} diff lines.`;
                notice.hidden = false;
            }
            const rendered = {};
            let current = 0;
            let mode = "line-by-line";
            const render = () => {
                const file = files[current];
                const key = `${current}:${mode}`;
                output.innerHTML = rendered[key] || (rendered[key] = Diff2Html.getPrettyHtml(file.diff, { outputFormat: mode }));
            };
            if (!files.length) {
                output.textContent = "No differences.";
                page.querySelector(".diff-view-toggle").hidden = true;
            } else {
                if (files.length > 1) {
                    fileNav.hidden = false;
                    fileSelect.innerHTML = files.map((file, index) => `<option value="${index}">${escape(file.name || "unknown file")}</option>`).join("");
                    const goto = index => {
                        current = Math.min(Math.max(index, 0), files.length - 1);
                        fileSelect.value = current;
                        render();
                    };
                    fileSelect.addEventListener("change", () => goto(Number(fileSelect.value)));
                    page.querySelector(".diff-file-prev").addEventListener("click", () => goto(current - 1));
                    page.querySelector(".diff-file-next").addEventListener("click", () => goto(current + 1));
                }
                render();
            }
            page.querySelectorAll(".diff-view-button").forEach(button => {
                button.addEventListener("click", () => {
                    page.querySelectorAll(".diff-view-button").forEach(other => other.classList.toggle("active", other === button));
                    mode = button.dataset.mode;
                    if (files.length) render();
                });
            });
        } catch (error) { showStatus(error.message, true); }
    };

    const initRevision = async () => {
        const revision = params.get("revision");
        page.querySelector(".back-history").href = `/history.html?id=${encodeURIComponent(id || "")}`;
        if (!id || !revision) { showStatus("A schedule ID and revision are required.", true); return; }
        try {
            const data = await api(`schedules/${encodeURIComponent(id)}/revision/${encodeURIComponent(revision)}`);
            page.querySelector("h1").textContent = `${data.schedule.title || "Schedule"} revision ${revision.slice(0, 7)}`;
            const files = (data.revision && data.revision.files) || [];
            const output = page.querySelector(".revision-output");
            if (!files.length) output.textContent = "No content stored for this revision.";
            else if (files.length === 1) output.textContent = files[0].content || "No content stored for this revision.";
            else output.textContent = files.map(file => `--- ${file.name} ---\n${file.content}`).join("\n\n");
        } catch (error) { showStatus(error.message, true); }
    };

    ({schedules: initSchedules, schedule: initSchedule, history: initHistory, diff: initDiff, revision: initRevision}[page.dataset.page])();
})();
