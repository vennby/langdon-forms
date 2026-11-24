const composer = document.getElementById("composer");
const title = document.getElementById("new-title");
const description = document.getElementById("new-description");
const status = document.getElementById("create-status");

function openComposer() {
  composer.hidden = false;
  title.focus();
}

document.getElementById("new-form").addEventListener("click", openComposer);
document.getElementById("cancel-form").addEventListener("click", () => {
  composer.hidden = true;
  title.value = "";
  description.value = "";
  status.textContent = "";
  status.classList.remove("warn");
});

document.getElementById("create-form").addEventListener("click", async () => {
  const button = document.getElementById("create-form");
  const name = title.value.trim();
  status.classList.remove("warn");
  if (!name) {
    status.textContent = "Give the form a title.";
    status.classList.add("warn");
    title.focus();
    return;
  }
  button.disabled = true;
  status.textContent = "";
  try {
    const form = await api("POST", "/api/forms", { title: name });
    const note = description.value.trim();
    if (note) {
      await api("PUT", "/api/forms/" + form.id, {
        title: name,
        description: note,
        questions: form.questions
      });
    }
    location.href = "/f/" + form.id + "/build?new=1";
  } catch (err) {
    button.disabled = false;
    status.textContent = err.message;
    status.classList.add("warn");
  }
});

title.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    document.getElementById("create-form").click();
  }
});

const search = document.getElementById("form-search");
if (search) {
  search.addEventListener("input", (event) => {
    const query = event.target.value.trim().toLocaleLowerCase();
    let visible = 0;
    document.querySelectorAll(".form-card").forEach((card) => {
      const matches = card.textContent.toLocaleLowerCase().includes(query);
      card.hidden = !matches;
      if (matches) visible += 1;
    });
    document.getElementById("no-matches").hidden = visible > 0;
  });
}
