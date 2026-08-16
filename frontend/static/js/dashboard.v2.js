(function () {
  // Read Django CSRF token from cookie for authenticated POST requests.
  function getCookie(name) {
    const cookies = document.cookie ? document.cookie.split(";") : [];
    for (const cookie of cookies) {
      const trimmed = cookie.trim();
      if (trimmed.startsWith(name + "=")) {
        return decodeURIComponent(trimmed.slice(name.length + 1));
      }
    }
    return "";
  }

  // Escape dynamic text before inserting into HTML to avoid rendering issues/XSS.
  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  // Lightweight global page loader shown during page navigation and form submits.
  function setupPageLoader() {
    if (document.getElementById("pageLoader")) return;
    const loader = document.createElement("div");
    loader.id = "pageLoader";
    loader.className = "page-loader";
    loader.innerHTML = `
      <div class="loader-panel">
        <div class="fw-semibold mb-2">Processing your request...</div>
        <div class="text-muted small mb-3">Please wait a few seconds.</div>
        <div class="loader-bar"><span></span></div>
      </div>
    `;
    document.body.appendChild(loader);

    function showLoader() {
      loader.classList.add("active");
    }
    function hideLoader() {
      loader.classList.remove("active");
    }

    document.addEventListener("click", (e) => {
      const link = e.target.closest("a[href]");
      if (!link) return;
      const href = link.getAttribute("href") || "";
      if (!href || href.startsWith("#") || href.startsWith("javascript:")) return;
      if (link.target === "_blank") return;
      if (href.startsWith("http") && !href.includes(location.host)) return;
      showLoader();
      setTimeout(hideLoader, 1800);
    });

    document.addEventListener("submit", (e) => {
      const form = e.target;
      if (!(form instanceof HTMLFormElement)) return;
      showLoader();
      setTimeout(hideLoader, 3000);
    });
  }

  setupPageLoader();

  // Scroll reveal animation helper for cards/sections marked with ".reveal".
  function setupRevealAnimations() {
    const nodes = document.querySelectorAll(".reveal");
    if (!nodes.length || !("IntersectionObserver" in window)) {
      nodes.forEach((n) => n.classList.add("visible"));
      return;
    }
    const obs = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            e.target.classList.add("visible");
            obs.unobserve(e.target);
          }
        }
      },
      { threshold: 0.12 }
    );
    nodes.forEach((n) => obs.observe(n));
  }
  setupRevealAnimations();

  // Upload forms + status panel wiring starts here.
  const singleForm = document.getElementById("singleUploadForm");
  const batchForm = document.getElementById("batchUploadForm");
  const resultBox = document.getElementById("uploadResult");
  let stageTimer = null;
  if (!singleForm && !batchForm) return;

  // Generic loading block (used before backend job token is received).
  function setLoading(message) {
    if (stageTimer) {
      clearInterval(stageTimer);
      stageTimer = null;
    }
    if (!resultBox) return;
    resultBox.innerHTML = `
      <div class="alert alert-info" role="alert">
        <div class="d-flex align-items-center gap-2">
          <div class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></div>
          <div>${escapeHtml(message)}</div>
        </div>
      </div>
    `;
  }

  // Simulated stage text while initial request is being accepted.
  function setStageLoading(prefix) {
    if (stageTimer) {
      clearInterval(stageTimer);
      stageTimer = null;
    }
    if (!resultBox) return;
    const stages = [
      "Step 1/4: Upload received",
      "Step 2/4: Reading document structure",
      "Step 3/4: Running extraction and validation checks",
      "Step 4/4: Generating verification assets",
    ];
    let idx = 0;
    resultBox.innerHTML = `
      <div class="alert alert-info" role="alert">
        <div class="d-flex align-items-center gap-2 mb-2">
          <div class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></div>
          <div><strong>${escapeHtml(prefix)}</strong></div>
        </div>
        <div id="stageText">${escapeHtml(stages[0])}</div>
      </div>
    `;
    stageTimer = setInterval(() => {
      idx = Math.min(idx + 1, stages.length - 1);
      const stageEl = document.getElementById("stageText");
      if (stageEl) stageEl.textContent = stages[idx];
      if (idx >= stages.length - 1 && stageTimer) {
        clearInterval(stageTimer);
        stageTimer = null;
      }
    }, 1800);
  }

  // Backend-driven status card update (polling response controls these values).
  function setBackendStage(step, progress, processed, total) {
    if (!resultBox) return;
    const safeStep = escapeHtml(step || "Processing...");
    const pct = Number.isFinite(Number(progress)) ? Number(progress) : 0;
    const proc = Number.isFinite(Number(processed)) ? Number(processed) : 0;
    const tot = Number.isFinite(Number(total)) ? Number(total) : 0;
    resultBox.innerHTML = `
      <div class="alert alert-info" role="alert">
        <div class="d-flex justify-content-between align-items-center mb-2">
          <strong>Upload accepted</strong>
          <span>${pct}%</span>
        </div>
        <div class="progress mb-2" style="height:8px;"><div class="progress-bar" style="width:${pct}%;"></div></div>
        <div>${safeStep}</div>
        ${tot ? `<div class="small text-muted mt-1">Files processed: ${proc}/${tot}</div>` : ""}
      </div>
    `;
  }

  // Uniform error renderer for all upload flows.
  function renderError(data, statusCode) {
    if (stageTimer) {
      clearInterval(stageTimer);
      stageTimer = null;
    }
    if (!resultBox) return;
    const detail = data?.detail || `Upload failed (${statusCode}).`;
    resultBox.innerHTML = `
      <div class="alert alert-danger" role="alert">
        <strong>Upload Failed</strong><br>${escapeHtml(detail)}
      </div>
    `;
  }

  // Detailed success renderer for single upload response payload.
  function renderSingleSuccess(data) {
    if (stageTimer) {
      clearInterval(stageTimer);
      stageTimer = null;
    }
    if (!resultBox) return;
    const status = (data.status || "active").toLowerCase();
    const statusBadge = status === "needs_review"
      ? '<span class="badge text-bg-warning">Needs Review</span>'
      : '<span class="badge text-bg-success">Active</span>';

    const warnings = (data.extracted_data && data.extracted_data.confidence_warnings) || [];
    const warningsHtml = warnings.length
      ? `<ul class="mb-0">${warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("")}</ul>`
      : "<div class='text-muted'>No warnings.</div>";

    const links = [
      data.pdf_file ? `<a class="btn btn-sm btn-outline-secondary" href="${escapeHtml(data.pdf_file)}" target="_blank">University PDF</a>` : "",
      data.college_certificate_pdf ? `<a class="btn btn-sm btn-outline-success" href="${escapeHtml(data.college_certificate_pdf)}" target="_blank">College PDF</a>` : "",
      data.combined_certificate_pdf ? `<a class="btn btn-sm btn-outline-dark" href="${escapeHtml(data.combined_certificate_pdf)}" target="_blank">Combined PDF</a>` : "",
      data.qr_code ? `<a class="btn btn-sm btn-outline-secondary" href="${escapeHtml(data.qr_code)}" target="_blank">QR</a>` : "",
    ].join("");

    const ingestion = (data.extracted_data && data.extracted_data.ingestion_meta) || {};
    const duplicateCandidates = (data.extracted_data && data.extracted_data.duplicate_candidates) || [];
    const duplicateHtml = duplicateCandidates.length
      ? `<div class="mt-2"><strong>Duplicate Candidates:</strong> ${duplicateCandidates.map(escapeHtml).join(", ")}</div>`
      : "";
    const duplicateReused = Boolean(data.duplicate_reused || (ingestion && ingestion.duplicate_reused));
    const duplicateBanner = duplicateReused
      ? `<div class="alert alert-warning mt-3 mb-2 py-2">
          Duplicate upload detected. Existing certificate record <strong>${escapeHtml(data.reused_certificate_id || data.certificate_id)}</strong> was reused. Verifier results remain unchanged.
        </div>`
      : "";
    const chain = data.blockchain || {};
    const txHash = chain.transaction_hash || "";
    const txLabel = txHash ? `${txHash.slice(0, 20)}...${txHash.slice(-10)}` : "";
    const blockchainHtml = chain.anchored
      ? `
        <div class="alert alert-primary mt-3 mb-2 py-2">
          <strong>Stored on blockchain:</strong> ${escapeHtml(chain.network || "Configured Chain")}
          <br><strong>Transaction Hash:</strong> ${escapeHtml(txLabel || txHash)}
          ${chain.transaction_link ? `<br><a href="${escapeHtml(chain.transaction_link)}" target="_blank" rel="noopener">Open transaction</a>` : ""}
          ${chain.block_number ? `<br><strong>Block:</strong> ${escapeHtml(chain.block_number)}` : ""}
        </div>
      `
      : `
        <div class="alert alert-warning mt-3 mb-2 py-2">
          <strong>Blockchain:</strong> Anchor not completed for this upload yet.
        </div>
      `;

    resultBox.innerHTML = `
      <div class="alert alert-success fade-in" role="alert">
        <div class="d-flex justify-content-between align-items-center">
          <strong>Certificate uploaded successfully</strong>
          ${statusBadge}
        </div>
        <div class="mt-2"><strong>Certificate ID:</strong> ${escapeHtml(data.certificate_id)}</div>
        <div><strong>Student:</strong> ${escapeHtml(data.student_name)}</div>
        <div><strong>Registration No:</strong> ${escapeHtml(data.registration_number)}</div>
        <div><strong>Course:</strong> ${escapeHtml(data.course)}</div>
        <div><strong>Department:</strong> ${escapeHtml(data.department)}</div>
        <div><strong>Graduation Year:</strong> ${escapeHtml(data.graduation_year)}</div>
        ${blockchainHtml}
        ${duplicateBanner}
        <div class="mt-2"><strong>Review Notes:</strong>${warningsHtml}</div>
        ${duplicateHtml}
        <div class="mt-3 d-flex gap-2 flex-wrap">
          <a class="btn btn-sm btn-outline-primary" href="/dashboard/review/">Open Review</a>
          ${links}
        </div>
      </div>
    `;
  }

  // Success renderer for batch upload completion summary.
  function renderBatchSuccess(data) {
    if (stageTimer) {
      clearInterval(stageTimer);
      stageTimer = null;
    }
    if (!resultBox) return;
    resultBox.innerHTML = `
      <div class="alert alert-success fade-in" role="alert">
        <strong>Batch upload completed</strong>
        <div class="mt-2">Processed: <strong>${escapeHtml(data.processed_count)}</strong></div>
        <div>Needs review: <strong>${escapeHtml(data.review_required_count ?? 0)}</strong></div>
        <div>Duplicates reused: <strong>${escapeHtml(data.duplicate_reused_count ?? 0)}</strong></div>
        <div class="mt-3 d-flex gap-2">
          <a class="btn btn-sm btn-outline-primary" href="/dashboard/review/">Open Review</a>
          <a class="btn btn-sm btn-outline-secondary" href="/dashboard/search/">Open Search</a>
        </div>
      </div>
    `;
  }

  // POST helper that also auto-switches to job polling when server returns a job token.
  async function postForm(url, formData, onSuccess, loadingText) {
    const headers = { "X-CSRFToken": getCookie("csrftoken") };
    const jwtToken = localStorage.getItem("access_token") || "";
    if (jwtToken) headers.Authorization = `Bearer ${jwtToken}`;

    setStageLoading(loadingText);
    const res = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers,
      body: formData,
    });

    let data;
    try {
      data = await res.json();
    } catch (_err) {
      data = { detail: `Unexpected response (${res.status})` };
    }

    if (!res.ok) {
      renderError(data, res.status);
      return;
    }

    if (data && data.job_token) {
      await pollJobStatus(data.job_token, onSuccess);
      return;
    }

    onSuccess(data);
  }

  // Poll async job endpoint until completed/failed, updating live progress in UI.
  async function pollJobStatus(jobToken, onSuccess) {
    let attempts = 0;
    const maxAttempts = 180;
    while (attempts < maxAttempts) {
      attempts += 1;
      let res;
      try {
        res = await fetch(`/api/upload-jobs/${encodeURIComponent(jobToken)}`, {
          credentials: "same-origin",
          headers: { "X-CSRFToken": getCookie("csrftoken") },
        });
      } catch (_err) {
        renderError({ detail: "Network error while checking upload status." }, 0);
        return;
      }

      let data = null;
      try {
        data = await res.json();
      } catch (_e) {
        data = null;
      }

      if (!res.ok || !data) {
        renderError({ detail: "Could not read upload job status." }, res.status || 0);
        return;
      }

      setBackendStage(data.step, data.progress, data.processed_files, data.total_files);

      if (data.status === "completed") {
        if (stageTimer) {
          clearInterval(stageTimer);
          stageTimer = null;
        }
        onSuccess(data.result || {});
        return;
      }

      if (data.status === "failed") {
        renderError({ detail: data.error_message || "Upload job failed." }, 500);
        return;
      }

      await new Promise((resolve) => setTimeout(resolve, 1500));
    }

    renderError({ detail: "Upload timed out. Please check logs and try again." }, 504);
  }

  if (singleForm) {
    singleForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const submitBtn = singleForm.querySelector('button[type="submit"]');
      if (submitBtn) submitBtn.disabled = true;
      try {
        await postForm(
          "/api/upload-certificate",
          new FormData(singleForm),
          renderSingleSuccess,
          "Uploading and extracting certificate details..."
        );
      } finally {
        if (submitBtn) submitBtn.disabled = false;
      }
    });
  }

  if (batchForm) {
    batchForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const submitBtn = batchForm.querySelector('button[type="submit"]');
      if (submitBtn) submitBtn.disabled = true;
      try {
        const fileInput = batchForm.querySelector('input[name="files"]');
        const folderInput = batchForm.querySelector('input[name="folder_files"]');
        const selectedFiles = [];

        if (fileInput && fileInput.files) {
          for (const file of fileInput.files) selectedFiles.push(file);
        }
        if (folderInput && folderInput.files) {
          for (const file of folderInput.files) {
            if (String(file.name || "").toLowerCase().match(/\.(pdf|png|jpg|jpeg|webp)$/)) selectedFiles.push(file);
          }
        }

        if (!selectedFiles.length) {
          renderError({ detail: "Select at least one supported file (PDF, PNG, JPG, JPEG, WEBP)." }, 400);
          return;
        }

        const formData = new FormData();
        for (const file of selectedFiles) formData.append("files", file);

        await postForm(
          "/api/upload-batch",
          formData,
          renderBatchSuccess,
          "Processing batch certificates..."
        );
      } finally {
        if (submitBtn) submitBtn.disabled = false;
      }
    });
  }
})();
