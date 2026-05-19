const AUTO_CROP_REFERENCE_TOP = 112;
const AUTO_CROP_REFERENCE_HEIGHT = 1296;
const videoInput = document.querySelector("#videoInput");
const fileDrop = document.querySelector("#fileDrop");
const preview = document.querySelector("#preview");
const stage = document.querySelector("#stage");
const dateInput = document.querySelector("#dateInput");
const timeInput = document.querySelector("#timeInput");
const removeAudio = document.querySelector("#removeAudio");
const fileName = document.querySelector("#fileName");
const form = document.querySelector("#processForm");
const submitButton = document.querySelector("#submitButton");
const statusText = document.querySelector("#statusText");
const mediaLoader = document.querySelector("#mediaLoader");
const generationPanel = document.querySelector("#generationPanel");
const progressFill = document.querySelector("#progressFill");
const progressPercent = document.querySelector("#progressPercent");
const progressText = document.querySelector("#progressText");
const resultPanel = document.querySelector("#resultPanel");
const resultVideo = document.querySelector("#resultVideo");
const downloadLink = document.querySelector("#downloadLink");

let selectedFile = null;
let currentObjectUrl = null;

function setStatus(text, isError = false) {
  statusText.textContent = text;
  statusText.classList.toggle("error", isError);
}

function currentStampText() {
  return `${dateInput.value || "2026-03-04"} ${timeInput.value || "11:50:47"}`;
}

function autoCropTop(sourceHeight) {
  return Math.max(0, Math.round((sourceHeight * AUTO_CROP_REFERENCE_TOP) / AUTO_CROP_REFERENCE_HEIGHT));
}

function updatePreviewLayout() {
  if (!preview.videoWidth || !preview.videoHeight) return;

  const rawHeight = (stage.clientWidth * preview.videoHeight) / preview.videoWidth;
  const cropPx = autoCropTop(preview.videoHeight);
  const cropDisplay = (cropPx / preview.videoHeight) * rawHeight;
  const visibleHeight = Math.max(180, rawHeight - cropDisplay);
  const scale = stage.clientWidth / preview.videoWidth;

  stage.classList.add("has-video");
  stage.style.height = `${visibleHeight}px`;
  stage.style.setProperty("--video-offset", `-${cropDisplay}px`);
}

function showMediaLoading(text) {
  fileDrop.classList.add("is-loading");
  mediaLoader.classList.add("is-visible");
  mediaLoader.querySelector("p").textContent = text;
}

function hideMediaLoading() {
  fileDrop.classList.remove("is-loading");
  mediaLoader.classList.remove("is-visible");
}

function setProgress(percent, text) {
  const value = Math.max(0, Math.min(Math.round(percent), 100));
  progressFill.style.width = `${value}%`;
  progressPercent.textContent = `${value}%`;
  progressText.textContent = text;
}

function loadFile(file) {
  selectedFile = file;
  fileName.textContent = file.name;
  resultPanel.hidden = true;
  resultVideo.removeAttribute("src");
  showMediaLoading("Готовлю превью...");
  setStatus("Видео выбрано. Открываю превью...");

  if (currentObjectUrl) URL.revokeObjectURL(currentObjectUrl);
  currentObjectUrl = URL.createObjectURL(file);
  preview.src = currentObjectUrl;
  preview.load();
}

videoInput.addEventListener("change", () => {
  const file = videoInput.files?.[0];
  if (file) loadFile(file);
});

preview.addEventListener("loadedmetadata", () => {
  updatePreviewLayout();
  hideMediaLoading();
  setStatus("Видео готово. Обрезка сверху будет сделана автоматически.");
});

preview.addEventListener("canplay", () => {
  hideMediaLoading();
  updatePreviewLayout();
});

preview.addEventListener("error", () => {
  hideMediaLoading();
  setStatus("Не удалось открыть превью этого видео. Можно попробовать обработать другой MP4/MOV.", true);
});

window.addEventListener("resize", updatePreviewLayout);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedFile) {
    setStatus("Сначала выберите видео.", true);
    return;
  }

  const data = new FormData();
  data.append("video", selectedFile);
  data.append("date", dateInput.value);
  data.append("time", timeInput.value);
  data.append("removeAudio", removeAudio.checked ? "1" : "0");

  submitButton.disabled = true;
  generationPanel.hidden = false;
  resultPanel.hidden = true;
  setProgress(0, "Загружаю видео на сервер...");
  setStatus("Генерация запущена...");

  try {
    const payload = await uploadJob(data);
    if (!payload.ok) throw new Error(payload.error || "Ошибка загрузки");
    if (payload.outputBlobUrl) {
      resultVideo.src = payload.outputBlobUrl;
      downloadLink.href = payload.outputBlobUrl;
      downloadLink.download = payload.filename || "ready-video.mp4";
      resultPanel.hidden = false;
      generationPanel.hidden = true;
      setProgress(100, "Готово");
      setStatus("Готово. Видео обработано.");
      return;
    }
    await watchJob(payload.jobId);
  } catch (error) {
    setStatus(error.message, true);
    setProgress(100, error.message);
  } finally {
    submitButton.disabled = false;
  }
});

function uploadJob(data) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/process");
    xhr.responseType = "blob";
    xhr.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) return;
      setProgress((event.loaded / event.total) * 10, "Загружаю видео на сервер...");
    });
    xhr.addEventListener("load", async () => {
      const contentType = xhr.getResponseHeader("Content-Type") || "";
      if (!xhr.status || xhr.status >= 400) {
        const text = xhr.response ? await xhr.response.text() : "";
        reject(new Error(readServerError(text, xhr.status)));
        return;
      }

      if (contentType.includes("video/mp4")) {
        const blobUrl = URL.createObjectURL(xhr.response);
        resolve({
          ok: true,
          outputBlobUrl: blobUrl,
          filename: xhr.getResponseHeader("X-Filename") || "ready-video.mp4",
        });
        return;
      }

      try {
        const text = xhr.response ? await xhr.response.text() : "";
        resolve(JSON.parse(text));
      } catch (error) {
        reject(new Error("Сервер вернул непонятный ответ. Возможно, Vercel прервал обработку по лимиту времени или размера файла."));
      }
    });
    xhr.addEventListener("error", () => reject(new Error("Не удалось загрузить видео")));
    xhr.send(data);
  });
}

function readServerError(text, status) {
  try {
    const payload = JSON.parse(text);
    if (payload.error) return payload.error;
  } catch (error) {
    // Keep the readable fallback below.
  }
  if (status === 413) return "Видео слишком большое для Vercel.";
  if (status === 504 || status === 500) return "Vercel не успел обработать видео. Попробуйте короткий файл или локальный сервер.";
  return `Ошибка сервера ${status}.`;
}

async function watchJob(jobId) {
  while (true) {
    const response = await fetch(`/jobs/${jobId}`);
    const payload = await response.json();
    if (!payload.ok) throw new Error(payload.error || "Задача не найдена");

    setProgress(payload.progress || 0, payload.message || "Обработка...");

    if (payload.status === "done") {
      resultVideo.src = payload.outputUrl;
      downloadLink.href = payload.outputUrl;
      downloadLink.download = payload.filename;
      resultPanel.hidden = false;
      generationPanel.hidden = true;
      setStatus(`Готово. Верх обрезан на ${payload.meta.cropTop} px, размер: ${payload.meta.width}x${payload.meta.height}.`);
      return;
    }

    if (payload.status === "error") {
      throw new Error(payload.error || payload.message || "Ошибка обработки");
    }

    await new Promise((resolve) => setTimeout(resolve, 700));
  }
}
