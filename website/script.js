const gate = document.querySelector("#gate");
const site = document.querySelector("#site");
const openControls = document.querySelectorAll(".crest-button");
const themeToggle = document.querySelector(".theme-toggle");
const runTabs = document.querySelectorAll(".run-tab");
const runStepLabel = document.querySelector("#run-step-label");
const runStepTitle = document.querySelector("#run-step-title");
const runStepCopy = document.querySelector("#run-step-copy");
const runStepCode = document.querySelector("#run-step-code");
const runStepExpected = document.querySelector("#run-step-expected");
const runStepNote = document.querySelector("#run-step-note");
const copyCommand = document.querySelector(".copy-command");
const root = document.documentElement;

const runSteps = [
  {
    label: "Step 0",
    title: "Install Interdict",
    copy:
      "Install the launcher once. If you are evaluating from this repo today, install from source. If you want the app and Postgres together, use the Docker profile.",
    code:
      "pip install agent-db-safety\n# from source today:\npip install .\n\n# Docker alternative:\ndocker compose --profile app run --rm app",
    expected:
      "The agentdb and agentdb-mcp commands are available. The Docker path opens the launcher after the database becomes healthy.",
    note:
      "The package exposes two entrypoints: agentdb for the terminal launcher, and agentdb-mcp for agent integrations.",
  },
  {
    label: "Step 1",
    title: "Launch the safety layer",
    copy:
      "Start the seeded Postgres fixture, then run agentdb. The landing screen asks who is writing SQL: an agent or you.",
    code:
      "docker compose up -d\nagentdb\n\n# from source:\nuv sync\nuv run python -m adapters.tui",
    expected:
      "You see the Interdict launcher with Agent Mode and Human Mode choices. The default local DSN is localhost:5433/pagila.",
    note:
      "First Docker start seeds Pagila and large benchmark tables; later starts reuse the volume.",
  },
  {
    label: "Step 2",
    title: "Use Human Mode",
    copy:
      "Type SQL at the prompt. Reads and scoped writes run. Risky writes show a blast-radius confirmation panel before they touch the database.",
    code:
      "agentdb ▸ SELECT count(*) FROM clients;\nagentdb ▸ UPDATE users SET plan = 'free';\nagentdb ▸ \\override\nagentdb ▸ \\undo\nagentdb ▸ \\stats",
    expected:
      "Unscoped writes are blocked with a reason and fix. Confirmed writes print an undo id. Stats show blocked, held, reverted, and largest blast radius.",
    note:
      "Because the human is the author, override is available; it is confirmed, audited, and still undoable when the shape supports it.",
  },
  {
    label: "Step 3",
    title: "Use Agent Mode",
    copy:
      "Point Claude Code or Codex at Interdict's MCP server. The agent calls run_query instead of receiving raw database credentials.",
    code:
      "claude mcp add interdict \\\n  --env AGENT_DB_DSN=postgresql://postgres:postgres@localhost:5433/pagila \\\n  --env AGENT_OPERATOR_TOKEN=choose-a-secret \\\n  -- agentdb-mcp\n\ncodex mcp add interdict \\\n  --env AGENT_DB_DSN=postgresql://postgres:postgres@localhost:5433/pagila \\\n  --env AGENT_OPERATOR_TOKEN=choose-a-secret \\\n  -- agentdb-mcp",
    expected:
      "The agent can call run_query. Held writes require approve_query with an operator token the model never sees.",
    note:
      "If Codex cannot find agentdb-mcp, use the absolute path from which agentdb-mcp in your MCP config.",
  },
  {
    label: "Step 4",
    title: "Verify before trusting changes",
    copy:
      "Run tests and the latency gate before changing policy behavior or the request path. The value of this product depends on both correctness and negligible overhead.",
    code:
      "uv run pytest\nuv run ruff check .\nuv run black --check .\nuv run python -m benchmarks.ci_latency_gate",
    expected:
      "The test suite passes and the benchmark gate remains under the committed p99 latency budget.",
    note:
      "The benchmark is local and hardware-sensitive, but it is the guardrail for the ~0 ms overhead claim.",
  },
];

const storedTheme = window.localStorage.getItem("interdict-theme");
if (storedTheme) {
  root.dataset.theme = storedTheme;
}

function openSite() {
  gate.classList.add("is-open");
  site.classList.add("is-visible");
  site.setAttribute("aria-hidden", "false");

  if (!window.location.hash || window.location.hash === "#gate") {
    window.history.replaceState(null, "", "#overview");
  }
}

function updateThemeButton() {
  const isLight = root.dataset.theme === "light";
  themeToggle.setAttribute(
    "aria-label",
    isLight ? "Switch to dark mode" : "Switch to light mode",
  );
}

function toggleTheme() {
  const nextTheme = root.dataset.theme === "light" ? "dark" : "light";
  root.dataset.theme = nextTheme;
  window.localStorage.setItem("interdict-theme", nextTheme);
  updateThemeButton();
}

function updateBlossomDrift() {
  root.style.setProperty("--scroll-y", String(window.scrollY));
}

function showRunStep(index) {
  const step = runSteps[index];

  runTabs.forEach((tab, tabIndex) => {
    tab.classList.toggle("is-active", tabIndex === index);
  });

  runStepLabel.textContent = step.label;
  runStepTitle.textContent = step.title;
  runStepCopy.textContent = step.copy;
  runStepCode.textContent = step.code;
  runStepExpected.textContent = step.expected;
  runStepNote.textContent = step.note;
  copyCommand.textContent = "Copy command";
}

async function copyRunCommand() {
  try {
    await navigator.clipboard.writeText(runStepCode.textContent);
    copyCommand.textContent = "Copied";
  } catch {
    copyCommand.textContent = "Select manually";
  }
}

openControls.forEach((control) => {
  control.addEventListener("click", openSite);
});

themeToggle.addEventListener("click", toggleTheme);
window.addEventListener("scroll", updateBlossomDrift, { passive: true });

runTabs.forEach((tab, index) => {
  tab.addEventListener("click", () => showRunStep(index));
});

copyCommand.addEventListener("click", copyRunCommand);

if (window.location.hash && window.location.hash !== "#gate") {
  openSite();
}

updateThemeButton();
updateBlossomDrift();
showRunStep(0);
