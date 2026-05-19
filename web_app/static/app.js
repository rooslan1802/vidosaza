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
  setProgress(0, "Загружаю видео на компьютер...");
  setStatus("Генерация запущена...");

  try {
    const payload = await uploadJob(data);
    if (!payload.ok) throw new Error(payload.error || "Ошибка загрузки");
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
    xhr.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) return;
      setProgress((event.loaded / event.total) * 10, "Загружаю видео на компьютер...");
    });
    xhr.addEventListener("load", () => {
      try {
        resolve(JSON.parse(xhr.responseText));
      } catch (error) {
        reject(new Error("Сервер вернул непонятный ответ"));
      }
    });
    xhr.addEventListener("error", () => reject(new Error("Не удалось загрузить видео")));
    xhr.send(data);
  });
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
