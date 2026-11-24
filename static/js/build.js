let form = JSON.parse(document.getElementById("form-data").textContent);
if (!Array.isArray(form.questions)) form.questions = [];
let timer = 0;
let focusIndex = -1;

const kindNote = {
  short: "A line or two. Useful for a name, a title, or a short reply.",
  long: "Room to write. The writing is kept with the finished answer."
};

function drawQuestions() {
  const host = document.getElementById("questions");
  const template = document.getElementById("question-template");
  host.replaceChildren();
  if (!form.questions.length) {
    const note = document.createElement("p");
    note.className = "empty";
    note.textContent = "No questions yet. Add one for people to answer.";
    host.appendChild(note);
    return;
  }
  form.questions.forEach((question, index) => {
    const node = template.content.firstElementChild.cloneNode(true);
    node.querySelector(".q-number").textContent = "Question " + (index + 1);
    const prompt = node.querySelector("[data-prompt]");
    prompt.value = question.prompt || "";
    prompt.addEventListener("input", () => {
      form.questions[index].prompt = prompt.value;
      queue();
    });
    node.querySelector("[data-remove]").addEventListener("click", () => {
      form.questions.splice(index, 1);
      drawQuestions();
      queue();
    });
    const selected = question.kind === "short" ? "short" : "long";
    node.querySelector(".kind-note").textContent = kindNote[selected];
    node.querySelectorAll("[data-kind]").forEach((button) => {
      button.classList.toggle("on", button.dataset.kind === selected);
      button.addEventListener("click", () => {
        form.questions[index].kind = button.dataset.kind;
        focusIndex = index;
        drawQuestions();
        queue();
      });
    });
    host.appendChild(node);
    if (focusIndex === index) prompt.focus();
  });
  focusIndex = -1;
}

function queue() {
  clearTimeout(timer);
  timer = setTimeout(save, 350);
}

async function save() {
  const status = document.getElementById("status");
  try {
    const saved = await api("PUT", "/api/forms/" + form.id, {
      title: form.title,
      description: form.description,
      questions: form.questions
    });
    form = saved;
    document.title = (form.title || "Untitled form") + " — edit";
    if (status) {
      status.textContent = "Changes saved";
      status.classList.remove("warn");
    }
  } catch (err) {
    if (status) {
      status.textContent = err.message;
      status.classList.add("warn");
    }
  }
}

const share = document.getElementById("share-link");
share.value = location.origin + "/f/" + form.id;
share.addEventListener("focus", () => share.select());

document.getElementById("copy-link").addEventListener("click", async () => {
  const status = document.getElementById("status");
  status.classList.remove("warn");
  try {
    await navigator.clipboard.writeText(share.value);
    status.textContent = "Link copied";
  } catch (err) {
    share.focus();
    share.select();
    status.textContent = "The link is selected. Copy it from there.";
  }
});

document.getElementById("title").addEventListener("input", (event) => {
  form.title = event.target.value;
  queue();
});

document.getElementById("description").addEventListener("input", (event) => {
  form.description = event.target.value;
  queue();
});

document.getElementById("add").addEventListener("click", () => {
  const bytes = new Uint8Array(4);
  crypto.getRandomValues(bytes);
  const qid = "q" + [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  form.questions.push({ id: qid, prompt: "", kind: "long" });
  focusIndex = form.questions.length - 1;
  drawQuestions();
  queue();
});

document.getElementById("delete-form").addEventListener("click", async () => {
  if (!confirm("Delete this form and every response it has collected?")) return;
  await api("DELETE", "/api/forms/" + form.id);
  location.href = "/forms";
});

document.title = (form.title || "Untitled form") + " — edit";
if (new URLSearchParams(location.search).has("new")) {
  const blank = form.questions.findIndex((question) => !question.prompt);
  focusIndex = blank >= 0 ? blank : -1;
  history.replaceState(null, "", location.pathname);
}
drawQuestions();
