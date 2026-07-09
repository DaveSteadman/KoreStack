(() => {
    const state = {
        entries:       [],
        providers:     [],
        selectedTool:  null,
        filterText:    "",
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

    function cloneValue(value) {
        if (typeof structuredClone === "function") {
            return structuredClone(value);
        }
        return JSON.parse(JSON.stringify(value));
    }

    function resizeInvokeArgs() {
        const el = $("invoke-args");
        if (!el) return;

        el.style.height = "auto";
        el.style.height = `${Math.max(el.scrollHeight, 180)}px`;
    }

    function selectedEntry() {
        if (!state.selectedTool) return null;
        return state.entries.find((item) => item.tool_name === state.selectedTool) || null;
    }

    function schemaType(schema) {
        if (!schema || typeof schema !== "object") return "";
        if (Array.isArray(schema.type)) return String(schema.type[0] || "");
        return String(schema.type || "");
    }

    function describeSchema(schema, fallbackName = "") {
        if (!schema || typeof schema !== "object") return "value";

        const schemaTypeName = schemaType(schema).toLowerCase();
        if (schemaTypeName) return schemaTypeName;
        if (schema.properties && typeof schema.properties === "object") return "object";
        if (schema.items && typeof schema.items === "object") return "array";
        if (fallbackName) return fallbackName;
        return "value";
    }

    function renderParameterSummary(schema) {
        if (!schema || typeof schema !== "object") {
            return "No structured parameter schema available.";
        }

        const props     = (schema.properties && typeof schema.properties === "object") ? schema.properties : {};
        const required  = new Set(Array.isArray(schema.required) ? schema.required.map((item) => String(item)) : []);
        const propNames = Object.keys(props);

        if (propNames.length === 0) {
            return "No parameters.";
        }

        const lines = [];
        for (const name of propNames) {
            const prop        = props[name];
            const typeName    = describeSchema(prop, name);
            const requirement = required.has(name) ? "required" : "optional";
            const desc        = String((prop && prop.description) || "").trim();
            const enumText    = Array.isArray(prop?.enum) && prop.enum.length > 0
                ? ` Allowed: ${prop.enum.map((item) => JSON.stringify(item)).join(", ")}.`
                : "";
            const defaultText = prop && Object.prototype.hasOwnProperty.call(prop, "default") && prop.default !== null
                ? ` Default: ${JSON.stringify(prop.default)}.`
                : "";
            lines.push(`${name} (${typeName}, ${requirement})`);
            if (desc || enumText || defaultText) {
                lines.push(`  ${desc}${enumText}${defaultText}`.trimEnd());
            }
        }
        return lines.join("\n");
    }

    function compactProviderLabel(entry) {
        const raw = String(entry?.provider_label || "").trim();
        if (!raw) return "";

        if (entry?.provider_type === "local") {
            return raw.replace(/^KoreAgent\s+/i, "");
        }

        if (/^https?:\/\//i.test(raw)) {
            try {
                return new URL(raw).host || raw;
            } catch {
                return raw;
            }
        }

        return raw.length > 56 ? `${raw.slice(0, 53)}...` : raw;
    }

    function renderStats(stats) {
        $("stats").textContent = `${stats.entry_count} functions | ${stats.provider_count} providers | ${stats.active_count} active`;
    }

    function renderList() {
        const host = $("tool-list");
        host.innerHTML = "";

        const needle = state.filterText.trim().toLowerCase();
        const rows = state.entries.filter((entry) => {
            if (!needle) return true;
            const corpus = `${entry.tool_name} ${entry.provider_label} ${entry.skill_name} ${entry.description || ""}`.toLowerCase();
            return corpus.includes(needle);
        });

        for (const entry of rows) {
            const btn = document.createElement("button");
            btn.type      = "button";
            btn.className = `tool-row${entry.tool_name === state.selectedTool ? " is-active" : ""}`;
            btn.innerHTML = `<span class="tool-name">${entry.tool_name}</span>`;
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
            $("detail-title").textContent    = "Select a skill function";
            $("detail-tags").innerHTML       = "";
            $("detail-meta").textContent     = "";
            $("detail-schema").textContent   = "Select a function to inspect parameters.";
            $("invoke-note").textContent     = "";
            $("source-view").textContent     = "Select a function to inspect source.";
            $("btn-load-module").disabled    = true;
            $("btn-load-skillmd").disabled   = true;
            return;
        }

        const canLoadModule  = Boolean(entry.module_path);
        const canLoadSkillMd = Boolean(entry.skill_md_path);
        const providerLabel  = compactProviderLabel(entry);
        const tags = [
            providerLabel ? { label: providerLabel, kind: "dim" } : null,
            entry.call_type ? { label: String(entry.call_type).toUpperCase(), kind: "dim" } : null,
        ].filter(Boolean);

        $("detail-title").textContent = entry.function_signature || entry.tool_name;
        $("detail-tags").innerHTML = tags.map((tag) => `<span class="kcui-tag kcui-tag--${tag.kind}">${tag.label}</span>`).join("");
        const lines = [
            providerLabel ? `Provider: ${providerLabel}` : "",
            `Type: ${entry.call_type}`,
            entry.description ? `Description: ${entry.description}` : "",
            entry.module_path ? `Module: ${entry.module_path}` : "",
            entry.skill_md_path ? `Skill MD: ${entry.skill_md_path}` : "",
        ].filter(Boolean);
        $("detail-meta").textContent   = lines.join("\n");
        $("detail-schema").textContent = renderParameterSummary(entry.parameters_schema);
        $("invoke-note").textContent = entry.call_type === "python"
            ? "Direct execution: Run invokes the Python function locally through the backend. Any LLM calls would only happen if the skill implementation itself makes them."
            : "Direct execution: Run invokes the MCP tool through the backend without the chat orchestration loop.";
        $("invoke-args").value         = textOf(cloneValue(entry.invoke_template || {}));
        resizeInvokeArgs();
        $("invoke-result").textContent = "Run a function to view result.";
        $("btn-load-module").disabled  = !canLoadModule;
        $("btn-load-skillmd").disabled = !canLoadSkillMd;

        if (!canLoadModule && !canLoadSkillMd) {
            $("source-view").textContent = "No local source is available for this tool.";
        } else {
            $("source-view").textContent = "Select a source to inspect.";
        }
    }

    async function loadCatalog() {
        const data   = await api("/api/skills/catalog");
        state.entries   = Array.isArray(data.entries) ? data.entries : [];
        state.providers = Array.isArray(data.providers) ? data.providers : [];

        const selectedStillExists = state.entries.some((item) => item.tool_name === state.selectedTool);
        if (!selectedStillExists) {
            state.selectedTool = state.entries.length > 0 ? state.entries[0].tool_name : null;
        }

        renderStats(data.stats || { entry_count: 0, provider_count: 0, active_count: 0 });
        renderList();
        renderDetail();
    }

    async function loadSource(kind) {
        const entry = selectedEntry();
        if (!entry) return;

        const allowed = kind === "module" ? Boolean(entry.module_path) : Boolean(entry.skill_md_path);
        if (!allowed) {
            $("source-view").textContent = "No local source is available for this tool.";
            return;
        }

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
                body:   JSON.stringify({
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

        $("invoke-args").addEventListener("input", resizeInvokeArgs);

        $("btn-load-module").addEventListener("click", () => loadSource("module"));
        $("btn-load-skillmd").addEventListener("click", () => loadSource("skill_md"));
        $("btn-run").addEventListener("click", runTool);

        $("btn-pretty").addEventListener("click", () => {
            try {
                const args = parseArgs();
                $("invoke-args").value = JSON.stringify(args, null, 2);
                resizeInvokeArgs();
            } catch (err) {
                $("invoke-result").textContent = `Cannot format JSON: ${err.message}`;
            }
        });
    }

    function initShell() {
        if (window.KoreChrome && typeof window.KoreChrome.initShell === "function") {
            window.KoreChrome.initShell({
                serviceLabel:  "KoreAgent Skills Catalog",
                serviceLinks:  [{ label: "KoreAgent", href: "/" }],
                activeService: "KoreAgent",
            });
        }
    }

    function init() {
        initShell();
        wireEvents();
        window.addEventListener("resize", resizeInvokeArgs);
        loadCatalog().catch((err) => {
            $("invoke-result").textContent = `Initial load failed: ${err.message}`;
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
