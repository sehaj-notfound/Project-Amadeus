import { KurisuStage } from "./avatar_stage.js";
import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import { marked } from "marked";
import markedKatex from "marked-katex-extension";
import "katex/dist/katex.min.css";

// Configure marked with KaTeX equations & GitHub Flavored Markdown
marked.use(
  markedKatex({
    throwOnError: false,
    displayMode: false,
    nonStandard: true,
  })
);
marked.setOptions({
  gfm: true,
  breaks: true,
});

// --- State Variables ---
let stage = null;
let ws = null;
let activeModel = "gemini-3.8-flash-low";
let currentActiveConvoId = null;
let audioEnabled = true;
let isVoiceMuted = false;
let activeSpeechEmotion = "neutral";
let isRecording = false;
let stagedAttachments = [];
const localImageDataCache = new Map();
const pendingImageRequests = new Set();

let currentAmadeusCard = null;
let currentTurnTimelineFlow = null;
let currentActiveSpeechSegment = null;
let currentActiveSpeechBuffer = "";
let currentOperationsFlow = null; // backward compatibility alias
let currentAmadeusMsgBody = null; // backward compatibility alias
let currentActiveThinkingPill = null;
let currentThinkingDots = null;
let thinkingInterval = null;
let currentRawResponse = "";
let currentThoughtText = "";
let availableModelsCache = [];

// Detailed Telemetry Inspector Modal & Session Caches
let activeDetailedTab = "diffs";
let sessionDiffsCache = [];
let sessionCommandsCache = [];
let sessionTasksCache = [];
let sessionSubagentsCache = [];
let selectedDiffFile = null;
let selectedCommandIndex = null;
let selectedTaskId = null;
let selectedSubagentId = null;

// File Mention Autocomplete State
let currentWorkspaceFiles = [];
let activeMentionQuery = null;
let selectedMentionIndex = 0;

// --- SVG Icons ---
const COPY_ICON_SVG = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>`;
const CHECK_ICON_SVG = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>`;
const EDIT_ICON_SVG = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"></path><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path></svg>`;

// --- DOM References ---
const workstationLayout = document.getElementById("workstationLayout");
const sidebarCol = document.getElementById("sidebarCol");
const collapseSidebarBtn = document.getElementById("collapseSidebarBtn");
const expandSidebarBtn = document.getElementById("expandSidebarBtn");

const sessionHeading = document.getElementById("sessionHeading");
const modelBtn = document.getElementById("modelBtn");
const activeModelLabel = document.getElementById("activeModelLabel");
const modelMenu = document.getElementById("modelMenu");
const modelList = document.getElementById("modelList");

const chatFeed = document.getElementById("chatFeed");
const chatInput = document.getElementById("chatInput");
const sendBtn = document.getElementById("sendBtn");
const micBtn = document.getElementById("micBtn");
const newChatBtn = document.getElementById("newChatBtn");
const historyList = document.getElementById("historyList");

// File Mention Autocomplete Elements
const fileMentionPopup = document.getElementById("fileMentionPopup");
const fileMentionList = document.getElementById("fileMentionList");
const fileMentionCount = document.getElementById("fileMentionCount");
const modalMentionPopup = document.getElementById("modalMentionPopup");
const modalMentionList = document.getElementById("modalMentionList");
const modalMentionCount = document.getElementById("modalMentionCount");
const amadeusModalTextarea = document.getElementById("amadeusModalTextarea");
const amadeusModalHintText = document.getElementById("amadeusModalHintText");

const avatarMuteBtn = document.getElementById("avatarMuteBtn");
const emotionBadge = document.getElementById("emotionBadge");
const mouthBadge = document.getElementById("mouthBadge");
const stageWrapper = document.getElementById("stageWrapper");

// Detailed Modal Elements
const openInspectorBtn = document.getElementById("openInspectorBtn");
const detailedModalOverlay = document.getElementById("detailedModalOverlay");
const detailedModalCloseBtn = document.getElementById("detailedModalCloseBtn");
const tabBtnDiffs = document.getElementById("tabBtnDiffs");
const tabBtnTerminal = document.getElementById("tabBtnTerminal");
const tabBtnTasks = document.getElementById("tabBtnTasks");
const tabBtnSubagents = document.getElementById("tabBtnSubagents");
const tabBtnUsage = document.getElementById("tabBtnUsage");
const diffsCountBadge = document.getElementById("diffsCountBadge");
const commandsCountBadge = document.getElementById("commandsCountBadge");
const tasksCountBadge = document.getElementById("tasksCountBadge");
const subagentsCountBadge = document.getElementById("subagentsCountBadge");

const paneDiffs = document.getElementById("paneDiffs");
const paneTerminal = document.getElementById("paneTerminal");
const paneTasks = document.getElementById("paneTasks");
const paneSubagents = document.getElementById("paneSubagents");
const paneUsage = document.getElementById("paneUsage");
const usageCardsGrid = document.getElementById("usageCardsGrid");
const refreshQuotaBtn = document.getElementById("refreshQuotaBtn");

const diffFileListItems = document.getElementById("diffFileListItems");
const diffCurrentFile = document.getElementById("diffCurrentFile");
const diffCurrentStats = document.getElementById("diffCurrentStats");
const diffCodeWrapper = document.getElementById("diffCodeWrapper");

const commandListItems = document.getElementById("commandListItems");
const consoleCmdTitle = document.getElementById("consoleCmdTitle");
const consoleExitBadge = document.getElementById("consoleExitBadge");
const copyConsoleBtn = document.getElementById("copyConsoleBtn");
const consoleOutputBox = document.getElementById("consoleOutputBox");

const taskListItems = document.getElementById("taskListItems");
const taskDetailId = document.getElementById("taskDetailId");
const taskDetailState = document.getElementById("taskDetailState");
const taskPromptBox = document.getElementById("taskPromptBox");
const taskLogBox = document.getElementById("taskLogBox");
const refreshTaskLogBtn = document.getElementById("refreshTaskLogBtn");

const subagentListItems = document.getElementById("subagentListItems");
const subagentMetaTitle = document.getElementById("subagentMetaTitle");
const subagentMetaBadge = document.getElementById("subagentMetaBadge");
const subagentPromptBox = document.getElementById("subagentPromptBox");
const subagentChatFeed = document.getElementById("subagentChatFeed");

const revealInExplorerBtn = document.getElementById("revealInExplorerBtn");
const killTaskBtn = document.getElementById("killTaskBtn");
const killSubagentBtn = document.getElementById("killSubagentBtn");

// Chat Archive Search DOM References
const searchChatsBtn = document.getElementById("searchChatsBtn");
const chatSearchModalOverlay = document.getElementById("chatSearchModalOverlay");
const chatSearchCloseBtn = document.getElementById("chatSearchCloseBtn");
const chatSearchInput = document.getElementById("chatSearchInput");
const clearSearchBtn = document.getElementById("clearSearchBtn");
const searchStatusText = document.getElementById("searchStatusText");
const searchCountBadge = document.getElementById("searchCountBadge");
const searchResultsList = document.getElementById("searchResultsList");

let pendingSearchScrollStep = null;
let searchDebounceTimer = null;
let lastSearchQuery = "";

// ════════ STARTUP OVERLAY & WORKSPACE ONBOARDING ════════
const startupOverlay = document.getElementById("startupOverlay");
const startupAudio = document.getElementById("startupAudio");
const startupAudioToggleBtn = document.getElementById("startupAudioToggleBtn");

const startupStageLoading = document.getElementById("startupStageLoading");
const startupLoadingStatusText = document.getElementById("startupLoadingStatusText");
const startupProgressBarFill = document.getElementById("startupProgressBarFill");
const startupProgressPercent = document.getElementById("startupProgressPercent");
const startupStageLoadingError = document.getElementById("startupStageLoadingError");
const startupLoadingErrorText = document.getElementById("startupLoadingErrorText");
const startupRetryLoadingBtn = document.getElementById("startupRetryLoadingBtn");

const startupStageWorkspace = document.getElementById("startupStageWorkspace");
const btnDefaultWorkspace = document.getElementById("btnDefaultWorkspace");
const btnCustomWorkspace = document.getElementById("btnCustomWorkspace");

const startupStageTrust = document.getElementById("startupStageTrust");
const startupTrustFolderPath = document.getElementById("startupTrustFolderPath");
const startupTrustYesBtn = document.getElementById("startupTrustYesBtn");
const startupTrustNoBtn = document.getElementById("startupTrustNoBtn");

const startupStageGeminiConflict = document.getElementById("startupStageGeminiConflict");
const startupGeminiFolderPath = document.getElementById("startupGeminiFolderPath");
const startupGeminiDismissBtn = document.getElementById("startupGeminiDismissBtn");
const startupGeminiCancelBtn = document.getElementById("startupGeminiCancelBtn");

const startupStageLaunching = document.getElementById("startupStageLaunching");
const startupLaunchingSpinnerBox = document.getElementById("startupLaunchingSpinnerBox");
const startupLaunchingDesc = document.getElementById("startupLaunchingDesc");
const startupStageWorkspaceError = document.getElementById("startupStageWorkspaceError");
const startupWorkspaceErrorText = document.getElementById("startupWorkspaceErrorText");
const startupWorkspaceRetryBtn = document.getElementById("startupWorkspaceRetryBtn");
const startupWorkspaceChangeBtn = document.getElementById("startupWorkspaceChangeBtn");

const quoteDivergenceMeter = document.getElementById("quoteDivergenceMeter");
const quoteFader = document.getElementById("quoteFader");
const quoteText = document.getElementById("quoteText");
const quoteAuthor = document.getElementById("quoteAuthor");

let pendingSelectedWorkspaceFolder = null;
let pendingIsDefaultWorkspace = false;
let startupAudioPlaying = false;
let quoteTickerInterval = null;

let activeWorkspaceCwd = null;
let activeWorkspaceIsDefault = true;

const STEINS_GATE_QUOTES = [
  {
    quote: "The universe has a beginning, but no end. — Infinity. Stars, too, have a beginning, but by their own power they perish. — Finite. History's greatest lesson is that those who possess wisdom are the most foolish of all. This is the final warning from God to those who would resist.",
    author: "Makise Kurisu, Epigraph",
    meter: "[1.048596%]"
  },
  {
    quote: "Time passes so quickly. Day to day, they feel so long, but the years fly by in the blink of an eye. If only time could be stopped... Time passes both fast and slow depending on perception. Relativity theory is so romantic... yet so heartbreaking.",
    author: "Makise Kurisu, Theory of Relativity",
    meter: "[1.048596%]"
  },
  {
    quote: "There is no beginning to the end. Neither is there an end to the beginning. The world is trapped in an endless spiral of causality, repeating in an eternal loop of despair. Yet even within a world where grief is absolute, time marches forward toward an unwritten destiny.",
    author: "Steins;Gate 0, Prologue",
    meter: "[1.048596%]"
  },
  {
    quote: "No one knows what the future holds. That is why its potential is infinite, just as this meeting would imply.",
    author: "Rintaro Okabe, 1.048596%",
    meter: "[1.048596%]"
  }
];

let currentQuoteIndex = Math.floor(Math.random() * STEINS_GATE_QUOTES.length);


let isGenerating = false;

function setGeneratingState(generating) {
  isGenerating = Boolean(generating);
  if (isGenerating) {
    sendBtn.classList.add("generating");
    sendBtn.title = "Halt generation (Click to stop)";
    sendBtn.innerHTML = '<span class="stop-icon">■</span>';
  } else {
    sendBtn.classList.remove("generating");
    sendBtn.title = "Send (Enter)";
    sendBtn.innerHTML = `
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <line x1="22" y1="2" x2="11" y2="13"></line>
        <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
      </svg>
    `;
  }
}

// ════════ IN-APP FUTURE GADGET LAB MODALS ════════
function showCustomModal({ title, message, hasInput = false, defaultValue = "", isDanger = false, isMultiline = false }) {
  return new Promise((resolve) => {
    const overlay = document.getElementById("amadeusModalOverlay");
    const modalBox = overlay ? overlay.querySelector(".amadeus-modal-box") : null;
    const titleEl = document.getElementById("amadeusModalTitle");
    const msgEl = document.getElementById("amadeusModalMessage");
    const inputWrap = document.getElementById("amadeusModalInputWrap");
    const inputEl = document.getElementById("amadeusModalInput");
    const textareaEl = document.getElementById("amadeusModalTextarea");
    const hintEl = document.getElementById("amadeusModalHintText");
    const cancelBtn = document.getElementById("amadeusModalCancelBtn");
    const confirmBtn = document.getElementById("amadeusModalConfirmBtn");

    if (!overlay) {
      if (hasInput) return resolve(window.prompt(message, defaultValue));
      return resolve(window.confirm(message));
    }

    titleEl.textContent = title || "CONFIRM ACTION";
    msgEl.textContent = message || "";

    if (isDanger) {
      confirmBtn.classList.add("danger");
    } else {
      confirmBtn.classList.remove("danger");
    }

    if (modalBox) {
      modalBox.classList.toggle("modal-box-large", Boolean(isMultiline));
    }

    if (hintEl) {
      hintEl.classList.toggle("hidden", !isMultiline);
    }

    if (hasInput) {
      inputWrap.classList.remove("hidden");
      if (isMultiline && textareaEl) {
        if (inputEl) inputEl.classList.add("hidden");
        textareaEl.classList.remove("hidden");
        textareaEl.value = defaultValue || "";
      } else {
        if (textareaEl) textareaEl.classList.add("hidden");
        if (inputEl) {
          inputEl.classList.remove("hidden");
          inputEl.value = defaultValue || "";
        }
      }
    } else {
      inputWrap.classList.add("hidden");
      if (inputEl) inputEl.classList.add("hidden");
      if (textareaEl) textareaEl.classList.add("hidden");
    }

    overlay.classList.remove("hidden");
    if (hasInput) {
      setTimeout(() => {
        const activeTarget = (isMultiline && textareaEl) ? textareaEl : inputEl;
        if (activeTarget) {
          activeTarget.focus();
          activeTarget.select();
        }
      }, 50);
    } else {
      setTimeout(() => confirmBtn.focus(), 50);
    }

    // Modal textarea event handlers for '@' file mention
    function onTextareaInput() {
      updateMentionPopup(textareaEl);
    }
    function onTextareaKeyUp(e) {
      if (["ArrowLeft", "ArrowRight", "Home", "End", "Backspace", "Delete"].includes(e.key)) {
        updateMentionPopup(textareaEl);
      }
    }
    function onTextareaClick() {
      updateMentionPopup(textareaEl);
    }
    function onTextareaKeyDown(e) {
      if (modalMentionPopup && !modalMentionPopup.classList.contains("hidden")) {
        const items = modalMentionList.querySelectorAll(".file-mention-item");
        if (e.key === "ArrowDown") {
          e.preventDefault();
          if (items.length > 0) {
            selectedMentionIndex = (selectedMentionIndex + 1) % items.length;
            highlightMentionItem(items, selectedMentionIndex);
          }
          return;
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          if (items.length > 0) {
            selectedMentionIndex = (selectedMentionIndex - 1 + items.length) % items.length;
            highlightMentionItem(items, selectedMentionIndex);
          }
          return;
        } else if (e.key === "Enter" || e.key === "Tab") {
          if (items.length > 0 && selectedMentionIndex >= 0 && selectedMentionIndex < items.length) {
            e.preventDefault();
            const targetFile = items[selectedMentionIndex].getAttribute("data-file");
            selectMentionFile(targetFile, textareaEl, modalMentionPopup);
            return;
          }
        } else if (e.key === "Escape") {
          e.preventDefault();
          hideMentionPopup(modalMentionPopup);
          return;
        }
      }

      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        onConfirm();
      }
    }

    if (isMultiline && textareaEl) {
      textareaEl.addEventListener("input", onTextareaInput);
      textareaEl.addEventListener("keyup", onTextareaKeyUp);
      textareaEl.addEventListener("click", onTextareaClick);
      textareaEl.addEventListener("keydown", onTextareaKeyDown);
    }

    function cleanup() {
      overlay.classList.add("hidden");
      if (modalBox) modalBox.classList.remove("modal-box-large");
      if (hintEl) hintEl.classList.add("hidden");
      hideMentionPopup(modalMentionPopup);

      if (isMultiline && textareaEl) {
        textareaEl.removeEventListener("input", onTextareaInput);
        textareaEl.removeEventListener("keyup", onTextareaKeyUp);
        textareaEl.removeEventListener("click", onTextareaClick);
        textareaEl.removeEventListener("keydown", onTextareaKeyDown);
      }
      cancelBtn.removeEventListener("click", onCancel);
      confirmBtn.removeEventListener("click", onConfirm);
      window.removeEventListener("keydown", onKeyDown);
    }

    function onCancel() {
      cleanup();
      resolve(hasInput ? null : false);
    }

    function onConfirm() {
      const resultVal = isMultiline && textareaEl ? textareaEl.value : (inputEl ? inputEl.value : "");
      cleanup();
      resolve(hasInput ? resultVal : true);
    }

    function onKeyDown(e) {
      if (modalMentionPopup && !modalMentionPopup.classList.contains("hidden")) {
        return; // Handled by onTextareaKeyDown
      }
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      } else if (e.key === "Enter" && !isMultiline) {
        e.preventDefault();
        onConfirm();
      }
    }

    cancelBtn.addEventListener("click", onCancel);
    confirmBtn.addEventListener("click", onConfirm);
    window.addEventListener("keydown", onKeyDown);
  });
}

function showCustomPrompt(title, message, defaultValue = "", isMultiline = false) {
  return showCustomModal({ title, message, hasInput: true, defaultValue, isDanger: false, isMultiline });
}

function showCustomConfirm(title, message, isDanger = false) {
  return showCustomModal({ title, message, hasInput: false, isDanger });
}

// ════════ STARTUP SEQUENCE & WORKSPACE ONBOARDING ════════
function initQuotesTicker() {
  if (!quoteText || !quoteFader) return;
  const initial = STEINS_GATE_QUOTES[currentQuoteIndex];
  if (quoteDivergenceMeter) quoteDivergenceMeter.textContent = initial.meter || "[1.048596%]";
  if (quoteAuthor) quoteAuthor.textContent = initial.author;
  quoteText.textContent = `“${initial.quote}”`;

  if (quoteTickerInterval) clearInterval(quoteTickerInterval);
  quoteTickerInterval = setInterval(() => {
    quoteFader.classList.add("fade-out");
    setTimeout(() => {
      currentQuoteIndex = (currentQuoteIndex + 1) % STEINS_GATE_QUOTES.length;
      const q = STEINS_GATE_QUOTES[currentQuoteIndex];
      if (quoteDivergenceMeter) quoteDivergenceMeter.textContent = q.meter || "[1.048596%]";
      if (quoteAuthor) quoteAuthor.textContent = q.author;
      quoteText.textContent = `“${q.quote}”`;
      quoteFader.classList.remove("fade-out");
    }, 800);
  }, 15000);
}

function getPathBasename(pathStr) {
  if (!pathStr) return "";
  const cleaned = pathStr.replace(/[/\\]+$/, "");
  const parts = cleaned.split(/[/\\]/);
  return parts[parts.length - 1] || pathStr;
}

function updateWorkspaceButtonLabel(cwd, isDefault) {
  activeWorkspaceCwd = cwd || null;
  activeWorkspaceIsDefault = Boolean(isDefault);

  const workspaceBtn = document.getElementById("workspaceBtn");
  const workspaceLabel = document.getElementById("workspaceLabel");

  let labelText = "Workspace; default workspace";
  let titleText = "Current Workspace Directory: Default Workspace";

  if (!activeWorkspaceIsDefault && activeWorkspaceCwd) {
    const folderName = getPathBasename(activeWorkspaceCwd);
    labelText = `Workspace; ${folderName}`;
    titleText = activeWorkspaceCwd;
  } else if (activeWorkspaceCwd) {
    titleText = activeWorkspaceCwd;
  }

  if (workspaceLabel) {
    workspaceLabel.textContent = labelText;
  }
  if (workspaceBtn) {
    workspaceBtn.title = titleText;
  }
}

async function initStartupAudio() {
  if (!startupAudio) return;
  try {
    const bytes = await invoke("load_startup_audio");
    if (!bytes || bytes.length === 0) {
      console.log("[Audio] No external audio.mp3 found, running in silent mode.");
      startupAudioPlaying = false;
      if (startupAudioToggleBtn) startupAudioToggleBtn.style.display = "none";
      return;
    }
    const blob = new Blob([new Uint8Array(bytes)], { type: "audio/mp3" });
    startupAudio.src = URL.createObjectURL(blob);
    startupAudio.volume = 0.35;
    const playPromise = startupAudio.play();
    if (playPromise !== undefined) {
      playPromise.then(() => {
        startupAudioPlaying = true;
        if (startupAudioToggleBtn) startupAudioToggleBtn.textContent = "🔊 Music: ON";
      }).catch((err) => {
        console.log("[Audio] Autoplay blocked, waiting for user gesture:", err);
        startupAudioPlaying = false;
        if (startupAudioToggleBtn) startupAudioToggleBtn.textContent = "🔈 Music: OFF (Click to Play)";

        const enableAudioOnGesture = () => {
          if (!startupAudioPlaying && startupAudio && startupOverlay && !startupOverlay.classList.contains("fade-out")) {
            startupAudio.play().then(() => {
              startupAudioPlaying = true;
              if (startupAudioToggleBtn) startupAudioToggleBtn.textContent = "🔊 Music: ON";
            }).catch(() => {});
          }
          window.removeEventListener("pointerdown", enableAudioOnGesture);
          window.removeEventListener("keydown", enableAudioOnGesture);
        };
        window.addEventListener("pointerdown", enableAudioOnGesture, { once: true });
        window.addEventListener("keydown", enableAudioOnGesture, { once: true });
      });
    }
  } catch (err) {
    console.log("[Audio] External startup audio skipped:", err);
    if (startupAudioToggleBtn) startupAudioToggleBtn.style.display = "none";
  }
}

function fadeOutStartupAudio(durationMs = 1000) {
  if (!startupAudio || startupAudio.paused) return;
  const stepTime = 50;
  const steps = durationMs / stepTime;
  const volumeStep = startupAudio.volume / steps;

  const fadeInterval = setInterval(() => {
    if (startupAudio.volume > volumeStep) {
      startupAudio.volume = Math.max(0, startupAudio.volume - volumeStep);
    } else {
      startupAudio.volume = 0;
      startupAudio.pause();
      startupAudioPlaying = false;
      clearInterval(fadeInterval);
    }
  }, stepTime);
}

function dismissStartupOverlay(immediate = false) {
  if (!startupOverlay) return;
  if (quoteTickerInterval) {
    clearInterval(quoteTickerInterval);
    quoteTickerInterval = null;
  }
  fadeOutStartupAudio(1000);

  if (immediate) {
    startupOverlay.classList.add("fade-out");
    startupOverlay.style.display = "none";
  } else {
    startupOverlay.classList.add("fade-out");
    setTimeout(() => {
      startupOverlay.style.display = "none";
    }, 1000);
  }
}

function showStartupStage(stageName) {
  const stages = [
    { name: "loading", el: startupStageLoading },
    { name: "workspace", el: startupStageWorkspace },
    { name: "trust", el: startupStageTrust },
    { name: "gemini", el: startupStageGeminiConflict },
    { name: "launching", el: startupStageLaunching }
  ];

  stages.forEach((s) => {
    if (s.el) {
      if (s.name === stageName) {
        s.el.classList.remove("hidden");
      } else {
        s.el.classList.add("hidden");
      }
    }
  });

  if (stageName === "launching") {
    if (startupLaunchingSpinnerBox) startupLaunchingSpinnerBox.classList.remove("hidden");
    if (startupStageWorkspaceError) startupStageWorkspaceError.classList.add("hidden");
  }
}

function openCustomFolderPicker() {
  sendWs({ type: "pick_folder" });
}

function handleSelectedWorkspaceFolder(folderPath) {
  if (!folderPath) return;
  pendingSelectedWorkspaceFolder = folderPath;
  pendingIsDefaultWorkspace = false;
  sendWs({ type: "check_folder_trust", folder: folderPath });
}

function setupStartupControls() {
  if (startupRetryLoadingBtn) {
    startupRetryLoadingBtn.addEventListener("click", () => {
      if (startupStageLoadingError) startupStageLoadingError.classList.add("hidden");
      if (startupLoadingStatusText) startupLoadingStatusText.textContent = "Retrying subsystem verification...";
      sendWs({ type: "retry_loading" });
    });
  }

  if (btnDefaultWorkspace) {
    btnDefaultWorkspace.addEventListener("click", () => {
      pendingIsDefaultWorkspace = true;
      pendingSelectedWorkspaceFolder = null;
      showStartupStage("launching");
      if (startupLaunchingDesc) startupLaunchingDesc.textContent = "Connecting to headless agy CLI runner at default workspace...";
      sendWs({ type: "start_workspace", is_default: true });
    });
  }

  if (btnCustomWorkspace) {
    btnCustomWorkspace.addEventListener("click", () => {
      openCustomFolderPicker();
    });
    const innerBtn = btnCustomWorkspace.querySelector("button");
    if (innerBtn) {
      innerBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        openCustomFolderPicker();
      });
    }
  }

  if (startupTrustYesBtn) {
    startupTrustYesBtn.addEventListener("click", () => {
      sendWs({ type: "trust_folder", folder: pendingSelectedWorkspaceFolder });
      sendWs({ type: "check_gemini_conflict", folder: pendingSelectedWorkspaceFolder });
    });
  }

  if (startupTrustNoBtn) {
    startupTrustNoBtn.addEventListener("click", () => {
      showStartupStage("workspace");
    });
  }

  if (startupGeminiDismissBtn) {
    startupGeminiDismissBtn.addEventListener("click", () => {
      showStartupStage("launching");
      if (startupLaunchingDesc) startupLaunchingDesc.textContent = `Connecting to headless agy CLI runner at ${pendingSelectedWorkspaceFolder}...`;
      sendWs({ type: "start_workspace", workspace_path: pendingSelectedWorkspaceFolder, is_default: false });
    });
  }

  if (startupGeminiCancelBtn) {
    startupGeminiCancelBtn.addEventListener("click", () => {
      showStartupStage("workspace");
    });
  }

  if (startupWorkspaceRetryBtn) {
    startupWorkspaceRetryBtn.addEventListener("click", () => {
      if (startupStageWorkspaceError) startupStageWorkspaceError.classList.add("hidden");
      if (startupLaunchingSpinnerBox) startupLaunchingSpinnerBox.classList.remove("hidden");
      if (pendingIsDefaultWorkspace) {
        if (startupLaunchingDesc) startupLaunchingDesc.textContent = "Connecting to headless agy CLI runner at default workspace...";
        sendWs({ type: "start_workspace", is_default: true });
      } else {
        if (startupLaunchingDesc) startupLaunchingDesc.textContent = `Connecting to headless agy CLI runner at ${pendingSelectedWorkspaceFolder}...`;
        sendWs({ type: "start_workspace", workspace_path: pendingSelectedWorkspaceFolder, is_default: false });
      }
    });
  }

  if (startupWorkspaceChangeBtn) {
    startupWorkspaceChangeBtn.addEventListener("click", () => {
      showStartupStage("workspace");
    });
  }

  if (startupAudioToggleBtn) {
    startupAudioToggleBtn.addEventListener("click", () => {
      if (!startupAudio || !startupAudio.src) return;
      if (startupAudio.paused) {
        startupAudio.play().then(() => {
          startupAudioPlaying = true;
          startupAudioToggleBtn.textContent = "🔊 Music: ON";
        }).catch(() => {});
      } else {
        startupAudio.pause();
        startupAudioPlaying = false;
        startupAudioToggleBtn.textContent = "🔈 Music: OFF";
      }
    });
  }
}

function renderTurnErrorCard(errorMessage) {
  setGeneratingState(false);
  stopThinkingDotsAnimation();

  if (currentActiveThinkingPill) {
    const content = currentActiveThinkingPill.querySelector(".thinking-content");
    if (!content || !content.textContent.trim()) {
      currentActiveThinkingPill.remove();
    }
    currentActiveThinkingPill = null;
  }

  currentAmadeusCard = null;
  currentTurnTimelineFlow = null;
  currentActiveSpeechSegment = null;
  currentActiveSpeechBuffer = "";
  currentOperationsFlow = null;
  currentAmadeusMsgBody = null;

  const card = document.createElement("div");
  card.className = "msg-card turn-error-card";
  card.innerHTML = `
    <div class="error-notice-body">
      <span class="error-notice-icon">⚠️</span>
      <div class="error-notice-content">
        <span class="error-notice-title">EXECUTION ERROR // TURN TERMINATED</span>
        <span class="error-notice-detail">${escapeHtml(errorMessage || "An unexpected error occurred during execution.")}</span>
      </div>
    </div>
  `;
  chatFeed.appendChild(card);
  scrollChatToBottom();
}

// --- Initialization ---
async function init() {
  stage = new KurisuStage(stageWrapper);

  stage.onStateChange = (state) => {
    if (emotionBadge) emotionBadge.textContent = `{${state.emotion}}`;
    if (mouthBadge) {
      const mouthDesc =
        state.mouth === "open"
          ? "Open (02)"
          : state.mouth === "half"
          ? "Half (01)"
          : "Closed (00)";
      mouthBadge.textContent = mouthDesc;
    }
  };

  await stage.init();

  // Bind UI Controls
  setupChatInputs();
  setupSidebarControls();
  setupAvatarControls();
  setupEmotionPalette();
  setupFocusGating();
  setupDetailedModal();
  setupChatSearchModal();
  setupLightbox();
  setupGlobalClickHandlers();
  setupStartupControls();

  // Startup Overlay & Audio Setup
  initStartupAudio();
  initQuotesTicker();

  // Initial Workspace button label
  updateWorkspaceButtonLabel(null, true);

  // Connect to Python Backend WebSocket Bridge
  connectWebSocket();
}

// ════════ WEBSOCKET CLIENT (ws://127.0.0.1:8765) ════════
function connectWebSocket() {
  const WS_URL = "ws://127.0.0.1:8765";
  try {
    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
      console.log("[WS] Connected to Amadeus Python Bridge");
      sendWs({ type: "request_loading_status" });
      sendWs({ type: "request_init" });
      sendWs({ type: "get_workspace_files" });
      sendWs({ type: "window_focus", focused: document.hasFocus() });
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleBackendPacket(data);
      } catch (err) {
        console.warn("[WS] Error parsing message:", err);
      }
    };

    ws.onclose = () => {
      setTimeout(connectWebSocket, 2500);
    };

    ws.onerror = () => {
      ws.close();
    };
  } catch (e) {
    setTimeout(connectWebSocket, 3000);
  }
}

function sendWs(payload) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(payload));
  }
}

// ════════ FOCUS GATING (Sentence-Boundary Language Gating) ════════
function setupFocusGating() {
  window.addEventListener("focus", () => {
    sendWs({ type: "window_focus", focused: true });
  });

  window.addEventListener("blur", () => {
    sendWs({ type: "window_focus", focused: false });
  });

  window.addEventListener("beforeunload", () => {
    sendWs({ type: "client_exit" });
  });

  try {
    import("@tauri-apps/api/window").then(({ getCurrentWindow }) => {
      const win = getCurrentWindow();
      win.onFocusChanged(({ payload: focused }) => {
        sendWs({ type: "window_focus", focused });
      });
    }).catch(() => {});
  } catch (e) {}
}

// ════════ BACKEND PACKET DISPATCHER ════════
function handleBackendPacket(data) {
  // 1. Initial State
  if (data.type === "init_state") {
    if (data.is_running === true) {
      dismissStartupOverlay(true);
    }
    if (data.cwd !== undefined) {
      updateWorkspaceButtonLabel(data.cwd, data.is_default);
    }
    if (data.models) renderModelOptions(data.models);
    if (data.model) updateActiveModel(data.model);
    if (data.active_conversation_id) currentActiveConvoId = data.active_conversation_id;
    if (data.conversations) renderHistoryList(data.conversations);
    if (typeof data.voice_enabled === "boolean") {
      updateVoiceStatus(data.voice_enabled);
    }
    if (data.workspace_files) {
      currentWorkspaceFiles = data.workspace_files;
    }
    if (data.history && data.history.length > 0) {
      loadConversationIntoChat(data.history, data.active_conversation_id, data.active_conversation_title);
    }
    if (data.avatar_config && stage) {
      stage.loadAvatarConfig(data.avatar_config);
    }
  }

  // 1b. Subsystem Loading Progress
  if (data.type === "loading_progress") {
    if (startupProgressBarFill) startupProgressBarFill.style.width = `${data.percent || 0}%`;
    if (startupProgressPercent) startupProgressPercent.textContent = `${data.percent || 0}%`;
    if (startupLoadingStatusText) startupLoadingStatusText.textContent = data.message || data.step || "Initializing subsystems...";
    if (startupStageLoadingError) startupStageLoadingError.classList.add("hidden");

    if (data.ready_for_workspace) {
      showStartupStage("workspace");
    }
  }

  // 1c. Subsystem Loading Error
  if (data.type === "loading_error") {
    if (startupStageLoadingError) startupStageLoadingError.classList.remove("hidden");
    if (startupLoadingErrorText) startupLoadingErrorText.textContent = data.error || "Subsystem initialization failed.";
  }

  // 1c2. Native Modern Folder Picker Response
  if (data.type === "folder_picked") {
    if (data.folder) {
      handleSelectedWorkspaceFolder(data.folder);
    }
  }

  // 1d. Workspace Folder Trust Status
  if (data.type === "trust_status") {
    if (data.folder) pendingSelectedWorkspaceFolder = data.folder;
    if (data.trusted) {
      sendWs({ type: "check_gemini_conflict", folder: data.folder || pendingSelectedWorkspaceFolder });
    } else {
      if (startupTrustFolderPath) startupTrustFolderPath.textContent = data.folder || pendingSelectedWorkspaceFolder;
      showStartupStage("trust");
    }
  }

  // 1e. Local GEMINI.md Conflict Status
  if (data.type === "gemini_status") {
    if (data.folder) pendingSelectedWorkspaceFolder = data.folder;
    if (data.has_conflict) {
      if (startupGeminiFolderPath) startupGeminiFolderPath.textContent = data.folder || pendingSelectedWorkspaceFolder;
      showStartupStage("gemini");
    } else {
      showStartupStage("launching");
      if (startupLaunchingDesc) startupLaunchingDesc.textContent = `Connecting to headless agy CLI runner at ${data.folder || pendingSelectedWorkspaceFolder}...`;
      sendWs({ type: "start_workspace", workspace_path: data.folder || pendingSelectedWorkspaceFolder, is_default: false });
    }
  }

  // 1f. Workspace Ready & Spawner Success
  if (data.type === "workspace_ready") {
    updateWorkspaceButtonLabel(data.cwd, pendingIsDefaultWorkspace);
    dismissStartupOverlay(false);
    sendWs({ type: "request_init" });
    sendWs({ type: "get_workspace_files" });
  }

  // 1g. Workspace Initialization Error
  if (data.type === "workspace_error") {
    if (startupLaunchingSpinnerBox) startupLaunchingSpinnerBox.classList.add("hidden");
    if (startupStageWorkspaceError) startupStageWorkspaceError.classList.remove("hidden");
    if (startupWorkspaceErrorText) startupWorkspaceErrorText.textContent = data.error || "Workspace initialization failed.";
  }

  // 1i. Workspace Closed (Switch Directory)
  if (data.type === "workspace_closed") {
    currentWorkspaceFiles = [];
    if (startupOverlay) {
      startupOverlay.style.display = "flex";
      startupOverlay.offsetHeight; // force reflow
      startupOverlay.classList.remove("fade-out");
      startupOverlay.style.opacity = "1";
      startupOverlay.style.pointerEvents = "all";
    }
    if (startupAudio && startupAudio.src) {
      startupAudio.currentTime = 0;
      startupAudio.volume = 0.35;
      const playPromise = startupAudio.play();
      if (playPromise !== undefined) {
        playPromise.then(() => {
          startupAudioPlaying = true;
          if (startupAudioToggleBtn) startupAudioToggleBtn.textContent = "🔊 Music: ON";
        }).catch((err) => {
          console.warn("[Audio] Could not restart audio:", err);
        });
      }
    }
    initQuotesTicker();
    showStartupStage("workspace");
  }

  // 1i2. Workspace Deleted or Moved Fallback
  if (data.type === "workspace_deleted") {
    currentWorkspaceFiles = [];
    if (startupOverlay) {
      startupOverlay.style.display = "flex";
      startupOverlay.offsetHeight; // force reflow
      startupOverlay.classList.remove("fade-out");
      startupOverlay.style.opacity = "1";
      startupOverlay.style.pointerEvents = "all";
    }
    if (startupAudio && startupAudio.src) {
      startupAudio.currentTime = 0;
      startupAudio.volume = 0.35;
      const playPromise = startupAudio.play();
      if (playPromise !== undefined) {
        playPromise.then(() => {
          startupAudioPlaying = true;
          if (startupAudioToggleBtn) startupAudioToggleBtn.textContent = "🔊 Music: ON";
        }).catch((err) => {
          console.warn("[Audio] Could not restart audio:", err);
        });
      }
    }
    initQuotesTicker();
    showStartupStage("workspace");
    renderTurnErrorCard("The active workspace directory was deleted or moved. Please select a workspace.");
  }

  // 1j. Workspace Files for '@' Mention Autocomplete
  if (data.type === "workspace_files") {
    currentWorkspaceFiles = data.files || [];
    if (activeMentionQuery !== null || (chatInput && /(?:^|\s)@([^\s]*)$/.test(chatInput.value.slice(0, chatInput.selectionStart)))) {
      updateMentionPopup(chatInput);
    }
    if (amadeusModalTextarea && !amadeusModalTextarea.classList.contains("hidden") && /(?:^|\s)@([^\s]*)$/.test(amadeusModalTextarea.value.slice(0, amadeusModalTextarea.selectionStart))) {
      updateMentionPopup(amadeusModalTextarea);
    }
  }

  // 1h. Turn Error Interception
  if (data.type === "turn_error") {
    renderTurnErrorCard(data.error || "A turn execution error occurred.");
  }

  // 2. Emotion Shift from Dual-Stream Demuxer
  if (data.type === "emotion" && data.emotion) {
    activeSpeechEmotion = data.emotion;
    if (!isVoiceMuted && stage) {
      stage.setEmotion(data.emotion);
      highlightEmotionChip(data.emotion);
    }
  }

  // 3. Audio RMS Viseme (Real-Time 44.1 kHz Mouth Flap)
  if (data.type === "viseme" && typeof data.rms === "number") {
    if (isVoiceMuted || !stage) {
      if (stage) stage.setAudioRms(0);
      if (mouthBadge) mouthBadge.textContent = "Closed (00)";
    } else {
      stage.setAudioRms(data.rms);
      if (mouthBadge) {
        if (data.rms >= 0.075) mouthBadge.textContent = "Open (02)";
        else if (data.rms >= 0.02) mouthBadge.textContent = "Flapping (RMS)";
        else mouthBadge.textContent = "Closed (00)";
      }
    }
  }

  // 4. Live Spoken Subtitle Stream (Subtitle strip removed)
  if (data.type === "subtitle" && data.text) {
    // Subtitle drawer removed in favor of clean visual stage
  }

  // 5. Speech End: cleanly close mouth
  if (data.type === "speech_end") {
    stage.setAudioRms(0);
    if (mouthBadge) mouthBadge.textContent = "Closed (00)";
  }

  // 6. User Message Echo
  if (data.type === "user_msg" && data.text) {
    appendUserMessage(data.text);
    // If the conversation is untitled, update topbar title
    if (sessionHeading && (sessionHeading.textContent === "New Chat" || !sessionHeading.textContent)) {
      sessionHeading.textContent = data.text.slice(0, 30) + (data.text.length > 30 ? "..." : "");
    }
  }

  // 7. Streaming Response Tokens & Thoughts
  if (data.type === "stream_start") {
    setGeneratingState(true);
    currentRawResponse = "";
    currentThoughtText = "";
    currentActiveSpeechSegment = null;
    currentActiveSpeechBuffer = "";
    currentActiveThinkingPill = null;
    const cardBundle = createAmadeusStreamCard();
    currentAmadeusCard = cardBundle.card;
    currentTurnTimelineFlow = cardBundle.timelineFlow;
    currentActiveThinkingPill = cardBundle.thinkingPill;
    currentOperationsFlow = currentTurnTimelineFlow;
  }

  if (data.type === "thought" && data.text) {
    currentThoughtText += data.text;
    if (currentTurnTimelineFlow) {
      const pill = getOrCreateActiveThinkingPill(currentTurnTimelineFlow);
      const content = pill.querySelector(".thinking-content");
      if (content) {
        content.textContent += data.text;
      }
      scrollChatToBottom();
    }
  }

  if (data.type === "tool_event" && data.tool) {
    handleLiveToolEvent(data.tool);
  }

  if (data.type === "tool_demoted") {
    handleToolDemotedEvent(data);
  }

  if (data.type === "token" && data.token) {
    stopThinkingDotsAnimation();
    if (currentActiveThinkingPill) {
      const content = currentActiveThinkingPill.querySelector(".thinking-content");
      if (!content || !content.textContent.trim()) {
        currentActiveThinkingPill.remove();
      }
      currentActiveThinkingPill = null;
    }

    if (currentTurnTimelineFlow) {
      if (!currentActiveSpeechSegment || currentTurnTimelineFlow.lastElementChild !== currentActiveSpeechSegment) {
        currentActiveSpeechSegment = document.createElement("div");
        currentActiveSpeechSegment.className = "speech-segment msg-body";
        currentTurnTimelineFlow.appendChild(currentActiveSpeechSegment);
        currentActiveSpeechBuffer = "";

        // Reveal the copy button as soon as the first speech segment appears
        if (currentAmadeusCard) {
          const copyBtn = currentAmadeusCard.querySelector(".copy-response-btn");
          if (copyBtn) copyBtn.style.display = "inline-flex";
        }
      }

      currentActiveSpeechBuffer += data.token;
      currentRawResponse += data.token;
      renderCleanResponse(currentActiveSpeechSegment, currentActiveSpeechBuffer);
      scrollChatToBottom();
    }
  }

  if (data.type === "stream_end") {
    activeSpeechEmotion = "neutral";
    setGeneratingState(false);
    stopThinkingDotsAnimation();
    sendWs({ type: "request_session_tasks" });
    sendWs({ type: "request_session_subagents" });

    if (currentActiveThinkingPill) {
      const content = currentActiveThinkingPill.querySelector(".thinking-content");
      if (!content || !content.textContent.trim()) {
        currentActiveThinkingPill.remove();
      }
      currentActiveThinkingPill = null;
    }

    if (currentTurnTimelineFlow && data.thought && data.thought.trim()) {
      const hasThinking = currentTurnTimelineFlow.querySelector(".thinking-pill");
      if (!hasThinking) {
        const pill = document.createElement("div");
        pill.className = "op-pill thinking-pill collapsed";
        pill.innerHTML = `
          <div class="pill-header">
            <div class="pill-title-group">
              <span class="pill-icon">💭</span>
              <span class="pill-label">Thinking</span>
            </div>
            <span class="pill-chevron">▼</span>
          </div>
          <div class="pill-body thinking-content">${escapeHtml(data.thought.trim())}</div>
        `;
        pill.querySelector(".pill-header").addEventListener("click", () => {
          pill.classList.toggle("collapsed");
        });
        currentTurnTimelineFlow.prepend(pill);
      }
    }

    if (currentActiveSpeechSegment && currentActiveSpeechBuffer) {
      renderCleanResponse(currentActiveSpeechSegment, currentActiveSpeechBuffer);
    }

    if (currentAmadeusCard) {
      const segments = Array.from(currentAmadeusCard.querySelectorAll(".speech-segment"));
      const combinedCleanText = segments.length > 0
        ? segments.map((s) => s.innerText.trim()).filter(Boolean).join("\n\n")
        : extractCleanVisualText(data.response || currentRawResponse);
      currentAmadeusCard.dataset.fullText = combinedCleanText;
    }

    currentAmadeusCard = null;
    currentTurnTimelineFlow = null;
    currentActiveSpeechSegment = null;
    currentActiveSpeechBuffer = "";
    currentOperationsFlow = null;
    currentAmadeusMsgBody = null;
    scrollChatToBottom();
  }

  // 8. Mic Recording State & Transcripts
  if (data.type === "mic_recording_state") {
    isRecording = Boolean(data.recording);
    if (isRecording) {
      micBtn.classList.add("recording");
      micBtn.title = "Recording voice... Click to stop";
    } else {
      micBtn.classList.remove("recording");
      micBtn.title = "Click to speak (Click again to finish & edit)";
    }
  }

  if (data.type === "mic_transcript") {
    micBtn.classList.remove("recording");
    micBtn.title = "Click to speak (Click again to finish & edit)";
    if (data.text && data.text.trim()) {
      const incoming = data.text.trim();
      const current = chatInput.value.trim();
      chatInput.value = current ? `${current} ${incoming}` : incoming;
      autoResizeTextarea();
      chatInput.focus();
    }
  }

  // 8c. Attachment Staged Confirmation
  if (data.type === "attachment_staged") {
    const att = stagedAttachments.find((a) => a.id === data.id);
    if (att) {
      att.path = data.path || "";
      att.file_uri = data.file_uri || "";
      att.tokens = typeof data.tokens === "number" ? data.tokens : att.tokens;
      att.size = typeof data.size === "number" ? data.size : att.size;
      att.status = "ready";
      renderStagingTray();
    }
    if (data.data_url) {
      if (data.file_uri) localImageDataCache.set(data.file_uri, data.data_url);
      if (data.path) {
        localImageDataCache.set(data.path, data.data_url);
        localImageDataCache.set(data.path.replace(/\\/g, "/"), data.data_url);
      }
    }
  }

  // 8d. Image Data URL Response (ss-16.png bug fix)
  if (data.type === "image_data_url_response" && data.data_url) {
    if (data.path) {
      localImageDataCache.set(data.path, data.data_url);
      localImageDataCache.set(data.path.replace(/\\/g, "/"), data.data_url);
      pendingImageRequests.delete(data.path);
      pendingImageRequests.delete(data.path.replace(/\\/g, "/"));
    }
    if (data.raw_path) {
      localImageDataCache.set(data.raw_path, data.data_url);
      localImageDataCache.set(data.raw_path.replace(/\\/g, "/"), data.data_url);
      pendingImageRequests.delete(data.raw_path);
      pendingImageRequests.delete(data.raw_path.replace(/\\/g, "/"));
    }
    const normP1 = (data.path || "").replace(/\\/g, "/").toLowerCase();
    const normP2 = (data.raw_path || "").replace(/\\/g, "/").toLowerCase();

    document.querySelectorAll("img.chat-image-thumb").forEach((img) => {
      const raw = img.dataset.rawPath;
      if (!raw) return;
      const normRaw = raw.replace(/\\/g, "/").toLowerCase();
      if (raw === data.path || raw === data.raw_path || normRaw === normP1 || normRaw === normP2) {
        img.src = data.data_url;
      }
    });
  }

  // 9. Model Switched
  if (data.type === "model_switched" && data.model) {
    updateActiveModel(data.model);
  }

  // 10. Chat Reset
  if (data.type === "chat_reset") {
    setGeneratingState(false);
    chatFeed.innerHTML = "";
    if (sessionHeading) sessionHeading.textContent = "New Chat";
    document.querySelectorAll(".history-item").forEach((el) => el.classList.remove("active"));
    sessionDiffsCache = [];
    sessionCommandsCache = [];
    sessionTasksCache = [];
    sessionSubagentsCache = [];
    updateModalBadges();
    appendWelcomeMessage();
  }

  // 11. Conversation Resumed from Archive
  if (data.type === "convo_resumed" && data.history) {
    loadConversationIntoChat(data.history, data.id, data.title);
  }

  // 12. Conversation List Updated
  if (data.type === "convos_updated" && data.conversations) {
    renderHistoryList(data.conversations);
  }

  // 12b. Search Chats Results
  if (data.type === "search_chats_results") {
    renderSearchResults(data.results || [], data.query || "");
  }

  // 13. Voice Toggled
  if (data.type === "voice_toggled") {
    updateVoiceStatus(data.enabled);
  }

  // 14. Slash Commands / Detailed Inspector View
  if (data.type === "open_detailed_view") {
    if (data.diffs) {
      sessionDiffsCache = data.diffs;
      renderDiffsPane(data.diffs);
    }
    if (data.commands) {
      sessionCommandsCache = data.commands;
      renderTerminalPane(data.commands);
    }
    if (data.tasks) {
      sessionTasksCache = data.tasks;
      renderTasksPane(data.tasks);
    }
    if (data.subagents) {
      sessionSubagentsCache = data.subagents;
      renderSubagentsPane(data.subagents);
    }
    updateModalBadges();
    openDetailedModalTab(data.view || "diffs");
  }

  // 15. Session Cache Packets
  if (data.type === "session_diffs") {
    sessionDiffsCache = data.diffs || [];
    renderDiffsPane(sessionDiffsCache);
    updateModalBadges();
  }

  if (data.type === "session_commands") {
    sessionCommandsCache = data.commands || [];
    renderTerminalPane(sessionCommandsCache);
    updateModalBadges();
  }

  if (data.type === "session_tasks") {
    sessionTasksCache = data.tasks || [];
    renderTasksPane(sessionTasksCache);
    updateModalBadges();
    for (const t of sessionTasksCache) {
      const tid = t.task_id || `task-${t.step_index}`;
      syncTaskStatusInDom(tid, t.state);
    }
  }

  if (data.type === "session_subagents") {
    const killOverlay = document.getElementById("subagentKillLoadingOverlay");
    if (killOverlay) killOverlay.classList.add("hidden");
    sessionSubagentsCache = data.subagents || [];
    renderSubagentsPane(sessionSubagentsCache);
    updateModalBadges();
    for (const s of sessionSubagentsCache) {
      if (s.subagent_id) {
        syncSubagentStatusInDom(s.subagent_id, s.state);
      }
    }
  }

  // 16. Live Task Log Packet
  if (data.type === "task_log") {
    if (taskLogBox) {
      taskLogBox.textContent = data.log || "No task log output recorded yet.";
    }
  }

  // 17. Subagent Transcript Packet
  if (data.type === "subagent_info") {
    if (selectedSubagentId === data.subagent_id) {
      renderSubagentTranscript(data.subagent_id, data.data);
    }
  }

  // 18. Process Terminations Feedback
  if (data.type === "kill_task_result") {
    const tid = data.task_id || selectedTaskId;
    syncTaskStatusInDom(tid, "CANCELLED");
    sendWs({ type: "request_session_tasks", subagent_id: null });
  }

  if (data.type === "kill_subagent_result") {
    const killOverlay = document.getElementById("subagentKillLoadingOverlay");
    if (killOverlay) killOverlay.classList.add("hidden");
    const sid = data.subagent_id;
    if (sid) {
      syncSubagentStatusInDom(sid, "DONE");
      sendWs({ type: "request_session_subagents" });
    }
  }

  // 19. Account Usage Quota Packet
  if (data.type === "quota_update") {
    renderUsageQuota(data.data);
  }
}

// ════════ THINKING ANIMATION & HELPERS ════════
function startThinkingDotsAnimation(dotsEl) {
  stopThinkingDotsAnimation();
  if (dotsEl) currentThinkingDots = dotsEl;
  let step = 0;
  const dotFrames = [".", "..", "...", "...."];
  thinkingInterval = setInterval(() => {
    if (currentThinkingDots) {
      currentThinkingDots.textContent = dotFrames[step % dotFrames.length];
      step++;
    }
  }, 350);
}

function stopThinkingDotsAnimation() {
  if (thinkingInterval) {
    clearInterval(thinkingInterval);
    thinkingInterval = null;
  }
  if (currentThinkingDots) {
    currentThinkingDots.textContent = "";
  }
}

function getOrCreateActiveThinkingPill(container) {
  if (currentActiveThinkingPill && container.contains(currentActiveThinkingPill)) {
    return currentActiveThinkingPill;
  }
  const pill = document.createElement("div");
  pill.className = "op-pill thinking-pill collapsed";
  pill.innerHTML = `
    <div class="pill-header">
      <div class="pill-title-group">
        <span class="pill-icon">💭</span>
        <span class="pill-label">Thinking</span>
        <span class="thinking-dots">...</span>
      </div>
      <span class="pill-chevron">▼</span>
    </div>
    <div class="pill-body thinking-content"></div>
  `;

  pill.querySelector(".pill-header").addEventListener("click", () => {
    pill.classList.toggle("collapsed");
  });

  container.appendChild(pill);
  currentActiveThinkingPill = pill;
  startThinkingDotsAnimation(pill.querySelector(".thinking-dots"));
  return pill;
}

function renderDiffLinesHtml(diffText) {
  if (!diffText) return `<span class="diff-line context">No diff content</span>`;
  const lines = diffText.split("\n");
  return lines.map((line) => {
    if (line.startsWith("@@")) {
      return `<div class="diff-line hunk">${escapeHtml(line)}</div>`;
    } else if (line.startsWith("+") && !line.startsWith("+++")) {
      return `<div class="diff-line add">${escapeHtml(line)}</div>`;
    } else if (line.startsWith("-") && !line.startsWith("---")) {
      return `<div class="diff-line del">${escapeHtml(line)}</div>`;
    } else {
      return `<div class="diff-line context">${escapeHtml(line)}</div>`;
    }
  }).join("");
}

function getFileName(filePath) {
  if (!filePath) return "file";
  const clean = filePath.replace(/\\/g, "/");
  const parts = clean.split("/");
  return parts[parts.length - 1] || filePath;
}

function isSameTaskId(idA, idB) {
  if (!idA || !idB) return false;
  if (idA === idB) return true;
  const cleanA = String(idA).replace(/\\/g, "/");
  const cleanB = String(idB).replace(/\\/g, "/");
  if (cleanA === cleanB) return true;
  const hasSlashA = cleanA.includes("/");
  const hasSlashB = cleanB.includes("/");
  if (!hasSlashA && hasSlashB) {
    return cleanB.endsWith("/" + cleanA);
  }
  if (hasSlashA && !hasSlashB) {
    return cleanA.endsWith("/" + cleanB);
  }
  return false;
}

function syncTaskStatusInDom(taskId, state) {
  if (!taskId) return;
  const isFailed = state === "FAILED" || state === "ERROR";
  const isCancelled = state === "CANCELLED" || state === "KILLED" || state === "CANCELED" || state === "INTERRUPTED";
  const isDone = state === "COMPLETED" || state === "DONE";
  const isRunning = state === "ACTIVE" || state === "RUNNING";
  const badgeClass = isFailed ? "file-badge-deleted" : (isCancelled ? "file-badge-deleted" : (isDone ? "badge-task-done" : "badge-task-active"));
  const badgeText = isFailed ? "FAILED" : (isCancelled ? (state === "INTERRUPTED" ? "INTERRUPTED" : "CANCELLED") : (isDone ? "COMPLETED" : "ACTIVE"));

  // 1. Update matching in-chat task pills in chatFeed
  document.querySelectorAll(".op-pill.task-pill").forEach((pill) => {
    const pillTid = pill.dataset.taskId;
    if (pillTid && isSameTaskId(pillTid, taskId)) {
      const badge = pill.querySelector(".pill-badge");
      if (badge) {
        badge.className = `pill-badge ${badgeClass}`;
        badge.textContent = badgeText;
      }
    }
  });

  // 2. Update task in sessionTasksCache
  sessionTasksCache.forEach((t) => {
    const tId = t.task_id || `task-${t.step_index}`;
    if (isSameTaskId(tId, taskId)) {
      t.state = state;
    }
  });

  // 3. Update task in detailed modal inspector if selected
  if (selectedTaskId && isSameTaskId(selectedTaskId, taskId)) {
    if (taskDetailState) {
      taskDetailState.className = `pill-badge ${badgeClass}`;
      taskDetailState.textContent = badgeText;
    }
    if (killTaskBtn) {
      if (isCancelled || isDone) {
        killTaskBtn.classList.add("hidden");
      } else if (isRunning) {
        killTaskBtn.classList.remove("hidden");
        killTaskBtn.textContent = "⏹ Kill Task";
      }
    }
  }

  // 4. Update task item list in paneTasks if rendered
  if (taskListItems) {
    taskListItems.querySelectorAll(".pane-item").forEach((item) => {
      const titleSpan = item.querySelector(".pane-item-title span");
      if (titleSpan) {
        const itemTid = titleSpan.textContent.replace("⏳", "").trim();
        if (isSameTaskId(itemTid, taskId)) {
          const statusSpan = item.querySelector(".pane-item-sub span:last-child");
          if (statusSpan) {
            statusSpan.style.color = isCancelled ? "#f43f5e" : (isDone ? "#10b981" : "#fbbf24");
            statusSpan.textContent = isCancelled ? "CANCELLED" : (isDone ? "DONE" : "ACTIVE");
          }
        }
      }
    });
  }
}

function syncSubagentStatusInDom(subagentId, state) {
  if (!subagentId) return;
  const isKilled = state === "KILLED" || state === "CANCELLED" || state === "INTERRUPTED";
  const isDone = state === "DONE" || state === "COMPLETED";
  const isWaiting = state === "WAITING";
  const isActive = state === "ACTIVE" || state === "ALIVE";
  const badgeClass = isKilled ? "file-badge-deleted" : (isDone ? "badge-subagent" : (isWaiting ? "badge-running" : "badge-task-active"));
  const badgeText = isKilled ? "KILLED" : (isDone ? "DONE" : (isWaiting ? "WAITING" : "ACTIVE"));

  // 1. Update in-chat subagent pills in chatFeed
  document.querySelectorAll(".op-pill.subagent-pill").forEach((pill) => {
    const pillSid = pill.dataset.subagentId || pill.dataset.subagentKey;
    if (pillSid === subagentId || (pill.dataset.subagentId && pill.dataset.subagentId === subagentId)) {
      const badge = pill.querySelector(".pill-badge");
      if (badge) {
        badge.className = `pill-badge ${badgeClass}`;
        badge.textContent = badgeText;
      }
    }
  });

  // 2. Update subagent in sessionSubagentsCache
  sessionSubagentsCache.forEach((s) => {
    if (s.subagent_id === subagentId) {
      s.state = state;
    }
  });

  // 3. Update detailed modal inspector if selected
  if (selectedSubagentId === subagentId) {
    if (subagentMetaBadge) {
      subagentMetaBadge.className = `pill-badge ${badgeClass}`;
      subagentMetaBadge.textContent = badgeText;
    }
    if (killSubagentBtn) {
      if (isKilled) {
        killSubagentBtn.classList.add("hidden");
      } else {
        killSubagentBtn.classList.remove("hidden");
        killSubagentBtn.textContent = "⏹ Stop Subagents";
      }
    }
  }

  // 4. Update subagent item list in paneSubagents if rendered
  if (subagentListItems) {
    subagentListItems.querySelectorAll(".pane-item").forEach((item) => {
      const itemSid = item.dataset.subagentId;
      if (itemSid === subagentId) {
        const statusSpan = item.querySelector(".pane-item-sub span:last-child");
        if (statusSpan) {
          const statusColor = isKilled ? "#f43f5e" : (isDone ? "#10b981" : (isWaiting ? "#fbbf24" : "#22d3ee"));
          statusSpan.style.color = statusColor;
          statusSpan.textContent = badgeText;
        }
      }
    });
  }
}

function registerToolInSessionCaches(tool) {
  if (!tool) return;
  const category = tool.category;
  if (category === "diff" && tool.file) {
    const existing = sessionDiffsCache.find((d) => d.file === tool.file);
    if (!existing) {
      sessionDiffsCache.push({ ...tool });
    } else {
      if (tool.diff && (!existing.diff || !existing.diff.includes(tool.diff.trim()))) {
        existing.diff = (existing.diff ? existing.diff.trim() + "\n\n" : "") + tool.diff.trim();
        existing.lines_added = (existing.lines_added || 0) + (tool.lines_added || 0);
        existing.lines_removed = (existing.lines_removed || 0) + (tool.lines_removed || 0);
      }
      if (tool.deleted) {
        existing.deleted = true;
        existing.status = "DELETED";
      }
      if (tool.step_index !== undefined) {
        existing.step_index = tool.step_index;
      }
      if (tool.state) {
        existing.state = tool.state;
      }
    }
  } else if (category === "command" && tool.command) {
    const existingCmd = sessionCommandsCache.find((c) => c.command === tool.command);
    if (!existingCmd) {
      sessionCommandsCache.push(tool);
    } else {
      Object.assign(existingCmd, tool);
    }
    if (tool.task_id) {
      const tid = tool.task_id;
      const existingTask = sessionTasksCache.find((t) => {
        const tId = t.task_id || `task-${t.step_index}`;
        return isSameTaskId(tId, tid);
      });
      if (!existingTask) {
        sessionTasksCache.push(tool);
      } else {
        Object.assign(existingTask, tool);
      }
    }
  } else if (category === "task") {
    const tid = tool.task_id || `task-${tool.step_index}`;
    const existing = sessionTasksCache.find((t) => {
      const existingTid = t.task_id || `task-${t.step_index}`;
      return isSameTaskId(existingTid, tid);
    });
    if (!existing) {
      sessionTasksCache.push(tool);
    } else {
      Object.assign(existing, tool);
    }
    if (tool.command) {
      const existingCmd = sessionCommandsCache.find((c) => c.command === tool.command || (c.task_id && isSameTaskId(c.task_id, tid)));
      if (!existingCmd) {
        sessionCommandsCache.push(tool);
      } else {
        Object.assign(existingCmd, tool);
      }
    }
  } else if (category === "subagent") {
    const sid = tool.subagent_id;
    if (sid) {
      const existing = sessionSubagentsCache.find((s) => s.subagent_id === sid);
      if (!existing) {
        sessionSubagentsCache.push(tool);
      } else {
        Object.assign(existing, tool);
      }
    }
  } else if (category === "task_op") {
    const action = (tool.action || "").toLowerCase();
    const targetTid = tool.target_task_id || tool.task_id;
    if (action === "kill" || action === "cancel") {
      syncTaskStatusInDom(targetTid, "CANCELLED");
    }
  } else if (category === "subagent_op") {
    const action = (tool.action || "").toLowerCase();
    if (action === "kill" || action === "kill_all") {
      const targetIds = tool.target_subagent_ids || tool.conversation_ids || [tool.subagent_id];
      if (Array.isArray(targetIds)) {
        targetIds.forEach((id) => syncSubagentStatusInDom(id, "KILLED"));
      } else if (typeof targetIds === "string") {
        syncSubagentStatusInDom(targetIds, "KILLED");
      }
      if (action === "kill_all") {
        sessionSubagentsCache.forEach((s) => {
          if (s.subagent_id) syncSubagentStatusInDom(s.subagent_id, "KILLED");
        });
      }
    }
  }
  updateModalBadges();
}

function handleLiveToolEvent(tool) {
  if (!tool) return;
  if (Array.isArray(tool)) {
    tool.forEach((t) => handleLiveToolEvent(t));
    return;
  }
  stopThinkingDotsAnimation();

  // If there's an active thinking pill that received no thought tokens, clean it up
  if (currentActiveThinkingPill) {
    const content = currentActiveThinkingPill.querySelector(".thinking-content");
    if (!content || !content.textContent.trim()) {
      currentActiveThinkingPill.remove();
    }
    currentActiveThinkingPill = null;
  }

  // Finalize any ongoing speech segment before the tool execution
  if (currentActiveSpeechSegment && currentActiveSpeechBuffer) {
    renderCleanResponse(currentActiveSpeechSegment, currentActiveSpeechBuffer);
  }
  currentActiveSpeechSegment = null;
  currentActiveSpeechBuffer = "";

  if (!currentTurnTimelineFlow) {
    const allCards = document.querySelectorAll(".msg-card.amadeus");
    if (allCards.length > 0) {
      currentTurnTimelineFlow = allCards[allCards.length - 1].querySelector(".turn-timeline-flow");
    }
  }

  registerToolInSessionCaches(tool);

  if (tool && tool.category === "task" && tool.task_id) {
    syncTaskStatusInDom(tool.task_id, tool.state);
  }
  if (tool && tool.category === "subagent" && tool.subagent_id) {
    syncSubagentStatusInDom(tool.subagent_id, tool.state);
  }

  if (currentTurnTimelineFlow) {
    renderOrUpdateOperationPill(currentTurnTimelineFlow, tool);
    scrollChatToBottom();
  }
}

function morphCommandToTaskPill(commandPill, taskId, state, output) {
  if (!commandPill) return;
  const stepIdx = commandPill.dataset.stepIndex;
  const canonicalTaskId = taskId || commandPill.dataset.taskId || (stepIdx !== undefined ? `task-${stepIdx}` : "task-unknown");
  const cleanId = canonicalTaskId.split("/").pop();
  const cmdText = commandPill.dataset.commandText || (commandPill.querySelector(".pill-cmd-code") ? commandPill.querySelector(".pill-cmd-code").textContent : "");

  commandPill.classList.remove("command-pill");
  commandPill.classList.add("task-pill");
  commandPill.className = "op-pill task-pill collapsed";
  commandPill.dataset.taskId = canonicalTaskId;
  commandPill.dataset.taskKey = `task_${canonicalTaskId}`;
  commandPill.dataset.opKey = `task_${canonicalTaskId}`;
  delete commandPill.dataset.commandKey;

  const iconEl = commandPill.querySelector(".pill-icon");
  if (iconEl) iconEl.textContent = "⏳";

  const labelEl = commandPill.querySelector(".pill-label");
  if (labelEl) labelEl.textContent = "Background Command:";

  let filenameEl = commandPill.querySelector(".pill-filename");
  if (!filenameEl) {
    filenameEl = document.createElement("span");
    filenameEl.className = "pill-filename";
    if (labelEl && labelEl.nextSibling) {
      labelEl.parentNode.insertBefore(filenameEl, labelEl.nextSibling);
    } else if (labelEl) {
      labelEl.parentNode.appendChild(filenameEl);
    }
  }
  if (filenameEl) {
    filenameEl.title = canonicalTaskId;
    filenameEl.textContent = cleanId;
  }

  const isFailed = state === "FAILED" || state === "ERROR";
  const isDone = state === "COMPLETED" || state === "DONE";
  const isCancelled = state === "CANCELLED" || state === "CANCELED" || state === "INTERRUPTED" || state === "KILLED";
  const badgeClass = isFailed ? "file-badge-deleted" : (isCancelled ? "file-badge-deleted" : (isDone ? "badge-task-done" : "badge-task-active"));
  const badgeText = isFailed ? "FAILED" : (isCancelled ? (state === "INTERRUPTED" ? "INTERRUPTED" : "CANCELLED") : (isDone ? "COMPLETED" : (state || "RUNNING")));

  const badgeEl = commandPill.querySelector(".pill-badge");
  if (badgeEl) {
    badgeEl.className = `pill-badge ${badgeClass}`;
    badgeEl.textContent = badgeText;
  }

  const expandBtn = commandPill.querySelector(".pill-expand-modal-btn");
  if (expandBtn) {
    expandBtn.title = "Open Live Task Log";
    const newExpandBtn = expandBtn.cloneNode(true);
    expandBtn.parentNode.replaceChild(newExpandBtn, expandBtn);
    newExpandBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      openDetailedModalTab("tasks", {
        taskId: canonicalTaskId,
        task: { task_id: canonicalTaskId, command: cmdText, state: state || "RUNNING", output: output || "" }
      });
    });
  }

  if (output) {
    const pre = commandPill.querySelector(".pill-code-pre");
    if (pre) pre.textContent = output;
  }
}

function handleToolDemotedEvent(data) {
  const stepIdx = data.step_index;
  const taskId = data.task_id;
  let cmdPill = null;
  if (currentTurnTimelineFlow && data.from_op_key) {
    cmdPill = currentTurnTimelineFlow.querySelector('[data-op-key="' + CSS.escape(data.from_op_key) + '"]');
  }
  if (!cmdPill && data.from_op_key) {
    cmdPill = document.querySelector('[data-op-key="' + CSS.escape(data.from_op_key) + '"]');
  }
  if (!cmdPill && data.op_key) {
    if (currentTurnTimelineFlow) {
      cmdPill = currentTurnTimelineFlow.querySelector('[data-op-key="' + CSS.escape(data.op_key) + '"]');
    }
    if (!cmdPill) {
      cmdPill = document.querySelector('[data-op-key="' + CSS.escape(data.op_key) + '"]');
    }
  }
  if (cmdPill) {
    morphCommandToTaskPill(cmdPill, taskId, data.state || "RUNNING", data.output);
    cmdPill.dataset.opKey = data.to_op_key || ('task_' + taskId);
  }
  registerToolInSessionCaches({
    category: "task",
    task_id: taskId,
    step_index: stepIdx,
    state: data.state || "RUNNING",
    output: data.output || "Task running in background..."
  });
  updateModalBadges();
}

function renderOrUpdateOperationPill(container, op) {
  if (!container || !op) return;

  const category = op.category || "generic";
  const stepIdx = op.step_index;

  // 1. Command Execution Pill
  if (category === "command") {
    const isRunning = op.state === "ACTIVE" || op.state === "RUNNING";
    const exitOk = op.exit_code === 0 && op.state !== "ERROR" && op.state !== "FAILED";
    const badgeClass = isRunning ? "badge-running" : (exitOk ? "badge-exit-ok" : "badge-exit-fail");
    const badgeText = isRunning ? "RUNNING" : (exitOk ? "EXIT 0" : `EXIT ${op.exit_code || 1}`);

    const opKey = op.op_key || `cmd_${stepIdx}_${op.tool_call_index || 0}`;
    let pill = container.querySelector('[data-op-key="' + CSS.escape(opKey) + '"]');

    if (pill) {
      pill.dataset.opKey = opKey;
      const badge = pill.querySelector(".pill-badge");
      if (badge) {
        badge.className = `pill-badge ${badgeClass}`;
        badge.textContent = badgeText;
      }
      const pre = pill.querySelector(".pill-code-pre");
      if (pre && op.output) {
        pre.textContent = op.output;
      }
      return;
    }

    pill = document.createElement("div");
    pill.className = "op-pill command-pill collapsed";
    pill.dataset.opKey = opKey;
    if (op.command) pill.dataset.commandText = op.command;
    if (stepIdx !== undefined && stepIdx !== null) pill.dataset.stepIndex = stepIdx;

    pill.innerHTML = `
      <div class="pill-header">
        <div class="pill-title-group">
          <span class="pill-icon">⚡</span>
          <span class="pill-label">Command:</span>
          <code class="pill-cmd-code" title="${escapeHtml(op.command || '')}">${escapeHtml(op.command || '')}</code>
        </div>
        <div class="pill-status-group">
          <span class="pill-badge ${badgeClass}">${badgeText}</span>
          <button class="pill-expand-modal-btn" title="Open in Terminal Inspector">↗ Detailed View</button>
          <span class="pill-chevron">▼</span>
        </div>
      </div>
      <div class="pill-body">
        ${op.cwd ? `<div class="pill-meta-row"><span>CWD: ${escapeHtml(op.cwd)}</span>${op.duration ? `<span>${op.duration.toFixed(1)}s</span>` : ""}</div>` : ""}
        <pre class="pill-code-pre">${escapeHtml(op.output || "Executing command in shell...")}</pre>
      </div>
    `;

    pill.querySelector(".pill-header").addEventListener("click", (e) => {
      if (e.target.closest(".pill-expand-modal-btn")) return;
      pill.classList.toggle("collapsed");
    });

    pill.querySelector(".pill-expand-modal-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      openDetailedModalTab("terminal", { command: op });
    });

    container.appendChild(pill);
    return;
  }

  // 2. File Diff Pill
  if (category === "diff") {
    const filename = getFileName(op.file);
    const added = op.lines_added || 0;
    const removed = op.lines_removed || 0;
    const fileBase = op.file ? op.file.split(/[/\\]/).pop() : "file";
    const opKey = op.op_key || `diff_${stepIdx}_${op.tool_call_index || 0}_${fileBase}`;

    let pill = container.querySelector('[data-op-key="' + CSS.escape(opKey) + '"]');

    if (pill) {
      if (op.file) pill.dataset.diffFile = op.file;
      if (stepIdx !== undefined && stepIdx !== null) pill.dataset.stepIndex = stepIdx;
      pill.dataset.opKey = opKey;
      const badge = pill.querySelector(".pill-badge");
      if (badge) badge.textContent = `+${added} / -${removed}`;
      const diffContainer = pill.querySelector(".diff-lines-container");
      if (diffContainer && op.diff) {
        diffContainer.innerHTML = renderDiffLinesHtml(op.diff);
      }
      return;
    }

    pill = document.createElement("div");
    pill.className = "op-pill diff-pill collapsed";
    if (op.file) pill.dataset.diffFile = op.file;
    if (stepIdx !== undefined && stepIdx !== null) pill.dataset.stepIndex = stepIdx;
    pill.dataset.opKey = opKey;

    pill.innerHTML = `
      <div class="pill-header">
        <div class="pill-title-group">
          <span class="pill-icon">📝</span>
          <span class="pill-label">Diff:</span>
          <span class="pill-filename" title="${escapeHtml(op.file || '')}">${escapeHtml(filename)}</span>
        </div>
        <div class="pill-status-group">
          <span class="pill-badge badge-diff">+${added} / -${removed}</span>
          <button class="pill-expand-modal-btn" title="Open in Diff Inspector">↗ Detailed View</button>
          <span class="pill-chevron">▼</span>
        </div>
      </div>
      <div class="pill-body">
        <div class="pill-meta-row">${escapeHtml(op.file || '')}</div>
        <div class="diff-lines-container">
          ${renderDiffLinesHtml(op.diff || '')}
        </div>
      </div>
    `;

    pill.querySelector(".pill-header").addEventListener("click", (e) => {
      if (e.target.closest(".pill-expand-modal-btn")) return;
      pill.classList.toggle("collapsed");
    });

    pill.querySelector(".pill-expand-modal-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      openDetailedModalTab("diffs", { file: op.file, diff: op });
    });

    container.appendChild(pill);
    return;
  }

  // 3. Background Task Pill
  if (category === "task") {
    const taskId = op.task_id || "";
    const cleanId = taskId ? taskId.split("/").pop() : `task-${stepIdx || 1}`;
    const isFailed = op.state === "FAILED" || op.state === "ERROR";
    const isDone = op.state === "COMPLETED" || op.state === "DONE";
    const isCancelled = op.state === "CANCELLED" || op.state === "CANCELED" || op.state === "INTERRUPTED" || op.state === "KILLED";
    const badgeClass = isFailed ? "file-badge-deleted" : (isCancelled ? "file-badge-deleted" : (isDone ? "badge-task-done" : "badge-task-active"));
    const badgeText = isFailed ? "FAILED" : (isCancelled ? (op.state === "INTERRUPTED" ? "INTERRUPTED" : "CANCELLED") : (isDone ? "COMPLETED" : "ACTIVE"));
    const isCommand = Boolean(op.command);
    const opKey = op.op_key || (taskId ? `task_${taskId}` : `task_step_${stepIdx}_${op.tool_call_index || 0}`);

    // Deterministic Lookup Rule
    let pill = opKey ? container.querySelector('[data-op-key="' + CSS.escape(opKey) + '"]') : null;
    if (!pill && taskId) {
      pill = container.querySelector('[data-task-id="' + CSS.escape(taskId) + '"]');
    }

    // NEVER mutate dataset.taskId from one ID to another in an existing pill!
    if (pill && pill.dataset.taskId && taskId && pill.dataset.taskId !== taskId) {
      pill = null;
    }

    if (pill) {
      if (!pill.dataset.taskId && taskId) {
        pill.dataset.taskId = taskId;
        const filenameEl = pill.querySelector(".pill-filename");
        if (filenameEl) {
          filenameEl.title = taskId;
          filenameEl.textContent = cleanId;
        }
        const expandBtn = pill.querySelector(".pill-expand-modal-btn");
        if (expandBtn) {
          expandBtn.onclick = (e) => {
            e.stopPropagation();
            openDetailedModalTab("tasks", { taskId: taskId, task: op });
          };
        }
      }
      pill.dataset.opKey = opKey;
      const badge = pill.querySelector(".pill-badge");
      if (badge) {
        badge.className = `pill-badge ${badgeClass}`;
        badge.textContent = badgeText;
      }
      const pre = pill.querySelector(".pill-code-pre");
      if (pre && op.output) {
        pre.textContent = op.output;
      }
      return;
    }

    pill = document.createElement("div");
    pill.className = "op-pill task-pill collapsed";
    if (taskId) pill.dataset.taskId = taskId;
    pill.dataset.opKey = opKey;
    if (stepIdx !== undefined && stepIdx !== null) pill.dataset.stepIndex = stepIdx;

    pill.innerHTML = `
      <div class="pill-header">
        <div class="pill-title-group">
          <span class="pill-icon">⏳</span>
          <span class="pill-label">${isCommand ? "Background Command:" : "Task:"}</span>
          <span class="pill-filename" title="${escapeHtml(taskId)}">${escapeHtml(cleanId)}</span>
          ${isCommand ? `<code class="pill-cmd-code" title="${escapeHtml(op.command)}">${escapeHtml(op.command)}</code>` : ""}
        </div>
        <div class="pill-status-group">
          <span class="pill-badge ${badgeClass}">${badgeText}</span>
          <button class="pill-expand-modal-btn" title="Open Live Task Log">↗ Detailed View</button>
          <span class="pill-chevron">▼</span>
        </div>
      </div>
      <div class="pill-body">
        <div class="pill-meta-row">
          ${isCommand ? `<span>Command: <code>${escapeHtml(op.command)}</code></span>` : `<span>${escapeHtml(op.prompt || op.action || "Background Scheduled Job")}</span>`}
          ${op.cwd ? `<span>CWD: ${escapeHtml(op.cwd)}</span>` : ""}
        </div>
        <pre class="pill-code-pre">${escapeHtml(op.output || "Task running in background...")}</pre>
      </div>
    `;

    pill.querySelector(".pill-header").addEventListener("click", (e) => {
      if (e.target.closest(".pill-expand-modal-btn")) return;
      pill.classList.toggle("collapsed");
    });

    pill.querySelector(".pill-expand-modal-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      openDetailedModalTab("tasks", { taskId: taskId, task: op });
    });

    container.appendChild(pill);
    return;
  }

  // 3b. Background Task Operation Pill (manage_task: kill, status, list, send_input)
  if (category === "task_op") {
    const action = (op.action || "manage").toLowerCase();
    const targetId = op.target_task_id || op.task_id || "";
    const cleanTargetId = targetId ? targetId.split("/").pop() : "";
    let actionLabel = "Task Action";
    let actionIcon = "📋";
    let badgeClass = "badge-task-done";
    let badgeText = "DONE";

    if (action === "kill" || action === "cancel") {
      actionLabel = "Cancelled Task";
      actionIcon = "🛑";
      badgeClass = "file-badge-deleted";
      badgeText = "CANCELLED";
    } else if (action === "status") {
      actionLabel = "Task Status Checked";
      actionIcon = "⏳";
      badgeClass = "badge-task-active";
      badgeText = "INSPECTED";
    } else if (action === "list") {
      actionLabel = "Listed Active Tasks";
      actionIcon = "📋";
      badgeClass = "badge-task-done";
      badgeText = "LISTED";
    } else if (action === "send_input") {
      actionLabel = "Sent Input to Task";
      actionIcon = "⌨️";
      badgeClass = "badge-task-done";
      badgeText = "SENT";
    }

    const opKey = op.op_key || `taskop_${stepIdx}_${op.tool_call_index || 0}_${action}_${targetId || "all"}`;
    let pill = container.querySelector('[data-op-key="' + CSS.escape(opKey) + '"]');

    if (pill) {
      pill.dataset.opKey = opKey;
      const badge = pill.querySelector(".pill-badge");
      if (badge) {
        badge.className = `pill-badge ${badgeClass}`;
        badge.textContent = badgeText;
      }
      const pre = pill.querySelector(".pill-code-pre");
      if (pre && op.output) pre.textContent = op.output;
      return;
    }

    pill = document.createElement("div");
    pill.className = "op-pill task-op-pill collapsed";
    pill.dataset.opKey = opKey;
    if (stepIdx !== undefined && stepIdx !== null) pill.dataset.stepIndex = stepIdx;
    if (targetId) pill.dataset.targetTaskId = targetId;

    pill.innerHTML = `
      <div class="pill-header">
        <div class="pill-title-group">
          <span class="pill-icon">${actionIcon}</span>
          <span class="pill-label">${actionLabel}:</span>
          ${cleanTargetId ? `<span class="pill-filename" title="${escapeHtml(targetId)}">${escapeHtml(cleanTargetId)}</span>` : ""}
        </div>
        <div class="pill-status-group">
          <span class="pill-badge ${badgeClass}">${badgeText}</span>
          ${targetId ? `<button class="pill-expand-modal-btn" title="Open Target Task in Tasks View">↗ Detailed View</button>` : ""}
          <span class="pill-chevron">▼</span>
        </div>
      </div>
      <div class="pill-body">
        ${targetId ? `<div class="pill-meta-row">Target Task: <code>${escapeHtml(targetId)}</code></div>` : ""}
        ${op.input ? `<div class="pill-meta-row">Input: <code>${escapeHtml(op.input)}</code></div>` : ""}
        <pre class="pill-code-pre">${escapeHtml(op.output || "Task operation executed.")}</pre>
      </div>
    `;

    pill.querySelector(".pill-header").addEventListener("click", (e) => {
      if (e.target.closest(".pill-expand-modal-btn")) return;
      pill.classList.toggle("collapsed");
    });

    const expandBtn = pill.querySelector(".pill-expand-modal-btn");
    if (expandBtn && targetId) {
      expandBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        openDetailedModalTab("tasks", { taskId: targetId });
      });
    }

    container.appendChild(pill);
    return;
  }

  // 4. Subagent Orchestration Pill
  if (category === "subagent") {
    const subName = op.role || op.type_name || "Subagent";
    const isDone = op.state === "DONE" || op.state === "COMPLETED";
    const isKilled = op.state === "KILLED" || op.state === "INTERRUPTED" || op.state === "CANCELLED";
    const isWaiting = op.state === "WAITING";
    const badgeClass = isKilled ? "file-badge-deleted" : (isDone ? "badge-subagent" : (isWaiting ? "badge-running" : "badge-task-active"));
    const badgeText = isKilled ? (op.state || "KILLED") : (isDone ? "DONE" : (isWaiting ? "WAITING" : (op.state || "ACTIVE")));
    const subId = op.subagent_id || "";
    const opKey = op.op_key || (subId ? `subagent_${subId}` : `subagent_${stepIdx}_${op.tool_call_index || 0}_${op.role || "subagent"}`);

    // Deterministic Lookup Rule
    let pill = opKey ? container.querySelector('[data-op-key="' + CSS.escape(opKey) + '"]') : null;
    if (!pill && subId) {
      pill = container.querySelector('[data-subagent-id="' + CSS.escape(subId) + '"]');
    }

    // NEVER mutate dataset.subagentId from one ID to another in an existing pill!
    if (pill && pill.dataset.subagentId && subId && pill.dataset.subagentId !== subId) {
      pill = null;
    }

    if (pill) {
      if (!pill.dataset.subagentId && subId) {
        pill.dataset.subagentId = subId;
        const metaRow = pill.querySelector(".pill-meta-row");
        if (metaRow && op.type_name) {
          metaRow.textContent = `Type: ${op.type_name} | ID: ${subId.slice(0, 8)}`;
        }
        const expandBtn = pill.querySelector(".pill-expand-modal-btn");
        if (expandBtn) {
          expandBtn.onclick = (e) => {
            e.stopPropagation();
            openDetailedModalTab("subagents", { subagentId: subId });
          };
        }
      }
      pill.dataset.opKey = opKey;
      const badge = pill.querySelector(".pill-badge");
      if (badge) {
        badge.className = `pill-badge ${badgeClass}`;
        badge.textContent = badgeText;
      }
      const pre = pill.querySelector(".pill-code-pre");
      if (pre && op.output) pre.textContent = op.output;
      return;
    }

    pill = document.createElement("div");
    pill.className = "op-pill subagent-pill collapsed";
    pill.dataset.opKey = opKey;
    if (subId) pill.dataset.subagentId = subId;
    if (stepIdx !== undefined && stepIdx !== null) pill.dataset.stepIndex = stepIdx;

    pill.innerHTML = `
      <div class="pill-header">
        <div class="pill-title-group">
          <span class="pill-icon">🤖</span>
          <span class="pill-label">Subagent:</span>
          <span class="pill-filename">${escapeHtml(subName)}</span>
        </div>
        <div class="pill-status-group">
          <span class="pill-badge ${badgeClass}">${badgeText}</span>
          <button class="pill-expand-modal-btn" title="Inspect Subagent">↗ Detailed View</button>
          <span class="pill-chevron">▼</span>
        </div>
      </div>
      <div class="pill-body">
        <div class="pill-meta-row">Type: ${escapeHtml(op.type_name || 'default')}${op.subagent_id ? ` | ID: ${escapeHtml(op.subagent_id.slice(0, 8))}` : ''}</div>
        ${op.prompt ? `<div class="pill-meta-row">Prompt: ${escapeHtml(op.prompt)}</div>` : ''}
        <pre class="pill-code-pre">${escapeHtml(op.output || "Subagent dispatched...")}</pre>
      </div>
    `;

    pill.querySelector(".pill-header").addEventListener("click", (e) => {
      if (e.target.closest(".pill-expand-modal-btn")) return;
      pill.classList.toggle("collapsed");
    });

    pill.querySelector(".pill-expand-modal-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      openDetailedModalTab("subagents", { subagentId: op.subagent_id, subagent: op });
    });

    container.appendChild(pill);
    return;
  }

  // 4b. Subagent Management Pill (manage_subagents: kill, kill_all, list)
  if (category === "subagent_op") {
    const action = (op.action || "manage").toLowerCase();
    const isKill = action === "kill" || action === "kill_all";
    const actionLabel = isKill ? (action === "kill_all" ? "Terminated All Subagents" : "Terminated Subagent") : "Listed Subagents";
    const actionIcon = isKill ? "🛑" : "🤖";
    const badgeClass = isKill ? "file-badge-deleted" : "badge-task-done";
    const badgeText = isKill ? "KILLED" : "LISTED";
    const targetSid = op.subagent_id || (Array.isArray(op.target_subagent_ids) ? op.target_subagent_ids[0] : (op.target_subagent_ids || ""));

    const subOpKey = op.op_key || `subop_${stepIdx}_${op.tool_call_index || 0}_${action}_${targetSid || "all"}`;
    let pill = container.querySelector('[data-op-key="' + CSS.escape(subOpKey) + '"]');

    if (pill) {
      pill.dataset.subagentOpKey = subOpKey;
      if (op.op_key) pill.dataset.opKey = op.op_key || "";
      const badge = pill.querySelector(".pill-badge");
      if (badge) {
        badge.className = `pill-badge ${badgeClass}`;
        badge.textContent = badgeText;
      }
      const pre = pill.querySelector(".pill-code-pre");
      if (pre && op.output) pre.textContent = op.output;
      return;
    }

    pill = document.createElement("div");
    pill.className = "op-pill subagent-op-pill collapsed";
    pill.dataset.subagentOpKey = subOpKey;
    if (op.op_key) pill.dataset.opKey = op.op_key || "";
    if (stepIdx !== undefined && stepIdx !== null) pill.dataset.stepIndex = stepIdx;
    if (targetSid) pill.dataset.targetSubagentId = targetSid;

    pill.innerHTML = `
      <div class="pill-header">
        <div class="pill-title-group">
          <span class="pill-icon">${actionIcon}</span>
          <span class="pill-label">${actionLabel}:</span>
          ${targetSid ? `<span class="pill-filename" title="${escapeHtml(targetSid)}">${escapeHtml(targetSid.slice(0, 8))}</span>` : ""}
        </div>
        <div class="pill-status-group">
          <span class="pill-badge ${badgeClass}">${badgeText}</span>
          ${targetSid ? `<button class="pill-expand-modal-btn" title="Open Dispatched Subagents View">↗ Detailed View</button>` : ""}
          <span class="pill-chevron">▼</span>
        </div>
      </div>
      <div class="pill-body">
        ${targetSid ? `<div class="pill-meta-row">Target Subagent: <code>${escapeHtml(targetSid)}</code></div>` : ""}
        <pre class="pill-code-pre">${escapeHtml(op.output || "Subagent operation executed.")}</pre>
      </div>
    `;

    pill.querySelector(".pill-header").addEventListener("click", (e) => {
      if (e.target.closest(".pill-expand-modal-btn")) return;
      pill.classList.toggle("collapsed");
    });

    const expandBtn = pill.querySelector(".pill-expand-modal-btn");
    if (expandBtn && targetSid) {
      expandBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        openDetailedModalTab("subagents", { subagentId: targetSid });
      });
    }

    container.appendChild(pill);
    return;
  }

  // 5. Thinking Process Pill
  if (category === "thinking") {
    const pill = document.createElement("div");
    pill.className = "op-pill thinking-pill collapsed";
    if (stepIdx !== undefined && stepIdx !== null) pill.dataset.stepIndex = stepIdx;
    pill.innerHTML = `
      <div class="pill-header">
        <div class="pill-title-group">
          <span class="pill-icon">💭</span>
          <span class="pill-label">Thought Process</span>
        </div>
        <span class="pill-chevron">▼</span>
      </div>
      <div class="pill-body thinking-content">${escapeHtml(op.thought || "")}</div>
    `;

    pill.querySelector(".pill-header").addEventListener("click", () => {
      pill.classList.toggle("collapsed");
    });

    container.appendChild(pill);
    return;
  }

  // 6. Generic Tool Pill
  if (category === "generic") {
    const genKey = op.op_key || `gen_${stepIdx}_${op.tool_call_index || 0}_${op.tool_name || "gen"}`;
    let pill = container.querySelector('[data-op-key="' + CSS.escape(genKey) + '"]');

    if (pill) {
      pill.dataset.genericKey = genKey;
      if (op.op_key) pill.dataset.opKey = op.op_key || "";
      const pre = pill.querySelector(".pill-code-pre");
      if (pre && op.output) pre.textContent = op.output;
      return;
    }

    pill = document.createElement("div");
    pill.className = "op-pill generic-pill collapsed";
    pill.dataset.genericKey = genKey;
    if (op.op_key) pill.dataset.opKey = op.op_key || "";
    if (stepIdx !== undefined && stepIdx !== null) pill.dataset.stepIndex = stepIdx;

    pill.innerHTML = `
      <div class="pill-header">
        <div class="pill-title-group">
          <span class="pill-icon">🔧</span>
          <span class="pill-label">Tool:</span>
          <span class="pill-filename">${escapeHtml(op.tool_name || "Tool")}</span>
        </div>
        <div class="pill-status-group">
          <span class="pill-badge badge-done">DONE</span>
          <span class="pill-chevron">▼</span>
        </div>
      </div>
      <div class="pill-body">
        ${op.target ? `<div class="pill-meta-row">Target: ${escapeHtml(op.target)}</div>` : ""}
        <pre class="pill-code-pre">${escapeHtml(op.output || op.description || "")}</pre>
      </div>
    `;

    pill.querySelector(".pill-header").addEventListener("click", () => {
      pill.classList.toggle("collapsed");
    });

    container.appendChild(pill);
  }
}

// ════════ TEXT CLEANING & MARKDOWN RENDERING ════════
function cleanLineDialogue(line) {
  let text = line;
  // 1. Remove emotion tags like {neutral} or {teasing: 0.6, tsundere: 0.4}
  text = text.replace(/\{\s*(?:neutral|tsundere|teasing|flustered|angry|warm|serious|surprised|distressed|tsundere_neutral|back_turned|curious|thinking|embarrassed)(?::\s*[\d.]+)?(?:\s*,\s*(?:neutral|tsundere|teasing|flustered|angry|warm|serious|surprised|distressed|tsundere_neutral|back_turned|curious|thinking|embarrassed)(?::\s*[\d.]+)?)*\s*\}\s*/gi, "");

  // 2. Remove [JA: ...] blocks (both closed and unclosed at end of stream)
  text = text.replace(/\[JA:[\s\S]*?(?:\]|$)\s*/gi, "");

  // 3. Unwrap [EN: ...] blocks:
  // Closed [EN: ...] blocks
  text = text.replace(/\[EN:\s*([\s\S]*?)\](?=\s*(?:\{|\[JA:|\[EN:|$|\n))/gi, "$1");
  // In-flight [EN: ...] at end of stream
  text = text.replace(/\[EN:\s*([\s\S]*)$/gi, "$1");

  return text;
}

function extractCleanVisualText(rawText) {
  if (!rawText) return "";
  const lines = rawText.split("\n");
  const outputLines = [];
  let inCodeBlock = false;
  let openingFenceLength = 0;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fenceMatch = line.match(/^[ \t]*(`{3,})/);

    if (!inCodeBlock) {
      if (fenceMatch) {
        inCodeBlock = true;
        openingFenceLength = fenceMatch[1].length;
        outputLines.push(line);
      } else {
        outputLines.push(cleanLineDialogue(line));
      }
    } else {
      outputLines.push(line);
      if (fenceMatch && fenceMatch[1].length === openingFenceLength) {
        inCodeBlock = false;
        openingFenceLength = 0;
      }
    }
  }
  return outputLines.join("\n").trim();
}

function renderMarkdown(rawText) {
  const clean = extractCleanVisualText(rawText);
  if (!clean) return "";

  try {
    const rawHtml = marked.parse(clean);
    const parser = new DOMParser();
    const doc = parser.parseFromString(rawHtml, "text/html");

    // Local image URLs resolution (ss-16.png bug fix)
    doc.querySelectorAll("img").forEach((img) => {
      const src = img.getAttribute("src") || "";
      if (src.startsWith("file://") || /^[a-zA-Z]:[/\\]/.test(src)) {
        let cleanPath = src.replace(/^file:\/\/\/?/, "");
        if (/^\/[a-zA-Z]:/.test(cleanPath)) cleanPath = cleanPath.slice(1);
        cleanPath = decodeURIComponent(cleanPath);

        const cachedUrl =
          localImageDataCache.get(cleanPath) ||
          localImageDataCache.get(src) ||
          localImageDataCache.get(cleanPath.replace(/\\/g, "/")) ||
          localImageDataCache.get(cleanPath.replace(/\//g, "\\"));

        if (cachedUrl) {
          img.src = cachedUrl;
        } else {
          img.dataset.rawPath = cleanPath;
          img.src = "";
          if (!pendingImageRequests.has(cleanPath)) {
            pendingImageRequests.add(cleanPath);
            sendWs({
              type: "get_image_data_url",
              path: cleanPath,
              req_id: String(Date.now()),
            });
          }
        }
        img.dataset.rawPath = cleanPath;
      }
      img.classList.add("chat-image-thumb");
    });

    // Wrap code blocks with styling and copy buttons
    doc.querySelectorAll("pre").forEach((pre) => {
      const codeEl = pre.querySelector("code");
      const codeText = codeEl ? codeEl.textContent : pre.textContent;
      if (!codeText || !codeText.trim()) {
        pre.remove();
        return;
      }

      let lang = "code";
      if (codeEl && codeEl.className) {
        const m = codeEl.className.match(/language-([a-zA-Z0-9_\-]+)/);
        if (m) lang = m[1];
      }

      const wrapper = document.createElement("div");
      wrapper.className = "code-container";
      wrapper.innerHTML = `
        <div class="code-header">
          <span class="code-lang">${escapeHtml(lang)}</span>
          <button class="copy-btn">Copy Code</button>
        </div>
        <pre class="code-content"><code>${escapeHtml(codeText)}</code></pre>
      `;

      const copyBtn = wrapper.querySelector(".copy-btn");
      copyBtn.onclick = () => {
        navigator.clipboard.writeText(codeText).then(() => {
          copyBtn.textContent = "Copied!";
          setTimeout(() => (copyBtn.textContent = "Copy Code"), 2000);
        });
      };

      pre.replaceWith(wrapper);
    });

    return doc.body.innerHTML;
  } catch (err) {
    return `<p>${escapeHtml(clean).replace(/\n/g, "<br>")}</p>`;
  }
}

function renderCleanResponse(container, rawText) {
  const html = renderMarkdown(rawText);
  if (!html) {
    container.innerHTML = `<span style="color: var(--text-dim); font-style: italic;">...</span>`;
    return;
  }
  container.innerHTML = html;
}

// ════════ CHAT FEED CARDS ════════
function renderUserMessageWithMentions(text) {
  if (!text) return "";
  const lines = text.split("\n");
  const result = [];
  let inCodeBlock = false;
  let openingFenceLength = 0;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fenceMatch = line.match(/^[ \t]*(`{3,})/);
    if (!inCodeBlock) {
      if (fenceMatch) {
        inCodeBlock = true;
        openingFenceLength = fenceMatch[1].length;
        result.push(line);
      } else {
        result.push(
          line.replace(/@([A-Za-z0-9_\-./\\@]+)/g, (match, candidate) => {
            const candNorm = candidate.replace(/\\/g, "/").toLowerCase();
            const candClean = candNorm.replace(/\/+$/, "");
            const exists = currentWorkspaceFiles.some((f) => {
              const fNorm = f.replace(/\\/g, "/").toLowerCase();
              const fClean = fNorm.replace(/\/+$/, "");
              return (
                fClean === candClean ||
                fClean.endsWith("/" + candClean)
              );
            });
            if (exists) {
              return `<span class="mentioned-file">@${escapeHtml(candidate)}</span>`;
            }
            return match;
          })
        );
      }
    } else {
      result.push(line);
      if (fenceMatch && fenceMatch[1].length === openingFenceLength) {
        inCodeBlock = false;
        openingFenceLength = 0;
      }
    }
  }
  return result.join("\n");
}

function appendUserMessage(text, stepIndex = null, timestamp = null) {
  const timeStr = timestamp ? formatTimestamp(timestamp) : getCurrentTimeStr();
  const card = document.createElement("div");
  card.className = "msg-card user";
  if (stepIndex !== null && stepIndex !== undefined) {
    card.dataset.stepIndex = stepIndex;
  }
  card.dataset.userText = text;

  const formattedText = renderUserMessageWithMentions(text);
  const bodyHtml = renderMarkdown(formattedText) || `<p>${escapeHtml(text)}</p>`;

  card.innerHTML = `
    <div class="msg-header">
      <span class="speaker-tag">User</span>
      <span class="timestamp">${timeStr}</span>
      <div class="msg-actions">
        <button class="copy-prompt-btn" title="Copy prompt">${COPY_ICON_SVG}</button>
        <button class="edit-msg-btn" title="Edit message and rewind conversation">${EDIT_ICON_SVG}</button>
      </div>
    </div>
    <div class="msg-body">${bodyHtml}</div>
  `;

  const copyPromptBtn = card.querySelector(".copy-prompt-btn");
  if (copyPromptBtn) {
    copyPromptBtn.addEventListener("click", () => {
      navigator.clipboard.writeText(text).then(() => {
        copyPromptBtn.innerHTML = CHECK_ICON_SVG;
        copyPromptBtn.title = "Copied!";
        setTimeout(() => {
          copyPromptBtn.innerHTML = COPY_ICON_SVG;
          copyPromptBtn.title = "Copy prompt";
        }, 2000);
      });
    });
  }

  const editBtn = card.querySelector(".edit-msg-btn");
  editBtn.addEventListener("click", async () => {
    const rawText = card.dataset.userText || text;
    const lines = rawText.split("\n");
    const attachmentLines = [];
    const promptLines = [];
    let inHeader = true;

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (inHeader) {
        if (/^!?\[[^\]]*\]\((?:file:\/\/\/|[a-zA-Z]:[/\\])[^\)]+\)\s*$/i.test(line.trim())) {
          attachmentLines.push(line.trim());
          continue;
        } else if (line.trim() === "" && attachmentLines.length > 0) {
          continue;
        } else {
          inHeader = false;
        }
      }
      promptLines.push(line);
    }

    const initialPrompt = promptLines.join("\n").trim();
    const newText = await showCustomPrompt(
      "EDIT MESSAGE // REWIND",
      "Edit your previous message to rewind the timeline:",
      initialPrompt,
      true
    );

    if (newText && newText.trim() && newText.trim() !== initialPrompt) {
      let targetStep = card.dataset.stepIndex;
      const fullNewText = attachmentLines.length > 0
        ? `${attachmentLines.join("\n")}\n\n${newText.trim()}`
        : newText.trim();

      sendWs({
        type: "edit_message",
        step_index: targetStep !== undefined && targetStep !== null ? parseInt(targetStep, 10) : null,
        orig_text: rawText,
        new_text: fullNewText,
      });
      setGeneratingState(true);
    }
  });

  chatFeed.appendChild(card);
  scrollChatToBottom();
}

function createAmadeusStreamCard() {
  const timeStr = getCurrentTimeStr();
  const card = document.createElement("div");
  card.className = "msg-card amadeus";

  card.innerHTML = `
    <div class="msg-header">
      <span class="speaker-tag">Amadeus</span>
      <span class="timestamp">${timeStr}</span>
      <button class="copy-response-btn" title="Copy response" style="display: none;">${COPY_ICON_SVG}</button>
    </div>
    <div class="turn-timeline-flow"></div>
  `;

  const timelineFlow = card.querySelector(".turn-timeline-flow");

  // Wire Copy Response button
  const copyBtn = card.querySelector(".copy-response-btn");
  copyBtn.addEventListener("click", () => {
    let textToCopy = card.dataset.fullText || "";
    if (!textToCopy) {
      const segments = Array.from(card.querySelectorAll(".speech-segment"));
      textToCopy = segments.map((s) => s.innerText.trim()).filter(Boolean).join("\n\n");
    }
    if (!textToCopy) return;
    navigator.clipboard.writeText(textToCopy).then(() => {
      copyBtn.innerHTML = CHECK_ICON_SVG;
      copyBtn.title = "Copied!";
      setTimeout(() => {
        copyBtn.innerHTML = COPY_ICON_SVG;
        copyBtn.title = "Copy response";
      }, 2000);
    });
  });

  chatFeed.appendChild(card);
  scrollChatToBottom();

  // Instantly instantiate the initial Thinking indicator with animated dots!
  const thinkingPill = getOrCreateActiveThinkingPill(timelineFlow);

  return {
    card,
    timelineFlow,
    thinkingPill,
    operationsFlow: timelineFlow, // backward compatibility
    msgBody: null,
  };
}

function appendHistoricalAmadeusMessage(rawContent, thoughtText = "", timestamp = null, operations = [], stepIndex = null) {
  const timeStr = timestamp ? formatTimestamp(timestamp) : getCurrentTimeStr();
  const card = document.createElement("div");
  card.className = "msg-card amadeus";
  if (stepIndex !== null && stepIndex !== undefined) {
    card.dataset.stepIndex = stepIndex;
  }
  card.dataset.fullText = extractCleanVisualText(rawContent);

  const hasSpeech = rawContent && rawContent.trim();
  card.innerHTML = `
    <div class="msg-header">
      <span class="speaker-tag">Amadeus</span>
      <span class="timestamp">${timeStr}</span>
      <button class="copy-response-btn" title="Copy response" style="display: ${hasSpeech ? 'inline-flex' : 'none'};">${COPY_ICON_SVG}</button>
    </div>
    <div class="turn-timeline-flow"></div>
  `;

  const timelineFlow = card.querySelector(".turn-timeline-flow");

  if (operations && operations.length > 0) {
    for (const op of operations) {
      try {
        renderOrUpdateOperationPill(timelineFlow, op);
      } catch (e) {
        console.warn("[History] Error rendering operation pill:", e);
      }
    }
  }

  const hasThinkingPill = operations && operations.some((op) => op.category === "thinking");
  if (!hasThinkingPill && thoughtText && thoughtText.trim()) {
    try {
      renderOrUpdateOperationPill(timelineFlow, {
        category: "thinking",
        thought: thoughtText.trim(),
      });
    } catch (e) {
      console.warn("[History] Error rendering thought pill:", e);
    }
  }

  if (hasSpeech) {
    const speechSegment = document.createElement("div");
    speechSegment.className = "speech-segment msg-body";
    renderCleanResponse(speechSegment, rawContent);
    timelineFlow.appendChild(speechSegment);
  }

  const copyBtn = card.querySelector(".copy-response-btn");
  copyBtn.addEventListener("click", () => {
    let textToCopy = card.dataset.fullText || "";
    if (!textToCopy) {
      const segments = Array.from(card.querySelectorAll(".speech-segment"));
      textToCopy = segments.map((s) => s.innerText.trim()).filter(Boolean).join("\n\n");
    }
    if (!textToCopy) return;
    navigator.clipboard.writeText(textToCopy).then(() => {
      copyBtn.innerHTML = CHECK_ICON_SVG;
      copyBtn.title = "Copied!";
      setTimeout(() => {
        copyBtn.innerHTML = COPY_ICON_SVG;
        copyBtn.title = "Copy response";
      }, 2000);
    });
  });

  chatFeed.appendChild(card);
}

function loadConversationIntoChat(history, convoId, title) {
  if (convoId) currentActiveConvoId = convoId;
  setGeneratingState(false);
  chatFeed.innerHTML = "";
  sessionDiffsCache = [];
  sessionCommandsCache = [];
  sessionTasksCache = [];
  sessionSubagentsCache = [];

  for (const msg of history) {
    try {
      if (msg.role === "user") {
        appendUserMessage(msg.content, msg.step_index, msg.created_at);
      } else {
        const ops = msg.operations || [];
        for (const op of ops) {
          try {
            registerToolInSessionCaches(op);
          } catch (e) {
            console.warn("[History] Error registering tool in cache:", e);
          }
        }
        appendHistoricalAmadeusMessage(msg.content, msg.thought, msg.created_at, ops, msg.step_index);
      }
    } catch (err) {
      console.error("[History] Error rendering message in conversation:", err);
    }
  }
  updateModalBadges();
  if (pendingSearchScrollStep !== null && pendingSearchScrollStep !== undefined) {
    const targetStep = pendingSearchScrollStep;
    pendingSearchScrollStep = null;
    setTimeout(() => {
      scrollToMessageStep(targetStep);
    }, 120);
  } else {
    scrollChatToBottom();
  }

  if (title && sessionHeading) {
    const cleanTitle = title.replace(/^\[AMADEUS\]\s*/i, "").trim() || "Active Conversation";
    sessionHeading.textContent = cleanTitle;
  }
  if (currentActiveConvoId) {
    document.querySelectorAll(".history-item").forEach((el) => {
      if (el.dataset.id === currentActiveConvoId) el.classList.add("active");
      else el.classList.remove("active");
    });
  }

  // Instantly sync authoritative subagents and background tasks from backend
  sendWs({ type: "request_session_subagents", conversation_id: currentActiveConvoId });
  sendWs({ type: "request_session_tasks", conversation_id: currentActiveConvoId });
}

function appendWelcomeMessage() {
  const card = document.createElement("div");
  card.className = "msg-card amadeus";
  const defaultText =
    "What are you staring at? If you have something worth my time, say it quickly. I have research to finish.";
  card.dataset.fullText = defaultText;

  card.innerHTML = `
    <div class="msg-header">
      <span class="speaker-tag">Amadeus</span>
      <span class="timestamp">${getCurrentTimeStr()}</span>
      <button class="copy-response-btn" title="Copy response">${COPY_ICON_SVG}</button>
    </div>
    <div class="turn-timeline-flow">
      <div class="speech-segment msg-body">${defaultText}</div>
    </div>
  `;

  const copyBtn = card.querySelector(".copy-response-btn");
  copyBtn.addEventListener("click", () => {
    navigator.clipboard.writeText(defaultText).then(() => {
      copyBtn.innerHTML = CHECK_ICON_SVG;
      copyBtn.title = "Copied!";
      setTimeout(() => {
        copyBtn.innerHTML = COPY_ICON_SVG;
        copyBtn.title = "Copy response";
      }, 2000);
    });
  });

  chatFeed.appendChild(card);
}

function scrollChatToBottom() {
  chatFeed.scrollTop = chatFeed.scrollHeight;
}

// ════════ ATTACHMENTS & LIGHTBOX HELPERS ════════
const TEXT_EXTS = new Set([
  "txt", "md", "py", "js", "ts", "jsx", "tsx", "rs", "c", "cpp", "h", "hpp",
  "json", "csv", "tsv", "log", "xml", "yaml", "yml", "toml", "ini", "sh",
  "bat", "ps1", "sql", "html", "css"
]);
const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "webp", "bmp", "gif"]);
const DOC_EXTS = new Set(["pdf", "docx", "pptx", "xlsx"]);
const AUDIO_EXTS = new Set(["mp3", "wav", "ogg", "flac", "m4a", "aac", "wma"]);
const VIDEO_EXTS = new Set(["mp4", "webm", "mkv", "avi", "mov", "wmv"]);

function getFileExt(fileName) {
  if (!fileName || !fileName.includes(".")) return "";
  return fileName.split(".").pop().toLowerCase();
}

function getFileBadgeEmoji(fileName) {
  const ext = getFileExt(fileName);
  const codeExts = ["py", "js", "ts", "html", "css", "json", "rs", "c", "cpp", "h", "hpp", "jsx", "tsx", "sql", "sh", "bat", "ps1", "yaml", "yml", "toml", "ini", "xml"];
  const sheetExts = ["csv", "xlsx", "tsv"];
  const docExts = ["pdf", "docx", "pptx"];
  const audioExts = ["mp3", "wav", "ogg", "flac", "m4a", "aac", "wma"];
  const videoExts = ["mp4", "webm", "mkv", "avi", "mov", "wmv"];

  if (codeExts.includes(ext)) return "💻";
  if (sheetExts.includes(ext)) return "📊";
  if (docExts.includes(ext)) return "📑";
  if (audioExts.includes(ext)) return "🎵";
  if (videoExts.includes(ext)) return "🎬";
  return "📄";
}

function formatFileSize(bytes) {
  if (!bytes || bytes <= 0) return "0 B";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function formatTokens(tokens) {
  if (!tokens || tokens <= 0) return "0 tokens";
  if (tokens >= 1000) {
    const k = (tokens / 1000).toFixed(1);
    return `${k}k tokens`;
  }
  return `${tokens} tokens`;
}

function removeAttachment(attId) {
  const idx = stagedAttachments.findIndex((a) => a.id === attId);
  if (idx === -1) return;
  const att = stagedAttachments[idx];
  if (att.previewUrl && att.previewUrl.startsWith("blob:")) {
    URL.revokeObjectURL(att.previewUrl);
  }
  if (att.path) {
    sendWs({ type: "unstage_attachment", path: att.path });
  }
  stagedAttachments.splice(idx, 1);
  renderStagingTray();
}

function validateAndStageFiles(files) {
  if (!files || files.length === 0) return;

  const fileArr = Array.from(files);

  // Folder guard: check for directories
  for (let i = 0; i < fileArr.length; i++) {
    const f = fileArr[i];
    if (f.webkitRelativePath && f.webkitRelativePath.includes("/")) {
      showCustomModal({
        title: "FOLDER NOT SUPPORTED",
        message: "Folders cannot be attached directly. Please attach individual files or a .zip archive.",
      });
      return;
    }
  }

  // 1. Max 5 files total check
  if (stagedAttachments.length + fileArr.length > 5) {
    showCustomModal({
      title: "ATTACHMENT LIMIT",
      message: "Maximum 5 files can be attached at once.",
    });
    return;
  }

  // Calculate current totals
  let totalTextBytes = stagedAttachments.reduce((sum, att) => {
    const ext = getFileExt(att.name);
    return TEXT_EXTS.has(ext) ? sum + (att.size || 0) : sum;
  }, 0);

  let totalTokens = stagedAttachments.reduce((sum, att) => sum + (att.tokens || 0), 0);

  const validFiles = [];

  for (let i = 0; i < fileArr.length; i++) {
    const file = fileArr[i];
    const ext = getFileExt(file.name);

    // 1. Byte sanity check: max 15 MB per single file
    if (file.size > 15 * 1024 * 1024) {
      showCustomModal({
        title: "FILE TOO LARGE",
        message: `'${file.name}' exceeds the 15 MB system limit.`,
      });
      return;
    }

    let estTokens = 0;
    let isImage = false;

    // 2. Token Estimation & Category Caps
    if (TEXT_EXTS.has(ext)) {
      if (file.size > 250 * 1024) {
        showCustomModal({
          title: "FILE TOO LARGE",
          message: `Text file '${file.name}' exceeds 250 KB. Large datasets should be inspected using tools.`,
        });
        return;
      }
      totalTextBytes += file.size;
      if (totalTextBytes > 280 * 1024) {
        showCustomModal({
          title: "TEXT LIMIT EXCEEDED",
          message: "Combined text/code files exceed the 280 KB safety ceiling (~70k tokens).",
        });
        return;
      }
      estTokens = Math.ceil(file.size / 4);
    } else if (IMAGE_EXTS.has(ext) || (file.type && file.type.startsWith("image/"))) {
      if (file.size > 10 * 1024 * 1024) {
        showCustomModal({
          title: "IMAGE TOO LARGE",
          message: `Image '${file.name}' exceeds 10 MB.`,
        });
        return;
      }
      isImage = true;
      estTokens = 258;
    } else if (AUDIO_EXTS.has(ext) || (file.type && file.type.startsWith("audio/"))) {
      if (file.size > 25 * 1024 * 1024) {
        showCustomModal({
          title: "AUDIO TOO LARGE",
          message: `Audio file '${file.name}' exceeds 25 MB limit.`,
        });
        return;
      }
      estTokens = Math.ceil((file.size / 1024) * 2);
    } else if (VIDEO_EXTS.has(ext) || (file.type && file.type.startsWith("video/"))) {
      if (file.size > 50 * 1024 * 1024) {
        showCustomModal({
          title: "VIDEO TOO LARGE",
          message: `Video file '${file.name}' exceeds 50 MB limit.`,
        });
        return;
      }
      estTokens = Math.ceil((file.size / 1024) * 2.1);
    } else if (DOC_EXTS.has(ext)) {
      estTokens = Math.ceil((file.size / 1024) * 2.5);
    } else {
      showCustomModal({
        title: "UNSUPPORTED FILE TYPE",
        message: `Unsupported file type: .${ext || "unknown"}`,
      });
      return;
    }

    totalTokens += estTokens;
    if (totalTokens > 75000) {
      showCustomModal({
        title: "TOKEN CEILING EXCEEDED",
        message: `Attachments exceed the 75,000 token limit (Estimated: ${totalTokens.toLocaleString()} tokens). Please attach fewer or smaller files.`,
      });
      return;
    }

    validFiles.push({ file, ext, isImage, estTokens });
  }

  // All files in batch validated: stage immediately
  validFiles.forEach(({ file, ext, isImage, estTokens }) => {
    const id = "att_" + Date.now() + "_" + Math.random().toString(36).substring(2, 8);
    const previewUrl = isImage ? URL.createObjectURL(file) : null;
    const attObj = {
      id,
      file,
      name: file.name || (isImage ? "image.png" : "file.bin"),
      size: file.size,
      type: file.type || "",
      isImage,
      previewUrl,
      path: "",
      file_uri: "",
      tokens: estTokens,
      status: "staging",
    };
    stagedAttachments.push(attObj);

    // Convert file to base64 via FileReader & send stage_attachment packet
    const reader = new FileReader();
    reader.onload = () => {
      const resStr = String(reader.result || "");
      const b64 = resStr.includes(",") ? resStr.split(",")[1] : resStr;
      sendWs({
        type: "stage_attachment",
        id: attObj.id,
        name: attObj.name,
        mime_type: attObj.type,
        is_image: attObj.isImage,
        size: attObj.size,
        base64_data: b64,
      });
    };
    reader.onerror = (err) => {
      console.error("[Attachment] FileReader error:", err);
    };
    reader.readAsDataURL(file);
  });

  renderStagingTray();
}

function renderStagingTray() {
  const tray = document.getElementById("attachmentStagingTray");
  const itemsContainer = document.getElementById("stagingItemsContainer");
  const tokenBadge = document.getElementById("stagingTokenCount");
  const sendBtn = document.getElementById("sendBtn");
  if (!tray) return;

  if (stagedAttachments.length === 0) {
    tray.style.display = "none";
    if (itemsContainer) itemsContainer.innerHTML = "";
    if (tokenBadge) {
      tokenBadge.textContent = "Estimated Attachment Tokens: 0 / 75,000";
      tokenBadge.style.display = "";
      tokenBadge.style.color = "";
    }
    if (sendBtn && !isGenerating) {
      sendBtn.classList.remove("disabled-staging");
      sendBtn.title = "Send message (Enter)";
    }
    return;
  }

  tray.style.display = "flex";
  const targetContainer = itemsContainer || tray;
  targetContainer.innerHTML = "";

  const anyStaging = stagedAttachments.some((a) => a.status === "staging");

  stagedAttachments.forEach((att) => {
    const isStaging = att.status === "staging";

    if (att.isImage) {
      const card = document.createElement("div");
      card.className = `staged-image-card ${isStaging ? "is-staging" : ""}`;
      card.title = isStaging
        ? `Staging ${escapeHtml(att.name)}...`
        : `${escapeHtml(att.name)} (${formatFileSize(att.size)} • ~${formatTokens(att.tokens)}) - Click to preview`;

      if (isStaging) {
        card.innerHTML = `
          <span class="staged-spinner" title="Staging..."></span>
          <button class="attachment-remove-btn" title="Remove attachment" type="button">✕</button>
        `;
      } else {
        const imgSrc = att.previewUrl || localImageDataCache.get(att.path) || (att.path ? convertFileSrc(att.path) : "");
        card.innerHTML = `
          <img src="${imgSrc}" alt="${escapeHtml(att.name)}" />
          <button class="attachment-remove-btn" title="Remove attachment" type="button">✕</button>
        `;
        // Preview in lightbox
        card.addEventListener("click", () => {
          if (imgSrc) openLightbox(imgSrc, att.name, att.path);
        });
      }

      // Remove button (always active even during staging)
      const removeBtn = card.querySelector(".attachment-remove-btn");
      removeBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        removeAttachment(att.id);
      });
      targetContainer.appendChild(card);
    } else {
      const badge = document.createElement("div");
      badge.className = "staged-file-badge";
      const emoji = getFileBadgeEmoji(att.name);

      let metaHtml = "";
      if (isStaging) {
        metaHtml = `<span class="staged-file-meta"><span class="staged-spinner"></span> Staging...</span>`;
      } else {
        metaHtml = `<span class="staged-file-meta">${formatFileSize(att.size)} • ~${formatTokens(att.tokens)}</span>`;
      }

      badge.innerHTML = `
        <span class="staged-file-icon">${emoji}</span>
        <span class="staged-file-name" title="${escapeHtml(att.name)}">${escapeHtml(att.name)}</span>
        ${metaHtml}
        <button class="attachment-remove-btn" title="Remove attachment" type="button">✕</button>
      `;
      const removeBtn = badge.querySelector(".attachment-remove-btn");
      removeBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        removeAttachment(att.id);
      });
      targetContainer.appendChild(badge);
    }
  });

  // Calculate total tokens strictly from staged attachments
  const totalTokens = stagedAttachments.reduce((sum, att) => sum + (att.tokens || 0), 0);
  if (tokenBadge) {
    if (anyStaging) {
      // Do NOT display the token pill until attachment_staged arrives from the server
      tokenBadge.style.display = "none";
    } else {
      tokenBadge.style.display = "";
      tokenBadge.textContent = `Estimated Attachment Tokens: ${totalTokens.toLocaleString()} / 75,000`;
      if (totalTokens > 75000) {
        tokenBadge.style.color = "#ef4444";
      } else {
        tokenBadge.style.color = "";
      }
    }
  }

  if (sendBtn && !isGenerating) {
    if (anyStaging) {
      sendBtn.classList.add("disabled-staging");
      sendBtn.title = "Staging attachments...";
    } else {
      sendBtn.classList.remove("disabled-staging");
      sendBtn.title = "Send message (Enter)";
    }
  }
}

function openLightbox(src, caption = "", rawPath = "") {
  const modal = document.getElementById("imageLightboxModal");
  const img = document.getElementById("lightboxImg");
  const cap = document.getElementById("lightboxCaption");
  const revealBtn = document.getElementById("lightboxRevealBtn");
  if (!modal || !img) return;

  img.src = src;
  if (cap) cap.textContent = caption || "";
  if (revealBtn) {
    if (rawPath) {
      revealBtn.style.display = "inline-flex";
      revealBtn.dataset.rawPath = rawPath;
    } else {
      revealBtn.style.display = "none";
      delete revealBtn.dataset.rawPath;
    }
  }
  modal.style.display = "flex";
}

function closeLightbox() {
  const modal = document.getElementById("imageLightboxModal");
  const img = document.getElementById("lightboxImg");
  const revealBtn = document.getElementById("lightboxRevealBtn");
  if (!modal) return;
  modal.style.display = "none";
  if (img) img.src = "";
  if (revealBtn) {
    revealBtn.style.display = "none";
    delete revealBtn.dataset.rawPath;
  }
}

function setupLightbox() {
  const closeBtn = document.getElementById("lightboxCloseBtn");
  if (closeBtn) {
    closeBtn.addEventListener("click", closeLightbox);
  }
  const modal = document.getElementById("imageLightboxModal");
  if (modal) {
    const backdrop = modal.querySelector(".lightbox-backdrop");
    if (backdrop) {
      backdrop.addEventListener("click", closeLightbox);
    }
  }
  const revealBtn = document.getElementById("lightboxRevealBtn");
  if (revealBtn) {
    revealBtn.addEventListener("click", () => {
      const p = revealBtn.dataset.rawPath;
      if (p) {
        sendWs({ type: "reveal_in_explorer", path: p });
      }
    });
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      const modal = document.getElementById("imageLightboxModal");
      if (modal && modal.style.display !== "none") {
        closeLightbox();
      }
    }
  });
}

function setupGlobalClickHandlers() {
  document.addEventListener("click", (e) => {
    // A. Lightbox Image click
    const chatImg = e.target.closest("img.chat-image-thumb");
    if (chatImg) {
      const rawPath = chatImg.dataset.rawPath || "";
      let src = chatImg.src;
      if (!src || src === window.location.href || src === window.location.origin + "/") {
        if (rawPath) {
          src = localImageDataCache.get(rawPath) ||
                localImageDataCache.get(rawPath.replace(/\\/g, "/")) ||
                localImageDataCache.get(rawPath.replace(/\//g, "\\")) || "";
        }
      }
      if (src && (src.startsWith("data:") || src.startsWith("blob:") || src.startsWith("http:") || src.startsWith("https:"))) {
        openLightbox(src, chatImg.alt, rawPath);
      }
      return;
    }

    // B. Links
    const link = e.target.closest("a");
    if (!link) return;

    const rawHref = link.getAttribute("href") || "";
    if (!rawHref || rawHref.startsWith("#")) return;

    // 1. Web URLs
    if (/^https?:\/\//i.test(rawHref)) {
      e.preventDefault();
      sendWs({ type: "open_url", url: rawHref });
      return;
    }

    // 2. Local File Links -> Reveal in File Explorer
    if (rawHref.startsWith("file://") || /^[a-zA-Z]:[/\\]/.test(rawHref)) {
      e.preventDefault();
      let cleanPath = rawHref.replace(/^file:\/\/\/?/, "");
      if (/^\/[a-zA-Z]:/.test(cleanPath)) cleanPath = cleanPath.slice(1);
      cleanPath = decodeURIComponent(cleanPath);
      sendWs({ type: "reveal_in_explorer", path: cleanPath });
      return;
    }
  });
}

// ════════ FILE MENTION AUTOCOMPLETE ════════
let lastWorkspaceFilesFetch = 0;

function updateMentionPopup(targetInput = chatInput) {
  const isModal = targetInput === amadeusModalTextarea;
  const popupEl = isModal ? modalMentionPopup : fileMentionPopup;
  const listEl = isModal ? modalMentionList : fileMentionList;
  const countEl = isModal ? modalMentionCount : fileMentionCount;

  if (!targetInput || !popupEl || !listEl || !countEl) return;

  const cursor = targetInput.selectionStart;
  const textBefore = targetInput.value.slice(0, cursor);

  // Determine if text before cursor has an active @ trigger: /(?:^|\s)@([^\s]*)$/
  const match = textBefore.match(/(?:^|\s)@([^\s]*)$/);
  if (!match) {
    hideMentionPopup(popupEl);
    return;
  }

  const query = match[1];
  activeMentionQuery = query;

  // Auto-refresh workspace files and subfolders on @ trigger (throttled to max once per 1000ms, or immediate on fresh @)
  const now = Date.now();
  if (now - lastWorkspaceFilesFetch > 1000 || (query === "" && now - lastWorkspaceFilesFetch > 300)) {
    lastWorkspaceFilesFetch = now;
    sendWs({ type: "get_workspace_files" });
  }

  // Filter workspace files and subfolders using case-insensitive substring matching
  const queryLower = query.toLowerCase();
  const matches = currentWorkspaceFiles.filter((f) =>
    f.toLowerCase().includes(queryLower)
  );

  // If the workspace has NO files at all (e.g. Default Workspace or an empty directory):
  if (currentWorkspaceFiles.length === 0) {
    renderEmptyMentionNotice(listEl, countEl);
    popupEl.classList.remove("hidden");
    return;
  }

  if (matches.length === 0) {
    hideMentionPopup(popupEl);
    return;
  }

  renderMentionList(matches, listEl, countEl, targetInput, popupEl);
  popupEl.classList.remove("hidden");
}

function hideMentionPopup(targetPopup = null) {
  if (targetPopup) {
    targetPopup.classList.add("hidden");
  } else {
    if (fileMentionPopup) fileMentionPopup.classList.add("hidden");
    if (modalMentionPopup) modalMentionPopup.classList.add("hidden");
  }
  activeMentionQuery = null;
  selectedMentionIndex = 0;
}

function renderEmptyMentionNotice(listEl = fileMentionList, countEl = fileMentionCount) {
  if (!listEl || !countEl) return;
  countEl.textContent = "0 items";
  listEl.innerHTML = `
    <div class="file-mention-empty">
      <span class="file-mention-item-icon">📁</span>
      <span class="file-mention-empty-text">Workspace is empty (or has excluded folders)</span>
    </div>
  `;
}

function renderMentionList(matches, listEl = fileMentionList, countEl = fileMentionCount, targetInput = chatInput, popupEl = fileMentionPopup) {
  if (!listEl || !countEl) return;

  const count = matches.length;
  countEl.textContent = `${count} item${count === 1 ? "" : "s"}`;
  listEl.innerHTML = "";

  if (selectedMentionIndex >= matches.length) {
    selectedMentionIndex = 0;
  }

  matches.forEach((file, index) => {
    const item = document.createElement("div");
    item.className = "file-mention-item" + (index === selectedMentionIndex ? " selected" : "");
    item.setAttribute("data-file", file);
    item.setAttribute("title", file);

    const isFolder = file.endsWith("/");
    const icon = isFolder ? "📁" : "📄";

    item.innerHTML = `
      <span class="file-mention-item-icon">${icon}</span>
      <span class="file-mention-item-name">${escapeHtml(file)}</span>
    `;

    item.addEventListener("mousedown", (e) => {
      e.preventDefault(); // Prevents input blur
    });

    item.addEventListener("click", (e) => {
      e.preventDefault();
      selectMentionFile(file, targetInput, popupEl);
    });

    item.addEventListener("mouseenter", () => {
      selectedMentionIndex = index;
      const allItems = listEl.querySelectorAll(".file-mention-item");
      allItems.forEach((el, i) => {
        el.classList.toggle("selected", i === index);
      });
    });

    listEl.appendChild(item);
  });

  scrollSelectedMentionIntoView(listEl);
}

function scrollSelectedMentionIntoView(listEl = fileMentionList) {
  if (!listEl) return;
  const selectedEl = listEl.querySelector(".file-mention-item.selected");
  if (selectedEl) {
    selectedEl.scrollIntoView({ block: "nearest" });
  }
}

function highlightMentionItem(items, newIndex) {
  items.forEach((item, idx) => {
    item.classList.toggle("selected", idx === newIndex);
  });
  const firstItem = items[0];
  const listEl = firstItem ? firstItem.closest(".file-mention-list") : fileMentionList;
  scrollSelectedMentionIntoView(listEl);
}

function selectMentionFile(file, targetInput = chatInput, popupEl = fileMentionPopup) {
  if (!targetInput) return;

  const cursor = targetInput.selectionStart;
  const textBefore = targetInput.value.slice(0, cursor);
  const textAfter = targetInput.value.slice(cursor);

  const match = textBefore.match(/(?:^|\s)@([^\s]*)$/);
  if (!match) {
    hideMentionPopup(popupEl);
    return;
  }

  const queryLen = match[1] !== undefined ? match[1].length : 0;
  const triggerIndex = textBefore.length - queryLen - 1;
  const prefix = textBefore.slice(0, triggerIndex) + "@" + file + " ";
  targetInput.value = prefix + textAfter;

  const newPos = prefix.length;
  targetInput.setSelectionRange(newPos, newPos);
  targetInput.focus();

  hideMentionPopup(popupEl);
  if (targetInput === chatInput) {
    autoResizeTextarea();
  }
}

// ════════ UI CONTROLS & AUTO-RESIZE ════════
function setupChatInputs() {
  const handleSend = async () => {
    if (isGenerating) {
      sendWs({ type: "stop_generation" });
      setGeneratingState(false);
      stopThinkingDotsAnimation();
      return;
    }

    const promptText = chatInput.value.trim();

    // Mandatory text prompt enforcer: if attachments present but prompt is empty -> shake & return
    if (stagedAttachments.length > 0 && !promptText) {
      chatInput.classList.add("input-error-shake");
      const wrapper = document.getElementById("chatInputWrapper");
      if (wrapper) wrapper.classList.add("input-error-shake");
      setTimeout(() => {
        chatInput.classList.remove("input-error-shake");
        if (wrapper) wrapper.classList.remove("input-error-shake");
      }, 500);
      chatInput.focus();
      return;
    }

    // If empty text and no attachments, ignore
    if (!promptText) return;

    // Block submission and shake if any attachment is still staging
    if (stagedAttachments.some((a) => a.status === "staging")) {
      chatInput.classList.add("input-error-shake");
      const wrapper = document.getElementById("chatInputWrapper");
      if (wrapper) wrapper.classList.add("input-error-shake");
      setTimeout(() => {
        chatInput.classList.remove("input-error-shake");
        if (wrapper) wrapper.classList.remove("input-error-shake");
      }, 500);
      return;
    }

    const stagedPayload = stagedAttachments.map((att) => ({
      name: att.name,
      path: att.path,
      file_uri: att.file_uri,
      is_image: att.isImage,
    }));

    chatInput.value = "";
    hideMentionPopup();
    autoResizeTextarea();
    setGeneratingState(true);

    stagedAttachments = [];
    renderStagingTray();

    sendWs({
      type: "user_message",
      text: promptText,
      staged_paths: stagedPayload,
    });
  };

  sendBtn.addEventListener("click", handleSend);

  chatInput.addEventListener("keydown", (e) => {
    if (fileMentionPopup && !fileMentionPopup.classList.contains("hidden")) {
      const items = fileMentionList.querySelectorAll(".file-mention-item");
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (items.length > 0) {
          selectedMentionIndex = (selectedMentionIndex + 1) % items.length;
          highlightMentionItem(items, selectedMentionIndex);
        }
        return;
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        if (items.length > 0) {
          selectedMentionIndex = (selectedMentionIndex - 1 + items.length) % items.length;
          highlightMentionItem(items, selectedMentionIndex);
        }
        return;
      } else if (e.key === "Enter" || e.key === "Tab") {
        if (items.length > 0 && selectedMentionIndex >= 0 && selectedMentionIndex < items.length) {
          e.preventDefault();
          const targetFile = items[selectedMentionIndex].getAttribute("data-file");
          selectMentionFile(targetFile);
          return;
        }
      } else if (e.key === "Escape") {
        e.preventDefault();
        hideMentionPopup();
        return;
      }
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  });

  chatInput.addEventListener("input", () => {
    autoResizeTextarea();
    updateMentionPopup();
  });

  chatInput.addEventListener("keyup", (e) => {
    if (["ArrowLeft", "ArrowRight", "Home", "End", "Backspace", "Delete"].includes(e.key)) {
      updateMentionPopup();
    }
  });

  chatInput.addEventListener("click", () => {
    updateMentionPopup();
  });

  document.addEventListener("pointerdown", (e) => {
    if (fileMentionPopup && !fileMentionPopup.classList.contains("hidden")) {
      if (!fileMentionPopup.contains(e.target) && e.target !== chatInput) {
        hideMentionPopup(fileMentionPopup);
      }
    }
    if (modalMentionPopup && !modalMentionPopup.classList.contains("hidden")) {
      if (!modalMentionPopup.contains(e.target) && e.target !== amadeusModalTextarea) {
        hideMentionPopup(modalMentionPopup);
      }
    }
  });

  // Attach button & hidden file input
  const attachFileBtn = document.getElementById("attachFileBtn");
  const attachmentFileInput = document.getElementById("attachmentFileInput");
  if (attachFileBtn && attachmentFileInput) {
    attachFileBtn.addEventListener("click", () => {
      attachmentFileInput.click();
    });
    attachmentFileInput.addEventListener("change", (e) => {
      if (e.target.files && e.target.files.length > 0) {
        validateAndStageFiles(e.target.files);
        attachmentFileInput.value = "";
      }
    });
  }

  // Paste Listener on #chatInput
  chatInput.addEventListener("paste", (e) => {
    if (!e.clipboardData) return;

    // Folder guard on paste
    if (e.clipboardData.items) {
      for (let i = 0; i < e.clipboardData.items.length; i++) {
        const item = e.clipboardData.items[i];
        if (item.webkitGetAsEntry) {
          const entry = item.webkitGetAsEntry();
          if (entry && entry.isDirectory) {
            showCustomModal({
              title: "FOLDER NOT SUPPORTED",
              message: "Folders cannot be attached directly. Please attach individual files or a .zip archive.",
            });
            return;
          }
        }
      }
    }

    if (e.clipboardData.files && e.clipboardData.files.length > 0) {
      validateAndStageFiles(e.clipboardData.files);
    }
  });

  // Drag and Drop on #chatInputContainer
  const chatInputContainer = document.getElementById("chatInputContainer");
  if (chatInputContainer) {
    let dragCounter = 0;
    chatInputContainer.addEventListener("dragenter", (e) => {
      e.preventDefault();
      dragCounter++;
      chatInputContainer.classList.add("drag-over-active");
    });

    chatInputContainer.addEventListener("dragover", (e) => {
      e.preventDefault();
      if (!chatInputContainer.classList.contains("drag-over-active")) {
        chatInputContainer.classList.add("drag-over-active");
      }
    });

    chatInputContainer.addEventListener("dragleave", (e) => {
      dragCounter--;
      if (dragCounter <= 0) {
        dragCounter = 0;
        chatInputContainer.classList.remove("drag-over-active");
      }
    });

    chatInputContainer.addEventListener("drop", (e) => {
      e.preventDefault();
      dragCounter = 0;
      chatInputContainer.classList.remove("drag-over-active");

      // Folder guard on drop
      if (e.dataTransfer && e.dataTransfer.items) {
        for (let i = 0; i < e.dataTransfer.items.length; i++) {
          const item = e.dataTransfer.items[i];
          if (item.webkitGetAsEntry) {
            const entry = item.webkitGetAsEntry();
            if (entry && entry.isDirectory) {
              showCustomModal({
                title: "FOLDER NOT SUPPORTED",
                message: "Folders cannot be attached directly. Please attach individual files or a .zip archive.",
              });
              return;
            }
          }
        }
      }

      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        validateAndStageFiles(e.dataTransfer.files);
      }
    });
  }

  // Mic Button: Click to Record -> Click again to Stop (Pastes into box)
  micBtn.addEventListener("click", () => {
    if (!isRecording) {
      sendWs({ type: "start_mic_record" });
      micBtn.classList.add("recording");
      micBtn.title = "Recording voice... Click to stop";
      isRecording = true;
    } else {
      sendWs({ type: "stop_mic_record" });
      micBtn.classList.remove("recording");
      micBtn.title = "Processing...";
      isRecording = false;
    }
  });

  newChatBtn.addEventListener("click", () => {
    currentActiveConvoId = null;
    sendWs({ type: "new_chat" });
    if (sessionHeading) sessionHeading.textContent = "New Chat";
    document.querySelectorAll(".history-item").forEach((el) => el.classList.remove("active"));
  });
}

function autoResizeTextarea() {
  chatInput.style.height = "auto";
  const newHeight = Math.min(chatInput.scrollHeight, 160);
  chatInput.style.height = `${newHeight}px`;
  chatInput.style.overflowY = chatInput.scrollHeight > 160 ? "auto" : "hidden";
}

function setupSidebarControls() {
  // Collapsible Sidebar toggle
  collapseSidebarBtn.addEventListener("click", () => {
    workstationLayout.classList.add("sidebar-collapsed");
    expandSidebarBtn.classList.remove("hidden");
    if (stage) stage.resize();
  });

  expandSidebarBtn.addEventListener("click", () => {
    workstationLayout.classList.remove("sidebar-collapsed");
    expandSidebarBtn.classList.add("hidden");
    if (stage) stage.resize();
  });

  // Workspace Directory Switcher Button
  const workspaceBtn = document.getElementById("workspaceBtn");
  if (workspaceBtn) {
    workspaceBtn.addEventListener("click", async () => {
      const confirmed = await showCustomConfirm("SWITCH WORKSPACE", "Do you want to change directories?");
      if (confirmed) {
        sendWs({ type: "close_workspace" });
      }
    });
  }

  // Dynamic Model Picker Dropdown (Top Right)
  modelBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    modelMenu.classList.toggle("hidden");
  });

  document.addEventListener("click", () => {
    modelMenu.classList.add("hidden");
  });
}

function setupAvatarControls() {
  const avatarMuteBtn = document.getElementById("avatarMuteBtn");
  if (avatarMuteBtn) {
    avatarMuteBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      isVoiceMuted = !isVoiceMuted;
      audioEnabled = !isVoiceMuted;
      // 0ms instant UI update
      if (isVoiceMuted) {
        avatarMuteBtn.classList.add("muted");
        avatarMuteBtn.title = "Unmute Voice";
        avatarMuteBtn.setAttribute("aria-label", "Unmute Kurisu Voice");
        if (stage) {
          stage.setEmotion("neutral", 0.25);
          stage.setAudioRms(0);
        }
        highlightEmotionChip("neutral");
      } else {
        avatarMuteBtn.classList.remove("muted");
        avatarMuteBtn.title = "Mute Voice";
        avatarMuteBtn.setAttribute("aria-label", "Mute Kurisu Voice");
        if (stage) {
          // Graceful 350ms fade-in to active expression when unmuted
          const targetEmotion = (activeSpeechEmotion && activeSpeechEmotion !== "neutral") ? activeSpeechEmotion : "neutral";
          stage.setEmotion(targetEmotion, 0.35);
          highlightEmotionChip(targetEmotion);
        }
      }
      sendWs({ type: "toggle_voice", enabled: !isVoiceMuted });
    });
  }
}

function updateVoiceStatus(enabled) {
  audioEnabled = !!enabled;
  isVoiceMuted = !audioEnabled;
  const avatarMuteBtn = document.getElementById("avatarMuteBtn");
  if (avatarMuteBtn) {
    if (isVoiceMuted) {
      avatarMuteBtn.classList.add("muted");
      avatarMuteBtn.title = "Unmute Voice";
      avatarMuteBtn.setAttribute("aria-label", "Unmute Kurisu Voice");
      if (stage) {
        stage.setEmotion("neutral", 0.25);
        stage.setAudioRms(0);
      }
      highlightEmotionChip("neutral");
    } else {
      avatarMuteBtn.classList.remove("muted");
      avatarMuteBtn.title = "Mute Voice";
      avatarMuteBtn.setAttribute("aria-label", "Mute Kurisu Voice");
      if (stage) {
        // Graceful 350ms fade-in to active expression when unmuted
        const targetEmotion = (activeSpeechEmotion && activeSpeechEmotion !== "neutral") ? activeSpeechEmotion : "neutral";
        stage.setEmotion(targetEmotion, 0.35);
        highlightEmotionChip(targetEmotion);
      }
    }
  }
}

function updateActiveModel(modelId) {
  activeModel = modelId;
  if (activeModelLabel) {
    const found = availableModelsCache.find((m) => m.id === modelId);
    activeModelLabel.textContent = found ? found.name : modelId;
  }
  // Update active state in menu
  document.querySelectorAll(".model-opt").forEach((el) => {
    if (el.dataset.id === modelId) el.classList.add("active");
    else el.classList.remove("active");
  });
}

function renderModelOptions(models) {
  availableModelsCache = models;
  modelList.innerHTML = "";
  for (const m of models) {
    const opt = document.createElement("div");
    opt.className = `model-opt ${m.id === activeModel ? "active" : ""}`;
    opt.dataset.id = m.id;
    opt.innerHTML = `
      <span>${escapeHtml(m.name)}</span>
      ${m.desc && m.desc !== m.name ? `<span class="model-opt-desc">${escapeHtml(m.desc)}</span>` : ""}
    `;
    opt.addEventListener("click", () => {
      sendWs({ type: "switch_model", model: m.id });
      modelMenu.classList.add("hidden");
    });
    modelList.appendChild(opt);
  }
}

function renderHistoryList(convos) {
  if (!convos || convos.length === 0) return;
  historyList.innerHTML = "";
  for (const c of convos) {
    // Strip [AMADEUS] tag for clean display
    const raw = c.title || "Untitled Conversation";
    const cleanTitle = raw.replace(/^\[AMADEUS\]\s*/i, "").trim() || "Untitled Conversation";

    const item = document.createElement("div");
    item.className = `history-item ${c.id === currentActiveConvoId ? "active" : ""}`;
    item.dataset.id = c.id;

    item.innerHTML = `
      <div class="history-item-left" title="${escapeHtml(cleanTitle)}">
        <span class="history-label">${escapeHtml(cleanTitle)}</span>
      </div>
      <div class="history-actions">
        <button class="history-action-btn edit-btn" title="Rename conversation">✏️</button>
        <button class="history-action-btn del-btn" title="Delete conversation">🗑️</button>
      </div>
    `;

    // Click item to resume
    item.querySelector(".history-item-left").addEventListener("click", () => {
      document.querySelectorAll(".history-item").forEach((el) => el.classList.remove("active"));
      item.classList.add("active");
      if (sessionHeading) sessionHeading.textContent = cleanTitle;
      sendWs({ type: "resume_convo", id: c.id });
    });

    // Rename button
    item.querySelector(".edit-btn").addEventListener("click", async (e) => {
      e.stopPropagation();
      const newTitle = await showCustomPrompt("RENAME CONVERSATION", "Enter new title for conversation:", cleanTitle);
      if (newTitle && newTitle.trim() && newTitle.trim() !== cleanTitle) {
        sendWs({ type: "rename_convo", id: c.id, title: newTitle.trim() });
        if (item.classList.contains("active") && sessionHeading) {
          sessionHeading.textContent = newTitle.trim();
        }
      }
    });

    // Delete button
    item.querySelector(".del-btn").addEventListener("click", async (e) => {
      e.stopPropagation();
      const confirmed = await showCustomConfirm("DELETE ARCHIVE", `Delete conversation "${cleanTitle}"? This cannot be undone.`, true);
      if (confirmed) {
        sendWs({ type: "delete_convo", id: c.id });
      }
    });

    historyList.appendChild(item);
  }
}

// ════════ EMOTION PALETTE & TELEMETRY ════════
function setupEmotionPalette() {
  const chips = document.querySelectorAll(".pal-chip");
  chips.forEach((chip) => {
    chip.addEventListener("click", () => {
      const emotion = chip.dataset.emotion;
      chips.forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      stage.setEmotion(emotion);
    });
  });
}

function highlightEmotionChip(emotion) {
  const chips = document.querySelectorAll(".pal-chip");
  chips.forEach((c) => {
    if (c.dataset.emotion === emotion) {
      c.classList.add("active");
    } else {
      c.classList.remove("active");
    }
  });
}

function formatDateTime(d) {
  const currentYear = new Date().getFullYear();
  const options = { month: "short", day: "numeric" };
  if (d.getFullYear() !== currentYear) {
    options.year = "numeric";
  }
  const datePart = d.toLocaleDateString(undefined, options);
  const timePart = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return `${datePart}, ${timePart}`;
}

function formatTimestamp(isoStr) {
  if (!isoStr) return getCurrentTimeStr();
  try {
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return getCurrentTimeStr();
    return formatDateTime(d);
  } catch (_) {
    return getCurrentTimeStr();
  }
}

function getCurrentTimeStr() {
  return formatDateTime(new Date());
}

function formatSearchDate(isoStr) {
  if (!isoStr) return "";
  try {
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return String(isoStr).substring(0, 16);
    const datePart = d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
    const timePart = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    return `${datePart} • ${timePart}`;
  } catch (_) {
    return String(isoStr).substring(0, 16);
  }
}

function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// ════════ STEINS;GATE DETAILED OPERATIONS INSPECTOR MODAL ════════
function setupDetailedModal() {
  if (!detailedModalOverlay) return;

  // 1. Tab Switchers
  const tabs = [
    { btn: tabBtnDiffs, pane: paneDiffs, name: "diffs" },
    { btn: tabBtnTerminal, pane: paneTerminal, name: "terminal" },
    { btn: tabBtnTasks, pane: paneTasks, name: "tasks" },
    { btn: tabBtnSubagents, pane: paneSubagents, name: "subagents" },
    { btn: tabBtnUsage, pane: paneUsage, name: "usage" },
  ];

  tabs.forEach(({ btn, name }) => {
    if (btn) {
      btn.addEventListener("click", () => {
        openDetailedModalTab(name);
      });
    }
  });

  // 2. Open Inspector Button in Avatar Column
  if (openInspectorBtn) {
    openInspectorBtn.addEventListener("click", () => {
      openDetailedModalTab(activeDetailedTab || "diffs");
    });
  }

  // 3. Close Button
  if (detailedModalCloseBtn) {
    detailedModalCloseBtn.addEventListener("click", closeDetailedModal);
  }

  // 4. Backdrop Click to close
  detailedModalOverlay.addEventListener("click", (e) => {
    if (e.target === detailedModalOverlay) {
      closeDetailedModal();
    }
  });

  // 5. Keyboard Shortcuts (Esc to close active modal)
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (detailedModalOverlay && !detailedModalOverlay.classList.contains("hidden")) {
        closeDetailedModal();
      }
      if (chatSearchModalOverlay && !chatSearchModalOverlay.classList.contains("hidden")) {
        closeChatSearchModal();
      }
    }
  });

  // 7. Copy Console Output Button
  if (copyConsoleBtn) {
    copyConsoleBtn.addEventListener("click", () => {
      const text = consoleOutputBox ? consoleOutputBox.textContent : "";
      if (text) {
        navigator.clipboard.writeText(text).then(() => {
          copyConsoleBtn.textContent = "Copied!";
          setTimeout(() => (copyConsoleBtn.textContent = "Copy Output"), 2000);
        });
      }
    });
  }

  // 8. Refresh Task Log Button
  if (refreshTaskLogBtn) {
    refreshTaskLogBtn.addEventListener("click", () => {
      if (selectedTaskId) {
        refreshTaskLogBtn.textContent = "Refreshing...";
        sendWs({ type: "request_task_log", task_id: selectedTaskId });
        setTimeout(() => (refreshTaskLogBtn.textContent = "↻ Refresh Log"), 1000);
      }
    });
  }

  // 9. Refresh Quota Button
  if (refreshQuotaBtn) {
    refreshQuotaBtn.addEventListener("click", () => {
      refreshQuotaBtn.textContent = "Refreshing...";
      sendWs({ type: "request_usage_quota" });
      setTimeout(() => (refreshQuotaBtn.textContent = "↻ Refresh Quota"), 1200);
    });
  }
}

function requestModalData(subagentId = null) {
  sendWs({ type: "request_session_diffs", subagent_id: subagentId });
  sendWs({ type: "request_session_commands", subagent_id: subagentId });
  sendWs({ type: "request_session_tasks", subagent_id: subagentId });
  sendWs({ type: "request_session_subagents" });
}

function openDetailedModalTab(tabName, itemToSelect = null) {
  if (!detailedModalOverlay) return;
  activeDetailedTab = tabName;

  // Deactivate all tabs & panes
  [tabBtnDiffs, tabBtnTerminal, tabBtnTasks, tabBtnSubagents, tabBtnUsage].forEach((b) => b && b.classList.remove("active"));
  [paneDiffs, paneTerminal, paneTasks, paneSubagents, paneUsage].forEach((p) => p && p.classList.remove("active"));

  const targetSubagentId = null;

  if (tabName === "diffs") {
    if (tabBtnDiffs) tabBtnDiffs.classList.add("active");
    if (paneDiffs) paneDiffs.classList.add("active");
    sendWs({ type: "request_session_diffs", subagent_id: targetSubagentId });
    if (itemToSelect && itemToSelect.file) {
      selectDiffFile(itemToSelect.file);
    } else if (sessionDiffsCache.length > 0) {
      selectDiffFile(sessionDiffsCache[0].file);
    }
  } else if (tabName === "terminal") {
    if (tabBtnTerminal) tabBtnTerminal.classList.add("active");
    if (paneTerminal) paneTerminal.classList.add("active");
    sendWs({ type: "request_session_commands", subagent_id: targetSubagentId });
    if (itemToSelect && itemToSelect.command) {
      selectCommandItem(itemToSelect.command);
    } else if (sessionCommandsCache.length > 0) {
      selectCommandItem(sessionCommandsCache[sessionCommandsCache.length - 1]);
    }
  } else if (tabName === "tasks") {
    if (tabBtnTasks) tabBtnTasks.classList.add("active");
    if (paneTasks) paneTasks.classList.add("active");
    sendWs({ type: "request_session_tasks", subagent_id: targetSubagentId });
    if (itemToSelect && itemToSelect.taskId) {
      selectTaskItem(itemToSelect.taskId);
    } else if (sessionTasksCache.length > 0) {
      selectTaskItem(sessionTasksCache[0].task_id || `task-${sessionTasksCache[0].step_index}`);
    }
  } else if (tabName === "subagents") {
    if (tabBtnSubagents) tabBtnSubagents.classList.add("active");
    if (paneSubagents) paneSubagents.classList.add("active");
    sendWs({ type: "request_session_subagents" });
    if (itemToSelect && itemToSelect.subagentId) {
      selectSubagentItem(itemToSelect.subagentId);
    } else if (sessionSubagentsCache.length > 0 && sessionSubagentsCache[0].subagent_id) {
      selectSubagentItem(sessionSubagentsCache[0].subagent_id);
    }
  } else if (tabName === "usage") {
    if (tabBtnUsage) tabBtnUsage.classList.add("active");
    if (paneUsage) paneUsage.classList.add("active");
    sendWs({ type: "request_usage_quota" });
  }

  updateModalBadges();
  detailedModalOverlay.classList.remove("hidden");
}

function closeDetailedModal() {
  if (detailedModalOverlay) {
    detailedModalOverlay.classList.add("hidden");
  }
}

// ════════ CHAT ARCHIVE SEARCH MODAL LOGIC ════════
function setupChatSearchModal() {
  if (!chatSearchModalOverlay) return;

  if (searchChatsBtn) {
    searchChatsBtn.addEventListener("click", openChatSearchModal);
  }

  if (chatSearchCloseBtn) {
    chatSearchCloseBtn.addEventListener("click", closeChatSearchModal);
  }

  chatSearchModalOverlay.addEventListener("click", (e) => {
    if (e.target === chatSearchModalOverlay) {
      closeChatSearchModal();
    }
  });

  if (clearSearchBtn && chatSearchInput) {
    clearSearchBtn.addEventListener("click", () => {
      chatSearchInput.value = "";
      clearSearchBtn.classList.add("hidden");
      chatSearchInput.focus();
      performChatSearch("");
    });
  }

  if (chatSearchInput) {
    chatSearchInput.addEventListener("input", (e) => {
      const q = e.target.value;
      if (clearSearchBtn) {
        if (q && q.trim()) clearSearchBtn.classList.remove("hidden");
        else clearSearchBtn.classList.add("hidden");
      }
      if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
      searchDebounceTimer = setTimeout(() => {
        performChatSearch(q);
      }, 180);
    });

    chatSearchInput.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        closeChatSearchModal();
      }
    });
  }
}

function openChatSearchModal() {
  if (!chatSearchModalOverlay) return;
  chatSearchModalOverlay.classList.remove("hidden");
  if (chatSearchInput) {
    chatSearchInput.value = "";
    if (clearSearchBtn) clearSearchBtn.classList.add("hidden");
    setTimeout(() => chatSearchInput.focus(), 60);
  }
  performChatSearch("");
}

function closeChatSearchModal() {
  if (!chatSearchModalOverlay) return;
  chatSearchModalOverlay.classList.add("hidden");
}

function performChatSearch(query) {
  lastSearchQuery = query || "";
  sendWs({ type: "search_chats", query: lastSearchQuery });
}

function highlightSearchMatch(text, query) {
  if (!text) return "";
  const escaped = escapeHtml(text);
  if (!query || !query.trim()) return escaped;
  const safeQ = escapeHtml(query.trim()).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const regex = new RegExp(`(${safeQ})`, "gi");
  return escaped.replace(regex, '<mark class="search-highlight">$1</mark>');
}

function renderSearchResults(results, query = "") {
  if (!searchResultsList) return;
  searchResultsList.innerHTML = "";

  const qClean = (query || "").trim();
  const count = results ? results.length : 0;
  if (searchCountBadge) searchCountBadge.textContent = count;

  if (searchStatusText) {
    if (qClean) {
      searchStatusText.textContent = `Found ${count} matching message${count === 1 ? "" : "s"} for "${qClean}"`;
    } else {
      searchStatusText.textContent = `All past conversations (${count})`;
    }
  }

  if (!results || results.length === 0) {
    searchResultsList.innerHTML = `
      <div class="empty-state" style="padding: 30px; text-align: center; color: var(--text-muted);">
        ${qClean ? `No messages found matching "${escapeHtml(qClean)}".` : "No past conversations found."}
      </div>
    `;
    return;
  }

  for (const item of results) {
    const card = document.createElement("div");
    card.className = "search-match-card";
    card.dataset.convId = item.conversation_id;
    card.dataset.stepIndex = item.step_index !== undefined ? item.step_index : 0;

    const formattedDate = formatSearchDate(item.created_at || item.last_modified);
    const roleBadge = item.role === "user"
      ? '<span class="search-role-badge user">User</span>'
      : item.role === "model"
      ? '<span class="search-role-badge model">Amadeus</span>'
      : "";

    const cleanTitle = (item.title || "").replace(/^\[AMADEUS\]\s*/i, "").trim() || "Untitled Conversation";
    const cleanSnippet = extractCleanVisualText(item.snippet || "");
    const highlightedSnippet = qClean
      ? highlightSearchMatch(cleanSnippet, qClean)
      : escapeHtml(cleanSnippet || "No preview available");

    card.innerHTML = `
      <div class="search-card-header">
        <span class="search-card-title">${escapeHtml(cleanTitle)}</span>
        <div class="search-card-meta">
          ${roleBadge}
          <span class="search-card-date">${formattedDate}</span>
        </div>
      </div>
      <div class="search-snippet">${highlightedSnippet}</div>
    `;

    card.addEventListener("click", () => {
      closeChatSearchModal();
      handleSearchCardSelect(item.conversation_id, item.step_index);
    });

    searchResultsList.appendChild(card);
  }
}

function handleSearchCardSelect(convId, stepIndex) {
  if (!convId) return;

  if (convId === currentActiveConvoId) {
    if (stepIndex !== null && stepIndex !== undefined && stepIndex > 0) {
      scrollToMessageStep(stepIndex);
    }
    return;
  }

  pendingSearchScrollStep = (stepIndex !== null && stepIndex !== undefined && stepIndex > 0) ? stepIndex : null;
  sendWs({ type: "resume_convo", id: convId });
}

function scrollToMessageStep(stepIndex) {
  if (stepIndex === null || stepIndex === undefined) return;
  const targetCard = chatFeed.querySelector(`[data-step-index="${stepIndex}"]`);
  if (targetCard) {
    targetCard.scrollIntoView({ behavior: "smooth", block: "center" });
    targetCard.classList.remove("search-highlight-pulse");
    void targetCard.offsetWidth; // trigger reflow
    targetCard.classList.add("search-highlight-pulse");
    setTimeout(() => {
      targetCard.classList.remove("search-highlight-pulse");
    }, 2800);
  }
}

function updateModalBadges() {
  if (diffsCountBadge) diffsCountBadge.textContent = sessionDiffsCache.length;
  if (commandsCountBadge) commandsCountBadge.textContent = sessionCommandsCache.length;
  if (tasksCountBadge) tasksCountBadge.textContent = sessionTasksCache.length;
  if (subagentsCountBadge) subagentsCountBadge.textContent = sessionSubagentsCache.length;
}

// ── 1. Diffs Pane Renderer ──
function renderDiffsPane(diffs) {
  if (!diffFileListItems) return;
  diffFileListItems.innerHTML = "";

  if (!diffs || diffs.length === 0) {
    diffFileListItems.innerHTML = `<div class="empty-state">No files modified in this view.</div>`;
    if (diffCurrentFile) diffCurrentFile.textContent = "No modified files";
    if (diffCurrentStats) diffCurrentStats.textContent = "";
    if (revealInExplorerBtn) revealInExplorerBtn.classList.add("hidden");
    if (diffCodeWrapper) diffCodeWrapper.innerHTML = `<div class="empty-state">No file diffs recorded yet.</div>`;
    return;
  }

  diffs.forEach((d) => {
    const item = document.createElement("div");
    item.className = `pane-item ${selectedDiffFile === d.file ? "active" : ""}`;
    const filename = getFileName(d.file);
    const authorTag = d.author_name ? `<span class="author-pill-badge ${d.author_type || 'amadeus'}">${d.author_type === 'subagent' ? '🤖 ' : '🧠 '}${escapeHtml(d.author_name)}</span>` : "";
    const statusBadge = d.deleted
      ? `<span class="pill-badge file-badge-deleted">DELETED</span>`
      : `<span style="color: #c084fc;">+${d.lines_added || 0} / -${d.lines_removed || 0}</span>`;

    item.innerHTML = `
      <div class="pane-item-title" title="${escapeHtml(d.file || '')}">
        <span>${escapeHtml(filename)}</span>
        ${authorTag}
      </div>
      <div class="pane-item-sub">
        <span>${escapeHtml(d.file ? d.file.slice(0, 30) : "")}</span>
        ${statusBadge}
      </div>
    `;

    item.addEventListener("click", () => {
      diffFileListItems.querySelectorAll(".pane-item").forEach((el) => el.classList.remove("active"));
      item.classList.add("active");
      selectDiffFile(d.file);
    });

    diffFileListItems.appendChild(item);
  });
}

function selectDiffFile(filePath) {
  selectedDiffFile = filePath;
  const found = sessionDiffsCache.find((d) => d.file === filePath);
  if (!found) return;

  if (diffCurrentFile) diffCurrentFile.textContent = found.file;
  if (diffCurrentStats) {
    if (found.deleted) {
      diffCurrentStats.innerHTML = `<span class="pill-badge file-badge-deleted">DELETED</span>`;
    } else {
      diffCurrentStats.textContent = `+${found.lines_added || 0} / -${found.lines_removed || 0} lines changed`;
    }
  }

  if (revealInExplorerBtn) {
    revealInExplorerBtn.classList.remove("hidden");
    revealInExplorerBtn.onclick = () => {
      sendWs({ type: "reveal_in_explorer", path: found.file });
    };
  }

  if (diffCodeWrapper) {
    if (found.deleted) {
      diffCodeWrapper.innerHTML = `
        <div style="padding: 24px; color: #f43f5e; font-family: var(--font-mono); font-size: 13px;">
          ⚠ [FILE DELETED] This file was removed from the workspace directory.
        </div>
        <div class="diff-lines-container">
          ${renderDiffLinesHtml(found.diff || "--- /dev/null")}
        </div>
      `;
    } else {
      diffCodeWrapper.innerHTML = `
        <div class="diff-lines-container">
          ${renderDiffLinesHtml(found.diff || "No diff content available.")}
        </div>
      `;
    }
  }
}

// ── 2. Terminal Console Pane Renderer ──
function renderTerminalPane(commands) {
  if (!commandListItems) return;
  commandListItems.innerHTML = "";

  if (!commands || commands.length === 0) {
    commandListItems.innerHTML = `<div class="empty-state">No commands executed yet.</div>`;
    if (consoleCmdTitle) consoleCmdTitle.textContent = "No executed commands";
    if (consoleExitBadge) {
      consoleExitBadge.className = "console-exit-badge";
      consoleExitBadge.textContent = "IDLE";
    }
    if (consoleOutputBox) consoleOutputBox.textContent = "Commands executed in this session will appear here with full stdout/stderr.";
    return;
  }

  commands.forEach((cmd, idx) => {
    const item = document.createElement("div");
    item.className = `pane-item ${selectedCommandIndex === idx ? "active" : ""}`;
    const exitOk = cmd.exit_code === 0;
    const authorTag = cmd.author_name ? `<span class="author-pill-badge ${cmd.author_type || 'amadeus'}">${cmd.author_type === 'subagent' ? '🤖 ' : '🧠 '}${escapeHtml(cmd.author_name)}</span>` : "";

    item.innerHTML = `
      <div class="pane-item-title" title="${escapeHtml(cmd.command || '')}">
        <span>⚡ ${escapeHtml(cmd.command || '')}</span>
        ${authorTag}
      </div>
      <div class="pane-item-sub">
        <span>Step ${cmd.step_index !== undefined ? cmd.step_index : idx}</span>
        <span style="color: ${exitOk ? '#10b981' : '#f43f5e'};">EXIT ${cmd.exit_code || 0}</span>
      </div>
    `;

    item.addEventListener("click", () => {
      commandListItems.querySelectorAll(".pane-item").forEach((el) => el.classList.remove("active"));
      item.classList.add("active");
      selectedCommandIndex = idx;
      selectCommandItem(cmd);
    });

    commandListItems.appendChild(item);
  });
}

function selectCommandItem(cmd) {
  if (!cmd) return;
  if (consoleCmdTitle) consoleCmdTitle.textContent = cmd.command || "Executed Command";
  if (consoleExitBadge) {
    const isRunning = cmd.state === "ACTIVE" || cmd.state === "RUNNING";
    const exitOk = cmd.exit_code === 0;
    consoleExitBadge.className = `console-exit-badge ${isRunning ? 'badge-running' : (exitOk ? 'badge-exit-ok' : 'badge-exit-fail')}`;
    consoleExitBadge.textContent = isRunning ? "RUNNING" : (exitOk ? "EXIT 0 (SUCCESS)" : `EXIT ${cmd.exit_code} (FAILED)`);
  }
  if (consoleOutputBox) {
    let out = "";
    if (cmd.author_name) out += `Executed by: ${cmd.author_name}\n`;
    if (cmd.cwd) out += `Working Directory: ${cmd.cwd}\n`;
    if (cmd.duration) out += `Execution Duration: ${cmd.duration.toFixed(2)}s\n`;
    out += `\n════════════════ TERMINAL STDOUT & STDERR ════════════════\n\n`;
    out += cmd.output || "(Command completed with no standard output)";
    consoleOutputBox.textContent = out;
  }
}

// ── 3. Background Tasks Pane Renderer ──
function renderTasksPane(tasks) {
  if (!taskListItems) return;
  taskListItems.innerHTML = "";

  if (!tasks || tasks.length === 0) {
    taskListItems.innerHTML = `<div class="empty-state">No background tasks scheduled.</div>`;
    if (taskDetailId) taskDetailId.textContent = "No active tasks";
    if (taskDetailState) taskDetailState.textContent = "";
    if (taskPromptBox) taskPromptBox.textContent = "";
    if (killTaskBtn) killTaskBtn.classList.add("hidden");
    if (taskLogBox) taskLogBox.textContent = "Background tasks scheduled via schedule / manage_task will stream live logs here.";
    return;
  }

  tasks.forEach((t) => {
    const tid = t.task_id || `task-${t.step_index || 1}`;
    const isFailed = t.state === "FAILED" || t.state === "ERROR";
    const isDone = t.state === "COMPLETED" || t.state === "DONE";
    const isCancelled = t.state === "CANCELLED" || t.state === "KILLED";
    const authorTag = t.author_name ? `<span class="author-pill-badge ${t.author_type || 'amadeus'}">${t.author_type === 'subagent' ? '🤖 ' : '🧠 '}${escapeHtml(t.author_name)}</span>` : "";

    const item = document.createElement("div");
    item.className = `pane-item ${selectedTaskId === tid ? "active" : ""}`;
    item.innerHTML = `
      <div class="pane-item-title">
        <span>⏳ ${escapeHtml(tid)}</span>
        ${authorTag}
      </div>
      <div class="pane-item-sub">
        <span>${escapeHtml(t.prompt ? t.prompt.slice(0, 24) : (t.action || 'task'))}</span>
        <span style="color: ${isFailed || isCancelled ? '#f43f5e' : (isDone ? '#10b981' : '#fbbf24')};">${isFailed ? 'FAILED' : (isCancelled ? 'CANCELLED' : (isDone ? 'DONE' : 'ACTIVE'))}</span>
      </div>
    `;

    item.addEventListener("click", () => {
      taskListItems.querySelectorAll(".pane-item").forEach((el) => el.classList.remove("active"));
      item.classList.add("active");
      selectTaskItem(tid);
    });

    taskListItems.appendChild(item);
  });
}

function selectTaskItem(taskId) {
  selectedTaskId = taskId;
  const found = sessionTasksCache.find((t) => {
    const tid = t.task_id || `task-${t.step_index}`;
    return isSameTaskId(tid, taskId);
  });
  if (!found) return;

  if (taskDetailId) taskDetailId.textContent = taskId;
  const isFailed = found.state === "FAILED" || found.state === "ERROR";
  const isCancelled = found.state === "CANCELLED" || found.state === "KILLED" || found.state === "CANCELED" || found.state === "INTERRUPTED";
  const isDone = found.state === "COMPLETED" || found.state === "DONE";
  const isRunning = found.state === "ACTIVE" || found.state === "RUNNING";

  if (taskDetailState) {
    const badgeClass = isFailed ? "pill-badge file-badge-deleted" : (isCancelled ? "pill-badge file-badge-deleted" : (isDone ? "pill-badge badge-task-done" : "pill-badge badge-task-active"));
    taskDetailState.className = badgeClass;
    taskDetailState.textContent = isFailed ? "FAILED" : (isCancelled ? (found.state === "INTERRUPTED" ? "INTERRUPTED" : "CANCELLED") : (isDone ? "COMPLETED" : "ACTIVE / RUNNING"));
  }

  if (killTaskBtn) {
    if (isRunning) {
      killTaskBtn.classList.remove("hidden");
      killTaskBtn.textContent = "⏹ Kill Task";
      killTaskBtn.onclick = () => {
        killTaskBtn.textContent = "Cancelling...";
        sendWs({ type: "kill_task", task_id: taskId });
      };
    } else {
      killTaskBtn.classList.add("hidden");
    }
  }

  if (taskPromptBox) {
    const promptText = found.command ? `Command: ${found.command}` : (found.prompt || found.action || "Background Job");
    taskPromptBox.textContent = `${promptText} ${found.duration_seconds ? `(Timer: ${found.duration_seconds}s)` : ""} ${found.author_name ? `[Author: ${found.author_name}]` : ""}`;
  }
  if (taskLogBox) {
    taskLogBox.textContent = found.output || "Fetching live task log...";
    sendWs({ type: "request_task_log", task_id: taskId });
  }
}

// ── 4. Subagents Pane Renderer ──
function renderSubagentsPane(subagents) {
  if (!subagentListItems) return;
  subagentListItems.innerHTML = "";

  if (!subagents || subagents.length === 0) {
    subagentListItems.innerHTML = `<div class="empty-state">No subagents dispatched yet.</div>`;
    if (subagentMetaTitle) subagentMetaTitle.textContent = "No active subagents";
    if (subagentMetaBadge) subagentMetaBadge.textContent = "IDLE";
    if (subagentPromptBox) subagentPromptBox.textContent = "";
    if (killSubagentBtn) killSubagentBtn.classList.add("hidden");
    if (subagentChatFeed) subagentChatFeed.innerHTML = `<div class="empty-state">Autonomous child subagents dispatched via invoke_subagent will appear here.</div>`;
    return;
  }

  subagents.forEach((s) => {
    const sid = s.subagent_id;
    if (!sid) return;
    const item = document.createElement("div");
    item.className = `pane-item ${selectedSubagentId === sid ? "active" : ""}`;
    item.dataset.subagentId = sid;
    const isKilled = s.state === "KILLED" || s.state === "CANCELLED" || s.state === "INTERRUPTED";
    const isDone = s.state === "DONE" || s.state === "COMPLETED";
    const isWaiting = s.state === "WAITING";
    const statusColor = isKilled ? '#f43f5e' : (isDone ? '#10b981' : (isWaiting ? '#fbbf24' : '#22d3ee'));
    item.innerHTML = `
      <div class="pane-item-title">🤖 ${escapeHtml(s.role || s.type_name || 'Subagent')}</div>
      <div class="pane-item-sub">
        <span>Type: ${escapeHtml(s.type_name || 'default')}</span>
        <span style="color: ${statusColor};">${escapeHtml(s.state || 'ACTIVE')}</span>
      </div>
    `;

    item.addEventListener("click", () => {
      subagentListItems.querySelectorAll(".pane-item").forEach((el) => el.classList.remove("active"));
      item.classList.add("active");
      selectSubagentItem(sid);
    });

    subagentListItems.appendChild(item);
  });
}

function selectSubagentItem(subagentId) {
  if (!subagentId) return;
  selectedSubagentId = subagentId;
  const found = sessionSubagentsCache.find((s) => s.subagent_id === subagentId);
  if (!found) return;

  if (subagentMetaTitle) subagentMetaTitle.textContent = `🤖 ${found.role || found.type_name || 'Subagent'}`;
  const isKilled = found.state === "KILLED" || found.state === "CANCELLED" || found.state === "INTERRUPTED";
  const isActive = found.state === "ACTIVE" || found.state === "ALIVE";
  const badgeClass = isKilled ? "pill-badge file-badge-deleted" : (isActive ? "pill-badge badge-subagent" : "pill-badge badge-done");

  if (subagentMetaBadge) {
    subagentMetaBadge.className = badgeClass;
    subagentMetaBadge.textContent = found.state || "ACTIVE";
  }

  if (killSubagentBtn) {
    if (!isKilled) {
      killSubagentBtn.classList.remove("hidden");
      killSubagentBtn.textContent = "⏹ Stop Subagents";
      killSubagentBtn.onclick = () => {
        killSubagentBtn.textContent = "Stopping...";
        const killOverlay = document.getElementById("subagentKillLoadingOverlay");
        if (killOverlay) killOverlay.classList.remove("hidden");
        sendWs({ type: "kill_subagent", subagent_id: subagentId, conversation_id: currentActiveConvoId });
        // Safety fallback timer to hide overlay after 6s if connection drops
        setTimeout(() => {
          const ov = document.getElementById("subagentKillLoadingOverlay");
          if (ov) ov.classList.add("hidden");
        }, 6000);
      };
    } else {
      killSubagentBtn.classList.add("hidden");
    }
  }

  if (subagentPromptBox) {
    subagentPromptBox.textContent = `Directive: ${found.prompt || "Autonomous Execution"}`;
  }
  if (subagentChatFeed) {
    subagentChatFeed.innerHTML = `<div class="empty-state">Fetching subagent operations and transcript...</div>`;
    sendWs({ type: "request_subagent_info", subagent_id: subagentId });
  }
}

function renderSubagentTranscript(subagentId, data) {
  if (!subagentChatFeed) return;
  subagentChatFeed.innerHTML = "";

  const history = (data && data.history) || [];
  if (history.length === 0) {
    subagentChatFeed.innerHTML = `<div class="empty-state">No operations or transcript recorded yet for this subagent.</div>`;
    return;
  }

  for (const step of history) {
    const turnCard = document.createElement("div");
    turnCard.className = `msg-card ${step.role === 'user' ? 'user' : 'amadeus'}`;

    const ops = step.operations || [];
    let opsContainer = null;

    turnCard.innerHTML = `
      <div class="msg-header">
        <span class="speaker-tag">${step.role === 'user' ? 'Direct Prompt' : 'Subagent Worker'}</span>
        <span class="timestamp">${formatTimestamp(step.created_at)}</span>
      </div>
      <div class="msg-body">${escapeHtml(step.content || '')}</div>
      ${ops.length > 0 ? `<div class="turn-timeline-flow" style="margin-top: 8px;"></div>` : ''}
    `;

    if (ops.length > 0) {
      opsContainer = turnCard.querySelector(".turn-timeline-flow");
      for (const op of ops) {
        try {
          renderOrUpdateOperationPill(opsContainer, op);
        } catch (e) {
          console.warn("[Subagent] Error rendering op pill:", e);
        }
      }
    }

    subagentChatFeed.appendChild(turnCard);
  }
  subagentChatFeed.scrollTop = subagentChatFeed.scrollHeight;
}

// ── 5. Usage & Quota Pane Renderer ──
function renderUsageQuota(data) {
  if (!usageCardsGrid) return;
  usageCardsGrid.innerHTML = "";

  const groups = (data && data.groups) || [];
  if (groups.length === 0) {
    usageCardsGrid.innerHTML = `<div class="empty-state">No quota data available.</div>`;
    return;
  }

  groups.forEach((group) => {
    const card = document.createElement("div");
    card.className = "usage-group-card";

    let bucketsHtml = "";
    const buckets = group.buckets || [];
    buckets.forEach((bucket) => {
      const fraction = typeof bucket.remaining_fraction === "number" ? bucket.remaining_fraction : 1;
      const pct = Math.max(0, Math.min(100, Math.round(fraction * 100)));

      let barColor = "linear-gradient(90deg, #06b6d4, #00f0ff)";
      let pctColor = "#00f0ff";
      if (pct <= 20) {
        barColor = "linear-gradient(90deg, #f43f5e, #fb7185)";
        pctColor = "#f43f5e";
      } else if (pct <= 50) {
        barColor = "linear-gradient(90deg, #f59e0b, #fbbf24)";
        pctColor = "#fbbf24";
      }

      let resetText = "";
      if (bucket.reset_time) {
        try {
          const d = new Date(bucket.reset_time);
          if (!isNaN(d.getTime())) {
            const month = d.toLocaleDateString(undefined, { month: "short" });
            const day = d.getDate();
            const hours = String(d.getHours()).padStart(2, "0");
            const mins = String(d.getMinutes()).padStart(2, "0");
            resetText = `Refreshes ${month} ${day}, ${hours}:${mins}`;
          }
        } catch (_) {
          resetText = `Refreshes ${bucket.reset_time}`;
        }
      }

      bucketsHtml += `
        <div class="quota-bucket-row">
          <div class="quota-bucket-header">
            <span class="quota-bucket-name">${escapeHtml(bucket.name || 'Quota Limit')}</span>
            <span class="quota-bucket-pct" style="color: ${pctColor};">${pct}%</span>
          </div>
          <div class="quota-bar-track">
            <div class="quota-bar-fill" style="width: ${pct}%; background: ${barColor};"></div>
          </div>
          ${resetText ? `<div class="quota-reset-text">${escapeHtml(resetText)}</div>` : ''}
        </div>
      `;
    });

    card.innerHTML = `
      <div class="usage-group-title">⚡ ${escapeHtml(group.name || 'Model Group')}</div>
      ${group.description ? `<div class="usage-group-desc">${escapeHtml(group.description)}</div>` : ''}
      <div class="quota-buckets-container">
        ${bucketsHtml}
      </div>
    `;

    usageCardsGrid.appendChild(card);
  });
}

init().catch((err) => console.error("Workstation initialization failed:", err));

