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

    const CURATED_MAX_TEMPLATES = {
        fetch_page_text: {
            url: "https://example.com/article",
            max_words: 2000,
            timeout_seconds: 15,
            query: "summarize key points",
        },
        get_page_links: {
            url: "https://example.com",
            filter_text: "",
            max_links: 30,
            timeout_seconds: 15,
        },
        get_page_links_text: {
            url: "https://example.com",
            filter_text: "",
            max_links: 30,
            timeout_seconds: 15,
        },
        search_web_text: {
            query: "latest AI agent platform updates",
            max_results: 10,
            locale: "en-US",
            freshness_days: 7,
        },
        koredata_search: {
            query: "example search",
            domains: ["feeds", "reference", "library", "rag", "scrape", "graph"],
            mode: "keyword",
            limit: 20,
        },
    };

    function splitArgsText(argText) {
        const parts = [];
        let current = "";
        let depth = 0;
        let quote = "";

        for (let i = 0; i < argText.length; i += 1) {
            const ch = argText[i];
            const prev = i > 0 ? argText[i - 1] : "";

            if (quote) {
                current += ch;
                if (ch === quote && prev !== "\\") quote = "";
                continue;
            }

            if (ch === '"' || ch === "'") {
                quote = ch;
                current += ch;
                continue;
            }

            if (ch === "(" || ch === "[" || ch === "{" ) {
                depth += 1;
                current += ch;
                continue;
            }

            if (ch === ")" || ch === "]" || ch === "}") {
                depth = Math.max(0, depth - 1);
                current += ch;
                continue;
            }

            if (ch === "," && depth === 0) {
                if (current.trim()) parts.push(current.trim());
                current = "";
                continue;
            }

            current += ch;
        }

        if (current.trim()) parts.push(current.trim());
        return parts;
    }

    function convertLiteral(text) {
        const raw = String(text || "").trim();
        if (!raw) return "example";

        const lower = raw.toLowerCase();
        if (lower === "none" || lower === "null") return "example";
        if (lower === "true") return true;
        if (lower === "false") return false;

        if (/^-?\d+$/.test(raw)) return Number.parseInt(raw, 10);
        if (/^-?\d+\.\d+$/.test(raw)) return Number.parseFloat(raw);

        if ((raw.startsWith("\"") && raw.endsWith("\"")) || (raw.startsWith("'") && raw.endsWith("'"))) {
            return raw.slice(1, -1);
        }

        if ((raw.startsWith("[") && raw.endsWith("]")) || (raw.startsWith("{") && raw.endsWith("}"))) {
            try {
                return JSON.parse(raw.replace(/'/g, '"'));
            } catch {
                return raw;
            }
        }

        return raw;
    }

    function placeholderFromName(name) {
        const n = String(name || "").toLowerCase();
        if (n.includes("url")) return "https://example.com";
        if (n.includes("path") || n.includes("file")) return "path/to/file.txt";
        if (n.includes("query") || n === "q") return "example search";
        if (n.includes("limit") || n.includes("count") || n.includes("max")) return 20;
        if (n.includes("timeout")) return 15;
        if (n.includes("enabled") || n.startsWith("is_")) return true;
        if (n.includes("ids") || n.endsWith("_list") || n.endsWith("_items")) return ["example"];
        return "example";
    }

    function templateFromSignature(signature) {
        const sig = String(signature || "").trim();
        const open = sig.indexOf("(");
        const close = sig.lastIndexOf(")");
        if (open < 0 || close <= open) return {};

        const argsText = sig.slice(open + 1, close).trim();
        if (!argsText) return {};

        const args = splitArgsText(argsText);
        const result = {};
        for (const token of args) {
            const cleaned = token.replace(/^\*\*?/, "").trim();
            if (!cleaned || cleaned === "/") continue;

            let left = cleaned;
            let right = "";
            const eq = cleaned.indexOf("=");
            if (eq >= 0) {
                left = cleaned.slice(0, eq).trim();
                right = cleaned.slice(eq + 1).trim();
            }

            const colon = left.indexOf(":");
            const name = (colon >= 0 ? left.slice(0, colon) : left).trim();
            if (!name) continue;

            result[name] = right ? convertLiteral(right) : placeholderFromName(name);
        }
        return result;
    }

    function schemaType(schema) {
        if (!schema || typeof schema !== "object") return "";
        if (Array.isArray(schema.type)) return String(schema.type[0] || "");
        return String(schema.type || "");
    }

    function exampleFromSchema(schema, propName = "") {
        if (!schema || typeof schema !== "object") return placeholderFromName(propName);

        if (Object.prototype.hasOwnProperty.call(schema, "default") && schema.default !== null) {
            return schema.default;
        }

        if (Array.isArray(schema.enum) && schema.enum.length > 0) {
            return schema.enum[0];
        }

        if (Array.isArray(schema.anyOf) && schema.anyOf.length > 0) {
            return exampleFromSchema(schema.anyOf[0], propName);
        }
        if (Array.isArray(schema.oneOf) && schema.oneOf.length > 0) {
            return exampleFromSchema(schema.oneOf[0], propName);
        }

        const t = schemaType(schema).toLowerCase();
        if (t === "object" || (!t && schema.properties)) {
            const props = (schema.properties && typeof schema.properties === "object") ? schema.properties : {};
            const out = {};
            for (const [key, value] of Object.entries(props)) {
                out[key] = exampleFromSchema(value, key);
            }
            return out;
        }
        if (t === "array") {
            return [exampleFromSchema(schema.items || {}, propName)];
        }
        if (t === "integer") return 1;
        if (t === "number") return 1.0;
        if (t === "boolean") return true;
        if (t === "string") {
            const format = String(schema.format || "").toLowerCase();
            if (format === "uri" || format === "url") return "https://example.com";
            if (format.includes("date")) return "2026-01-01";
            return placeholderFromName(propName);
        }

        return placeholderFromName(propName);
    }

    function buildInvokeTemplate(entry) {
        if (!entry) return {};

        const curated = CURATED_MAX_TEMPLATES[entry.tool_name];
        if (curated && typeof curated === "object") {
            return typeof structuredClone === "function"
                ? structuredClone(curated)
                : JSON.parse(JSON.stringify(curated));
        }

        if (entry.parameters_schema && typeof entry.parameters_schema === "object") {
            return exampleFromSchema(entry.parameters_schema);
        }

        return templateFromSignature(entry.function_signature);
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
        $("invoke-args").value = JSON.stringify(buildInvokeTemplate(entry), null, 2);
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
