const progress = document.querySelector(".scroll-progress");
const revealItems = document.querySelectorAll(".reveal");
const demoButtons = document.querySelectorAll(".demo-step");
const demoCode = document.querySelector("#demo-code");
const demoVerdict = document.querySelector("#demo-verdict");
const demoTimestamp = document.querySelector("#demo-timestamp");
const demoProgress = document.querySelector(".video-progress-bar");

const demoFrames = [
  {
    label: "Block",
    time: "00:03",
    code: "UPDATE users SET plan = 'free';",
    verdict: "Blocked: missing WHERE",
    progress: "25%",
  },
  {
    label: "Simulate",
    time: "00:08",
    code: "DELETE FROM accounts WHERE balance < 2000;",
    verdict: "Held: 19 rows affected",
    progress: "50%",
  },
  {
    label: "Approve",
    time: "00:13",
    code: "approve_query(approval_id, operator_token)",
    verdict: "Operator token accepted",
    progress: "75%",
  },
  {
    label: "Undo",
    time: "00:18",
    code: "revert_write(undo_action_id)",
    verdict: "Restored: 1 row",
    progress: "100%",
  },
];

let demoIndex = 0;
let demoTimer;

function updateScrollProgress() {
  const scrollable = document.documentElement.scrollHeight - window.innerHeight;
  const pct = scrollable > 0 ? (window.scrollY / scrollable) * 100 : 0;
  progress.style.width = `${pct}%`;
}

function showDemoFrame(index) {
  demoIndex = index;
  const frame = demoFrames[index];
  demoButtons.forEach((button, buttonIndex) => {
    button.classList.toggle("is-active", buttonIndex === index);
  });
  demoCode.textContent = frame.code;
  demoVerdict.textContent = frame.verdict;
  demoTimestamp.textContent = frame.time;
  demoProgress.style.width = frame.progress;
}

function startDemoLoop() {
  window.clearInterval(demoTimer);
  demoTimer = window.setInterval(() => {
    showDemoFrame((demoIndex + 1) % demoFrames.length);
  }, 3000);
}

const observer = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      }
    });
  },
  { threshold: 0.18 },
);

revealItems.forEach((item) => observer.observe(item));

demoButtons.forEach((button, index) => {
  button.addEventListener("click", () => {
    showDemoFrame(index);
    startDemoLoop();
  });
});

window.addEventListener("scroll", updateScrollProgress, { passive: true });
window.addEventListener("resize", updateScrollProgress);

updateScrollProgress();
showDemoFrame(0);
startDemoLoop();
