const host = document.getElementById("responses");
const formId = host.dataset.form;

function sittingNode(column, answer) {
  const node = document.getElementById("sitting-template").content.firstElementChild.cloneNode(true);
  node.querySelector("h3").textContent = column.prompt;
  const text = node.querySelector(".answer");
  if (answer.text) text.textContent = answer.text;
  else {
    text.textContent = "Empty";
    text.classList.add("meta");
  }
  if (/^[0-9a-f]{16}$/.test(answer.session || "")) {
    const frame = document.createElement("iframe");
    frame.className = "writing";
    frame.src = "/report/" + answer.session;
    frame.title = "How this answer was written: " + column.prompt;
    node.appendChild(frame);
  }
  return node;
}

async function loadAnswers() {
  let payload;
  try {
    payload = await api("GET", "/api/forms/" + formId + "/responses");
  } catch (err) {
    document.getElementById("form-title").textContent = "Langdon Forms";
    document.getElementById("lede").textContent = err.message;
    document.getElementById("lede").classList.add("status", "warn");
    return;
  }
  const form = payload.form;
  const responses = payload.responses;
  document.title = form.title + " — responses";
  document.getElementById("form-title").textContent = form.title;
  const columns = form.questions.map((question) => ({ id: question.id, prompt: question.prompt }));
  const seen = new Set(columns.map((column) => column.id));
  for (const response of responses) {
    for (const [qid, answer] of Object.entries(response.answers || {})) {
      if (!seen.has(qid)) {
        seen.add(qid);
        columns.push({ id: qid, prompt: answer.prompt });
      }
    }
  }
  const lede = responses.length
    ? `${responses.length === 1 ? "1 response." : responses.length + " responses."} Play an answer to watch how it was written.`
    : "Responses will show up here, with a replay of how each answer was written.";
  document.getElementById("lede").textContent = lede;
  if (!responses.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No responses yet.";
    host.appendChild(empty);
    return;
  }
  const template = document.getElementById("response-template");
  responses.forEach((response, index) => {
    const article = template.content.firstElementChild.cloneNode(true);
    const when = response.submitted_unix_ms ? new Date(response.submitted_unix_ms).toLocaleString() : "";
    article.querySelector("h2").textContent = "Response " + (index + 1) + (when ? " · " + when : "");
    const sittings = article.querySelector(".sittings");
    columns.forEach((column) => {
      const answer = (response.answers || {})[column.id];
      if (answer) sittings.appendChild(sittingNode(column, answer));
    });
    host.appendChild(article);
  });
}

loadAnswers();
