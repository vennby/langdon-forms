const sessionState = {
  csrf: window.langdon.csrf || "",
  account: window.langdon.account || null
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[ch]));
}

async function api(method, path, body) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && method !== "HEAD") headers["X-CSRF-Token"] = sessionState.csrf || "";
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const text = await response.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch (err) { data = null; }
  }
  if (data && typeof data.csrf === "string") sessionState.csrf = data.csrf;
  if (!response.ok) throw new Error(text || response.statusText);
  return data;
}

const profile = document.getElementById("profile");
const profileButton = document.getElementById("profile-button");
const profileMenu = document.getElementById("profile-menu");
if (profile && profileButton && profileMenu) {
  const setOpen = (open) => {
    profile.classList.toggle("open", open);
    profileButton.setAttribute("aria-expanded", open ? "true" : "false");
    profileMenu.hidden = !open;
  };
  profileButton.addEventListener("click", (event) => {
    event.stopPropagation();
    setOpen(profileMenu.hidden);
  });
  document.addEventListener("click", (event) => {
    if (!profile.contains(event.target)) setOpen(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setOpen(false);
  });
}

const logout = document.getElementById("logout");
if (logout) {
  logout.addEventListener("click", async () => {
    try { await api("DELETE", "/api/session"); } catch (err) { /* leave anyway */ }
    location.href = "/";
  });
}
