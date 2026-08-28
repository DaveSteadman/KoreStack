(() => {
    const $ = (id) => document.getElementById(id);
    const input = $("packet-input");
    const output = $("packet-response");
    const status = $("input-status");
    const sendButton = $("btn-send");

    function setStatus(text, className = "") {
        status.textContent = text;
        status.className = `kcui-tag kcui-tag--dim${className ? ` ${className}` : ""}`;
    }

    function parseInput() {
        const text = input.value.trim();
        if (!text) throw new Error("Enter a JSON work packet first.");
        JSON.parse(text);
        return text;
    }

    $("btn-back").addEventListener("click", () => { window.location.href = "/"; });
    $("btn-format").addEventListener("click", () => {
        try {
            input.value = JSON.stringify(JSON.parse(input.value), null, 2);
            setStatus("Valid JSON");
        } catch (error) {
            setStatus(error.message, "is-error");
        }
    });

    async function sendPacket() {
        let jsonText;
        try {
            jsonText = parseInput();
        } catch (error) {
            setStatus(error.message, "is-error");
            return;
        }

        setStatus("Running…", "is-busy");
        sendButton.disabled = true;
        output.textContent = "Waiting for the LLM…";
        $("response-meta").textContent = "";
        try {
            const response = await fetch("/api/work-packet", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ json_text: jsonText }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.detail || `${response.status} ${response.statusText}`);
            output.textContent = data.response || "(The model returned an empty response.)";
            const tps = Number(data.tokens_per_second || 0).toFixed(1);
            $("response-meta").textContent = `${data.model} • ${data.prompt_tokens} in / ${data.completion_tokens} out • ${tps} tok/s`;
            setStatus("Complete");
        } catch (error) {
            output.textContent = `Error: ${error.message}`;
            setStatus("Failed", "is-error");
        } finally {
            sendButton.disabled = false;
        }
    }

    sendButton.addEventListener("click", sendPacket);
    input.addEventListener("keydown", (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === "Enter") sendPacket();
    });
})();
