function scrollDown() {
  const start = document.body.scrollTop;
  const distance = document.getElementById("about").offsetTop - start;
  if (distance < 8) return;
  document.body.classList.add("gliding");
  const duration = 980;
  const began = performance.now();
  function step(now) {
    const t = Math.min(1, (now - began) / duration);
    const eased = 0.5 - Math.cos(Math.PI * t) / 2;
    document.body.scrollTop = start + distance * eased;
    if (t < 1) {
      requestAnimationFrame(step);
      return;
    }
    document.body.classList.remove("gliding");
  }
  requestAnimationFrame(step);
}

function cueOnce(clip) {
  clip.addEventListener("ended", () => {
    clip.loop = true;
    if (document.body.scrollTop < window.innerHeight * 0.35) scrollDown();
    clip.play().catch(() => {});
  }, { once: true });
}

document.getElementById("beckon").onclick = (event) => {
  event.preventDefault();
  scrollDown();
};
const clip = document.querySelector(".reel video");
if (clip) {
  clip.play().catch(() => {});
  cueOnce(clip);
}
