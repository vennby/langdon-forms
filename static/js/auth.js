const creating = document.getElementById("auth-form").dataset.create === "1";
const choice = document.querySelector(".choice");

if (choice) {
  choice.querySelectorAll("[data-kind]").forEach((button) => {
    button.addEventListener("click", () => {
      const organization = button.dataset.kind === "organization";
      choice.querySelectorAll("[data-kind]").forEach((item) => {
        item.classList.toggle("on", item === button);
      });
      document.getElementById("individual-name").hidden = organization;
      document.getElementById("org-name").hidden = !organization;
      document.getElementById("org-contact").hidden = !organization;
    });
  });
}

const first = document.getElementById(creating ? "name" : "email");
if (first) first.focus({ preventScroll: true });

document.getElementById("auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.getElementById("auth-submit");
  const status = document.getElementById("status");
  button.disabled = true;
  status.textContent = "";
  status.classList.remove("warn");
  const body = {
    email: document.getElementById("email").value,
    password: document.getElementById("password").value
  };
  if (creating) {
    const organization = !document.getElementById("org-name").hidden;
    body.kind = organization ? "organization" : "individual";
    body.name = document.getElementById(organization ? "org" : "name").value;
    if (organization) body.contact = document.getElementById("contact").value;
  }
  try {
    await api("POST", creating ? "/api/accounts" : "/api/session", body);
    location.href = window.langdon.next || "/forms";
  } catch (err) {
    button.disabled = false;
    status.textContent = err.message;
    status.classList.add("warn");
  }
});
