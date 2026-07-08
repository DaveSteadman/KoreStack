(() => {
    const state = {
        entries: [],
        providers: [],
        selectedTool: null,
        filterText: "",
    };

    const $ = (id) => document.getElementById(id);

    function api(path, options = {}) {
        return fetch(path, {
            headers: { "Content-Type": "application/json" },
            ...options,
        }).then(async (res) => {
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                const msg = (data && data.detail) ? data.detail : `${res.status} ${res.statusText}`;
                throw new Error(msg);
            }
            return data;
        });
    }

    function textOf(obj) {
        try {
            return JSON.stringify(obj, null, 2);
        } catch {
            return String(obj);
        }
    }

    function selectedEntry() {
        if (!state.selectedTool) return null;
        return state.entries.find((item) => item.tool_name === state.selectedTool) || null;
    }

    function renderStats(stats) {
        $("stats").textContent = `${stats.entry_count} functions • ${stats.provider_count} providers • ${stats.active_count} active`;
    }

    function renderList() {
        const host = $("tool-list");
        host.innerHTML = "";

        const needle = state.filterText.trim().toLowerCase();
        const rows = state.entries.filter((entry) => {
            if (!needle) return true;
            const corpus = `${entry.tool_name} ${entry.provider_label} ${entry.skill_name}`.toLowerCase();
            return corpus.includes(needle);
        });

        for (const entry of rows) {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = `tool-row${entry.tool_name === state.selectedTool ? " is-active" : ""}`;
            btn.innerHTML = `
                <div class="tool-name">${entry.tool_name}</div>
                <div class="tool-meta">${entry.provider_label} • ${entry.call_type}</div>
            `;
            btn.addEventListener("click", () => {
                state.selectedTool = entry.tool_name;
                renderList();
                renderDetail();
            });
            host.appendChild(btn);
        }
    }

    function renderDetail() {
        const entry = selectedEntry();
        if (!entry) {
            $("detail-title").textContent = "Select a skill function";
            $("detail-meta").textContent = "";
            $("source-view").textContent = "Select a function to inspect source.";
            return;
        }

        $("detail-title").textContent = entry.function_signature || entry.tool_name;
        const lines = [
            `Provider: ${entry.provider_label}`,
            `Type: ${entry.call_type}`,
            entry.purpose ? `Purpose: ${entry.purpose}` : "",
            entry.module_path ? `Module: ${entry.module_path}` : "",
            entry.skill_md_path ? `Skill MD: ${entry.skill_md_path}` : "",
        ].filter(Boolean);
        $("detail-meta").textContent = lines.join("\n");
        $("invoke-args").value = "{}";
        $("invoke-result").textContent = "Run a function to view result.";
    }

    async function loadCatalog() {
        const data = await api("/api/skills/catalog");
        state.entries = Array.isArray(data.entries) ? data.entries : [];
        state.providers = Array.isArray(data.providers) ? data.providers : [];

        if (!state.selectedTool && state.entries.length > 0) {
            state.selectedTool = state.entries[0].tool_name;
        }

        renderStats(data.stats || { entry_count: 0, provider_count: 0, active_count: 0 });
        renderList();
        renderDetail();
    }

    async function loadSource(kind) {
        const entry = selectedEntry();
        if (!entry) return;
        try {
            const data = await api(`/api/skills/source?tool_name=${encodeURIComponent(entry.tool_name)}&source_kind=${encodeURIComponent(kind)}`);
            $("source-view").textContent = `Path: ${data.path}\n\n${data.content}`;
        } catch (err) {
            $("source-view").textContent = `Unable to load source: ${err.message}`;
        }
    }

    function parseArgs() {
        const raw = $("invoke-args").value.trim();
        if (!raw) return {};
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
            throw new Error("Arguments must be a JSON object");
        }
        return parsed;
    }

    async function runTool() {
        const entry = selectedEntry();
        if (!entry) return;

        let args;
        try {
            args = parseArgs();
        } catch (err) {
            $("invoke-result").textContent = `Invalid JSON arguments: ${err.message}`;
            return;
        }

        $("invoke-result").textContent = "Running...";
        try {
            const data = await api("/api/skills/invoke", {
                method: "POST",
                body: JSON.stringify({
                    tool_name: entry.tool_name,
                    arguments: args,
                }),
            });
            $("invoke-result").textContent = textOf(data);
        } catch (err) {
            $("invoke-result").textContent = `Invocation failed: ${err.message}`;
        }
    }

    function wireEvents() {
        $("btn-back").addEventListener("click", () => {
            window.location.href = "/";
        });

        $("btn-refresh").addEventListener("click", () => {
            loadCatalog().catch((err) => {
                $("invoke-result").textContent = `Catalog refresh failed: ${err.message}`;
            });
        });

        $("tool-filter").addEventListener("input", (ev) => {
            state.filterText = String(ev.target.value || "");
            renderList();
        });

        $("btn-load-module").addEventListener("click", () => loadSource("module"));
        $("btn-load-skillmd").addEventListener("click", () => loadSource("skill_md"));
        $("btn-run").addEventListener("click", runTool);

        $("btn-pretty").addEventListener("click", () => {
            try {
                const args = parseArgs();
                $("invoke-args").value = JSON.stringify(args, null, 2);
            } catch (err) {
                $("invoke-result").textContent = `Cannot format JSON: ${err.message}`;
            }
        });
    }

    function initShell() {
        if (window.KoreChrome && typeof window.KoreChrome.initShell === "function") {
            window.KoreChrome.initShell({
                serviceLabel: "KoreAgent Skills Catalog",
                serviceLinks: [{ label: "KoreAgent", href: "/" }],
                activeService: "KoreAgent",
            });
        }
    }

    function init() {
        initShell();
        wireEvents();
        loadCatalog().catch((err) => {
            $("invoke-result").textContent = `Initial load failed: ${err.message}`;
        });
    }

    document.addEventListener("DOMContentLoaded", init);
})();
