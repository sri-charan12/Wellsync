// ═══════════════════════════════════════════════
// WELLSYNC MEDICAL RECORDS MODULE
// Works in agent, patient, and doctor dashboards
// ═══════════════════════════════════════════════

var MedicalRecords = (function() {

  var RECORD_TYPES = [
    'Blood Test', 'X-Ray', 'MRI', 'CT Scan', 'ECG',
    'Ultrasound', 'Prescription', 'Discharge Summary',
    'Lab Report', 'Vaccination', 'Surgery Report', 'Other'
  ];

  // ── RENDER RECORDS LIST ──
  function renderRecordsList(containerId, records, canDelete) {
    var container = document.getElementById(containerId);
    if (!container) return;

    if (!records || records.length === 0) {
      container.innerHTML = '<div style="text-align:center;padding:2rem;color:#64748b;">'
        + '<i class="fas fa-folder-open" style="font-size:2.5rem;display:block;margin-bottom:.8rem;opacity:.3;"></i>'
        + 'No medical records yet.</div>';
      return;
    }

    var html = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:1.2rem;">';
    records.forEach(function(rec) {
      var isImage = rec.file_type && rec.file_type.startsWith('image/');
      var isPdf   = rec.file_type === 'application/pdf';
      var icon    = isImage ? 'fa-image' : (isPdf ? 'fa-file-pdf' : 'fa-file-medical');
      var iconColor = isImage ? '#2563eb' : (isPdf ? '#ef4444' : '#10b981');

      html += '<div style="background:white;border-radius:1rem;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.07);transition:transform .2s;" onmouseover="this.style.transform=\'translateY(-4px)\'" onmouseout="this.style.transform=\'none\'">'
        + '<div style="background:linear-gradient(135deg,#eff6ff,#dbeafe);padding:1.5rem;text-align:center;position:relative;">'
        +   '<i class="fas ' + icon + '" style="font-size:2.5rem;color:' + iconColor + ';"></i>'
        +   (canDelete ? '<button onclick="MedicalRecords.deleteRecord(\'' + rec._id + '\',this)" '
        +     'style="position:absolute;top:.5rem;right:.5rem;background:none;border:none;color:#ef4444;cursor:pointer;font-size:1rem;" title="Delete">'
        +     '<i class="fas fa-trash"></i></button>' : '')
        + '</div>'
        + '<div style="padding:1rem;">'
        +   '<div style="font-weight:700;font-size:.95rem;color:#1e293b;margin-bottom:.3rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="' + rec.title + '">' + rec.title + '</div>'
        +   '<div style="font-size:.8rem;background:#f1f5f9;color:#475569;padding:.2rem .6rem;border-radius:20px;display:inline-block;margin-bottom:.5rem;">' + rec.record_type + '</div>'
        +   '<div style="font-size:.78rem;color:#94a3b8;margin-bottom:.8rem;">'
        +     '<i class="fas fa-calendar" style="margin-right:.3rem;"></i>' + (rec.date || '') + '&nbsp;·&nbsp;'
        +     '<i class="fas fa-user" style="margin-right:.3rem;"></i>' + (rec.uploader_role || '') + '</div>'
        +   '<button onclick="MedicalRecords.viewRecord(\'' + rec._id + '\',\'' + rec.file_type + '\',\'' + (rec.title||'').replace(/'/g,"\\'") + '\')" '
        +     'style="width:100%;padding:.6rem;background:var(--primary-color, #2563eb);color:white;border:none;border-radius:8px;font-weight:600;cursor:pointer;font-size:.88rem;">'
        +     '<i class="fas fa-eye" style="margin-right:.4rem;"></i>View Record</button>'
        + '</div></div>';
    });
    html += '</div>';
    container.innerHTML = html;
  }

  // ── VIEW A RECORD (fetch file and show in modal) ──
  function viewRecord(recordId, fileType, title) {
    var modal = document.getElementById('medRecordViewModal');
    var body  = document.getElementById('medRecordViewBody');
    var titleEl = document.getElementById('medRecordViewTitle');
    if (!modal || !body) {
      MedicalRecords.injectModal();
      modal = document.getElementById('medRecordViewModal');
      body  = document.getElementById('medRecordViewBody');
      titleEl = document.getElementById('medRecordViewTitle');
    }
    if (!modal) return;

    if (titleEl) titleEl.textContent = title || 'Medical Record';
    body.innerHTML = '<div style="text-align:center;padding:3rem;color:#64748b;"><i class="fas fa-spinner fa-spin" style="font-size:2rem;"></i><br><br>Loading...</div>';
    modal.style.display = 'flex';

    fetch('/api/medical_record_file/' + recordId)
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.error) {
          body.innerHTML = '<p style="color:#ef4444;text-align:center;padding:2rem;">' + data.error + '</p>';
          return;
        }
        var src = 'data:' + data.file_type + ';base64,' + data.file_data;
        if (data.file_type && data.file_type.startsWith('image/')) {
          body.innerHTML = '<img src="' + src + '" style="max-width:100%;max-height:70vh;display:block;margin:0 auto;border-radius:.5rem;" alt="' + data.title + '">';
        } else if (data.file_type === 'application/pdf') {
          body.innerHTML = '<iframe src="' + src + '" style="width:100%;height:70vh;border:none;border-radius:.5rem;"></iframe>';
        } else {
          body.innerHTML = '<div style="text-align:center;padding:2rem;">'
            + '<i class="fas fa-file" style="font-size:3rem;color:#64748b;margin-bottom:1rem;display:block;"></i>'
            + '<p>' + (data.file_name || 'File') + '</p>'
            + '<a href="' + src + '" download="' + (data.file_name || 'record') + '" '
            +   'style="display:inline-block;margin-top:1rem;padding:.7rem 1.5rem;background:#2563eb;color:white;border-radius:8px;text-decoration:none;font-weight:600;">'
            +   '<i class="fas fa-download"></i> Download</a>'
            + '</div>';
        }
      })
      .catch(function() {
        body.innerHTML = '<p style="color:#ef4444;text-align:center;padding:2rem;">Could not load file. Try again.</p>';
      });
  }

  // ── DELETE A RECORD ──
  function deleteRecord(recordId, btnEl) {
    if (!confirm('Delete this medical record permanently?')) return;
    fetch('/api/delete_medical_record/' + recordId, { method: 'DELETE' })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.success) {
          var card = btnEl.closest('div[style*="border-radius:1rem"]');
          if (card) {
            card.style.transition = 'opacity .3s';
            card.style.opacity = '0';
            setTimeout(function() { if (card.parentNode) card.parentNode.removeChild(card); }, 300);
          }
        } else {
          alert('Could not delete: ' + (data.error || 'Unknown error'));
        }
      })
      .catch(function() { alert('Network error. Try again.'); });
  }

  // ── UPLOAD FORM HTML ──
  function getUploadFormHtml(patientId, patientName) {
    var typeOptions = RECORD_TYPES.map(function(t) {
      return '<option value="' + t + '">' + t + '</option>';
    }).join('');

    return '<div style="background:white;border-radius:1rem;padding:1.5rem;margin-bottom:1.5rem;">'
      + '<h4 style="margin-bottom:1.2rem;color:#1e293b;"><i class="fas fa-cloud-upload-alt" style="color:#2563eb;margin-right:.5rem;"></i>Upload Medical Record'
      + (patientName ? ' for ' + patientName : '') + '</h4>'
      + '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:1rem;margin-bottom:1rem;">'
      +   '<div><label style="display:block;font-size:.85rem;font-weight:600;color:#475569;margin-bottom:.3rem;">Record Title</label>'
      +     '<input type="text" id="mrTitle" placeholder="e.g. Blood Test June 2026" '
      +     'style="width:100%;padding:.7rem;border:1.5px solid #e2e8f0;border-radius:8px;font-size:.95rem;"></div>'
      +   '<div><label style="display:block;font-size:.85rem;font-weight:600;color:#475569;margin-bottom:.3rem;">Record Type</label>'
      +     '<select id="mrType" style="width:100%;padding:.7rem;border:1.5px solid #e2e8f0;border-radius:8px;font-size:.95rem;">'
      +     typeOptions + '</select></div>'
      + '</div>'

      // File drop zone
      + '<div id="mrDropZone" '
      +   'onclick="document.getElementById(\'mrFileInput\').click()" '
      +   'ondragover="event.preventDefault();this.style.borderColor=\'#2563eb\';this.style.background=\'#eff6ff\';" '
      +   'ondragleave="this.style.borderColor=\'#cbd5e1\';this.style.background=\'#f8fafc\';" '
      +   'ondrop="MedicalRecords.handleFileDrop(event,\'' + patientId + '\')" '
      +   'style="border:2px dashed #cbd5e1;border-radius:10px;padding:2rem;text-align:center;cursor:pointer;background:#f8fafc;transition:all .2s;margin-bottom:1rem;">'
      +   '<i class="fas fa-cloud-upload-alt" style="font-size:2rem;color:#94a3b8;display:block;margin-bottom:.5rem;"></i>'
      +   '<div style="font-weight:600;color:#475569;">Click to select or drag & drop</div>'
      +   '<div style="font-size:.82rem;color:#94a3b8;margin-top:.3rem;">Images (JPG, PNG) or PDF — max 5MB</div>'
      +   '<input type="file" id="mrFileInput" accept="image/*,application/pdf" style="display:none;" '
      +     'onchange="MedicalRecords.handleFileSelect(this,\'' + patientId + '\')">'
      + '</div>'

      // Preview area
      + '<div id="mrPreview" style="display:none;margin-bottom:1rem;"></div>'

      + '<button id="mrUploadBtn" onclick="MedicalRecords.uploadRecord(\'' + patientId + '\')" '
      +   'style="width:100%;padding:.8rem;background:#10b981;color:white;border:none;border-radius:8px;font-weight:700;font-size:1rem;cursor:pointer;">'
      +   '<i class="fas fa-upload" style="margin-right:.5rem;"></i>Upload Record</button>'
      + '</div>';
  }

  // ── FILE SELECTION HANDLER ──
  var selectedFileData = null;
  var selectedFileName = null;
  var selectedFileType = null;

  function handleFileSelect(input, patientId) {
    var file = input.files[0];
    if (!file) return;
    processFile(file);
  }

  function handleFileDrop(event, patientId) {
    event.preventDefault();
    var dropZone = document.getElementById('mrDropZone');
    if (dropZone) { dropZone.style.borderColor = '#cbd5e1'; dropZone.style.background = '#f8fafc'; }
    var file = event.dataTransfer.files[0];
    if (!file) return;
    processFile(file);
  }

  function processFile(file) {
    if (file.size > 5 * 1024 * 1024) {
      alert('File too large. Maximum size is 5MB.');
      return;
    }
    var preview = document.getElementById('mrPreview');
    var dropZone = document.getElementById('mrDropZone');

    var reader = new FileReader();
    reader.onload = function(e) {
      var result = e.target.result; // data:type;base64,data
      var parts  = result.split(',');
      selectedFileData = parts[1];
      selectedFileType = file.type;
      selectedFileName = file.name;

      // Show preview
      if (preview) {
        preview.style.display = 'block';
        if (file.type.startsWith('image/')) {
          preview.innerHTML = '<div style="position:relative;display:inline-block;">'
            + '<img src="' + result + '" style="max-height:160px;max-width:100%;border-radius:8px;border:2px solid #e2e8f0;">'
            + '<div style="margin-top:.4rem;font-size:.82rem;color:#64748b;">' + file.name + ' (' + Math.round(file.size/1024) + ' KB)</div>'
            + '</div>';
        } else {
          preview.innerHTML = '<div style="background:#fff5f5;border:1px solid #fecaca;border-radius:8px;padding:1rem;display:inline-flex;align-items:center;gap:.8rem;">'
            + '<i class="fas fa-file-pdf" style="font-size:1.5rem;color:#ef4444;"></i>'
            + '<div><div style="font-weight:600;">' + file.name + '</div>'
            +   '<div style="font-size:.82rem;color:#64748b;">' + Math.round(file.size/1024) + ' KB</div></div></div>';
        }
      }
      if (dropZone) dropZone.style.borderColor = '#10b981';
    };
    reader.readAsDataURL(file);
  }

  // ── UPLOAD ──
  function uploadRecord(patientId) {
    var title  = document.getElementById('mrTitle');
    var type   = document.getElementById('mrType');
    var btn    = document.getElementById('mrUploadBtn');

    if (!title || !title.value.trim()) { alert('Please enter a record title.'); return; }
    if (!selectedFileData) { alert('Please select a file to upload.'); return; }

    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Uploading...'; }

    fetch('/api/upload_medical_record', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        patient_id:   patientId,
        title:        title.value.trim(),
        record_type:  type ? type.value : 'General',
        file_data:    selectedFileData,
        file_name:    selectedFileName,
        file_type:    selectedFileType
      })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-upload"></i> Upload Record'; }
      if (data.success) {
        alert('✅ Medical record uploaded successfully!');
        // Reset form
        if (title) title.value = '';
        selectedFileData = null; selectedFileName = null; selectedFileType = null;
        var preview = document.getElementById('mrPreview');
        if (preview) { preview.style.display = 'none'; preview.innerHTML = ''; }
        var dropZone = document.getElementById('mrDropZone');
        if (dropZone) dropZone.style.borderColor = '#cbd5e1';
        var fileInput = document.getElementById('mrFileInput');
        if (fileInput) fileInput.value = '';
        // Reload records list
        if (window._currentMrPatientId) {
          loadRecords(window._currentMrPatientId, window._currentMrContainerId, window._currentMrCanDelete);
        }
      } else {
        alert('❌ Upload failed: ' + (data.error || 'Unknown error'));
      }
    })
    .catch(function() {
      if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-upload"></i> Upload Record'; }
      alert('❌ Network error. Try again.');
    });
  }

  // ── LOAD RECORDS ──
  function loadRecords(patientId, containerId, canDelete) {
    window._currentMrPatientId   = patientId;
    window._currentMrContainerId = containerId;
    window._currentMrCanDelete   = canDelete;

    var container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = '<div style="text-align:center;padding:2rem;color:#64748b;"><i class="fas fa-spinner fa-spin"></i> Loading records...</div>';

    fetch('/api/medical_records/' + patientId)
      .then(function(r) { return r.json(); })
      .then(function(data) {
        renderRecordsList(containerId, data.records || [], canDelete);
      })
      .catch(function() {
        var c = document.getElementById(containerId);
        if (c) c.innerHTML = '<p style="color:#ef4444;text-align:center;padding:2rem;">Could not load records. Try again.</p>';
      });
  }

  // ── MODAL HTML (inject once into page body) ──
  function injectModal() {
    if (document.getElementById('medRecordViewModal')) return;
    var modal = document.createElement('div');
    modal.id = 'medRecordViewModal';
    modal.style.cssText = 'display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.6);z-index:9999;align-items:center;justify-content:center;';
    modal.innerHTML = '<div style="background:white;border-radius:1rem;padding:1.5rem;max-width:820px;width:95%;max-height:90vh;overflow-y:auto;">'
      + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;border-bottom:2px solid #f1f5f9;padding-bottom:.8rem;">'
      +   '<h3 id="medRecordViewTitle" style="font-size:1.1rem;color:#1e293b;">Medical Record</h3>'
      +   '<button onclick="MedicalRecords.closeModal()" style="background:none;border:none;font-size:1.5rem;cursor:pointer;color:#64748b;">&times;</button>'
      + '</div>'
      + '<div id="medRecordViewBody"></div>'
      + '</div>';
    modal.classList = modal.classList || {};
    // Use display flex when active
    modal.addEventListener('click', function(e) {
      if (e.target === modal) MedicalRecords.closeModal();
    });
    document.body.appendChild(modal);
  }

  function closeModal() {
    var modal = document.getElementById('medRecordViewModal');
    if (modal) modal.style.display = 'none';
    var body = document.getElementById('medRecordViewBody');
    if (body) body.innerHTML = '';
  }

  // viewRecord already handles display:flex internally

  // Public API
  return {
    renderRecordsList: renderRecordsList,
    viewRecord:        viewRecord,
    deleteRecord:      deleteRecord,
    getUploadFormHtml: getUploadFormHtml,
    handleFileSelect:  handleFileSelect,
    handleFileDrop:    handleFileDrop,
    uploadRecord:      uploadRecord,
    loadRecords:       loadRecords,
    injectModal:       injectModal,
    closeModal:        closeModal
  };

})();