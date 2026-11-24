const host = document.getElementById("fill");
const formId = host.dataset.form;

async function openForm() {
  const error = document.getElementById("fill-error");
  let form;
  try {
    form = await api("GET", "/api/forms/" + formId);
  } catch (err) {
    document.getElementById("form-title").textContent = "Form";
    error.hidden = false;
    error.textContent = err.message;
    return;
  }
  document.title = form.title;
  document.getElementById("form-title").textContent = form.title;
  const lede = document.getElementById("lede");
  if (form.description) {
    lede.hidden = false;
    lede.textContent = form.description;
  }
  let draft;
  try {
    draft = await api("POST", "/api/forms/" + formId + "/responses", {});
  } catch (err) {
    error.hidden = false;
    error.textContent = err.message;
    return;
  }
  if (!form.description) {
    lede.hidden = false;
    lede.textContent = "Writing is saved with the answer.";
  }
  const fields = document.getElementById("fields");
  const template = document.getElementById("field-template");
  const recorders = draft.questions.map((question) => {
    const node = template.content.firstElementChild.cloneNode(true);
    node.querySelector("span").textContent = question.prompt;
    const area = node.querySelector("textarea");
    area.dataset.qid = question.id;
    area.rows = question.kind === "short" ? 2 : 7;
    fields.appendChild(node);
    return watch(area, question.token);
  });
  const sheet = document.getElementById("sheet");
  sheet.hidden = false;
  sheet.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = document.getElementById("submit");
    const status = document.getElementById("status");
    button.disabled = true;
    status.textContent = "Saving";
    status.classList.remove("warn");
    try {
      await Promise.race([
        Promise.all(recorders.map((recorder) => recorder.flush())),
        new Promise((resolve) => setTimeout(resolve, 9000))
      ]);
      const answers = {};
      for (const question of draft.questions) {
        answers[question.id] = fields.querySelector(`[data-qid="${CSS.escape(question.id)}"]`).value;
      }
      await api("POST", `/api/forms/${formId}/responses/${draft.id}/submit`, { answers });
      recorders.forEach((recorder) => recorder.stop());
      sheet.hidden = true;
      lede.hidden = true;
      document.getElementById("form-title").hidden = true;
      document.getElementById("done").hidden = false;
    } catch (err) {
      button.disabled = false;
      status.textContent = err.message;
      status.classList.add("warn");
    }
  });
}

openForm();
