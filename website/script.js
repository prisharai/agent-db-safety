const gate = document.querySelector("#gate");
const site = document.querySelector("#site");
const enterButton = document.querySelector(".enter-button");
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
const lagItems = Array.from(document.querySelectorAll(".scroll-lag"));

const runSteps = [
  {
    label: "Step 0",
    title: "Install Interdict",
    copy:
      "Install the MCP server once. If you are evaluating from this repo, install from source. If you want the app and Postgres together, use the Docker profile.",
    code:
      "pip install interdict-db\n# from source:\npip install .\n\n# Docker alternative:\ndocker compose --profile app run --rm app",
    expected:
      "The interdict command is available for MCP, with agentdb kept as the optional local shell. The Docker path opens the launcher after the database becomes healthy.",
    note:
      "Use interdict for agent integrations. agentdb remains the terminal launcher.",
  },
  {
    label: "Step 1",
    title: "Start the dev database",
    copy:
      "Start the seeded Postgres fixture. Interdict can also point at any Postgres database through AGENT_DB_DSN.",
    code:
      "docker compose up -d\n\n# default DSN:\npostgresql://postgres:postgres@localhost:5433/pagila",
    expected:
      "Postgres is healthy on localhost:5433 with Pagila and large benchmark tables loaded.",
    note:
      "First Docker start seeds Pagila and large benchmark tables; later starts reuse the volume.",
  },
  {
    label: "Step 2",
    title: "Put Interdict between the agent and database",
    copy:
      "Register the MCP server. The agent calls run_query instead of receiving direct database access.",
    code:
      "codex mcp add interdict \\\n  --env AGENT_DB_DSN=postgresql://postgres:postgres@localhost:5433/pagila \\\n  --env AGENT_OPERATOR_TOKEN=$(python -c 'import secrets; print(secrets.token_urlsafe(32))') \\\n  -- interdict",
    expected:
      "The agent can call run_query. Held writes require approve_query with an operator token the model never sees.",
    note:
      "Use the same command shape for Claude Code; the important part is that the model talks to Interdict, not Postgres.",
  },
  {
    label: "Step 3",
    title: "Optional local SQL shell",
    copy:
      "The product is agent-first, but the same safety engine is available as a local SQL shell for manual testing.",
    code:
      "agentdb\n\nagentdb ▸ SELECT count(*) FROM clients;\nagentdb ▸ \\stats\nagentdb ▸ \\undo",
    expected:
      "You get the same parse, simulate, block, confirm, audit, and undo behavior in a terminal.",
    note:
      "Human mode exists for evaluation and manual use; the main integration path is the MCP server.",
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
  themeToggle.setAttribute("aria-checked", isLight ? "false" : "true");
}

function toggleTheme() {
  const nextTheme = root.dataset.theme === "light" ? "dark" : "light";
  root.dataset.theme = nextTheme;
  window.localStorage.setItem("interdict-theme", nextTheme);
  updateThemeButton();
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

function setupLagScroll() {
  if (!lagItems.length || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    return;
  }

  root.classList.add("is-lagging");
  let targetY = window.scrollY;
  let currentY = targetY;
  let rafId = null;

  function tick() {
    currentY += (targetY - currentY) * 0.085;
    lagItems.forEach((item) => {
      const factor = Number.parseFloat(item.style.getPropertyValue("--lag")) || 0.05;
      item.style.setProperty("--lag-y", `${(targetY - currentY) * factor}px`);
    });

    if (Math.abs(targetY - currentY) > 0.15) {
      rafId = window.requestAnimationFrame(tick);
    } else {
      currentY = targetY;
      rafId = null;
    }
  }

  window.addEventListener(
    "scroll",
    () => {
      targetY = window.scrollY;
      if (rafId === null) {
        rafId = window.requestAnimationFrame(tick);
      }
    },
    { passive: true }
  );
}

async function copyRunCommand() {
  try {
    await navigator.clipboard.writeText(runStepCode.textContent);
    copyCommand.textContent = "Copied";
  } catch {
    copyCommand.textContent = "Select manually";
  }
}

enterButton.addEventListener("click", openSite);
themeToggle.addEventListener("click", toggleTheme);

runTabs.forEach((tab, index) => {
  tab.addEventListener("click", () => showRunStep(index));
});

copyCommand.addEventListener("click", copyRunCommand);

if (window.location.hash && window.location.hash !== "#gate") {
  openSite();
}

updateThemeButton();
showRunStep(0);
setupLagScroll();
