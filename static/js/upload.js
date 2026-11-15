/* Photo upload: Dropzone box + explicit "Take photo" / "Choose from gallery" inputs.
 * Everything goes through the same Dropzone queue, so phone and desktop uploads get
 * the same per-file progress, error messages and in-browser resizing. */
(function () {
  "use strict";

  var form = document.getElementById("photo-dropzone");
  if (!form || typeof Dropzone === "undefined") return;

  Dropzone.autoDiscover = false;
  var csrf = form.querySelector("input[name=csrfmiddlewaretoken]").value;
  var messages = document.getElementById("upload-messages");

  function isHeic(file) {
    return /hei[cf]/i.test(file.type || "") || /\.(heic|heif)$/i.test(file.name || "");
  }

  function showMessage(kind, text) {
    var div = document.createElement("div");
    div.className = "alert alert-" + kind + " alert-dismissible fade show";
    div.setAttribute("role", "alert");
    div.textContent = text;
    var close = document.createElement("button");
    close.type = "button";
    close.className = "btn-close";
    close.setAttribute("data-bs-dismiss", "alert");
    close.setAttribute("aria-label", "Close");
    div.appendChild(close);
    messages.appendChild(div);
  }

  var dz = new Dropzone(form, {
    url: form.action,
    paramName: "file",
    maxFilesize: 20, // MB, checked again on the server
    acceptedFiles: "image/*,.heic,.heif",
    parallelUploads: 2,
    timeout: 180000,
    headers: { "X-CSRFToken": csrf },
    resizeWidth: 2000,
    resizeQuality: 0.8,
    thumbnailWidth: 120,
    thumbnailHeight: 120,
    addRemoveLinks: false,
    dictDefaultMessage: "Drop photos here or tap to choose files",
    dictFileTooBig: "File is too big ({{filesize}} MB). Max {{maxFilesize}} MB.",
    dictInvalidFileType: "Not an image file.",
    dictResponseError: "Server error ({{statusCode}}).",

    // Browsers cannot decode HEIC in a canvas: send those untouched and let the server convert.
    // When Dropzone does resize, the canvas pixels are already upright but the original EXIF
    // (including Orientation) is copied back, so tell the server not to rotate again.
    transformFile: function (file, done) {
      if (isHeic(file) || !/^image\//.test(file.type || "")) {
        return done(file);
      }
      return this.resizeImage(
        file, this.options.resizeWidth, this.options.resizeHeight, this.options.resizeMethod,
        function (result) {
          file.clientOriented = result !== file;
          done(result);
        }
      );
    },

    init: function () {
      this.on("sending", function (file, xhr, formData) {
        if (file.clientOriented) formData.append("client_oriented", "1");
      });
      this.on("success", function (file, response) {
        if (response && response.warning) showMessage("warning", response.warning);
        htmx.trigger(document.body, "photos-changed");
        var self = this;
        window.setTimeout(function () { self.removeFile(file); }, 1500);
      });
      this.on("error", function (file, message) {
        var text = typeof message === "string" ? message : (message && message.error) || "Upload failed.";
        var el = file.previewElement && file.previewElement.querySelector("[data-dz-errormessage]");
        if (el) el.textContent = text;
      });
    }
  });

  // "Take photo" and "Choose from gallery" feed their files into the same queue.
  document.querySelectorAll("[data-dropzone-input]").forEach(function (input) {
    input.addEventListener("change", function () {
      Array.prototype.forEach.call(input.files, function (file) { dz.addFile(file); });
      input.value = "";
    });
  });
})();
