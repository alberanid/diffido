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

    const initSchedules = async () => {
        const tbody = page.querySelector("tbody");
        try {
            const {schedules = {}} = await api("schedules");
            const entries = Object.entries(schedules);
            if (!entries.length) {
                tbody.innerHTML = '<tr><td colspan="4">No schedules yet. Add one to begin monitoring.</td></tr>';
                return;
            }
            tbody.innerHTML = entries.map(([scheduleId, item]) => `<tr>
                <td><a href="/schedule.html?id=${encodeURIComponent(scheduleId)}">${escape(item.title || "Untitled")}</a><br><small><a href="${safeUrl(item.url)}" target="_blank" rel="noopener">${escape(item.url || "")}</a></small></td>
                <td>${scheduleFrequency(item)}</td><td>${date(item.last_history && item.last_history.message)}</td>
                <td><a class="button secondary" href="/history.html?id=${encodeURIComponent(scheduleId)}">History</a> <button class="button primary" data-run="${escape(scheduleId)}">Run now</button></td>
            </tr>`).join("");
        } catch (error) { showStatus(error.message, true); }
        tbody.addEventListener("click", async event => {
            const button = event.target.closest("[data-run]");
            if (!button) return;
            button.disabled = true;
            try { await api(`schedules/${encodeURIComponent(button.dataset.run)}/run`, {method: "POST"}); showStatus("Schedule started."); }
            catch (error) { showStatus(error.message, true); }
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
        input("trigger").addEventListener("change", updateTrigger);
        form.addEventListener("submit", async event => {
            event.preventDefault();
            if (!form.reportValidity()) return;
            const data = Object.fromEntries(new FormData(form).entries());
            data.enabled = input("enabled").checked;
            try {
                await api(id ? `schedules/${encodeURIComponent(id)}` : "schedules", {
                    method: id ? "PUT" : "POST", body: JSON.stringify(data),
                });
                window.location.assign("/");
            } catch (error) { showStatus(error.message, true); }
        });
        remove.addEventListener("click", async () => {
            if (!window.confirm("Delete this schedule and its stored history?")) return;
            try { await api(`schedules/${encodeURIComponent(id)}`, {method: "DELETE"}); window.location.assign("/"); }
            catch (error) { showStatus(error.message, true); }
        });
    };

    const initHistory = async () => {
        const heading = page.querySelector("h1");
        const tbody = page.querySelector("tbody");
        const toggle = page.querySelector("[name=show-empty]");
        if (!id) { showStatus("A schedule ID is required.", true); return; }
        try {
            const data = await api(`schedules/${encodeURIComponent(id)}/history`);
            heading.textContent = `${data.schedule.title || "Schedule"} history`;
            const render = () => {
                const entries = toggle.checked ? data.history : data.history.filter(item => item.changes);
                tbody.innerHTML = entries.length ? entries.map(item => `<tr><td><code>${escape(item.id.slice(0, 7))}</code></td><td>+${item.insertions}, −${item.deletions}</td><td>${date(item.message)}</td><td><a class="button secondary" href="/diff.html?id=${encodeURIComponent(id)}&diff=${encodeURIComponent(item.id)}">View diff</a></td></tr>`).join("") : '<tr><td colspan="4">No matching history entries.</td></tr>';
            };
            render(); toggle.addEventListener("change", render);
        } catch (error) { showStatus(error.message, true); }
    };

    const initDiff = async () => {
        const diff = params.get("diff");
        const oldid = params.get("oldid");
        page.querySelector(".back-history").href = `/history.html?id=${encodeURIComponent(id || "")}`;
        if (!id || !diff) { showStatus("A schedule ID and revision are required.", true); return; }
        try {
            const data = await api(`schedules/${encodeURIComponent(id)}/diff/${encodeURIComponent(diff)}/${encodeURIComponent(oldid || "")}`);
            page.querySelector("h1").textContent = `${data.schedule.title || "Schedule"} diff`;
            page.querySelector(".diff-output").textContent = data.diff || "No differences.";
        } catch (error) { showStatus(error.message, true); }
    };

    ({schedules: initSchedules, schedule: initSchedule, history: initHistory, diff: initDiff}[page.dataset.page])();
})();
