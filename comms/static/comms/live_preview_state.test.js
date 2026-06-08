const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

class FakeElement {
  constructor() {
    this.dataset = {};
    this.classList = { toggle() {} };
    this.style = {};
    this.value = "";
    this.textContent = "";
    this.innerHTML = "";
    this.scrollTop = 0;
    this.scrollLeft = 0;
    this.clientWidth = 800;
  }

  addEventListener() {}
  focus() {}
  querySelector() { return null; }
  querySelectorAll() { return []; }
  getBoundingClientRect() {
    return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 };
  }
}

function createHarness() {
  const textarea = new FakeElement();
  const intentSelect = new FakeElement();
  intentSelect.value = "request";
  const statusNode = new FakeElement();
  const panelNode = new FakeElement();
  const listNode = new FakeElement();
  const scoreListNode = new FakeElement();
  const draftShell = new FakeElement();
  const draftHighlightNode = new FakeElement();
  const draftSuggestionLayer = new FakeElement();
  const csrfInput = new FakeElement();
  const modeInput = new FakeElement();
  modeInput.value = "lightweight";

  const form = new FakeElement();
  form.dataset = {
    previewDebounceMs: "999999",
    previewUrl: "/inline-preview/",
  };
  form.querySelector = (selector) => {
    if (selector.includes("textarea")) return textarea;
    if (selector === 'select[name="intent"]') return intentSelect;
    if (selector === "[data-live-preview-panel]") return panelNode;
    if (selector === "[data-live-preview-status]") return statusNode;
    if (selector === "[data-live-preview-list]") return listNode;
    if (selector === "[data-live-score-list]") return scoreListNode;
    if (selector === "[data-draft-shell]") return draftShell;
    if (selector === "[data-draft-highlight]") return draftHighlightNode;
    if (selector === "[data-draft-suggestion-layer]") return draftSuggestionLayer;
    if (selector === 'input[name="csrfmiddlewaretoken"]') return csrfInput;
    if (selector === 'input[name="suggestion_mode"]:checked') return modeInput;
    return null;
  };
  form.querySelectorAll = (selector) => (
    selector === 'input[name="suggestion_mode"]' ? [modeInput] : []
  );

  const windowObject = {
    __LIVE_PREVIEW_TEST_HOOKS__: true,
    addEventListener() {},
  };
  const documentObject = {
    querySelector(selector) {
      return selector === ".live-preview-form" ? form : null;
    },
  };

  const context = {
    assert,
    console,
    clearTimeout() {},
    document: documentObject,
    fetch() {
      throw new Error("fetch should not be called in state tests");
    },
    setTimeout() { return 0; },
    window: windowObject,
  };
  windowObject.document = documentObject;

  const scriptPath = path.join(__dirname, "live_preview.js");
  const script = fs.readFileSync(scriptPath, "utf8");
  vm.runInNewContext(script, context, { filename: scriptPath });

  return {
    api: windowObject.__livePreviewTestApi,
    textarea,
  };
}

function testAcceptingCorrectionPreservesBroadSuggestionInSameMark() {
  const { api } = createHarness();
  const draft = "Regarding the projct we discussed, can you make it shiny?";
  api.setDraft(draft);
  const seeded = api.seedReviewWindow({
    start: 0,
    end: draft.length,
    text: draft,
    suggestions: [
      {
        target_text: "projct",
        suggested_replacement: "project",
        issue: "Typo",
        reason: "Correct spelling.",
        affected_scores: { clarity: 3 },
      },
      {
        target_text: "Regarding the projct we discussed",
        suggested_replacement: "Regarding the project we discussed, can you outline the next step?",
        issue: "Clarify ask",
        reason: "Turns the setup into an actionable request.",
        affected_scores: { clarity: 6 },
      },
    ],
  });

  const correctionId = api.state().suggestions.find((suggestion) => suggestion.target_text === "projct")._index;
  api.acceptSuggestion(correctionId);
  const state = api.state();

  assert.strictEqual(state.draft, "Regarding the project we discussed, can you make it shiny?");
  assert.strictEqual(state.reviewWindows.length, 1);
  assert.strictEqual(state.reviewWindows[0].status, "suggested");
  assert.strictEqual(state.suggestions.length, 1);
  assert.strictEqual(state.suggestions[0].target_text, "Regarding the project we discussed");
  assert.ok(state.suggestions[0]._range);
}

function testAcceptingOneSuggestionPreservesOtherSuggestionInSameMark() {
  const { api } = createHarness();
  const draft = "alos can we make it shiny and noce?";
  api.setDraft(draft);
  const seeded = api.seedReviewWindow({
    start: 0,
    end: draft.length,
    text: draft,
    suggestions: [
      {
        target_text: "alos",
        suggested_replacement: "also",
        issue: "Typo",
        reason: "Correct spelling.",
      },
      {
        target_text: "noce",
        suggested_replacement: "nice",
        issue: "Typo",
        reason: "Correct spelling.",
      },
    ],
  });

  const firstCorrectionId = api.state().suggestions.find((suggestion) => suggestion.target_text === "alos")._index;
  api.acceptSuggestion(firstCorrectionId);
  const state = api.state();

  assert.strictEqual(state.draft, "also can we make it shiny and noce?");
  assert.strictEqual(state.reviewWindows.length, 1);
  assert.strictEqual(state.suggestions.length, 1);
  assert.strictEqual(state.suggestions[0].target_text, "noce");
}

function testAcceptingEarlierSuggestionShiftsLaterMark() {
  const { api } = createHarness();
  const first = "projct here.";
  const second = "Let's do it good and great and shiny!";
  const draft = `${first} ${second}`;
  api.setDraft(draft);
  const firstWindow = api.seedReviewWindow({
    start: 0,
    end: first.length,
    text: first,
    suggestions: [
      {
        target_text: "projct",
        suggested_replacement: "project",
        issue: "Typo",
        reason: "Correct spelling.",
      },
    ],
  });
  api.seedReviewWindow({
    start: first.length + 1,
    end: draft.length,
    text: second,
    suggestions: [
      {
        target_text: second,
        suggested_replacement: "Let's make it polished, user-facing, and fully functional.",
        issue: "Vague polish language",
        reason: "Make the product goal concrete.",
      },
    ],
  });

  api.acceptSuggestion(firstWindow.suggestionIds[0]);
  const state = api.state();

  assert.strictEqual(state.draft, `project here. ${second}`);
  assert.strictEqual(state.reviewWindows.length, 1);
  assert.strictEqual(state.suggestions.length, 1);
  assert.strictEqual(state.suggestions[0].target_text, second);
  assert.strictEqual(state.reviewWindows[0].start, "project here. ".length);
}

function testAcceptingEarlierSuggestionPreservesLaterCheckingWindowAndResponse() {
  const { api } = createHarness();
  const first = "Regarding the prokject we talked about, can you make it good?";
  const second = "Morover, how to fix the features that are not inline with out values?";
  const draft = `${first} ${second}`;
  api.setDraft(draft);
  api.seedReviewWindow({
    start: 0,
    end: first.length,
    text: first,
    suggestions: [
      {
        target_text: "prokject",
        suggested_replacement: "project",
        issue: "Typo",
        reason: "Correct spelling.",
      },
      {
        target_text: "can you make it good",
        suggested_replacement: "can you confirm the implementation is complete and ready for the customer demo",
        issue: "Vague ask",
        reason: "Makes the request specific.",
      },
    ],
  });
  const secondWindow = api.seedReviewWindow({
    start: first.length + 1,
    end: draft.length,
    text: second,
    status: "checking",
    suggestions: [],
  });

  const typoId = api.state().suggestions.find((suggestion) => suggestion.target_text === "prokject")._index;
  api.acceptSuggestion(typoId);
  let state = api.state();
  const fixedFirst = "Regarding the project we talked about, can you make it good?";
  const shiftedSecondStart = fixedFirst.length + 1;
  const checkingWindow = state.reviewWindows.find((windowItem) => windowItem.id === secondWindow.reviewId);

  assert.ok(checkingWindow);
  assert.strictEqual(checkingWindow.status, "checking");
  assert.strictEqual(checkingWindow.start, shiftedSecondStart);
  assert.strictEqual(checkingWindow.text, second);
  assert.strictEqual(state.draft, `${fixedFirst} ${second}`);

  const vagueAskId = state.suggestions.find((suggestion) => suggestion.target_text === "can you make it good")._index;
  api.acceptSuggestion(vagueAskId);
  state = api.state();
  const expandedFirst = "Regarding the project we talked about, can you confirm the implementation is complete and ready for the customer demo?";
  const expandedSecondStart = expandedFirst.length + 1;
  const shiftedCheckingWindow = state.reviewWindows.find((windowItem) => windowItem.id === secondWindow.reviewId);

  assert.ok(shiftedCheckingWindow);
  assert.strictEqual(shiftedCheckingWindow.status, "checking");
  assert.strictEqual(shiftedCheckingWindow.start, expandedSecondStart);
  assert.strictEqual(shiftedCheckingWindow.text, second);
  assert.strictEqual(state.draft, `${expandedFirst} ${second}`);

  api.attachSuggestions(secondWindow.reviewId, [
    {
      target_text: "Morover",
      suggested_replacement: "Moreover",
      issue: "Typo",
      reason: "Correct spelling.",
    },
    {
      target_text: "out values",
      suggested_replacement: "our values",
      issue: "Typo",
      reason: "Clarifies the reference to company values.",
    },
  ]);
  state = api.state();
  const secondSuggestions = state.suggestions.filter((suggestion) => suggestion._reviewId === secondWindow.reviewId);

  assert.strictEqual(secondSuggestions.length, 2);
  assert.strictEqual(
    JSON.stringify(secondSuggestions.map((suggestion) => suggestion.target_text)),
    JSON.stringify(["Morover", "out values"]),
  );
}

testAcceptingCorrectionPreservesBroadSuggestionInSameMark();
testAcceptingOneSuggestionPreservesOtherSuggestionInSameMark();
testAcceptingEarlierSuggestionShiftsLaterMark();
testAcceptingEarlierSuggestionPreservesLaterCheckingWindowAndResponse();

console.log("live_preview_state tests passed");
