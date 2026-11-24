const pages = JSON.parse(document.getElementById("docs-pages").textContent);
const prose = document.getElementById("prose");
const side = [...document.querySelectorAll(".side a")];

function show(id) {
  const page = pages.find((item) => item.id === id) || pages[0];
  prose.replaceChildren();
  const article = document.createElement("article");
  article.className = "on";
  article.id = page.id;
  article.innerHTML = page.html;
  prose.appendChild(article);
  side.forEach((link) => link.classList.toggle("on", link.getAttribute("href") === "#" + page.id));
  if (location.hash !== "#" + page.id) history.replaceState(null, "", "#" + page.id);
  window.scrollTo(0, 0);
}

side.forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    show(link.getAttribute("href").slice(1));
  });
});
show(location.hash.slice(1));
