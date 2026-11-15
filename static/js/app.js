/* Site-wide behaviour: photo lightbox, confirm-before-submit, copy-to-clipboard. */
(function () {
  "use strict";

  // ---- confirm-before-submit for plain forms (HTMX forms use hx-confirm) ----
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (form.dataset && form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
      event.preventDefault();
    }
  }, true);

  // ---- copy to clipboard (navigator.clipboard needs HTTPS, so fall back to execCommand) ----
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-copy-target]");
    if (!button) return;
    var input = document.querySelector(button.dataset.copyTarget);
    if (!input) return;
    var done = function () {
      var label = button.textContent;
      button.textContent = "Copied";
      window.setTimeout(function () { button.textContent = label; }, 1500);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(input.value).then(done);
    } else {
      input.select();
      input.setSelectionRange(0, input.value.length);
      try { document.execCommand("copy"); done(); } catch (e) { /* user can copy manually */ }
    }
  });

  // ---- lightbox ----
  var modalEl = document.getElementById("lightbox");
  if (!modalEl || typeof bootstrap === "undefined") return;

  var modal = bootstrap.Modal.getOrCreateInstance(modalEl);
  var img = document.getElementById("lightbox-image");
  var caption = document.getElementById("lightbox-caption");
  var counter = document.getElementById("lightbox-counter");
  var prevBtn = document.getElementById("lightbox-prev");
  var nextBtn = document.getElementById("lightbox-next");
  var deleteForm = document.getElementById("lightbox-delete-form");
  var items = [];
  var index = 0;

  function show(i) {
    if (i < 0 || i >= items.length) return;
    index = i;
    var link = items[i];
    img.src = link.href;
    caption.textContent = link.dataset.caption || "";
    counter.textContent = (i + 1) + " / " + items.length;
    prevBtn.disabled = i === 0;
    nextBtn.disabled = i === items.length - 1;
    if (deleteForm) {
      var url = link.dataset.deleteUrl;
      deleteForm.hidden = !url;
      if (url) deleteForm.action = url;
    }
  }

  document.addEventListener("click", function (event) {
    var link = event.target.closest(".lightbox-link");
    if (!link) return;
    event.preventDefault();
    var gallery = link.dataset.gallery;
    items = Array.prototype.slice.call(
      document.querySelectorAll('.lightbox-link[data-gallery="' + gallery + '"]')
    );
    show(items.indexOf(link));
    modal.show();
  });

  prevBtn.addEventListener("click", function () { show(index - 1); });
  nextBtn.addEventListener("click", function () { show(index + 1); });

  modalEl.addEventListener("keydown", function (event) {
    if (event.key === "ArrowLeft") show(index - 1);
    if (event.key === "ArrowRight") show(index + 1);
  });

  // Swipe left/right on touch screens.
  var touchX = null;
  modalEl.addEventListener("touchstart", function (e) { touchX = e.changedTouches[0].clientX; }, { passive: true });
  modalEl.addEventListener("touchend", function (e) {
    if (touchX === null) return;
    var dx = e.changedTouches[0].clientX - touchX;
    touchX = null;
    if (Math.abs(dx) > 50) show(dx > 0 ? index - 1 : index + 1);
  }, { passive: true });

  modalEl.addEventListener("hidden.bs.modal", function () { img.removeAttribute("src"); });
})();
