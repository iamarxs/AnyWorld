"use strict";
const SCENARIO_STORAGE_KEY = "artificialDungeonScenarios";
const scenarioFields = ["scenario", "guidance", "chancePercent", "chanceCadence",
    "chanceTrigger", "chanceEligibility", "chanceEffect", "chanceScope"];
const scenarioLibrary = {
    list: document.getElementById("saved-scenarios"),
    name: document.getElementById("scenario-save-name"),
    save: document.getElementById("save-scenario"),
    load: document.getElementById("load-scenario"),
    delete: document.getElementById("delete-scenario"),
    export: document.getElementById("export-scenario"),
    import: document.getElementById("import-scenario"),
    file: document.getElementById("scenario-file"),
    status: document.getElementById("scenario-storage-status"),
};

function validScenario(entry) {
    return entry && typeof entry.name === "string" && entry.name.trim() &&
        entry.fields && scenarioFields.every((field) => typeof entry.fields[field] === "string") &&
        ["", "per_round", "condition"].includes(entry.fields.chanceCadence) &&
        ["", "shared", "per_player"].includes(entry.fields.chanceScope);
}

function currentScenario(name) {
    return { name, fields: Object.fromEntries(scenarioFields.map((field) =>
        [field, elements[field].value])) };
}

function loadScenario(entry) {
    for (const field of scenarioFields) elements[field].value = entry.fields[field];
    scenarioLibrary.name.value = entry.name;
}

function readScenarios() {
    const stored = window.localStorage.getItem(SCENARIO_STORAGE_KEY);
    const scenarios = stored === null ? [] : JSON.parse(stored);
    if (!Array.isArray(scenarios) || scenarios.some((entry) => !validScenario(entry))) {
        throw new Error("Invalid saved scenarios.");
    }
    return scenarios;
}

function scenarioStorageStatus(message, failed = false) {
    scenarioLibrary.status.textContent = message;
    scenarioLibrary.status.classList[failed ? "add" : "remove"]("error");
}

function useScenarioStorage(action) {
    try { action(); } catch {
        scenarioStorageStatus("Could not access saved scenarios. Browser storage may be unavailable, full, or contain invalid data.", true);
    }
}

function updateScenarioSelection() {
    scenarioLibrary.load.disabled = !scenarioLibrary.list.value;
    scenarioLibrary.delete.disabled = !scenarioLibrary.list.value;
}

function renderSavedScenarios(scenarios, selected = "") {
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = scenarios.length ? "Choose a saved scenario" : "No saved scenarios";
    scenarioLibrary.list.replaceChildren(placeholder);
    for (const entry of scenarios) {
        const option = document.createElement("option");
        option.value = entry.name;
        option.textContent = entry.name;
        scenarioLibrary.list.append(option);
    }
    scenarioLibrary.list.value = selected;
    updateScenarioSelection();
}

scenarioLibrary.list.addEventListener("change", () => {
    updateScenarioSelection();
    if (scenarioLibrary.list.value) scenarioLibrary.name.value = scenarioLibrary.list.value;
    scenarioStorageStatus("");
});

elements.scenarioForm.addEventListener("reset", () => {
    scenarioLibrary.load.disabled = true;
    scenarioLibrary.delete.disabled = true;
    scenarioStorageStatus("");
});

scenarioLibrary.save.addEventListener("click", () => useScenarioStorage(() => {
    const name = scenarioLibrary.name.value.trim();
    if (!name) { scenarioStorageStatus("Enter a name for this scenario.", true); return; }
    const scenarios = readScenarios();
    const index = scenarios.findIndex((entry) => entry.name === name);
    if (index !== -1 && !window.confirm(`Replace saved scenario “${name}”?`)) return;
    const entry = currentScenario(name);
    if (index === -1) scenarios.push(entry);
    else scenarios[index] = entry;
    // Do not use writeStored: its silent fallback would falsely report a successful save.
    window.localStorage.setItem(SCENARIO_STORAGE_KEY, JSON.stringify(scenarios));
    renderSavedScenarios(scenarios, name);
    scenarioStorageStatus("Scenario saved in this browser.");
}));

scenarioLibrary.load.addEventListener("click", () => useScenarioStorage(() => {
    const scenarios = readScenarios();
    const entry = scenarios.find((entry) => entry.name === scenarioLibrary.list.value);
    if (!entry) {
        renderSavedScenarios(scenarios);
        scenarioStorageStatus("Choose an available saved scenario.", true);
        return;
    }
    loadScenario(entry);
    scenarioStorageStatus("Scenario loaded into the form.");
}));

scenarioLibrary.delete.addEventListener("click", () => useScenarioStorage(() => {
    const name = scenarioLibrary.list.value;
    if (!name || !window.confirm(`Delete saved scenario “${name}”?`)) return;
    const scenarios = readScenarios().filter((entry) => entry.name !== name);
    window.localStorage.setItem(SCENARIO_STORAGE_KEY, JSON.stringify(scenarios));
    renderSavedScenarios(scenarios);
    if (scenarioLibrary.name.value === name) scenarioLibrary.name.value = "";
    scenarioStorageStatus("Saved scenario deleted. The current form is unchanged.");
}));

scenarioLibrary.export.addEventListener("click", () => {
    try {
        const entry = currentScenario(scenarioLibrary.name.value.trim() || "Scenario");
        const link = document.createElement("a");
        link.href = URL.createObjectURL(new Blob([JSON.stringify(entry, null, 2) + "\n"],
            { type: "application/json" }));
        link.download = `${entry.name.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").slice(0, 100)}.json`;
        try { link.click(); } finally { URL.revokeObjectURL(link.href); }
        scenarioStorageStatus("Scenario file download requested.");
    } catch {
        scenarioStorageStatus("Could not save the scenario file.", true);
    }
});

scenarioLibrary.import.addEventListener("click", () => scenarioLibrary.file.click());
scenarioLibrary.file.addEventListener("change", async () => {
    const file = scenarioLibrary.file.files[0];
    scenarioLibrary.file.value = "";
    if (!file) return;
    try {
        const entry = JSON.parse(await file.text());
        if (!validScenario(entry)) throw new Error("Invalid scenario file.");
        loadScenario(entry);
        scenarioStorageStatus("Scenario file loaded into the form. Use Save scenario to keep it in this browser.");
    } catch {
        scenarioStorageStatus("Could not load the file. Choose a scenario JSON file saved by Anyworld.", true);
    }
});

useScenarioStorage(() => renderSavedScenarios(readScenarios()));
